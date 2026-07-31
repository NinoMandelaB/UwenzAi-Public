"""
The price list, and the one way to charge for a paid feature.

``BillingReservationService`` knows how to hold, finalise and give back a number
of Sparks. It deliberately does not know what anything costs. This module is the
seam between the two: it maps a feature to its price, resolves the principal
from a request, and gives call sites a single ``reserve`` to call before they
touch a provider.

Prices are unchanged from the constants that ``ChargeService.ACTION_COST_MAP``
used to read; this module replaces that map rather than repricing anything.

Every paid call site follows the same shape::

    reservation = PaidFeature.reserve(
        feature=BillingReservation.Feature.IMAGE_GENERATION,
        operation_id=operation_id,
        user=actor,
        operation_reference=f"gallery_image:{character.pk}:{operation_id}",
    )
    if reservation.status != BillingReservation.Status.RESERVED:
        return replay_outcome(reservation)      # already settled, or already refunded

    try:
        artifact = call_provider_and_persist()
    except Exception:
        PaidFeature.refund(reservation, reason="provider_failed")
        raise

    PaidFeature.settle(reservation, reason="ok")

The status check after ``reserve`` is not optional. ``reserve`` returns the
*existing* reservation for a replayed key, in whatever state it reached, so a
caller that ignores the status would redo paid work for free after a refund and
produce a second artifact after a settle.
"""

import logging

from capp.classes.billing.reservation_service import (
    BillingReservationService,
    IdempotencyKeyConflict,
    InsufficientBalance,
    ReservationError,
)
from capp.constants import (
    CHARACTER_EDIT_COST,
    CHARACTER_GENERATION_COST,
    IMAGE_GENERATION_COST,
    MESSAGE_PAIR_COST,
)
from capp.models import BillingReservation

logger = logging.getLogger(__name__)

FEATURE_COST = {
    BillingReservation.Feature.MESSAGE_PAIR: MESSAGE_PAIR_COST,
    BillingReservation.Feature.CHARACTER_GENERATION: CHARACTER_GENERATION_COST,
    BillingReservation.Feature.CHARACTER_EDIT: CHARACTER_EDIT_COST,
    BillingReservation.Feature.IMAGE_GENERATION: IMAGE_GENERATION_COST,
}


class PaidFeature:
    @staticmethod
    def cost(feature, *, cost_override=None) -> int:
        """Price in Sparks. ``cost_override`` exists for BYOK's reduced rate."""
        if cost_override is not None:
            return int(cost_override)
        try:
            return int(FEATURE_COST[feature])
        except KeyError:
            raise ReservationError(f"No price configured for feature '{feature}'") from None

    @classmethod
    def reserve(
        cls,
        *,
        feature,
        operation_id: str,
        user=None,
        guest_identity=None,
        operation_reference: str = None,
        cost_override=None,
    ) -> BillingReservation:
        """Hold the price of ``feature``. Raises :class:`InsufficientBalance`."""
        return BillingReservationService.reserve(
            idempotency_key=operation_id,
            feature=feature,
            amount_sparks=cls.cost(feature, cost_override=cost_override),
            user=user,
            guest_identity=guest_identity,
            operation_reference=operation_reference,
        )

    @staticmethod
    def settle(reservation, *, reason: str) -> BillingReservation:
        return BillingReservationService.settle(reservation, reason=reason)

    @staticmethod
    def refund(reservation, *, reason: str) -> BillingReservation:
        """
        Give the Sparks back, never raising into the caller's failure path.

        Refunds run from ``except`` blocks and from Celery teardown. If the
        refund itself fails, the original error is what the caller must keep
        propagating, and the hold is left for ``manage.py billing_reservations
        --list-stale`` to surface rather than being papered over.
        """
        try:
            return BillingReservationService.refund(reservation, reason=reason)
        except ReservationError:
            logger.exception(
                "Refund failed for reservation %s; left held for operator review",
                getattr(reservation, "reservation_id", reservation),
            )
            return reservation


def is_free_actor(actor) -> bool:
    """Admins and unauthenticated non-guest callers are not billed."""
    if actor is None:
        return True
    if not getattr(actor, "is_authenticated", False):
        return True
    return bool(getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False))


__all__ = [
    "FEATURE_COST",
    "IdempotencyKeyConflict",
    "InsufficientBalance",
    "PaidFeature",
    "ReservationError",
    "is_free_actor",
]
