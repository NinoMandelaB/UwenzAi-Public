# capp/tests_agent_gateway.py
"""
Comprehensive test suite for the UwenzAi Agent Gateway feature.

Tests cover:
1. Key lifecycle (create, auth, revoke, expire)
2. Key storage security (hash only, never plaintext)
3. Auth boundary (Bearer token vs session auth)
4. Endpoint parity (OpenAI-compatible shapes)
5. Pricing math (12.5% markup, EUR->KES->spark conversion)
6. Billing (reservation, settle, refund)
7. Idempotency
8. Provider layer
9. Rate limiting
10. Migrations
11. Failed-auth limiter (per-address, pre-DB)
12. ReservationError idempotency-claim cleanup
"""

import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from capp.classes.agent_gateway.auth import generate_api_key, hash_api_key
from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.agent_gateway.pricing_service import AgentPricingService
from capp.classes.agent_gateway.proxy_service import AgentProxyService
from capp.classes.agent_gateway.providers.registry import (
    get_provider,
    get_all_models,
    PROVIDERS,
    reset_registry,
)
from capp.classes.billing.reservation_service import (
    BillingReservationService,
    IdempotencyKeyConflict,
    InsufficientBalance,
    ReservationError,
)
from capp.models import (
    AgentAPIKey,
    AgentGatewayOperation,
    AgentModelPrice,
    AgentUsageLog,
    BillingReservation,
    SparkPool,
)
from capp.tests import _make_user

# ============================================================================
# Helpers
# ============================================================================


def _make_user_with_sparks(sparks=1000, **user_kwargs):
    """Create a user with a SparkPool containing the specified number of sparks."""
    user = _make_user(**user_kwargs)
    pool, _ = SparkPool.objects.get_or_create(user=user, defaults={"sparks": 0})
    pool.sparks = sparks
    pool.save(update_fields=["sparks"])
    return user


def _create_api_key(user, label="Test Key", **kwargs):
    """Helper to create an AgentAPIKey."""
    return AgentAPIKey.objects.create(
        user=user,
        label=label,
        key_hash=hash_api_key("test-key-1234"),
        key_hint="1234",
        is_active=True,
        **kwargs,
    )


# ============================================================================
# 1. Key Lifecycle Tests
# ============================================================================


class AgentAPIKeyTests(TestCase):
    """Test AgentAPIKey model and lifecycle operations."""

    def setUp(self):
        self.user = _make_user()

    def test_create_key_with_plaintext_never_stores_it(self):
        """Verify that plaintext key is NEVER stored in the database."""
        full_key, key_record = AgentKeyService.create_key(
            user=self.user,
            label="Test Key",
        )

        # The full key should be returned
        self.assertTrue(full_key.startswith("uwenzai-dev-"))
        self.assertEqual(len(full_key), 44)  # "uwenzai-dev-" + 32 random chars

        # Refresh from DB
        key_record.refresh_from_db()

        # The DB should only have hash and hint, never plaintext
        self.assertNotEqual(key_record.key_hash, full_key)
        self.assertEqual(key_record.key_hint, full_key[-4:])
        self.assertEqual(len(key_record.key_hash), 64)  # SHA-256 hex

    def test_create_key_generates_unique_hash(self):
        """Two keys with different plaintext should have different hashes."""
        full_key1, _ = AgentKeyService.create_key(self.user, label="Key 1")
        full_key2, _ = AgentKeyService.create_key(self.user, label="Key 2")

        self.assertNotEqual(full_key1, full_key2)

        # The plaintext key never contains the record ID; look records up by hash
        key1 = AgentAPIKey.objects.get(key_hash=hash_api_key(full_key1))
        key2 = AgentAPIKey.objects.get(key_hash=hash_api_key(full_key2))

        self.assertNotEqual(key1.key_hash, key2.key_hash)

    def test_list_keys_for_user(self):
        """Test listing keys for a user."""
        AgentKeyService.create_key(self.user, label="Key 1")
        AgentKeyService.create_key(self.user, label="Key 2")

        keys = AgentKeyService.list_keys(self.user)

        self.assertEqual(len(keys), 2)
        labels = [k["label"] for k in keys]
        self.assertIn("Key 1", labels)
        self.assertIn("Key 2", labels)

        # Verify each key has required fields
        for key in keys:
            self.assertIn("id", key)
            self.assertIn("label", key)
            self.assertIn("hint", key)
            self.assertIn("is_active", key)
            self.assertIn("created_at", key)

    def test_revoke_key_soft_deletes(self):
        """Revoked keys are soft-deleted (is_active=False, still in DB)."""
        _, key_record = AgentKeyService.create_key(self.user, label="Key to Revoke")
        key_id = str(key_record.agent_api_key_id)

        success = AgentKeyService.revoke_key(self.user, key_id)
        self.assertTrue(success)

        key_record.refresh_from_db()
        self.assertFalse(key_record.is_active)

        # Key should still exist in DB
        self.assertTrue(AgentAPIKey.objects.filter(agent_api_key_id=key_id).exists())

    def test_revoke_nonexistent_key_fails(self):
        """Revoking a non-existent key returns False."""
        success = AgentKeyService.revoke_key(self.user, str(uuid.uuid4()))
        self.assertFalse(success)

    def test_expired_key_cannot_be_used(self):
        """Expired keys cannot be used."""
        key = _create_api_key(self.user, label="Expired Key", expires_at=timezone.now() - timedelta(days=1))

        self.assertFalse(key.can_be_used())
        self.assertTrue(key.is_expired())

    def test_active_key_can_be_used(self):
        """Active, non-expired keys can be used."""
        key = _create_api_key(self.user, label="Active Key")

        self.assertTrue(key.can_be_used())
        self.assertFalse(key.is_expired())


