"""
Atomic transfer of a guest's state to the account that was created from it.

Invariants this module exists to guarantee:

* A claim requires a ``GuestTransferGrant``: a server-created record, written
  during signup inside the account's own transaction, naming both the new user
  and the cookie-resolved guest identity. Holding a guest cookie is *not*
  authority to claim, so signing in to a pre-existing account transfers nothing.
  The grant names whichever account signed up most recently from that guest
  session without abandoning it — see
  :mod:`capp.classes.guest.guest_transfer_grant_service` — so an account whose
  entitlement was reassigned to a later signup claims nothing either.
* A claim additionally requires a *verified* account, so the transfer lands only
  once control of the email address has been proved.
* The whole transfer is one transaction, and the grant is consumed inside it.
  Either the account ends up owning every conversation, spark, charge and
  purchase and the grant is spent, or nothing moves, the grant is still pending,
  and the guest is left exactly as it was so the next login can retry.
* Nothing is deleted. Ledger and usage rows are reassigned and stamped with
  immutable guest-origin provenance; the guest quota row survives with its
  counters intact and its balance zeroed.
* Repeating the claim is safe. Once the grant is spent, a replayed or concurrent
  claim moves nothing and double-credits nothing.
* The caller clears the guest cookie only after the transaction commits, and
  only when a transfer actually happened.
"""

import logging

from django.db import transaction
from django.utils import timezone

from capp.classes.billing.balances import credit_locked, debit_locked, lock_spark_pool
from capp.classes.guest.guest_transfer_grant_service import GuestTransferGrantService
from capp.models import (
    BillingReservation,
    Conversation,
    CustomUser,
    GuestIdentity,
    GuestTokenQuota,
    GuestWebSocketTicket,
    SparkTransaction,
    UsageCharge,
)

logger = logging.getLogger(__name__)


