# paystack_app/tests_callbacks.py
"""
Open-redirect resistance for the post-payment return URL.

The property under test is that the client can select at most *which* of a few
server-configured routes it comes back to, and never the scheme, host, port,
credentials, query string or fragment.
"""

from django.test import TestCase, override_settings

from paystack_app import callbacks

ORIGINS = ["https://app.uwenzai.com", "https://www.uwenzai.com"]
PATHS = ["/payment/verify", "/spark_pool"]

callback_settings = override_settings(
    PAYMENT_CALLBACK_ALLOWED_ORIGINS=ORIGINS,
    PAYMENT_CALLBACK_ALLOWED_PATHS=PATHS,
    PAYMENT_CALLBACK_DEFAULT_PATH="/payment/verify",
)


@callback_settings
class ResolveCallbackPathTests(TestCase):
    def assertRejected(self, value):
        self.assertEqual(callbacks.resolve_callback_path(value), "", f"expected rejection of {value!r}")

    # ------------------------------------------------------------------
    # Accepted forms
    # ------------------------------------------------------------------
    def test_bare_allowlisted_path_is_accepted(self):
        self.assertEqual(callbacks.resolve_callback_path("/payment/verify"), "/payment/verify")

    def test_second_allowlisted_path_is_accepted(self):
        self.assertEqual(callbacks.resolve_callback_path("/spark_pool"), "/spark_pool")

    def test_full_url_on_allowlisted_origin_keeps_only_its_path(self):
        self.assertEqual(
            callbacks.resolve_callback_path("https://app.uwenzai.com/payment/verify"),
            "/payment/verify",
        )

    def test_non_primary_allowlisted_origin_is_accepted(self):
        self.assertEqual(
            callbacks.resolve_callback_path("https://www.uwenzai.com/payment/verify"),
            "/payment/verify",
        )

    def test_origin_comparison_ignores_case(self):
        self.assertEqual(
            callbacks.resolve_callback_path("https://APP.UWENZAI.COM/payment/verify"),
            "/payment/verify",
        )

    def test_trailing_slash_still_matches(self):
        self.assertEqual(callbacks.resolve_callback_path("/payment/verify/"), "/payment/verify")

    def test_nothing_supplied_is_not_an_error(self):
        """Absent is different from rejected; the caller falls back to the default."""
        for empty in (None, "", "   "):
            self.assertEqual(callbacks.resolve_callback_path(empty), "")

    # ------------------------------------------------------------------
    # Other hosts
    # ------------------------------------------------------------------
    def test_foreign_origin_is_rejected(self):
        self.assertRejected("https://evil.example/payment/verify")

    def test_protocol_relative_url_is_rejected(self):
        # A browser reads //evil.example as another host entirely.
        self.assertRejected("//evil.example/payment/verify")

    def test_lookalike_prefix_origin_is_rejected(self):
        self.assertRejected("https://app.uwenzai.com.evil.example/payment/verify")

    def test_credentials_in_authority_are_rejected(self):
        # https://app.uwenzai.com@evil.example/... points at evil.example.
        self.assertRejected("https://app.uwenzai.com@evil.example/payment/verify")

    def test_userinfo_with_password_is_rejected(self):
        self.assertRejected("https://user:pw@app.uwenzai.com/payment/verify")

    def test_port_change_on_allowlisted_host_is_rejected(self):
        self.assertRejected("https://app.uwenzai.com:8443/payment/verify")

    def test_scheme_downgrade_is_rejected(self):
        self.assertRejected("http://app.uwenzai.com/payment/verify")

    # ------------------------------------------------------------------
    # Non-http schemes
    # ------------------------------------------------------------------
    def test_javascript_scheme_is_rejected(self):
        self.assertRejected("javascript:alert(1)")

    def test_data_scheme_is_rejected(self):
        self.assertRejected("data:text/html;base64,PHNjcmlwdD4=")

    def test_custom_app_scheme_is_rejected(self):
        self.assertRejected("uwenzai://payment/verify")

    # ------------------------------------------------------------------
    # Encoding and control-character tricks
    # ------------------------------------------------------------------
    def test_encoded_slashes_cannot_smuggle_another_host(self):
        self.assertRejected("/%2F%2Fevil.example/payment/verify")

    def test_double_encoded_host_is_rejected(self):
        self.assertRejected("%252F%252Fevil.example")

    def test_backslash_authority_is_rejected(self):
        self.assertRejected("/\\evil.example/payment/verify")

    def test_newline_in_destination_is_rejected(self):
        self.assertRejected("/payment/verify\nSet-Cookie: a=b")

    def test_carriage_return_in_destination_is_rejected(self):
        self.assertRejected("/payment/verify\r\nLocation: https://evil.example")

    def test_null_byte_in_destination_is_rejected(self):
        self.assertRejected("/payment/verify\x00.evil")

    def test_traversal_out_of_the_allowlisted_route_is_rejected(self):
        self.assertRejected("/payment/../../admin")

    def test_encoded_traversal_is_rejected(self):
        self.assertRejected("/payment/%2e%2e/%2e%2e/admin")

    # ------------------------------------------------------------------
    # Anything the server did not offer
    # ------------------------------------------------------------------
    def test_unlisted_local_route_is_rejected(self):
        self.assertRejected("/admin/")

    def test_path_prefix_of_an_allowlisted_route_is_rejected(self):
        self.assertRejected("/payment")

    def test_path_extension_of_an_allowlisted_route_is_rejected(self):
        self.assertRejected("/payment/verify/../../etc")

    def test_query_string_is_rejected(self):
        # The server appends nothing to the callback; a client-supplied query is
        # an attempt to influence what the return page does.
        self.assertRejected("/payment/verify?next=https://evil.example")

    def test_fragment_is_rejected(self):
        self.assertRejected("/payment/verify#https://evil.example")

    def test_relative_path_is_rejected(self):
        self.assertRejected("payment/verify")

    def test_non_string_input_is_rejected(self):
        for value in (1, [], {}, object()):
            self.assertRejected(value)


