# paystack_app/tests_concurrency.py
"""
Real-concurrency coverage for payment fulfillment, against a real PostgreSQL.

These are ``TransactionTestCase``s on purpose: ``TestCase`` wraps each test in a
transaction that never commits, so a second thread would not see the first
thread's writes and every race below would pass vacuously.

The property under test is that a single payment produces exactly one net
credit, no matter how many callers try to fulfill it at once — a webhook and the
customer's own return from Paystack arriving together, a redelivered webhook
with a fresh event ID, or a retried manual verification.

Every thread closes its connections in a ``finally``; a leaked connection
holding a row lock would deadlock the suite rather than fail one test.
"""

import threading
from decimal import Decimal
from unittest import mock

from django.db import connections
from django.test import TransactionTestCase

from capp.models import SparkPackage, SparkPool
from paystack_app.models import PaystackFulfillment, PaystackTransaction
from paystack_app.services import PaystackService
from paystack_app.tests import make_user

REFERENCE = "ref_concurrent_001"
EXPECTED_MINOR = 10000


def provider_data(event_id=1, amount=EXPECTED_MINOR):
    return {
        "id": event_id,
        "reference": REFERENCE,
        "status": "success",
        "amount": amount,
        "currency": "KES",
        "metadata": {"package_slug": "starter", "package_type": "spark"},
    }


def run_together(targets):
    """
    Run each callable in its own thread, released simultaneously by a barrier.

    Without the barrier the first caller can finish before the second begins, and
    the race the test exists to provoke never happens.
    """
    barrier = threading.Barrier(len(targets))
    results = [None] * len(targets)

    def wrapped(index, target):
        try:
            barrier.wait(timeout=30)
            results[index] = ("ok", target())
        except Exception as exc:  # noqa: BLE001 - recorded and asserted on
            results[index] = ("error", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapped, args=(i, t)) for i, t in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for thread in threads:
        assert not thread.is_alive(), "thread did not finish; likely a deadlock"
    return results


class FulfillmentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)
        SparkPackage.objects.create(
            slug="starter",
            name="Starter",
            base_spark_amount=1000,
            base_price_kes=Decimal("100.00"),
            discount_percent=Decimal("0.00"),
            final_price_kes=Decimal("100.00"),
            is_active=True,
        )
        PaystackTransaction.objects.create(
            user=self.user,
            reference=REFERENCE,
            amount=Decimal("100.00"),
            currency="KES",
            status=PaystackTransaction.StatusChoices.PENDING,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=EXPECTED_MINOR,
        )

    def tearDown(self):
        connections.close_all()

    def _sparks(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def _fulfillments(self):
        return PaystackFulfillment.objects.filter(reference=REFERENCE).count()

    def _webhook(self, event_id=1):
        def call():
            return PaystackService.fulfill_reference(
                REFERENCE,
                provider_data(event_id),
                source=PaystackFulfillment.Source.WEBHOOK,
            )

        return call

    def _manual(self):
        def call():
            return PaystackService.fulfill_reference(
                REFERENCE,
                provider_data(event_id=99),
                source=PaystackFulfillment.Source.MANUAL_VERIFICATION,
            )

        return call

    def assertCreditedOnce(self, results):
        self.assertEqual(self._sparks(), 1000)
        self.assertEqual(self._fulfillments(), 1)
        # No caller may fail with an unexpected exception; the loser of the race
        # must return a clean "already fulfilled", not blow up.
        errors = [exc for status, exc in results if status == "error"]
        self.assertEqual(errors, [], f"unexpected exceptions: {errors}")

    # ------------------------------------------------------------------
    def test_concurrent_webhook_and_manual_verification_credit_once(self):
        """
        The realistic race: Paystack's webhook and the customer's return from the
        redirect land at the same moment on two workers.
        """
        results = run_together([self._webhook(event_id=1), self._manual()])
        self.assertCreditedOnce(results)

    def test_two_concurrent_webhook_deliveries_credit_once(self):
        results = run_together([self._webhook(event_id=1), self._webhook(event_id=2)])
        self.assertCreditedOnce(results)

    def test_five_concurrent_callers_credit_once(self):
        results = run_together([self._webhook(event_id=i) for i in range(1, 5)] + [self._manual()])
        self.assertCreditedOnce(results)

    def test_serial_retries_after_a_completed_fulfillment_credit_nothing_more(self):
        self._webhook(event_id=1)()
        self.assertEqual(self._sparks(), 1000)

        for event_id in (2, 3, 4):
            result = PaystackService.fulfill_reference(
                REFERENCE,
                provider_data(event_id),
                source=PaystackFulfillment.Source.WEBHOOK,
            )
            self.assertEqual(result["data"], "already_fulfilled")

        self.assertEqual(self._sparks(), 1000)
        self.assertEqual(self._fulfillments(), 1)

    def test_a_concurrent_mismatched_payload_cannot_ride_along_with_a_valid_one(self):
        """
        A forged replay racing the genuine webhook must not be credited even
        though the genuine one succeeds in the same instant.
        """

        def forged():
            return PaystackService.fulfill_reference(
                REFERENCE,
                provider_data(event_id=2, amount=1),
                source=PaystackFulfillment.Source.WEBHOOK,
            )

        run_together([self._webhook(event_id=1), forged])

        # Whoever took the lock first decides the outcome, and either outcome is
        # safe: at most one credit exists, and it is only ever the full amount.
        self.assertLessEqual(self._fulfillments(), 1)
        if self._fulfillments() == 1:
            fulfillment = PaystackFulfillment.objects.get(reference=REFERENCE)
            self.assertEqual(fulfillment.amount_minor, EXPECTED_MINOR)
            self.assertEqual(fulfillment.sparks_granted, 1000)
            self.assertEqual(self._sparks(), 1000)
        else:
            # The forged payload won the lock, so the row is FAILED and the
            # genuine delivery is refused rather than credited against it. The
            # customer's money is queued for manual review, not silently kept.
            self.assertEqual(self._sparks(), 0)
            txn = PaystackTransaction.objects.get(reference=REFERENCE)
            self.assertEqual(txn.status, PaystackTransaction.StatusChoices.FAILED)
            self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)


