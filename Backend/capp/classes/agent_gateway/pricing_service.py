"""
Agent Gateway Pricing Service

Handles pricing calculations for Agent Gateway requests.

Supports multiple providers with different native currencies (EUR, USD).
Each provider carries its own markup, resolved by get_markup_percent().

FX rates are resolved in this order:
1. Django cache (TTL = AGENT_EXCHANGE_RATE_CACHE_TTL, default 3600s)
2. Live API fetch (if AGENT_EXCHANGE_RATE_API_URL is configured)
3. Fallback to AGENT_FX_EUR_KES / AGENT_FX_USD_KES settings
"""

import logging
import math
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

from capp.constants import SPARK_BASE_VALUE_KES
from capp.models import AgentModelPrice

logger = logging.getLogger(__name__)

_FX_CACHE_PREFIX = "agent_gateway_fx_"  # e.g. "agent_gateway_fx_eur_kes"

# Maps a provider name to its dedicated markup setting. Providers without an
# entry (or without the setting present) fall back to AGENT_MARKUP_PERCENT.
_PROVIDER_MARKUP_SETTINGS = {
    "cortecs": "CORTECS_UWENZAI_AGENT_MARKUP_PERCENT",
    "mistral": "MISTRAL_UWENZAI_AGENT_MARKUP_PERCENT",
    "fikra": "FIKRA_UWENZAI_AGENT_MARKUP_PERCENT",
    "inceptron": "INCEPTRON_UWENZAI_AGENT_MARKUP_PERCENT",
    "aki": "AKI_UWENZAI_AGENT_MARKUP_PERCENT",
    "ovh": "OVH_AI_UWENZAI_AGENT_MARKUP_PERCENT",
    "tensorx": "TENSORX_UWENZAI_AGENT_MARKUP_PERCENT",
}


def get_markup_percent(provider=None):
    """Resolve the markup percentage for a provider.

    Uses the provider's dedicated setting when one exists, otherwise the
    global AGENT_MARKUP_PERCENT default. Kept settings-based (not
    provider-instance-based) so pricing works even for providers whose
    credentials are not currently configured.
    """
    setting_name = _PROVIDER_MARKUP_SETTINGS.get(provider or "")
    if setting_name:
        override = getattr(settings, setting_name, None)
        if override is not None:
            return float(override)
    return float(getattr(settings, "AGENT_MARKUP_PERCENT", 0))


def cheapest_provider_for_model(model):
    """Deterministic default routing for a model served by several providers.

    Returns the provider name serving `model` with the lowest effective cost
    (marked-up KES for 1M input + 1M output tokens), tie-broken by provider
    name so the choice is stable across requests and restarts. This mirrors
    the price-first default of established aggregators without adopting their
    probabilistic load balancing: UwenzAi picks one deterministic provider so
    billing and behaviour stay predictable. Returns None when no active
    catalog row exists for the model.
    """
    from capp.models import AgentModelPrice

    rows = list(AgentModelPrice.objects.filter(model=model, is_active=True))
    if not rows:
        return None

    def effective_cost(row):
        try:
            blended = AgentPricingService.calculate_cost(row.provider, row.model, 1_000_000, 1_000_000)["charge_kes"]
        except Exception:
            logger.warning(
                "Agent Gateway: cost comparison failed for %s/%s; sorting last",
                row.provider,
                row.model,
            )
            return Decimal("Infinity")
        return blended

    ordered = sorted(rows, key=lambda row: (effective_cost(row), row.provider))
    return ordered[0].provider


class ExchangeRateError(Exception):
    """Raised when exchange rate fetch fails and no fallback is configured."""

    pass


def _parse_json_path(data, path):
    """Extract a value from nested dict using dotted notation."""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


# Currencies whose default-rate fallback has been logged once this process;
# the fallback is reached on every request when no FX API is configured, but
# the info line only needs to appear once.
_FX_DEFAULT_LOGGED: set = set()


