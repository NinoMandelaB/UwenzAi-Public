"""
Agent Gateway Proxy Service

Handles proxying requests from external agents to the configured providers.
"""

import json
import hashlib
import logging
import uuid
import time
from typing import Any
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from capp.models import (
    AgentAPIKey,
    AgentUsageLog,
    AgentModelPrice,
    AgentGatewayOperation,
    BillingReservation,
    CustomUser,
)
from capp.authentication import account_can_generate
from capp.classes.agent_gateway.guards import gateway_operation_lock
from capp.classes.agent_gateway.providers import get_provider, AgentProvider, PROVIDERS
from capp.classes.agent_gateway.pricing_service import AgentPricingService
from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.billing.reservation_service import (
    BillingReservationService,
    InsufficientBalance,
    ReservationError,
)

logger = logging.getLogger(__name__)


class AgentProxyService:
    """
    Service for proxying Agent Gateway requests to providers.

    Handles:
    - Provider selection and routing
    - Request validation
    - Cost calculation and billing
    - Response formatting
    """

    @staticmethod
    def get_provider_for_model(model: str | None, provider_name: str | None = None) -> AgentProvider | None:
        """
        Get the provider for a given model.

        Args:
            model: Model identifier, or None to use default

        Returns:
            Provider instance or None if not configured
        """
        if model is None:
            # Use default model from settings
            model = getattr(settings, "AGENT_DEFAULT_MODEL", "deepseek-v4-flash")

        return get_provider(provider_name=provider_name, model=model)

    @staticmethod
    def validate_request(
        model: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Validate an incoming chat completion request.

        Args:
            model: Model identifier
            messages: List of messages
            params: Additional parameters

        Returns:
            Dict with validated parameters

        Raises:
            ValueError: If request is invalid
        """
        # "provider/model" (e.g. "cortecs/deepseek-v3.2") explicitly selects
        # the provider. Only a known provider name counts as a prefix, so
        # model IDs that contain slashes themselves (Inceptron's
        # vendor-prefixed IDs) keep working unchanged. Strip the prefix here
        # so pricing, reservations, and usage logs all record the clean model.
        if model and not provider_name:
            prefix, sep, rest = model.partition("/")
            if sep and rest and prefix in PROVIDERS:
                provider_name = prefix
                model = rest

        # Check messages
        if not isinstance(messages, list) or not messages:
            raise ValueError("Messages must be a non-empty list")
        if model is not None and (not isinstance(model, str) or not model or len(model) > 100):
            raise ValueError("Select a valid model from the pricing catalog")
        if provider_name is not None and (
            not isinstance(provider_name, str) or not provider_name or len(provider_name) > 50
        ):
            raise ValueError("Select a valid provider from the pricing catalog")
        allowed = {
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "stream",
            "stream_options",
            "stop",
            "presence_penalty",
            "frequency_penalty",
            "user",
            "tools",
            "tool_choice",
        }
        if not isinstance(params, dict) or set(params) - allowed:
            raise ValueError("Unsupported parameters; provider endpoint URLs cannot be overridden")
        params = dict(params)
        # Tool calling: schemas are client-supplied and executed by the client —
        # the gateway only relays them. Validation bounds size and shape so a
        # tools payload cannot smuggle endpoint overrides or balloon the
        # reservation estimate; the token estimate counts the tools JSON so
        # the reservation covers the extra input tokens upstreams charge for
        # the schemas (settle_amount is capped at reserved, so an uncounted
        # tools block would systematically undercharge).
        tools = params.get("tools")
        if tools is not None:
            if not isinstance(tools, list) or not 1 <= len(tools) <= 128:
                raise ValueError("tools must be a list of 1 to 128 tool definitions")
            for tool in tools:
                if not isinstance(tool, dict) or tool.get("type") != "function":
                    raise ValueError("Each tool must be an object with type 'function'")
                function = tool.get("function")
                if (
                    not isinstance(function, dict)
                    or not isinstance(function.get("name"), str)
                    or not 1 <= len(function["name"]) <= 128
                ):
                    raise ValueError("Each tool must define a function with a name")
            if len(json.dumps(tools, ensure_ascii=False)) > 200_000:
                raise ValueError("The tools payload is too large")
        tool_choice = params.get("tool_choice")
        if tool_choice is not None and tool_choice not in ("none", "auto", "required"):
            if (
                not isinstance(tool_choice, dict)
                or tool_choice.get("type") != "function"
                or not isinstance(tool_choice.get("function"), dict)
                or not isinstance(tool_choice["function"].get("name"), str)
                or not tool_choice["function"]["name"]
            ):
                raise ValueError("tool_choice must be 'none', 'auto', 'required' or a function object")
        # stream_options is OpenAI-standard (agents send {"include_usage": true}
        # with every streaming request). The gateway always includes usage in
        # the final SSE chunk, so the flag is honored by construction; strip it
        # before the provider call because the upstream request is never
        # streamed and a leftover stream_options would 400 upstream.
        stream_options = params.pop("stream_options", None)
        if stream_options is not None:
            if not isinstance(stream_options, dict) or set(stream_options) - {"include_usage"}:
                raise ValueError("stream_options supports only include_usage")
            if not isinstance(stream_options.get("include_usage", False), bool):
                raise ValueError("stream_options.include_usage must be a boolean")
        # Newer OpenAI SDKs send max_completion_tokens (the two spellings are
        # mutually exclusive in the OpenAI API). Normalize to max_tokens so
        # billing/validation below sees exactly one knob; a provider whose
        # upstream wants the other spelling re-maps it itself.
        if params.get("max_completion_tokens") is not None:
            if params.get("max_tokens") is not None:
                raise ValueError("Provide either max_tokens or max_completion_tokens, not both")
            params["max_tokens"] = params.pop("max_completion_tokens")
        else:
            params.pop("max_completion_tokens", None)
        if params.get("max_tokens") is None:
            # 1024 matches the playground output cap. The old 256 default
            # silently truncated replies from clients that omit max_tokens
            # (finish_reason=length) — 256 tokens is ~10 lines of prose.
            params["max_tokens"] = 1024
        if type(params["max_tokens"]) is not int or not 1 <= params["max_tokens"] <= 131072:
            raise ValueError("max_tokens must be a positive integer no greater than 131072")

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise ValueError(f"Message {i}: must be an object")
            if "role" not in msg:
                raise ValueError(f"Message {i}: 'role' is required")
            if "content" not in msg:
                raise ValueError(f"Message {i}: 'content' is required")
            if msg["role"] not in ("user", "assistant", "system", "tool"):
                raise ValueError(f"Message {i}: invalid role '{msg['role']}'")
            if msg["role"] == "tool":
                if not isinstance(msg.get("tool_call_id"), str) or not msg["tool_call_id"]:
                    raise ValueError(f"Message {i}: tool results require a 'tool_call_id'")
            if msg["role"] == "assistant" and msg.get("content") is None:
                # The agent loop replays the previous assistant message, which
                # carries tool_calls and a null content — that shape is valid.
                if not isinstance(msg.get("tool_calls"), list) or not msg["tool_calls"]:
                    raise ValueError(f"Message {i}: assistant content cannot be empty without tool_calls")

        # Get model (use default if not provided)
        if model is None:
            model = getattr(settings, "AGENT_DEFAULT_MODEL", "deepseek-v4-flash")

        # Check provider is available
        provider = AgentProxyService.get_provider_for_model(model, provider_name)
        if provider is None:
            raise ValueError(f"No provider available for model '{model}'")

        if not provider.is_configured():
            raise ValueError(f"Provider for model '{model}' is not configured")

        # Explicit pairs must be catalogued and enabled, even if a registry is overridden.
        price = AgentModelPrice.objects.filter(provider=provider.name, model=model).first()
        if (provider_name is not None and price is None) or (price and not price.is_active):
            raise ValueError("This provider/model pair is disabled or not in the catalog; choose an available pair")
        AgentPricingService.get_price(provider.name, model)
        if price:
            from capp.classes.ai.token_utils import TokenUtils

            if price.max_output_tokens and params["max_tokens"] > price.max_output_tokens:
                raise ValueError("max_tokens exceeds this model's output limit; reduce it")
            if (
                price.max_context
                and TokenUtils.count_message_tokens(messages, model=model, tools=params.get("tools"))
                + params["max_tokens"]
                > price.max_context
            ):
                raise ValueError("Messages plus requested output exceed this model's context; shorten the conversation")

        return {
            "model": model,
            "messages": messages,
            "params": params,
            "provider": provider,
        }

    @staticmethod
    def calculate_required_sparks(
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        provider_name: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        """
        Estimate the maximum sparks needed for a request.

        This is used for reservation before the actual provider call.
        We estimate based on the input tokens + worst-case output tokens.

        Args:
            model: Model identifier
            messages: List of messages
            max_tokens: Maximum tokens to generate (for output estimate)

        Returns:
            Estimated sparks needed (worst case)
        """
        from capp.classes.ai.token_utils import TokenUtils

        # Model-aware token estimate; count_message_tokens handles string and
        # multi-modal (list) content plus per-message framing overhead
        input_tokens = TokenUtils.count_message_tokens(messages, model=model, tools=tools)

        # Estimate output tokens
        if max_tokens:
            output_tokens = max_tokens
        else:
            # Default to a reasonable estimate
            output_tokens = 256

        # Use the same selected provider for reservation and settlement.
        if provider_name is None:
            provider = AgentProxyService.get_provider_for_model(model)
            if provider is None:
                raise ValueError("Selected provider/model pair is not available")
            provider_name = provider.name

        # Calculate estimate
        estimate = AgentPricingService.calculate_estimate(
            provider=provider_name,
            model=model,
            input_tokens=input_tokens,
            max_output_tokens=output_tokens,
        )

        return estimate["sparks"]

    @staticmethod
    def execute_request(key, model, messages, params, client_operation_id=None, provider_name=None, source=None):
        """Shared billed path for browser and external keys; serialize duplicate operations only.

        ``source`` tags the resulting usage log (AgentUsageLog.Source) so usage
        reporting can distinguish workspace playground calls from direct API
        calls; it never affects billing.
        """
        if source is None:
            source = AgentUsageLog.Source.API
        # Reject invalid data before acquiring any lock or creating billing records.
        AgentProxyService.validate_request(model, messages, params, provider_name)
        if client_operation_id is None:
            client_operation_id = str(uuid.uuid4())
        if not isinstance(client_operation_id, str) or not 1 <= len(client_operation_id) <= 300:
            raise ValueError("client_operation_id must be a string of 1 to 300 characters")
        with gateway_operation_lock(key, client_operation_id):
            key = AgentAPIKey.objects.select_related("user").filter(pk=key.pk).first()
            if key is None or not key.can_be_used() or not account_can_generate(key.user):
                return {
                    "error": {
                        "status": "invalid_api_key",
                        "message": "This key or account is inactive or expired. Select an active key.",
                    }
                }
            return AgentProxyService._execute_locked(
                key, model, messages, params, client_operation_id, provider_name, source
            )

    @staticmethod
    def _execute_locked(
        key: AgentAPIKey,
        model: str | None,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        client_operation_id: str | None = None,
        provider_name: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute a chat completion request through the Agent Gateway.

        This is the main entry point that:
        1. Validates the request
        2. Checks the user's balance
        3. Reserves sparks
        4. Calls the provider
        5. Calculates actual cost
        6. Settles the billing
        7. Logs the usage
        8. Returns the response

        Args:
            key: The AgentAPIKey being used
            model: Model identifier
            messages: List of messages
            params: Additional parameters
            client_operation_id: Optional idempotency key

        Returns:
            OpenAI-compatible response dict

        Raises:
            Exception: If request fails
        """
        start_time = time.time()

        # Validate request
        validated = AgentProxyService.validate_request(model, messages, params, provider_name)
        model = validated["model"]
        messages = validated["messages"]
        params = validated["params"]
        provider = validated["provider"]
        provider_name = provider.name

        # Get the user
        user = key.user

        # The complete worst-case reservation must fit the monthly key budget.
        estimated_sparks = AgentProxyService.calculate_required_sparks(
            model, messages, params["max_tokens"], provider_name, tools=params.get("tools")
        )
        if client_operation_id is None:
            client_operation_id = str(uuid.uuid4())
        if not isinstance(client_operation_id, str) or not 1 <= len(client_operation_id) <= 300:
            raise ValueError("client_operation_id must be a string of 1 to 300 characters")

        # Check for identical request (idempotency)
        existing_log = AgentUsageLog.objects.filter(
            client_operation_id=client_operation_id,
            user=user,
            key=key,
        ).first()

        if existing_log:
            if existing_log.model != model or existing_log.provider != provider_name:
                return {
                    "error": {
                        "status": "idempotency_conflict",
                        "message": "Operation ID already belongs to a different model/provider. Use a new ID.",
                    }
                }
            if existing_log.status != AgentUsageLog.Status.SUCCESS:
                billing_id = "agent:{}:{}".format(key.pk, hashlib.sha256(client_operation_id.encode()).hexdigest())
                # Legacy failed logs may have been written even when refund failed.
                # Confirm ledger state, rather than trusting the historical zero-charge log.
                from django.db.models import Q

                prior = (
                    BillingReservation.objects.filter(user=user, feature="agent_gateway")
                    .filter(Q(idempotency_key=billing_id) | Q(idempotency_key=client_operation_id))
                    .first()
                )
                if prior is None or prior.status != BillingReservation.Status.REFUNDED:
                    return AgentProxyService._refund_pending()
                return {
                    "error": {
                        "status": "upstream_error",
                        "refund_confirmed": True,
                        "terminal": True,
                        "message": (
                            "The original attempt failed and was not charged. Use a new operation ID to try " "again."
                        ),
                    }
                }
            logger.info(
                "Agent Gateway: idempotent retry for key=%s (no re-charge)",
                key.pk,
            )
            # P1.4: Return 200 with the original usage info, not an error.
            # Do NOT double-charge. The agent gets a valid response back.
            return {
                "id": f"uwenzai-{existing_log.model}",
                "object": "chat.completion",
                "created": int(existing_log.created_at.timestamp()),
                "model": existing_log.model,
                "choices": [],
                "usage": {
                    "prompt_tokens": existing_log.input_tokens,
                    "completion_tokens": existing_log.output_tokens,
                    "total_tokens": existing_log.input_tokens + existing_log.output_tokens,
                },
                "_uwenzai": {
                    "sparks_charged": 0,  # No re-charge on retry
                    "idempotent_retry": True,
                    "original_sparks_charged": existing_log.sparks_charged,
                },
            }

        # Admission and settlement share this short DB key lock. The two budget
        # aggregates therefore observe a consistent ledger + pending-hold state.
        # No key/user DB lock is held during provider I/O; agent fan-out is allowed.
        with transaction.atomic():
            # Match erasure order; NO KEY UPDATE permits ordinary billing FK key-share locks.
            CustomUser.objects.select_for_update(no_key=True).get(pk=user.pk)
            key = AgentAPIKey.objects.select_for_update(of=("self",)).select_related("user").get(pk=key.pk)
            if not key.can_be_used() or not account_can_generate(key.user):
                return {
                    "error": {
                        "status": "invalid_api_key",
                        "message": "The key or account is no longer active. Choose an active key.",
                    }
                }
            if not AgentKeyService.check_budget(key, estimated_sparks):
                return {
                    "error": {
                        "status": "budget_exceeded",
                        "message": (
                            "This request's maximum cost exceeds the key's remaining monthly budget. Reduce "
                            "max_tokens or choose another key."
                        ),
                    }
                }

            # Older deployments used the raw client ID for reservations. An incomplete
            # legacy attempt cannot safely be replayed under the new namespace.
            if BillingReservation.objects.filter(
                user=user, feature="agent_gateway", idempotency_key=client_operation_id
            ).exists():
                return {
                    "error": {
                        "status": "idempotency_conflict",
                        "message": (
                            "This earlier operation needs reconciliation. Check usage or contact "
                            "support before retrying."
                        ),
                    }
                }

            claim, created = AgentGatewayOperation.objects.get_or_create(
                user=user,
                key=key,
                client_operation_id=client_operation_id,
                defaults={"model": model, "provider": provider_name},
            )
            if not created:
                return {
                    "error": {
                        "status": "idempotency_conflict",
                        "message": (
                            "This operation is already running or awaiting billing reconciliation. Do not retry "
                            "it with a new ID until its outcome is known."
                        ),
                    }
                }
            # Namespaced reservation keys prevent cross-feature/key collisions; no client text in logs.
            billing_id = "agent:{}:{}".format(key.pk, hashlib.sha256(client_operation_id.encode()).hexdigest())

            # Reserve sparks using the BillingReservationService
            # This will raise InsufficientBalance if the user doesn't have enough sparks
            try:
                reservation = BillingReservationService.reserve(
                    idempotency_key=billing_id,
                    feature="agent_gateway",
                    amount_sparks=estimated_sparks,
                    user=user,
                    # operation_reference is globally unique (one reservation per
                    # operation); it must include the per-call id, not just the model.
                    operation_reference=billing_id,
                )
            except InsufficientBalance as e:
                claim.delete()  # No hold/debit was created: a funded retry is safe.
                logger.warning(
                    "Agent Gateway: reservation failed for user %s: %s",
                    user.user_id,
                    str(e),
                )
                return {
                    "error": {
                        "status": "insufficient_sparks",
                        "message": "Insufficient sparks for this request",
                        "required": estimated_sparks,
                        # InsufficientBalance carries its payload in .context
                        "available": int(e.context.get("balance") or 0),
                    }
                }
            except ReservationError as e:
                logger.warning(
                    "Agent Gateway: reservation error for user %s: %s",
                    user.user_id,
                    str(e),
                )
                # The reserve call failed without handing back a reservation:
                # no hold or debit exists, so a retried operation ID must not
                # be poisoned by the claim created above (permanent
                # idempotency_conflict). Delete it — but only after confirming
                # no reservation row was actually persisted: subclasses such
                # as IdempotencyKeyConflict mean a real reservation exists and
                # the claim must stay for reconciliation.
                with transaction.atomic():
                    locked_claim = AgentGatewayOperation.objects.select_for_update().filter(pk=claim.pk).first()
                    if (
                        locked_claim is not None
                        and not BillingReservation.objects.filter(
                            user=user, feature="agent_gateway", idempotency_key=billing_id
                        ).exists()
                    ):
                        locked_claim.delete()
                return {
                    "error": {
                        "status": "reservation_error",
                        "message": str(e) or "Reservation failed",
                    }
                }

        reserved_amount = reservation.amount_sparks

        try:
            # Call the provider
            result = provider.chat_completion(
                model=model,
                messages=messages,
                params=params,
            )

            if result.status != "success":
                return AgentProxyService._record_failure(
                    key,
                    model,
                    provider_name,
                    reservation,
                    client_operation_id,
                    start_time,
                    upstream_status_code=result.upstream_status_code,
                    source=source,
                )

            # A tool_calls finish with no relayed calls is a broken upstream
            # response: the client would wait forever for calls to execute.
            # Report it as an upstream error (refunded) instead of stalling
            # the agent loop.
            if result.finish_reason == "tool_calls" and not result.tool_calls:
                return AgentProxyService._record_failure(
                    key,
                    model,
                    provider_name,
                    reservation,
                    client_operation_id,
                    start_time,
                    failure_kind="tool_calls_dropped",
                    source=source,
                )

            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            if (
                type(input_tokens) is not int
                or type(output_tokens) is not int
                or input_tokens < 0
                or output_tokens < 0
                or input_tokens + output_tokens == 0
            ):
                return AgentProxyService._record_failure(
                    key,
                    model,
                    provider_name,
                    reservation,
                    client_operation_id,
                    start_time,
                    failure_kind="no_token_usage",
                    source=source,
                )

            cost_info = AgentPricingService.calculate_cost(
                provider=provider_name,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            actual_sparks = cost_info["sparks"]

            # P0.2: Settle at actual amount, not the reserved estimate.
            # settle_actual credits back the difference through locking primitives.
            # If actual >= reserved, user pays only the reserved amount (no overcharge).
            settle_amount = min(actual_sparks, reserved_amount)

            # Format before settlement: a malformed provider response cannot become
            # a settled charge followed by an attempted refund/duplicate error log.
            response = result.to_openai_response(model)
            response["created"] = int(time.time())
            with transaction.atomic():
                CustomUser.objects.select_for_update(no_key=True).get(pk=user.pk)
                AgentAPIKey.objects.select_for_update().get(pk=key.pk)
                BillingReservationService.settle_actual(
                    reservation,
                    actual_amount_sparks=settle_amount,
                    reason=f"agent_gateway:{model}: actual={actual_sparks}, reserved={reserved_amount}",
                )

                # Record usage log - track actual settled amount
                latency_ms = int((time.time() - start_time) * 1000)

                AgentUsageLog.objects.create(
                    key=key,
                    key_hint=key.key_hint,
                    user=user,
                    provider=provider_name,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_native=Decimal(str(cost_info["cost_native"])),
                    cost_currency=cost_info.get("cost_currency", "EUR"),
                    fx_rate=Decimal(str(cost_info["fx_rate"])),
                    sparks_charged=settle_amount,
                    status=AgentUsageLog.Status.SUCCESS,
                    latency_ms=latency_ms,
                    client_operation_id=client_operation_id,
                    source=source,
                )

                AgentAPIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())

            # Add UwenzAi-specific metadata
            response["_uwenzai"] = {
                "sparks_charged": settle_amount,
                "actual_sparks": actual_sparks,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": model,
                "provider": provider_name,
            }

            return response

        except Exception as e:
            return AgentProxyService._record_failure(
                key,
                model,
                provider_name,
                reservation,
                client_operation_id,
                start_time,
                failure_kind="gateway_error:{}".format(type(e).__name__),
                source=source,
            )

    @staticmethod
    def _refund_pending():
        return {
            "error": {
                "status": "refund_pending",
                "refund_confirmed": False,
                "terminal": False,
                "message": (
                    "Billing could not confirm a refund. Do not start another paid "
                    "attempt; check usage or contact support."
                ),
            }
        }

    @staticmethod
    def _record_failure(
        key,
        model,
        provider_name,
        reservation,
        client_operation_id,
        start_time,
        upstream_status_code=None,
        failure_kind="upstream_error",
        source=None,
    ):
        """Persist a confirmed refund or an explicitly uncertain hold, never false zero-charge claims."""
        with transaction.atomic():
            CustomUser.objects.select_for_update(no_key=True).get(pk=key.user_id)
            AgentAPIKey.objects.select_for_update().get(pk=key.pk)
            try:
                with transaction.atomic():
                    refunded = BillingReservationService.refund(reservation, reason="agent_gateway:upstream_error")
                confirmed = refunded.status == BillingReservation.Status.REFUNDED
            except Exception:
                confirmed = False
            AgentUsageLog.objects.create(
                key=key,
                key_hint=key.key_hint,
                user=key.user,
                provider=provider_name,
                model=model,
                input_tokens=0,
                output_tokens=0,
                cost_native=Decimal("0"),
                cost_currency="EUR",
                fx_rate=Decimal("0"),
                sparks_charged=0,
                status=AgentUsageLog.Status.UPSTREAM_ERROR if confirmed else AgentUsageLog.Status.REFUND_PENDING,
                latency_ms=int((time.time() - start_time) * 1000),
                source=source,
                client_operation_id=client_operation_id,
            )
        # Upstream error text can echo request content, so it is never
        # returned or logged (privacy rule). The numeric HTTP status, the
        # failure kind and the model/provider identifiers are content-free
        # and enough to triage: 400 = bad parameter, 403 = model access
        # denied, 404 = unknown model.
        logger.error(
            "Agent Gateway: failed operation key=%s provider=%s model=%s refund_confirmed=%s "
            "kind=%s upstream_http_status=%s",
            key.pk,
            provider_name,
            model,
            confirmed,
            failure_kind,
            upstream_status_code if upstream_status_code else "unknown",
        )
        if not confirmed:
            return AgentProxyService._refund_pending()
        return {
            "error": {
                "status": "upstream_error",
                "refund_confirmed": True,
                "terminal": True,
                "message": (
                    "The provider request failed and its reservation was refunded. Use a "
                    "new operation ID to try again."
                ),
            }
        }

    @staticmethod
    def list_models() -> list[dict[str, Any]]:
        """
        List all available models for the Agent Gateway.

        Returns:
            List of model info dicts (OpenAI-compatible format)
        """
        from capp.classes.agent_gateway.providers.registry import get_all_models

        models = get_all_models()

        result = []
        for m in models:
            result.append(
                {
                    "id": m.id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "uwenzai",
                }
            )

        return result
