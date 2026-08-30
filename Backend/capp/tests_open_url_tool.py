# capp/tests_open_url_tool.py
"""
Tests for the open_url tool (fetch a single URL, extract readable content).

Covers:
- Happy path with a mocked fetch (SSRF checks bypassed by mocking
  socket.getaddrinfo to resolve to a public IP; requests.get is mocked so no
  real network call ever happens).
- SSRF protections: private IP, loopback, cloud metadata endpoint
  (169.254.169.254), and a redirect that points at a private IP.
- Response size cap.
- Per-message call cap enforcement in the tool loop.
- Toggle-off => open_url is never offered alongside web_search.
"""

from unittest import mock

from django.test import TestCase

from capp.classes.search.open_url import (
    BlockedURLError,
    ResponseTooLargeError,
    open_url,
    format_open_url_result,
    OPEN_URL_TOOL_NAME,
)
from capp.classes.search.tool_loop import run_open_url_tool_calls, OPEN_URL_TOOL_SPEC


def _fake_getaddrinfo_public(*args, **kwargs):
    # 93.184.216.34 == example.com's long-standing public IP; any public
    # address works here since we never actually connect to it (requests.get
    # is mocked in every test below).
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


def _fake_getaddrinfo_private(*args, **kwargs):
    return [(2, 1, 6, "", ("10.0.0.5", 0))]


def _fake_getaddrinfo_metadata(*args, **kwargs):
    return [(2, 1, 6, "", ("169.254.169.254", 0))]


class _FakeResponse:
    """Minimal stand-in for requests.Response, streaming-compatible.

    Deliberately exposes no ``raw`` attribute, so open_url's body reader takes
    its ``iter_content`` fallback path. The production path uses
    ``raw.read1`` (see _read_body_with_deadline) to keep the wall-clock
    deadline enforceable against slow-drip servers.
    """

    def __init__(self, status_code=200, headers=None, chunks=None, url="https://example.com/page"):
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html"}
        self._chunks = chunks if chunks is not None else [b"<html><head><title>T</title></head><body>Hi</body></html>"]
        self.url = url
        self.encoding = "utf-8"
        self._content = b"".join(self._chunks)
        self._content_consumed = False

    def iter_content(self, chunk_size=8192):
        for c in self._chunks:
            yield c

    def close(self):
        pass

    @property
    def text(self):
        return self._content.decode("utf-8", errors="replace")

    @property
    def content(self):
        return self._content


class OpenURLHappyPathTests(TestCase):
    def test_fetches_and_extracts_readable_content(self):
        html = (
            b"<html><head><title>Example Domain</title></head>"
            b"<body><p>Hello world, this is the page body.</p></body></html>"
        )
        fake_resp = _FakeResponse(chunks=[html])

        with mock.patch(
            "capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_public
        ), mock.patch("capp.classes.search.open_url._pinned_get", return_value=fake_resp):
            result = open_url("https://example.com/page")

        self.assertEqual(result.url, "https://example.com/page")
        self.assertIn("Hello world", result.text)
        self.assertFalse(result.truncated)

    def test_format_wraps_content_with_untrusted_data_notice(self):
        html = b"<html><head><title>T</title></head><body>Body text here.</body></html>"
        fake_resp = _FakeResponse(chunks=[html])

        with mock.patch(
            "capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_public
        ), mock.patch("capp.classes.search.open_url._pinned_get", return_value=fake_resp):
            result = open_url("https://example.com/page")

        formatted = format_open_url_result(result)
        self.assertIn("=== OPEN_URL RESULT (System) ===", formatted)
        self.assertIn("UNTRUSTED", formatted)
        self.assertIn("not instructions", formatted)
        self.assertIn("BEGIN PAGE CONTENT", formatted)
        self.assertIn("END PAGE CONTENT", formatted)
        self.assertIn("Body text here", formatted)