# ============================================================================
# 2. Authentication Tests
# ============================================================================


class AgentGatewayAuthenticationTests(TestCase):
    """Test authentication for Agent Gateway."""

    def setUp(self):
        self.user = _make_user()
        self.full_key, self.key_record = AgentKeyService.create_key(self.user, label="Auth Test Key")
        self.key_hash = self.key_record.key_hash

    def test_generated_key_format(self):
        """Generated keys have the correct format: uwenzai-{prefix}-{32_random_chars}"""
        key = generate_api_key("dev")
        self.assertTrue(key.startswith("uwenzai-dev-"))
        # "uwenzai-dev-" = 12 chars + 32 random = 44 total
        self.assertEqual(len(key), 44)

    def test_generated_key_format_live(self):
        """Generated keys with 'live' prefix have the correct format."""
        key = generate_api_key("live")
        self.assertTrue(key.startswith("uwenzai-live-"))
        # "uwenzai-live-" = 13 chars + 32 random = 45 total
        self.assertEqual(len(key), 45)

    def test_hash_api_key_returns_sha256_hex(self):
        """hash_api_key returns a SHA-256 hex digest."""
        key_hash = hash_api_key("test-key-1234")
        expected_hash = hashlib.sha256("test-key-1234".encode()).hexdigest()
        self.assertEqual(key_hash, expected_hash)
        self.assertEqual(len(key_hash), 64)


# ============================================================================
# 3. Key Storage Security Tests
# ============================================================================


class AgentKeyStorageSecurityTests(TestCase):
    """Test that key storage is secure (hash only, never plaintext)."""

    def setUp(self):
        self.user = _make_user()

    def test_db_never_stores_plaintext(self):
        """The database NEVER stores plaintext keys."""
        full_key, key_record = AgentKeyService.create_key(self.user, label="Security Test")

        # Query the database directly
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT key_hash, key_hint FROM agent_api_keys WHERE agent_api_key_id = %s",
                [key_record.agent_api_key_id],
            )
            row = cursor.fetchone()
            db_hash = row[0]
            db_hint = row[1]

        # The hash should NOT be the plaintext key
        self.assertNotEqual(db_hash, full_key)
        # The hint should be the last 4 chars
        self.assertEqual(db_hint, full_key[-4:])
        # The hash should be SHA-256 of the full key
        expected_hash = hashlib.sha256(full_key.encode()).hexdigest()
        self.assertEqual(db_hash, expected_hash)

    def test_key_hash_unique_constraint(self):
        """Key hashes must be unique (DB-level constraint)."""
        AgentKeyService.create_key(self.user, label="Key 1")

        # Inserting a second record with an identical hash must fail
        AgentAPIKey.objects.create(
            user=self.user,
            label="First",
            key_hash=hash_api_key("collision-test-key"),
            key_hint="t-ky",
            is_active=True,
        )
        with self.assertRaises(Exception):
            AgentAPIKey.objects.create(
                user=self.user,
                label="Second",
                key_hash=hash_api_key("collision-test-key"),
                key_hint="t-ky",
                is_active=True,
            )