class GuestClaimService:
    @classmethod
    def claim(cls, user, identity: GuestIdentity) -> dict:
        """
        Transfer everything owned by ``identity`` to ``user``, if entitled.

        Returns a dict whose ``claimed`` flag tells the caller whether a transfer
        committed and the guest cookie is therefore spent. Every "not entitled"
        outcome returns False, which is what keeps an ordinary login from
        retiring a guest session it had no claim on.
        """
        if not user.is_verified:
            # The grant is created at signup, before the email is proved, so this
            # is a live check and not just defence in depth.
            logger.warning("Refusing guest claim for unverified user %s", user.user_id)
            return {"claimed": False, "reason": "user_not_verified"}

        with transaction.atomic():
            # Identity first, then the grant, and in this order everywhere that
            # takes both — signup's reassignment of an abandoned grant does the
            # same — so contenders queue rather than deadlock.
            locked = GuestIdentity.objects.select_for_update().filter(pk=identity.pk).first()

            grant = GuestTransferGrantService.lock_claimable(user, identity)
            if grant is None:
                logger.info(
                    "No pending guest transfer grant for user=%s identity=%s — nothing transferred",
                    user.user_id,
                    identity.guest_id,
                )
                return {"claimed": False, "reason": "no_transfer_grant"}

            if locked is None:
                return {"claimed": False, "reason": "guest_identity_not_found"}

            if locked.status == GuestIdentity.Status.CLAIMED:
                # A grant was pending but the identity is already claimed: the
                # two are written together, so this means someone forced them
                # apart. Refuse rather than move anything.
                logger.warning(
                    "Guest identity %s already claimed while a grant was pending for user=%s",
                    locked.guest_id,
                    user.user_id,
                )
                return {"claimed": False, "reason": "guest_identity_already_claimed"}

            if locked.status != GuestIdentity.Status.ACTIVE:
                return {"claimed": False, "reason": "guest_identity_revoked"}

            if not GuestTransferGrantService.consume(grant):
                # Lost to a concurrent claim that committed between our lock and
                # this update. Exactly one caller may reach the transfer.
                logger.info("Guest transfer grant %s already consumed — no double credit", grant.grant_id)
                return {"claimed": False, "reason": "transfer_grant_already_consumed"}

            result = cls._transfer(user, locked)

        # Queued after commit so a failed transfer never leaves a task chasing
        # conversations that were rolled back.
        if result["conversations_claimed"] > 0:
            cls._queue_retrospective_memory(user)

        logger.info(
            "Guest identity %s claimed by user %s "
            "(conversations=%d sparks=%d charges=%d purchases=%d reservations=%d)",
            identity.guest_id,
            user.user_id,
            result["conversations_claimed"],
            result["sparks_transferred"],
            result["usage_charges_claimed"],
            result["purchases_claimed"],
            result["reservations_claimed"],
        )
        return result

    @classmethod
    def _transfer(cls, user, identity: GuestIdentity) -> dict:
        now = timezone.now()
        owner_key = identity.owner_key

        conversations_claimed = Conversation.objects.filter(
            guest_session_id=owner_key,
            user__isnull=True,
        ).update(
            user=user,
            guest_session_id=None,
            origin_guest_session_id=owner_key,
            claimed_from_guest_at=now,
            updated_at=now,
        )

        sparks_transferred = cls._transfer_sparks(user, owner_key, now)

        usage_charges_claimed = UsageCharge.objects.filter(
            guest_session_id=owner_key,
            user__isnull=True,
        ).update(
            user=user,
            guest_session_id=None,
            origin_guest_session_id=owner_key,
            claimed_from_guest_at=now,
            updated_at=now,
        )

        purchases_claimed = cls._transfer_purchases(user, owner_key, now)

        reservations_claimed = cls._stamp_reservations(user, identity, now)

        # Any ticket already issued to this guest must not survive the claim.
        GuestWebSocketTicket.objects.filter(
            guest_identity=identity,
            consumed_at__isnull=True,
        ).update(consumed_at=now, updated_at=now)

        identity.status = GuestIdentity.Status.CLAIMED
        identity.claimed_at = now
        identity.claimed_by = user
        identity.save(update_fields=["status", "claimed_at", "claimed_by", "updated_at"])

        # Carry the guest's first recorded visit over to the account so the
        # person's earliest recorded visit survives the merge (the audience view
        # then reads it from the account row, not the retired guest). One
        # conditional update: rows with no first_visit_at or a later one adopt
        # the guest's earlier timestamp; an account that somehow already has an
        # earlier first_visit_at keeps it.
        if identity.first_visit_at:
            CustomUser.objects.filter(pk=user.pk).exclude(first_visit_at__lt=identity.first_visit_at).update(
                first_visit_at=identity.first_visit_at
            )

        return {
            "claimed": True,
            "reason": "claimed",
            "guest_id": owner_key,
            "conversations_claimed": conversations_claimed,
            "sparks_transferred": sparks_transferred,
            "usage_charges_claimed": usage_charges_claimed,
            "purchases_claimed": purchases_claimed,
            "reservations_claimed": reservations_claimed,
        }

    @staticmethod
    def _stamp_reservations(user, identity, now) -> int:
        """
        Record the claim on the guest's billing reservations.

        Unlike conversations and usage charges, the principal is *not* moved to
        the account. A reservation says who incurred the charge, and that was the
        guest; rewriting it would erase the only record that the Sparks came from
        an anonymous session. The claim is additive provenance, and it is what
        lets a later refund credit the account instead of the now-zeroed quota.
        """
        return BillingReservation.objects.filter(
            guest_identity=identity,
            claimed_by__isnull=True,
        ).update(
            claimed_by=user,
            claimed_from_guest_at=now,
            updated_at=now,
        )

    @staticmethod
    def _transfer_sparks(user, owner_key: str, now) -> int:
        quota = GuestTokenQuota.objects.select_for_update().filter(session_id=owner_key).first()
        if quota is None:
            return 0

        sparks = int(quota.sparks)

        if sparks > 0:
            spark_pool = lock_spark_pool(user)
            sparks_before = int(spark_pool.sparks)
            credit_locked(spark_pool, sparks)

            # Recorded as a ledger entry so the transferred balance is visible in
            # account history rather than appearing out of nowhere.
            SparkTransaction.objects.create(
                spark_pool=spark_pool,
                user=user,
                entry_type=SparkTransaction.EntryType.CREDIT,
                source=SparkTransaction.SourceChoices.ADJUSTMENT,
                spark_amount=sparks,
                sparks_before=sparks_before,
                sparks_after=spark_pool.sparks,
                description="Sparks transferred from guest session",
                metadata={"origin_guest_session_id": owner_key},
            )

        # Usage counters are kept; only the spendable balance moves. The drain
        # goes through debit_locked like any other, so the row lock taken above
        # is the same one the write happens under.
        debit_locked(quota, sparks)
        quota.claimed_at = now
        quota.claimed_by = user
        quota.save(update_fields=["claimed_at", "claimed_by", "updated_at"])

        return sparks

    @staticmethod
    def _transfer_purchases(user, owner_key: str, now) -> int:
        from paystack_app.models import PaystackTransaction

        return PaystackTransaction.objects.filter(
            guest_session_id=owner_key,
            user__isnull=True,
            owner_deleted_at__isnull=True,
        ).update(
            user=user,
            guest_session_id=None,
            origin_guest_session_id=owner_key,
            claimed_from_guest_at=now,
            updated_at=now,
        )

    @staticmethod
    def _queue_retrospective_memory(user) -> None:
        try:
            from capp.tasks import retrospective_memory_for_new_user

            retrospective_memory_for_new_user.delay(str(user.user_id))
        except Exception:
            logger.exception(
                "Failed to queue retrospective memory for user=%s — memory will build from future chats",
                user.user_id,
            )