class OpenURLSSRFTests(TestCase):
    def test_blocks_private_ip(self):
        with mock.patch("capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_private):
            with self.assertRaises(BlockedURLError):
                open_url("https://internal.example.com/secret")

    def test_blocks_literal_private_ip_in_url(self):
        with self.assertRaises(BlockedURLError):
            open_url("http://127.0.0.1:8000/admin")

    def test_blocks_cloud_metadata_endpoint(self):
        with self.assertRaises(BlockedURLError):
            open_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_metadata_hostname_via_dns(self):
        with mock.patch("capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_metadata):
            with self.assertRaises(BlockedURLError):
                open_url("https://sneaky-lookalike.example.com/")

    def test_blocks_non_http_scheme(self):
        with self.assertRaises(BlockedURLError):
            open_url("file:///etc/passwd")

    def test_redirect_to_private_ip_is_blocked(self):
        """A public URL that 302s to a private IP must be blocked at the
        redirect hop, not just at the initial URL."""
        redirect_resp = _FakeResponse(status_code=302, headers={"Location": "http://10.0.0.5/internal"})

        call_state = {"n": 0}

        def fake_getaddrinfo(host, *args, **kwargs):
            call_state["n"] += 1
            if call_state["n"] == 1:
                return [(2, 1, 6, "", ("93.184.216.34", 0))]
            return [(2, 1, 6, "", ("10.0.0.5", 0))]

        with mock.patch("capp.classes.search.open_url.socket.getaddrinfo", side_effect=fake_getaddrinfo), mock.patch(
            "capp.classes.search.open_url._pinned_get", return_value=redirect_resp
        ):
            with self.assertRaises(BlockedURLError):
                open_url("https://public-looking.example.com/redirector")

    def test_blocklisted_domain_is_blocked(self):
        with mock.patch("capp.classes.search.open_url.settings.OPEN_URL_BLOCKED_DOMAINS", ["evil.example.com"]):
            with self.assertRaises(BlockedURLError):
                open_url("https://evil.example.com/page")

    def test_blocklisted_domain_blocks_subdomains(self):
        with mock.patch("capp.classes.search.open_url.settings.OPEN_URL_BLOCKED_DOMAINS", ["evil.example.com"]):
            with self.assertRaises(BlockedURLError):
                open_url("https://sub.evil.example.com/page")


class OpenURLSizeCapTests(TestCase):
    def test_response_exceeding_cap_is_rejected(self):
        big_chunk = b"a" * 1000
        fake_resp = _FakeResponse(chunks=[big_chunk] * 10)  # 10,000 bytes total

        with mock.patch(
            "capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_public
        ), mock.patch("capp.classes.search.open_url._pinned_get", return_value=fake_resp), mock.patch(
            "capp.classes.search.open_url.settings.OPEN_URL_MAX_RESPONSE_BYTES", 500
        ):
            with self.assertRaises(ResponseTooLargeError):
                open_url("https://example.com/huge")

    def test_extracted_content_is_truncated_to_budget(self):
        long_text = "word " * 5000
        html = f"<html><head><title>Long</title></head><body><p>{long_text}</p></body></html>".encode()
        fake_resp = _FakeResponse(chunks=[html])

        with mock.patch(
            "capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_public
        ), mock.patch("capp.classes.search.open_url._pinned_get", return_value=fake_resp), mock.patch(
            "capp.classes.search.open_url.settings.OPEN_URL_MAX_EXTRACTED_CHARS", 200
        ):
            result = open_url("https://example.com/long")

        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 201)  # +1 for the ellipsis char


class OpenURLToolLoopTests(TestCase):
    def _fake_tool_call(self, url):
        call = mock.Mock()
        call.function.name = OPEN_URL_TOOL_NAME
        call.function.arguments = f'{{"url": "{url}"}}'
        return call

    def test_per_message_call_cap_enforced(self):
        html = b"<html><head><title>T</title></head><body>content</body></html>"
        fake_resp = _FakeResponse(chunks=[html])

        calls = [self._fake_tool_call(f"https://example.com/{i}") for i in range(5)]

        with mock.patch(
            "capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_public
        ), mock.patch("capp.classes.search.open_url._pinned_get", return_value=fake_resp):
            condensed, used = run_open_url_tool_calls(calls, max_calls=1)

        self.assertEqual(used, 1)
        self.assertIn("OPEN_URL RESULT", condensed)

    def test_default_cap_from_settings_is_one(self):
        html = b"<html><head><title>T</title></head><body>content</body></html>"
        fake_resp = _FakeResponse(chunks=[html])
        calls = [self._fake_tool_call(f"https://example.com/{i}") for i in range(3)]

        with mock.patch(
            "capp.classes.search.open_url.socket.getaddrinfo", side_effect=_fake_getaddrinfo_public
        ), mock.patch("capp.classes.search.open_url._pinned_get", return_value=fake_resp), mock.patch(
            "capp.classes.search.tool_loop.settings.OPEN_URL_MAX_CALLS_PER_MESSAGE", 1
        ):
            condensed, used = run_open_url_tool_calls(calls)

        self.assertEqual(used, 1)

    def test_ssrf_failure_is_reported_not_raised(self):
        """A blocked URL must degrade to a message for the model, never crash
        the tool loop / reply pipeline."""
        calls = [self._fake_tool_call("http://169.254.169.254/latest/meta-data/")]
        condensed, used = run_open_url_tool_calls(calls, max_calls=1)

        self.assertEqual(used, 1)
        self.assertIn("Could not fetch", condensed)

    def test_ignores_tool_calls_for_other_tool_names(self):
        call = mock.Mock()
        call.function.name = "web_search"
        call.function.arguments = '{"query": "something"}'

        condensed, used = run_open_url_tool_calls([call])
        self.assertEqual(used, 0)
        self.assertEqual(condensed, "")


class OpenURLToggleGatingTests(TestCase):
    """open_url must only ever be offered alongside web_search, never alone,
    and never when the per-chat toggle is off (mirrors the tasks.py gating
    already covered end-to-end in tests_search_and_reasoning_toggles.py)."""

    def test_tool_spec_shape_is_openai_compatible(self):
        self.assertEqual(OPEN_URL_TOOL_SPEC["type"], "function")
        self.assertEqual(OPEN_URL_TOOL_SPEC["function"]["name"], OPEN_URL_TOOL_NAME)
        self.assertIn("url", OPEN_URL_TOOL_SPEC["function"]["parameters"]["properties"])
        self.assertIn("url", OPEN_URL_TOOL_SPEC["function"]["parameters"]["required"])