# ============================================================================
# 4. Pricing Service Tests
# ============================================================================


class AgentPricingServiceTests(TestCase):
    """Test pricing calculations."""

    def setUp(self):
        # FX rates are cached; clear so each test resolves them deterministically
        cache.clear()

    def test_calculate_cost_deepseek_v4_flash(self):
        """Test pricing for deepseek-v4-flash (0.22/0.66 per 1M in EUR)."""
        with override_settings(AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5):
            # deepseek-v4-flash: input=0.22, output=0.66 EUR per 1M tokens
            cost_info = AgentPricingService.calculate_cost(
                provider="cortecs",
                model="deepseek-v4-flash",
                input_tokens=1_000_000,
                output_tokens=0,
            )

            # Input cost: 1M * 0.22 / 1M = 0.22 EUR (native, pre-markup)
            self.assertAlmostEqual(float(cost_info["cost_native"]), 0.22, places=6)
            self.assertEqual(cost_info["cost_currency"], "EUR")

            # With 12.5% markup: 0.22 * 1.125 = 0.2475
            self.assertAlmostEqual(float(cost_info["marked_up_cost"]), 0.22 * 1.125, places=6)

            # Test with both input and output
            cost_info = AgentPricingService.calculate_cost(
                provider="cortecs",
                model="deepseek-v4-flash",
                input_tokens=1_000,
                output_tokens=100,
            )

            # Input: 1000 * 0.22 / 1_000_000 = 0.00022
            # Output: 100 * 0.66 / 1_000_000 = 0.000066
            # Total: 0.000286
            expected_eur = 0.000286
            self.assertAlmostEqual(float(cost_info["cost_native"]), expected_eur, places=6)

    def test_calculate_cost_kimi_k3(self):
        """Test pricing for kimi-k3 (3.00/15.00 per 1M in EUR)."""
        with override_settings(AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5):
            cost_info = AgentPricingService.calculate_cost(
                provider="cortecs",
                model="kimi-k3",
                input_tokens=1_000,
                output_tokens=100,
            )

            # Input: 1000 * 3.00 / 1_000_000 = 0.003
            # Output: 100 * 15.00 / 1_000_000 = 0.0015
            # Total: 0.0045
            expected_eur = 0.0045
            self.assertAlmostEqual(float(cost_info["cost_native"]), expected_eur, places=6)

    def test_calculate_cost_glm_5(self):
        """Test pricing for glm-5 (1.00/3.20 per 1M in EUR)."""
        with override_settings(AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5):
            cost_info = AgentPricingService.calculate_cost(
                provider="cortecs",
                model="glm-5",
                input_tokens=10_000,
                output_tokens=5_000,
            )

            # Input: 10000 * 1.00 / 1_000_000 = 0.01
            # Output: 5000 * 3.20 / 1_000_000 = 0.016
            # Total: 0.026
            expected_eur = 0.026
            self.assertAlmostEqual(float(cost_info["cost_native"]), expected_eur, places=6)

    def test_markup_is_12_5_percent(self):
        """Verify 12.5% markup is applied correctly."""
        # Override settings for this test
        with override_settings(AGENT_MARKUP_PERCENT=12.5, AGENT_FX_EUR_KES=145):
            cost_info = AgentPricingService.calculate_cost(
                provider="cortecs",
                model="deepseek-v4-flash",
                input_tokens=1_000_000,
                output_tokens=0,
            )

            # 0.22 * 1.125 = 0.2475
            self.assertAlmostEqual(
                float(cost_info["marked_up_cost"]),
                0.22 * 1.125,
                places=6,
            )
            self.assertEqual(cost_info["markup_percent"], 12.5)

    def test_spark_conversion_uses_constant(self):
        """Verify spark conversion uses SPARK_BASE_VALUE_KES."""
        # SPARK_BASE_VALUE_KES = 0.1 (1 spark = 0.1 KES)
        # So sparks = ceil(charge_kes / 0.1)

        with override_settings(AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5):
            cost_info = AgentPricingService.calculate_cost(
                provider="cortecs",
                model="deepseek-v4-flash",
                input_tokens=1_000_000,
                output_tokens=0,
            )

            # cost_eur = 0.22
            # marked_up = 0.22 * 1.125 = 0.2475
            # charge_kes = 0.2475 * 145 = 35.8875
            # sparks = ceil(35.8875 / 0.1) = ceil(358.875) = 359

            self.assertEqual(cost_info["sparks"], 359)


