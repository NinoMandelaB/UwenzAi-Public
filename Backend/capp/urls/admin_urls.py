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
    admin_user_safety,
    admin_message_user,
    admin_reverse_safety_event,
    admin_unlock_safety,
    admin_reset_model_refusals,
    admin_shadow_overview,
    admin_audience_overview,
    admin_list_promo_codes,
    admin_create_promo_codes,
    admin_set_promo_code_active,
    admin_revoke_promo_code,
    admin_delete_promo_code,
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
    # Cross-session safety ledger: event list + reversal + account unlock.
    # Metadata only — never message text (see SafetyEvent).
    path("users/<uuid:user_id>/safety/", admin_user_safety, name="admin_user_safety"),
    path(
        "users/<uuid:user_id>/safety/events/<uuid:event_id>/reverse/",
        admin_reverse_safety_event,
        name="admin_reverse_safety_event",
    ),
    path(
        "users/<uuid:user_id>/safety/unlock/",
        admin_unlock_safety,
        name="admin_unlock_safety",
    ),
    # Direct admin outreach: open a support ticket on the user's behalf
    # (first message from the admin). See admin_views.py::admin_message_user.
    path("users/<uuid:user_id>/message/", admin_message_user, name="admin_message_user"),
    path(
        "users/<uuid:user_id>/safety/model-refusals/reset/",
        admin_reset_model_refusals,
        name="admin_reset_model_refusals",
    ),
    # Flyer campaign promo codes: list/generate codes, pause/resume, revoke
    # (permanent) and delete (unredeemed only). See capp/views/admin_views.py
    # and capp/classes/billing/promo_code_service.py.
    path("promo-codes/", admin_list_promo_codes, name="admin_list_promo_codes"),
    path("promo-codes/create/", admin_create_promo_codes, name="admin_create_promo_codes"),
    path(
        "promo-codes/<uuid:promo_code_id>/active/",
        admin_set_promo_code_active,
        name="admin_set_promo_code_active",
    ),
    path(
        "promo-codes/<uuid:promo_code_id>/revoke/",
        admin_revoke_promo_code,
        name="admin_revoke_promo_code",
    ),
    path(
        "promo-codes/<uuid:promo_code_id>/",
        admin_delete_promo_code,
        name="admin_delete_promo_code",
    ),
    # Shadow-mode overview: every account the classifier flagged while the
    # ledger only recorded (never enforced). Metadata only.
    path("safety/shadow-overview/", admin_shadow_overview, name="admin_shadow_overview"),
    # Per-person audience analytics across accounts AND guests (the user stats
    # endpoint above deliberately excludes guests). Country is the edge-injected
    # country of the latest recorded visit; first/last seen carry source labels.
    # See capp/views/admin_views.py::admin_audience_overview.
    path("audience/", admin_audience_overview, name="admin_audience_overview"),
]
