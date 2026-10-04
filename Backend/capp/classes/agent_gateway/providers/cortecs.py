"""
Cortecs Agent Provider Implementation

Implements the AgentProvider protocol for Cortecs API.
Uses the dedicated CORTECS_UWENZAI_AGENT_* settings, NOT the companion-app keys.

Model catalog is fetched live from Cortecs' /models endpoint and cached (TTL 30 min).
Prices are upserted into AgentModelPrice so admin can disable models as a kill-switch.
DEFAULT_CORTECS_MODELS is a last-resort fallback for tests only.
"""

import httpx
import logging
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache

from capp.classes.agent_gateway.providers.metadata import catalog_metadata

from capp.classes.agent_gateway.providers.base import (
    ModelInfo,
    ProviderResult,
)

logger = logging.getLogger(__name__)

_MODELS_CACHE_KEY = "agent_gateway_cortecs_models"
_MODELS_CACHE_TTL = 1800  # 30 minutes


# Last-resort fallback for tests only (never the source of truth in production).
# The prices below are illustrative placeholders, not real provider prices.
DEFAULT_CORTECS_MODELS = [
    {
        "id": "deepseek-v4-flash",
        "name": "DeepSeek V4 Flash",
        "input_price_per_1m": Decimal("1.00"),
        "output_price_per_1m": Decimal("2.00"),
        "max_context": 131072,
        "description": "Fast, cost-effective model with strong reasoning",
    },
    {
        "id": "kimi-k3",
        "name": "Kimi K3",
        "input_price_per_1m": Decimal("2.00"),
        "output_price_per_1m": Decimal("4.00"),
        "max_context": 131072,
        "description": "Advanced reasoning model",
    },
    {
        "id": "glm-5",
        "name": "GLM-5",
        "input_price_per_1m": Decimal("1.50"),
        "output_price_per_1m": Decimal("3.00"),
        "max_context": 131072,
        "description": "General-purpose model with strong performance",
    },
]


