# capp/classes/chat/unread_service.py

"""
Unread read-state for conversations.

A conversation has exactly one owner — an account or a guest — so the read
watermark lives on the conversation row itself instead of a per-user join
table. "Unread" is everything the other party said after that watermark.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Count, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from capp.models import Conversation, Message

logger = logging.getLogger(__name__)

# Only the other party's messages can be unread. The user's own messages are
# read by definition, and system notices are transient UI chrome.
UNREAD_SENDER_TYPES = (Message.SenderType.AI,)

AUTH_VISIBLE_STATUSES = (
    Conversation.StatusChoices.ACTIVE,
    Conversation.StatusChoices.ARCHIVED,
    Conversation.StatusChoices.CLOSED,
)
GUEST_VISIBLE_STATUSES = (
    Conversation.StatusChoices.ACTIVE,
    Conversation.StatusChoices.ARCHIVED,
)


def visible_conversations(user_id=None, guest_session_id=None):
    """The conversations an owner can see — the same set the chat list returns."""
    if user_id:
        return Conversation.objects.filter(user_id=user_id, status__in=AUTH_VISIBLE_STATUSES)
    if guest_session_id:
        return Conversation.objects.filter(guest_session_id=guest_session_id, status__in=GUEST_VISIBLE_STATUSES)
    return Conversation.objects.none()


def annotate_unread(queryset):
    """Add an ``unread_count`` annotation, so a list needs no per-row query."""
    counted = (
        Message.objects.filter(
            conversation=OuterRef("pk"),
            sender_type__in=UNREAD_SENDER_TYPES,
            created_at__gt=OuterRef("last_read_at"),
        )
        .order_by()
        .values("conversation")
        .annotate(unread=Count("message_id"))
        .values("unread")
    )
    return queryset.annotate(unread_count=Coalesce(Subquery(counted, output_field=IntegerField()), 0))


def unread_count_for(conversation) -> int:
    return Message.objects.filter(
        conversation=conversation,
        sender_type__in=UNREAD_SENDER_TYPES,
        created_at__gt=conversation.last_read_at,
    ).count()


def total_unread(user_id=None, guest_session_id=None) -> int:
    """Unread messages across everything the owner can see (the nav badge)."""
    conversations = visible_conversations(user_id=user_id, guest_session_id=guest_session_id)
    return Message.objects.filter(
        conversation__in=conversations,
        sender_type__in=UNREAD_SENDER_TYPES,
        created_at__gt=F("conversation__last_read_at"),
    ).count()


def mark_read(conversation, when=None):
    """Move the watermark to now, clearing the conversation's badge."""
    conversation.last_read_at = when or timezone.now()
    # ``updated_at`` is deliberately left alone: it drives the model's default
    # ordering, and merely looking at a chat should not reorder anyone's list.
    conversation.save(update_fields=["last_read_at"])


def owner_group_name(conversation):
    """The owner's app-wide WebSocket group, shared with spark balance pushes."""
    if conversation.user_id:
        return f"spark_pool_{conversation.user_id}"
    if conversation.guest_session_id:
        return f"guest_spark_pool_{conversation.guest_session_id}"
    return None


def unread_payload(conversation) -> dict:
    return {
        "conversation_id": str(conversation.conversation_id),
        "unread_count": unread_count_for(conversation),
        "total_unread": total_unread(
            user_id=conversation.user_id,
            guest_session_id=conversation.guest_session_id,
        ),
    }


def broadcast_unread(conversation):
    """
    Push the conversation's badge and the owner's total to their app socket.

    This goes to the owner-wide group, not the per-conversation one: the badge
    has to update on the chat list and the nav tab, which are exactly the
    places where the conversation's own socket is not connected.
    """
    group = owner_group_name(conversation)
    if group is None:
        return
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(group, {"type": "unread.update", **unread_payload(conversation)})
    except Exception:
        logger.exception(
            "Failed to broadcast unread update for conversation %s",
            conversation.conversation_id,
        )
