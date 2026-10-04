"""
Shared base for OpenAI-compatible providers.

All providers in this family use the same OpenAI-compatible API shape:
- GET /v1/models for catalog
- POST /v1/chat/completions for inference
- Bearer token auth

Only the base URL, API key, and currency differ.
"""

import httpx
import logging
from decimal import Decimal
from typing import Any

from django.core.cache import cache

from capp.classes.agent_gateway.providers.metadata import catalog_metadata

from capp.classes.agent_gateway.providers.base import (
    ModelInfo,
    ProviderResult,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """
    Base implementation for any OpenAI-compatible provider.
    Subclasses set: name, _api_key, _base_url, _currency, _models_cache_key, _models_cache_ttl
    """

    name = "openai_compatible"
    _currency = "USD"
    _models_cache_key = "agent_gateway_models"
    _models_cache_ttl = 1800  # 30 minutes
    # Operator-maintained prices per 1M tokens (input, output) for providers
    # whose /models endpoint lists models without pricing. Empty by default;
    # populated by subclasses from settings.
    _model_prices = {}

    def __init__(self):
        self._api_key = None
        self._base_url = None

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_headers(self) -> dict:
        return {
            "Authorization": "Bearer {}".format(self._api_key),
            "Content-Type": "application/json",
        }

    def _fetch_models_from_api(self) -> list[dict] | None:
        """Fetch model list from the provider's /models endpoint."""
        if not self._api_key:
            return None

        url = "{}/models".format(self._base_url.rstrip("/"))
        try:
            response = httpx.get(
                url,
                headers=self._get_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            models_data = data.get("data", [])
            if not models_data:
                logger.warning("Agent Gateway: %s /models returned empty list", self.name)
                return None

            result = []
            for m in models_data:
                model_id = m.get("id")
                if not model_id:
                    continue

                # Try nested pricing (Cortecs-style input_token or Fikra-style
                # plain input, each per 1M tokens in the provider's currency).
                pricing = m.get("pricing") or {}

                def first_present(*values):
                    for value in values:
                        if value is not None:
                            return value
                    return None

                input_price = first_present(
                    pricing.get("input_token"), pricing.get("input_price"), pricing.get("input")
                )
                output_price = first_present(
                    pricing.get("output_token"), pricing.get("output_price"), pricing.get("output")
                )

                # Some /models endpoints (e.g. Mistral's) list models without
                # prices; fall back to the operator-maintained price map.
                if input_price is None or output_price is None:
                    configured_price = self._model_prices.get(model_id)
                    if configured_price:
                        input_price = configured_price[0]
                        output_price = configured_price[1]

                # If still unpriced, skip (don't create zero-price entries)
                if input_price is None or output_price is None:
                    logger.warning("Agent Gateway: skipping unpriced %s model %s", self.name, model_id)
                    continue

                result.append(
                    {
                        "id": model_id,
                        "name": m.get("id"),
                        "input_price_per_1m": Decimal(str(input_price)),
                        "output_price_per_1m": Decimal(str(output_price)),
                        "currency": self._currency,
                        # AKI reports context length as max_model_len (0 = unspecified);
                        # other providers use context_size or max_context.
                        "max_context": m.get("context_size")
                        or m.get("max_context")
                        or (m.get("max_model_len") or None),
                        "description": m.get("description"),
                        "max_output_tokens": m.get("max_output_tokens"),
                        "supported_features": m.get("supported_features"),
                    }
                )
            return result

        except Exception as e:
            logger.warning("Agent Gateway: failed to fetch %s models: %s", self.name, e)
            return None

    def _upsert_catalog_to_db(self, models: list[dict]) -> None:
        """Upsert fetched models into AgentModelPrice, preserving admin is_active."""
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
                    "currency": self._currency,
                    "is_active": True,
                },
            )

            metadata = catalog_metadata(m)
            if metadata:
                AgentModelPrice.objects.filter(pk=price.pk).update(**metadata)

            if not created:
                price.input_price_per_1m = input_price
                price.output_price_per_1m = output_price
                price.currency = self._currency
                price.save(
                    update_fields=[
                        "input_price_per_1m",
                        "output_price_per_1m",
                        "currency",
                        "updated_at",
                    ]
                )

    def list_models(self) -> list[ModelInfo]:
        """List available models from cache, API, database, or fallback."""
        from capp.models import AgentModelPrice

        # 1. Check cache
        cached = cache.get(self._models_cache_key)
        if cached is not None:
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
                    currency=m.get("currency", self._currency),
                    max_context=m.get("max_context"),
                    description=m.get("description"),
                )
                for m in cached
                if m["id"] not in disabled
            ]

        # 2. Try live API fetch
        api_models = self._fetch_models_from_api()
        if api_models:
            cache.set(self._models_cache_key, api_models, self._models_cache_ttl)
            try:
                self._upsert_catalog_to_db(api_models)
            except Exception as e:
                logger.warning("Agent Gateway: failed to upsert %s catalog to DB: %s", self.name, e)

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
                    currency=m.get("currency", self._currency),
                    max_context=m.get("max_context"),
                    description=m.get("description"),
                )
                for m in api_models
                if m["id"] not in disabled
            ]

        # 3. Try database (prior cached data)
        db_prices = AgentModelPrice.objects.filter(provider=self.name, is_active=True).order_by("model")
        if db_prices.exists():
            return [
                ModelInfo(
                    id=p.model,
                    name=p.model,
                    provider=self.name,
                    input_price_per_1m=p.input_price_per_1m,
                    output_price_per_1m=p.output_price_per_1m,
                    currency=p.currency,
                    max_context=p.max_context,
                    description=p.description or None,
                )
                for p in db_prices
            ]

        # 4. No fallback — return empty (unlike Cortecs, we don't hardcode models)
        return []

    def refresh_catalog(self) -> int | None:
        """
        Force a live catalog fetch, refresh the cache, and upsert prices to the DB.

        Used by the scheduled catalog sync (and the sync_agent_catalog command).
        Unlike list_models(), this bypasses the cache and reports honestly: it
        returns the number of models that carried a price (upstream or via the
        operator map), 0 when the fetch succeeded but nothing was priced, or
        None when the live fetch failed (in which case existing catalog rows
        are left untouched). Only database upsert errors propagate.
        """
        fetched = self._fetch_models_from_api()
        if fetched is None:
            return None
        if fetched:
            cache.set(self._models_cache_key, fetched, self._models_cache_ttl)
            self._upsert_catalog_to_db(fetched)
        return len(fetched)

    def _chat_completions_url(self, model: str) -> str:
        """Chat-completions endpoint for ``model``.

        Providers whose upstream serves different models under different base
        paths override this per model.
        """
        return "{}/chat/completions".format(self._base_url.rstrip("/"))

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> ProviderResult:
        """Execute a chat completion request via the provider's API."""
        if not self._api_key:
            raise RuntimeError("{} provider is not configured".format(self.name))

        url = self._chat_completions_url(model)

        payload = {
            "model": model,
            "messages": messages,
        }
        for k, v in params.items():
            if v is not None and k not in ("model", "messages"):
                payload[k] = v

        if "stream" in payload:
            del payload["stream"]
            logger.info("Agent Gateway: streaming disabled for reliable billing")
        if "stream_options" in payload:
            # Normalized away at the gateway boundary; kept here as a belt-and-
            # braces guard because upstreams 400 on stream_options without stream.
            del payload["stream_options"]

        try:
            response = httpx.post(
                url,
                headers=self._get_headers(),
                json=payload,
                # Non-streamed generations complete inside one HTTP call, so the
                # read budget must cover large agent outputs: 8k tokens at ~35
                # tok/s is ~4 minutes. 60s cut those off mid-generation (502,
                # refunded, agent retries forever). Connect stays fast-fail.
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
            response.raise_for_status()
            data = response.json()

            usage = data.get("usage", {})
            choices = data.get("choices", [])
            text = ""
            finish_reason = None
            tool_calls = None
            if choices:
                first_choice = choices[0]
                message = first_choice.get("message", {})
                text = message.get("content") or ""
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
            error_message = "{} API error: {}".format(self.name, status_code)
            try:
                error_detail = e.response.json().get("message", "") or e.response.text
                error_message = "{} API error: {} - {}".format(self.name, status_code, error_detail)
            except Exception:
                pass
            return ProviderResult(
                text="",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                status="upstream_error",
                error_message=error_message,
                upstream_status_code=status_code,
                finish_reason=None,
                model=model,
            )
        except httpx.RequestError as e:
            return ProviderResult(
                text="",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                status="upstream_error",
                error_message="{} request error: {}".format(self.name, str(e)),
                finish_reason=None,
                model=model,
            )
        except Exception as e:
            return ProviderResult(
                text="",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
                status="upstream_error",
                error_message="{} error: {}".format(self.name, str(e)),
                finish_reason=None,
                model=model,
            )
