# capp/tests_billing_reservations.py
"""
State-machine coverage for durable Spark reservations.

The failure this suite exists to prevent: paid features used to debit Sparks
after the provider call returned, with nothing durable recording that an
operation was supposed to be billed. A retry double-charged, a crash gave the
work away, and a provider error left the user paid-up with no refund path.

Everything here is about the *transitions*. Whether two concurrent callers can
both spend the same Sparks is a separate question, covered against a real
database in ``tests_billing_concurrency``.
"""

import uuid
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from capp.classes.auth.auth import Auth
from capp.classes.billing.reservation_service import (
    BillingReservationService,
    GuestIdentityNotUsable,
    IdempotencyKeyConflict,
    InsufficientBalance,
    InvalidTransition,
    ReservationError,
)
from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.constants import IMAGE_GENERATION_COST
from capp.models import (
    BillingReservation,
    CustomUser,
    GuestIdentity,
    GuestTokenQuota,
    GuestTransferGrant,
    SparkPool,
    SparkTransaction,
    UsageCharge,
)
from capp.tests import RECAPTCHA_OK, _make_user

FEATURE = BillingReservation.Feature.IMAGE_GENERATION


def _fund_user(user, sparks):
    pool, _ = SparkPool.objects.get_or_create(user=user, defaults={"sparks": 0})
    pool.sparks = sparks
    pool.save(update_fields=["sparks"])
    return pool


def _fund_guest(identity, sparks):
    quota, _ = GuestTokenQuota.objects.get_or_create(session_id=identity.owner_key, defaults={"sparks": 0})
    quota.sparks = sparks
    quota.save(update_fields=["sparks"])
    return quota


def _balance(user):
    return int(SparkPool.objects.get(user=user).sparks)


def _guest_balance(identity):
    return int(GuestTokenQuota.objects.get(session_id=identity.owner_key).sparks)


class ReserveTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        _fund_user(self.user, 1000)

    def test_reserve_debits_once_and_records_ledger(self):
        reservation = BillingReservationService.reserve(
            idempotency_key="op-1",
            feature=FEATURE,
            amount_sparks=IMAGE_GENERATION_COST,
            user=self.user,
        )

        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)
        self.assertEqual(_balance(self.user), 1000 - IMAGE_GENERATION_COST)

        self.assertIsNotNone(reservation.debit_spark_transaction)
        self.assertEqual(reservation.debit_spark_transaction.spark_amount, -IMAGE_GENERATION_COST)
        self.assertEqual(reservation.usage_charge.sparks_charged, IMAGE_GENERATION_COST)

    def test_image_generation_price_is_unchanged(self):
        self.assertEqual(IMAGE_GENERATION_COST, 450)

    def test_duplicate_key_returns_the_same_reservation_without_charging_again(self):
        first = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )
        second = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(_balance(self.user), 900)
        self.assertEqual(BillingReservation.objects.count(), 1)
        self.assertEqual(SparkTransaction.objects.filter(user=self.user).count(), 1)

    def test_replaying_a_key_after_settlement_does_not_reopen_the_operation(self):
        first = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )
        BillingReservationService.settle(first)

        replay = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )

        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(replay.status, BillingReservation.Status.SETTLED)
        self.assertEqual(_balance(self.user), 900)

    def test_reusing_a_key_for_a_different_amount_is_rejected(self):
        BillingReservationService.reserve(idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user)

        with self.assertRaises(IdempotencyKeyConflict):
            BillingReservationService.reserve(
                idempotency_key="op-1", feature=FEATURE, amount_sparks=250, user=self.user
            )
        self.assertEqual(_balance(self.user), 900)

    def test_reusing_a_key_for_a_different_feature_is_rejected(self):
        BillingReservationService.reserve(idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user)

        with self.assertRaises(IdempotencyKeyConflict):
            BillingReservationService.reserve(
                idempotency_key="op-1",
                feature=BillingReservation.Feature.MESSAGE_PAIR,
                amount_sparks=100,
                user=self.user,
            )

    def test_same_key_for_two_principals_is_not_a_conflict(self):
        other = _make_user(username="bob", email="bob@example.com")
        _fund_user(other, 1000)

        mine = BillingReservationService.reserve(
            idempotency_key="shared", feature=FEATURE, amount_sparks=100, user=self.user
        )
        theirs = BillingReservationService.reserve(
            idempotency_key="shared", feature=FEATURE, amount_sparks=100, user=other
        )

        self.assertNotEqual(mine.pk, theirs.pk)
        self.assertEqual(_balance(self.user), 900)
        self.assertEqual(_balance(other), 900)

    def test_insufficient_balance_writes_nothing_at_all(self):
        _fund_user(self.user, 10)

        with self.assertRaises(InsufficientBalance):
            BillingReservationService.reserve(
                idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
            )

        self.assertEqual(_balance(self.user), 10)
        self.assertFalse(BillingReservation.objects.exists())
        self.assertFalse(UsageCharge.objects.exists())
        self.assertFalse(SparkTransaction.objects.exists())

    def test_exact_balance_is_spendable(self):
        _fund_user(self.user, 100)

        BillingReservationService.reserve(idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user)
        self.assertEqual(_balance(self.user), 0)

    def test_reserve_creates_a_missing_spark_pool(self):
        fresh = _make_user(username="carol", email="carol@example.com")
        SparkPool.objects.filter(user=fresh).delete()
        _fund_user(fresh, 500)
        SparkPool.objects.filter(user=fresh).update(sparks=500)

        BillingReservationService.reserve(idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=fresh)
        self.assertEqual(_balance(fresh), 400)

    def test_principal_must_be_exactly_one(self):
        identity, _ = GuestIdentityService.mint()

        with self.assertRaises(ReservationError):
            BillingReservationService.reserve(idempotency_key="k", feature=FEATURE, amount_sparks=1)
        with self.assertRaises(ReservationError):
            BillingReservationService.reserve(
                idempotency_key="k",
                feature=FEATURE,
                amount_sparks=1,
                user=self.user,
                guest_identity=identity,
            )

    def test_non_positive_amount_is_rejected(self):
        for amount in (0, -5):
            with self.assertRaises(ReservationError):
                BillingReservationService.reserve(
                    idempotency_key=f"k{amount}", feature=FEATURE, amount_sparks=amount, user=self.user
                )

    def test_operation_reference_cannot_be_reused(self):
        BillingReservationService.reserve(
            idempotency_key="op-1",
            feature=FEATURE,
            amount_sparks=100,
            user=self.user,
            operation_reference="artifact-42",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservationService.reserve(
                    idempotency_key="op-2",
                    feature=FEATURE,
                    amount_sparks=100,
                    user=self.user,
                    operation_reference="artifact-42",
                )


class GuestReserveTests(TestCase):
    def setUp(self):
        self.identity, _ = GuestIdentityService.mint()
        _fund_guest(self.identity, 1000)

    def test_guest_reserve_debits_the_quota_and_records_provenance(self):
        reservation = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, guest_identity=self.identity
        )

        self.assertEqual(_guest_balance(self.identity), 900)
        self.assertEqual(reservation.origin_guest_session_id, self.identity.owner_key)
        self.assertIsNone(reservation.user_id)
        self.assertIsNone(reservation.debit_spark_transaction)
        self.assertEqual(reservation.usage_charge.guest_session_id, self.identity.owner_key)

    def test_guest_insufficient_balance_writes_nothing(self):
        _fund_guest(self.identity, 10)

        with self.assertRaises(InsufficientBalance):
            BillingReservationService.reserve(
                idempotency_key="op-1", feature=FEATURE, amount_sparks=100, guest_identity=self.identity
            )
        self.assertEqual(_guest_balance(self.identity), 10)
        self.assertFalse(BillingReservation.objects.exists())

    def test_expired_identity_cannot_reserve(self):
        self.identity.expires_at = timezone.now() - timedelta(seconds=1)
        self.identity.save(update_fields=["expires_at"])

        with self.assertRaises(GuestIdentityNotUsable):
            BillingReservationService.reserve(
                idempotency_key="op-1", feature=FEATURE, amount_sparks=100, guest_identity=self.identity
            )
        self.assertEqual(_guest_balance(self.identity), 1000)

    def test_revoked_identity_cannot_reserve(self):
        GuestIdentityService.revoke(self.identity)
        self.identity.refresh_from_db()

        with self.assertRaises(GuestIdentityNotUsable):
            BillingReservationService.reserve(
                idempotency_key="op-1", feature=FEATURE, amount_sparks=100, guest_identity=self.identity
            )

    def test_claimed_identity_cannot_reserve(self):
        self.identity.status = GuestIdentity.Status.CLAIMED
        self.identity.claimed_at = timezone.now()
        self.identity.save(update_fields=["status", "claimed_at"])

        with self.assertRaises(GuestIdentityNotUsable):
            BillingReservationService.reserve(
                idempotency_key="op-1", feature=FEATURE, amount_sparks=100, guest_identity=self.identity
            )


class SettleTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        _fund_user(self.user, 1000)
        self.reservation = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )

    def test_settle_is_terminal_and_does_not_charge_again(self):
        settled = BillingReservationService.settle(self.reservation, reason="provider_ok")

        self.assertEqual(settled.status, BillingReservation.Status.SETTLED)
        self.assertIsNotNone(settled.settled_at)
        self.assertEqual(settled.settlement_reason, "provider_ok")
        self.assertEqual(_balance(self.user), 900)

    def test_settling_twice_is_a_no_op(self):
        first = BillingReservationService.settle(self.reservation)
        second = BillingReservationService.settle(self.reservation)

        self.assertEqual(first.settled_at, second.settled_at)
        self.assertEqual(_balance(self.user), 900)
        self.assertEqual(SparkTransaction.objects.filter(user=self.user).count(), 1)

    def test_settling_a_refunded_reservation_is_rejected(self):
        BillingReservationService.refund(self.reservation)

        with self.assertRaises(InvalidTransition):
            BillingReservationService.settle(self.reservation)
        self.assertEqual(_balance(self.user), 1000)

    def test_settle_accepts_a_bare_id(self):
        settled = BillingReservationService.settle(self.reservation.pk)
        self.assertEqual(settled.status, BillingReservation.Status.SETTLED)

    def test_unknown_reservation_is_rejected(self):
        with self.assertRaises(ReservationError):
            BillingReservationService.settle(uuid.uuid4())


class RefundTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        _fund_user(self.user, 1000)
        self.reservation = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )

    def test_refund_credits_back_exactly_once(self):
        refunded = BillingReservationService.refund(self.reservation, reason="provider_failed")

        self.assertEqual(refunded.status, BillingReservation.Status.REFUNDED)
        self.assertEqual(refunded.refund_reason, "provider_failed")
        self.assertEqual(_balance(self.user), 1000)

        credit = refunded.refund_spark_transaction
        self.assertEqual(credit.entry_type, SparkTransaction.EntryType.CREDIT)
        self.assertEqual(credit.source, SparkTransaction.SourceChoices.REFUND)
        self.assertEqual(credit.spark_amount, 100)

    def test_refunding_twice_credits_only_once(self):
        first = BillingReservationService.refund(self.reservation)
        second = BillingReservationService.refund(self.reservation)

        self.assertEqual(first.refunded_at, second.refunded_at)
        self.assertEqual(_balance(self.user), 1000)
        self.assertEqual(
            SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.REFUND).count(),
            1,
        )

    def test_settled_reservations_cannot_be_refunded(self):
        BillingReservationService.settle(self.reservation)

        with self.assertRaises(InvalidTransition):
            BillingReservationService.refund(self.reservation)
        self.assertEqual(_balance(self.user), 900)

    def test_guest_refund_credits_the_quota(self):
        identity, _ = GuestIdentityService.mint()
        _fund_guest(identity, 500)
        reservation = BillingReservationService.reserve(
            idempotency_key="g-1", feature=FEATURE, amount_sparks=100, guest_identity=identity
        )

        BillingReservationService.refund(reservation, reason="provider_failed")
        self.assertEqual(_guest_balance(identity), 500)

    def test_expired_guest_can_still_be_refunded(self):
        identity, _ = GuestIdentityService.mint()
        _fund_guest(identity, 500)
        reservation = BillingReservationService.reserve(
            idempotency_key="g-1", feature=FEATURE, amount_sparks=100, guest_identity=identity
        )

        identity.expires_at = timezone.now() - timedelta(seconds=1)
        identity.save(update_fields=["expires_at"])

        BillingReservationService.refund(reservation, reason="provider_failed")
        self.assertEqual(_guest_balance(identity), 500)


