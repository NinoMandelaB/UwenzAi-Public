# capp/tests_billing_concurrency.py
"""
Real-concurrency coverage for Spark balances, against a real PostgreSQL server.

These are ``TransactionTestCase``s on purpose. ``TestCase`` wraps each test in a
transaction that is never committed, so a second thread would never see the
first thread's writes and every race below would pass vacuously. Only committed
data and genuinely separate connections can show whether ``select_for_update``
is doing its job.

The bug being pinned down: the guest charge path read a balance, decided it was
sufficient, and wrote the difference back — with no lock. Two requests arriving
together both read the same balance and the second write clobbered the first, so
a guest with enough Sparks for one operation got two. Reserving is now the only
way Sparks leave a balance, so these tests drive it rather than the charge path
it replaced.

Every thread closes its connection in a ``finally``; a leaked connection holding
a row lock deadlocks the whole suite rather than failing one test.
"""

import threading
import uuid

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from capp.classes.billing.balances import lock_guest_quota, lock_spark_pool
from capp.classes.billing.reservation_service import (
    BillingReservationService,
    IdempotencyKeyConflict,
    InsufficientBalance,
)
from capp.classes.characters.character_service import CharacterService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.constants import IMAGE_GENERATION_COST
from capp.models import (
    BillingReservation,
    CharacterGalleryImage,
    GuestTokenQuota,
    SparkPool,
    SparkTransaction,
    UsageCharge,
)
from capp.tests import _bypass_generated_image_screen, _make_character, _make_user
from capp.tests_billing_integration import stub_image_providers

FEATURE = BillingReservation.Feature.IMAGE_GENERATION


