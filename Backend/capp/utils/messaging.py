# capp/utils/messaging.py

import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from capp.models import Message, Conversation

logger = logging.getLogger(__name__)


def send_system_message(conversation: Conversation, text: str):
    """
    Save a system message in the given conversation and broadcast it over WebSocket.
    """
    # Save to database
    msg = Message.objects.create(
        conversation=conversation,
        sender_type=Message.SenderType.SYSTEM,
        content=text,
        status=Message.StatusChoices.SENT,
    )

    # Broadcast via WebSocket
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"conversation_{conversation.conversation_id}",
            {
                "type": "chat.message",
                "message": {
                    "message_id": str(msg.message_id),
                    "sender_type": "system",
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                },
            },
        )
    except Exception:
        logger.exception(
            "Failed to send system message over WebSocket for conversation %s",
            conversation.conversation_id,
        )
