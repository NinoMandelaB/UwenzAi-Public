"""
Agent Gateway API Views

Provides the HTTP API endpoints for the UwenzAi Agent Gateway.

Endpoints:
- POST /api/v1/chat/completions - OpenAI-compatible completion (dedicated Agent service)
- GET /api/v1/models - Model list (dedicated Agent service)
- /api/v1/agent/ retains the legacy inference aliases on both services
- GET /api/v1/agent/keys - List user's API keys (session auth)
- POST /api/v1/agent/keys - Create new API key (session auth)
- DELETE /api/v1/agent/keys/{id} - Revoke API key (session auth)
- GET /api/v1/agent/usage - Get usage summary (session auth)
"""

import json
import logging
from datetime import datetime

from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from django.conf import settings
from rest_framework.decorators import (
    action,
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request

from capp.models import AgentAPIKey, AgentUsageLog, ComplianceConfirmationLog, CustomUser
from capp.authentication import AccountJWTAuthentication
from capp.classes.agent_gateway.guards import (
    AGENT_GATEWAY_CONSENT_VERSION,
    has_gateway_consent,
    GatewayBusy,
    GatewayUnavailable,
    gateway_user_lock,
    GatewayAccountPermission,
)
from capp.classes.agent_gateway import playground_service
from capp.classes.agent_gateway.auth import AgentGatewayAuthentication
from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.agent_gateway.proxy_service import AgentProxyService
from capp.classes.agent_gateway.pricing_service import AgentPricingService
from capp.classes.agent_gateway.usage_service import AgentUsageService
from capp.classes.billing.reservation_service import (
    InsufficientBalance,
    IdempotencyKeyConflict,
)
from capp.throttles import AgentGatewayKeyThrottle

logger = logging.getLogger(__name__)


# ============================================================================
# INTERNAL ENDPOINTS (Session Authentication)
# ============================================================================


@method_decorator(never_cache, name="dispatch")
class AgentKeyViewSet(viewsets.ViewSet):
    """
    API key management for authenticated users.

    These endpoints use session authentication, not Bearer token auth.
    """

    authentication_classes = [AccountJWTAuthentication, SessionAuthentication]
    permission_classes = [GatewayAccountPermission]

    def list(self, request: Request) -> Response:
        """
        List all Agent API keys for the authenticated user.

        GET /api/v1/agent/keys
        """
        user = request.user
        if not isinstance(user, CustomUser):
            return Response(
                {"status": "unauthorized", "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        keys = AgentKeyService.list_keys(user)

        return Response(
            {
                "status": "ok",
                "data": {
                    "keys": keys,
                },
            }
        )

    def create(self, request: Request) -> Response:
        """
        Create a new Agent API key.

        POST /api/v1/agent/keys

        Request body:
        - label: Optional label for the key
        - expires_at: Optional ISO date string for expiration
        - monthly_spark_budget: Optional integer per-key spending cap in
          sparks (1..AGENT_KEY_MAX_MONTHLY_BUDGET). Omit for unlimited.

        Returns the full key exactly once (in the 'full_key' field).
        """
        user = request.user
        if not isinstance(user, CustomUser):
            return Response(
                {"status": "unauthorized", "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not has_gateway_consent(user):
            return Response(
                {
                    "status": "consent_required",
                    "message": "Accept the developer gateway consent before creating an API key.",
                    "version": AGENT_GATEWAY_CONSENT_VERSION,
                },
                status=403,
            )
        if not isinstance(request.data, dict):
            return Response({"status": "invalid_request", "message": "Send a JSON object"}, status=400)
        label = request.data.get("label", "")
        if not isinstance(label, str) or len(label) > 100:
            return Response(
                {"status": "invalid_request", "message": "Key label must be text of at most 100 characters."},
                status=400,
            )
        expires_at_str = request.data.get("expires_at")
        expires_at = None

        monthly_spark_budget, budget_error = AgentKeyService.parse_monthly_budget(
            request.data.get("monthly_spark_budget")
        )
        if budget_error:
            return Response(
                {"status": "invalid_request", "message": budget_error},
                status=400,
            )

        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                if timezone.is_naive(expires_at) or expires_at <= timezone.now():
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                return Response(
                    {
                        "status": "invalid_request",
                        "message": "expires_at must be a future ISO timestamp with timezone.",
                    },
                    status=400,
                )

        # Create the key
        full_key, key_record = AgentKeyService.create_key(
            user=user,
            label=label,
            expires_at=expires_at,
            monthly_spark_budget=monthly_spark_budget,
        )

        # Return the full key ONCE
        return Response(
            {
                "status": "ok",
                "data": {
                    "full_key": full_key,  # Only shown once!
                    "key_id": str(key_record.agent_api_key_id),
                    "label": key_record.label,
                    "hint": key_record.display_hint,
                    "created_at": key_record.created_at.isoformat(),
                    "is_active": key_record.is_active,
                    "monthly_spark_budget": key_record.monthly_spark_budget,
                },
                "warning": "This is the only time the full key will be shown. Store it securely.",
            }
        )

    @action(detail=True, methods=["patch"])
    def budget(self, request: Request, pk: str = None) -> Response:
        """
        Set or clear a key's monthly Sparks spending limit.

        PATCH /api/v1/agent/keys/{id}/budget

        Request body:
        - monthly_spark_budget: integer 1..AGENT_KEY_MAX_MONTHLY_BUDGET, or
          null to remove the limit. The field is required.

        Setting a budget below the key's usage this month is allowed: it simply
        blocks further calls with the key until next month. Revoked keys reject
        edits (create a new key instead).
        """
        user = request.user
        if not isinstance(user, CustomUser):
            return Response(
                {"status": "unauthorized", "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        key = AgentKeyService.get_key(user, pk)
        if key is None:
            return Response(
                {"status": "not_found", "message": "API key not found or not owned by user"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not key.is_active:
            return Response(
                {
                    "status": "invalid_request",
                    "message": "This key is revoked and its budget can no longer be changed. Create a new key instead.",
                },
                status=400,
            )
        if "monthly_spark_budget" not in request.data:
            return Response(
                {
                    "status": "invalid_request",
                    "message": "monthly_spark_budget is required (an integer, or null to remove the limit).",
                },
                status=400,
            )

        budget, budget_error = AgentKeyService.parse_monthly_budget(request.data["monthly_spark_budget"])
        if budget_error:
            return Response(
                {"status": "invalid_request", "message": budget_error},
                status=400,
            )

        key.monthly_spark_budget = budget
        key.save(update_fields=["monthly_spark_budget", "updated_at"])

        logger.info(
            "Agent Gateway: set monthly budget %s for key %s",
            budget,
            pk,
        )

        return Response(
            {
                "status": "ok",
                "data": {
                    "key_id": pk,
                    "monthly_spark_budget": budget,
                },
            }
        )

    def destroy(self, request: Request, pk: str) -> Response:
        """
        Revoke an Agent API key.

        DELETE /api/v1/agent/keys/{id}
        """
        user = request.user
        if not isinstance(user, CustomUser):
            return Response(
                {"status": "unauthorized", "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        success = AgentKeyService.revoke_key(user, pk)

        if not success:
            return Response(
                {"status": "not_found", "message": "API key not found or not owned by user"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "ok",
                "data": {
                    "message": "API key revoked",
                    "key_id": pk,
                },
            }
        )


@method_decorator(never_cache, name="dispatch")
class AgentUsageViewSet(viewsets.ViewSet):
    """
    Usage reporting for authenticated users.

    These endpoints use session authentication, not Bearer token auth.
    """

    authentication_classes = [AccountJWTAuthentication, SessionAuthentication]
    permission_classes = [GatewayAccountPermission]

    def list(self, request: Request) -> Response:
        """
        Get usage summary for the authenticated user.

        GET /api/v1/agent/usage
        """
        user = request.user
        if not isinstance(user, CustomUser):
            return Response(
                {"status": "unauthorized", "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Get overall usage
        usage = AgentUsageService.get_user_usage(str(user.user_id))

        # Get per-key usage
        keys_usage = AgentUsageService.get_all_keys_usage(str(user.user_id))

        # Same configured public inference base advertised by the pricing view.
        base_url = settings.AGENT_BASE_URL

        return Response(
            {
                "status": "ok",
                "data": {
                    "usage": usage,
                    "keys_usage": keys_usage,
                    "by_model": AgentUsageService.get_usage_by_model(str(user.user_id)),
                    "by_key": AgentUsageService.get_usage_by_key(str(user.user_id)),
                    "base_url": base_url,
                },
            }
        )


# ============================================================================
# CONSENT ENDPOINT (Session Authentication)
# ============================================================================


@never_cache
@api_view(["GET", "POST"])
@authentication_classes([AccountJWTAuthentication, SessionAuthentication])
@permission_classes([GatewayAccountPermission])
def agent_gateway_consent(request: Request) -> Response:
    """
    Check or record the user's consent to use the Agent Gateway (UwenzAi-Dev).

    GET: Returns whether the user has explicitly accepted the current version.
    POST: Requires accepted=true and the exact displayed version; appends a log.

    Historical gateway and general registration consents are never relabelled.
    """
    user = request.user
    if not isinstance(user, CustomUser):
        return Response(
            {"status": "unauthorized", "message": "Authentication required"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if request.method == "GET":
        existing = has_gateway_consent(user)

        # Get spark value for the response
        from capp.constants import SPARK_BASE_VALUE_KES
        from capp.classes.agent_gateway.pricing_service import get_live_eur_kes_rate

        try:
            fx_eur_kes = float(get_live_eur_kes_rate())
        except Exception:
            fx_eur_kes = float(getattr(settings, "AGENT_FX_EUR_KES", 145))

        return Response(
            {
                "status": "ok",
                "data": {
                    "confirmed": existing,
                    "version": AGENT_GATEWAY_CONSENT_VERSION,
                    "spark_value_kes": SPARK_BASE_VALUE_KES,
                    "fx_eur_kes": fx_eur_kes,
                    "markup_percent": getattr(settings, "AGENT_MARKUP_PERCENT", 0),
                },
            }
        )

    # Do not turn an empty POST, a stale browser, or a false checkbox into consent.
    if not isinstance(request.data, dict) or request.data.get("accepted") is not True:
        return Response(
            {
                "status": "consent_required",
                "message": "Explicitly accept the displayed developer gateway terms to continue.",
                "version": AGENT_GATEWAY_CONSENT_VERSION,
            },
            status=400,
        )
    if request.data.get("version") != AGENT_GATEWAY_CONSENT_VERSION:
        return Response(
            {
                "status": "consent_version_mismatch",
                "message": "Refresh and review the current developer gateway terms before accepting.",
                "version": AGENT_GATEWAY_CONSENT_VERSION,
            },
            status=409,
        )

    # POST — append only the version explicitly acknowledged by this request.
    # Get client IP
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR")

    user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]

    log = ComplianceConfirmationLog.objects.create(
        user=user,
        confirmation_type=ComplianceConfirmationLog.ConfirmationType.AGENT_GATEWAY,
        accepted=True,
        version=AGENT_GATEWAY_CONSENT_VERSION,
        ip_address=ip,
        user_agent=user_agent,
    )

    logger.info(
        "Agent Gateway: consent confirmed for user %s at %s (log_id=%s)",
        user.user_id,
        log.accepted_at.isoformat(),
        log.compliance_confirmation_log_id,
    )

    return Response(
        {
            "status": "ok",
            "data": {
                "confirmed": True,
                "version": AGENT_GATEWAY_CONSENT_VERSION,
                "accepted_at": log.accepted_at.isoformat(),
            },
        }
    )


@never_cache
@api_view(["GET"])
@authentication_classes([AccountJWTAuthentication, SessionAuthentication])
@permission_classes([GatewayAccountPermission])
def agent_gateway_pricing(request: Request) -> Response:
    """
    Model catalog with spark prices for the UwenzAi-Dev page (session auth).

    GET /api/v1/agent/pricing

    Lets builders see which models are available and what they cost in
    sparks (the currency they actually pay in) before creating a key —
    the same information OpenRouter shows for its models.
    """
    user = request.user
    if not isinstance(user, CustomUser):
        return Response(
            {"status": "unauthorized", "message": "Authentication required"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        models = AgentPricingService.list_models_with_spark_prices()
    except Exception:
        logger.exception("Agent Gateway: error building pricing catalog")
        return Response(
            {"status": "error", "message": "Failed to load the pricing catalog."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    from capp.constants import SPARK_BASE_VALUE_KES
    from capp.classes.agent_gateway.providers.metadata import PROVIDER_DETAILS

    return Response(
        {
            "status": "ok",
            "data": {
                "models": models,
                "providers": PROVIDER_DETAILS,
                "base_url": settings.AGENT_BASE_URL,
                "playground_limits": playground_service.PLAYGROUND_LIMITS,
                "markup_percent": getattr(settings, "AGENT_MARKUP_PERCENT", 0),
                "spark_value_kes": SPARK_BASE_VALUE_KES,
            },
        }
    )


# ============================================================================
# PROVIDER ENDPOINTS (Bearer Token Authentication)
# ============================================================================


@never_cache
@api_view(["GET"])
@authentication_classes([AgentGatewayAuthentication])
@permission_classes([GatewayAccountPermission])
@throttle_classes([AgentGatewayKeyThrottle])
def agent_models(request: Request) -> Response:
    """
    List available models for the Agent Gateway.

    GET /api/v1/models on the dedicated Agent service.
    Legacy alias: GET /api/v1/agent/models on both services.

    Authentication: Bearer uwenzai-dev-...

    Returns OpenAI-compatible model list.
    """
    try:
        models = AgentProxyService.list_models()
        return Response(
            {
                "object": "list",
                "data": models,
            }
        )
    except Exception as e:
        logger.exception("Agent Gateway: error listing models")
        return Response(
            {"error": {"status": "provider_not_configured", "message": str(e)}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@never_cache
@api_view(["POST"])
@authentication_classes([AgentGatewayAuthentication])
@permission_classes([GatewayAccountPermission])
@throttle_classes([AgentGatewayKeyThrottle])
def agent_chat_completions(request: Request) -> Response:
    """
    Execute a chat completion request.

    POST /api/v1/chat/completions on the dedicated Agent service.
    Legacy alias: POST /api/v1/agent/chat/completions on both services.

    Authentication: Bearer uwenzai-dev-...

    Request body (OpenAI-compatible):
    - model: Model identifier (optional, uses default if not provided)
    - messages: List of message dicts with role and content
    - max_tokens: Maximum tokens to generate
    - temperature: Temperature (0.0 - 1.0)
    - top_p: Top-p sampling
    - stream: Boolean — when true the response is an SSE stream (the upstream
      call itself stays non-streaming; the completed response is emitted as
      OpenAI-shaped chat.completion.chunk events, so streaming clients work
      while billing stays exact and idempotent)
    - client_operation_id: Optional idempotency key

    Returns OpenAI-compatible response with usage block.
    """
    # Get the API key from authentication
    # AgentGatewayAuthentication returns (user, key_record)
    # so request.user = CustomUser, request.auth = AgentAPIKey
    api_key = request.auth
    if not isinstance(api_key, AgentAPIKey):
        logger.warning("Agent Gateway: auth failed - no valid key in request.auth")
        return Response(
            {"error": {"status": "invalid_api_key", "message": "Invalid authentication"}},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Provider URLs are settings-only. Never accept a user-supplied endpoint.
    if not isinstance(request.data, dict):
        return Response({"error": {"status": "invalid_request", "message": "Send a JSON object"}}, status=400)
    if any(field in request.data for field in ("base_url", "endpoint", "endpoint_url", "api_url", "api_key")):
        return Response(
            {
                "error": {
                    "status": "invalid_request",
                    "message": (
                        "Provider endpoints and credentials cannot be overridden. Choose provider and model "
                        "from the catalog."
                    ),
                }
            },
            status=400,
        )
    # Parse request body
    model = request.data.get("model")
    messages = request.data.get("messages", [])
    params = {
        "max_tokens": request.data.get("max_tokens"),
        "temperature": request.data.get("temperature"),
        "top_p": request.data.get("top_p"),
        "stream": request.data.get("stream", False),
        "stream_options": request.data.get("stream_options"),
        "stop": request.data.get("stop"),
        "presence_penalty": request.data.get("presence_penalty"),
        "frequency_penalty": request.data.get("frequency_penalty"),
        "user": request.data.get("user"),
        "tools": request.data.get("tools"),
        "tool_choice": request.data.get("tool_choice"),
    }

    client_operation_id = request.data.get("client_operation_id")

    provider_name = request.data.get("provider")
    # Streaming is emulated end-to-end: the upstream call and settlement run
    # exactly as in the non-streaming path, then the finished response is
    # wrapped in OpenAI-shaped SSE chunks (see _sse_frames). Errors stay JSON
    # with their HTTP status — the standard OpenAI behavior for pre-stream
    # failures, which every SSE client handles.

    try:
        result = AgentProxyService.execute_request(
            key=api_key,
            model=model,
            messages=messages,
            params=params,
            client_operation_id=client_operation_id,
            provider_name=provider_name,
        )

        # Check for error in result
        if "error" in result:
            error = result["error"]
            status_code = status.HTTP_400_BAD_REQUEST

            if error.get("status") == "insufficient_sparks":
                status_code = status.HTTP_402_PAYMENT_REQUIRED
            elif error.get("status") == "budget_exceeded":
                status_code = status.HTTP_429_TOO_MANY_REQUESTS
            elif error.get("status") == "upstream_error":
                status_code = status.HTTP_502_BAD_GATEWAY
            elif error.get("status") == "invalid_api_key":
                status_code = status.HTTP_401_UNAUTHORIZED
            elif error.get("status") == "refund_pending":
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            elif error.get("status") == "idempotency_conflict":
                status_code = status.HTTP_409_CONFLICT

            return Response(result, status=status_code)

        if request.data.get("stream"):
            return _sse_stream_response(result)
        return Response(result)

    except ValueError as exc:
        return Response({"error": {"status": "invalid_request", "message": str(exc)}}, status=400)
    except (GatewayBusy, GatewayUnavailable) as exc:
        return Response(
            {
                "error": {
                    "status": "gateway_busy" if exc.status_code == 409 else "gateway_unavailable",
                    "message": str(exc.detail),
                }
            },
            status=exc.status_code,
        )
    except InsufficientBalance:
        return Response(
            {"error": {"status": "insufficient_sparks", "message": "Insufficient sparks"}},
            status=status.HTTP_402_PAYMENT_REQUIRED,
        )
    except IdempotencyKeyConflict as e:
        return Response(
            {"error": {"status": "idempotency_conflict", "message": str(e)}},
            status=status.HTTP_409_CONFLICT,
        )
    except Exception:
        logger.error("Agent Gateway: chat completion failed; provider details omitted")
        return Response(
            {"error": {"status": "internal_error", "message": "Internal server error"}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _sse_frames(payload: dict) -> str:
    """OpenAI-shaped SSE stream built from a completed (non-streamed) result.

    The upstream call and settlement happen exactly as in the non-streaming
    path; the finished response is then emitted as chat.completion.chunk
    events — a role chunk, one content chunk, a final chunk carrying
    finish_reason and usage (per OpenAI's stream_options.include_usage
    shape, harmless for clients that ignore it), then `data: [DONE]`.
    Streaming-only clients (agent CLIs, SSE parsers) therefore work, while
    billing stays exact and idempotent. The content is served only after
    settlement, so a mid-stream disconnect cannot split a charge.
    """
    import time

    model = payload.get("model", "")
    created = payload.get("created") or int(time.time())
    completion_id = payload.get("id") or f"chatcmpl-uwenzai-{created}"
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    finish_reason = choice.get("finish_reason") or "stop"
    usage = payload.get("usage") or {}
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        # Streamed tool calls use delta.tool_calls with an index per call; the
        # completed list is emitted as one delta — legal for SSE emulation and
        # the shape OpenAI clients accumulate over.
        tool_calls = [{"index": i, **call} for i, call in enumerate(tool_calls) if isinstance(call, dict)] or None

    def chunk(delta, finish=None, with_usage=False):
        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish,
                }
            ],
        }
        if with_usage and usage:
            body["usage"] = usage
        return "data: {}\n\n".format(json.dumps(body, separators=(",", ":")))

    frames = [chunk({"role": "assistant"})]
    if content:
        frames.append(chunk({"content": content}))
    if tool_calls:
        frames.append(chunk({"tool_calls": tool_calls}))
    frames.append(chunk({}, finish=finish_reason, with_usage=True))
    frames.append("data: [DONE]\n\n")
    return "".join(frames)


def _sse_stream_response(payload: dict) -> StreamingHttpResponse:
    frames = _sse_frames(payload)

    async def frame_iterator():
        # Async so ASGI serves the stream natively; a sync iterator made uvicorn
        # log "StreamingHttpResponse must consume synchronous iterators" per
        # request (harmless, but noisy in production logs).
        yield frames.encode()

    response = StreamingHttpResponse(frame_iterator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# Routing: agent_public_urls.py exposes only inference at "api/v1/" on the
# dedicated Agent service; agent_gateway_urls.py keeps "api/v1/agent/" on both.


@never_cache
@api_view(["POST"])
@authentication_classes([AccountJWTAuthentication, SessionAuthentication])
@permission_classes([GatewayAccountPermission])
def agent_playground(request):
    """Owned-key text-only chat. Uses exactly the same billing path as external agents."""
    if not has_gateway_consent(request.user):
        return Response(
            {
                "error": {
                    "status": "consent_required",
                    "message": "Accept the developer gateway consent before using the playground.",
                    "version": AGENT_GATEWAY_CONSENT_VERSION,
                }
            },
            status=403,
        )
    try:
        # Bound parser work before JSON/tokenization, including excessive unknown fields.
        if len(request.body) > 65536:
            raise ValueError("Request exceeds 65536 bytes; shorten the conversation")
        data = playground_service.validate_playground(request.data)
        key = AgentKeyService.get_key(request.user, data["key_id"])
        if key is None:
            return Response(
                {"error": {"status": "invalid_api_key", "message": "Key not found. Select one of your own keys."}},
                status=404,
            )
        if not key.can_be_used():
            return Response(
                {
                    "error": {
                        "status": "invalid_api_key",
                        "message": "This key is revoked or expired. Select an active key.",
                    }
                },
                status=400,
            )
        AgentProxyService.validate_request(
            data["model"], data["messages"], {"max_tokens": data["max_tokens"]}, data["provider"]
        )
        from capp.throttles import AgentPlaygroundUserThrottle

        throttle = AgentPlaygroundUserThrottle()
        if not throttle.allow_request(request, None):
            return Response(
                {
                    "error": {
                        "status": "rate_limited",
                        "message": "Playground limit reached. Wait one minute before trying again.",
                    }
                },
                status=429,
            )
        with gateway_user_lock(request.user):
            ticket = None
            if data["save_history"]:
                ticket = playground_service.begin_history(request.user, data["model"], data["provider"])
            result = AgentProxyService.execute_request(
                key=key,
                model=data["model"],
                provider_name=data["provider"],
                messages=data["messages"],
                params={"max_tokens": data["max_tokens"]},
                client_operation_id=data["client_operation_id"],
                source=AgentUsageLog.Source.PLAYGROUND,
            )
            if "error" in result:
                code = result["error"].get("status")
                http_status = {
                    "insufficient_sparks": 402,
                    "budget_exceeded": 429,
                    "invalid_api_key": 400,
                    "upstream_error": 502,
                    "idempotency_conflict": 409,
                    "refund_pending": 503,
                }.get(code, 400)
                return Response(result, status=http_status)
            result.setdefault("_uwenzai", {})["history_saved"] = False
            if ticket:
                try:
                    result["_uwenzai"]["history_saved"] = playground_service.save_history(
                        ticket, request.user, data["model"], data["provider"], data["messages"], result
                    )
                except Exception:
                    # Billing already succeeded: never turn a history failure into a retryable chat failure.
                    result["_uwenzai"]["history_warning"] = "The reply succeeded but history was not saved."
            return Response(result)
    except ValueError as exc:
        return Response({"error": {"status": "invalid_request", "message": str(exc)}}, status=400)
    except (GatewayBusy, GatewayUnavailable) as exc:
        return Response(
            {
                "error": {
                    "status": "gateway_busy" if exc.status_code == 409 else "gateway_unavailable",
                    "message": str(exc.detail),
                }
            },
            status=exc.status_code,
        )
    except Exception:
        # No provider response/exception text: it may include prompt content or secrets.
        return Response(
            {
                "error": {
                    "status": "gateway_unavailable",
                    "message": (
                        "The playground is temporarily unavailable. Check usage before retrying the same "
                        "operation ID."
                    ),
                }
            },
            status=503,
        )


@never_cache
@api_view(["GET", "DELETE"])
@authentication_classes([AccountJWTAuthentication, SessionAuthentication])
@permission_classes([GatewayAccountPermission])
def agent_playground_history(request):
    try:
        model, provider = playground_service.validate_pair(
            request.query_params.get("model"), request.query_params.get("provider")
        )
        if request.method == "DELETE":
            playground_service.delete_history(request.user, model, provider)
            messages = []
        else:
            messages = playground_service.read_history(request.user, model, provider)
        return Response({"status": "ok", "data": {"messages": messages}})
    except ValueError as exc:
        return Response({"status": "invalid_request", "message": str(exc)}, status=400)
    except Exception:
        return Response(
            {
                "status": "history_unavailable",
                "message": "Saved history is unavailable. Try again or delete it to start fresh.",
            },
            status=503,
        )