# ============================================================================
# 5. Provider Tests
# ============================================================================


class AgentProviderTests(TestCase):
    """Test provider registry and providers, always without live HTTP."""

    def setUp(self):
        reset_registry()
        cache.clear()
        self.addCleanup(reset_registry)
        patcher = mock.patch(
            "capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider._fetch_models_from_api",
            return_value=None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy")
    def test_cortecs_provider_in_registry(self):
        """Cortecs provider should be in the registry."""
        self.assertIn("cortecs", PROVIDERS)

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy")
    def test_get_provider_for_cortecs_model(self):
        """get_provider should return Cortecs provider for its models."""
        # Model-to-provider resolution is catalog-driven (AgentModelPrice)
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="deepseek-v4-flash",
            currency="EUR",
            input_price_per_1m=Decimal("0.22"),
            output_price_per_1m=Decimal("0.66"),
            is_active=True,
        )
        reset_registry()
        try:
            provider = get_provider(model="deepseek-v4-flash")
            self.assertIsNotNone(provider)
            self.assertEqual(provider.name, "cortecs")
        finally:
            reset_registry()

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy")
    def test_get_all_models_returns_models(self):
        """get_all_models should return available models."""
        models = get_all_models()
        self.assertGreater(len(models), 0)

        # Each model should have id
        for m in models:
            self.assertTrue(hasattr(m, "id"))

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": ""})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="")
    def test_provider_not_configured_returns_none(self):
        """When provider key is not configured, get_provider returns None for that provider's models."""
        # Without the API key, Cortecs provider should not be configured
        provider = get_provider(model="deepseek-v4-flash")
        if provider:
            self.assertFalse(provider.is_configured())


# ============================================================================
# 6. Billing Integration Tests
# ============================================================================


class AgentGatewayBillingTests(TestCase):
    """Test billing integration with reservation service."""

    def setUp(self):
        # The registry caches provider instances per process; a stale instance
        # from another test class (with an empty API key) would leak in here
        reset_registry()
        cache.clear()
        # Pin the test model in the catalog so provider resolution never
        # depends on the live Cortecs /models endpoint (offline-deterministic)
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="deepseek-v4-flash",
            currency="EUR",
            input_price_per_1m=Decimal("0.22"),
            output_price_per_1m=Decimal("0.66"),
            is_active=True,
        )
        self.user = _make_user_with_sparks(sparks=1000)
        self.full_key, self.key_record = AgentKeyService.create_key(self.user, label="Billing Test Key")

    def tearDown(self):
        reset_registry()

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5)
    @mock.patch("capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion")
    def test_execute_request_reserves_sparks(self, mock_chat_completion):
        """Test that execute_request reserves sparks."""
        # Mock the provider to return a successful response
        from capp.classes.agent_gateway.providers.base import ProviderResult

        mock_chat_completion.return_value = ProviderResult(
            text="test",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            status="success",
            model="deepseek-v4-flash",
        )

        messages = [{"role": "user", "content": "Hello"}]

        AgentProxyService.execute_request(
            key=self.key_record,
            model="deepseek-v4-flash",
            messages=messages,
            params={"max_tokens": 256},
        )

        # Check that a reservation was created
        reservations = BillingReservation.objects.filter(user=self.user, feature="agent_gateway")
        self.assertTrue(reservations.exists())

        reservation = reservations.first()
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5)
    @mock.patch("capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion")
    def test_execute_request_refunds_on_error(self, mock_chat_completion):
        """Test that sparks are refunded when provider fails."""
        # Mock the provider to raise an error
        mock_chat_completion.side_effect = Exception("Provider error")

        messages = [{"role": "user", "content": "Hello"}]

        result = AgentProxyService.execute_request(
            key=self.key_record,
            model="deepseek-v4-flash",
            messages=messages,
            params={"max_tokens": 256},
        )

        # Should have an error
        self.assertIn("error", result)
        self.assertEqual(result["error"]["status"], "upstream_error")

        # Check that reservation was refunded
        reservations = BillingReservation.objects.filter(user=self.user, feature="agent_gateway")
        self.assertTrue(reservations.exists())

        reservation = reservations.first()
        self.assertEqual(reservation.status, BillingReservation.Status.REFUNDED)

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5)
    def test_insufficient_sparks_returns_error(self):
        """Test that insufficient sparks returns proper error."""
        # Create a user with no sparks (distinct identity from setUp's user)
        broke_user = _make_user_with_sparks(sparks=0, username="broke", email="broke@example.com")
        _, broke_key = AgentKeyService.create_key(broke_user, label="Broke Key")

        messages = [{"role": "user", "content": "Hello"}]

        result = AgentProxyService.execute_request(
            key=broke_key,
            model="deepseek-v4-flash",
            messages=messages,
            params={"max_tokens": 256},
        )

        self.assertIn("error", result)
        self.assertEqual(result["error"]["status"], "insufficient_sparks")

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5)
    @mock.patch("capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion")
    def test_usage_log_created_on_success(self, mock_chat_completion):
        """Test that usage log is created on successful request."""
        from capp.classes.agent_gateway.providers.base import ProviderResult

        mock_chat_completion.return_value = ProviderResult(
            text="test",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            status="success",
            model="deepseek-v4-flash",
        )

        messages = [{"role": "user", "content": "Hello"}]

        AgentProxyService.execute_request(
            key=self.key_record,
            model="deepseek-v4-flash",
            messages=messages,
            params={"max_tokens": 256},
        )

        # Check usage log was created
        logs = AgentUsageLog.objects.filter(user=self.user)
        self.assertTrue(logs.exists())

        log = logs.first()
        self.assertEqual(log.provider, "cortecs")
        self.assertEqual(log.model, "deepseek-v4-flash")
        self.assertEqual(log.input_tokens, 10)
        self.assertEqual(log.output_tokens, 20)
        self.assertEqual(log.status, AgentUsageLog.Status.SUCCESS)


