"""Upgrade existing payment intents without changing financial history."""

from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from paystack_app.tests import make_user


class PaymentErasureMigrationTests(TransactionTestCase):
    def _migrate(self, name):
        executor = MigrationExecutor(connection)
        executor.migrate([("paystack_app", name)])
        return executor.loader.project_state([("paystack_app", name)]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_paid_intents_and_fulfillments_survive_upgrade_and_erasure(self):
        apps = self._migrate("0004_transaction_intent_and_fulfillment")
        old_txn = apps.get_model("paystack_app", "PaystackTransaction")
        old_fulfillment = apps.get_model("paystack_app", "PaystackFulfillment")
        user = make_user()
        paid = old_txn.objects.create(
            user_id=user.pk,
            reference="migration-paid",
            amount=Decimal("98.00"),
            currency="KES",
            status="success",
            fulfillment_state="fulfilled",
            package_slug="coins_1000",
            package_type="spark",
            expected_amount_minor=9800,
            callback_path="/payment/verify",
            paystack_data={"id": 1234, "amount": 9800},
        )
        proof = old_fulfillment.objects.create(
            transaction=paid,
            reference=paid.reference,
            source="webhook",
            package_slug=paid.package_slug,
            package_type="spark",
            amount_minor=9800,
            currency="KES",
            sparks_granted=1000,
        )
        before = old_txn.objects.filter(pk=paid.pk).values().get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            old_txn.objects.filter(pk=paid.pk).update(user_id=None)

        apps = self._migrate("0005_payment_owner_erasure")
        new_txn = apps.get_model("paystack_app", "PaystackTransaction")
        after = new_txn.objects.filter(pk=paid.pk).values().get()
        self.assertIsNone(after.pop("owner_deleted_at"))
        self.assertEqual(before, after)
        self.assertTrue(apps.get_model("paystack_app", "PaystackFulfillment").objects.filter(pk=proof.pk).exists())
        # A naked SET_NULL still fails: the explicit erasure state is mandatory.
        with self.assertRaises(IntegrityError), transaction.atomic():
            new_txn.objects.filter(pk=paid.pk).update(user_id=None)
        new_txn.objects.filter(pk=paid.pk).update(user_id=None, owner_deleted_at=timezone.now())
        retained = new_txn.objects.get(pk=paid.pk)
        self.assertEqual(retained.amount, Decimal("98.00"))
        self.assertEqual(retained.expected_amount_minor, 9800)
        self.assertEqual(retained.fulfillment_state, "fulfilled")
        self.assertEqual(retained.paystack_data, {"id": 1234, "amount": 9800})
