"""
Short-lived, single-use WebSocket tickets for anonymous guests.

A WebSocket URL is not a safe place for a long-lived credential: it lands in
proxy and server access logs, browser history and Referer-adjacent telemetry.
Guests therefore exchange their HttpOnly cookie — over an ordinary
cookie-authenticated HTTPS request — for a ticket that is

* high-entropy and stored only as a SHA-256 hash,
* bound to one guest identity and one channel (and one conversation for chat),
* valid for seconds, and
* consumed atomically, so a replayed ticket is refused even under a race.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from capp.models import Conversation, GuestIdentity, GuestWebSocketTicket

logger = logging.getLogger(__name__)

TICKET_BYTES = 32


def hash_ticket(raw_ticket: str) -> str:
    return hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()


class GuestWebSocketTicketService:
    @classmethod
    def issue(cls, identity: GuestIdentity, channel: str, conversation_id=None) -> dict:
        if channel not in GuestWebSocketTicket.Channel.values:
            return {"status": "fail", "data": "invalid_channel"}

        if channel == GuestWebSocketTicket.Channel.CHAT:
            if not conversation_id:
                return {"status": "fail", "data": "conversation_id_required"}
            try:
                conversation_id = uuid.UUID(str(conversation_id))
            except (ValueError, AttributeError, TypeError):
                return {"status": "fail", "data": "conversation_not_found"}
            owns_conversation = Conversation.objects.filter(
                conversation_id=conversation_id,
                guest_session_id=identity.owner_key,
            ).exists()
            if not owns_conversation:
                return {"status": "fail", "data": "conversation_not_found"}
        else:
            conversation_id = None

        raw_ticket = secrets.token_urlsafe(TICKET_BYTES)
        ttl = settings.GUEST_WS_TICKET_TTL_SECONDS

        GuestWebSocketTicket.objects.create(
            guest_identity=identity,
            ticket_hash=hash_ticket(raw_ticket),
            channel=channel,
            conversation_id=conversation_id,
            expires_at=timezone.now() + timedelta(seconds=ttl),
        )

        logger.info(
            "Issued guest WebSocket ticket for identity=%s channel=%s",
            identity.guest_id,
            channel,
        )
        return {
            "status": "success",
            "data": {"ticket": raw_ticket, "expires_in": ttl},
        }

    @classmethod
    def consume(cls, raw_ticket: str, channel: str, conversation_id=None):
        """
        Atomically spend a ticket and return the guest identity it belongs to,
        or None.

        The conditional UPDATE is the whole point: two concurrent connections
        presenting the same ticket produce exactly one row update, so only one
        of them is allowed through.
        """
        if not raw_ticket:
            return None

        now = timezone.now()
        candidates = GuestWebSocketTicket.objects.filter(
            ticket_hash=hash_ticket(raw_ticket),
            channel=channel,
            consumed_at__isnull=True,
            expires_at__gt=now,
        )
        if conversation_id is not None:
            candidates = candidates.filter(conversation_id=conversation_id)

        ticket = candidates.first()
        if ticket is None:
            return None

        spent = GuestWebSocketTicket.objects.filter(
            pk=ticket.pk,
            consumed_at__isnull=True,
        ).update(consumed_at=now, updated_at=now)
        if spent != 1:
            return None

        identity = GuestIdentity.objects.filter(pk=ticket.guest_identity_id).first()
        if identity is None or not identity.is_usable(now):
            return None

        return identity

    @staticmethod
    def purge_expired(older_than_seconds: int = 3600) -> int:
        cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
        deleted, _ = GuestWebSocketTicket.objects.filter(expires_at__lt=cutoff).delete()
        return deleted
