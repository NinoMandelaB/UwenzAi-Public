# capp/urls/admin_urls.py
"""
Admin-only API surface consumed by the separate uwenzAi-Admin panel repo.
All views here require is_staff/is_superuser (see capp.permissions.IsAdminOrSuperuser).
"""

from django.urls import path
from capp.views.admin_views import (
    admin_list_users,
    admin_get_user_detail,
    admin_update_user_status,
    admin_list_user_transactions,
    admin_adjust_user_sparks,
    admin_list_byok_users,
    admin_character_stats,
    admin_user_stats,
    admin_list_characters,
    admin_get_character_detail,
    admin_hard_delete_character,
    admin_audit_log,
)

urlpatterns = [
    path("users/", admin_list_users, name="admin_list_users"),
    path("users/<uuid:user_id>/", admin_get_user_detail, name="admin_get_user_detail"),
    path("users/<uuid:user_id>/status/", admin_update_user_status, name="admin_update_user_status"),
    path(
        "users/<uuid:user_id>/transactions/",
        admin_list_user_transactions,
        name="admin_list_user_transactions",
    ),
    path(
        "users/<uuid:user_id>/sparks/adjust/",
        admin_adjust_user_sparks,
        name="admin_adjust_user_sparks",
    ),
    path("byok-users/", admin_list_byok_users, name="admin_list_byok_users"),
    # Privacy-first userbase analytics: aggregate totals, k-anonymised
    # distributions and a top-N most-active-users leaderboard. See
    # capp/views/admin_views.py::admin_user_stats.
    path("users/stats/", admin_user_stats, name="admin_user_stats"),
    path("characters/stats/", admin_character_stats, name="admin_character_stats"),
    path("characters/", admin_list_characters, name="admin_list_characters"),
    path("characters/<uuid:character_id>/", admin_get_character_detail, name="admin_get_character_detail"),
    path(
        "characters/<uuid:character_id>/hard-delete/",
        admin_hard_delete_character,
        name="admin_hard_delete_character",
    ),
    # Unified audit trail: admin actions + Paystack payment events.
    # See capp/views/admin_views.py::admin_audit_log.
    path("audit-log/", admin_audit_log, name="admin_audit_log"),
]
