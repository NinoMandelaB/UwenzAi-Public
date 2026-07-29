# paystack_app/tests_migrations.py
"""
Schema guarantees, and what happens to rows written before them.

Two things are covered. First, the database itself refuses the states the
application must never reach — a payment owned by both an account and a guest, a
half-recorded intent, a second credit against one reference. Application bugs
are contained by the schema rather than only by the code path above it.

Second, the upgrade path. Legacy rows have no recoverable purchase intent, so the
migration leaves it NULL and the new constraints are conditional. These tests pin
that down against real legacy shapes, because a constraint that rejects existing
production data is an outage.
"""

from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from capp.classes.guest.guest_identity_service import GuestIdentityService
from paystack_app.models import PaystackFulfillment, PaystackTransaction
from paystack_app.tests import make_user

APP = "paystack_app"
BEFORE = "0003_paystacktransaction_claimed_from_guest_at_and_more"
AFTER = "0004_transaction_intent_and_fulfillment"


class ConstraintTests(TestCase):
    """The states the schema will not store, whatever the code above it does."""

    def setUp(self):
        self.user = make_user()
        self.identity, _ = GuestIdentityService.mint()

    def _create(self, **overrides):
        fields = {
            "reference": "ref_constraint",
            "amount": Decimal("100.00"),
            "currency": "KES",
            "status": PaystackTransaction.StatusChoices.PENDING,
        }
        fields.update(overrides)
        with transaction.atomic():
            return PaystackTransaction.objects.create(**fields)

    def test_a_payment_cannot_be_owned_by_both_an_account_and_a_guest(self):
        with self.assertRaises(IntegrityError):
            self._create(user=self.user, guest_session_id=self.identity.owner_key)

    def test_a_recorded_intent_must_have_an_owner(self):
        with self.assertRaises(IntegrityError):
            self._create(
                package_slug="starter",
                package_type=PaystackTransaction.PackageType.SPARK,
                expected_amount_minor=10000,
            )

    def test_a_recorded_intent_must_carry_an_expected_amount(self):
        with self.assertRaises(IntegrityError):
            self._create(
                user=self.user,
                package_slug="starter",
                package_type=PaystackTransaction.PackageType.SPARK,
            )

    def test_a_recorded_intent_must_carry_a_package_type(self):
        with self.assertRaises(IntegrityError):
            self._create(
                user=self.user,
                package_slug="starter",
                expected_amount_minor=10000,
            )

    def test_a_recorded_intent_must_carry_a_currency(self):
        with self.assertRaises(IntegrityError):
            self._create(
                user=self.user,
                currency="",
                package_slug="starter",
                package_type=PaystackTransaction.PackageType.SPARK,
                expected_amount_minor=10000,
            )

    def test_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            self._create(user=self.user, amount=Decimal("0.00"))

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self._create(user=self.user, amount=Decimal("-1.00"))

    def test_expected_amount_must_be_positive_when_present(self):
        with self.assertRaises(IntegrityError):
            self._create(
                user=self.user,
                package_slug="starter",
                package_type=PaystackTransaction.PackageType.SPARK,
                expected_amount_minor=0,
            )

    def test_reference_is_unique(self):
        self._create(user=self.user)
        with self.assertRaises(IntegrityError):
            self._create(user=self.user)

    def test_a_complete_intent_is_accepted(self):
        txn = self._create(
            user=self.user,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=10000,
        )
        self.assertEqual(txn.fulfillment_state, PaystackTransaction.FulfillmentState.UNFULFILLED)

    def test_a_guest_owned_intent_is_accepted(self):
        txn = self._create(
            guest_session_id=self.identity.owner_key,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=10000,
        )
        self.assertIsNone(txn.user_id)

    def test_a_claimed_purchase_keeps_provenance_as_its_owner_proof(self):
        """After a claim, guest_session_id is released but provenance remains."""
        txn = self._create(
            user=self.user,
            origin_guest_session_id=self.identity.owner_key,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=10000,
        )
        self.assertIsNone(txn.guest_session_id)
        self.assertEqual(txn.origin_guest_session_id, self.identity.owner_key)


