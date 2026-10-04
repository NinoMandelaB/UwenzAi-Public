# capp/urls/conversation_urls.py

from django.urls import path
from capp.views.conversation_views import (
    api_delete_message,
    api_reset_conversation,
    api_start_conversation,
    api_send_message,
    api_generate_chat_image,
    api_accept_photo_offer,
    api_decline_photo_offer,
    api_list_conversations,
    api_list_messages,
    api_mark_conversation_read,
    api_unread_summary,
    api_conversation_settings,
    api_get_attachment,
)

urlpatterns = [
    # List all conversations (GET)
    path("", api_list_conversations, name="list-conversations"),
    # Start a new conversation (POST)
    path("start/", api_start_conversation, name="start-conversation"),
    # Unread counts for the nav badge (GET)
    path("unread/", api_unread_summary, name="conversations-unread"),
    # Mark a conversation read (POST)
    path("<uuid:conversation_id>/read/", api_mark_conversation_read, name="mark-conversation-read"),
    # Send a message in a conversation (POST)
    path("<uuid:conversation_id>/messages/", api_send_message, name="send-message"),
    # Serve one user-sent photo attachment: decrypt + stream (GET)
    path(
        "<uuid:conversation_id>/attachments/<uuid:attachment_id>/",
        api_get_attachment,
        name="get-attachment",
    ),
    # Generate an AI image of the character in the conversation (POST)
    path(
        "<uuid:conversation_id>/generate-image/",
        api_generate_chat_image,
        name="generate-chat-image",
    ),
    # Accept or decline a photo the character offered to send (POST)
    path(
        "<uuid:conversation_id>/photo-offers/<uuid:message_id>/accept/",
        api_accept_photo_offer,
        name="accept-photo-offer",
    ),
    path(
        "<uuid:conversation_id>/photo-offers/<uuid:message_id>/decline/",
        api_decline_photo_offer,
        name="decline-photo-offer",
    ),
    # Get message history for a conversation (GET)
    path("<uuid:conversation_id>/messages-list/", api_list_messages, name="list-messages"),
    path(
        "<uuid:conversation_id>/reset/",
        api_reset_conversation,
        name="reset-conversation",
    ),
    path(
        "<uuid:conversation_id>/messages/<uuid:message_id>/delete/",
        api_delete_message,
        name="delete-message",
    ),
    # Get/update per-chat toggles: web search + reasoning effort (GET, PATCH)
    path(
        "<uuid:conversation_id>/settings/",
        api_conversation_settings,
        name="conversation-settings",
    ),
]
