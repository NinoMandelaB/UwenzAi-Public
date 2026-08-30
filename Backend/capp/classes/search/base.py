# capp/classes/search/base.py
"""
Clean provider-agnostic interface for the web search tool.

Any concrete provider implements
``SearchProvider.search(query, max_results) -> List[SearchResult]``. Callers
(the tool-calling loop in capp/tasks.py) depend only on this interface, never
on a concrete provider class, so a provider stays swappable.

Staan (https://staan.ai) is the only implementation. It is EU-based (France),
GDPR-native, with EU-only routing available and no US CLOUD Act exposure --
see capp/classes/search/staan_provider.py.

The ``SearchProvider`` base class is deliberately kept even with a single
implementation: it is what keeps provider-specific response shapes out of
the tool loop, and adding a second provider later means writing one class
and extending the check in ``get_search_provider``.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider:
    """Abstract base for web search providers."""

    #: Human-readable provider name, used in logs/metadata.
    name = "base"

    def is_configured(self) -> bool:
        """Return True if this provider has everything it needs (e.g. an
        API key) to actually perform a search. Callers must check this
        before offering the search tool to the model, so that an unset key
        gracefully degrades (no tool offered) instead of crashing."""
        raise NotImplementedError

    def search(self, query: str, max_results: int = 3) -> List[SearchResult]:
        """Execute a search and return normalized results. Implementations
        must not raise on provider-side errors during normal degraded
        operation; callers treat exceptions as an unexpected failure and
        will drop the tool call rather than crash the reply pipeline."""
        raise NotImplementedError


def get_search_provider() -> Optional[SearchProvider]:
    """
    Factory returning the active SearchProvider, or None if no provider is
    configured (graceful degradation -- no tool will be offered to the
    model and no outbound search calls will be made).

    Staan is the only provider. If ``STAAN_API_KEY`` is unset this returns
    None and the search tool is simply never offered.

    ``SEARCH_PROVIDER`` is no longer a selection knob (Tavily was removed --
    it was never keyed and never used). It is still READ, purely as a
    misconfiguration guard: if a deployment still has an explicit
    ``SEARCH_PROVIDER`` naming something other than Staan, this fails closed
    rather than quietly serving Staan instead. An operator who explicitly
    named a provider asked for that provider's data residency; silently
    substituting a different third-party processor is exactly the
    substitution that guard exists to prevent. Unset or blank is the normal
    case and selects Staan.
    """
    # Local import avoids a hard dependency/circular import at module load
    # time; also makes it trivial to swap in a different provider later.
    from django.conf import settings as dj_settings

    from capp.classes.search.staan_provider import StaanProvider

    configured = (getattr(dj_settings, "SEARCH_PROVIDER", "") or "").strip().lower()
    if configured and configured != StaanProvider.name:
        logger.error(
            "SEARCH_PROVIDER=%r is not available (Staan is the only provider); refusing to search. "
            "Unset SEARCH_PROVIDER, or set it to %r, to enable web search.",
            configured,
            StaanProvider.name,
        )
        return None

    provider = StaanProvider()
    if provider.is_configured():
        return provider
    return None
