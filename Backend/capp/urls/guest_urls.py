# capp/urls/guest_urls.py

from django.urls import path

from capp.views.guest_views import (
    api_ensure_guest_session,
    api_issue_guest_ws_ticket,
    api_migrate_legacy_guest_session,
)

urlpatterns = [
    path("session/", api_ensure_guest_session, name="ensure-guest-session"),
    path(
        "session/migrate/",
        api_migrate_legacy_guest_session,
        name="migrate-legacy-guest-session",
    ),
    path("ws-ticket/", api_issue_guest_ws_ticket, name="issue-guest-ws-ticket"),
]
