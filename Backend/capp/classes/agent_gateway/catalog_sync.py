"""
Scheduled synchronization of the Agent Gateway model catalog.

The workspace pricing view is database-only by design — browsing it must never
initiate provider calls — so the AgentModelPrice table is kept current by this
sync (plus opportunistic refreshes whenever an external client calls
GET /api/v1/models). A failed fetch never deletes or downgrades existing rows,
and admin-disabled models (is_active=False) always survive a refresh.

Providers whose /models endpoint does not publish prices (Mistral, AKI,
TensorX) only catalog models listed in their operator-maintained settings price
map. A model released upstream but missing from that map would previously
disappear silently. The sync therefore also reports, per provider, models the
fetch skipped — and flags those never seen before, so the operator notices new
models the day they appear instead of when a customer asks.
"""

import logging

from django.core.cache import cache

from capp.classes.agent_gateway.providers.registry import PROVIDERS, get_provider

logger = logging.getLogger(__name__)

# How long a skipped model stays "seen" after its last appearance in a fetch.
# Long enough that the daily sync does not re-alert on stable catalogs, short
# enough that a model that disappears and returns (e.g. relaunch) re-alerts.
_SKIPPED_SEEN_TTL = 60 * 60 * 24 * 30  # 30 days


def _skipped_seen_key(provider_name: str) -> str:
    return "agent_gateway_{}_skipped_seen".format(provider_name)


def _diff_new_skipped(provider_name: str, skipped: list[str]) -> list[str]:
    """Return the skipped model IDs not seen in any recent sync for this provider.

    Always refreshes the seen set (even for an empty skipped list, which
    clears stale entries once a model gains a price).
    """
    seen = cache.get(_skipped_seen_key(provider_name)) or []
    new = sorted(set(skipped) - set(seen))
    cache.set(_skipped_seen_key(provider_name), sorted(set(skipped)), _SKIPPED_SEEN_TTL)
    return new


def run_catalog_sync() -> dict[str, dict]:
    """
    Fetch current model catalogs from every configured provider and upsert them.

    Returns a per-provider summary for logging and observability:
    ``{"status": "ok" | "not_configured" | "error", "models": <int>}`` — plus,
    when a successful fetch skipped models, ``"skipped_models"`` (everything
    skipped in this run) and ``"new_skipped_models"`` (first appearance in the
    retention window; these need an operator price-map entry). The extra keys
    are only present when non-empty so existing summary consumers stay stable.
    One failing provider never blocks the others.
    """
    results: dict[str, dict] = {}
    for name in PROVIDERS:
        provider = get_provider(provider_name=name)
        if provider is None:
            results[name] = {"status": "not_configured", "models": 0}
            continue
        try:
            fetched = provider.refresh_catalog()
        except Exception as exc:
            logger.warning("Agent Gateway: catalog sync failed for %s: %s", name, exc)
            results[name] = {"status": "error", "models": 0, "error": str(exc)}
            continue
        if fetched is None:
            # The live fetch failed; existing catalog rows are intentionally
            # left untouched so users keep the last known good catalog.
            results[name] = {"status": "error", "models": 0, "error": "live model fetch failed"}
        else:
            entry: dict = {"status": "ok", "models": fetched}
            skipped = sorted(set(getattr(provider, "_skipped_models", None) or []))
            new_skipped = _diff_new_skipped(name, skipped)
            if skipped:
                entry["skipped_models"] = skipped
            if new_skipped:
                entry["new_skipped_models"] = new_skipped
            results[name] = entry
    logger.info("Agent Gateway: catalog sync complete: %s", results)
    return results
