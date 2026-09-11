"""Paid-account erasure must retain accounting evidence, not payment access."""

import uuid
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models.signals import pre_delete
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from capp.classes.auth.auth import Auth
from capp.classes.billing.reservation_service import BillingReservationService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.models import (
    BillingReservation,
    CharacterGalleryImage,
    Conversation,
    ConversationSummary,
    CustomUser,
    GuestTokenQuota,
    Message,
    PaymentTransaction,
    Reminder,
    ReminderDeliveryAttempt,
    SparkPool,
    SparkTransaction,
    TokenUsageLog,
    UsageCharge,
)
from capp.tests import _make_character
from paystack_app.models import PaystackFulfillment, PaystackTransaction, PaystackWebhookEvent
from paystack_app.services import PaystackService
from paystack_app.tests import make_user
from paystack_app.tests_ownership import auth_header, make_spark_package, make_txn


class PaidAccountDeletionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.user_id = self.user.pk
        self.pool = SparkPool.objects.create(user=self.user, sparks=1000, is_active=True)
        self.txn = make_txn(
            "paid-account",
            user=self.user,
            fulfillment_state=PaystackTransaction.FulfillmentState.FULFILLED,
            callback_path="/payment/verify",
            paystack_data={"id": 123, "amount": 10000, "currency": "KES"},
        )
        self.fulfillment = PaystackFulfillment.objects.create(
            transaction=self.txn,
            reference=self.txn.reference,
            source=PaystackFulfillment.Source.WEBHOOK,
            package_slug="starter",
            package_type="spark",
            amount_minor=10000,
            currency="KES",
            sparks_granted=1000,
        )

    def test_delete_endpoint_removes_paid_account_and_reminders_but_keeps_billing(self):
        character = _make_character(name="Retention test", slug="retention-test")
        conversation = Conversation.objects.create(user=self.user, character=character)
        message = Message.objects.create(
            conversation=conversation, sender_type=Message.SenderType.USER, content="Private message"
        )
        summary = ConversationSummary.objects.create(conversation=conversation, summary_text="Private summary")
        image = CharacterGalleryImage.objects.create(
            character=character,
            created_by=self.user,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
        )
        other = make_user("unrelated", "unrelated@example.com")
        other_conversation = Conversation.objects.create(user=other, character=character)
        other_image = CharacterGalleryImage.objects.create(
            character=character, created_by=other, visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE
        )
        reservation = BillingReservationService.reserve(
            idempotency_key="reminder-hold",
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            amount_sparks=85,
            user=self.user,
        )
        reminder = Reminder.objects.create(
            user=self.user,
            conversation=conversation,
            character=character,
            reminder_text="Private reminder",
            due_at=timezone.now() + timedelta(hours=1),
            timezone_snapshot="Europe/Berlin",
        )
        attempt = ReminderDeliveryAttempt.objects.create(
            reminder_key=reminder.pk,
            user=self.user,
            revision=1,
            lease_until=timezone.now() + timedelta(minutes=5),
            reservation=reservation,
        )
        ledger = reservation.debit_spark_transaction
        payment = PaymentTransaction.objects.create(
            user=self.user,
            spark_pool=self.pool,
            amount=Decimal("100.00"),
            currency="KES",
            payment_method=PaymentTransaction.PaymentMethod.CARD,
            purpose=PaymentTransaction.PurposeChoices.SPARK_PURCHASE,
            status=PaymentTransaction.StatusChoices.COMPLETED,
            provider_reference=self.txn.reference,
        )
        # A charge can also be deleted through the private token-log cascade.
        token_log = TokenUsageLog.objects.create(user=self.user, conversation=conversation)
        charge = UsageCharge.objects.create(
            user=self.user,
            token_usage_log=token_log,
            spark_transaction=ledger,
            charge_source=UsageCharge.ChargeSource.SPARK_POOL,
            sparks_charged=85,
        )
        before = PaystackTransaction.objects.filter(pk=self.txn.pk).values().get()

        response = self.client.delete("/api/v1/auth/delete-account/", **auth_header(self.user))

        self.assertEqual(response.status_code, 200, response.json())
        for model, pk in (
            (CustomUser, self.user_id),
            (SparkPool, self.pool.pk),
            (Conversation, conversation.pk),
            (Message, message.pk),
            (ConversationSummary, summary.pk),
            (CharacterGalleryImage, image.pk),
            (Reminder, reminder.pk),
            (ReminderDeliveryAttempt, attempt.pk),
            (BillingReservation, reservation.pk),
            (TokenUsageLog, token_log.pk),
        ):
            self.assertFalse(model.objects.filter(pk=pk).exists(), model.__name__)
        self.assertTrue(Conversation.objects.filter(pk=other_conversation.pk).exists())
        self.assertTrue(CharacterGalleryImage.objects.filter(pk=other_image.pk).exists())
        self.assertEqual(PaystackFulfillment.objects.get(pk=self.fulfillment.pk).sparks_granted, 1000)
        self.txn.refresh_from_db()
        self.assertIsNone(self.txn.user_id)
        self.assertIsNotNone(self.txn.owner_deleted_at)
        after = PaystackTransaction.objects.filter(pk=self.txn.pk).values().get()
        for field, value in before.items():
            if field not in {"user_id", "owner_deleted_at", "updated_at"}:
                self.assertEqual(after[field], value, field)
        ledger.refresh_from_db()
        self.assertIsNone(ledger.user_id)
        self.assertIsNone(ledger.spark_pool_id)
        self.assertEqual(ledger.spark_amount, -85)
        payment.refresh_from_db()
        self.assertIsNone(payment.user_id)
        self.assertIsNone(payment.spark_pool_id)
        self.assertEqual(payment.provider_reference, self.txn.reference)
        self.assertEqual(payment.amount, Decimal("100.00"))
        charge.refresh_from_db()
        self.assertIsNone(charge.user_id)
        self.assertIsNone(charge.token_usage_log_id)
        self.assertEqual(charge.spark_transaction_id, ledger.pk)
        self.assertEqual(charge.sparks_charged, 85)
        for row in (ledger, payment, charge):
            self.assertIn("Deleted account", str(row))

    def test_instance_and_queryset_deletion_preserve_purchase_intents(self):
        for bulk in (False, True):
            with self.subTest(bulk=bulk):
                user = make_user(f"delete-{bulk}", f"delete-{bulk}@example.com")
                txn = make_txn(f"delete-{bulk}", user=user, status="pending")
                if bulk:
                    CustomUser.objects.filter(pk=user.pk).delete()
                else:
                    user.delete()
                txn.refresh_from_db()
                self.assertIsNone(txn.user_id)
                self.assertIsNotNone(txn.owner_deleted_at)

    def test_erasure_and_ledger_detachment_roll_back_together(self):
        def abort_after_payment_detachment(**kwargs):
            self.assertTrue(PaystackTransaction.objects.filter(pk=self.txn.pk, owner_deleted_at__isnull=False).exists())
            raise RuntimeError("abort after detachment")

        # Registered after the payment receiver; forces a real collector rollback.
        pre_delete.connect(abort_after_payment_detachment, sender=CustomUser)
        try:
            self.assertEqual(Auth.hard_delete_user(self.user)["status"], "fail")
        finally:
            pre_delete.disconnect(abort_after_payment_detachment, sender=CustomUser)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.user_id, self.user_id)
        self.assertIsNone(self.txn.owner_deleted_at)
        self.assertTrue(CustomUser.objects.filter(pk=self.user_id).exists())

    def test_erased_payment_cannot_be_reassigned_to_account_or_guest(self):
        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        other = make_user("other", "other@example.com")
        for owner in ({"user": other}, {"guest_session_id": str(uuid.uuid4())}):
            with self.subTest(owner=tuple(owner)):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PaystackTransaction.objects.filter(pk=self.txn.pk).update(**owner)

    def test_recreated_account_cannot_verify_deleted_accounts_payment(self):
        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        recreated = make_user()
        response = self.client.get(
            reverse("paystack_verify"), {"reference": self.txn.reference}, **auth_header(recreated)
        )
        self.assertEqual(response.status_code, 404)

    def test_fulfilled_payment_replay_after_erasure_cannot_credit_again(self):
        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        with mock.patch.object(PaystackService, "_apply_fulfillment") as apply:
            result = PaystackService.fulfill_reference(self.txn.reference, {}, PaystackFulfillment.Source.WEBHOOK)
        self.assertEqual(result, {"status": "success", "data": "already_fulfilled"})
        apply.assert_not_called()
        self.assertEqual(PaystackFulfillment.objects.filter(reference=self.txn.reference).count(), 1)

    def test_historical_origin_only_payment_never_credits_old_guest(self):
        make_spark_package()
        identity, _ = GuestIdentityService.mint()
        txn = make_txn("historical-orphan", origin_guest_session_id=identity.owner_key, user=None, status="pending")
        payload = {"status": "success", "reference": txn.reference, "amount": 10000, "currency": "KES"}
        with mock.patch.object(PaystackService, "_apply_fulfillment") as apply:
            result = PaystackService.fulfill_reference(txn.reference, payload, PaystackFulfillment.Source.WEBHOOK)
        self.assertEqual(result, {"status": "fail", "data": "payment_owner_missing"})
        apply.assert_not_called()
        txn.refresh_from_db()
        self.assertIsNone(txn.owner_deleted_at)
        self.assertEqual(txn.fulfillment_state, "manual_review")
        self.assertFalse(GuestTokenQuota.objects.filter(session_id=identity.owner_key).exists())

    def test_financial_facts_survive_but_personal_payloads_and_credentials_do_not(self):
        payload = {
            "id": 987,
            "reference": self.txn.reference,
            "amount": 10000,
            "currency": "KES",
            "customer": {"email": "private@example.com", "phone": "+254700000000"},
            "authorization": {"authorization_code": "secret", "last4": "1234"},
            "ip_address": "192.0.2.1",
            "metadata": {"full_name": "Private Person", "user_id": str(self.user_id)},
        }
        PaystackTransaction.objects.filter(pk=self.txn.pk).update(
            paystack_data=payload,
            paystack_message="Private Person",
            paystack_access_code="checkout-secret",
            paystack_authorization_url="https://checkout.example.com/secret",
            paystack_transaction_id=987,
        )
        event = PaystackWebhookEvent.objects.create(
            event_type="charge.success", paystack_event_id="987", data=payload, processed=True
        )
        ledger = SparkTransaction.objects.create(
            user=self.user,
            spark_pool=self.pool,
            entry_type="credit",
            source="purchase",
            spark_amount=1000,
            metadata={"email": "private@example.com"},
            description="Private Person",
        )
        payment = PaymentTransaction.objects.create(
            user=self.user,
            amount=100,
            currency="KES",
            payment_method="card",
            purpose="spark_purchase",
            provider_reference=self.txn.reference,
            provider_payload=payload,
            metadata={"phone": "+254700000000"},
            failure_reason="Private Person",
        )
        charge = UsageCharge.objects.create(user=self.user, charge_source="spark_pool", notes="Private Person")

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        self.txn.refresh_from_db()
        event.refresh_from_db()
        ledger.refresh_from_db()
        payment.refresh_from_db()
        charge.refresh_from_db()
        expected = {"id": 987, "reference": self.txn.reference, "amount": 10000, "currency": "KES"}
        self.assertEqual(self.txn.paystack_data, expected)
        self.assertEqual(event.data, expected)
        self.assertIsNone(self.txn.paystack_access_code)
        self.assertIsNone(self.txn.paystack_authorization_url)
        self.assertIsNone(self.txn.paystack_message)
        self.assertEqual(ledger.metadata, {})
        self.assertIsNone(ledger.description)
        self.assertEqual(ledger.spark_amount, 1000)
        self.assertEqual(payment.provider_payload, {})
        self.assertEqual(payment.metadata, {})
        self.assertIsNone(payment.failure_reason)
        self.assertEqual(payment.amount, 100)
        self.assertEqual(payment.provider_reference, self.txn.reference)
        self.assertIsNone(charge.notes)
        self.assertTrue(PaystackFulfillment.objects.filter(pk=self.fulfillment.pk).exists())

        # Even a new signed event ID must not reintroduce PII or credit value.
        payload["id"] = 988
        body = {"event": "charge.success", "data": payload}
        raw = json.dumps(body).encode()
        signature = hmac.new(b"test-webhook-secret", raw, hashlib.sha512).hexdigest()
        with mock.patch.object(PaystackService, "WEBHOOK_SECRET", "test-webhook-secret"):
            result = PaystackService.process_webhook(body, signature, raw)
        self.assertEqual(result["status"], "success")
        late = PaystackWebhookEvent.objects.get(paystack_event_id="988")
        self.assertNotIn("customer", late.data)
        self.assertNotIn("authorization", late.data)
        self.assertNotIn("metadata", late.data)
        self.assertEqual(PaystackFulfillment.objects.filter(reference=self.txn.reference).count(), 1)

    def test_integrity_error_without_fulfillment_is_not_reported_as_success(self):
        make_spark_package()
        txn = make_txn("failed-integrity", user=self.user, status="pending")
        payload = {"status": "success", "reference": txn.reference, "amount": 10000, "currency": "KES"}
        with mock.patch.object(PaystackService, "_apply_fulfillment", side_effect=IntegrityError("owner gone")):
            result = PaystackService.fulfill_reference(txn.reference, payload, PaystackFulfillment.Source.WEBHOOK)
        self.assertEqual(result, {"status": "fail", "data": "fulfillment_integrity_error"})
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    def test_late_capture_for_deleted_account_goes_to_review_not_guest_credit(self):
        make_spark_package()
        identity, _ = GuestIdentityService.mint()
        quota = GuestTokenQuota.objects.create(session_id=identity.owner_key, sparks=0)
        for guest_origin in (None, identity.owner_key):
            with self.subTest(guest_origin=guest_origin):
                user = make_user(f"late-{bool(guest_origin)}", f"late-{bool(guest_origin)}@example.com")
                txn = make_txn(
                    f"late-{bool(guest_origin)}",
                    user=user,
                    status="pending",
                    origin_guest_session_id=guest_origin,
                    claimed_from_guest_at=timezone.now() if guest_origin else None,
                )
                self.assertEqual(Auth.hard_delete_user(user)["status"], "success")
                payload = {
                    "status": "success",
                    "reference": txn.reference,
                    "amount": 10000,
                    "currency": "KES",
                    "customer": {"email": "private@example.com"},
                    "message": "Private Person",
                }
                with mock.patch.object(PaystackService, "_apply_fulfillment") as apply:
                    result = PaystackService.fulfill_reference(
                        txn.reference, payload, PaystackFulfillment.Source.WEBHOOK
                    )
                self.assertEqual(result, {"status": "fail", "data": "payment_owner_deleted"})
                apply.assert_not_called()
                txn.refresh_from_db()
                self.assertEqual(txn.status, "success")
                self.assertEqual(txn.fulfillment_state, "manual_review")
                self.assertEqual(txn.origin_guest_session_id, guest_origin)
                self.assertNotIn("customer", txn.paystack_data)
                self.assertEqual(txn.paystack_message, "")
                self.assertFalse(PaystackFulfillment.objects.filter(reference=txn.reference).exists())
        quota.refresh_from_db()
        self.assertEqual(quota.sparks, 0)
