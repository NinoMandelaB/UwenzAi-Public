"""
Reserve / settle / refund for Spark-billed operations.

The problem this solves: every paid feature today debits Sparks *after* the
provider call succeeds. If the debit fails, or the process dies between the two,
the user got paid work for free — and if the provider fails after a debit, the
user paid for nothing. Neither is recoverable, because nothing durable records
that an operation was supposed to be billed.

The flow this module replaces that with::

    reservation = BillingReservationService.reserve(
        idempotency_key=..., feature=..., user=... or guest_identity=...,
    )
    # ── no DB locks are held here ──
    try:
        result = call_the_provider()
    except Exception:
        BillingReservationService.refund(reservation, reason="provider_failed")
        raise
    BillingReservationService.settle(reservation, reason="ok")

Sparks leave the balance at ``reserve``. ``settle`` only finalises that debit; it
never charges again. So a retry that reuses the same idempotency key gets the
existing reservation back and cannot double-charge, and a crash between the
provider call and ``settle`` leaves a durable ``reserved`` row for
``manage.py billing_reservations`` to surface rather than a silent loss.

Locking rules
-------------
Each of the three entry points is its own short transaction that takes the
balance row lock, writes, and commits. No lock is held across a provider call,
and no lock is held across the WebSocket balance push (which is a network write,
and is therefore deferred to ``transaction.on_commit``).
"""

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from capp.classes.billing.balances import (
    InsufficientSparks,
    credit_locked,
    debit_locked,
    lock_guest_quota,
    lock_spark_pool,
)
from capp.classes.billing import subscription_accounting
from capp.constants import SPARK_BASE_VALUE_KES
from capp.models import (
    BillingReservation,
    GuestIdentity,
    SparkTransaction,
    UsageCharge,
)

logger = logging.getLogger(__name__)


class ReservationError(Exception):
    """Base class for reservation failures that callers are expected to handle."""

    code = "reservation_error"

    def __init__(self, message: str = None, **context):
        self.context = context
        super().__init__(message or self.code)


class InsufficientBalance(ReservationError):
    code = "insufficient_balance"


class GuestIdentityNotUsable(ReservationError):
    """The guest identity is expired, revoked or already claimed."""

    code = "guest_identity_not_usable"


class IdempotencyKeyConflict(ReservationError):
    """The key already exists for this principal with different terms."""

    code = "idempotency_key_conflict"


class InvalidTransition(ReservationError):
    code = "invalid_transition"


