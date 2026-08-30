# capp/tests_staan_provider.py
"""
Tests for the Staan (https://staan.ai) SearchProvider implementation.

Covers happy-path result parsing, no-key graceful degradation, timeout
handling, malformed-response handling, the per-message call cap (shared
tool_loop code, provider-agnostic), and the SEARCH_PROVIDER
deprecation-guard behaviour in capp/classes/search/base.py.
"""

import json
from unittest import mock

import requests
from django.test import TestCase

from capp.classes.search.base import SearchResult


def _fake_staan_response(results):
    """Build a Staan Web Search for AI response payload from a list of
    (title, url, snippet, extra_snippets) tuples, matching the documented
    shape at https://docs.staan.ai/docs/web-for-ai."""
    web_results = []
    for title, url, snippet, extra_snippets in results:
        item = {
            "title": title,
            "url": url,
            "snippet": snippet,
            "display_url": url,
            "hostname": url,
        }
        if extra_snippets is not None:
            item["extra_snippets"] = extra_snippets
        web_results.append(item)
    return {
        "search_id": "01906c9e-7e3f-7000-8000-abc123def456",
        "query": {"q": "test", "market": "en-us", "count": len(web_results), "offset": 0},
        "web": {"results": web_results},
    }


def _as_streaming(mock_response, payload):
    """Make a Mock behave like a `stream=True` requests.Response.

    The providers now read bodies via capp.classes.search.http_json.
    read_json_bounded (stream + byte cap) instead of `response.json()`, so a
    plain `.json.return_value` mock is no longer exercised by the code path.
    Setting iter_content keeps these tests testing the real parsing code.
    """
    return _as_streaming_bytes(mock_response, json.dumps(payload).encode("utf-8"))


def _as_streaming_bytes(mock_response, body: bytes):
    mock_response.encoding = "utf-8"

    def _iter_content(chunk_size=8192):
        remaining = body
        if not remaining:
            yield b""
        while remaining:
            yield remaining[:chunk_size]
            remaining = remaining[chunk_size:]

    mock_response.iter_content = _iter_content
    mock_response.close = lambda: None
    return mock_response


class StaanProviderHappyPathTests(TestCase):
    def test_search_returns_normalized_results(self):
        from capp.classes.search.staan_provider import StaanProvider

        payload = _fake_staan_response(
            [
                (
                    "Nairobi Today",
                    "https://example.com/a",
                    "plain SERP snippet",
                    [{"chunk": "Top reranked chunk about Nairobi.", "score": 0.91}],
                ),
                ("Second result", "https://example.com/b", "another snippet", []),
            ]
        )
        fake_response = mock.Mock()
        _as_streaming(fake_response, payload)
        fake_response.raise_for_status.return_value = None

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ) as mock_post:
            results = StaanProvider().search("nairobi news", max_results=3)

        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0], SearchResult)
        self.assertEqual(results[0].title, "Nairobi Today")
        self.assertEqual(results[0].url, "https://example.com/a")
        # Top-scored extra_snippets chunk is preferred over the plain snippet.
        self.assertEqual(results[0].snippet, "Top reranked chunk about Nairobi.")
        # No extra_snippets => falls back to the plain SERP snippet.
        self.assertEqual(results[1].snippet, "another snippet")

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["headers"],
            {"Authorization": "Bearer staan-test-key", "Content-Type": "application/json"},
        )
        self.assertTrue(kwargs["json"]["extra_snippets"])
        self.assertEqual(kwargs["json"]["q"], "nairobi news")
        self.assertEqual(kwargs["json"]["market"], "en-us")

    def test_empty_market_is_omitted_to_defer_to_staan(self):
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        _as_streaming(fake_response, _fake_staan_response([]))
        fake_response.raise_for_status.return_value = None

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_MARKET", None
        ), mock.patch("capp.classes.search.staan_provider.requests.post", return_value=fake_response) as mock_post:
            StaanProvider().search("topic")

        self.assertNotIn("market", mock_post.call_args.kwargs["json"])

    def test_query_is_capped_at_documented_400_character_limit(self):
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        _as_streaming(fake_response, _fake_staan_response([]))
        fake_response.raise_for_status.return_value = None

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ) as mock_post:
            StaanProvider().search("x" * 450)

        self.assertEqual(len(mock_post.call_args.kwargs["json"]["q"]), 400)

    def test_search_respects_max_results(self):
        from capp.classes.search.staan_provider import StaanProvider

        payload = _fake_staan_response(
            [(f"Title {i}", f"https://example.com/{i}", f"snippet {i}", []) for i in range(5)]
        )
        fake_response = mock.Mock()
        _as_streaming(fake_response, payload)
        fake_response.raise_for_status.return_value = None

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("topic", max_results=2)

        self.assertEqual(len(results), 2)

    def test_snippet_truncated_to_500_chars(self):
        from capp.classes.search.staan_provider import StaanProvider

        long_chunk = "x" * 1000
        payload = _fake_staan_response(
            [("Title", "https://example.com", "short", [{"chunk": long_chunk, "score": 0.5}])]
        )
        fake_response = mock.Mock()
        _as_streaming(fake_response, payload)
        fake_response.raise_for_status.return_value = None

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("topic")

        self.assertEqual(len(results[0].snippet), 500)