@callback_settings
class BuildCallbackUrlTests(TestCase):
    def test_url_is_built_from_the_configured_origin(self):
        self.assertEqual(
            callbacks.build_callback_url("/payment/verify"),
            "https://app.uwenzai.com/payment/verify",
        )

    def test_unlisted_path_builds_nothing(self):
        self.assertEqual(callbacks.build_callback_url("/admin/"), "")

    def test_empty_path_builds_nothing(self):
        self.assertEqual(callbacks.build_callback_url(""), "")

    @override_settings(PAYMENT_CALLBACK_ALLOWED_ORIGINS=[], CORS_ALLOWED_ORIGINS=[])
    def test_no_configured_origin_sends_no_callback_at_all(self):
        """Better to let the Paystack dashboard default apply than to guess a host."""
        self.assertEqual(callbacks.build_callback_url("/payment/verify"), "")

    @override_settings(
        PAYMENT_CALLBACK_ALLOWED_ORIGINS=[],
        CORS_ALLOWED_ORIGINS=["https://cors.uwenzai.com/"],
    )
    def test_cors_origins_are_the_fallback_allowlist(self):
        self.assertEqual(
            callbacks.build_callback_url("/payment/verify"),
            "https://cors.uwenzai.com/payment/verify",
        )
        self.assertEqual(
            callbacks.resolve_callback_path("https://cors.uwenzai.com/payment/verify"),
            "/payment/verify",
        )


@callback_settings
class DefaultPathTests(TestCase):
    def test_configured_default_is_used_when_allowlisted(self):
        self.assertEqual(callbacks.default_path(), "/payment/verify")

    @override_settings(PAYMENT_CALLBACK_DEFAULT_PATH="/not/allowlisted")
    def test_unlisted_default_falls_back_to_the_first_allowlisted_path(self):
        self.assertEqual(callbacks.default_path(), "/payment/verify")

    @override_settings(PAYMENT_CALLBACK_ALLOWED_PATHS=[], PAYMENT_CALLBACK_DEFAULT_PATH="")
    def test_no_paths_configured_yields_no_default(self):
        self.assertEqual(callbacks.default_path(), "")
