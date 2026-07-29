# paystack_app/tests.py
"""
Webhook authenticity and provider-result validation.

The property under test throughout is that a payment is credited only when the
provider result matches the intent recorded before the customer was sent to
Paystack, and that nothing in a payload can substitute for that intent.
"""

import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from capp.models import CustomUser, SparkPackage, SparkPool
from capp.utils.encryption import EncryptionService
from paystack_app.models import PaystackFulfillment, PaystackTransaction, PaystackWebhookEvent
from paystack_app.services import PaystackService

TEST_SECRET = "test_webhook_secret_value"


def _sign(raw_body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()


def make_user(username="buyer", email="buyer@example.com"):
    email_fields = EncryptionService.prepare_email_fields(email)
    return CustomUser.objects.create_user(
        username=username,
        password="pw12345",
        email_encrypted=email_fields["email_encrypted"],
        email_hash=email_fields["email_hash"],
        is_verified=True,
        account_status=CustomUser.AccountStatus.ACTIVE,
    )


class PaystackWebhookTests(TestCase):
    def setUp(self):
        self.url = reverse("paystack_webhook")

        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)

        self.package = SparkPackage.objects.create(
            slug="starter",
            name="Starter Pack",
            base_spark_amount=1000,
            base_price_kes=Decimal("100.00"),
            discount_percent=Decimal("0.00"),
            final_price_kes=Decimal("100.00"),
            is_active=True,
        )
        # Paystack amount is in minor units (KES * 100).
        self.expected_amount = 10000

        self.txn = PaystackTransaction.objects.create(
            user=self.user,
            reference="txn_ref_001",
            amount=Decimal("100.00"),
            currency="KES",
            status=PaystackTransaction.StatusChoices.PENDING,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=self.expected_amount,
        )

    def _build_payload(
        self,
        amount=None,
        event_id=1001,
        reference="txn_ref_001",
        currency="KES",
        metadata=None,
        status="success",
    ):
        if metadata is None:
            metadata = {"package_slug": "starter", "package_type": "spark"}
        return {
            "event": "charge.success",
            "data": {
                "id": event_id,
                "reference": reference,
                "status": status,
                "amount": self.expected_amount if amount is None else amount,
                "currency": currency,
                "metadata": metadata,
            },
        }

    def _post(self, payload, sign=True, secret=TEST_SECRET, with_signature=True):
        raw = json.dumps(payload).encode("utf-8")
        extra = {}
        if with_signature:
            sig = _sign(raw, secret) if sign else "deadbeef"
            extra["HTTP_X_PAYSTACK_SIGNATURE"] = sig
        return self.client.post(self.url, data=raw, content_type="application/json", **extra)

    def _sparks(self):
        return SparkPool.objects.get(user=self.user).sparks

    # ------------------------------------------------------------------
    # Authenticity
    # ------------------------------------------------------------------
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", None)
    def test_webhook_rejected_when_secret_missing(self):
        resp = self._post(self._build_payload())
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._sparks(), 0)
        self.assertFalse(PaystackWebhookEvent.objects.exists())

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_webhook_rejected_when_signature_missing(self):
        resp = self._post(self._build_payload(), with_signature=False)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self._sparks(), 0)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_webhook_rejected_on_invalid_signature(self):
        resp = self._post(self._build_payload(), sign=False)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["data"], "invalid_signature")
        self.assertEqual(self._sparks(), 0)

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_valid_signature_and_amount_credits_sparks(self):
        resp = self._post(self._build_payload())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._sparks(), 1000)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.SUCCESS)
        self.assertEqual(self.txn.fulfillment_state, PaystackTransaction.FulfillmentState.FULFILLED)

        fulfillment = PaystackFulfillment.objects.get(reference="txn_ref_001")
        self.assertEqual(fulfillment.sparks_granted, 1000)
        self.assertEqual(fulfillment.amount_minor, self.expected_amount)
        self.assertEqual(fulfillment.currency, "KES")

    # ------------------------------------------------------------------
    # Amount and currency must match the recorded intent exactly
    # ------------------------------------------------------------------
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_valid_signature_wrong_amount_not_credited(self):
        resp = self._post(self._build_payload(amount=5000))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)
        self.assertIn("amount_mismatch", self.txn.paystack_message)
        self.assertFalse(PaystackFulfillment.objects.exists())

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_underpayment_of_one_minor_unit_is_rejected(self):
        self._post(self._build_payload(amount=self.expected_amount - 1))
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_overpayment_is_also_rejected_rather_than_credited(self):
        # An overpayment is still a mismatch: we do not know what it was for.
        self._post(self._build_payload(amount=self.expected_amount + 1))
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_wrong_currency_not_credited(self):
        # Same numeric amount, cheaper currency — the classic value swap.
        resp = self._post(self._build_payload(currency="NGN"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)
        self.assertIn("currency_mismatch", self.txn.paystack_message)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_missing_currency_not_credited(self):
        self._post(self._build_payload(currency=""))
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.paystack_message, "currency_missing")

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_unreadable_amount_not_credited(self):
        self._post(self._build_payload(amount="not-a-number"))
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_non_success_provider_status_not_credited(self):
        self._post(self._build_payload(status="failed"))
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)

    # ------------------------------------------------------------------
    # Metadata is a cross-check, never a source of truth
    # ------------------------------------------------------------------
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_metadata_cannot_upgrade_the_package(self):
        """A payload naming a bigger package must not buy that bigger package."""
        SparkPackage.objects.create(
            slug="whale",
            name="Whale Pack",
            base_spark_amount=1_000_000,
            base_price_kes=Decimal("100000.00"),
            discount_percent=Decimal("0.00"),
            final_price_kes=Decimal("100000.00"),
            is_active=True,
        )
        resp = self._post(
            self._build_payload(metadata={"package_slug": "whale", "package_type": "spark"}),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, PaystackTransaction.StatusChoices.FAILED)
        self.assertIn("metadata_mismatch:package_slug", self.txn.paystack_message)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_metadata_cannot_change_the_package_type(self):
        self._post(self._build_payload(metadata={"package_slug": "starter", "package_type": "subscription"}))
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertIn("metadata_mismatch:package_type", self.txn.paystack_message)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_metadata_cannot_redirect_value_to_another_guest(self):
        self._post(
            self._build_payload(
                metadata={
                    "package_slug": "starter",
                    "package_type": "spark",
                    "guest_session_id": "some-other-guest",
                }
            )
        )
        self.assertEqual(self._sparks(), 0)
        self.txn.refresh_from_db()
        self.assertIn("metadata_mismatch:guest_session_id", self.txn.paystack_message)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_absent_metadata_is_fine_because_intent_is_authoritative(self):
        """Paystack need not echo metadata; the DB intent is sufficient."""
        resp = self._post(self._build_payload(metadata={}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._sparks(), 1000)

    # ------------------------------------------------------------------
    # Reference handling
    # ------------------------------------------------------------------
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_unknown_reference_credits_nothing(self):
        resp = self._post(self._build_payload(reference="txn_does_not_exist"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._sparks(), 0)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_transaction_without_recorded_intent_is_never_credited(self):
        """A legacy row has no intent, so there is nothing to validate against."""
        legacy = PaystackTransaction.objects.create(
            user=self.user,
            reference="txn_legacy_001",
            amount=Decimal("100.00"),
            currency="KES",
            status=PaystackTransaction.StatusChoices.PENDING,
        )
        self._post(self._build_payload(reference="txn_legacy_001", event_id=2002))
        self.assertEqual(self._sparks(), 0)
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, PaystackTransaction.StatusChoices.FAILED)
        self.assertEqual(legacy.paystack_message, "no_recorded_intent")
        # Flagged so an operator can reconcile a real payment we refused.
        self.assertEqual(legacy.fulfillment_state, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_duplicate_event_does_not_double_credit(self):
        payload = self._build_payload()
        self.assertEqual(self._post(payload).status_code, 200)
        self.assertEqual(self._sparks(), 1000)
        resp2 = self._post(payload)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["data"], "duplicate_ignored")
        self.assertEqual(self._sparks(), 1000)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_same_reference_with_different_event_ids_credits_once(self):
        """
        The event-ID dedupe is not the real guard: a replay with a fresh ID gets
        past it and must still be stopped by reference-level fulfillment.
        """
        self.assertEqual(self._post(self._build_payload(event_id=1)).status_code, 200)
        self.assertEqual(self._sparks(), 1000)

        self.assertEqual(self._post(self._build_payload(event_id=2)).status_code, 200)
        self.assertEqual(self._sparks(), 1000)
        self.assertEqual(self._post(self._build_payload(event_id=3)).status_code, 200)
        self.assertEqual(self._sparks(), 1000)

        self.assertEqual(PaystackFulfillment.objects.filter(reference="txn_ref_001").count(), 1)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_fulfillment_is_refused_once_recorded_even_if_state_is_reset(self):
        """The fulfillment row alone is enough; it does not rely on the status column."""
        self._post(self._build_payload(event_id=1))
        self.assertEqual(self._sparks(), 1000)

        # Simulate an operator or bug clearing the transaction's own markers.
        PaystackTransaction.objects.filter(reference="txn_ref_001").update(
            status=PaystackTransaction.StatusChoices.PENDING,
            fulfillment_state=PaystackTransaction.FulfillmentState.UNFULFILLED,
        )

        self._post(self._build_payload(event_id=2))
        self.assertEqual(self._sparks(), 1000)

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_legacy_success_is_not_credited_again(self):
        """
        The migration marks historical successes fulfilled, so a redelivered old
        webhook cannot credit a payment that was already honoured.
        """
        PaystackTransaction.objects.create(
            user=self.user,
            reference="txn_old_success",
            amount=Decimal("100.00"),
            currency="KES",
            status=PaystackTransaction.StatusChoices.SUCCESS,
            fulfillment_state=PaystackTransaction.FulfillmentState.FULFILLED,
        )
        self._post(self._build_payload(reference="txn_old_success", event_id=5005))
        self.assertEqual(self._sparks(), 0)


class PaystackSignatureUnitTests(TestCase):
    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", None)
    def test_verify_signature_fails_closed_without_secret(self):
        self.assertFalse(PaystackService._verify_signature(b"body", "sig"))

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_verify_signature_rejects_missing_parts(self):
        self.assertFalse(PaystackService._verify_signature(b"", "sig"))
        self.assertFalse(PaystackService._verify_signature(b"body", ""))

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_verify_signature_accepts_valid(self):
        raw = b'{"event":"charge.success"}'
        self.assertTrue(PaystackService._verify_signature(raw, _sign(raw)))

    @mock.patch.object(PaystackService, "WEBHOOK_SECRET", TEST_SECRET)
    def test_verify_signature_rejects_a_signature_for_a_different_body(self):
        self.assertFalse(PaystackService._verify_signature(b'{"a":1}', _sign(b'{"a":2}')))