class CortecsAgentProvider:
    """
    Cortecs provider implementation for Agent Gateway.

    Uses the dedicated CORTECS_UWENZAI_AGENT_* settings, NOT the companion-app keys.
    Fetches model catalog live from Cortecs and caches it.
    """

    name = "cortecs"

    def __init__(self):
        self._api_key = getattr(settings, "CORTECS_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(settings, "CORTECS_UWENZAI_AGENT_BASE_URL", "https://api.cortecs.ai/v1")

    def is_configured(self) -> bool:
        """Check if Cortecs agent provider is configured (has dedicated API key)."""
        return bool(self._api_key)

    def _get_headers(self) -> dict:
        """Build auth headers for Cortecs API calls."""
        return {
            "Authorization": "Bearer {}".format(self._api_key),
            "Content-Type": "application/json",
        }

    def _fetch_models_from_api(self) -> list[dict] | None:
        """
        Fetch model list from Cortecs API (GET /v1/models?currency=EUR&extended=true).

        Parses the documented response shape with nested `pricing` fields.
        Returns list of model dicts or None if fetch fails.
        """
        if not self._api_key:
            return None

        url = "{}/models".format(self._base_url.rstrip("/"))
        try:
            response = httpx.get(
                url,
                headers=self._get_headers(),
                params={"currency": "EUR", "extended": "true"},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            models_data = data.get("data", [])
            if not models_data:
                logger.warning("Agent Gateway: Cortecs /models returned empty list")
                return None

            result = []
            for m in models_data:
                model_id = m.get("id")
                if not model_id:
                    continue

                # Parse nested pricing block (documented Cortecs shape)
                pricing = m.get("pricing") or {}
                currency = pricing.get("currency")
                input_price = pricing.get("input_token")
                output_price = pricing.get("output_token")

                if input_price is None or output_price is None:
                    # Do not place an unpriced model into the billable catalog
                    logger.warning("Agent Gateway: skipping unpriced Cortecs model %s", model_id)
                    continue

                if currency and currency != "EUR":
                    # Reject non-EUR prices; do not silently mislabel
                    logger.warning(
                        "Agent Gateway: skipping model %s with unexpected currency %s",
                        model_id,
                        currency,
                    )
                    continue

                result.append(
                    {
                        "id": model_id,
                        "name": m.get("id"),
                        "input_price_per_1m": Decimal(str(input_price)),
                        "currency": "EUR",
                        "output_price_per_1m": Decimal(str(output_price)),
                        "max_context": m.get("context_size"),
                        "max_output_tokens": m.get("max_output_tokens"),
                        "supported_features": m.get("supported_features"),
                        "providers": m.get("providers", []),
                        "description": m.get("description"),
                    }
                )
            return result

        except Exception as e:
            logger.warning("Agent Gateway: failed to fetch Cortecs models: %s", e)
            return None

    def _upsert_catalog_to_db(self, models: list[dict]) -> None:
        """
        Upsert fetched models into AgentModelPrice.

        Uses get_or_create so admin is_active state is preserved:
        - New models are created with is_active=True
        - Existing models get their prices updated, but is_active is NOT touched
        """
        from capp.models import AgentModelPrice

        for m in models:
            input_price = m.get("input_price_per_1m", Decimal("0"))
            output_price = m.get("output_price_per_1m", Decimal("0"))
            if input_price <= 0 and output_price <= 0:
                continue

            price, created = AgentModelPrice.objects.get_or_create(
                provider=self.name,
                model=m["id"],
                defaults={
                    "input_price_per_1m": input_price,
                    "output_price_per_1m": output_price,
                    "currency": "EUR",
                    "is_active": True,
                },
            )

            metadata = catalog_metadata(m)
            if metadata:
                AgentModelPrice.objects.filter(pk=price.pk).update(**metadata)

            if not created:
                # Update prices but preserve admin's is_active setting
                price.input_price_per_1m = input_price
                price.output_price_per_1m = output_price
                price.save(
                    update_fields=[
                        "input_price_per_1m",
                        "output_price_per_1m",
                        "updated_at",
                    ]
                )

    def list_models(self) -> list[ModelInfo]:
        """
        List available Cortecs models for the Agent Gateway.

        Resolution order:
        1. Django cache (TTL 30 min)
        2. Live Cortecs API fetch (GET /v1/models) + upsert to AgentModelPrice
        3. AgentModelPrice database (if API failed but we have prior data)
        4. DEFAULT_CORTECS_MODELS (last-resort fallback, tests only)

        Models disabled via admin (is_active=False) are hidden from the list.
        """
        from capp.models import AgentModelPrice

        # 1. Check cache
        cached = cache.get(_MODELS_CACHE_KEY)
        if cached is not None:
            # Filter by admin kill-switch
            disabled = set(
                AgentModelPrice.objects.filter(provider=self.name, is_active=False).values_list("model", flat=True)
            )
            return [
                ModelInfo(
                    id=m["id"],
                    name=m.get("name", m["id"]),
                    provider=self.name,
                    input_price_per_1m=m.get("input_price_per_1m", Decimal("0")),
                    output_price_per_1m=m.get("output_price_per_1m", Decimal("0")),
                    max_context=m.get("max_context"),
                    description=m.get("description"),
                )
                for m in cached
                if m["id"] not in disabled
            ]

        # 2. Try live API fetch
        api_models = self._fetch_models_from_api()
        if api_models:
            # Cache the result
            cache.set(_MODELS_CACHE_KEY, api_models, _MODELS_CACHE_TTL)
            # Upsert to database for admin kill-switch
            try:
                self._upsert_catalog_to_db(api_models)
            except Exception as e:
                logger.warning("Agent Gateway: failed to upsert catalog to DB: %s", e)

            # Filter by admin kill-switch
            disabled = set(
                AgentModelPrice.objects.filter(provider=self.name, is_active=False).values_list("model", flat=True)
            )
            return [
                ModelInfo(
                    id=m["id"],
                    name=m.get("name", m["id"]),
                    provider=self.name,
                    input_price_per_1m=m.get("input_price_per_1m", Decimal("0")),
                    output_price_per_1m=m.get("output_price_per_1m", Decimal("0")),
                    max_context=m.get("max_context"),
                    description=m.get("description"),
                )
                for m in api_models
                if m["id"] not in disabled
            ]

        # 3. Try database (prior cached data)
        db_prices = AgentModelPrice.objects.filter(provider=self.name, is_active=True).order_by("model")
        if db_prices.exists():
            models = []
            for p in db_prices:
                models.append(
                    ModelInfo(
                        id=p.model,
                        name=p.model,
                        provider=self.name,
                        input_price_per_1m=p.input_price_per_1m,
                        output_price_per_1m=p.output_price_per_1m,
                        max_context=p.max_context,
                        description=p.description or None,
                    )
                )
            return models

        # 4. Last-resort fallback (tests only)
        logger.warning("Agent Gateway: using DEFAULT_CORTECS_MODELS fallback")
        return [
            ModelInfo(
                id=m["id"],
                name=m["name"],
                provider=self.name,
                input_price_per_1m=m["input_price_per_1m"],
                output_price_per_1m=m["output_price_per_1m"],
                max_context=m.get("max_context"),
                description=m.get("description"),
            )
            for m in DEFAULT_CORTECS_MODELS
        ]

    def refresh_catalog(self) -> int | None:
        """
        Force a live Cortecs fetch, refresh the cache, and upsert prices to the DB.

        Used by the scheduled catalog sync (and the sync_agent_catalog command).
        Unlike list_models(), this bypasses the cache and reports honestly: it
        returns the number of fetched models, or None when the live fetch failed
        (in which case existing catalog rows are left untouched). Only database
        upsert errors propagate to the caller.
        """
        fetched = self._fetch_models_from_api()
        if not fetched:
            return None
        cache.set(_MODELS_CACHE_KEY, fetched, _MODELS_CACHE_TTL)
        self._upsert_catalog_to_db(fetched)
        return len(fetched)

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> ProviderResult:
        """
        Execute a chat completion request via Cortecs.

        Uses the dedicated CORTECS_UWENZAI_AGENT_API_KEY, never the companion key.
        Makes a direct HTTP call (does not reuse CortecsClient which reads companion keys).
        """
        if not self._api_key:
            raise RuntimeError("Cortecs agent provider is not configured")

        url = "{}/chat/completions".format(self._base_url.rstrip("/"))

        # Build the request payload (OpenAI-compatible)
        payload = {
            "model": model,
            "messages": messages,
        }
        # Merge in additional params, excluding None values
        for k, v in params.items():
            if v is not None and k not in ("model", "messages"):
                payload[k] = v

        # Remove stream parameter (v1 doesn't support streaming for reliable billing)
        if "stream" in payload:
            del payload["stream"]
            logger.info("Agent Gateway: streaming disabled for reliable billing")

        try:
            response = httpx.post(
                url,
                headers=self._get_headers(),
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            # Extract usage from response
            usage = data.get("usage", {})

            # Get the completion text
            choices = data.get("choices", [])
            text = ""
            finish_reason = None
            tool_calls = None
            if choices:
                first_choice = choices[0]
                message = first_choice.get("message", {})
                text = message.get("content", "")
                finish_reason = first_choice.get("finish_reason")
                # Relay structured tool calls verbatim, synthesizing an id when
                # the upstream omits one — some gateways return id-less calls,
                # and a client that receives finish_reason=tool_calls without
                # any calls stalls its agent loop forever.
                upstream_tool_calls = message.get("tool_calls")
                if isinstance(upstream_tool_calls, list) and upstream_tool_calls:
                    relayed = []
                    for i, call in enumerate(upstream_tool_calls):
                        if not isinstance(call, dict):
                            continue
                        call = dict(call)
                        if not call.get("id"):
                            call["id"] = "call_{}".format(i)
                        relayed.append(call)
                    tool_calls = relayed or None

            return ProviderResult(
                text=text,
                usage=usage,
                status="success",
                finish_reason=finish_reason,
                model=model,
                tool_calls=tool_calls,
            )
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_message = "Cortecs API error: {}".format(status_code)
            try:
                error_detail = e.response.json().get("message", "") or e.response.text
                error_message = "Cortecs API error: {} - {}".format(status_code, error_detail)
            except Exception:
                pass

            return ProviderResult(
                text="",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                status="upstream_error",
                error_message=error_message,
                finish_reason=None,
                model=model,
            )
        except httpx.RequestError as e:
            return ProviderResult(
                text="",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                status="upstream_error",
                error_message="Cortecs request error: {}".format(str(e)),
                finish_reason=None,
                model=model,
            )
        except Exception as e:
            return ProviderResult(
                text="",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                status="upstream_error",
                error_message="Cortecs error: {}".format(str(e)),
                finish_reason=None,
                model=model,
            )
