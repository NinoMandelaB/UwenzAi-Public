# capp/classes/search/staan_provider.py
"""
Staan (https://staan.ai) implementation of the SearchProvider interface.

Staan is a EU-based (France), GDPR-native web search API co-built by Qwant
and Ecosia through the European Search Perspective joint venture. It offers
EU-hosted infrastructure with no US CLOUD Act exposure, at EUR2/1k requests
with 1k free requests/month. See https://staan.ai and
https://docs.staan.ai/docs/web-for-ai for product and API details.

This is the only SearchProvider implementation.

This uses Staan's "Web Search for AI" endpoint -- the enrichment variant of
the Web Search endpoint (same URL, activated via the ``extra_snippets``
query parameter) that returns semantically-scored content chunks per result,
which is the LLM-grounding shape this codebase's tool loop consumes. See
docs.staan.ai/docs/web-for-ai for the documented request/response contract;
the exact field shapes used below (``search_id``, ``query``, ``web.results``,
each result's ``title``/``url``/``snippet``/``extra_snippets``) are taken
verbatim from that page's documented example. Fields NOT explicitly
confirmed by Staan's public docs (exact error-response shape/HTTP codes for
auth/rate-limit failures, and any dedicated EU-only-routing request
parameter) are handled defensively below and called out in this module's
docstrings rather than assumed silently -- see the PR description for the
full list of assumptions.

Configured via the STAAN_API_KEY env var (see eaaicproject/settings.py). The
optional STAAN_MARKET setting defaults to ``en-us`` because the documented
API default is ``fr-fr`` and English is the documented broadly applicable
choice for this app. Staan does not document Kenya/Tanzania/Uganda or
Kiswahili-specific market, country, locale, or language parameters, so none
are guessed. If the API key is unset, ``is_configured()`` returns False and
the tool is never offered to the model -- no crash, no outbound call.
"""

import logging
from typing import List

import requests
from django.conf import settings

from capp.classes.search.base import SearchProvider, SearchResult
from capp.classes.search.http_json import read_json_bounded

logger = logging.getLogger(__name__)

# Recommended enrichment baseline per Staan's docs (docs.staan.ai/docs/web-for-ai):
# start with min_score=0.2, max_snippets=5, then tune from there.
_DEFAULT_MIN_SCORE = 0.2
_DEFAULT_MAX_SNIPPETS = 5


class StaanProvider(SearchProvider):
    name = "staan"

    def is_configured(self) -> bool:
        return bool(getattr(settings, "STAAN_API_KEY", None))

    def search(self, query: str, max_results: int = 3) -> List[SearchResult]:
        query = (query or "").strip()[:400]
        if not query:
            return []

        api_key = getattr(settings, "STAAN_API_KEY", None)
        if not api_key:
            # Should not normally be reached (callers check is_configured
            # first), but stay defensive/graceful rather than raising.
            logger.info("Staan search skipped: STAAN_API_KEY not set")
            return []

        url = getattr(settings, "STAAN_API_URL", "https://api.staan.ai/v2/search/web")
        timeout = getattr(settings, "STAAN_TIMEOUT", 10.0)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Web Search for AI is the same endpoint as plain Web Search, with
        # enrichment activated via extra_snippets=true (per Staan docs). We
        # request the AI-grounding variant (scored content chunks) rather
        # than full_content, since chunks are a closer match to this tool
        # loop's condensed-snippet consumption model and carry a much
        # lighter enrichment-pipeline latency budget than full page fetches.
        payload = {
            "q": query,
            "extra_snippets": True,
            "max_snippets": _DEFAULT_MAX_SNIPPETS,
            "min_score": _DEFAULT_MIN_SCORE,
        }
        # Staan documents market as the language-and-region selector. Its
        # documented API default is fr-fr, so settings supplies en-us by
        # default. Do not invent unsupported East African/Kiswahili codes;
        # callers may opt out by setting STAAN_MARKET to an empty string.
        market = getattr(settings, "STAAN_MARKET", "en-us")
        if market:
            payload["market"] = market

        try:
            # stream=True + read_json_bounded caps the body: an oversized or
            # runaway provider response must not be buffered unbounded into a
            # Celery worker's memory.
            response = requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
            response.raise_for_status()
            data = read_json_bounded(response)
        except requests.exceptions.RequestException as e:
            logger.warning("Staan search failed for query=%r: %s", query, e)
            return []
        except ValueError as e:
            # Also catches ResponseTooLargeError, which subclasses ValueError.
            logger.warning("Staan search returned unusable response: %s", e)
            return []

        web = data.get("web") if isinstance(data, dict) else None
        raw_results = (web or {}).get("results") if isinstance(web, dict) else None

        results = []
        for item in (raw_results or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            snippet = item.get("snippet") or ""
            # Prefer the top-scored extra_snippets chunk (already reranked
            # against the query) when present, falling back to the plain
            # SERP snippet -- extra_snippets is empty when the page could
            # not be fetched (anti-bot/timeout) or no chunk cleared
            # min_score, per Staan's documented behavior.
            extra_snippets = item.get("extra_snippets") or []
            if extra_snippets and isinstance(extra_snippets, list):
                top_chunk = extra_snippets[0]
                if isinstance(top_chunk, dict) and top_chunk.get("chunk"):
                    snippet = top_chunk["chunk"]

            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    snippet=(snippet or "")[:500],
                )
            )
        return results
