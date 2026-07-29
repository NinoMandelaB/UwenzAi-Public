"""
Subscription-plan side effects of a Spark debit.

Charging a subscriber does two things beyond moving Sparks: it burns down
``UserSubscription.sparks_remaining`` before the pool balance, and it advances
the per-cycle counters in ``SubscriptionUsage`` that the plan's fair-use caps
are measured against. ``ChargeService._charge_spark_pool`` used to do both
inline; they now live here so ``BillingReservationService`` can run them in the
same transaction as the debit it is already making, and undo them in the same
transaction as a refund.

Running them anywhere else would reintroduce the bug this PR exists to fix: a
wrapper around reserve/refund can be interrupted between the two writes, while a
call inside the service's own transaction happens exactly as often as the debit
does.

``apply`` records how many subscription Sparks it actually consumed on the debit
transaction's metadata. ``reverse`` reads that number back rather than
recomputing it, because the remaining balance has moved on since — recomputing
would restore the wrong amount for a subscription that was topped up or that ran
out midway through the original charge.
"""

from django.db import models
from django.utils import timezone

from capp.models import BillingReservation, SubscriptionUsage, UserSubscription

USAGE_COUNTER = {
    BillingReservation.Feature.MESSAGE_PAIR: "message_pairs_used",
    BillingReservation.Feature.CHARACTER_GENERATION: "characters_created",
    BillingReservation.Feature.IMAGE_GENERATION: "images_generated",
    # character_edit is deliberately uncounted: no plan meters edits.
}

METADATA_SPARKS_SPENT = "subscription_sparks_spent"
METADATA_SUBSCRIPTION_ID = "subscription_id"


def _active_subscription(user):
    now = timezone.now()
    return (
        UserSubscription.objects.filter(
            user=user,
            status=UserSubscription.StatusChoices.ACTIVE,
            started_at__lte=now,
            ends_at__gt=now,
        )
        .select_related("subscription_plan")
        .first()
    )


def _usage_record(subscription):
    now = timezone.now()
    usage = SubscriptionUsage.objects.filter(
        user_subscription=subscription,
        cycle_start__lte=now,
        cycle_end__gt=now,
    ).first()
    if usage is None:
        usage = SubscriptionUsage.objects.create(
            user_subscription=subscription,
            cycle_start=now,
            cycle_end=subscription.ends_at,
        )
    return usage


def apply(user, feature, amount_sparks: int) -> dict:
    """Burn subscription Sparks and advance counters. Returns debit metadata."""
    subscription = _active_subscription(user)
    if subscription is None:
        return {}

    spent = min(int(amount_sparks), int(subscription.sparks_remaining))
    if spent > 0:
        subscription.sparks_remaining = models.F("sparks_remaining") - spent
        subscription.save(update_fields=["sparks_remaining", "updated_at"])

    counter = USAGE_COUNTER.get(feature)
    if counter:
        usage = _usage_record(subscription)
        setattr(usage, counter, models.F(counter) + 1)
        usage.save(update_fields=[counter, "updated_at"])

    return {
        METADATA_SUBSCRIPTION_ID: str(subscription.pk),
        METADATA_SPARKS_SPENT: spent,
    }


def reverse(reservation) -> None:
    """Undo what :func:`apply` did for ``reservation``, if anything."""
    debit = reservation.debit_spark_transaction
    metadata = getattr(debit, "metadata", None) or {}
    subscription_id = metadata.get(METADATA_SUBSCRIPTION_ID)
    if not subscription_id:
        return

    subscription = UserSubscription.objects.filter(pk=subscription_id).first()
    if subscription is None:
        return

    spent = int(metadata.get(METADATA_SPARKS_SPENT) or 0)
    if spent > 0:
        subscription.sparks_remaining = models.F("sparks_remaining") + spent
        subscription.save(update_fields=["sparks_remaining", "updated_at"])

    counter = USAGE_COUNTER.get(reservation.feature)
    if counter:
        usage = _usage_record(subscription)
        # Guard the floor: the cycle may have rolled over since the debit, in
        # which case this counter never saw the increment being undone.
        if int(getattr(usage, counter)) > 0:
            setattr(usage, counter, models.F(counter) - 1)
            usage.save(update_fields=[counter, "updated_at"])
