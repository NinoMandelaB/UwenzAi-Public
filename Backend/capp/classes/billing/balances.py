"""
Row-locked primitives for every Spark balance mutation.

Every debit and credit in the codebase must go through this module. A balance
change is only safe if the read that decides it and the write that applies it
happen while holding the row lock, so each helper here takes the lock itself
rather than trusting the caller to have taken it.

Two balances exist:

* ``SparkPool.sparks`` — authenticated accounts.
* ``GuestTokenQuota.sparks`` — anonymous guests, keyed by the ownership ID of a
  ``GuestIdentity`` (see :mod:`capp.classes.guest.guest_identity_service`).

Both columns carry a database-level non-negative check, so a lost update that
slipped past the application check would raise ``IntegrityError`` rather than
silently minting Sparks. The locks here are what keep that constraint from ever
being reached.

Callers must already be inside ``transaction.atomic()``; the helpers assert this
rather than opening a transaction of their own, so that a debit and the ledger
row recording it commit together or not at all.
"""

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from capp.constants import GUEST_INITIAL_SPARKS
from capp.models import GuestTokenQuota, SparkPool

logger = logging.getLogger(__name__)


class InsufficientSparks(Exception):
    """Raised when a debit would take a balance below zero."""

    def __init__(self, required: int, balance: int):
        self.required = required
        self.balance = balance
        super().__init__(f"Insufficient spark balance: required {required}, available {balance}")


def _require_atomic() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Spark balance mutations must run inside transaction.atomic()")


# ----------------------------------------------------------------------
# Locking
# ----------------------------------------------------------------------
def lock_spark_pool(user) -> SparkPool:
    """
    Return the caller's ``SparkPool`` with its row locked, creating it if absent.

    ``get_or_create`` can lose the race for a first-time user, so a concurrent
    creation is caught and the existing row is locked instead.
    """
    _require_atomic()

    pool = SparkPool.objects.select_for_update().filter(user=user).first()
    if pool is not None:
        return pool

    try:
        with transaction.atomic():
            SparkPool.objects.create(user=user, sparks=0, token_balance=0, is_active=True)
    except IntegrityError:
        # A concurrent request created the pool first; its row is the real one.
        pass

    return SparkPool.objects.select_for_update().get(user=user)


def lock_guest_quota(owner_key: str, initial_sparks: int = GUEST_INITIAL_SPARKS) -> GuestTokenQuota:
    """
    Return the ``GuestTokenQuota`` for a guest ownership ID with its row locked.

    First touch creates the row and grants the free guest allowance, matching
    ``GuestTokenQuota.get_or_create_quota``. Because the grant only happens on
    the branch that actually inserts, a request that loses the creation race is
    handed the winner's row and cannot mint a second allowance.
    """
    _require_atomic()

    quota = GuestTokenQuota.objects.select_for_update().filter(session_id=owner_key).first()
    if quota is not None:
        return quota

    try:
        with transaction.atomic():
            GuestTokenQuota.objects.create(
                session_id=owner_key,
                sparks=initial_sparks,
                daily_reset_at=timezone.now() + timedelta(hours=24),
            )
    except IntegrityError:
        pass

    return GuestTokenQuota.objects.select_for_update().get(session_id=owner_key)


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
def read_user_sparks(user) -> int:
    """Current balance for an account, or zero when it has no active pool."""
    pool = SparkPool.objects.filter(user=user, is_active=True).first()
    return int(pool.sparks) if pool is not None else 0


def read_guest_sparks(owner_key: str) -> int:
    """
    Current balance for a guest, granting the free allowance on first touch.

    Every surface that reports a guest balance reads it here. A reader that
    answers "no row yet" with zero tells a guest who has never spent anything
    that they are broke, which is exactly what the socket used to push on
    connect — and the zero then outlived the connection in the browser's cache.
    """
    with transaction.atomic():
        return int(lock_guest_quota(owner_key).sparks)


# ----------------------------------------------------------------------
# Mutations
# ----------------------------------------------------------------------
def debit_locked(row, amount: int) -> int:
    """
    Subtract ``amount`` from an already-locked balance row and return the new balance.

    Raises :class:`InsufficientSparks` without writing anything if the balance is
    too low, so a rejected charge leaves no trace and no partial state.
    """
    _require_atomic()
    if amount < 0:
        raise ValueError("Debit amount must not be negative")

    balance = int(row.sparks)
    if balance < amount:
        raise InsufficientSparks(required=amount, balance=balance)

    row.sparks = balance - amount
    row.save(update_fields=["sparks", "updated_at"])
    return row.sparks


def credit_locked(row, amount: int) -> int:
    """Add ``amount`` to an already-locked balance row and return the new balance."""
    _require_atomic()
    if amount < 0:
        raise ValueError("Credit amount must not be negative")

    row.sparks = int(row.sparks) + amount
    row.save(update_fields=["sparks", "updated_at"])
    return row.sparks
