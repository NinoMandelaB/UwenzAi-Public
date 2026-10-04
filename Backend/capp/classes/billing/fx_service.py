# capp/classes/billing/fx_service.py
"""
KES → USD/EUR exchange rates for approximate price display.

Spark packages are priced and charged in KES; the USD/EUR figures shown in
the purchase UI are *informational only* — the Paystack charge is always the
authoritative KES amount. Rates come from a fixed, allowlisted public endpoint
(no user input ever reaches the URL, so there is no SSRF surface) and are
cached for 24 hours, well inside the provider's own daily refresh window.

Failure semantics are deliberately conservative: if the provider is
unreachable or returns something unusable, ``get_kes_fx_rates`` returns
``None`` and the frontend simply hides the converted prices rather than
showing stale or wrong numbers.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Optional

import requests
from django.core.cache import cache

from capp.classes.search.http_json import read_json_bounded

logger = logging.getLogger(__name__)

#: Fixed allowlisted endpoint — base KES, quotes for every other currency.
#: Free tier of exchangerate-api.com, refreshed daily by the provider.
FX_ENDPOINT = "https://open.er-api.com/v6/latest/KES"

#: Currencies surfaced in the purchase UI.
DISPLAY_CURRENCIES = ("usd", "eur")

#: 24h cache TTL. The provider updates once a day; refreshing more often
#: only adds latency and rate-limit risk without new information.
FX_CACHE_TTL_SECONDS = 24 * 60 * 60

#: An FX quote response is a few KB; 64 KB is a generous runaway cap.
FX_MAX_JSON_BYTES = 64_000

FX_CACHE_KEY = "billing:fx:kes_rates"

TWO_PLACES = Decimal("0.01")


def _fetch_live_rates() -> Optional[Dict[str, str]]:
    """Fetch KES quotes once from the provider, or return None.

    Returns ``{"usd": <Decimal>, "eur": <Decimal>, "updated_at": <iso>}``.
    """
    try:
        response = requests.get(
            FX_ENDPOINT,
            timeout=(3, 5),
            stream=True,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = read_json_bounded(response, max_bytes=FX_MAX_JSON_BYTES)
    except Exception:
        logger.warning("FX rate fetch failed", exc_info=True)
        return None

    if not isinstance(payload, dict) or payload.get("result") != "success":
        logger.warning("FX rate payload rejected: unexpected shape")
        return None

    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None

    parsed: Dict[str, Decimal] = {}
    for currency in DISPLAY_CURRENCIES:
        try:
            rate = Decimal(str(rates[currency.upper()]))
        except (KeyError, InvalidOperation):
            logger.warning("FX rate payload missing %s", currency.upper())
            return None
        if rate <= 0:
            logger.warning("FX rate for %s not positive", currency.upper())
            return None
        parsed[currency] = rate

    updated_at = None
    raw = payload.get("time_last_update_unix")
    if isinstance(raw, (int, float)):
        updated_at = datetime.fromtimestamp(raw, tz=timezone.utc).isoformat()

    return {"rates": parsed, "updated_at": updated_at}


def get_kes_fx_rates() -> Optional[Dict[str, Decimal]]:
    """Return {'usd': Decimal, 'eur': Decimal} KES quotes, cached 24h.

    ``None`` means "no usable rates right now" — callers must treat that as
    "hide the conversion", never as a zero rate.
    """
    cached = cache.get(FX_CACHE_KEY)
    if cached is None:
        live = _fetch_live_rates()
        if live is None:
            return None
        cache.set(FX_CACHE_KEY, live, FX_CACHE_TTL_SECONDS)
        cached = live

    rates = cached.get("rates")
    if not isinstance(rates, dict) or not all(rates.get(c) for c in DISPLAY_CURRENCIES):
        return None
    return rates


def convert_kes(amount_kes: Decimal, currency: str) -> Optional[Decimal]:
    """Convert a KES Decimal amount to a display currency, 2dp half-up.

    Returns ``None`` when rates are unavailable; callers hide the figure.
    """
    rates = get_kes_fx_rates()
    if rates is None:
        return None
    rate = rates.get(currency.lower())
    if rate is None:
        return None
    return (Decimal(amount_kes) * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