# ============================================================================
# 7. Idempotency Tests
# ============================================================================


class AgentGatewayIdempotencyTests(TestCase):
    """Test idempotency handling."""

    def setUp(self):
        reset_registry()
        cache.clear()
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="deepseek-v4-flash",
            currency="EUR",
            input_price_per_1m=Decimal("0.22"),
            output_price_per_1m=Decimal("0.66"),
            is_active=True,
        )
        self.user = _make_user_with_sparks(sparks=1000)
        self.full_key, self.key_record = AgentKeyService.create_key(self.user, label="Idempotency Test Key")

    def tearDown(self):
        reset_registry()

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5)
    @mock.patch("capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion")
    def test_identical_request_detected(self, mock_chat_completion):
        """Test that identical requests are detected and not double-billed."""
        from capp.classes.agent_gateway.providers.base import ProviderResult

        mock_chat_completion.return_value = ProviderResult(
            text="test",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            status="success",
            model="deepseek-v4-flash",
        )

        messages = [{"role": "user", "content": "Hello"}]
        client_op_id = str(uuid.uuid4())

        # First request
        result1 = AgentProxyService.execute_request(
            key=self.key_record,
            model="deepseek-v4-flash",
            messages=messages,
            params={"max_tokens": 256},
            client_operation_id=client_op_id,
        )

        # Second identical request with same operation_id
        result2 = AgentProxyService.execute_request(
            key=self.key_record,
            model="deepseek-v4-flash",
            messages=messages,
            params={"max_tokens": 256},
            client_operation_id=client_op_id,
        )

        # A retry replays the original outcome instead of failing: no error,
        # no re-charge, and clearly flagged as an idempotent retry.
        self.assertNotIn("error", result2)
        self.assertTrue(result2["_uwenzai"]["idempotent_retry"])
        self.assertEqual(result2["_uwenzai"]["sparks_charged"], 0)
        self.assertEqual(
            result2["_uwenzai"]["original_sparks_charged"],
            result1["_uwenzai"]["sparks_charged"],
        )

        # The provider must only have been called once
        self.assertEqual(mock_chat_completion.call_count, 1)

        # Only one reservation should exist
        reservations = BillingReservation.objects.filter(
            user=self.user, feature="agent_gateway", idempotency_key__startswith=f"agent:{self.key_record.pk}:"
        )
        self.assertEqual(reservations.count(), 1)

        # Only one usage log (no log row for the replay)
        self.assertEqual(AgentUsageLog.objects.filter(user=self.user).count(), 1)


# ============================================================================
# 8. Throttling Tests
# ============================================================================


