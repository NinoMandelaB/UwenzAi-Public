"""Account export regression coverage; local DB/encryption, no provider traffic."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from capp.classes.agent_gateway import playground_service
from capp.classes.agent_gateway.export_service import export_gateway_data
from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.auth.auth import Auth
from capp.classes.byok.byok_service import BYOKService
from capp.models import (
    AgentAPIKey,
    AgentGatewayOperation,
    AgentPlaygroundHistory,
    AgentUsageLog,
    ComplianceConfirmationLog,
    Conversation,
    MemoryFact,
    Message,
    UserBYOKConfig,
)
from capp.tests import _make_character, _make_user
from capp.utils.encryption import EncryptionService

EXPORT_URL = "/api/v1/auth/export-data/"


class _FixtureTokenizer:
    """Deterministic byte tokens for short history fixtures; no downloaded assets."""

    def encode(self, text):
        return list(text.encode("utf-8"))


class AccountExportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.other = _make_user(username="other", email="other@example.com")
        self.full_key, self.key = AgentKeyService.create_key(self.user, "My developer key")
        self.other_full_key, self.other_key = AgentKeyService.create_key(self.other, "Other private key")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        for target in ("httpx.get", "httpx.post", "requests.sessions.Session.request"):
            patcher = mock.patch(target, side_effect=AssertionError("External HTTP is forbidden in export tests"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def history(self, user=None, provider="cortecs", model="shared-model"):
        user = user or self.user
        messages = [{"role": "user", "content": f"Private question — {user.username}"}]
        ticket = playground_service.begin_history(user, model, provider)
        # Test real history persistence/encryption, not BPE vocabulary loading.
        # A cold tiktoken cache otherwise downloads assets through requests and
        # correctly trips our HTTP guard. Scope this double to fixture creation
        # so export code and the other gateway tests remain unmocked.
        with mock.patch("capp.classes.ai.token_utils.get_tokenizer_for_model", return_value=_FixtureTokenizer()):
            self.assertTrue(
                playground_service.save_history(
                    ticket,
                    user,
                    model,
                    provider,
                    messages,
                    {"choices": [{"message": {"content": f"Private answer — {user.username}"}}]},
                )
            )
        return AgentPlaygroundHistory.objects.get(pk=ticket[0])

    def usage(self, user=None, key=None, **overrides):
        return AgentUsageLog.objects.create(
            **{
                "user": user or self.user,
                "key": key or self.key,
                "key_hint": (key or self.key).key_hint,
                "provider": "cortecs",
                "model": "shared-model",
                "input_tokens": 123,
                "output_tokens": 45,
                "cost_native": Decimal("0.012345"),
                "cost_currency": "EUR",
                "fx_rate": Decimal("145.1234"),
                "sparks_charged": 3,
                "status": AgentUsageLog.Status.SUCCESS,
                "latency_ms": 987,
                "client_operation_id": "untrusted-operation-text-must-not-export",
                **overrides,
            }
        )

    def execution(self, user=None, key=None, **overrides):
        return AgentGatewayOperation.objects.create(
            **{
                "user": user or self.user,
                "key": key or self.key,
                "provider": "cortecs",
                "model": "shared-model",
                "client_operation_id": "untrusted-operation-text-must-not-export",
                **overrides,
            }
        )

    def gateway(self):
        result = Auth.export_user_data(self.user)
        self.assertEqual(result["status"], "success")
        return result["data"]["developer_gateway"]

    def assert_no_store(self, response):
        for directive in ("no-store", "private", "no-cache", "max-age=0"):
            self.assertIn(directive, response["Cache-Control"])
        self.assertIn("Expires", response)

    def assert_history_unavailable(self, data, row):
        self.assertFalse(data["history_export_complete"])
        exported = next(item for item in data["playground_history"] if item["id"] == str(row.pk))
        self.assertEqual(exported["content_status"], "unavailable")
        self.assertIsNone(exported["messages"])
        self.assertEqual(exported["provider"], row.provider)
        self.assertEqual(exported["model"], row.model)
        self.assertEqual(exported["created_at"], row.created_at.isoformat())
        self.assertEqual(exported["updated_at"], row.updated_at.isoformat())
        self.assertEqual(len(data["warnings"]), 1)
        self.assertEqual(data["warnings"][0]["code"], "gateway_history_unavailable")
        self.assertEqual(data["warnings"][0]["history_id"], str(row.pk))
        self.assertIn("incomplete", data["warnings"][0]["message"])
        self.assertNotIn(row.messages_encrypted, json.dumps(data))

    def test_history_includes_decrypted_roles_pair_and_row_timestamps(self):
        row = self.history()
        exported = self.gateway()["playground_history"]
        self.assertEqual(
            exported,
            [
                {
                    "id": str(row.pk),
                    "provider": "cortecs",
                    "model": "shared-model",
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                    "content_status": "saved",
                    "messages": [
                        {"role": "user", "content": "Private question — alice"},
                        {"role": "assistant", "content": "Private answer — alice"},
                    ],
                }
            ],
        )
        self.assertNotIn("Private question", row.messages_encrypted)
        self.assertNotIn("timestamp", exported[0]["messages"][0])  # Not stored per message.

    def test_owner_isolation_for_every_gateway_collection_and_same_pair(self):
        own_history = self.history()
        foreign_history = self.history(self.other)
        own_usage = self.usage()
        foreign_usage = self.usage(self.other, self.other_key)
        own_execution = self.execution()
        foreign_execution = self.execution(self.other, self.other_key)
        data = self.gateway()
        self.assertEqual([row["agent_api_key_id"] for row in data["api_keys"]], [str(self.key.pk)])
        self.assertEqual([row["id"] for row in data["playground_history"]], [str(own_history.pk)])
        self.assertEqual([row["usage_log_id"] for row in data["usage_logs"]], [str(own_usage.pk)])
        self.assertEqual([row["id"] for row in data["execution_records"]], [str(own_execution.pk)])
        serialized = json.dumps(data)
        for foreign in (self.other_key, foreign_history, foreign_usage, foreign_execution):
            self.assertNotIn(str(foreign.pk), serialized)
        self.assertNotIn("Private question — other", serialized)
        self.assertNotIn("Other private key", serialized)

    def test_key_allowlist_includes_persisted_budget_expiry_and_revocation(self):
        now = timezone.now()
        AgentAPIKey.objects.filter(pk=self.key.pk).update(
            monthly_spark_budget=4321, expires_at=now - timedelta(days=1), last_used_at=now, is_active=False
        )
        self.key.refresh_from_db()
        self.assertEqual(
            self.gateway()["api_keys"],
            [
                {
                    "agent_api_key_id": str(self.key.pk),
                    "label": "My developer key",
                    "key_hint": self.key.key_hint,
                    "is_active": False,
                    "monthly_spark_budget": 4321,
                    "expires_at": self.key.expires_at.isoformat(),
                    "last_used_at": now.isoformat(),
                    "created_at": self.key.created_at.isoformat(),
                    "updated_at": self.key.updated_at.isoformat(),
                }
            ],
        )

    def test_no_credentials_hashes_ciphertext_or_internal_fences(self):
        history = self.history()
        self.usage()
        self.execution()
        with override_settings(CORTECS_UWENZAI_AGENT_API_KEY="provider-credential-never-export"):
            serialized = json.dumps(self.gateway())
        for forbidden in (
            self.full_key,
            self.other_full_key,
            self.key.key_hash,
            self.other_key.key_hash,
            history.messages_encrypted,
            str(history.revision),
            "provider-credential-never-export",
            "untrusted-operation-text-must-not-export",
            "client_operation_id",
            "messages_encrypted",
            "key_hash",
            "full_key",
            "revision",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_usage_cost_precision_tokens_and_safe_execution_allowlist(self):
        usage = self.usage()
        execution = self.execution()
        self.assertEqual(
            self.gateway()["usage_logs"],
            [
                {
                    "usage_log_id": str(usage.pk),
                    "key_id": str(self.key.pk),
                    "key_hint": self.key.key_hint,
                    "provider": "cortecs",
                    "model": "shared-model",
                    "input_tokens": 123,
                    "output_tokens": 45,
                    "cost_native": "0.012345",
                    "cost_currency": "EUR",
                    "fx_rate": "145.1234",
                    "sparks_charged": 3,
                    "status": "success",
                    "latency_ms": 987,
                    "created_at": usage.created_at.isoformat(),
                    "updated_at": usage.updated_at.isoformat(),
                }
            ],
        )
        self.assertEqual(
            self.gateway()["execution_records"],
            [
                {
                    "id": str(execution.pk),
                    "key_id": str(self.key.pk),
                    "provider": "cortecs",
                    "model": "shared-model",
                    "created_at": execution.created_at.isoformat(),
                    "updated_at": execution.updated_at.isoformat(),
                }
            ],
        )

    def test_all_retained_usage_not_only_recent_or_successful(self):
        for index in range(15):
            usage = self.usage(
                client_operation_id=f"operation-{index}",
                provider="mistral" if index % 2 else "cortecs",
                status="refund_pending" if index % 2 else "upstream_error",
                fx_rate=None,
                latency_ms=None,
            )
            AgentUsageLog.objects.filter(pk=usage.pk).update(created_at=timezone.now() - timedelta(days=400))
        data = self.gateway()["usage_logs"]
        self.assertEqual(len(data), 15)
        self.assertEqual({row["provider"] for row in data}, {"cortecs", "mistral"})
        self.assertEqual({row["status"] for row in data}, {"refund_pending", "upstream_error"})
        self.assertTrue(all(row["fx_rate"] is None and row["latency_ms"] is None for row in data))

    def test_deleted_key_usage_is_retained(self):
        self.usage()
        key_hint = self.key.key_hint
        self.key.delete()
        data = self.gateway()
        self.assertEqual(data["api_keys"], [])
        self.assertEqual(len(data["usage_logs"]), 1)
        self.assertIsNone(data["usage_logs"][0]["key_id"])
        self.assertEqual(data["usage_logs"][0]["key_hint"], key_hint)

    def test_revoked_expired_key_usage_and_claims_are_retained(self):
        usage = self.usage()
        execution = self.execution()
        AgentAPIKey.objects.filter(pk=self.key.pk).update(
            is_active=False, expires_at=timezone.now() - timedelta(days=1)
        )
        data = self.gateway()
        self.assertEqual(data["usage_logs"][0]["usage_log_id"], str(usage.pk))
        self.assertEqual(data["usage_logs"][0]["key_id"], str(self.key.pk))
        self.assertEqual(data["execution_records"][0]["id"], str(execution.pk))
        self.assertFalse(data["api_keys"][0]["is_active"])

    def test_inconsistent_key_ownership_never_leaks_foreign_key_metadata(self):
        self.usage(key=self.other_key)
        self.execution(key=self.other_key)
        data = self.gateway()
        self.assertIsNone(data["usage_logs"][0]["key_id"])
        self.assertIsNone(data["usage_logs"][0]["key_hint"])
        self.assertIsNone(data["execution_records"][0]["key_id"])

    def test_both_provider_pairs_preserved_without_invented_ui_configuration(self):
        self.history(provider="cortecs")
        self.history(provider="mistral")
        data = self.gateway()
        self.assertEqual(
            {(row["model"], row["provider"]) for row in data["playground_history"]},
            {("shared-model", "cortecs"), ("shared-model", "mistral")},
        )
        self.assertEqual(
            set(data),
            {
                "api_keys",
                "playground_history",
                "usage_logs",
                "execution_records",
                "history_export_complete",
                "warnings",
            },
        )
        for field in ("selected_model", "selected_provider", "selected_key", "max_tokens", "save_history"):
            self.assertNotIn(field, json.dumps(data))

    def test_unsaved_ticket_is_distinct_from_saved_empty_history(self):
        ticket = playground_service.begin_history(self.user, "shared-model", "cortecs")
        row = AgentPlaygroundHistory.objects.get(pk=ticket[0])
        exported = self.gateway()["playground_history"][0]
        self.assertEqual(exported["content_status"], "not_saved")
        self.assertIsNone(exported["messages"])
        payload = {"user": str(self.user.pk), "provider": row.provider, "model": row.model, "messages": []}
        row.messages_encrypted = EncryptionService.encrypt_value(json.dumps(payload))
        row.save()
        exported = self.gateway()["playground_history"][0]
        self.assertEqual(exported["content_status"], "saved")
        self.assertEqual(exported["messages"], [])

    def test_corruption_returns_explicit_warning_without_content_or_logs(self):
        row = self.history()
        row.messages_encrypted = "corrupted-ciphertext-marker"
        row.save()
        with self.assertNoLogs("capp", level="DEBUG"):
            self.assert_history_unavailable(self.gateway(), row)

    def test_wrong_owner_model_or_provider_binding_fails_closed(self):
        row = self.history()
        for changed in ({"user": str(self.other.pk)}, {"model": "other-model"}, {"provider": "mistral"}):
            payload = {
                "user": str(self.user.pk),
                "provider": row.provider,
                "model": row.model,
                "messages": [{"role": "user", "content": "Do not return me"}],
                **changed,
            }
            row.messages_encrypted = EncryptionService.encrypt_value(json.dumps(payload))
            row.save()
            with self.subTest(changed=changed), self.assertNoLogs("capp", level="DEBUG"):
                self.assert_history_unavailable(self.gateway(), row)

    def test_invalid_json_utf8_and_message_schema_fail_without_logging_plaintext(self):
        row = self.history()
        valid = {"user": str(self.user.pk), "provider": row.provider, "model": row.model}
        malformed = [
            b"plaintext-marker-not-json",
            b"plaintext-marker\xff",
            b"[]",
            json.dumps(valid).encode(),
        ]
        for messages in (
            None,
            {},
            ["plaintext-marker"],
            [{"role": "system", "content": "plaintext-marker"}],
            [{"role": "user", "content": {"unexpected": "plaintext-marker"}}],
            [{"role": "user", "content": "plaintext-marker", "api_key": "secret-marker"}],
        ):
            malformed.append(json.dumps({**valid, "messages": messages}).encode())
        key = settings.FIELD_ENCRYPTION_KEY
        fernet = Fernet(key.encode() if isinstance(key, str) else key)
        for plaintext in malformed:
            row.messages_encrypted = fernet.encrypt(plaintext).decode()
            row.save()
            with self.subTest(plaintext=plaintext), self.assertNoLogs("capp", level="DEBUG"):
                self.assert_history_unavailable(self.gateway(), row)

    def test_missing_wrong_or_invalid_encryption_key_fails_explicitly(self):
        row = self.history()
        # Keep legacy profile decryption out of this gateway-specific key test.
        self.user.email_encrypted = None
        for key in (None, "invalid-key", Fernet.generate_key().decode()):
            with self.subTest(key=key), override_settings(FIELD_ENCRYPTION_KEY=key):
                with self.assertNoLogs("capp", level="DEBUG"):
                    self.assert_history_unavailable(self.gateway(), row)

    def test_corrupt_foreign_history_does_not_block_export(self):
        AgentPlaygroundHistory.objects.create(
            user=self.other, model="shared-model", provider="cortecs", messages_encrypted="invalid"
        )
        self.assertEqual(self.gateway()["playground_history"], [])

    def test_export_empty_gateway_does_not_create_records(self):
        self.key.delete()
        self.assertEqual(
            self.gateway(),
            {
                "api_keys": [],
                "playground_history": [],
                "usage_logs": [],
                "execution_records": [],
                "history_export_complete": True,
                "warnings": [],
            },
        )
        self.assertFalse(AgentAPIKey.objects.filter(user=self.user).exists())
        self.assertFalse(AgentPlaygroundHistory.objects.filter(user=self.user).exists())

    def test_gateway_export_reads_four_scoped_queries_without_per_row_queries(self):
        self.history()
        self.usage()
        self.execution()
        with self.assertNumQueries(4):
            export_gateway_data(self.user)

    def test_existing_export_fields_and_gateway_consent_are_unchanged(self):
        self.user.last_login = timezone.now()
        self.user.save(update_fields=["last_login"])
        consent = ComplianceConfirmationLog.objects.create(
            user=self.user,
            confirmation_type="agent_gateway",
            accepted=True,
            version="1.0",
            ip_address="192.0.2.1",
            user_agent="Export test client",
        )
        ComplianceConfirmationLog.objects.create(
            user=self.other, confirmation_type="agent_gateway", accepted=True, version="other-consent"
        )
        fact = MemoryFact.objects.create(user=self.user, fact_text="I like tea")
        MemoryFact.objects.create(user=self.user, fact_text="Inactive fact", is_active=False)
        MemoryFact.objects.create(user=self.other, fact_text="Foreign fact")
        character = _make_character()
        conversation = Conversation.objects.create(user=self.user, character=character)
        message = Message.objects.create(
            conversation=conversation, sender_type="user", content="My original chat", language_code="en"
        )
        Message.objects.create(conversation=conversation, sender_type="ai", content="Not exported legacy AI")
        Message.objects.create(conversation=conversation, sender_type="system", content="Not exported system")
        foreign = Conversation.objects.create(user=self.other, character=character)
        Message.objects.create(conversation=foreign, sender_type="user", content="Foreign chat")
        byok, _ = UserBYOKConfig.create_or_update(
            self.user,
            "https://example.invalid/v1",
            "byok-private-key-1234",
            "existing-byok-model",
            system_prompt="Existing custom prompt",
            store_on_platform=True,
        )
        result = Auth.export_user_data(self.user)
        self.assertEqual(set(result), {"status", "data"})
        self.assertEqual(result["status"], "success")
        data = result["data"]
        self.assertEqual(
            set(data),
            {
                "exported_at",
                "personal_info",
                "compliance_consents",
                "memory_facts",
                "user_messages",
                "activity_logs",
                "byok_config",
                "developer_gateway",
            },
        )
        personal = {
            field: getattr(self.user, field)
            for field in (
                "username",
                "full_name",
                "display_name",
                "preferred_language",
                "auth_provider",
                "account_status",
                "is_verified",
                "age_verified",
            )
        }
        personal.update(
            email="alice@example.com",
            phone_number=None,
            age_verified_at=None,
            created_at=self.user.created_at.isoformat(),
            updated_at=self.user.updated_at.isoformat(),
        )
        self.assertEqual(data["personal_info"], personal)
        self.assertEqual(
            data["compliance_consents"],
            [
                {
                    "confirmation_type": "agent_gateway",
                    "accepted": True,
                    "version": "1.0",
                    "accepted_at": consent.accepted_at.isoformat(),
                    "ip_address": "192.0.2.1",
                    "user_agent": "Export test client",
                }
            ],
        )
        self.assertEqual(
            data["memory_facts"],
            [
                {
                    "fact_text": "I like tea",
                    "fact_type": fact.fact_type,
                    "confidence_score": 0.5,
                    "importance_score": 5,
                    "extracted_at": fact.extracted_at.isoformat(),
                    "is_active": True,
                }
            ],
        )
        self.assertEqual(
            data["user_messages"],
            [
                {
                    "timestamp": message.created_at.isoformat(),
                    "content": "My original chat",
                    "language_code": "en",
                }
            ],
        )
        self.assertEqual(
            data["activity_logs"],
            [
                {
                    "type": "login",
                    "timestamp": self.user.last_login.isoformat(),
                    "details": {"note": "last successful login (IP/user agent not stored)"},
                }
            ],
        )
        self.assertEqual(
            data["byok_config"],
            {
                "api_url": byok.api_url,
                "model_name": byok.model_name,
                "api_key_hint": BYOKService._key_hint("byok-private-key-1234"),
                "system_prompt": byok.system_prompt,
                "store_on_platform": True,
                "is_active": True,
                "created_at": byok.created_at.isoformat(),
                "note": "Full API key is not included in exports for security reasons. "
                "The hint shows the last 4 characters only.",
            },
        )
        self.assertNotIn("byok-private-key-1234", json.dumps(data))
        self.assertNotIn(byok.api_key_encrypted, json.dumps(data))

    def test_authenticated_download_filename_json_cache_and_request_owner(self):
        self.history()
        self.history(self.other)
        response = self.client.get(EXPORT_URL, {"user_id": str(self.other.pk), "provider": "mistral"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="uwenzai_data.json"')
        self.assertEqual(response.json()["personal_info"]["username"], "alice")
        self.assertNotIn("Private question — other", response.content.decode())
        self.assert_no_store(response)

    def test_anonymous_invalid_jwt_and_gateway_key_rejected_without_caching(self):
        for token in (None, "not-a-jwt", self.full_key):
            client = APIClient()
            if token:
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            with self.subTest(token=token):
                response = client.get(EXPORT_URL)
                self.assertEqual(response.status_code, 401)
                self.assert_no_store(response)
                self.assertNotIn("developer_gateway", response.content.decode())

    def test_ineligible_account_cannot_export(self):
        for changes in (
            {"is_active": False},
            {"is_deleted": True},
            {"account_status": "banned"},
            {"account_status": "suspended"},
        ):
            type(self.user).objects.filter(pk=self.user.pk).update(
                is_active=True, is_deleted=False, account_status="active"
            )
            type(self.user).objects.filter(pk=self.user.pk).update(**changes)
            with self.subTest(changes=changes):
                response = self.client.get(EXPORT_URL)
                self.assertEqual(response.status_code, 401)
                self.assert_no_store(response)

    def test_corruption_download_preserves_readable_history_and_warns_about_incomplete_content(self):
        valid = self.history()
        row = self.history(provider="mistral")
        row.messages_encrypted = "corrupt"
        row.save()
        response = self.client.get(EXPORT_URL)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["personal_info"]["username"], "alice")
        self.assert_history_unavailable(data["developer_gateway"], row)
        readable = next(item for item in data["developer_gateway"]["playground_history"] if item["id"] == str(valid.pk))
        self.assertEqual(readable["content_status"], "saved")
        self.assertEqual(readable["messages"][0]["content"], "Private question — alice")
        self.assertIn("Content-Disposition", response)
        self.assert_no_store(response)

    def test_error_and_method_not_allowed_responses_are_not_cached(self):
        with mock.patch.object(Auth, "export_user_data", side_effect=RuntimeError("sensitive-error-marker")):
            with self.assertLogs("capp.views.auth_views", level="ERROR") as logs:
                response = self.client.get(EXPORT_URL)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive-error-marker", str(logs.output))
        self.assertNotIn("sensitive-error-marker", response.content.decode())
        self.assert_no_store(response)
        response = self.client.post(EXPORT_URL, {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assert_no_store(response)

    def test_existing_service_failure_response_contract_and_cache_headers_preserved(self):
        with mock.patch.object(Auth, "export_user_data", return_value={"status": "fail", "data": "server_error"}):
            response = self.client.get(EXPORT_URL)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"status": "fail", "data": "server_error"})
        self.assert_no_store(response)

    def test_second_download_reflects_deleted_history_not_cached_content(self):
        self.history()
        first = self.client.get(EXPORT_URL)
        self.assertEqual(len(first.json()["developer_gateway"]["playground_history"]), 1)
        playground_service.delete_history(self.user, "shared-model", "cortecs")
        second = self.client.get(EXPORT_URL)
        self.assertEqual(second.json()["developer_gateway"]["playground_history"], [])
        self.assert_no_store(second)
