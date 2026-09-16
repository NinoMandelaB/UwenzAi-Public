"""Dedicated Agent public base and legacy routing; no inference or HTTP traffic."""

import importlib
import os
import runpy
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve, reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.agent_gateway.proxy_service import AgentProxyService
from capp.models import AgentAPIKey, AgentUsageLog, BillingReservation
from capp.tests import _make_user
from capp.throttles import AgentGatewayKeyThrottle
from capp.views.agent_gateway_views import agent_chat_completions, agent_models

CANONICAL_BASE = "https://agent-api.uwenzai.com/api/v1"
BASES = ("/api/v1/", "/api/v1/agent/")


@contextmanager
def service_urls(agent_only):
    """Reload the real root URLconf with a controlled flag, then restore it exactly."""
    import eaaicproject.urls as root_urls

    previous_patterns = root_urls.urlpatterns
    previous_mode = root_urls.SERVE_AGENT_ONLY
    try:
        with mock.patch.dict(os.environ, {"SERVE_AGENT_ONLY": "true" if agent_only else "false"}):
            importlib.reload(root_urls)
            clear_url_caches()
            with override_settings(ROOT_URLCONF="eaaicproject.urls"):
                yield
    finally:
        # Do not leak mode, URL resolver cache, or environment into other suites,
        # including when a failed assertion exits the context early.
        root_urls.urlpatterns = previous_patterns
        root_urls.SERVE_AGENT_ONLY = previous_mode
        clear_url_caches()


class AgentBaseSettingsTests(SimpleTestCase):
    def read_base_setting(self, **environment):
        # Execute actual settings in an isolated namespace, not Django's live
        # settings module. Exclude machine/.env overrides and monitoring hooks.
        env = {
            "DEBUG": "true",
            "DJANGO_SECRET_KEY": "isolated-routing-settings-test-only",
            "DATABASE_URL": "postgres://test:test@localhost/test",
            **environment,
        }
        settings_path = Path(__file__).resolve().parents[1] / "eaaicproject" / "settings.py"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("dotenv.load_dotenv"):
            return runpy.run_path(str(settings_path))["AGENT_BASE_URL"]

    def test_default_base_is_canonical_without_trailing_slash(self):
        self.assertEqual(self.read_base_setting(), CANONICAL_BASE)

    def test_environment_custom_origin_and_path_are_preserved(self):
        for custom in (
            "https://gateway.example.invalid/custom/prefix/v1",
            "https://gateway.example.invalid/api/v1/agent/",
        ):
            with self.subTest(custom=custom):
                self.assertEqual(self.read_base_setting(AGENT_BASE_URL=custom), custom)


class AgentPublicRouteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.full_key, self.key = AgentKeyService.create_key(self.user, "Routing test")
        self.external = APIClient()
        self.external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        self.account = APIClient()
        self.account.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        self.payload = {
            "model": "routing-model",
            "provider": "cortecs",
            "messages": [{"role": "user", "content": "Routing fixture"}],
            "max_tokens": 17,
            "temperature": 0.25,
            "client_operation_id": "routing-operation",
        }
        # These routing fixtures never tokenize, fetch a catalog, or infer.
        for target in ("httpx.get", "httpx.post", "requests.sessions.Session.request"):
            patcher = mock.patch(target, side_effect=AssertionError("External HTTP is forbidden in routing tests"))
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def paths(endpoint):
        return [base + endpoint + slash for base in BASES for slash in ("", "/")]

    def assert_direct_private_response(self, response, status):
        self.assertEqual(response.status_code, status, response.content)
        self.assertNotIn("Location", response)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_agent_new_and_legacy_slash_variants_use_identical_view_objects(self):
        with service_urls(True):
            for endpoint, view in (("models", agent_models), ("chat/completions", agent_chat_completions)):
                names = set()
                for url in self.paths(endpoint):
                    with self.subTest(url=url):
                        match = resolve(url)
                        self.assertIs(match.func, view)
                        self.assertEqual(reverse(match.url_name), url)
                        names.add(match.url_name)
                self.assertEqual(len(names), 4)  # No duplicate reverse names.

    def test_main_backend_keeps_legacy_inference_without_new_root_inference(self):
        with service_urls(False):
            for endpoint, view in (("models", agent_models), ("chat/completions", agent_chat_completions)):
                for slash in ("", "/"):
                    self.assertIs(resolve("/api/v1/agent/" + endpoint + slash).func, view)
                    with self.assertRaises(Resolver404):
                        resolve("/api/v1/" + endpoint + slash)
            self.assertEqual(
                self.external.post("/api/v1/chat/completions", self.payload, format="json").status_code, 404
            )
            self.assertEqual(self.external.get("/api/v1/models").status_code, 404)

    @override_settings(DEBUG=True)
    def test_agent_public_root_does_not_expose_management_account_admin_or_media(self):
        with service_urls(True):
            for path in (
                "/api/v1/keys/",
                "/api/v1/usage/",
                "/api/v1/pricing/",
                "/api/v1/consent/",
                "/api/v1/playground/",
                "/api/v1/playground/history/",
                "/api/v1/auth/export-data/",
                "/api/v1/auth/signin/",
                "/api/v1/admin/",
                "/admin/",
                "/media/private.txt",
            ):
                with self.subTest(path=path):
                    with self.assertRaises(Resolver404):
                        resolve(path)
                    self.assertEqual(self.account.get(path).status_code, 404)

    def test_full_legacy_management_routes_remain_on_both_services(self):
        names = {
            "keys/": "agent_keys",
            f"keys/{self.key.pk}/": "agent_key_delete",
            "usage/": "agent_usage",
            "pricing/": "agent_gateway_pricing",
            "consent/": "agent_gateway_consent",
            "playground/": "agent_playground",
            "playground/history/": "agent_playground_history",
        }
        for agent_only in (True, False):
            with service_urls(agent_only):
                for suffix, name in names.items():
                    self.assertEqual(resolve("/api/v1/agent/" + suffix).url_name, name)
                response = self.account.get("/api/v1/agent/keys/")
                self.assert_direct_private_response(response, 200)
                self.assertEqual(response.json()["data"]["keys"][0]["id"], str(self.key.pk))
                self.assertEqual(self.account.get("/api/v1/agent/consent/").status_code, 200)
                self.assertEqual(
                    self.account.get(
                        "/api/v1/agent/playground/history/?model=routing-model&provider=cortecs"
                    ).status_code,
                    200,
                )

    def test_anonymous_and_account_jwt_cannot_use_either_inference_base(self):
        with service_urls(True), mock.patch.object(AgentProxyService, "execute_request") as execute:
            with mock.patch.object(AgentProxyService, "list_models") as catalog:
                for client in (APIClient(), self.account):
                    for url in self.paths("models"):
                        self.assert_direct_private_response(client.get(url), 401)
                    for url in self.paths("chat/completions"):
                        self.assert_direct_private_response(client.post(url, self.payload, format="json"), 401)
                execute.assert_not_called()
                catalog.assert_not_called()

    def test_revoked_key_and_ineligible_owner_remain_rejected_on_all_aliases(self):
        for changes in ({"is_active": False}, {"account_status": "banned"}):
            self.user.__class__.objects.filter(pk=self.user.pk).update(is_active=True, account_status="active")
            self.user.__class__.objects.filter(pk=self.user.pk).update(**changes)
            with service_urls(True):
                for url in self.paths("chat/completions"):
                    self.assert_direct_private_response(self.external.post(url, self.payload, format="json"), 401)
        self.user.__class__.objects.filter(pk=self.user.pk).update(is_active=True, account_status="active")
        AgentAPIKey.objects.filter(pk=self.key.pk).update(is_active=False)
        with service_urls(True):
            for url in self.paths("models"):
                self.assert_direct_private_response(self.external.get(url), 401)

    def test_model_list_dispatch_is_identical_without_redirects(self):
        models = [{"id": "routing-model", "object": "model", "owned_by": "cortecs"}]
        with service_urls(True), mock.patch.object(AgentProxyService, "list_models", return_value=models) as catalog:
            for url in self.paths("models"):
                self.assertIs(resolve(url).func, agent_models)
                response = self.external.get(url)
                self.assert_direct_private_response(response, 200)
                self.assertEqual(response.json(), {"object": "list", "data": models})
            self.assertEqual(catalog.call_count, 4)

    def test_post_body_key_and_operation_forward_to_same_billed_dispatch_without_redirects(self):
        result = {"id": "mock-completion", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        with service_urls(True), mock.patch.object(
            AgentProxyService, "execute_request", return_value=result
        ) as execute:
            for url in self.paths("chat/completions"):
                response = self.external.post(url, self.payload, format="json")
                self.assert_direct_private_response(response, 200)
                self.assertEqual(response.json(), result)
                args = execute.call_args.kwargs
                self.assertEqual(args["key"].pk, self.key.pk)
                self.assertEqual(args["model"], self.payload["model"])
                self.assertEqual(args["provider_name"], self.payload["provider"])
                self.assertEqual(args["messages"], self.payload["messages"])
                self.assertEqual(args["client_operation_id"], self.payload["client_operation_id"])
                self.assertEqual(args["params"]["max_tokens"], 17)
                self.assertEqual(args["params"]["temperature"], 0.25)
            self.assertEqual(execute.call_count, 4)
        self.assertFalse(AgentUsageLog.objects.exists())
        self.assertFalse(BillingReservation.objects.exists())

    def test_aliases_preserve_billing_error_http_status_and_cache_contract(self):
        with service_urls(True), mock.patch.object(AgentProxyService, "execute_request") as execute:
            for error, status in (
                ("insufficient_sparks", 402),
                ("budget_exceeded", 429),
                ("upstream_error", 502),
                ("refund_pending", 503),
                ("idempotency_conflict", 409),
            ):
                execute.return_value = {"error": {"status": error, "message": "Controlled test outcome"}}
                for url in self.paths("chat/completions"):
                    response = self.external.post(url, self.payload, format="json")
                    self.assert_direct_private_response(response, status)
                    self.assertEqual(response.json(), execute.return_value)

    def test_real_request_validation_runs_before_paid_dispatch_for_every_alias(self):
        with service_urls(True), mock.patch.object(AgentProxyService, "_execute_locked") as paid:
            for url in self.paths("chat/completions"):
                response = self.external.post(url, {**self.payload, "messages": []}, format="json")
                self.assert_direct_private_response(response, 400)
                self.assertEqual(response.json()["error"]["status"], "invalid_request")
            paid.assert_not_called()
        self.assertFalse(BillingReservation.objects.exists())

    def test_provider_endpoint_and_credential_overrides_still_rejected_before_dispatch(self):
        with service_urls(True), mock.patch.object(AgentProxyService, "execute_request") as execute:
            for field in ("base_url", "endpoint", "endpoint_url", "api_url", "api_key"):
                for url in self.paths("chat/completions"):
                    response = self.external.post(url, {**self.payload, field: "not-allowed"}, format="json")
                    self.assert_direct_private_response(response, 400)
            execute.assert_not_called()

    def test_method_restrictions_are_unchanged_without_redirects(self):
        with service_urls(True):
            for url in self.paths("models"):
                self.assert_direct_private_response(self.external.post(url, {}, format="json"), 405)
            for url in self.paths("chat/completions"):
                self.assert_direct_private_response(self.external.get(url), 405)

    def test_all_aliases_share_the_existing_key_throttle(self):
        with service_urls(True), mock.patch.object(AgentGatewayKeyThrottle, "rate", "2/minute"):
            with mock.patch.object(AgentProxyService, "list_models", return_value=[]) as catalog:
                self.assert_direct_private_response(self.external.get("/api/v1/models"), 200)
                self.assert_direct_private_response(self.external.get("/api/v1/agent/models/"), 200)
                self.assert_direct_private_response(self.external.get("/api/v1/models/"), 429)
                self.assertEqual(catalog.call_count, 2)

    def test_usage_and_pricing_advertise_same_configured_base_on_both_services(self):
        for agent_only in (True, False):
            with service_urls(agent_only):
                for base_url in (CANONICAL_BASE, "https://gateway.example.invalid/custom/v1/"):
                    with override_settings(AGENT_BASE_URL=base_url):
                        with mock.patch(
                            "capp.views.agent_gateway_views.AgentPricingService.list_models_with_spark_prices",
                            return_value=[],
                        ):
                            for endpoint in ("usage/", "pricing/"):
                                response = self.account.get("/api/v1/agent/" + endpoint)
                                self.assert_direct_private_response(response, 200)
                                self.assertEqual(response.json()["data"]["base_url"], base_url)

    def test_url_mode_and_environment_restore_even_when_context_exits_with_error(self):
        import eaaicproject.urls as root_urls

        original_patterns = root_urls.urlpatterns
        original_mode = root_urls.SERVE_AGENT_ONLY
        original_environment = os.environ.get("SERVE_AGENT_ONLY")
        with self.assertRaisesRegex(RuntimeError, "test exit"):
            with service_urls(True):
                self.assertIs(resolve("/api/v1/models").func, agent_models)
                with service_urls(False):
                    with self.assertRaises(Resolver404):
                        resolve("/api/v1/models")
                self.assertIs(resolve("/api/v1/models").func, agent_models)
                raise RuntimeError("test exit")
        self.assertIs(root_urls.urlpatterns, original_patterns)
        self.assertEqual(root_urls.SERVE_AGENT_ONLY, original_mode)
        self.assertEqual(os.environ.get("SERVE_AGENT_ONLY"), original_environment)