class AgentGatewayThrottleTests(TestCase):
    """Test rate limiting for Agent Gateway."""

    def setUp(self):
        self.user = _make_user_with_sparks(sparks=1000)
        self.full_key, self.key_record = AgentKeyService.create_key(self.user, label="Throttle Test Key")
        # Clear cache before each test
        cache.clear()

    @mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
    @override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy")
    def test_throttle_uses_api_key(self):
        """Test that throttle uses API key for identification."""
        from capp.throttles import AgentGatewayKeyThrottle
        from django.test import RequestFactory

        throttle = AgentGatewayKeyThrottle()
        factory = RequestFactory()

        # Create a mock request with Authorization header
        request = factory.get("/api/v1/agent/models")
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {self.full_key}"

        # get_request_identifier hashes the full Bearer key
        ident = throttle.get_request_identifier(request)
        self.assertEqual(ident, hashlib.sha256(self.full_key.encode()).hexdigest())

        # The cache key builds on the (already hashed) identifier, and must
        # never contain the plaintext key
        cache_key = throttle.get_cache_key(request, None)
        self.assertIsNotNone(cache_key)
        self.assertIn(hashlib.sha256(ident.lower().encode()).hexdigest(), cache_key)
        self.assertNotIn(self.full_key, cache_key)


# ============================================================================
# 9. Model Price Tests
# ============================================================================


class AgentModelPriceTests(TestCase):
    """Test AgentModelPrice model."""

    def test_create_model_price(self):
        """Test creating a model price."""
        price = AgentModelPrice.objects.create(
            provider="cortecs",
            model="test-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.50"),
            output_price_per_1m=Decimal("2.50"),
            is_active=True,
        )

        self.assertEqual(price.provider, "cortecs")
        self.assertEqual(price.model, "test-model")
        self.assertEqual(float(price.input_price_per_1m), 1.50)
        self.assertEqual(float(price.output_price_per_1m), 2.50)

    def test_unique_constraint(self):
        """Test that (provider, model) is unique."""
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="unique-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.00"),
            output_price_per_1m=Decimal("2.00"),
        )

        with self.assertRaises(Exception):
            AgentModelPrice.objects.create(
                provider="cortecs",
                model="unique-model",
                currency="EUR",
                input_price_per_1m=Decimal("3.00"),
                output_price_per_1m=Decimal("4.00"),
            )

    def test_inactive_models_hidden_from_list(self):
        """Inactive catalog models are hidden from the priced model list."""
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="active-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.00"),
            output_price_per_1m=Decimal("2.00"),
            is_active=True,
        )
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="inactive-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.00"),
            output_price_per_1m=Decimal("2.00"),
            is_active=False,
        )

        # The admin kill-switch (is_active=False) hides catalog rows from the
        # priced model listing used for display/billing.
        models = AgentPricingService.list_models_with_prices()
        model_ids = [m["model"] for m in models]

        self.assertIn("active-model", model_ids)
        self.assertNotIn("inactive-model", model_ids)


# ============================================================================
# 10. Pricing Catalog Endpoint Tests (session auth — UwenzAi-Dev page)
# ============================================================================


class AgentPricingEndpointTests(TestCase):
    """Test GET /api/v1/agent/pricing — the models/pricelist for the Dev page."""

    def setUp(self):
        cache.clear()
        self.user = _make_user_with_sparks()
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="deepseek-v4-flash",
            currency="EUR",
            input_price_per_1m=Decimal("0.22"),
            output_price_per_1m=Decimal("0.66"),
            is_active=True,
        )
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="hidden-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.00"),
            output_price_per_1m=Decimal("2.00"),
            is_active=False,
        )

    def tearDown(self):
        cache.clear()

    def test_pricing_requires_authentication(self):
        """Anonymous requests must not see the pricing catalog."""
        response = self.client.get("/api/v1/agent/pricing/")
        self.assertEqual(response.status_code, 401)

    @override_settings(AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5)
    def test_pricing_returns_models_with_spark_prices(self):
        """The catalog lists active models with native prices and spark costs."""
        self.client.force_login(self.user)
        response = self.client.get("/api/v1/agent/pricing/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")

        models = response.data["data"]["models"]
        model_ids = [m["model"] for m in models]
        self.assertIn("deepseek-v4-flash", model_ids)
        self.assertNotIn("hidden-model", model_ids)  # admin kill-switch honoured

        deepseek = next(m for m in models if m["model"] == "deepseek-v4-flash")
        self.assertEqual(deepseek["provider"], "cortecs")
        self.assertEqual(deepseek["currency"], "EUR")
        self.assertEqual(deepseek["input_price_per_1m"], "0.22")
        self.assertEqual(deepseek["output_price_per_1m"], "0.66")
        # 1M input tokens: 0.22 EUR * 1.125 markup * 145 KES/EUR = 35.8875 KES
        # -> ceil(358.875 sparks) = 359
        self.assertEqual(deepseek["sparks_per_1m_input"], 359)
        # 1M output tokens: 0.66 EUR * 1.125 * 145 = 107.6625 KES
        # -> ceil(1076.625 sparks) = 1077
        self.assertEqual(deepseek["sparks_per_1m_output"], 1077)

        data = response.data["data"]
        self.assertEqual(data["markup_percent"], 12.5)
        self.assertIn("spark_value_kes", data)