def run_in_threads(target, count):
    """
    Run ``target(index)`` in ``count`` threads released simultaneously.

    A barrier is used rather than bare thread starts so the threads actually
    overlap inside the critical section; without it the first can finish before
    the second begins and the race never happens.
    """
    barrier = threading.Barrier(count)
    results = [None] * count

    def wrapped(index):
        try:
            barrier.wait(timeout=30)
            results[index] = ("ok", target(index))
        except Exception as exc:  # noqa: BLE001 - recorded and asserted on
            results[index] = ("error", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for thread in threads:
        assert not thread.is_alive(), "thread did not finish; likely a deadlock"

    return results


def outcomes(results):
    return [status for status, _ in results]


class GuestChargeConcurrencyTests(TransactionTestCase):
    """Reserving on the guest path, from several connections at once."""

    def setUp(self):
        self.identity, _ = GuestIdentityService.mint()
        self.owner_key = self.identity.owner_key

    def _fund(self, sparks):
        GuestTokenQuota.objects.update_or_create(
            session_id=self.owner_key,
            defaults={"sparks": sparks},
        )

    def _balance(self):
        return int(GuestTokenQuota.objects.get(session_id=self.owner_key).sparks)

    def _reserve(self, _index):
        """Reserve 100 Sparks under a key of its own, as a distinct action would."""
        try:
            BillingReservationService.reserve(
                idempotency_key=str(uuid.uuid4()),
                feature=FEATURE,
                amount_sparks=100,
                guest_identity=self.identity,
            )
            return "success"
        except InsufficientBalance:
            return "fail"

    def test_two_concurrent_charges_against_one_affordable_balance(self):
        """The classic lost update: enough for one charge, two callers."""
        self._fund(100)

        results = run_in_threads(self._reserve, 2)

        self.assertEqual(sorted(value for _, value in results), ["fail", "success"])
        self.assertEqual(self._balance(), 0)
        self.assertEqual(UsageCharge.objects.filter(guest_session_id=self.owner_key).count(), 1)
        self.assertEqual(BillingReservation.objects.filter(guest_identity=self.identity).count(), 1)

    def test_concurrent_charges_never_drive_the_balance_negative(self):
        self._fund(250)

        results = run_in_threads(self._reserve, 5)

        self.assertEqual([value for _, value in results].count("success"), 2)
        self.assertEqual(self._balance(), 50)
        self.assertEqual(BillingReservation.objects.filter(guest_identity=self.identity).count(), 2)

    def test_concurrent_first_touch_grants_the_allowance_only_once(self):
        """``get_or_create`` races must not mint a second free allowance."""
        GuestTokenQuota.objects.filter(session_id=self.owner_key).delete()

        def touch(_):
            with transaction.atomic():
                quota = lock_guest_quota(self.owner_key)
                return int(quota.sparks)

        results = run_in_threads(touch, 4)

        self.assertEqual(outcomes(results), ["ok"] * 4)
        self.assertEqual(GuestTokenQuota.objects.filter(session_id=self.owner_key).count(), 1)
        balances = {value for _, value in results}
        self.assertEqual(len(balances), 1, f"racing callers saw different balances: {balances}")


class UserChargeConcurrencyTests(TransactionTestCase):
    """Reserving on the authenticated path, from several connections at once."""

    def setUp(self):
        self.user = _make_user()
        SparkPool.objects.update_or_create(user=self.user, defaults={"sparks": 0})

    def _fund(self, sparks):
        SparkPool.objects.filter(user=self.user).update(sparks=sparks)

    def _balance(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def _reserve(self, _index):
        try:
            BillingReservationService.reserve(
                idempotency_key=str(uuid.uuid4()),
                feature=FEATURE,
                amount_sparks=100,
                user=self.user,
            )
            return "success"
        except InsufficientBalance:
            return "fail"

    def test_two_concurrent_charges_against_one_affordable_balance(self):
        self._fund(100)

        results = run_in_threads(self._reserve, 2)

        self.assertEqual(sorted(value for _, value in results), ["fail", "success"])
        self.assertEqual(self._balance(), 0)
        self.assertEqual(SparkTransaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 1)

    def test_concurrent_pool_creation_does_not_duplicate_the_pool(self):
        SparkPool.objects.filter(user=self.user).delete()

        def touch(_):
            with transaction.atomic():
                return str(lock_spark_pool(self.user).pk)

        results = run_in_threads(touch, 4)

        self.assertEqual(outcomes(results), ["ok"] * 4)
        self.assertEqual(SparkPool.objects.filter(user=self.user).count(), 1)
        self.assertEqual(len({value for _, value in results}), 1)


class ReservationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = _make_user()
        SparkPool.objects.update_or_create(user=self.user, defaults={"sparks": 0})
        self.identity, _ = GuestIdentityService.mint()

    def _fund_user(self, sparks):
        SparkPool.objects.filter(user=self.user).update(sparks=sparks)

    def _fund_guest(self, sparks):
        GuestTokenQuota.objects.update_or_create(
            session_id=self.identity.owner_key,
            defaults={"sparks": sparks},
        )

    def _user_balance(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def _guest_balance(self):
        return int(GuestTokenQuota.objects.get(session_id=self.identity.owner_key).sparks)

    # ---- different keys: genuinely competing spends ----
    def test_two_different_keys_cannot_both_spend_one_affordable_balance(self):
        self._fund_user(100)

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key=f"key-{i}",
                feature=FEATURE,
                amount_sparks=100,
                user=self.user,
            ),
            2,
        )

        self.assertEqual(sorted(outcomes(results)), ["error", "ok"])
        errors = [value for status, value in results if status == "error"]
        self.assertIsInstance(errors[0], InsufficientBalance)

        self.assertEqual(self._user_balance(), 0)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 1)

    def test_guest_different_keys_cannot_both_spend_one_affordable_balance(self):
        self._fund_guest(100)

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key=f"key-{i}",
                feature=FEATURE,
                amount_sparks=100,
                guest_identity=self.identity,
            ),
            2,
        )

        self.assertEqual(sorted(outcomes(results)), ["error", "ok"])
        self.assertEqual(self._guest_balance(), 0)
        self.assertEqual(BillingReservation.objects.filter(guest_identity=self.identity).count(), 1)

    def test_exact_balance_is_fully_spendable_under_contention(self):
        self._fund_user(300)

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key=f"key-{i}",
                feature=FEATURE,
                amount_sparks=100,
                user=self.user,
            ),
            3,
        )

        self.assertEqual(outcomes(results), ["ok"] * 3)
        self.assertEqual(self._user_balance(), 0)

    def test_guest_exact_balance_is_fully_spendable_under_contention(self):
        """
        Locking must not cost a guest their last operation. Three actions and
        exactly three actions' worth of Sparks: all three must go through.
        """
        self._fund_guest(300)

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key=f"key-{i}",
                feature=FEATURE,
                amount_sparks=100,
                guest_identity=self.identity,
            ),
            3,
        )

        self.assertEqual(outcomes(results), ["ok"] * 3)
        self.assertEqual(self._guest_balance(), 0)
        self.assertEqual(BillingReservation.objects.filter(guest_identity=self.identity).count(), 3)

    def test_no_overspend_when_more_callers_than_the_balance_allows(self):
        self._fund_user(250)

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key=f"key-{i}",
                feature=FEATURE,
                amount_sparks=100,
                user=self.user,
            ),
            6,
        )

        succeeded = [value for status, value in results if status == "ok"]
        self.assertEqual(len(succeeded), 2)
        self.assertEqual(self._user_balance(), 50)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 2)

    # ---- same key: idempotent retry ----
    def test_same_key_concurrently_charges_only_once(self):
        self._fund_user(1000)

        results = run_in_threads(
            lambda _: BillingReservationService.reserve(
                idempotency_key="retry-me",
                feature=FEATURE,
                amount_sparks=100,
                user=self.user,
            ),
            5,
        )

        self.assertEqual(outcomes(results), ["ok"] * 5)
        self.assertEqual(len({str(value.pk) for _, value in results}), 1)
        self.assertEqual(self._user_balance(), 900)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 1)
        self.assertEqual(SparkTransaction.objects.filter(user=self.user).count(), 1)

    def test_same_key_concurrently_charges_a_guest_only_once(self):
        self._fund_guest(1000)

        results = run_in_threads(
            lambda _: BillingReservationService.reserve(
                idempotency_key="retry-me",
                feature=FEATURE,
                amount_sparks=100,
                guest_identity=self.identity,
            ),
            5,
        )

        self.assertEqual(outcomes(results), ["ok"] * 5)
        self.assertEqual(self._guest_balance(), 900)
        self.assertEqual(UsageCharge.objects.filter(guest_session_id=self.identity.owner_key).count(), 1)

    def test_conflicting_terms_on_one_key_are_rejected_under_contention(self):
        """Exactly one set of terms wins; the losers are told, not charged."""
        self._fund_user(1000)
        amounts = [100, 200, 300, 400]

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key="same-key",
                feature=FEATURE,
                amount_sparks=amounts[i],
                user=self.user,
            ),
            len(amounts),
        )

        succeeded = [value for status, value in results if status == "ok"]
        failed = [value for status, value in results if status == "error"]
        self.assertEqual(len(succeeded), 1)
        self.assertTrue(all(isinstance(exc, IdempotencyKeyConflict) for exc in failed))

        reservation = BillingReservation.objects.get(user=self.user)
        self.assertEqual(self._user_balance(), 1000 - int(reservation.amount_sparks))

    def test_unique_operation_reference_admits_only_one_reservation(self):
        self._fund_user(1000)

        results = run_in_threads(
            lambda i: BillingReservationService.reserve(
                idempotency_key=f"key-{i}",
                feature=FEATURE,
                amount_sparks=100,
                user=self.user,
                operation_reference="one-artifact",
            ),
            4,
        )

        succeeded = [value for status, value in results if status == "ok"]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(self._user_balance(), 900)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 1)


