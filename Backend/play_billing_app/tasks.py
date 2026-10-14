# play_billing_app/tasks.py
"""Scheduled Google Play Billing reconciliation."""

import logging

from celery import shared_task

from .services import PlayBillingService

logger = logging.getLogger(__name__)


@shared_task(name="play_billing_app.tasks.reconcile_play_voided_purchases")
def reconcile_play_voided_purchases():
    """
    Poll the Play voided-purchases API and claw back Sparks from refunds.

    Runs on the general queue via the beat schedule. Failures (Play
    unreachable, billing not configured) are logged and returned rather than
    raised: reconciliation is idempotent, so the next scheduled run retries
    from the stored checkpoint without losing anything.
    """
    result = PlayBillingService.reconcile_voided_purchases()
    if result.get("status") != "success":
        logger.warning("Play voided-purchase reconciliation skipped: %s", result.get("data"))
    else:
        data = result.get("data") or {}
        logger.info(
            "Play voided-purchase reconciliation: %s voided seen, %s reversed",
            data.get("voided_seen", 0),
            data.get("reversed", 0),
        )
    return result