class FulfillmentUniquenessTests(TestCase):
    """One credit per reference, enforced below the application."""

    def setUp(self):
        self.user = make_user()
        self.txn = PaystackTransaction.objects.create(
            user=self.user,
            reference="ref_unique",
            amount=Decimal("100.00"),
            currency="KES",
            status=PaystackTransaction.StatusChoices.SUCCESS,
            package_slug="starter",
            package_type=PaystackTransaction.PackageType.SPARK,
            expected_amount_minor=10000,
        )

    def _fulfill(self, reference="ref_unique", txn=None):
        with transaction.atomic():
            return PaystackFulfillment.objects.create(
                transaction=txn or self.txn,
                reference=reference,
                source=PaystackFulfillment.Source.WEBHOOK,
                package_slug="starter",
                package_type=PaystackTransaction.PackageType.SPARK,
                amount_minor=10000,
                currency="KES",
                sparks_granted=1000,
            )

    def test_a_second_fulfillment_for_the_same_reference_is_rejected(self):
        self._fulfill()
        with self.assertRaises(IntegrityError):
            self._fulfill()

    def test_a_second_fulfillment_for_the_same_transaction_is_rejected(self):
        """
        Belt and braces: the reference is unique *and* the relation is one-to-one,
        so neither column alone has to be the whole guard.
        """
        self._fulfill()
        with self.assertRaises(IntegrityError):
            self._fulfill(reference="ref_unique_other")

    def test_deleting_the_transaction_removes_its_fulfillment(self):
        self._fulfill()
        self.txn.delete()
        self.assertFalse(PaystackFulfillment.objects.exists())


class StatusTransitionTests(TestCase):
    """Terminal states stay terminal."""

    def _txn(self, status):
        return PaystackTransaction(status=status)

    def test_pending_may_succeed_or_fail(self):
        txn = self._txn(PaystackTransaction.StatusChoices.PENDING)
        self.assertTrue(txn.can_transition_to(PaystackTransaction.StatusChoices.SUCCESS))
        self.assertTrue(txn.can_transition_to(PaystackTransaction.StatusChoices.FAILED))

    def test_success_may_only_be_reversed(self):
        txn = self._txn(PaystackTransaction.StatusChoices.SUCCESS)
        self.assertTrue(txn.can_transition_to(PaystackTransaction.StatusChoices.REVERSED))
        self.assertFalse(txn.can_transition_to(PaystackTransaction.StatusChoices.FAILED))
        self.assertFalse(txn.can_transition_to(PaystackTransaction.StatusChoices.PENDING))

    def test_failed_is_terminal(self):
        """A refused payment cannot be talked back into a success by a replay."""
        txn = self._txn(PaystackTransaction.StatusChoices.FAILED)
        for target in PaystackTransaction.StatusChoices:
            self.assertFalse(txn.can_transition_to(target), target)

    def test_reversed_is_terminal(self):
        txn = self._txn(PaystackTransaction.StatusChoices.REVERSED)
        for target in PaystackTransaction.StatusChoices:
            self.assertFalse(txn.can_transition_to(target), target)


class OwnerDescriptionTests(TestCase):
    """Log lines identify an owner without printing anything sensitive."""

    def test_account_owner_is_described_by_id_only(self):
        user = make_user()
        txn = PaystackTransaction(user=user)
        self.assertEqual(txn.owner_description, f"user:{user.user_id}")

    def test_guest_owner_is_described_by_ownership_id_not_credential(self):
        txn = PaystackTransaction(guest_session_id="abc-123")
        self.assertEqual(txn.owner_description, "guest:abc-123")

    def test_claimed_purchase_reports_its_provenance(self):
        txn = PaystackTransaction(origin_guest_session_id="abc-123")
        self.assertEqual(txn.owner_description, "claimed-guest:abc-123")