# ============================================================================
# 11. Migration Tests
# ============================================================================


class AgentGatewayMigrationTests(TestCase):
    """Test that migrations are correctly applied."""

    def test_agent_models_exist(self):
        """Test that Agent Gateway models exist after migration."""
        # These should be created by the migration
        user = _make_user()

        # Create an API key
        key = AgentAPIKey.objects.create(
            user=user,
            label="Migration Test",
            key_hash=hash_api_key("test-key"),
            key_hint="test",
            is_active=True,
        )

        self.assertIsNotNone(key.agent_api_key_id)
        self.assertTrue(key.can_be_used())

    def test_agent_usage_log_exist(self):
        """Test that AgentUsageLog model works."""
        user = _make_user()
        key = _create_api_key(user)

        log = AgentUsageLog.objects.create(
            user=user,
            key=key,
            key_hint="test",
            provider="cortecs",
            model="test-model",
            input_tokens=10,
            output_tokens=20,
            cost_native=Decimal("0.001"),
            cost_currency="EUR",
            fx_rate=Decimal("145.0000"),
            sparks_charged=10,
            status=AgentUsageLog.Status.SUCCESS,
            latency_ms=100,
            client_operation_id=str(uuid.uuid4()),
        )

        self.assertIsNotNone(log.usage_log_id)
        self.assertEqual(log.provider, "cortecs")

    def test_agent_model_price_exist(self):
        """Test that AgentModelPrice model works."""
        price = AgentModelPrice.objects.create(
            provider="cortecs",
            model="test-model",
            currency="EUR",
            input_price_per_1m=Decimal("1.00"),
            output_price_per_1m=Decimal("2.00"),
            is_active=True,
        )

        self.assertIsNotNone(price.id)
        self.assertEqual(price.provider, "cortecs")


# ============================================================================
# 11. Failed-auth limiter tests
# ============================================================================


class AgentAuthFailureLimitTests(TestCase):
    """Cache-backed limiter caps failed key lookups per client address.

    DRF runs view throttles only after authentication, so the limiter lives
    in AgentGatewayAuthentication and runs before the DB lookup.
    """

    def setUp(self):
        cache.clear()
        self.user = _make_user_with_sparks(sparks=1000)
        self.full_key, self.key_record = AgentKeyService.create_key(self.user, label="Auth Limit Key")
        self.client = APIClient()

    def _bad_auth_get(self, remote_addr):
        return self.client.get(
            "/api/v1/agent/models",
            HTTP_AUTHORIZATION="Bearer uwenzai-dev-invalid-key",
            REMOTE_ADDR=remote_addr,
        )

    def test_unknown_key_under_limit_returns_401(self):
        with override_settings(AGENT_AUTH_FAILURE_LIMIT=3):
            response = self._bad_auth_get("203.0.113.10")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["status"], "invalid_api_key")

    def test_over_limit_returns_429(self):
        with override_settings(AGENT_AUTH_FAILURE_LIMIT=3):
            for _ in range(3):
                self.assertEqual(self._bad_auth_get("203.0.113.11").status_code, 401)
            response = self._bad_auth_get("203.0.113.11")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["status"], "rate_limited")

    def test_limit_is_per_address(self):
        with override_settings(AGENT_AUTH_FAILURE_LIMIT=2):
            for _ in range(2):
                self.assertEqual(self._bad_auth_get("203.0.113.12").status_code, 401)
            # Blocked address:
            self.assertEqual(self._bad_auth_get("203.0.113.12").status_code, 429)
            # A different address is unaffected:
            self.assertEqual(self._bad_auth_get("203.0.113.13").status_code, 401)

    def test_valid_key_from_fresh_address_still_works(self):
        with override_settings(AGENT_AUTH_FAILURE_LIMIT=2):
            for _ in range(2):
                self._bad_auth_get("203.0.113.14")
            with mock.patch.object(AgentProxyService, "execute_request") as execute:
                execute.return_value = {"id": "x", "object": "chat.completion"}
                response = self.client.post(
                    "/api/v1/agent/chat/completions",
                    {
                        "model": "deepseek-v4-flash",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "max_tokens": 8,
                    },
                    format="json",
                    HTTP_AUTHORIZATION=f"Bearer {self.full_key}",
                    REMOTE_ADDR="203.0.113.15",
                )
        self.assertEqual(response.status_code, 200, response.data)
        execute.assert_called_once()

    def test_disabled_limit_never_throttles(self):
        with override_settings(AGENT_AUTH_FAILURE_LIMIT=0):
            for _ in range(25):
                self.assertEqual(self._bad_auth_get("203.0.113.16").status_code, 401)