class StaanProviderGracefulDegradationTests(TestCase):
    """Search must gracefully degrade (no crash) without STAAN_API_KEY."""

    def test_is_configured_false_without_key(self):
        from capp.classes.search.staan_provider import StaanProvider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", None):
            self.assertFalse(StaanProvider().is_configured())

    def test_is_configured_true_with_key(self):
        from capp.classes.search.staan_provider import StaanProvider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"):
            self.assertTrue(StaanProvider().is_configured())

    def test_search_without_key_returns_empty_list_not_crash(self):
        from capp.classes.search.staan_provider import StaanProvider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", None):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_get_search_provider_returns_none_without_key(self):
        """With SEARCH_PROVIDER=staan (default) and no STAAN_API_KEY, the
        factory must return None -- no crash, no tool offered."""
        from capp.classes.search.base import get_search_provider

        with mock.patch("django.conf.settings.SEARCH_PROVIDER", "staan", create=True), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_API_KEY", None
        ):
            self.assertIsNone(get_search_provider())


class StaanProviderTimeoutAndErrorTests(TestCase):
    def test_timeout_returns_empty_list_not_crash(self):
        from capp.classes.search.staan_provider import StaanProvider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_connection_error_returns_empty_list_not_crash(self):
        from capp.classes.search.staan_provider import StaanProvider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post",
            side_effect=requests.exceptions.ConnectionError("no route"),
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_http_error_status_returns_empty_list_not_crash(self):
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        fake_response.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "bad-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_malformed_json_returns_empty_list_not_crash(self):
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        _as_streaming_bytes(fake_response, b"not JSON at all")

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_missing_web_key_returns_empty_list_not_crash(self):
        """Response JSON that doesn't match the documented shape (e.g.
        missing the top-level "web" key) must not raise."""
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        _as_streaming(fake_response, {"search_id": "abc", "query": {}})

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_non_dict_result_items_are_skipped_not_crash(self):
        """Defensive handling if web.results contains non-dict entries."""
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        _as_streaming(fake_response, {"web": {"results": ["not-a-dict", None, 42]}})

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_results_missing_optional_fields_do_not_crash(self):
        """title/url/snippet missing entirely -- should default to empty
        strings rather than raising a KeyError."""
        from capp.classes.search.staan_provider import StaanProvider

        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        _as_streaming(fake_response, {"web": {"results": [{}]}})

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"), mock.patch(
            "capp.classes.search.staan_provider.requests.post", return_value=fake_response
        ):
            results = StaanProvider().search("some topic")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "")
        self.assertEqual(results[0].url, "")
        self.assertEqual(results[0].snippet, "")


class StaanToolLoopCallCapTests(TestCase):
    """Per-message call cap is enforced identically regardless of provider
    (tool_loop.run_search_tool_calls is provider-agnostic), verified here
    explicitly with a Staan-backed provider double."""

    def _fake_tool_call(self, query):
        call = mock.Mock()
        call.function.name = "web_search"
        call.function.arguments = f'{{"query": "{query}"}}'
        return call

    def test_caps_at_max_calls_per_message_with_staan_provider(self):
        from capp.classes.search.tool_loop import run_search_tool_calls

        fake_provider = mock.Mock()
        fake_provider.name = "staan"
        fake_provider.search.return_value = [SearchResult(title="T", url="https://x", snippet="s")]

        calls = [self._fake_tool_call(f"topic {i}") for i in range(5)]
        with mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=fake_provider):
            condensed, used = run_search_tool_calls(calls, max_calls=2)

        self.assertEqual(used, 2)
        self.assertEqual(fake_provider.search.call_count, 2)

    def test_search_enabled_executes_and_condenses_results_with_staan(self):
        from capp.classes.search.tool_loop import run_search_tool_calls

        fake_provider = mock.Mock()
        fake_provider.name = "staan"
        fake_provider.search.return_value = [
            SearchResult(title="Nairobi Today", url="https://example.com/a", snippet="Some news snippet."),
        ]

        with mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=fake_provider):
            condensed, used = run_search_tool_calls([self._fake_tool_call("latest Nairobi news")])

        self.assertEqual(used, 1)
        self.assertIn("Nairobi Today", condensed)
        self.assertIn("https://example.com/a", condensed)
        fake_provider.search.assert_called_once()