class MigrationUpgradeTests(TransactionTestCase):
    """
    Migrate backwards to before the change, write realistic legacy rows, and
    migrate forward again.

    ``TransactionTestCase`` is required: the schema changes must actually commit.
    The class restores the full migration state afterwards so the rest of the
    suite is unaffected.
    """

    available_apps = ["capp", "paystack_app", "django.contrib.contenttypes", "django.contrib.auth"]

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(APP, target)])
        return executor

    def tearDown(self):
        self._migrate(AFTER)
        super().tearDown()

    def _legacy_model(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        return executor.loader.project_state((APP, BEFORE)).apps.get_model(APP, "PaystackTransaction")

    def _forward_model(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        return executor.loader.project_state((APP, AFTER)).apps.get_model(APP, "PaystackTransaction")

    def test_fresh_install_has_the_new_shape(self):
        """The default state of the test database is already fully migrated."""
        columns = {
            col.name
            for col in connection.introspection.get_table_description(connection.cursor(), "paystack_transactions")
        }
        self.assertLessEqual(
            {"package_slug", "package_type", "expected_amount_minor", "callback_path", "fulfillment_state"},
            columns,
        )
        self.assertIn("paystack_fulfillments", connection.introspection.table_names())

    def test_legacy_rows_survive_the_upgrade_with_null_intent(self):
        self._migrate(BEFORE)
        Legacy = self._legacy_model()
        user = make_user()

        Legacy.objects.create(
            user_id=user.user_id,
            reference="legacy_pending",
            amount=Decimal("100.00"),
            currency="KES",
            status="pending",
        )
        Legacy.objects.create(
            guest_session_id="legacy-guest",
            reference="legacy_success",
            amount=Decimal("250.00"),
            currency="KES",
            status="success",
        )
        Legacy.objects.create(
            user_id=user.user_id,
            reference="legacy_failed",
            amount=Decimal("50.00"),
            currency="KES",
            status="failed",
        )

        self._migrate(AFTER)
        Forward = self._forward_model()

        pending = Forward.objects.get(reference="legacy_pending")
        # Not guessed: the price the customer was quoted is not recoverable.
        self.assertIsNone(pending.package_slug)
        self.assertIsNone(pending.expected_amount_minor)
        # Still unfulfilled, so it stays on the reconciliation list rather than
        # being credited on an assumption.
        self.assertEqual(pending.fulfillment_state, "unfulfilled")

        success = Forward.objects.get(reference="legacy_success")
        # Marked fulfilled in the protective direction: a redelivered old webhook
        # now hits the replay guard instead of crediting a second time.
        self.assertEqual(success.fulfillment_state, "fulfilled")
        self.assertIsNone(success.package_slug)

        failed = Forward.objects.get(reference="legacy_failed")
        self.assertEqual(failed.fulfillment_state, "unfulfilled")

    def test_the_upgrade_refuses_to_run_against_a_dual_owned_legacy_row(self):
        """
        A row that cannot satisfy the new constraint stops the migration with an
        explanation, rather than failing later with a raw constraint error.
        """
        self._migrate(BEFORE)
        Legacy = self._legacy_model()
        user = make_user()
        Legacy.objects.create(
            user_id=user.user_id,
            guest_session_id="also-a-guest",
            reference="legacy_dual_owner",
            amount=Decimal("100.00"),
            currency="KES",
            status="success",
        )

        with self.assertRaises(RuntimeError) as ctx:
            self._migrate(AFTER)
        self.assertIn("both a user and a guest_session_id", str(ctx.exception))

        # Clean up so tearDown can complete the upgrade.
        Legacy.objects.filter(reference="legacy_dual_owner").update(guest_session_id=None)

    def test_the_upgrade_refuses_to_run_against_a_non_positive_amount(self):
        self._migrate(BEFORE)
        Legacy = self._legacy_model()
        user = make_user()
        Legacy.objects.create(
            user_id=user.user_id,
            reference="legacy_zero_amount",
            amount=Decimal("0.00"),
            currency="KES",
            status="failed",
        )

        with self.assertRaises(RuntimeError) as ctx:
            self._migrate(AFTER)
        self.assertIn("amount <= 0", str(ctx.exception))

        Legacy.objects.filter(reference="legacy_zero_amount").update(amount=Decimal("1.00"))

    def test_the_change_is_reversible(self):
        self._migrate(BEFORE)
        columns = {
            col.name
            for col in connection.introspection.get_table_description(connection.cursor(), "paystack_transactions")
        }
        self.assertNotIn("package_slug", columns)
        self.assertNotIn("paystack_fulfillments", connection.introspection.table_names())
