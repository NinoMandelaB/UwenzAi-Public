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
    admin_list_byok_users,
    admin_character_stats,
    admin_list_characters,
    admin_get_character_detail,
    admin_hard_delete_character,
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
    path("byok-users/", admin_list_byok_users, name="admin_list_byok_users"),
    path("characters/stats/", admin_character_stats, name="admin_character_stats"),
    path("characters/", admin_list_characters, name="admin_list_characters"),
    path("characters/<uuid:character_id>/", admin_get_character_detail, name="admin_get_character_detail"),
    path(
        "characters/<uuid:character_id>/hard-delete/",
        admin_hard_delete_character,
        name="admin_hard_delete_character",
    ),
]