class BillingReservationService:
    # ------------------------------------------------------------------
    # reserve
    # ------------------------------------------------------------------
    @classmethod
    def reserve(
        cls,
        *,
        idempotency_key: str,
        feature: str,
        amount_sparks: int,
        user=None,
        guest_identity: GuestIdentity = None,
        operation_reference: str = None,
    ) -> BillingReservation:
        """
        Debit ``amount_sparks`` and record the hold.

        Replaying the same ``(idempotency_key, principal)`` returns the existing
        reservation untouched — including when it has already settled or been
        refunded, so a late retry cannot resurrect a closed operation. Reusing
        the key with a different feature or amount raises
        :class:`IdempotencyKeyConflict` rather than quietly charging the old
        terms.

        On insufficient balance nothing is written at all: no reservation row,
        no ledger row, and the caller must not proceed to the provider.
        """
        cls._validate_principal(user, guest_identity)
        if amount_sparks <= 0:
            raise ReservationError("amount_sparks must be positive", amount_sparks=amount_sparks)
        if not idempotency_key:
            raise ReservationError("idempotency_key is required")

        existing = cls._find_existing(idempotency_key, user, guest_identity)
        if existing is not None:
            return cls._assert_compatible(existing, feature, amount_sparks)

        # Reserving is the only operation that requires a *live* guest. Settle
        # and refund stay available afterwards, because refusing to refund an
        # expired guest would keep money for work that never happened.
        if guest_identity is not None and not guest_identity.is_usable():
            raise GuestIdentityNotUsable(guest_identity_id=str(guest_identity.pk))

        try:
            with transaction.atomic():
                reservation = cls._create_and_debit(
                    idempotency_key=idempotency_key,
                    feature=feature,
                    amount_sparks=amount_sparks,
                    user=user,
                    guest_identity=guest_identity,
                    operation_reference=operation_reference,
                )
        except InsufficientSparks as exc:
            raise InsufficientBalance(required=exc.required, balance=exc.balance) from exc
        except IntegrityError:
            # Lost a race on the per-principal unique key. The winner's row is
            # the reservation; return it if its terms match.
            existing = cls._find_existing(idempotency_key, user, guest_identity)
            if existing is None:
                raise
            return cls._assert_compatible(existing, feature, amount_sparks)

        cls._push_balance_after_commit(user, guest_identity)
        logger.info(
            "Reserved %d sparks for %s feature=%s reservation=%s",
            amount_sparks,
            reservation.principal_label,
            feature,
            reservation.reservation_id,
        )
        return reservation

    @classmethod
    def _create_and_debit(
        cls,
        *,
        idempotency_key,
        feature,
        amount_sparks,
        user,
        guest_identity,
        operation_reference,
    ) -> BillingReservation:
        now = timezone.now()
        amount_charged = Decimal(amount_sparks) * Decimal(str(SPARK_BASE_VALUE_KES))
        description = f"Reservation for {feature.replace('_', ' ')}"

        if user is not None:
            pool = lock_spark_pool(user)
            balance_before = int(pool.sparks)
            balance_after = debit_locked(pool, amount_sparks)
            subscription_metadata = subscription_accounting.apply(user, feature, amount_sparks)

            spark_transaction = SparkTransaction.objects.create(
                spark_pool=pool,
                user=user,
                entry_type=SparkTransaction.EntryType.DEBIT,
                source=SparkTransaction.SourceChoices.USAGE,
                spark_amount=-amount_sparks,
                sparks_before=balance_before,
                sparks_after=balance_after,
                description=description,
                metadata={
                    "idempotency_key": idempotency_key,
                    "feature": feature,
                    **subscription_metadata,
                },
            )
            usage_charge = UsageCharge.objects.create(
                user=user,
                spark_transaction=spark_transaction,
                charge_source=UsageCharge.ChargeSource.SPARK_POOL,
                sparks_charged=amount_sparks,
                amount_charged=amount_charged,
            )
            origin_guest_session_id = None
        else:
            owner_key = guest_identity.owner_key
            quota = lock_guest_quota(owner_key)
            debit_locked(quota, amount_sparks)

            spark_transaction = None
            usage_charge = UsageCharge.objects.create(
                user=None,
                guest_session_id=owner_key,
                charge_source=UsageCharge.ChargeSource.FREE_TIER,
                sparks_charged=amount_sparks,
                amount_charged=amount_charged,
            )
            origin_guest_session_id = owner_key

        return BillingReservation.objects.create(
            idempotency_key=idempotency_key,
            user=user,
            guest_identity=guest_identity,
            feature=feature,
            amount_sparks=amount_sparks,
            status=BillingReservation.Status.RESERVED,
            reserved_at=now,
            operation_reference=operation_reference,
            origin_guest_session_id=origin_guest_session_id,
            debit_spark_transaction=spark_transaction,
            usage_charge=usage_charge,
        )

    # ------------------------------------------------------------------
    # settle
    # ------------------------------------------------------------------
    @classmethod
    def settle(cls, reservation, *, reason: str = None) -> BillingReservation:
        """
        Mark a reservation final. Idempotent; never charges again.

        Settling an already-settled reservation is a no-op that returns the same
        row. Settling a refunded one raises :class:`InvalidTransition`, because
        the Sparks have been given back and there is nothing left to finalise.
        """
        reservation_id = getattr(reservation, "pk", reservation)

        with transaction.atomic():
            locked = cls._lock(reservation_id)

            if locked.status == BillingReservation.Status.SETTLED:
                return locked
            if locked.status != BillingReservation.Status.RESERVED:
                raise InvalidTransition(
                    f"Cannot settle a {locked.status} reservation",
                    reservation_id=str(reservation_id),
                    status=locked.status,
                )

            locked.status = BillingReservation.Status.SETTLED
            locked.settled_at = timezone.now()
            locked.settlement_reason = reason
            locked.save(update_fields=["status", "settled_at", "settlement_reason", "updated_at"])

        logger.info("Settled reservation %s (%s)", locked.reservation_id, reason or "no reason given")
        return locked

    # ------------------------------------------------------------------
    # refund
    # ------------------------------------------------------------------
    @classmethod
    def refund(cls, reservation, *, reason: str = None) -> BillingReservation:
        """
        Credit the reserved Sparks back exactly once.

        Idempotent: refunding an already-refunded reservation returns the same
        row without crediting twice. Refunding a *settled* reservation raises
        :class:`InvalidTransition` — a completed operation is not reversible
        through this path, and reversing one is an administrative action with
        its own audit trail rather than something a request handler may do.

        Where the Sparks land follows ownership at refund time. If the guest was
        claimed since reserving, its quota balance was already moved into the
        account, so the credit goes to that account's pool; refunding into the
        zeroed quota would strand it somewhere nobody can spend.
        """
        reservation_id = getattr(reservation, "pk", reservation)

        with transaction.atomic():
            locked = cls._lock(reservation_id)

            if locked.status == BillingReservation.Status.REFUNDED:
                return locked
            if locked.status != BillingReservation.Status.RESERVED:
                raise InvalidTransition(
                    f"Cannot refund a {locked.status} reservation",
                    reservation_id=str(reservation_id),
                    status=locked.status,
                )

            credited_user, credited_guest = cls._credit_back(locked)

            locked.status = BillingReservation.Status.REFUNDED
            locked.refunded_at = timezone.now()
            locked.refund_reason = reason
            locked.save(
                update_fields=[
                    "status",
                    "refunded_at",
                    "refund_reason",
                    "refund_spark_transaction",
                    "updated_at",
                ]
            )

        cls._push_balance_after_commit(credited_user, credited_guest)
        logger.info(
            "Refunded %d sparks for reservation %s (%s)",
            locked.amount_sparks,
            locked.reservation_id,
            reason or "no reason given",
        )
        return locked

    @classmethod
    def _credit_back(cls, reservation) -> tuple:
        """Return ``(user, guest_identity)`` whose balance was credited."""
        amount = int(reservation.amount_sparks)

        beneficiary = reservation.user
        if beneficiary is None and reservation.claimed_by_id is not None:
            beneficiary = reservation.claimed_by

        if beneficiary is not None:
            pool = lock_spark_pool(beneficiary)
            balance_before = int(pool.sparks)
            balance_after = credit_locked(pool, amount)
            subscription_accounting.reverse(reservation)

            reservation.refund_spark_transaction = SparkTransaction.objects.create(
                spark_pool=pool,
                user=beneficiary,
                entry_type=SparkTransaction.EntryType.CREDIT,
                source=SparkTransaction.SourceChoices.REFUND,
                spark_amount=amount,
                sparks_before=balance_before,
                sparks_after=balance_after,
                description=f"Refund for {reservation.feature.replace('_', ' ')}",
                metadata={
                    "reservation_id": str(reservation.reservation_id),
                    "origin_guest_session_id": reservation.origin_guest_session_id,
                },
            )
            return beneficiary, None

        quota = lock_guest_quota(reservation.guest_identity.owner_key)
        credit_locked(quota, amount)
        return None, reservation.guest_identity

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_principal(user, guest_identity) -> None:
        if (user is None) == (guest_identity is None):
            raise ReservationError("Exactly one of user or guest_identity is required")

    @staticmethod
    def _lock(reservation_id) -> BillingReservation:
        # No select_related: the principal FKs are nullable, so joining them
        # would make this the nullable side of an outer join, which PostgreSQL
        # refuses to lock.
        locked = BillingReservation.objects.select_for_update().filter(pk=reservation_id).first()
        if locked is None:
            raise ReservationError("Reservation not found", reservation_id=str(reservation_id))
        return locked

    @staticmethod
    def _find_existing(idempotency_key, user, guest_identity):
        query = BillingReservation.objects.filter(idempotency_key=idempotency_key)
        if user is not None:
            return query.filter(user=user).first()
        return query.filter(guest_identity=guest_identity).first()

    @staticmethod
    def _assert_compatible(existing, feature, amount_sparks) -> BillingReservation:
        if existing.feature != feature or int(existing.amount_sparks) != int(amount_sparks):
            raise IdempotencyKeyConflict(
                "Idempotency key already used with different terms",
                reservation_id=str(existing.reservation_id),
                existing_feature=existing.feature,
                existing_amount=int(existing.amount_sparks),
                requested_feature=feature,
                requested_amount=int(amount_sparks),
            )
        return existing

    @staticmethod
    def _push_balance_after_commit(user, guest_identity) -> None:
        """Defer the WebSocket push until the balance it reports is committed."""
        from capp.classes.billing.charge_service import (
            _push_guest_spark_pool_balance,
            _push_spark_pool_balance,
        )

        if user is not None:
            transaction.on_commit(lambda: _push_spark_pool_balance(user))
        elif guest_identity is not None:
            owner_key = guest_identity.owner_key
            transaction.on_commit(lambda: _push_guest_spark_pool_balance(owner_key))
