# capp/classes/billing/promo_code_service.py
"""
Single-use promo codes that credit extra Sparks at signup.

Design mirrors the rest of the billing subsystem: every balance mutation goes
through the row-locked primitives in :mod:`capp.classes.billing.balances`, and
the code row is claimed with ``select_for_update`` in the same transaction as
the Spark credit and the ledger entry, so two concurrent signups can never
redeem the same code and a failed signup can never consume one.

The admin operations here (generate / set active / revoke / delete) are used by
the admin-only views; each writes an ``AdminActionLog`` entry and locks the
same row the redemption path locks, so an admin toggle can never race a
redemption in flight.
"""

import logging
import secrets
import string

from django.db import IntegrityError, transaction
from django.utils import timezone

from capp.classes.billing.balances import credit_locked, lock_spark_pool
from capp.models import AdminActionLog, CustomUser, PromoCode, SparkTransaction

logger = logging.getLogger(__name__)

# Upper-case, digits, without visually ambiguous characters (0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10

MAX_GENERATE_COUNT = 500
MAX_SPARKS_AMOUNT = 1_000_000


def normalize_code(raw: str) -> str:
    """
    Normalise user/admin input to the stored form: uppercase, separators
    (spaces, dashes, dots) removed. ``PROMO-2026 x`` and ``promo2026x`` both
    resolve to the same stored code.
    """
    cleaned = "".join(ch for ch in (raw or "").upper() if ch in string.ascii_uppercase + string.digits)
    return cleaned


def _generate_code_value() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def redeem_at_signup(user: CustomUser, raw_code: str) -> dict:
    """
    Redeem a promo code for a just-created account, inside the caller's
    signup transaction.

    Returns an outcome dict for the signup response — the caller decides how
    to surface it, because a bad code must never fail the signup itself:

    * ``{"status": "applied", "sparks": <credited>}``
    * ``{"status": "not_applied", "reason": "not_found" | "already_redeemed" |
      "not_available"}`` — ``not_available`` covers deactivated and revoked
      codes; the distinction is only interesting server-side.
    """
    if not raw_code or not raw_code.strip():
        return {"status": "not_applied", "reason": "not_found"}

    code_value = normalize_code(raw_code)
    if not code_value:
        return {"status": "not_applied", "reason": "not_found"}

    # Lock the code row first: the read that decides the outcome and the write
    # that claims the code must happen while holding the lock, exactly like
    # every other Spark mutation in this codebase.
    promo = PromoCode.objects.select_for_update().filter(code=code_value).first()
    if promo is None:
        return {"status": "not_applied", "reason": "not_found"}

    if promo.redeemed_at is not None:
        return {"status": "not_applied", "reason": "already_redeemed"}

    if promo.is_revoked or not promo.is_active:
        return {"status": "not_applied", "reason": "not_available"}

    pool = lock_spark_pool(user)
    sparks_before = int(pool.sparks)
    sparks_after = credit_locked(pool, promo.sparks_amount)

    promo.redeemed_by = user
    promo.redeemed_at = timezone.now()
    promo.save(update_fields=["redeemed_by", "redeemed_at", "updated_at"])

    SparkTransaction.objects.create(
        spark_pool=pool,
        user=user,
        entry_type=SparkTransaction.EntryType.CREDIT,
        source=SparkTransaction.SourceChoices.BONUS,
        spark_amount=promo.sparks_amount,
        sparks_before=sparks_before,
        sparks_after=sparks_after,
        reference=promo.code,
        description=f"Promo code {promo.code} bonus sparks",
        metadata={"promo_code_id": str(promo.promo_code_id)},
    )

    logger.info(
        "Promo code redeemed: code=%s user=%s sparks=%d",
        promo.code,
        user.user_id,
        promo.sparks_amount,
    )

    return {"status": "applied", "sparks": promo.sparks_amount}


