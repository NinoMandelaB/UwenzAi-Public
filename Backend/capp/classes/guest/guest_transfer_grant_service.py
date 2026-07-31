"""
Server-side binding between a brand-new account and the guest session it grew out of.

The transfer of a guest's Sparks, conversations and purchases used to be
authorised by nothing more than "a guest credential cookie arrived alongside a
successful login". That is true of a guest who signs up *and* of a guest who
walks up to the login form and signs in to an account they already had, so the
second case silently absorbed the guest's state into a pre-existing account.

Eligibility is therefore an explicit, server-created record instead of an
inference. ``GuestTransferGrant`` is written during signup, inside the same
transaction as the account, from the ``GuestIdentity`` the server resolved from
the HttpOnly cookie — never from anything the client supplied. The claim path
consults it and nothing else.

Two rules follow from the unique constraints on the model and are what make the
grant safe to hand to the claim path:

* an identity can back at most one grant, so two accounts can never hold an
  entitlement to the same guest session at once, and
* a user can hold at most one grant, so no account accumulates entitlements.

The entitlement belongs to the guest session rather than to whichever account
first asked for it, which matters because signup does not finish at signup: an
account is created before its email is verified, and a visitor who abandons that
account and signs up again from the same guest session must still get their
Sparks and conversations. So the single grant per identity is *reassignable*, but
only while it is unspent and its holder unverified — see ``grant_at_signup``.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from capp.models import GuestIdentity, GuestTransferGrant

logger = logging.getLogger(__name__)


class GuestTransferGrantService:
    @classmethod
    def grant_at_signup(cls, user, identity) -> GuestTransferGrant | None:
        """
        Record that ``user`` — created by the signup now in progress — may claim
        ``identity`` exactly once.

        Must be called inside the signup transaction so that an account either
        exists with its entitlement or does not exist at all. Returns ``None``
        when no grant is appropriate, which is the safe outcome: no grant means
        no transfer, and the guest session simply stays a guest session.

        If the identity already backs a grant, the grant is handed over to
        ``user`` when — and only when — it is still ``PENDING`` and its holder has
        not verified their email. That is the abandoned-signup case: an account
        was created from this guest session and never proved control of its
        address, so it is holding an entitlement it may never spend, and the
        visitor trying again from the same session is the same person. A grant
        held by a verified account, or already consumed, is never moved.
        """
        if identity is None:
            return None

        try:
            # Nested atomic: an IntegrityError from a losing race, or from a user
            # who already holds a grant, would poison the outer signup
            # transaction without its own savepoint.
            with transaction.atomic():
                # The identity row is the only lock available to serialise two
                # signups from the same guest session, since the grant they
                # contend for may not exist yet. Every path that takes both locks
                # takes the identity first — see GuestClaimService.claim — so
                # they queue rather than deadlock.
                locked_identity = GuestIdentity.objects.select_for_update().filter(pk=identity.pk).first()

                if locked_identity is None or not locked_identity.is_usable():
                    # Expired, revoked, or already claimed by an earlier signup.
                    logger.info(
                        "No guest transfer grant for user=%s: identity %s is unusable",
                        user.user_id,
                        identity.guest_id,
                    )
                    return None

                existing = GuestTransferGrant.objects.select_for_update().filter(guest_identity=locked_identity).first()
                if existing is not None:
                    return cls._reassign(existing, user, locked_identity)

                grant = GuestTransferGrant.objects.create(
                    user=user,
                    guest_identity=locked_identity,
                    status=GuestTransferGrant.Status.PENDING,
                    origin_guest_session_id=locked_identity.owner_key,
                )
        except IntegrityError:
            # This user already holds a grant on another guest session. Not
            # eligible, not an error.
            logger.warning(
                "Guest transfer grant refused for user=%s identity=%s (already bound)",
                user.user_id,
                identity.guest_id,
            )
            return None

        logger.info(
            "Guest transfer grant %s created: user=%s identity=%s",
            grant.grant_id,
            user.user_id,
            identity.guest_id,
        )
        return grant

    @staticmethod
    def _reassign(grant: GuestTransferGrant, user, identity) -> GuestTransferGrant | None:
        """
        Move an abandoned ``grant`` to ``user``, or refuse and return ``None``.

        Caller must hold the identity row lock and ``grant``'s own row lock. The
        two conditions that make a handover safe are nevertheless re-tested in the
        ``UPDATE``'s ``WHERE`` clause rather than in Python, so the database is
        the thing that decides: a grant is moved only while ``PENDING`` and only
        while its holder is unverified. A verification or a claim that commits
        alongside this therefore wins, and the entitlement cannot be taken out
        from under an account that is about to spend it.
        """
        if grant.user_id == user.pk:
            return grant

        now = timezone.now()

        moved = GuestTransferGrant.objects.filter(
            pk=grant.pk,
            status=GuestTransferGrant.Status.PENDING,
            user__is_verified=False,
        ).update(
            user=user,
            previous_user_id=grant.user_id,
            reassigned_at=now,
            updated_at=now,
        )

        if moved != 1:
            logger.info(
                "Guest transfer grant %s not reassigned to user=%s: held by user=%s (status=%s)",
                grant.grant_id,
                user.user_id,
                grant.user_id,
                grant.status,
            )
            return None

        logger.warning(
            "Guest transfer grant %s reassigned from unverified user=%s to user=%s (identity=%s)",
            grant.grant_id,
            grant.user_id,
            user.user_id,
            identity.guest_id,
        )
        grant.refresh_from_db()
        return grant

    @staticmethod
    def lock_claimable(user, identity) -> GuestTransferGrant | None:
        """
        Return the row-locked ``PENDING`` grant entitling ``user`` to ``identity``.

        Both sides are matched, so neither a stolen cookie presented with the
        wrong account nor the right account presented with someone else's cookie
        resolves to a grant. ``None`` means "not eligible" and the caller must
        transfer nothing.

        Caller must hold an open transaction; the lock is what serialises
        concurrent claims of the same grant.
        """
        if identity is None:
            return None

        return (
            GuestTransferGrant.objects.select_for_update()
            .filter(
                user=user,
                guest_identity=identity,
                status=GuestTransferGrant.Status.PENDING,
            )
            .first()
        )

    @staticmethod
    def consume(grant: GuestTransferGrant, now=None) -> bool:
        """
        Spend ``grant``, returning False if someone else already spent it.

        The status test lives in the ``UPDATE``'s ``WHERE`` clause rather than in
        Python, so the database decides the winner. Combined with the caller's
        transaction this is also the rollback story: if the transfer that follows
        raises, the grant reverts to ``PENDING`` and the next login retries.
        """
        now = now or timezone.now()

        updated = GuestTransferGrant.objects.filter(
            pk=grant.pk,
            status=GuestTransferGrant.Status.PENDING,
        ).update(
            status=GuestTransferGrant.Status.CONSUMED,
            consumed_at=now,
            updated_at=now,
        )
        return updated == 1
