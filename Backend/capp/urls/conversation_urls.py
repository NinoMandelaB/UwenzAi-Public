# capp/urls/conversation_urls.py

from django.urls import path
from capp.views.conversation_views import (
    api_delete_message,
    api_reset_conversation,
    api_start_conversation,
    api_send_message,
    api_generate_chat_image,
    api_list_conversations,
    api_list_messages,
)

urlpatterns = [
    # List all conversations (GET)
    path("", api_list_conversations, name="list-conversations"),
    # Start a new conversation (POST)
    path("start/", api_start_conversation, name="start-conversation"),
    # Send a message in a conversation (POST)
    path("<uuid:conversation_id>/messages/", api_send_message, name="send-message"),
    # Generate an AI image of the character in the conversation (POST)
    path(
        "<uuid:conversation_id>/generate-image/",
        api_generate_chat_image,
        name="generate-chat-image",
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
]
