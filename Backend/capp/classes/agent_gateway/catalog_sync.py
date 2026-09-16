"""
Scheduled synchronization of the Agent Gateway model catalog.

The workspace pricing view is database-only by design — browsing it must never
initiate provider calls — so the AgentModelPrice table is kept current by this
sync (plus opportunistic refreshes whenever an external client calls
GET /api/v1/models). A failed fetch never deletes or downgrades existing rows,
and admin-disabled models (is_active=False) always survive a refresh.
"""

import logging

from capp.classes.agent_gateway.providers.registry import PROVIDERS, get_provider

logger = logging.getLogger(__name__)


def run_catalog_sync() -> dict[str, dict]:
    """
    Fetch current model catalogs from every configured provider and upsert them.

    Returns a per-provider summary for logging and observability:
    ``{"status": "ok" | "not_configured" | "error", "models": <int>}``,
    with an ``"error"`` message when a fetch or upsert failed. One failing
    provider never blocks the others.
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
            results[name] = {"status": "ok", "models": fetched}
    logger.info("Agent Gateway: catalog sync complete: %s", results)
    return results
