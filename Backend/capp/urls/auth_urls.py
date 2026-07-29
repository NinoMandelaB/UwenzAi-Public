from django.urls import path
from capp.views.auth_views import (
    api_change_password,
    api_confirm_password_reset,
    api_csrf,
    api_export_user_data,
    api_request_password_reset,
    api_signup_user,
    api_signin_user,
    api_google_auth_user,
    api_refresh_access_token,
    api_signout_user,
    api_get_authenticated_user,
    api_staff_users,
    api_update_profile,
    api_verify_email,
    api_resend_verification_code,
    api_delete_user_permanently,
    # NEW imports
    api_request_email_change,
    api_confirm_email_change,
)
from capp.views.byok_views import api_byok_config, api_byok_toggle

urlpatterns = [
    path("csrf/", api_csrf),
    path("signup/", api_signup_user),
    path("signin/", api_signin_user),
    path("google/", api_google_auth_user),
    path("refresh/", api_refresh_access_token),
    path("signout/", api_signout_user),
    path("me/", api_get_authenticated_user),
    path("verify-email/", api_verify_email),
    path("resend-verification-code/", api_resend_verification_code),
    path("request-password-reset/", api_request_password_reset),
    path("confirm-password-reset/", api_confirm_password_reset),
    path("change-password/", api_change_password),
    path("delete-account/", api_delete_user_permanently),
    path("profile/", api_update_profile),
    path("export-data/", api_export_user_data),
    # NEW endpoints
    path("request-email-change/", api_request_email_change),
    path("confirm-email-change/", api_confirm_email_change),
    # BYOK
    path("byok/", api_byok_config),
    path("byok/toggle/", api_byok_toggle),
    path("staff/", api_staff_users),
]