def _get_fx_rate(currency: str) -> Decimal:
    """
    Resolve a currency-to-KES exchange rate.

    Order of resolution:
    1. Django cache (TTL = AGENT_EXCHANGE_RATE_CACHE_TTL)
    2. Live API fetch (if AGENT_EXCHANGE_RATE_API_URL is configured)
    3. Fallback to AGENT_FX_{currency}_KES setting

    Returns:
        Exchange rate as Decimal
    """
    currency_upper = currency.upper()
    cache_key = "{}{}_kes".format(_FX_CACHE_PREFIX, currency_upper.lower())
    cache_ttl = getattr(settings, "AGENT_EXCHANGE_RATE_CACHE_TTL", 3600)

    # 1. Check cache
    cached_rate = cache.get(cache_key)
    if cached_rate is not None:
        logger.debug("Agent Gateway: using cached %s->KES rate: %s", currency_upper, cached_rate)
        return Decimal(str(cached_rate))

    # 2. Try live fetch if configured
    exchange_api_url = getattr(settings, "AGENT_EXCHANGE_RATE_API_URL", None)
    exchange_api_key = getattr(settings, "AGENT_EXCHANGE_RATE_API_KEY", None)
    json_path = getattr(settings, "AGENT_EXCHANGE_RATE_JSON_PATH", "rates.KES")

    if exchange_api_url:
        try:
            import requests

            headers = {}
            if exchange_api_key:
                headers["Authorization"] = "Bearer " + exchange_api_key

            # Append currency to the URL if it's a base-currency endpoint
            # e.g. https://api.exchangerate-api.com/v4/latest/EUR
            url = exchange_api_url
            if not url.endswith(currency_upper) and "/latest/" in url:
                url = url.rsplit("/", 1)[0] + "/" + currency_upper

            response = requests.get(url, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()

            rate = _parse_json_path(data, json_path)
            if rate is None:
                rate = data.get("{}_KES".format(currency_upper)) or data.get("KES") or data.get("rate")

            if rate is not None:
                rate_decimal = Decimal(str(rate))
                cache.set(cache_key, str(rate_decimal), cache_ttl)
                logger.info("Agent Gateway: fetched live %s->KES rate: %s", currency_upper, rate_decimal)
                return rate_decimal

        except Exception as e:
            logger.warning(
                "Agent Gateway: failed to fetch %s->KES exchange rate: %s",
                currency_upper,
                e,
            )

    # 3. Fallback to configured default
    setting_name = "AGENT_FX_{}_KES".format(currency_upper)
    default_rate = Decimal(str(getattr(settings, setting_name, None) or _get_default_fx(currency_upper)))
    # Cache the fallback too: without this, an unconfigured FX API makes
    # every priced request re-log the fallback (279 lines in one deploy's
    # startup logs) and a failing API gets re-attempted on every request.
    # The cache TTL bounds how long a fallback rate sticks before the live
    # source is tried again.
    cache.set(cache_key, str(default_rate), cache_ttl)
    if currency_upper not in _FX_DEFAULT_LOGGED:
        _FX_DEFAULT_LOGGED.add(currency_upper)
        logger.info("Agent Gateway: using default %s->KES rate: %s", currency_upper, default_rate)
    return default_rate


def _get_default_fx(currency: str) -> float:
    """Get a sensible fallback FX rate for a currency."""
    defaults = {
        "EUR": 145.0,
        "USD": 129.0,
    }
    return defaults.get(currency.upper(), 1.0)


def get_live_eur_kes_rate() -> Decimal:
    """Convenience: resolve EUR to KES rate."""
    return _get_fx_rate("EUR")


def get_live_usd_kes_rate() -> Decimal:
    """Convenience: resolve USD to KES rate."""
    return _get_fx_rate("USD")


class AgentPricingService:
    """
    Service for calculating prices for Agent Gateway requests.

    Uses the cost catalog (AgentModelPrice) with fallback to default prices.
    Applies the per-provider markup resolved by get_markup_percent().
    All money math uses Decimal end-to-end.
    """

    @staticmethod
    def get_model_price(provider, model):
        """Get price from database catalog."""
        try:
            price = AgentModelPrice.objects.get(
                provider=provider,
                model=model,
                is_active=True,
            )
            return {
                "input_price_per_1m": price.input_price_per_1m,
                "output_price_per_1m": price.output_price_per_1m,
                "currency": price.currency,
            }
        except AgentModelPrice.DoesNotExist:
            return None

    @staticmethod
    def get_default_price(model):
        """Get default price for a model (fallback when not in database)."""
        # No legacy price defaults ship in the public source: the cost catalog
        # (AgentModelPrice, filled by sync_agent_catalog or the admin) is the
        # only source of prices.
        DEFAULT_PRICES = {}

        if model in DEFAULT_PRICES:
            return DEFAULT_PRICES[model]

        model_lower = model.lower()
        for key, price in DEFAULT_PRICES.items():
            if key.lower() == model_lower:
                return price

        return None

    @staticmethod
    def get_price(provider, model):
        """Get the price for a provider/model, with fallback to defaults."""
        price = AgentPricingService.get_model_price(provider, model)
        if price:
            return price

        # Legacy defaults belong ONLY to Cortecs and never override a disabled pair.
        if provider == "cortecs" and not AgentModelPrice.objects.filter(provider=provider, model=model).exists():
            price = AgentPricingService.get_default_price(model)
            if price:
                return price

        raise ValueError("No price available for {}/{}".format(provider, model))

    @staticmethod
    def calculate_cost(provider, model, input_tokens, output_tokens):
        """
        Calculate the cost for a request using Decimal end-to-end.

        Formula:
            cost = (input_tokens * input_price / 1_000_000) + (output_tokens * output_price / 1_000_000)
            charge_kes = cost * markup_multiplier * FX_rate  (FX depends on provider currency)
            sparks = ceil(charge_kes / SPARK_BASE_VALUE_KES)

        The markup is provider-specific (get_markup_percent()).
        FX rate is resolved via _get_fx_rate() (cached, live, or fallback).
        """
        price = AgentPricingService.get_price(provider, model)

        input_price = price["input_price_per_1m"]
        output_price = price["output_price_per_1m"]
        currency = price.get("currency", "EUR")

        markup_percent = get_markup_percent(provider)
        spark_base_value_kes = Decimal(str(SPARK_BASE_VALUE_KES))

        # Resolve FX rate based on the catalog's currency
        fx_rate = _get_fx_rate(currency)

        # Calculate cost in native currency (pre-markup) - all Decimal
        input_cost = Decimal(input_tokens) * input_price / Decimal("1000000")
        output_cost = Decimal(output_tokens) * output_price / Decimal("1000000")
        cost_native = input_cost + output_cost

        # Apply the provider-specific markup
        markup_multiplier = Decimal("1.00") + (Decimal(str(markup_percent)) / Decimal("100"))
        marked_up_cost = cost_native * markup_multiplier

        # Convert to KES - keep Decimal throughout
        charge_kes = marked_up_cost * fx_rate

        # Convert to sparks (ceil rounding)
        sparks = math.ceil(charge_kes / spark_base_value_kes)

        return {
            "cost_native": cost_native,
            "cost_currency": currency,
            "marked_up_cost": marked_up_cost,
            "charge_kes": charge_kes,
            "sparks": int(sparks),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_price_per_1m": input_price,
            "output_price_per_1m": output_price,
            "markup_percent": markup_percent,
            "fx_rate": fx_rate,
            "fx_currency": currency,
            "spark_base_value_kes": spark_base_value_kes,
        }

    @staticmethod
    def calculate_estimate(provider, model, input_tokens, max_output_tokens):
        """Calculate an estimated cost for a request (for reservation purposes)."""
        return AgentPricingService.calculate_cost(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=max_output_tokens,
        )

    @staticmethod
    def list_models_with_prices():
        """List all models with their prices from the database catalog."""
        models = []
        db_prices = AgentModelPrice.objects.filter(is_active=True).order_by("provider", "model")
        for p in db_prices:
            models.append(
                {
                    "id": "{}/{}".format(p.provider, p.model),
                    "provider": p.provider,
                    "model": p.model,
                    "name": p.model,
                    "input_price_per_1m": p.input_price_per_1m,
                    "output_price_per_1m": p.output_price_per_1m,
                    "currency": p.currency,
                    "max_context": None,
                    "description": None,
                }
            )
        return models

    @staticmethod
    def list_models_with_spark_prices():
        """DB-only catalog: exact marked-up KES, not rounded spark conversion.

        Unknown metadata remains null/empty; listing never initiates a provider call.
        """
        from capp.classes.agent_gateway.providers.registry import get_provider

        models = []
        for p in AgentModelPrice.objects.filter(is_active=True).order_by("provider", "model"):
            input_cost = AgentPricingService.calculate_cost(p.provider, p.model, 1_000_000, 0)
            output_cost = AgentPricingService.calculate_cost(p.provider, p.model, 0, 1_000_000)
            provider = get_provider(provider_name=p.provider, model=p.model)
            models.append(
                {
                    "provider": p.provider,
                    "model": p.model,
                    "currency": p.currency,
                    "input_price_per_1m": str(p.input_price_per_1m.normalize()),
                    "output_price_per_1m": str(p.output_price_per_1m.normalize()),
                    "sparks_per_1m_input": input_cost["sparks"],
                    "sparks_per_1m_output": output_cost["sparks"],
                    "input_kes_per_1m": str(input_cost["charge_kes"].normalize()),
                    "output_kes_per_1m": str(output_cost["charge_kes"].normalize()),
                    "markup_percent": input_cost["markup_percent"],
                    "max_context": p.max_context,
                    "max_output_tokens": p.max_output_tokens,
                    "description": p.description or None,
                    "capabilities": p.capabilities if isinstance(p.capabilities, list) else [],
                    "available": bool(provider and provider.is_configured()),
                }
            )
        return models