class ManualVerificationRetryTests(TransactionTestCase):
    """Retrying the provider call is safe because credit keys on the DB row."""

    def setUp(self):
        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)
        SparkPackage.objects.create(
            slug="starter",
            name="Starter",
            base_spark_amount=1000,
            base_price_kes=Decimal("100.00"),
            discount_percent=Decimal("0.00"),
            final_price_kes=Decimal("100.00"),
            is_active=True,
        )
        PaystackTransaction.objects.create(
            user=self.user,
            reference=REFERENCE,
            amount=Decimal("100.00"),
            currency="KES",
            status=PaystackTransaction.StatusChoices.PENDING,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=EXPECTED_MINOR,
        )

    def tearDown(self):
        connections.close_all()

    def _sparks(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def _verify_response(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"status": True, "data": provider_data()}
        return response

    def test_repeated_manual_verification_credits_once(self):
        with mock.patch("paystack_app.services.requests.get", return_value=self._verify_response()):
            first = PaystackService.verify_and_fulfill(REFERENCE)
            second = PaystackService.verify_and_fulfill(REFERENCE)
            third = PaystackService.verify_and_fulfill(REFERENCE)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["data"], "already_fulfilled")
        self.assertEqual(third["data"], "already_fulfilled")
        self.assertEqual(self._sparks(), 1000)
        self.assertEqual(PaystackFulfillment.objects.filter(reference=REFERENCE).count(), 1)

    def test_transient_provider_error_leaves_the_payment_retryable(self):
        """A failed provider call must not poison the row; the retry succeeds."""
        import requests

        with mock.patch(
            "paystack_app.services.requests.get",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            result = PaystackService.verify_and_fulfill(REFERENCE)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(self._sparks(), 0)

        txn = PaystackTransaction.objects.get(reference=REFERENCE)
        self.assertEqual(txn.status, PaystackTransaction.StatusChoices.PENDING)

        with mock.patch("paystack_app.services.requests.get", return_value=self._verify_response()):
            retry = PaystackService.verify_and_fulfill(REFERENCE)
        self.assertEqual(retry["status"], "success")
        self.assertEqual(self._sparks(), 1000)