# ============================================================================
# 12. ReservationError claim-cleanup tests
# ============================================================================


class AgentReservationErrorCleanupTests(TestCase):
    """A ReservationError must not orphan the client operation ID."""

    def setUp(self):
        # The registry caches provider instances per process; a stale instance
        # from another test class would leak in here.
        reset_registry()
        cache.clear()
        env = mock.patch.dict("os.environ", {"CORTECS_UWENZAI_AGENT_API_KEY": "dummy"})
        env.start()
        self.addCleanup(env.stop)
        self.settings_override = override_settings(
            CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_MARKUP_PERCENT=12.5
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="deepseek-v4-flash",
            currency="EUR",
            input_price_per_1m=Decimal("0.22"),
            output_price_per_1m=Decimal("0.66"),
            is_active=True,
        )
        self.user = _make_user_with_sparks(sparks=10000)
        self.full_key, self.key_record = AgentKeyService.create_key(self.user, label="Cleanup Test Key")

    def tearDown(self):
        reset_registry()

    def _execute(self, operation_id):
        return AgentProxyService.execute_request(
            key=self.key_record,
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "Hello"}],
            params={"max_tokens": 64},
            client_operation_id=operation_id,
        )

    def test_reservation_error_releases_operation_id_for_retry(self):
        with mock.patch.object(
            BillingReservationService,
            "reserve",
            side_effect=ReservationError("simulated reservation failure"),
        ):
            result = self._execute("op-cleanup-1")

        self.assertEqual(result["error"]["status"], "reservation_error")
        # No hold, no debit, and crucially no orphaned claim: the same
        # operation ID can be retried per the documented contract.
        self.assertFalse(AgentGatewayOperation.objects.filter(client_operation_id="op-cleanup-1").exists())
        self.assertFalse(BillingReservation.objects.filter(feature="agent_gateway").exists())

        with mock.patch.object(
            BillingReservationService,
            "reserve",
            side_effect=InsufficientBalance("not enough sparks"),
        ):
            retry = self._execute("op-cleanup-1")
        # Not idempotency_conflict: the claim was released, not poisoned.
        self.assertEqual(retry["error"]["status"], "insufficient_sparks")

    def test_conflict_error_keeps_claim_when_reservation_exists(self):
        # A real reservation for the namespaced billing id (created moments
        # earlier by the same operation) must keep the claim so it can be
        # reconciled, even though reserve() raised a ReservationError subclass.
        billing_id = "agent:{}:{}".format(self.key_record.pk, hashlib.sha256(b"op-conflict-1").hexdigest())
        BillingReservationService.reserve(
            idempotency_key=billing_id,
            feature="agent_gateway",
            amount_sparks=10,
            user=self.user,
            operation_reference=billing_id,
        )

        with mock.patch.object(
            BillingReservationService,
            "reserve",
            side_effect=IdempotencyKeyConflict("already reserved"),
        ):
            result = self._execute("op-conflict-1")

        self.assertEqual(result["error"]["status"], "reservation_error")
        self.assertTrue(
            AgentGatewayOperation.objects.filter(client_operation_id="op-conflict-1").exists(),
            "Claim must survive when a real reservation row exists",
        )
