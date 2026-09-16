"""Real PostgreSQL/Redis, mocked inference: concurrent gateway admission and accounting."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings

from capp.classes.agent_gateway.key_service import AgentKeyService
from capp.classes.agent_gateway.providers.base import ProviderResult
from capp.classes.agent_gateway.providers.registry import reset_registry
from capp.classes.agent_gateway.proxy_service import AgentProxyService
from capp.models import AgentModelPrice, AgentUsageLog, BillingReservation, SparkPool
from capp.tests_agent_gateway import _make_user_with_sparks


@override_settings(CORTECS_UWENZAI_AGENT_API_KEY="dummy", AGENT_FX_EUR_KES=145, AGENT_EXCHANGE_RATE_API_URL=None)
class GatewayAdmissionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        reset_registry()
        self.user = _make_user_with_sparks(1000)
        _, self.key = AgentKeyService.create_key(self.user)
        AgentModelPrice.objects.create(
            provider="cortecs",
            model="parallel-model",
            currency="EUR",
            input_price_per_1m=Decimal("0.22"),
            output_price_per_1m=Decimal("0.66"),
        )
        self.addCleanup(reset_registry)

    def execute(self):
        close_old_connections()
        try:
            return AgentProxyService.execute_request(
                self.key,
                "parallel-model",
                [{"role": "user", "content": "hello"}],
                {"max_tokens": 256},
                provider_name="cortecs",
                client_operation_id=str(uuid.uuid4()),
            )
        finally:
            close_old_connections()

    @staticmethod
    def result():
        return ProviderResult(text="Mock reply", usage={"prompt_tokens": 11, "completion_tokens": 7})

    def test_same_key_different_operations_reach_inference_concurrently(self):
        barrier = threading.Barrier(2, timeout=10)

        def completion(**kwargs):
            barrier.wait()
            return self.result()

        with mock.patch(
            "capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion", side_effect=completion
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(self.execute), executor.submit(self.execute)]
                results = [future.result(timeout=20) for future in futures]
        for result in results:
            self.assertNotIn("error", result, result)
        self.assertEqual(AgentUsageLog.objects.count(), 2)
        self.assertEqual(BillingReservation.objects.filter(status=BillingReservation.Status.SETTLED).count(), 2)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 998)

    def test_concurrent_budget_admission_reserves_only_once(self):
        self.key.monthly_spark_budget = 1
        self.key.save()
        started = threading.Event()
        finish = threading.Event()

        def completion(**kwargs):
            started.set()
            if not finish.wait(timeout=10):
                raise AssertionError("Timed out waiting for concurrent admission")
            return self.result()

        with mock.patch(
            "capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion", side_effect=completion
        ) as provider:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(self.execute)
                self.assertTrue(started.wait(timeout=10))
                try:
                    second = executor.submit(self.execute).result(timeout=10)
                    self.assertEqual(second["error"]["status"], "budget_exceeded")
                finally:
                    finish.set()
                self.assertNotIn("error", first.result(timeout=10))
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(BillingReservation.objects.count(), 1)
        self.assertEqual(AgentUsageLog.objects.count(), 1)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 999)

    def test_admission_and_soft_erasure_share_user_then_key_lock_order(self):
        from django.db import transaction
        from capp.models import CustomUser

        admission_started = threading.Event()
        release_admission = threading.Event()
        erasure_started = threading.Event()
        erasure_locked_user = threading.Event()
        provider_started = threading.Event()
        release_provider = threading.Event()
        check_budget = AgentKeyService.check_budget

        def paused_budget(key, amount):
            admission_started.set()
            if not release_admission.wait(timeout=10):
                raise AssertionError("Admission gate timed out")
            return check_budget(key, amount)

        def completion(**kwargs):
            provider_started.set()
            if not release_provider.wait(timeout=10):
                raise AssertionError("Provider gate timed out")
            return self.result()

        def erase():
            close_old_connections()
            try:
                erasure_started.set()
                with transaction.atomic():
                    user = CustomUser.objects.select_for_update().get(pk=self.user.pk)
                    erasure_locked_user.set()
                    user.soft_delete()
            finally:
                close_old_connections()

        with mock.patch.object(AgentKeyService, "check_budget", side_effect=paused_budget):
            with mock.patch(
                "capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion",
                side_effect=completion,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    request = executor.submit(self.execute)
                    self.assertTrue(admission_started.wait(timeout=10))
                    erasure = executor.submit(erase)
                    self.assertTrue(erasure_started.wait(timeout=10))
                    try:
                        # Admission owns user BEFORE key: erasure cannot acquire user
                        # and then deadlock on key while admission inserts user-FK rows.
                        self.assertFalse(erasure_locked_user.wait(timeout=0.2))
                    finally:
                        release_admission.set()
                    try:
                        self.assertTrue(provider_started.wait(timeout=10))
                        erasure.result(timeout=10)
                    finally:
                        release_provider.set()
                    result = request.result(timeout=10)
                    self.assertNotIn("error", result, result)
        self.user.refresh_from_db()
        self.key.refresh_from_db()
        self.assertTrue(self.user.is_deleted)
        self.assertFalse(self.key.is_active)
        self.assertEqual(BillingReservation.objects.get().status, BillingReservation.Status.SETTLED)

    def test_gateway_account_lock_allows_ordinary_billing_foreign_key_lock(self):
        from django.db import transaction
        from capp.classes.billing.balances import lock_spark_pool
        from capp.classes.billing.reservation_service import BillingReservationService

        pool_locked = threading.Event()
        gateway_has_user_lock = threading.Event()
        check_budget = AgentKeyService.check_budget

        def ordinary_billing():
            close_old_connections()
            try:
                with transaction.atomic():
                    lock_spark_pool(self.user)
                    pool_locked.set()
                    if not gateway_has_user_lock.wait(timeout=10):
                        raise AssertionError("Gateway did not reach its admission lock")
                    # This insertion takes a PostgreSQL FK KEY SHARE lock on user.
                    # It must coexist with gateway NO KEY UPDATE while gateway waits on pool.
                    return BillingReservationService.reserve(
                        user=self.user,
                        feature="chat",
                        amount_sparks=1,
                        idempotency_key=str(uuid.uuid4()),
                    )
            finally:
                close_old_connections()

        def observed_budget(key, amount):
            gateway_has_user_lock.set()
            return check_budget(key, amount)

        with mock.patch.object(AgentKeyService, "check_budget", side_effect=observed_budget):
            with mock.patch(
                "capp.classes.agent_gateway.providers.cortecs.CortecsAgentProvider.chat_completion",
                return_value=self.result(),
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    ordinary = executor.submit(ordinary_billing)
                    self.assertTrue(pool_locked.wait(timeout=10))
                    gateway = executor.submit(self.execute)
                    ordinary.result(timeout=20)
                    result = gateway.result(timeout=20)
        self.assertNotIn("error", result, result)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 998)
        self.assertEqual(BillingReservation.objects.count(), 2)