class SearchProviderSelectionTests(TestCase):
    """Staan is the only provider; SEARCH_PROVIDER is a deprecation guard.

    TavilyProvider was removed (never keyed, never used). SEARCH_PROVIDER is
    no longer a selection knob -- unset/blank/"staan" all select Staan, and
    any other value fails closed rather than silently substituting a
    different third-party processor for the one the operator named.
    """

    def test_search_provider_staan_selects_staan(self):
        from capp.classes.search.base import get_search_provider

        with mock.patch("django.conf.settings.SEARCH_PROVIDER", "staan", create=True), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"
        ):
            provider = get_search_provider()

        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "staan")

    def test_tavily_provider_module_is_gone(self):
        """The removed provider must not come back by accident."""
        with self.assertRaises(ImportError):
            import capp.classes.search.tavily_provider  # noqa: F401

    def test_stale_tavily_setting_fails_closed(self):
        """A deployment left on SEARCH_PROVIDER=tavily must NOT get Staan.

        Tavily no longer exists, but an operator who set this was choosing
        Tavily's data residency. Quietly routing those queries to Staan is
        the substitution this guard exists to prevent, so search is disabled
        until the stale variable is unset.
        """
        from capp.classes.search.base import get_search_provider

        with mock.patch("django.conf.settings.SEARCH_PROVIDER", "tavily", create=True), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"
        ):
            self.assertIsNone(get_search_provider())

    def test_unset_search_provider_defaults_to_staan(self):
        """SEARCH_PROVIDER unset (attribute absent from settings entirely)
        must still default to staan, not raise."""
        from django.conf import settings as dj_settings

        from capp.classes.search.base import get_search_provider

        # Simulate the setting being genuinely absent (not just empty),
        # matching a fresh settings module before SEARCH_PROVIDER existed.
        had_attr = hasattr(dj_settings, "SEARCH_PROVIDER")
        original = getattr(dj_settings, "SEARCH_PROVIDER", None)
        if had_attr:
            delattr(dj_settings, "SEARCH_PROVIDER")
        try:
            with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"):
                provider = get_search_provider()
            self.assertIsNotNone(provider)
            self.assertEqual(provider.name, "staan")
        finally:
            if had_attr:
                dj_settings.SEARCH_PROVIDER = original

    def test_empty_string_search_provider_defaults_to_staan(self):
        from capp.classes.search.base import get_search_provider

        with mock.patch("django.conf.settings.SEARCH_PROVIDER", "", create=True), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"
        ):
            provider = get_search_provider()

        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "staan")

    def test_unrecognized_search_provider_fails_closed(self):
        """An unknown SEARCH_PROVIDER disables search.

        A typo such as ``SEARCH_PROVIDER=staaan`` should surface as an ERROR
        log and no search, not as a silent fallback.
        """
        from capp.classes.search.base import get_search_provider

        with mock.patch("django.conf.settings.SEARCH_PROVIDER", "some-unknown-provider", create=True), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"
        ):
            provider = get_search_provider()

        self.assertIsNone(provider)

    def test_search_provider_case_and_whitespace_insensitive(self):
        from capp.classes.search.base import get_search_provider

        for value in ("STAAN", "  Staan  ", "sTaAn"):
            with mock.patch("django.conf.settings.SEARCH_PROVIDER", value, create=True), mock.patch(
                "capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"
            ):
                provider = get_search_provider()

            self.assertIsNotNone(provider, value)
            self.assertEqual(provider.name, "staan")

    def test_missing_staan_key_returns_none(self):
        """No API key means no search tool -- and no fallback provider now
        exists to silently take over."""
        from capp.classes.search.base import get_search_provider

        with mock.patch("django.conf.settings.SEARCH_PROVIDER", "staan", create=True), mock.patch(
            "capp.classes.search.staan_provider.settings.STAAN_API_KEY", None
        ):
            provider = get_search_provider()

        self.assertIsNone(provider)
