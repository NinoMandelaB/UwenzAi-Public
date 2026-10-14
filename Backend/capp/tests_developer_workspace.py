"""Developer workspace integration tests. Real JWTs, real DB billing, mocked inference only."""

import re
import uuid
from datetime import timedelta
from pathlib import Path
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from redis import Redis
from redis.exceptions import ConnectionError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from capp.classes.agent_gateway import playground_service
from capp.classes.agent_gateway.guards import (
    AGENT_GATEWAY_CONSENT_VERSION,
    gateway_operation_lock,
    gateway_user_lock,
)
from capp.classes.agent_gateway.catalog_sync import run_catalog_sync
from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.agent_gateway.pricing_service import AgentPricingService, get_markup_percent
from capp.classes.agent_gateway.providers.base import ProviderResult
from capp.classes.agent_gateway.providers.cortecs import CortecsAgentProvider
from capp.classes.agent_gateway.providers.aki import AkiAgentProvider
from capp.classes.agent_gateway.providers.fikra import FikraAgentProvider
from capp.classes.agent_gateway.providers.mistral import MistralAgentProvider
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider
from capp.classes.agent_gateway.providers.registry import reset_registry
from capp.classes.agent_gateway.proxy_service import AgentProxyService
from capp.classes.agent_gateway.usage_service import AgentUsageService
from capp.tasks import cleanup_revoked_agent_keys, sync_agent_catalog
from capp.models import (
    AgentAPIKey,
    AgentGatewayOperation,
    AgentModelPrice,
    AgentPlaygroundHistory,
    AgentUsageLog,
    BillingReservation,
    ComplianceConfirmationLog,
    SparkPool,
)
from capp.tests_agent_gateway import _make_user_with_sparks

BASE = "/api/v1/agent/"


