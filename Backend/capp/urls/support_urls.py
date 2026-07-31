# capp/urls/support_urls.py
from django.urls import path
from capp.views.support_views import (
    api_list_tickets,
    api_create_ticket,
    api_get_ticket,
    api_mark_ticket_read,
    api_reply_to_ticket,
    api_unread_count,
    api_update_ticket_status,
)

urlpatterns = [
    path("tickets/", api_list_tickets, name="api_list_tickets"),
    path("tickets/create/", api_create_ticket, name="api_create_ticket"),
    path("tickets/<uuid:ticket_id>/", api_get_ticket, name="api_get_ticket"),
    path(
        "tickets/<uuid:ticket_id>/reply/",
        api_reply_to_ticket,
        name="api_reply_to_ticket",
    ),
    path(
        "tickets/<uuid:ticket_id>/status/",
        api_update_ticket_status,
        name="api_update_ticket_status",
    ),
    path("unread-count/", api_unread_count, name="api_unread_count"),
    path(
        "tickets/<uuid:ticket_id>/mark-read/",
        api_mark_ticket_read,
        name="api_mark_ticket_read",
    ),
]