class DoubleClickArtifactConcurrencyTests(TransactionTestCase):
    """
    The whole point of the key, exercised end to end: a user who clicks twice.

    The reservation tests above prove one charge. These prove the other half —
    one *artifact* — because a second picture that was billed as a replay of the
    first is just as wrong as a second charge, and only a real second connection
    can produce the interleaving where both callers get past the lookup.
    """

    def setUp(self):
        _bypass_generated_image_screen(self)
        self.user = _make_user(username="clicker", email="clicker@example.com", verified=True)
        SparkPool.objects.update_or_create(user=self.user, defaults={"sparks": 10000})
        self.character = _make_character(name="Ada", slug="ada-concurrency", created_by=self.user)

    def _balance(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def _generate(self, operation_id, count):
        with stub_image_providers(b"image-bytes"):
            return run_in_threads(
                lambda _: CharacterService.generate_gallery_image(
                    character=self.character,
                    prompt="a portrait",
                    actor=self.user,
                    is_public=False,
                    operation_id=operation_id,
                ),
                count,
            )

    def test_same_key_twice_produces_one_image_and_one_charge(self):
        before = self._balance()

        results = self._generate(str(uuid.uuid4()), 2)

        self.assertEqual(outcomes(results), ["ok", "ok"])
        self.assertTrue(all(value["status"] == "success" for _, value in results))
        self.assertEqual(len({value["data"]["image"]["id"] for _, value in results}), 1)
        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 1)
        self.assertEqual(self._balance(), before - IMAGE_GENERATION_COST)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 1)

    def test_different_keys_twice_produce_two_images_and_two_charges(self):
        """The control: two genuinely distinct actions must not be deduplicated."""
        before = self._balance()

        with stub_image_providers(b"image-bytes"):
            results = run_in_threads(
                lambda i: CharacterService.generate_gallery_image(
                    character=self.character,
                    prompt=f"portrait {i}",
                    actor=self.user,
                    is_public=False,
                    operation_id=str(uuid.uuid4()),
                ),
                2,
            )

        self.assertEqual(outcomes(results), ["ok", "ok"])
        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 2)
        self.assertEqual(self._balance(), before - 2 * IMAGE_GENERATION_COST)
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 2)

    def tearDown(self):
        connection.close()


class TerminalTransitionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        _bypass_generated_image_screen(self)
        self.user = _make_user()
        SparkPool.objects.update_or_create(user=self.user, defaults={"sparks": 1000})
        self.reservation = BillingReservationService.reserve(
            idempotency_key=str(uuid.uuid4()),
            feature=FEATURE,
            amount_sparks=100,
            user=self.user,
        )

    def _balance(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def test_concurrent_refunds_credit_exactly_once(self):
        results = run_in_threads(
            lambda _: BillingReservationService.refund(self.reservation.pk, reason="provider_failed"),
            5,
        )

        self.assertEqual(outcomes(results), ["ok"] * 5)
        self.assertEqual(self._balance(), 1000)
        self.assertEqual(
            SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.REFUND).count(),
            1,
        )

    def test_concurrent_settles_are_idempotent(self):
        results = run_in_threads(lambda _: BillingReservationService.settle(self.reservation.pk), 5)

        self.assertEqual(outcomes(results), ["ok"] * 5)
        self.assertEqual(self._balance(), 900)
        self.assertEqual(BillingReservation.objects.get(pk=self.reservation.pk).status, "settled")

    def test_a_concurrent_settle_and_refund_cannot_both_win(self):
        """Whichever lands first is terminal; the other is refused, not applied."""

        def act(index):
            if index == 0:
                return BillingReservationService.settle(self.reservation.pk)
            return BillingReservationService.refund(self.reservation.pk)

        run_in_threads(act, 2)

        final = BillingReservation.objects.get(pk=self.reservation.pk)
        self.assertIn(final.status, {"settled", "refunded"})

        expected_balance = 1000 if final.status == "refunded" else 900
        self.assertEqual(self._balance(), expected_balance)

        refund_entries = SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.REFUND).count()
        self.assertEqual(refund_entries, 1 if final.status == "refunded" else 0)

    def tearDown(self):
        connection.close()