@override_settings(
    CORTECS_UWENZAI_AGENT_API_KEY="dummy",
    MISTRAL_UWENZAI_AGENT_API_KEY="dummy",
    AGENT_FX_EUR_KES=145,
    AGENT_FX_USD_KES=129,
    AGENT_MARKUP_PERCENT=10,
    MISTRAL_UWENZAI_AGENT_MARKUP_PERCENT=5.0,
    AGENT_EXCHANGE_RATE_API_URL=None,
)
class DeveloperWorkspaceTests(TestCase):
    def setUp(self):
        reset_registry()
        cache.clear()
        self.user = _make_user_with_sparks(10000)
        self.other = _make_user_with_sparks(10000, username="other", email="other@example.com")
        self.full_key, self.key = AgentKeyService.create_key(self.user, "Workspace")
        self.other_full_key, self.other_key = AgentKeyService.create_key(self.other)
        self.client = self.jwt_client(self.user)
        self.price = AgentModelPrice.objects.create(
            provider="cortecs",
            model="shared-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.00"),
            output_price_per_1m=Decimal("2.00"),
            max_context=16384,
            description="Catalog description",
            capabilities=["text"],
        )
        self.second_price = AgentModelPrice.objects.create(
            provider="mistral",
            model="shared-model",
            currency="USD",
            input_price_per_1m=Decimal("22"),
            output_price_per_1m=Decimal("66"),
        )
        self.consent(self.user)
        self.consent(self.other)
        patcher = mock.patch("capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion")
        self.provider = patcher.start()
        self.addCleanup(patcher.stop)
        self.provider.return_value = ProviderResult(
            text="Private reply", usage={"prompt_tokens": 11, "completion_tokens": 7}
        )
        patcher = mock.patch(
            "capp.classes.agent_gateway.providers.openai_base.OpenAICompatibleProvider.chat_completion"
        )
        self.second_provider = patcher.start()
        self.addCleanup(patcher.stop)
        self.second_provider.return_value = self.provider.return_value
        # Fail the test on accidental real provider HTTP calls.
        patcher = mock.patch("httpx.post", side_effect=AssertionError("Real provider HTTP is forbidden in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("httpx.get", side_effect=AssertionError("Real catalog HTTP is forbidden in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(reset_registry)

    @staticmethod
    def jwt_client(user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client

    @staticmethod
    def consent(user):
        ComplianceConfirmationLog.objects.create(
            user=user,
            accepted=True,
            confirmation_type=ComplianceConfirmationLog.ConfirmationType.AGENT_GATEWAY,
            version=AGENT_GATEWAY_CONSENT_VERSION,
        )

    def payload(self, **overrides):
        return {
            "key_id": str(self.key.pk),
            "model": "shared-model",
            "provider": "cortecs",
            "messages": [{"role": "user", "content": "Private question"}],
            "max_tokens": 256,
            "save_history": False,
            "client_operation_id": str(uuid.uuid4()),
            **overrides,
        }

    def chat(self, **overrides):
        return self.client.post(BASE + "playground/", self.payload(**overrides), format="json")

    def history(self, method="get", client=None, provider="cortecs", model="shared-model"):
        return getattr(client or self.client, method)(BASE + f"playground/history/?model={model}&provider={provider}")

    def assert_unbilled(self):
        self.assertFalse(BillingReservation.objects.exists())
        self.assertFalse(AgentUsageLog.objects.exists())
        self.provider.assert_not_called()
        self.second_provider.assert_not_called()
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 10000)

    def test_real_access_token_management_endpoints(self):
        for endpoint in ("keys/", "usage/", "consent/", "pricing/"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(BASE + endpoint).status_code, 200)
        response = self.client.post(BASE + "keys/", {"label": "JWT key"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("full_key", response.data["data"])
        self.assertNotIn("full_key", str(self.client.get(BASE + "keys/").data))
        key_id = response.data["data"]["key_id"]
        self.assertEqual(self.client.delete(BASE + f"keys/{key_id}/").status_code, 200)

    def test_anonymous_and_gateway_bearer_cannot_manage_workspace(self):
        anonymous = APIClient()
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        for client in (anonymous, external):
            self.assertEqual(client.get(BASE + "keys/").status_code, 401)
            self.assertEqual(client.post(BASE + "playground/", self.payload(), format="json").status_code, 401)
            self.assertEqual(
                client.get(BASE + "playground/history/?model=shared-model&provider=cortecs").status_code, 401
            )
        self.assert_unbilled()

    def test_session_auth_and_csrf(self):
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.user)
        self.assertEqual(session.get(BASE + "pricing/").status_code, 200)
        self.assertEqual(session.post(BASE + "playground/", self.payload(), format="json").status_code, 403)
        self.assert_unbilled()

    def test_consent_required_but_catalog_and_history_deletion_allowed(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).delete()
        self.assertEqual(self.chat().status_code, 403)
        self.assertEqual(self.client.post(BASE + "keys/", {}, format="json").status_code, 403)
        self.assertEqual(self.client.get(BASE + "pricing/").status_code, 200)
        self.assertEqual(self.history("delete").status_code, 200)
        self.assert_unbilled()

    def test_foreign_key_and_revocation_are_isolated(self):
        self.assertEqual(self.chat(key_id=str(self.other_key.pk)).status_code, 404)
        self.assertEqual(self.client.delete(BASE + f"keys/{self.other_key.pk}/").status_code, 404)
        self.other_key.refresh_from_db()
        self.assertTrue(self.other_key.is_active)
        self.assert_unbilled()

    def test_revoked_and_expired_key_rejected(self):
        for change in ({"is_active": False}, {"is_active": True, "expires_at": timezone.now() - timedelta(seconds=1)}):
            AgentAPIKey.objects.filter(pk=self.key.pk).update(**change)
            self.assertEqual(self.chat().status_code, 400)
        self.assert_unbilled()

    def test_banned_suspended_deleted_accounts_rejected_by_jwt_and_external_key(self):
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        for changes in (
            {"account_status": "banned"},
            {"account_status": "suspended"},
            {"is_deleted": True},
            {"is_active": False},
        ):
            type(self.user).objects.filter(pk=self.user.pk).update(
                is_deleted=False, is_active=True, account_status="active"
            )
            type(self.user).objects.filter(pk=self.user.pk).update(**changes)
            self.assertEqual(self.client.get(BASE + "pricing/").status_code, 401)
            self.assertEqual(external.post(BASE + "chat/completions", self.payload(), format="json").status_code, 401)
        self.assert_unbilled()

    def test_strict_validation_bounds(self):
        invalid = [
            {"messages": []},
            {"messages": "not a list"},
            {"messages": ["not an object"]},
            {"messages": [{"role": "system", "content": "no"}]},
            {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "http://bad"}]}]},
            {"messages": [{"role": "user", "content": ""}]},
            {"messages": [{"role": "user", "content": "hello", "tool_calls": []}]},
            {"messages": [{"role": "user", "content": "x"}] * 61},
            {"messages": [{"role": "user", "content": "é" * 16384}]},
            {"max_tokens": 1025},
            {"max_tokens": 0},
            {"max_tokens": True},
            {"max_tokens": "50"},
            {"save_history": "true"},
            {"client_operation_id": "not-uuid"},
            {"key_id": "bad"},
            {"provider": "https://example.org"},
            {"provider": None},
            {"model": "absent"},
            {"base_url": "http://127.0.0.1"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.chat(**payload).status_code, 400)
        self.assert_unbilled()

    @mock.patch("capp.classes.agent_gateway.playground_service.TokenUtils.count_message_tokens", return_value=8193)
    def test_token_cap_rejects_before_billing(self, tokens):
        self.assertEqual(self.chat().status_code, 400)
        self.assert_unbilled()

    def test_exact_message_and_output_limits_accepted(self):
        response = self.chat(messages=[{"role": "user", "content": "hi"}] * 60, max_tokens=1024)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self.provider.call_args.kwargs["messages"]), 60)
        self.assertEqual(self.provider.call_args.kwargs["params"]["max_tokens"], 1024)

    def test_model_context_and_output_limit(self):
        self.price.max_context = 20
        self.price.save()
        self.assertEqual(self.chat(max_tokens=20).status_code, 400)
        self.price.max_context = 16384
        self.price.max_output_tokens = 100
        self.price.save()
        self.assertEqual(self.chat(max_tokens=101).status_code, 400)
        self.assert_unbilled()

    def test_budget_checks_full_reservation(self):
        self.key.monthly_spark_budget = 1
        self.key.save()
        response = self.chat(provider="mistral", max_tokens=1024)
        self.assertEqual(response.status_code, 429, response.data)
        self.assert_unbilled()

    def test_empty_balance_no_charge_or_provider(self):
        SparkPool.objects.filter(user=self.user).update(sparks=0)
        response = self.chat()
        self.assertEqual(response.status_code, 402, response.data)
        self.assertFalse(BillingReservation.objects.exists())
        self.assertFalse(AgentGatewayOperation.objects.exists())
        self.provider.assert_not_called()

    def test_explicit_provider_billing_and_catalog_match(self):
        response = self.chat(provider="mistral")
        self.assertEqual(response.status_code, 200, response.data)
        self.provider.assert_not_called()
        self.second_provider.assert_called_once()
        log = AgentUsageLog.objects.get(user=self.user)
        self.assertEqual(log.provider, "mistral")
        self.assertEqual(log.cost_currency, "USD")
        exact = AgentPricingService.calculate_cost("mistral", "shared-model", 11, 7)
        self.assertEqual(log.sparks_charged, exact["sparks"])
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 10000 - exact["sparks"])

    def test_disabled_or_invalid_pair_cannot_fall_back(self):
        self.price.is_active = False
        self.price.save()
        self.assertEqual(self.chat().status_code, 400)
        self.assertEqual(self.chat(provider="fikra").status_code, 400)
        self.assert_unbilled()

    def test_pricing_exact_kes_and_unknown_metadata(self):
        response = self.client.get(BASE + "pricing/")
        data = response.data["data"]
        models = {m["provider"]: m for m in data["models"]}
        self.assertEqual(models["cortecs"]["input_kes_per_1m"], "159.5")
        self.assertEqual(models["cortecs"]["output_kes_per_1m"], "319")
        self.assertEqual(models["cortecs"]["max_context"], 16384)
        self.assertEqual(models["cortecs"]["capabilities"], ["text"])
        self.assertIsNone(models["mistral"]["max_context"])
        self.assertIsNone(models["mistral"]["max_output_tokens"])
        self.assertEqual(models["mistral"]["capabilities"], [])
        self.assertTrue(models["mistral"]["available"])
        self.assertEqual(data["markup_percent"], 10)
        self.assertEqual(
            data["playground_limits"], {"max_messages": 60, "max_input_tokens": 8192, "max_output_tokens": 1024}
        )
        self.assertIn("base_url", data)
        self.assertNotIn("dummy", str(response.data))

    def test_pricing_per_provider_markup_and_provider_details(self):
        data = self.client.get(BASE + "pricing/").data["data"]
        models = {m["provider"]: m for m in data["models"]}
        self.assertEqual(models["cortecs"]["markup_percent"], 10.0)
        self.assertEqual(models["mistral"]["markup_percent"], 5.0)
        # 22 USD * 1.05 markup * 129 KES/USD = 2979.9
        self.assertEqual(models["mistral"]["input_kes_per_1m"], "2979.9")
        # 66 USD * 1.05 markup * 129 KES/USD = 8939.7
        self.assertEqual(models["mistral"]["output_kes_per_1m"], "8939.7")
        providers = data["providers"]
        self.assertEqual(set(providers), {"cortecs", "mistral", "fikra", "inceptron", "aki", "ovh", "tensorx"})
        self.assertEqual(providers["cortecs"], {"country": "Austria (EU)", "kind": "gateway"})
        self.assertEqual(providers["mistral"], {"country": "France (EU)", "kind": "inference"})
        self.assertEqual(providers["fikra"]["country"], "Kenya")
        self.assertEqual(providers["tensorx"], {"country": "Ireland (EU)", "kind": "inference"})

    def test_unconfigured_catalog_pair_is_unavailable(self):
        reset_registry()
        with override_settings(MISTRAL_UWENZAI_AGENT_API_KEY=None):
            data = self.client.get(BASE + "pricing/").data["data"]
        self.assertFalse(next(m for m in data["models"] if m["provider"] == "mistral")["available"])

    def test_external_slashless_and_legacy_omitted_provider(self):
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        for path in ("chat/completions", "chat/completions/"):
            response = external.post(
                BASE + path,
                {"model": "shared-model", "messages": [{"role": "user", "content": "hello"}]},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.provider.call_count, 2)
        # Omitted max_tokens defaults to 1024 (was 256 — silently truncated
        # agent replies with finish_reason=length).
        self.assertEqual(self.provider.call_args.kwargs["params"]["max_tokens"], 1024)

    def test_omitted_provider_routes_to_cheapest_and_reports_selection(self):
        # Cortecs: (1 + 2) EUR * 1.10 * 145 KES ~ 479 KES per 1M tokens.
        # Mistral: (22 + 66) USD * 1.05 * 129 KES ~ 11,920 KES per 1M tokens.
        # The cheaper provider must win deterministically, not by row order.
        AgentModelPrice.objects.filter(provider="mistral", model="shared-model").update(
            input_price_per_1m=Decimal("0.01"), output_price_per_1m=Decimal("0.03")
        )
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        response = external.post(
            BASE + "chat/completions",
            {"model": "shared-model", "messages": [{"role": "user", "content": "hello"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.second_provider.assert_called_once()
        self.assertEqual(self.second_provider.call_args.kwargs["model"], "shared-model")
        self.assertEqual(response.data["_uwenzai"]["provider"], "mistral")
        self.assertEqual(response.data["_uwenzai"]["model"], "shared-model")
        log = AgentUsageLog.objects.get()
        self.assertEqual(log.provider, "mistral")
        self.assertEqual(log.model, "shared-model")

    def test_provider_model_prefix_selects_provider(self):
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        op = str(uuid.uuid4())
        response = external.post(
            BASE + "chat/completions",
            {
                "model": "mistral/shared-model",
                "messages": [{"role": "user", "content": "hello"}],
                "client_operation_id": op,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.second_provider.assert_called_once()
        self.assertEqual(self.second_provider.call_args.kwargs["model"], "shared-model")
        self.assertEqual(response.data["_uwenzai"]["provider"], "mistral")
        self.assertEqual(response.data["_uwenzai"]["model"], "shared-model")
        log = AgentUsageLog.objects.get()
        self.assertEqual(log.provider, "mistral")
        self.assertEqual(log.model, "shared-model")
        # Same operation retried through the plain body parameter is not a
        # conflict: identical effective (model, provider) pair.
        response = external.post(
            BASE + "chat/completions",
            {
                "model": "shared-model",
                "provider": "mistral",
                "messages": [{"role": "user", "content": "hello"}],
                "client_operation_id": op,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["_uwenzai"]["idempotent_retry"])

    def test_unknown_prefix_is_not_a_provider_selector(self):
        # "foo/shared-model" is not a known provider prefix, so it is treated
        # as an unknown model name and must fail closed without billing.
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        response = external.post(
            BASE + "chat/completions",
            {"model": "foo/shared-model", "messages": [{"role": "user", "content": "hello"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assert_unbilled()

    def test_slashed_vendor_model_id_still_resolves_without_prefix(self):
        # Inceptron serves vendor-prefixed IDs like "deepseek-ai/DeepSeek-...".
        # The leading segment is not a known provider, so the whole string must
        # stay a single model identifier and resolve through the catalog.
        AgentModelPrice.objects.all().update(is_active=False)
        AgentModelPrice.objects.create(
            provider="mistral",
            model="deepseek-ai/DeepSeek-V4-Flash",
            currency="USD",
            input_price_per_1m=Decimal("0.10"),
            output_price_per_1m=Decimal("0.40"),
        )
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        response = external.post(
            BASE + "chat/completions",
            {
                "model": "deepseek-ai/DeepSeek-V4-Flash",
                "messages": [{"role": "user", "content": "hello"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.second_provider.assert_called_once()
        self.assertEqual(self.second_provider.call_args.kwargs["model"], "deepseek-ai/DeepSeek-V4-Flash")
        self.assertEqual(response.data["_uwenzai"]["model"], "deepseek-ai/DeepSeek-V4-Flash")
        self.assertEqual(response.data["_uwenzai"]["provider"], "mistral")

    def test_external_explicit_provider_and_url_rejection(self):
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        payload = {"model": "shared-model", "provider": "mistral", "messages": [{"role": "user", "content": "hi"}]}
        response = external.post(BASE + "chat/completions", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.second_provider.assert_called_once()
        response = external.post(BASE + "chat/completions", {**payload, "base_url": "http://127.0.0.1"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.second_provider.call_count, 1)

    def test_external_models_requires_key_and_slashless_works(self):
        self.assertEqual(APIClient().get(BASE + "models").status_code, 401)
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        with mock.patch.object(AgentProxyService, "list_models", return_value=[]):
            self.assertEqual(external.get(BASE + "models").status_code, 200)

    def test_cross_user_and_cross_key_idempotency_isolation(self):
        op = str(uuid.uuid4())
        self.assertEqual(self.chat(client_operation_id=op).status_code, 200)
        other_client = self.jwt_client(self.other)
        response = other_client.post(
            BASE + "playground/", self.payload(key_id=str(self.other_key.pk), client_operation_id=op), format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("idempotent_retry", response.data["_uwenzai"])
        _, another_key = AgentKeyService.create_key(self.user)
        response = self.chat(key_id=str(another_key.pk), client_operation_id=op)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.provider.call_count, 3)
        self.assertEqual(BillingReservation.objects.count(), 3)
        self.assertEqual(AgentUsageLog.objects.count(), 3)

    def test_same_operation_retry_and_pair_conflict(self):
        op = str(uuid.uuid4())
        first = self.chat(client_operation_id=op)
        second = self.chat(client_operation_id=op)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(second.data["_uwenzai"]["idempotent_retry"])
        self.assertEqual(second.data["_uwenzai"]["sparks_charged"], 0)
        self.assertEqual(self.chat(client_operation_id=op, provider="mistral").status_code, 409)
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(BillingReservation.objects.count(), 1)

    def test_durable_claim_blocks_crash_reexecution(self):
        op = str(uuid.uuid4())
        AgentGatewayOperation.objects.create(
            user=self.user, key=self.key, client_operation_id=op, model="shared-model", provider="cortecs"
        )
        self.assertEqual(self.chat(client_operation_id=op).status_code, 409)
        self.assert_unbilled()

    def test_key_concurrency_and_playground_account_lock(self):
        operation_id = str(uuid.uuid4())
        with gateway_operation_lock(self.key, operation_id):
            self.assertEqual(self.chat(client_operation_id=operation_id).status_code, 409)
        with gateway_user_lock(self.user):
            self.assertEqual(self.chat().status_code, 409)
        self.assert_unbilled()

    @mock.patch.object(Redis, "lock", side_effect=ConnectionError("cache unavailable"))
    def test_cache_unavailable_fails_closed(self, lock):
        self.assertEqual(self.chat().status_code, 503)
        self.assert_unbilled()

    def test_throttle_account_scope_cannot_rotate_keys_or_ip(self):
        cache.set(f"agent-playground-throttle:{self.user.pk}", [timezone.now().timestamp()] * 20, 60)
        response = self.chat()
        self.assertEqual(response.status_code, 429, response.data)
        self.assert_unbilled()

    def test_opt_out_never_persists_prompts(self):
        self.assertEqual(self.chat().status_code, 200)
        self.assertFalse(AgentPlaygroundHistory.objects.exists())
        self.assertNotIn("Private question", str(list(AgentUsageLog.objects.values())))
        self.assertNotIn("Private reply", str(list(AgentUsageLog.objects.values())))
        self.assertNotIn("Private question", str(list(AgentGatewayOperation.objects.values())))

    def test_history_encrypted_scoped_and_deletable(self):
        response = self.chat(save_history=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["_uwenzai"]["history_saved"])
        row = AgentPlaygroundHistory.objects.get(user=self.user)
        self.assertNotIn("Private", row.messages_encrypted)
        self.assertTrue(row.messages_encrypted.startswith("gAAAA"))
        messages = self.history().data["data"]["messages"]
        self.assertEqual([m["content"] for m in messages], ["Private question", "Private reply"])
        self.assertEqual(self.history(client=self.jwt_client(self.other)).data["data"]["messages"], [])
        self.assertEqual(self.history(provider="mistral").data["data"]["messages"], [])
        self.assertEqual(self.history("delete").status_code, 200)
        self.assertFalse(AgentPlaygroundHistory.objects.exists())
        self.assertEqual(self.history().data["data"]["messages"], [])

    def test_delete_during_inflight_response_never_restores_history(self):
        def completion(**kwargs):
            self.assertEqual(self.history("delete").status_code, 200)
            return ProviderResult(text="Private reply", usage={"prompt_tokens": 11, "completion_tokens": 7})

        self.provider.side_effect = completion
        response = self.chat(save_history=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["_uwenzai"]["history_saved"])
        self.assertFalse(AgentPlaygroundHistory.objects.exists())

    def test_delete_and_new_save_ticket_fences_old_save(self):
        old = playground_service.begin_history(self.user, "shared-model", "cortecs")
        playground_service.delete_history(self.user, "shared-model", "cortecs")
        playground_service.begin_history(self.user, "shared-model", "cortecs")
        written = playground_service.save_history(
            old,
            self.user,
            "shared-model",
            "cortecs",
            self.payload()["messages"],
            self.provider.return_value.to_openai_response(),
        )
        self.assertFalse(written)
        self.assertEqual(self.history().data["data"]["messages"], [])

    def test_history_storage_cap_and_message_trimming(self):
        response = self.chat(save_history=True, messages=[{"role": "user", "content": "hi"}] * 60)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(self.history().data["data"]["messages"]), 60)
        for i in range(19):
            AgentPlaygroundHistory.objects.create(user=self.user, provider="cortecs", model=f"saved-{i}")
        response = self.chat(provider="mistral", save_history=True)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(AgentPlaygroundHistory.objects.filter(user=self.user).count(), 20)
        self.assertEqual(self.provider.call_count, 1)

    def test_history_encryption_missing_rejects_before_charge(self):
        with override_settings(FIELD_ENCRYPTION_KEY=None):
            self.assertEqual(self.chat(save_history=True).status_code, 400)
        self.assert_unbilled()

    def test_history_ciphertext_cannot_be_moved_between_users(self):
        self.assertEqual(self.chat(save_history=True).status_code, 200)
        row = AgentPlaygroundHistory.objects.get()
        AgentPlaygroundHistory.objects.create(
            user=self.other, model=row.model, provider=row.provider, messages_encrypted=row.messages_encrypted
        )
        response = self.history(client=self.jwt_client(self.other))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Private question", str(response.data))

    def test_soft_erasure_purges_history_and_fences_inflight(self):
        ticket = playground_service.begin_history(self.user, "shared-model", "cortecs")
        self.user.soft_delete()
        self.assertFalse(AgentPlaygroundHistory.objects.filter(user=self.user).exists())
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertFalse(
            playground_service.save_history(
                ticket,
                self.user,
                "shared-model",
                "cortecs",
                self.payload()["messages"],
                self.provider.return_value.to_openai_response(),
            )
        )

    def test_account_erasure_hook_purges_history(self):
        from capp.classes.auth.account_erasure import erase_private_content

        playground_service.begin_history(self.user, "shared-model", "cortecs")
        erase_private_content(self.user)
        self.assertFalse(AgentPlaygroundHistory.objects.filter(user=self.user).exists())

    def test_provider_error_is_generic_refunded_and_not_logged_as_prompt(self):
        self.provider.return_value = ProviderResult(
            text="", usage={}, status="upstream_error", error_message="Private question secret-token"
        )
        with self.assertLogs("capp.classes.agent_gateway.proxy_service", level="ERROR") as logs:
            response = self.chat(save_history=False)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("Private question", str(response.data))
        self.assertNotIn("Private question", str(logs.output))
        self.assertNotIn("secret-token", str(logs.output))
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 10000)
        self.assertEqual(BillingReservation.objects.get().status, BillingReservation.Status.REFUNDED)
        self.assertNotIn("Private", BillingReservation.objects.get().refund_reason)

    def test_tool_calls_finish_without_calls_is_refunded_error(self):
        """A tool_calls finish with zero relayed calls is a broken upstream
        response; billing must refund and the client must get an explicit
        upstream error instead of a silently unusable 200 that stalls agent
        loops."""
        self.provider.return_value = ProviderResult(
            text="I'll check that.",
            usage={"prompt_tokens": 276, "completion_tokens": 49},
            finish_reason="tool_calls",
            tool_calls=None,
        )
        with self.assertLogs("capp.classes.agent_gateway.proxy_service", level="ERROR") as logs:
            response = self.chat(save_history=False)
        self.assertEqual(response.status_code, 502)
        self.assertIn("tool_calls_dropped", str(logs.output))
        # Full refund, no sparks settled from the reservation.
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 10000)
        self.assertEqual(BillingReservation.objects.get().status, BillingReservation.Status.REFUNDED)
        usage = AgentUsageLog.objects.get()
        self.assertEqual(usage.status, AgentUsageLog.Status.UPSTREAM_ERROR)
        self.assertEqual(usage.sparks_charged, 0)

    def test_relayed_tool_calls_still_bill_normally(self):
        """The guard must not interfere with valid tool-call responses."""
        self.provider.return_value = ProviderResult(
            text="",
            usage={"prompt_tokens": 276, "completion_tokens": 49},
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Nairobi"}'},
                }
            ],
        )
        response = self.chat(save_history=False)
        self.assertEqual(response.status_code, 200)
        message = response.data["choices"][0]["message"]
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get_weather")
        usage = AgentUsageLog.objects.get()
        self.assertGreater(usage.sparks_charged, 0)
        self.assertEqual(BillingReservation.objects.get().status, BillingReservation.Status.SETTLED)

    def test_all_model_usage_pairs_monthly_and_total_no_truncation(self):
        for i in range(12):
            AgentUsageLog.objects.create(
                user=self.user,
                key=self.key,
                provider="cortecs",
                model=f"model-{i}",
                input_tokens=11,
                output_tokens=7,
                sparks_charged=3,
                cost_native=0,
            )
        old = AgentUsageLog.objects.create(
            user=self.user,
            key=self.key,
            provider="mistral",
            model="model-0",
            input_tokens=20,
            output_tokens=10,
            sparks_charged=8,
            cost_native=0,
        )
        # The usage endpoint buckets the calendar month in the server timezone
        # (Africa/Nairobi, UTC+3): the month starts at 21:00 UTC on the last day
        # of the previous month. "now minus one day" can land after that boundary
        # and flake depending on the hour the suite runs. Anchor to one
        # microsecond before the service's own month start instead.
        previous_month = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(
            microseconds=1
        )
        AgentUsageLog.objects.filter(pk=old.pk).update(created_at=previous_month)
        AgentUsageLog.objects.create(
            user=self.other,
            key=self.other_key,
            provider="other",
            model="secret-other",
            input_tokens=1,
            output_tokens=1,
            sparks_charged=99,
            cost_native=0,
        )
        rows = self.client.get(BASE + "usage/").data["data"]["by_model"]
        self.assertEqual(len(rows), 13)
        row = next(row for row in rows if row["provider"] == "mistral")
        self.assertEqual(row["total_input_tokens"], 20)
        self.assertEqual(row["total_output_tokens"], 10)
        self.assertEqual(row["total_sparks"], 8)
        self.assertEqual(row["monthly_input_tokens"], 0)
        self.assertEqual(row["monthly_calls"], 0)
        row = next(row for row in rows if row["provider"] == "cortecs")
        self.assertEqual(row["monthly_sparks"], 3)
        self.assertEqual(row["monthly_input_tokens"], 11)
        self.assertEqual(row["monthly_output_tokens"], 7)
        self.assertEqual(row["monthly_calls"], 1)

    def test_playground_usage_is_tagged_playground(self):
        response = self.chat()
        self.assertEqual(response.status_code, 200, response.data)
        log = AgentUsageLog.objects.get()
        self.assertEqual(log.source, AgentUsageLog.Source.PLAYGROUND)

    def test_direct_api_usage_is_tagged_api(self):
        result = AgentProxyService.execute_request(
            key=self.key,
            model="shared-model",
            provider_name="cortecs",
            messages=[{"role": "user", "content": "Private question"}],
            params={"max_tokens": 256},
        )
        self.assertNotIn("error", result)
        log = AgentUsageLog.objects.get()
        self.assertEqual(log.source, AgentUsageLog.Source.API)

    def test_usage_endpoint_reports_by_key_and_source(self):
        self.assertEqual(self.chat().status_code, 200)
        result = AgentProxyService.execute_request(
            key=self.key,
            model="shared-model",
            provider_name="cortecs",
            messages=[{"role": "user", "content": "Private question"}],
            params={"max_tokens": 256},
        )
        self.assertNotIn("error", result)

        data = self.client.get(BASE + "usage/").data["data"]
        rows = data["by_key"]
        self.assertEqual(len(rows), 2)
        sources = {row["source"] for row in rows}
        self.assertEqual(sources, {AgentUsageLog.Source.PLAYGROUND, AgentUsageLog.Source.API})
        for row in rows:
            self.assertEqual(row["key_id"], str(self.key.pk))
            self.assertFalse(row["removed"])
            self.assertTrue(row["is_active"])

    def test_usage_by_key_treats_legacy_rows_as_api(self):
        self.assertEqual(self.chat().status_code, 200)
        # Rows written before the source field existed (NULL) display as API usage.
        AgentUsageLog.objects.update(source=None)
        rows = AgentUsageService.get_usage_by_key(str(self.user.user_id))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], AgentUsageLog.Source.API)

    def test_usage_by_key_merges_legacy_null_with_new_api_rows(self):
        """NULL (pre-field) and 'api' rows for one key are ONE Direct API line,
        not two rows that would collide in the UI with the same React key."""
        self.assertEqual(self.chat().status_code, 200)
        result = AgentProxyService.execute_request(
            key=self.key,
            model="shared-model",
            provider_name="cortecs",
            messages=[{"role": "user", "content": "Private question"}],
            params={"max_tokens": 256},
        )
        self.assertNotIn("error", result)
        # Simulate the deployed mix: the older API row predates the source field.
        first_api_log = AgentUsageLog.objects.filter(source=AgentUsageLog.Source.API).order_by("created_at").first()
        AgentUsageLog.objects.filter(pk=first_api_log.pk).update(source=None)
        result = AgentProxyService.execute_request(
            key=self.key,
            model="shared-model",
            provider_name="cortecs",
            messages=[{"role": "user", "content": "Another question"}],
            params={"max_tokens": 256},
        )
        self.assertNotIn("error", result)

        rows = AgentUsageService.get_usage_by_key(str(self.user.user_id))
        self.assertEqual(len(rows), 2)
        api_row = next(row for row in rows if row["source"] == AgentUsageLog.Source.API)
        self.assertEqual(api_row["total_calls"], 2)
        self.assertIsNotNone(next(row for row in rows if row["source"] == AgentUsageLog.Source.PLAYGROUND))

    def test_sensitive_responses_are_private_no_store(self):
        responses = [
            self.client.get(BASE + "keys/"),
            self.client.post(BASE + "keys/", {"label": "Sensitive key"}, format="json"),
            self.client.get(BASE + "usage/"),
            self.client.get(BASE + "consent/"),
            self.client.get(BASE + "pricing/"),
            self.history(),
            self.history("delete"),
            self.chat(),
        ]
        for response in responses:
            with self.subTest(code=response.status_code):
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("private", response["Cache-Control"])

    def test_session_auth_rejects_suspended_account(self):
        client = APIClient()
        client.force_login(self.user)
        type(self.user).objects.filter(pk=self.user.pk).update(account_status="suspended")
        self.assertEqual(client.get(BASE + "keys/").status_code, 403)
        self.assert_unbilled()

    def test_jwt_can_accept_consent_then_create_key(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).delete()
        response = self.client.post(
            BASE + "consent/", {"accepted": True, "version": AGENT_GATEWAY_CONSENT_VERSION}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["data"]["confirmed"])
        self.assertEqual(self.client.post(BASE + "keys/", {"label": "Consented key"}, format="json").status_code, 200)

    def test_legacy_consent_requires_review_without_changing_history_or_management_access(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).update(version="1.0")
        before = list(ComplianceConfirmationLog.objects.filter(user=self.user).values())
        response = self.client.get(BASE + "consent/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["data"]["confirmed"])
        self.assertEqual(response.data["data"]["version"], AGENT_GATEWAY_CONSENT_VERSION)
        self.assertEqual(self.chat(save_history=True).status_code, 403)
        self.assertEqual(self.client.post(BASE + "keys/", {}, format="json").status_code, 403)
        for endpoint in ("keys/", "usage/", "pricing/"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(BASE + endpoint).status_code, 200)
        self.assertEqual(self.history().status_code, 200)
        self.assertEqual(self.history("delete").status_code, 200)
        self.assertEqual(self.client.delete(BASE + f"keys/{self.key.pk}/").status_code, 200)
        self.assertEqual(before, list(ComplianceConfirmationLog.objects.filter(user=self.user).values()))
        self.assertFalse(AgentPlaygroundHistory.objects.exists())
        self.assert_unbilled()

    def test_gateway_consent_requires_actual_boolean_acceptance(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).delete()
        for payload in (
            {},
            [],
            {"version": AGENT_GATEWAY_CONSENT_VERSION},
            *(
                {"accepted": value, "version": AGENT_GATEWAY_CONSENT_VERSION}
                for value in (False, None, 1, 0, "true", "false", [], {})
            ),
        ):
            with self.subTest(payload=payload):
                response = self.client.post(BASE + "consent/", payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["status"], "consent_required")
                self.assertIn("no-store", response["Cache-Control"])
                self.assertFalse(ComplianceConfirmationLog.objects.filter(user=self.user).exists())
        self.assert_unbilled()

    def test_gateway_consent_rejects_absent_stale_and_unknown_versions(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).update(version="1.0")
        before = list(ComplianceConfirmationLog.objects.filter(user=self.user).values())
        for version in (None, "", "1.0", "2026-09-11", "2099-01-01", 1, [], {}):
            with self.subTest(version=version):
                payload = {"accepted": True}
                if version is not None:
                    payload["version"] = version
                response = self.client.post(BASE + "consent/", payload, format="json")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["status"], "consent_version_mismatch")
                self.assertEqual(response.data["version"], AGENT_GATEWAY_CONSENT_VERSION)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(before, list(ComplianceConfirmationLog.objects.filter(user=self.user).values()))
        self.assert_unbilled()

    def test_gateway_consent_malformed_json_and_unsupported_content_do_not_record_acceptance(self):
        before = list(ComplianceConfirmationLog.objects.values())
        for body, content_type, expected in (
            ('{"accepted": true,', "application/json", 400),
            ("null", "application/json", 400),
            ("true", "application/json", 400),
            ('"accepted"', "application/json", 400),
            ("accepted=true", "text/plain", 415),
        ):
            with self.subTest(body=body):
                response = self.client.post(BASE + "consent/", body, content_type=content_type)
                self.assertEqual(response.status_code, expected)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(before, list(ComplianceConfirmationLog.objects.values()))
        self.assert_unbilled()

    def test_gateway_acceptance_appends_exact_version_preserving_registration_and_legacy_records(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).update(version="1.0")
        self.user.compliance_version = "2026-09-11"
        self.user.save(update_fields=["compliance_version"])
        for kind in ("age_gate", "terms", "privacy"):
            ComplianceConfirmationLog.objects.create(
                user=self.user, confirmation_type=kind, version="2026-09-11", accepted=True
            )
        before = list(ComplianceConfirmationLog.objects.filter(user=self.user).values())
        old_ids = [row["compliance_confirmation_log_id"] for row in before]
        response = self.client.post(
            BASE + "consent/",
            {"accepted": True, "version": AGENT_GATEWAY_CONSENT_VERSION},
            format="json",
            REMOTE_ADDR="192.0.2.10",
            HTTP_USER_AGENT="Explicit legal review test",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["version"], AGENT_GATEWAY_CONSENT_VERSION)
        log = ComplianceConfirmationLog.objects.get(
            user=self.user, confirmation_type="agent_gateway", version=AGENT_GATEWAY_CONSENT_VERSION
        )
        self.assertTrue(log.accepted)
        self.assertEqual(log.ip_address, "192.0.2.10")
        self.assertEqual(log.user_agent, "Explicit legal review test")
        self.assertEqual(response.data["data"]["accepted_at"], log.accepted_at.isoformat())
        self.assertEqual(before, list(ComplianceConfirmationLog.objects.filter(pk__in=old_ids).values()))
        self.user.refresh_from_db()
        self.assertEqual(self.user.compliance_version, "2026-09-11")
        self.assertTrue(self.client.get(BASE + "consent/").data["data"]["confirmed"])
        self.assertEqual(ComplianceConfirmationLog.objects.filter(user=self.user).count(), len(before) + 1)
        self.assert_unbilled()

    def test_general_current_consent_and_rejected_gateway_record_do_not_unlock_gateway(self):
        ComplianceConfirmationLog.objects.filter(user=self.user).delete()
        for kind in ("age_gate", "terms", "privacy"):
            ComplianceConfirmationLog.objects.create(
                user=self.user, confirmation_type=kind, version=AGENT_GATEWAY_CONSENT_VERSION, accepted=True
            )
        ComplianceConfirmationLog.objects.create(
            user=self.user,
            confirmation_type="agent_gateway",
            version=AGENT_GATEWAY_CONSENT_VERSION,
            accepted=False,
        )
        # Another user's current acceptance also must not apply to this account.
        self.assertFalse(self.client.get(BASE + "consent/").data["data"]["confirmed"])
        self.assertEqual(self.chat().status_code, 403)
        self.assertEqual(self.client.post(BASE + "keys/", {}, format="json").status_code, 403)
        self.assert_unbilled()

    def test_gateway_consent_post_requires_account_authentication_and_session_csrf(self):
        payload = {"accepted": True, "version": AGENT_GATEWAY_CONSENT_VERSION}
        before = list(ComplianceConfirmationLog.objects.values())
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        for client in (APIClient(), external):
            self.assertEqual(client.post(BASE + "consent/", payload, format="json").status_code, 401)
        session = APIClient(enforce_csrf_checks=True)
        session.force_login(self.user)
        self.assertEqual(session.post(BASE + "consent/", payload, format="json").status_code, 403)
        self.assertEqual(before, list(ComplianceConfirmationLog.objects.values()))
        self.assert_unbilled()

    def test_existing_external_inference_and_paid_replay_do_not_require_new_workspace_consent(self):
        external = APIClient()
        external.credentials(HTTP_AUTHORIZATION=f"Bearer {self.full_key}")
        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                if legacy:
                    ComplianceConfirmationLog.objects.filter(user=self.user).update(version="1.0")
                else:
                    ComplianceConfirmationLog.objects.filter(user=self.user).delete()
                before = list(ComplianceConfirmationLog.objects.filter(user=self.user).values())
                payload = self.payload()
                response = external.post(BASE + "chat/completions", payload, format="json")
                self.assertEqual(response.status_code, 200, response.data)
                self.assertTrue(response.data["choices"])
                calls = self.provider.call_count
                balance = SparkPool.objects.get(user=self.user).sparks
                reservations = BillingReservation.objects.count()
                retry = external.post(BASE + "chat/completions", payload, format="json")
                self.assertEqual(retry.status_code, 200, retry.data)
                self.assertTrue(retry.data["_uwenzai"]["idempotent_retry"])
                self.assertEqual(retry.data["_uwenzai"]["sparks_charged"], 0)
                self.assertEqual(retry.data["choices"], [])
                self.assertEqual(self.provider.call_count, calls)
                self.assertEqual(SparkPool.objects.get(user=self.user).sparks, balance)
                self.assertEqual(BillingReservation.objects.count(), reservations)
                self.assertEqual(before, list(ComplianceConfirmationLog.objects.filter(user=self.user).values()))
                self.assertFalse(self.client.get(BASE + "consent/").data["data"]["confirmed"])
                self.assertEqual(self.chat().status_code, 403)
        self.key.refresh_from_db()
        self.assertTrue(self.key.is_active)
        self.assertFalse(AgentPlaygroundHistory.objects.exists())

    def test_account_export_retains_both_gateway_acceptance_versions(self):
        from capp.classes.auth.auth import Auth

        ComplianceConfirmationLog.objects.filter(user=self.user).update(version="1.0")
        old = ComplianceConfirmationLog.objects.get(user=self.user)
        response = self.client.post(
            BASE + "consent/", {"accepted": True, "version": AGENT_GATEWAY_CONSENT_VERSION}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        current = ComplianceConfirmationLog.objects.get(user=self.user, version=AGENT_GATEWAY_CONSENT_VERSION)
        result = Auth.export_user_data(self.user)
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["data"]["compliance_consents"],
            [
                {
                    "confirmation_type": "agent_gateway",
                    "accepted": True,
                    "version": row.version,
                    "accepted_at": row.accepted_at.isoformat(),
                    "ip_address": row.ip_address,
                    "user_agent": row.user_agent,
                }
                for row in (current, old)
            ],
        )
        self.assert_unbilled()

    def test_catalog_refresh_persists_only_verified_metadata(self):
        from capp.classes.agent_gateway.providers.cortecs import CortecsAgentProvider

        provider = CortecsAgentProvider()
        provider._upsert_catalog_to_db(
            [
                {
                    "id": "shared-model",
                    "input_price_per_1m": Decimal("1.00"),
                    "output_price_per_1m": Decimal("2.00"),
                    "max_context": 65536,
                    "max_output_tokens": 4096,
                    "description": "Verified description",
                    "supported_features": ["text", "tools"],
                }
            ]
        )
        self.price.refresh_from_db()
        self.assertEqual(self.price.max_context, 65536)
        self.assertEqual(self.price.max_output_tokens, 4096)
        self.assertEqual(self.price.capabilities, ["text", "tools"])
        self.price.is_active = False
        self.price.save()
        provider._upsert_catalog_to_db(
            [
                {
                    "id": "shared-model",
                    "input_price_per_1m": Decimal("1.00"),
                    "output_price_per_1m": Decimal("2.00"),
                    "max_context": "invented",
                    "max_output_tokens": -1,
                }
            ]
        )
        self.price.refresh_from_db()
        self.assertEqual(self.price.max_context, 65536)
        self.assertEqual(self.price.max_output_tokens, 4096)
        self.assertEqual(self.price.description, "Verified description")
        self.assertEqual(self.price.capabilities, ["text", "tools"])
        self.assertFalse(self.price.is_active)

    def test_history_caps_saved_tokens_and_bytes(self):
        self.provider.return_value.text = "very long reply " * 12000
        response = self.chat(save_history=True)
        self.assertEqual(response.status_code, 200, response.data)
        messages = self.history().data["data"]["messages"]
        self.assertLessEqual(playground_service.message_bytes(messages), 32768)
        self.assertLessEqual(playground_service.TokenUtils.count_message_tokens(messages, "shared-model"), 8192)

    def test_opt_out_does_not_update_existing_history(self):
        self.assertEqual(self.chat(save_history=True).status_code, 200)
        before = AgentPlaygroundHistory.objects.get().messages_encrypted
        response = self.chat(messages=[{"role": "user", "content": "Never save this"}], save_history=False)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(AgentPlaygroundHistory.objects.get().messages_encrypted, before)
        self.assertNotIn("Never save this", str(self.history().data))

    def test_pending_reserved_budget_cannot_be_spent_again(self):
        from capp.classes.billing.reservation_service import BillingReservationService

        self.key.monthly_spark_budget = 3
        self.key.save()
        BillingReservationService.reserve(
            user=self.user,
            feature="agent_gateway",
            amount_sparks=3,
            idempotency_key=f"agent:{self.key.pk}:pending",
            operation_reference="pending-test-reservation",
        )
        self.assertEqual(self.chat().status_code, 429)
        self.provider.assert_not_called()
        self.assertEqual(BillingReservation.objects.count(), 1)

    def test_legacy_unfinished_reservation_is_not_replayed(self):
        from capp.classes.billing.reservation_service import BillingReservationService

        op = str(uuid.uuid4())
        BillingReservationService.reserve(user=self.user, feature="agent_gateway", amount_sparks=1, idempotency_key=op)
        self.assertEqual(self.chat(client_operation_id=op).status_code, 409)
        self.provider.assert_not_called()
        self.assertEqual(BillingReservation.objects.count(), 1)

    def test_different_external_keys_can_execute_concurrently(self):
        _, other_key = AgentKeyService.create_key(self.user)
        with gateway_operation_lock(self.key, str(uuid.uuid4())):
            response = AgentProxyService.execute_request(
                other_key,
                "shared-model",
                [{"role": "user", "content": "Parallel request"}],
                {"max_tokens": 256},
                provider_name="cortecs",
            )
        self.assertNotIn("error", response)
        self.provider.assert_called_once()

    def test_no_new_history_after_soft_erasure(self):
        self.user.soft_delete()
        with self.assertRaises(ValueError):
            playground_service.begin_history(self.user, "shared-model", "cortecs")
        self.assertFalse(AgentPlaygroundHistory.objects.exists())

    def test_failed_refund_is_uncertain_on_initial_call_and_retry(self):
        from capp.classes.billing.reservation_service import BillingReservationService

        self.provider.side_effect = RuntimeError("Private question")
        operation_id = str(uuid.uuid4())
        with mock.patch.object(BillingReservationService, "refund", side_effect=RuntimeError("DB unavailable")):
            response = self.chat(client_operation_id=operation_id)
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data["error"]["status"], "refund_pending")
        self.assertFalse(response.data["error"]["refund_confirmed"])
        self.assertFalse(response.data["error"]["terminal"])
        self.assertNotIn("new operation", response.data["error"]["message"])
        self.assertEqual(AgentUsageLog.objects.get().status, AgentUsageLog.Status.REFUND_PENDING)
        reservation = BillingReservation.objects.get()
        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)
        retry = self.chat(client_operation_id=operation_id)
        self.assertEqual(retry.status_code, 503, retry.data)
        self.assertFalse(retry.data["error"]["refund_confirmed"])
        self.assertEqual(self.provider.call_count, 1)
        BillingReservationService.refund(reservation)
        reconciled = self.chat(client_operation_id=operation_id)
        self.assertEqual(reconciled.status_code, 502, reconciled.data)
        self.assertTrue(reconciled.data["error"]["refund_confirmed"])
        self.assertTrue(reconciled.data["error"]["terminal"])
        self.assertEqual(self.provider.call_count, 1)

    def test_confirmed_refund_has_terminal_metadata(self):
        self.provider.side_effect = RuntimeError("Private question")
        response = self.chat()
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.data["error"]["refund_confirmed"])
        self.assertTrue(response.data["error"]["terminal"])

    def test_legacy_paid_reservation_without_log_cannot_recharge(self):
        from capp.classes.billing.reservation_service import BillingReservationService

        operation_id = str(uuid.uuid4())
        reservation = BillingReservationService.reserve(
            user=self.user,
            feature="agent_gateway",
            amount_sparks=1,
            idempotency_key=operation_id,
        )
        BillingReservationService.settle(reservation)
        response = self.chat(client_operation_id=operation_id)
        self.assertEqual(response.status_code, 409, response.data)
        self.provider.assert_not_called()
        self.assertEqual(BillingReservation.objects.count(), 1)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 9999)

    def test_legacy_pending_holds_count_conservatively_against_budget(self):
        from capp.classes.billing.reservation_service import BillingReservationService

        self.key.monthly_spark_budget = 3
        self.key.save()
        BillingReservationService.reserve(
            user=self.user,
            feature="agent_gateway",
            amount_sparks=3,
            idempotency_key=str(uuid.uuid4()),
        )
        self.assertEqual(self.chat().status_code, 429)
        self.provider.assert_not_called()

    def test_legacy_failure_log_does_not_assert_refund_without_ledger_confirmation(self):
        from capp.classes.billing.reservation_service import BillingReservationService

        operation_id = str(uuid.uuid4())
        BillingReservationService.reserve(
            user=self.user,
            feature="agent_gateway",
            amount_sparks=1,
            idempotency_key=operation_id,
        )
        AgentUsageLog.objects.create(
            user=self.user,
            key=self.key,
            provider="cortecs",
            model="shared-model",
            client_operation_id=operation_id,
            status=AgentUsageLog.Status.UPSTREAM_ERROR,
            input_tokens=0,
            output_tokens=0,
            sparks_charged=0,
            cost_native=0,
        )
        response = self.chat(client_operation_id=operation_id)
        self.assertEqual(response.status_code, 503, response.data)
        self.assertFalse(response.data["error"]["refund_confirmed"])
        self.provider.assert_not_called()


@override_settings(
    AGENT_MARKUP_PERCENT=10,
    CORTECS_UWENZAI_AGENT_MARKUP_PERCENT=7.5,
    MISTRAL_UWENZAI_AGENT_MARKUP_PERCENT=5.0,
)
class PerProviderMarkupTests(TestCase):
    """Per-provider markup resolution with a global fallback (values are test fixtures)."""

    def test_defaults_and_fallback(self):
        self.assertEqual(get_markup_percent("cortecs"), 7.5)
        self.assertEqual(get_markup_percent("mistral"), 5.0)
        self.assertEqual(get_markup_percent("fictional"), get_markup_percent(None))
        self.assertEqual(get_markup_percent(None), float(settings.AGENT_MARKUP_PERCENT))

    def test_setting_overrides(self):
        with override_settings(MISTRAL_UWENZAI_AGENT_MARKUP_PERCENT=10.5):
            self.assertEqual(get_markup_percent("mistral"), 10.5)
            # Other providers are unaffected by one provider's override.
            self.assertEqual(get_markup_percent("cortecs"), 7.5)
        with override_settings(AGENT_MARKUP_PERCENT=15):
            self.assertEqual(get_markup_percent("fictional"), 15.0)
            # A provider without its setting falls back to the global default.
            with override_settings(CORTECS_UWENZAI_AGENT_MARKUP_PERCENT=None):
                self.assertEqual(get_markup_percent("cortecs"), 15.0)

    def test_unset_markup_means_no_markup(self):
        with override_settings(AGENT_MARKUP_PERCENT=0, CORTECS_UWENZAI_AGENT_MARKUP_PERCENT=None):
            self.assertEqual(get_markup_percent("cortecs"), 0.0)
            self.assertEqual(get_markup_percent(None), 0.0)


@override_settings(
    CORTECS_UWENZAI_AGENT_API_KEY="dummy",
    MISTRAL_UWENZAI_AGENT_API_KEY="dummy",
)
class CatalogSyncTests(TestCase):
    """Daily catalog sync: seeds AgentModelPrice and preserves admin kill-switches."""

    def setUp(self):
        reset_registry()
        cache.clear()
        self.addCleanup(reset_registry)
        self.addCleanup(cache.clear)
        # Fail the test on accidental real provider HTTP calls.
        patcher = mock.patch("httpx.get", side_effect=AssertionError("Real catalog HTTP is forbidden in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _cortecs_catalog():
        return [
            {
                "id": "sync-model-existing",
                "name": "sync-model-existing",
                "input_price_per_1m": Decimal("1.00"),
                "output_price_per_1m": Decimal("2.00"),
                "currency": "EUR",
                "max_context": 32768,
            },
            {
                "id": "sync-model-new",
                "name": "sync-model-new",
                "input_price_per_1m": Decimal("1.00"),
                "output_price_per_1m": Decimal("3.00"),
                "currency": "EUR",
            },
        ]

    @staticmethod
    def _mistral_catalog():
        return [
            {
                "id": "sync-mistral-model",
                "name": "sync-mistral-model",
                "input_price_per_1m": Decimal("0.10"),
                "output_price_per_1m": Decimal("0.30"),
            }
        ]

    def _patched_fetches(self, cortecs=None, mistral=None):
        return (
            mock.patch.object(CortecsAgentProvider, "_fetch_models_from_api", return_value=cortecs),
            mock.patch.object(OpenAICompatibleProvider, "_fetch_models_from_api", return_value=mistral),
        )

    def test_sync_seeds_catalog_and_preserves_admin_disables(self):
        disabled = AgentModelPrice.objects.create(
            provider="cortecs",
            model="sync-model-existing",
            currency="EUR",
            input_price_per_1m=Decimal("0.11"),
            output_price_per_1m=Decimal("0.33"),
            is_active=False,
        )
        patch_cortecs, patch_mistral = self._patched_fetches(
            cortecs=self._cortecs_catalog(), mistral=self._mistral_catalog()
        )
        with patch_cortecs, patch_mistral:
            summary = run_catalog_sync()
        self.assertEqual(summary["cortecs"], {"status": "ok", "models": 2})
        self.assertEqual(summary["mistral"], {"status": "ok", "models": 1})
        # Providers without a dedicated key are reported, never guessed.
        self.assertEqual(summary["fikra"]["status"], "not_configured")
        # Admin kill-switch survives the refresh, but the price is updated.
        disabled.refresh_from_db()
        self.assertFalse(disabled.is_active)
        self.assertEqual(disabled.input_price_per_1m, Decimal("1.00"))
        self.assertTrue(
            AgentModelPrice.objects.filter(provider="cortecs", model="sync-model-new", is_active=True).exists()
        )
        self.assertTrue(
            AgentModelPrice.objects.filter(provider="mistral", model="sync-mistral-model", is_active=True).exists()
        )

    def test_sync_fetch_failure_keeps_last_known_catalog(self):
        existing = AgentModelPrice.objects.create(
            provider="cortecs",
            model="sync-model-existing",
            currency="EUR",
            input_price_per_1m=Decimal("0.11"),
            output_price_per_1m=Decimal("0.33"),
        )
        patch_cortecs, _ = self._patched_fetches(cortecs=None, mistral=None)
        with patch_cortecs:
            summary = run_catalog_sync()
        self.assertEqual(summary["cortecs"]["status"], "error")
        existing.refresh_from_db()
        self.assertEqual(existing.input_price_per_1m, Decimal("0.11"))

    def test_sync_task_is_callable_inline(self):
        patch_cortecs, patch_mistral = self._patched_fetches(
            cortecs=self._cortecs_catalog(), mistral=self._mistral_catalog()
        )
        with patch_cortecs, patch_mistral:
            result = sync_agent_catalog()
        self.assertEqual(result["cortecs"]["models"], 2)

    def test_management_command_seeds_and_reports(self):
        out = StringIO()
        patch_cortecs, patch_mistral = self._patched_fetches(
            cortecs=self._cortecs_catalog(), mistral=self._mistral_catalog()
        )
        with patch_cortecs, patch_mistral:
            call_command("sync_agent_catalog", stdout=out)
        output = out.getvalue()
        self.assertIn("cortecs: ok (2 models)", output)
        self.assertIn("mistral: ok (1 models)", output)
        self.assertIn("fikra: not_configured", output)
        self.assertTrue(AgentModelPrice.objects.filter(provider="cortecs", model="sync-model-new").exists())

    def test_management_command_fails_only_when_nothing_refreshed(self):
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="sync-model-existing",
            currency="EUR",
            input_price_per_1m=Decimal("0.11"),
            output_price_per_1m=Decimal("0.33"),
        )
        out = StringIO()
        patch_cortecs, _ = self._patched_fetches(cortecs=None, mistral=None)
        with patch_cortecs, self.assertRaises(CommandError):
            call_command("sync_agent_catalog", stdout=out)
        self.assertIn("cortecs: error", out.getvalue())

    def test_beat_schedule_includes_daily_catalog_sync(self):
        entry = settings.CELERY_BEAT_SCHEDULE["sync-agent-catalog"]
        self.assertEqual(entry["task"], "capp.tasks.sync_agent_catalog")
        self.assertEqual(entry["options"]["queue"], "general")

    def _mistral_models_http(self, model_ids):
        """Mistral's real /v1/models shape: plain IDs, no pricing."""

        def fake_get(url, headers=None, timeout=None, **kwargs):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"data": [{"id": model_id} for model_id in model_ids]}

            return R()

        return mock.patch(
            "capp.classes.agent_gateway.providers.openai_base.httpx.get",
            side_effect=fake_get,
        )

    # Fixture prices only: the public source ships no price table.
    @override_settings(
        MISTRAL_UWENZAI_AGENT_MODEL_PRICES={
            "mistral-small-latest": ["1.10", "2.20"],
            "mistral-large-4": ["2.00", "4.00"],
        }
    )
    def test_sync_flags_new_unpriced_models_once(self):
        """A model released upstream without an operator price must not vanish
        silently: the first sync reports it as new, later syncs do not re-alert
        (retention window), and it never reaches the billable catalog."""
        patch_cortecs, _ = self._patched_fetches(cortecs=self._cortecs_catalog(), mistral=None)
        with patch_cortecs:
            with self._mistral_models_http(["mistral-small-latest", "mistral-brand-new-model"]):
                summary = run_catalog_sync()
        entry = summary["mistral"]
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["models"], 1)
        self.assertEqual(entry["skipped_models"], ["mistral-brand-new-model"])
        self.assertEqual(entry["new_skipped_models"], ["mistral-brand-new-model"])
        self.assertTrue(AgentModelPrice.objects.filter(provider="mistral", model="mistral-small-latest").exists())
        self.assertFalse(AgentModelPrice.objects.filter(provider="mistral", model="mistral-brand-new-model").exists())

        # Second sync: the model is still skipped (still unpriced upstream) but
        # no longer NEW — the operator alert fires exactly once.
        with patch_cortecs:
            with self._mistral_models_http(["mistral-small-latest", "mistral-brand-new-model"]):
                summary = run_catalog_sync()
        entry = summary["mistral"]
        self.assertEqual(entry["skipped_models"], ["mistral-brand-new-model"])
        self.assertNotIn("new_skipped_models", entry)

    def test_sync_task_warns_about_new_unpriced_models(self):
        """The daily task logs an explicit warning for new unpriced models."""
        patch_cortecs, _ = self._patched_fetches(cortecs=self._cortecs_catalog(), mistral=None)
        with patch_cortecs:
            with self._mistral_models_http(["mistral-brand-new-model"]):
                with self.assertLogs("capp.tasks", level="WARNING") as logs:
                    summary = sync_agent_catalog()
        self.assertTrue(any("mistral-brand-new-model" in line for line in logs.output))
        self.assertEqual(summary["mistral"]["new_skipped_models"], ["mistral-brand-new-model"])

    def test_management_command_lists_new_unpriced_models(self):
        out = StringIO()
        patch_cortecs, _ = self._patched_fetches(cortecs=self._cortecs_catalog(), mistral=None)
        with patch_cortecs:
            with self._mistral_models_http(["mistral-small-latest", "mistral-brand-new-model"]):
                call_command("sync_agent_catalog", stdout=out)
        output = out.getvalue()
        self.assertIn("mistral-brand-new-model", output)
        self.assertIn("without a price", output)

    def test_mistral_price_map_fills_unpriced_models(self):
        """Mistral's /models lists no prices; the settings map supplies them."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        # In the price map -> seeded from the map.
                        {"id": "mistral-small-latest"},
                        # Not in the map and unpriced upstream -> skipped.
                        {"id": "glm-5-2"},
                        # Priced upstream -> map not needed.
                        {
                            "id": "sync-mistral-priced",
                            "pricing": {"input_token": "0.10", "output_token": "0.30"},
                        },
                    ]
                }

        # Fixture prices only: the public source ships no price table.
        with override_settings(MISTRAL_UWENZAI_AGENT_MODEL_PRICES={"mistral-small-latest": ["1.10", "2.20"]}):
            provider = MistralAgentProvider()
            with mock.patch("httpx.get", return_value=FakeResponse()):
                count = provider.refresh_catalog()
        self.assertEqual(count, 2)
        rows = {p.model: p for p in AgentModelPrice.objects.filter(provider="mistral")}
        self.assertEqual(rows["mistral-small-latest"].input_price_per_1m, Decimal("1.10"))
        self.assertEqual(rows["mistral-small-latest"].output_price_per_1m, Decimal("2.20"))
        self.assertEqual(rows["mistral-small-latest"].currency, "USD")
        self.assertEqual(rows["sync-mistral-priced"].input_price_per_1m, Decimal("0.10"))
        self.assertNotIn("glm-5-2", rows)

    # Fixture prices only: the public source ships no price table.
    @override_settings(
        MISTRAL_UWENZAI_AGENT_MODEL_PRICES={
            "mistral-small-latest": ["1.10", "2.20"],
            "mistral-large-4": ["2.00", "4.00"],
        }
    )
    def test_mistral_capabilities_dict_and_max_context_length(self):
        """Mistral's /models reports capabilities as a boolean dict and context as max_context_length."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        {
                            "id": "mistral-small-latest",
                            "max_context_length": 96000,
                            "capabilities": {
                                "completion_chat": True,
                                "completion_fim": False,
                                "function_calling": True,
                                "fine_tuning": False,
                                "vision": True,
                                "classification": False,
                            },
                        },
                        # Every capability disabled -> no capability labels stored.
                        {"id": "mistral-large-4", "capabilities": {"completion_chat": False}},
                    ]
                }

        provider = MistralAgentProvider()
        with mock.patch("httpx.get", return_value=FakeResponse()):
            count = provider.refresh_catalog()
        self.assertEqual(count, 2)
        rows = {p.model: p for p in AgentModelPrice.objects.filter(provider="mistral")}
        self.assertEqual(
            rows["mistral-small-latest"].capabilities,
            ["completion_chat", "function_calling", "vision"],
        )
        self.assertEqual(rows["mistral-small-latest"].max_context, 96000)
        self.assertEqual(rows["mistral-large-4"].capabilities, [])

    def test_aki_price_map_and_max_model_len(self):
        """AKI's /models lists no prices and reports context as max_model_len."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        # In the price map; context length from max_model_len.
                        {"id": "mistral4-119b", "max_model_len": 262144},
                        # max_model_len of 0 means unspecified upstream.
                        {"id": "qwen3.8-27b", "max_model_len": 0},
                        # Not in the map and unpriced upstream -> skipped.
                        {"id": "unknown-aki-model"},
                    ]
                }

        # Fixture prices only: the public source ships no price table.
        aki_prices = {"mistral4-119b": ["1.20", "2.40"], "qwen3.8-27b": ["0.30", "0.60"]}
        with override_settings(AKI_UWENZAI_AGENT_API_KEY="dummy", AKI_UWENZAI_AGENT_MODEL_PRICES=aki_prices):
            provider = AkiAgentProvider()
            with mock.patch("httpx.get", return_value=FakeResponse()):
                count = provider.refresh_catalog()
        self.assertEqual(count, 2)
        rows = {p.model: p for p in AgentModelPrice.objects.filter(provider="aki")}
        self.assertEqual(rows["mistral4-119b"].input_price_per_1m, Decimal("1.20"))
        self.assertEqual(rows["mistral4-119b"].output_price_per_1m, Decimal("2.40"))
        self.assertEqual(rows["mistral4-119b"].currency, "EUR")
        self.assertEqual(rows["mistral4-119b"].max_context, 262144)
        self.assertIsNone(rows["qwen3.8-27b"].max_context)
        self.assertNotIn("unknown-aki-model", rows)

    def test_fikra_inline_pricing_and_price_map_fallback(self):
        """Fikra publishes pricing on /models (pricing.input/output per 1M);
        the settings map is the fallback for unpriced entries."""

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": [
                        # Priced upstream -> seeded from the live catalog.
                        {
                            "id": "fikra-pro-20b",
                            "pricing": {"unit": "USD_PER_1M_TOKENS", "input": 2.0, "output": 2.0},
                        },
                        # Not priced upstream -> falls back to the operator map.
                        {"id": "fikra-flash"},
                        # Unpriced upstream and absent from the map -> skipped.
                        {"id": "fikra-embedding-v1"},
                    ]
                }

        # Fixture prices only: the public source ships no price table.
        with override_settings(
            FIKRA_UWENZAI_AGENT_API_KEY="dummy",
            FIKRA_UWENZAI_AGENT_MODEL_PRICES={"fikra-flash": ["0.75", "0.75"]},
        ):
            provider = FikraAgentProvider()
            with mock.patch("httpx.get", return_value=FakeResponse()):
                count = provider.refresh_catalog()
        self.assertEqual(count, 2)
        rows = {p.model: p for p in AgentModelPrice.objects.filter(provider="fikra")}
        self.assertEqual(rows["fikra-pro-20b"].input_price_per_1m, Decimal("2.0"))
        self.assertEqual(rows["fikra-pro-20b"].output_price_per_1m, Decimal("2.0"))
        self.assertEqual(rows["fikra-flash"].input_price_per_1m, Decimal("0.75"))
        self.assertEqual(rows["fikra-flash"].currency, "USD")
        self.assertNotIn("fikra-embedding-v1", rows)

    def test_sync_reports_ok_zero_models_when_fetch_succeeds_but_nothing_priced(self):
        """A successful fetch that yields zero priced models is not a fetch
        failure: report ok with 0 models instead of the misleading
        'live model fetch failed'."""
        patch_cortecs, patch_mistral = self._patched_fetches(cortecs=self._cortecs_catalog(), mistral=[])
        with patch_cortecs, patch_mistral:
            summary = run_catalog_sync()
        self.assertEqual(summary["mistral"], {"status": "ok", "models": 0})


# ============================================================================
# Key creation: optional monthly Spark budget (M2 dead-control fix)
# ============================================================================


class KeyMonthlyBudgetTests(TestCase):
    """The per-key monthly Spark budget must be settable at creation."""

    def setUp(self):
        cache.clear()
        self.user = _make_user_with_sparks(10000)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        ComplianceConfirmationLog.objects.create(
            user=self.user,
            accepted=True,
            confirmation_type=ComplianceConfirmationLog.ConfirmationType.AGENT_GATEWAY,
            version=AGENT_GATEWAY_CONSENT_VERSION,
        )

    def _create(self, **extra):
        return self.client.post(BASE + "keys/", {"label": "Budget key", **extra}, format="json")

    def test_create_key_with_monthly_budget(self):
        response = self._create(monthly_spark_budget=500)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["monthly_spark_budget"], 500)
        key = AgentAPIKey.objects.get(pk=response.data["data"]["key_id"])
        self.assertEqual(key.monthly_spark_budget, 500)

    def test_budget_appears_in_key_listing(self):
        key_id = self._create(monthly_spark_budget=777).data["data"]["key_id"]
        listing = self.client.get(BASE + "keys/").data["data"]
        entry = next(k for k in listing["keys"] if k["id"] == key_id)
        self.assertEqual(entry["monthly_spark_budget"], 777)

    def test_create_key_without_budget_is_unlimited(self):
        response = self._create()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["data"]["monthly_spark_budget"])
        key = AgentAPIKey.objects.get(pk=response.data["data"]["key_id"])
        self.assertIsNone(key.monthly_spark_budget)

    def test_budget_validation_rejects_bad_values(self):
        for bad in (0, -5, "abc", "12.5", True, 1.5):
            with self.subTest(bad=bad):
                before = AgentAPIKey.objects.count()
                response = self._create(monthly_spark_budget=bad)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(AgentAPIKey.objects.count(), before)

    @override_settings(AGENT_KEY_MAX_MONTHLY_BUDGET=1000)
    def test_budget_rejected_above_server_cap(self):
        response = self._create(monthly_spark_budget=1001)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AgentAPIKey.objects.exists())
        # The cap itself is accepted:
        self.assertEqual(self._create(monthly_spark_budget=1000).status_code, 200)

    def _patch_budget(self, key_id, payload):
        return self.client.patch(BASE + f"keys/{key_id}/budget/", payload, format="json")

    def test_update_budget_via_patch(self):
        key_id = self._create(monthly_spark_budget=500).data["data"]["key_id"]
        response = self._patch_budget(key_id, {"monthly_spark_budget": 250})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["monthly_spark_budget"], 250)
        self.assertEqual(AgentAPIKey.objects.get(pk=key_id).monthly_spark_budget, 250)

    def test_clear_budget_with_null(self):
        key_id = self._create(monthly_spark_budget=500).data["data"]["key_id"]
        response = self._patch_budget(key_id, {"monthly_spark_budget": None})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["data"]["monthly_spark_budget"])
        self.assertIsNone(AgentAPIKey.objects.get(pk=key_id).monthly_spark_budget)

    def test_patch_budget_requires_the_field(self):
        key_id = self._create().data["data"]["key_id"]
        response = self._patch_budget(key_id, {})
        self.assertEqual(response.status_code, 400)

    def test_patch_budget_rejects_bad_values(self):
        key_id = self._create().data["data"]["key_id"]
        for bad in (0, -5, "abc", "12.5", True, 1.5):
            with self.subTest(bad=bad):
                response = self._patch_budget(key_id, {"monthly_spark_budget": bad})
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(AgentAPIKey.objects.get(pk=key_id).monthly_spark_budget)

    def test_patch_budget_rejects_revoked_key(self):
        key_id = self._create().data["data"]["key_id"]
        self.assertTrue(AgentKeyService.revoke_key(self.user, key_id))
        response = self._patch_budget(key_id, {"monthly_spark_budget": 100})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(AgentAPIKey.objects.get(pk=key_id).monthly_spark_budget)

    def test_patch_budget_requires_ownership(self):
        intruder = _make_user_with_sparks(1000, username="intruder", email="intruder@example.com")
        key_id = self._create().data["data"]["key_id"]
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(intruder)}")
        response = other_client.patch(BASE + f"keys/{key_id}/budget/", {"monthly_spark_budget": 100}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(AgentAPIKey.objects.get(pk=key_id).monthly_spark_budget)


class AgentKeyRetentionTests(TestCase):
    """Revoked keys get a revocation timestamp and expire from the key list."""

    def setUp(self):
        cache.clear()
        self.user = _make_user_with_sparks(10000)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        ComplianceConfirmationLog.objects.create(
            user=self.user,
            accepted=True,
            confirmation_type=ComplianceConfirmationLog.ConfirmationType.AGENT_GATEWAY,
            version=AGENT_GATEWAY_CONSENT_VERSION,
        )
        self.full_key, self.key = AgentKeyService.create_key(self.user, "Retention")

    def test_revoke_records_timestamp_and_repeats_keep_it(self):
        self.assertTrue(AgentKeyService.revoke_key(self.user, str(self.key.pk)))
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertIsNotNone(self.key.revoked_at)
        # A repeated revoke after the window must not extend the key's life.
        stale = timezone.now() - timedelta(days=90)
        AgentAPIKey.objects.filter(pk=self.key.pk).update(revoked_at=stale)
        self.assertTrue(AgentKeyService.revoke_key(self.user, str(self.key.pk)))
        self.key.refresh_from_db()
        self.assertLess(abs((self.key.revoked_at - stale).total_seconds()), 1)

    def test_cleanup_deletes_only_stale_revoked_keys(self):
        _, fresh_revoked = AgentKeyService.create_key(self.user, "Fresh revoked")
        self.assertTrue(AgentKeyService.revoke_key(self.user, str(fresh_revoked.pk)))
        _, stale_revoked = AgentKeyService.create_key(self.user, "Stale revoked")
        self.assertTrue(AgentKeyService.revoke_key(self.user, str(stale_revoked.pk)))
        AgentAPIKey.objects.filter(pk=stale_revoked.pk).update(revoked_at=timezone.now() - timedelta(days=40))
        _, legacy_revoked = AgentKeyService.create_key(self.user, "Legacy revoked")
        self.assertTrue(AgentKeyService.revoke_key(self.user, str(legacy_revoked.pk)))
        AgentAPIKey.objects.filter(pk=legacy_revoked.pk).update(revoked_at=None)

        deleted = cleanup_revoked_agent_keys()
        self.assertEqual(deleted, 1)
        remaining = set(AgentAPIKey.objects.filter(user=self.user).values_list("pk", flat=True))
        self.assertIn(self.key.pk, remaining)  # active: never touched
        self.assertIn(fresh_revoked.pk, remaining)
        self.assertIn(legacy_revoked.pk, remaining)  # unknown date: never deleted
        self.assertNotIn(stale_revoked.pk, remaining)

    def test_usage_logs_survive_key_deletion(self):
        AgentUsageLog.objects.create(
            user=self.user,
            key=self.key,
            key_hint=self.key.key_hint,
            provider="cortecs",
            model="shared-model",
            input_tokens=11,
            output_tokens=7,
            sparks_charged=3,
            cost_native=0,
        )
        AgentAPIKey.objects.filter(pk=self.key.pk).update(
            is_active=False, revoked_at=timezone.now() - timedelta(days=40)
        )
        deleted = cleanup_revoked_agent_keys()
        self.assertEqual(deleted, 1)

        log = AgentUsageLog.objects.get()
        self.assertIsNone(log.key)
        self.assertEqual(log.key_hint, self.key.key_hint)
        self.assertEqual(log.user_id, self.user.user_id)
        # Per-key usage reporting still shows the removed key (hint only).
        rows = AgentUsageService.get_usage_by_key(str(self.user.user_id))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["removed"])
        self.assertEqual(rows[0]["hint"], self.key.display_hint)
        self.assertFalse(rows[0]["is_active"])

    @override_settings(AGENT_KEY_REVOKED_RETENTION_DAYS=0)
    def test_retention_can_be_disabled(self):
        _, revoked = AgentKeyService.create_key(self.user, "Disabled retention")
        self.assertTrue(AgentKeyService.revoke_key(self.user, str(revoked.pk)))
        AgentAPIKey.objects.filter(pk=revoked.pk).update(revoked_at=timezone.now() - timedelta(days=365))
        self.assertEqual(cleanup_revoked_agent_keys(), 0)
        self.assertTrue(AgentAPIKey.objects.filter(pk=revoked.pk).exists())

    def test_soft_delete_stamps_revoked_at_for_retention(self):
        self.user.soft_delete()
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertIsNotNone(self.key.revoked_at)
        # The retention task can now purge it after the window.
        AgentAPIKey.objects.filter(pk=self.key.pk).update(revoked_at=timezone.now() - timedelta(days=40))
        self.assertEqual(cleanup_revoked_agent_keys(), 1)

    def test_account_erasure_stamps_revoked_at_for_retention(self):
        from capp.classes.auth.account_erasure import erase_private_content

        erase_private_content(self.user)
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)
        self.assertIsNotNone(self.key.revoked_at)
        AgentAPIKey.objects.filter(pk=self.key.pk).update(revoked_at=timezone.now() - timedelta(days=40))
        self.assertEqual(cleanup_revoked_agent_keys(), 1)

    def _make_already_revoked_key(self, days_ago):
        _, old_key = AgentKeyService.create_key(self.user, "Already revoked")
        revoked_at = timezone.now() - timedelta(days=days_ago)
        AgentAPIKey.objects.filter(pk=old_key.pk).update(is_active=False, revoked_at=revoked_at)
        return old_key, revoked_at

    def test_soft_delete_keeps_original_revoked_at_of_revoked_keys(self):
        old_key, revoked_at = self._make_already_revoked_key(days_ago=29)
        self.user.soft_delete()
        old_key.refresh_from_db()
        self.key.refresh_from_db()
        # Already-revoked key keeps its timestamp, so its retention clock does not restart.
        self.assertEqual(old_key.revoked_at, revoked_at)
        # The still-active key is stamped now.
        self.assertFalse(self.key.is_active)
        self.assertGreater(self.key.revoked_at, revoked_at)

    def test_account_erasure_keeps_original_revoked_at_of_revoked_keys(self):
        from capp.classes.auth.account_erasure import erase_private_content

        old_key, revoked_at = self._make_already_revoked_key(days_ago=35)
        erase_private_content(self.user)
        old_key.refresh_from_db()
        self.key.refresh_from_db()
        self.assertEqual(old_key.revoked_at, revoked_at)
        self.assertFalse(self.key.is_active)
        self.assertGreater(self.key.revoked_at, revoked_at)
        # Only the key past the window is purged; the freshly stamped one stays.
        self.assertEqual(cleanup_revoked_agent_keys(), 1)
        self.assertFalse(AgentAPIKey.objects.filter(pk=old_key.pk).exists())
        self.assertTrue(AgentAPIKey.objects.filter(pk=self.key.pk).exists())


class GatewayConsentVersionCoordinationTests(SimpleTestCase):
    """The backend consent gate and the frontend publication must stay in sync.

    The developer workspace POSTs the frontend ``LEGAL_POLICY_VERSION`` as the
    consent version and the backend compares it against
    ``AGENT_GATEWAY_CONSENT_VERSION``. When the two diverge, every confirmation
    is rejected with 409 ``consent_version_mismatch`` and keys and the
    playground stay locked for every user (seen in production on 2026-10-03
    after a legal-document version bump). This test fails the build instead.

    Both constants live in this monorepo, so the check is a plain file read.
    """

    FRONTEND_LEGAL_INDEX = Path(__file__).resolve().parents[2] / "Frontend" / "src" / "i18n" / "legal" / "index.js"

    def test_backend_consent_version_equals_frontend_policy_version(self):
        path = self.FRONTEND_LEGAL_INDEX
        if not path.exists():  # backend-only checkout
            self.skipTest(f"frontend legal index not found at {path}")

        match = re.search(r'LEGAL_POLICY_VERSION\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "LEGAL_POLICY_VERSION export not found in frontend legal index")
        self.assertEqual(
            AGENT_GATEWAY_CONSENT_VERSION,
            match.group(1),
            "AGENT_GATEWAY_CONSENT_VERSION must equal the frontend LEGAL_POLICY_VERSION: "
            "bump both together when legal documents change, or every gateway consent "
            "confirmation will be rejected with 409 consent_version_mismatch.",
        )