class ClaimProvenanceTests(TestCase):
    """A claim adds provenance to a guest reservation; it never rewrites it."""

    def setUp(self):
        self.user = _make_user()
        self.identity, _ = GuestIdentityService.mint()
        _fund_guest(self.identity, 1000)
        self.reservation = BillingReservationService.reserve(
            idempotency_key="g-1", feature=FEATURE, amount_sparks=100, guest_identity=self.identity
        )
        # Stands in for signup: without it the account is not entitled to this
        # guest session and nothing would be transferred.
        GuestTransferGrant.objects.create(
            user=self.user,
            guest_identity=self.identity,
            status=GuestTransferGrant.Status.PENDING,
            origin_guest_session_id=self.identity.owner_key,
        )

    def test_claim_stamps_the_reservation_without_moving_the_principal(self):
        result = GuestClaimService.claim(self.user, self.identity)
        self.assertTrue(result["claimed"])
        self.assertEqual(result["reservations_claimed"], 1)

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.guest_identity_id, self.identity.pk)
        self.assertIsNone(self.reservation.user_id)
        self.assertEqual(self.reservation.claimed_by_id, self.user.pk)
        self.assertIsNotNone(self.reservation.claimed_from_guest_at)
        self.assertEqual(self.reservation.origin_guest_session_id, self.identity.owner_key)

    def test_refund_after_a_claim_credits_the_account_not_the_zeroed_quota(self):
        GuestClaimService.claim(self.user, self.identity)
        # The claim moved the guest's 900 remaining sparks into the account.
        self.assertEqual(_balance(self.user), 900)
        self.assertEqual(_guest_balance(self.identity), 0)

        self.reservation.refresh_from_db()
        BillingReservationService.refund(self.reservation, reason="provider_failed")

        self.assertEqual(_balance(self.user), 1000)
        self.assertEqual(_guest_balance(self.identity), 0)

    def test_refund_after_claim_is_still_idempotent(self):
        GuestClaimService.claim(self.user, self.identity)
        self.reservation.refresh_from_db()

        BillingReservationService.refund(self.reservation)
        BillingReservationService.refund(self.reservation)

        self.assertEqual(_balance(self.user), 1000)


class ClaimerErasureTests(TestCase):
    """Erasing an account must not be blocked by the reservations it claimed.

    Django's deletion collector SET_NULLs ``claimed_by`` on every reservation
    the deleted account claimed, leaving the guest provenance behind. The
    original two-branch CHECK refused that row, so the whole deletion rolled
    back and nobody who had ever converted from a guest could close their
    account.
    """

    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.other = _make_user(username="bob", email="bob@example.com")

    def _guest_reservation_claimed_by(self, claimer, key="g-1"):
        identity, _ = GuestIdentityService.mint()
        _fund_guest(identity, 1000)
        reservation = BillingReservationService.reserve(
            idempotency_key=key, feature=FEATURE, amount_sparks=100, guest_identity=identity
        )
        BillingReservation.objects.filter(pk=reservation.pk).update(
            claimed_by=claimer, claimed_from_guest_at=timezone.now()
        )
        reservation.refresh_from_db()
        return reservation, identity

    def test_deleting_the_claimer_keeps_the_reservation_and_its_provenance(self):
        reservation, identity = self._guest_reservation_claimed_by(self.user)
        claimed_at = reservation.claimed_from_guest_at

        result = Auth.hard_delete_user(self.user)

        self.assertEqual(result["status"], "success")
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())

        reservation.refresh_from_db()
        self.assertIsNone(reservation.claimed_by_id)
        self.assertEqual(reservation.claimed_from_guest_at, claimed_at)
        self.assertEqual(reservation.guest_identity_id, identity.pk)
        self.assertEqual(reservation.origin_guest_session_id, identity.owner_key)
        self.assertEqual(reservation.amount_sparks, 100)

    def test_deleting_the_claimer_leaves_another_accounts_rows_alone(self):
        mine, _ = self._guest_reservation_claimed_by(self.user, key="g-mine")
        theirs, their_identity = self._guest_reservation_claimed_by(self.other, key="g-theirs")
        # A reservation the surviving account owns outright — no claim at all.
        _fund_user(self.other, 1000)
        plain = BillingReservationService.reserve(
            idempotency_key="u-theirs", feature=FEATURE, amount_sparks=50, user=self.other
        )

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")

        mine.refresh_from_db()
        theirs.refresh_from_db()
        plain.refresh_from_db()
        self.assertIsNone(mine.claimed_by_id)
        self.assertEqual(theirs.claimed_by_id, self.other.pk)
        self.assertEqual(theirs.guest_identity_id, their_identity.pk)
        self.assertEqual(plain.user_id, self.other.pk)

    def test_deleting_the_claimer_of_an_identity_owned_by_someone_else(self):
        # Cross-user shape: the reservation's guest identity was claimed by
        # another account while this reservation carries our user's stamp.
        reservation, identity = self._guest_reservation_claimed_by(self.user)
        GuestIdentity.objects.filter(pk=identity.pk).update(
            status=GuestIdentity.Status.CLAIMED, claimed_at=timezone.now(), claimed_by=self.other
        )

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")

        reservation.refresh_from_db()
        identity.refresh_from_db()
        self.assertIsNone(reservation.claimed_by_id)
        self.assertIsNotNone(reservation.claimed_from_guest_at)
        self.assertEqual(identity.claimed_by_id, self.other.pk)

    def test_deleting_the_claimer_cascades_their_own_reservations(self):
        claimed, _ = self._guest_reservation_claimed_by(self.user)
        _fund_user(self.user, 1000)
        own = BillingReservationService.reserve(
            idempotency_key="u-mine", feature=FEATURE, amount_sparks=50, user=self.user
        )

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")

        self.assertFalse(BillingReservation.objects.filter(pk=own.pk).exists())
        self.assertTrue(BillingReservation.objects.filter(pk=claimed.pk).exists())

    def test_deleting_an_account_after_a_real_guest_transfer_claim(self):
        identity, _ = GuestIdentityService.mint()
        _fund_guest(identity, 1000)
        reservation = BillingReservationService.reserve(
            idempotency_key="g-real", feature=FEATURE, amount_sparks=100, guest_identity=identity
        )
        GuestTransferGrant.objects.create(
            user=self.user,
            guest_identity=identity,
            status=GuestTransferGrant.Status.PENDING,
            origin_guest_session_id=identity.owner_key,
        )
        GuestClaimService.claim(self.user, identity)
        reservation.refresh_from_db()
        self.assertEqual(reservation.claimed_by_id, self.user.pk)

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")

        reservation.refresh_from_db()
        self.assertIsNone(reservation.claimed_by_id)
        self.assertIsNotNone(reservation.claimed_from_guest_at)
        self.assertEqual(reservation.guest_identity_id, identity.pk)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_delete_account_endpoint_succeeds_for_a_converted_account(self, _recaptcha):
        self._guest_reservation_claimed_by(self.user)
        signin = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": self.user.username, "password": "pw123456"},
            content_type="application/json",
        )
        token = signin.json()["data"]["access_token"]

        resp = self.client.delete("/api/v1/auth/delete-account/", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["message"], "user_permanently_deleted")
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())

    def test_guest_identity_behind_a_claimed_reservation_stays_protected(self):
        _, identity = self._guest_reservation_claimed_by(self.user)
        Auth.hard_delete_user(self.user)

        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                identity.delete()

        self.assertTrue(GuestIdentity.objects.filter(pk=identity.pk).exists())


class ConstraintTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_database_rejects_a_reservation_with_no_principal(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(idempotency_key="k", feature=FEATURE, amount_sparks=1)

    def test_database_rejects_two_principals(self):
        identity, _ = GuestIdentityService.mint()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(
                    idempotency_key="k",
                    feature=FEATURE,
                    amount_sparks=1,
                    user=self.user,
                    guest_identity=identity,
                )

    def test_database_rejects_a_non_positive_amount(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(idempotency_key="k", feature=FEATURE, amount_sparks=0, user=self.user)

    def test_database_rejects_status_disagreeing_with_timestamps(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(
                    idempotency_key="k",
                    feature=FEATURE,
                    amount_sparks=1,
                    user=self.user,
                    status=BillingReservation.Status.SETTLED,
                )

    def test_database_rejects_settled_and_refunded_at_once(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(
                    idempotency_key="k",
                    feature=FEATURE,
                    amount_sparks=1,
                    user=self.user,
                    status=BillingReservation.Status.SETTLED,
                    settled_at=now,
                    refunded_at=now,
                )

    def test_database_rejects_a_claim_stamp_on_a_user_reservation(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(
                    idempotency_key="k",
                    feature=FEATURE,
                    amount_sparks=1,
                    user=self.user,
                    claimed_by=self.user,
                    claimed_from_guest_at=timezone.now(),
                )

    def test_database_rejects_a_partial_claim_stamp(self):
        identity, _ = GuestIdentityService.mint()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(
                    idempotency_key="k",
                    feature=FEATURE,
                    amount_sparks=1,
                    guest_identity=identity,
                    claimed_by=self.user,
                )

    def test_database_rejects_a_claim_timestamp_without_a_guest_identity(self):
        # Third branch requires the guest provenance to be *complete*: an
        # erased claimer still has to leave the origin identity behind.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.create(
                    idempotency_key="k",
                    feature=FEATURE,
                    amount_sparks=1,
                    user=self.user,
                    claimed_from_guest_at=timezone.now(),
                )

    def test_database_rejects_clearing_the_guest_identity_of_an_erased_claim(self):
        identity, _ = GuestIdentityService.mint()
        reservation = BillingReservation.objects.create(
            idempotency_key="k",
            feature=FEATURE,
            amount_sparks=1,
            guest_identity=identity,
            claimed_by=self.user,
            claimed_from_guest_at=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.filter(pk=reservation.pk).update(claimed_by=None, guest_identity=None)

    def test_database_rejects_dropping_only_the_claim_timestamp(self):
        identity, _ = GuestIdentityService.mint()
        reservation = BillingReservation.objects.create(
            idempotency_key="k",
            feature=FEATURE,
            amount_sparks=1,
            guest_identity=identity,
            claimed_by=self.user,
            claimed_from_guest_at=timezone.now(),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BillingReservation.objects.filter(pk=reservation.pk).update(claimed_from_guest_at=None)

    def test_database_accepts_a_claim_whose_claimer_was_erased(self):
        identity, _ = GuestIdentityService.mint()
        reservation = BillingReservation.objects.create(
            idempotency_key="k",
            feature=FEATURE,
            amount_sparks=1,
            guest_identity=identity,
            claimed_by=self.user,
            claimed_from_guest_at=timezone.now(),
        )

        BillingReservation.objects.filter(pk=reservation.pk).update(claimed_by=None)

        reservation.refresh_from_db()
        self.assertIsNone(reservation.claimed_by_id)
        self.assertIsNotNone(reservation.claimed_from_guest_at)
        self.assertEqual(reservation.guest_identity_id, identity.pk)

    def test_database_rejects_a_negative_spark_pool(self):
        pool, _ = SparkPool.objects.get_or_create(user=self.user, defaults={"sparks": 0})
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SparkPool.objects.filter(pk=pool.pk).update(sparks=-1)

    def test_database_rejects_a_negative_guest_quota(self):
        quota = GuestTokenQuota.objects.create(session_id="neg", sparks=0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GuestTokenQuota.objects.filter(pk=quota.pk).update(sparks=-1)


class StaleReservationCommandTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        _fund_user(self.user, 1000)
        self.reservation = BillingReservationService.reserve(
            idempotency_key="op-1", feature=FEATURE, amount_sparks=100, user=self.user
        )

    def _age(self, minutes):
        BillingReservation.objects.filter(pk=self.reservation.pk).update(
            reserved_at=timezone.now() - timedelta(minutes=minutes)
        )

    def _run(self, *args):
        out = StringIO()
        call_command("billing_reservations", *args, stdout=out)
        return out.getvalue()

    def test_requires_an_action(self):
        with self.assertRaises(CommandError):
            self._run()

    def test_recent_reservations_are_not_listed_as_stale(self):
        output = self._run("--list-stale", "--older-than-minutes", "60")
        self.assertIn("No reservations", output)

    def test_stale_reservations_are_listed(self):
        self._age(120)
        output = self._run("--list-stale", "--older-than-minutes", "60")
        self.assertIn(str(self.reservation.pk), output)
        self.assertIn("image_generation", output)

    def test_settled_reservations_are_never_listed_as_stale(self):
        self._age(120)
        BillingReservationService.settle(self.reservation)

        output = self._run("--list-stale", "--older-than-minutes", "60")
        self.assertIn("No reservations", output)

    def test_refund_without_confirm_is_a_dry_run(self):
        output = self._run("--refund", str(self.reservation.pk))

        self.assertIn("DRY RUN", output)
        self.assertEqual(_balance(self.user), 900)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, BillingReservation.Status.RESERVED)

    def test_refund_with_confirm_credits_the_balance(self):
        output = self._run("--refund", str(self.reservation.pk), "--confirm")

        self.assertIn("Refunded", output)
        self.assertEqual(_balance(self.user), 1000)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, BillingReservation.Status.REFUNDED)

    def test_refunding_a_settled_reservation_is_refused(self):
        BillingReservationService.settle(self.reservation)

        with self.assertRaises(CommandError):
            self._run("--refund", str(self.reservation.pk), "--confirm")
        self.assertEqual(_balance(self.user), 900)

    def test_unknown_id_is_refused(self):
        with self.assertRaises(CommandError):
            self._run("--refund", str(uuid.uuid4()), "--confirm")