def generate_codes(*, count: int, sparks_amount: int, note: str = "", created_by=None) -> list:
    """
    Generate ``count`` unique active codes worth ``sparks_amount`` each.

    Codes are drawn from an unambiguous alphabet with ``secrets`` and are
    checked against the database, so they cannot be guessed from a pattern.
    """
    if not isinstance(count, int) or count < 1 or count > MAX_GENERATE_COUNT:
        raise ValueError("invalid_count")
    if not isinstance(sparks_amount, int) or sparks_amount < 1 or sparks_amount > MAX_SPARKS_AMOUNT:
        raise ValueError("invalid_sparks_amount")

    created = []
    with transaction.atomic():
        collisions = 0
        while len(created) < count:
            candidate = _generate_code_value()
            try:
                # Nested atomic block = savepoint: a unique-constraint collision
                # marks only this insertion as rolled back, keeping the outer
                # transaction usable for the next draw. Catching outside the
                # inner block is what makes that safe.
                with transaction.atomic():
                    promo = PromoCode.objects.create(
                        code=candidate,
                        sparks_amount=sparks_amount,
                        note=note[:500],
                        created_by=created_by,
                    )
            except IntegrityError:
                # Astronomically unlikely collision — draw again, bounded so a
                # pathological generator cannot loop forever.
                collisions += 1
                if collisions > 25:
                    raise RuntimeError("promo_code_generation_collisions")
                continue
            created.append(promo)

        if created_by is not None:
            AdminActionLog.objects.create(
                admin_user=created_by,
                action_type=AdminActionLog.ActionType.PROMO_CODE_CREATE,
                reason=note[:500] or "promo code generation",
                metadata={
                    "count": len(created),
                    "sparks_amount": sparks_amount,
                    "promo_code_ids": [str(p.promo_code_id) for p in created],
                },
            )

    return created


def set_active(promo_code_id, *, is_active: bool, admin_user) -> PromoCode:
    """
    Reversibly pause (``is_active=False``) or resume a code. Refused for
    revoked codes (revocation is permanent) and redeemed codes (the outcome is
    already fixed in the ledger).
    """
    with transaction.atomic():
        promo = PromoCode.objects.select_for_update().filter(promo_code_id=promo_code_id).first()
        if promo is None:
            raise LookupError("promo_code_not_found")
        if promo.redeemed_at is not None:
            raise ValueError("already_redeemed")
        if promo.is_revoked:
            raise ValueError("revoked")

        if promo.is_active != is_active:
            promo.is_active = is_active
            promo.save(update_fields=["is_active", "updated_at"])
            AdminActionLog.objects.create(
                admin_user=admin_user,
                action_type=AdminActionLog.ActionType.PROMO_CODE_UPDATE,
                metadata={
                    "promo_code_id": str(promo.promo_code_id),
                    "code": promo.code,
                    "is_active": is_active,
                },
            )

    return promo


def revoke(promo_code_id, *, admin_user) -> PromoCode:
    """
    Permanently withdraw an unredeemed code. A redeemed code cannot be
    revoked — its Sparks are already in the user's ledger, which is
    append-only by design.
    """
    with transaction.atomic():
        promo = PromoCode.objects.select_for_update().filter(promo_code_id=promo_code_id).first()
        if promo is None:
            raise LookupError("promo_code_not_found")
        if promo.redeemed_at is not None:
            raise ValueError("already_redeemed")

        if not promo.is_revoked:
            promo.is_revoked = True
            promo.is_active = False
            promo.save(update_fields=["is_revoked", "is_active", "updated_at"])
            AdminActionLog.objects.create(
                admin_user=admin_user,
                action_type=AdminActionLog.ActionType.PROMO_CODE_REVOKE,
                metadata={
                    "promo_code_id": str(promo.promo_code_id),
                    "code": promo.code,
                },
            )

    return promo


def delete(promo_code_id, *, admin_user) -> PromoCode:
    """
    Delete an unredeemed code outright. Redeemed codes are kept forever: the
    ``SparkTransaction`` ledger rows reference them and the campaign history
    (who used which flyer) is exactly what the admin panel is for.
    """
    with transaction.atomic():
        promo = PromoCode.objects.select_for_update().filter(promo_code_id=promo_code_id).first()
        if promo is None:
            raise LookupError("promo_code_not_found")
        if promo.redeemed_at is not None:
            raise ValueError("already_redeemed")

        # Django clears the instance's primary key after delete(), so capture
        # the audit metadata first — the AdminActionLog must keep the original
        # UUID, not a None.
        audit_metadata = {
            "promo_code_id": str(promo.promo_code_id),
            "code": promo.code,
            "sparks_amount": promo.sparks_amount,
        }

        promo.delete()
        AdminActionLog.objects.create(
            admin_user=admin_user,
            action_type=AdminActionLog.ActionType.PROMO_CODE_DELETE,
            metadata=audit_metadata,
        )

    return promo
