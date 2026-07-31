from django.urls import path
from capp.views.moderation_views import (
    submit_message_flag,
    admin_list_message_flags,
    admin_review_message_flag,
    user_flagged_messages,
)

urlpatterns = [
    path("messages/flag/", submit_message_flag, name="submit_message_flag"),
    path(
        "admin/message-flags/",
        admin_list_message_flags,
        name="admin_list_message_flags",
    ),
    path(
        "admin/message-flags/<uuid:flag_id>/review/",
        admin_review_message_flag,
        name="admin_review_message_flag",
    ),
    path(
        "conversations/<uuid:conversation_id>/flagged-messages/",
        user_flagged_messages,
        name="user_flagged_messages",
    ),
]
