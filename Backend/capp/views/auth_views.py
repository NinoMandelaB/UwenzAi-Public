import logging

from django.http import HttpResponse
import json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from capp.authentication import OptionalAccountJWTAuthentication
from capp.csrf import csrf_protect_api, csrf_protect_for_guests
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.auth.auth import Auth
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_cookie import clear_guest_cookie
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.throttles import (
    SignupThrottle,
    SigninThrottle,
    GoogleAuthThrottle,
    RefreshTokenThrottle,
    SignoutThrottle,
    RequestPasswordResetThrottle,
    ConfirmPasswordResetThrottle,
)
from capp.models import CustomUser

logger = logging.getLogger(__name__)


def _set_refresh_cookie(response, refresh_token: str):
    response.set_cookie(
        key=settings.JWT_REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.JWT_REFRESH_COOKIE_AGE,
        httponly=settings.JWT_REFRESH_COOKIE_HTTPONLY,
        secure=settings.JWT_REFRESH_COOKIE_SECURE,
        samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
        path="/",  # ← add this
    )
    return response


def _clear_refresh_cookie(response):
    response.delete_cookie(
        key=settings.JWT_REFRESH_COOKIE_NAME,
        samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
        path="/",  # ← add this
    )
    return response


def _claim_guest_session(request, response, user_id):
    """
    Hand a guest's data to the account that was created from that guest session.

    Two things must line up, both established server-side:

    * the guest identity resolved from this request's HttpOnly credential cookie,
      never from a body field or header, and
    * a pending ``GuestTransferGrant`` naming *that* identity and *this* user,
      which only ``api_signup_user`` can create.

    So a visitor who chatted as a guest and then signed in to an account they
    already had transfers nothing: there is no grant, and this returns having
    touched neither the account nor the guest. Critically it also leaves the
    guest cookie in place in that case, so the visitor can sign out and pick the
    guest session back up.

    A failed transfer must never fail the login itself, or a guest whose transfer
    keeps erroring could never sign in to retry it. The grant stays pending on
    failure, so the next login of that same account retries.
    """
    if not request.COOKIES.get(settings.GUEST_CREDENTIAL_COOKIE_NAME):
        return

    try:
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            # Already claimed, revoked or expired. The credential is dead, so it
            # cannot be revived after a later logout and is safe to drop.
            clear_guest_cookie(response)
            return

        user = CustomUser.objects.filter(pk=user_id).first()
        if user is None:
            return

        result = GuestClaimService.claim(user, identity)
    except Exception:
        logger.exception("Guest claim raised for user=%s — guest state preserved for retry", user_id)
        return

    if result.get("claimed"):
        clear_guest_cookie(response)
    else:
        # Not an error. The overwhelmingly common reason is the intended one:
        # this account did not come from this guest session. The guest cookie
        # deliberately survives so the guest session stays recoverable.
        logger.info(
            "No guest transfer for user=%s reason=%s — guest session left intact",
            user_id,
            result.get("reason"),
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_staff_users(request):
    """Get list of staff users for assignment dropdown."""
    if not (request.user.is_staff or request.user.is_superuser):
        return Response({"status": "fail", "data": "permission_denied"}, status=403)

    staff = CustomUser.objects.filter(is_staff=True).order_by("username")
    data = [
        {
            "user_id": str(u.user_id),
            "username": u.username,
            "display_name": u.get_display_name(),
        }
        for u in staff
    ]
    return Response({"status": "success", "data": {"users": data}})


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_request_email_change(request):
    try:
        result = Auth.request_email_change(request.user, request.data, request=request)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Unexpected error in api_request_email_change")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_confirm_email_change(request):
    try:
        result = Auth.confirm_email_change(request.user, request.data, request=request)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Unexpected error in api_confirm_email_change")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@never_cache
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_export_user_data(request):
    try:
        result = Auth.export_user_data(request.user)

        if result["status"] != "success":
            return Response(result, status=400)

        # Return as a downloadable JSON file
        response = HttpResponse(
            json.dumps(result["data"], indent=2, default=str),
            content_type="application/json",
        )
        response["Content-Disposition"] = 'attachment; filename="uwenzai_data.json"'
        return response

    except Exception:
        logger.error("Unexpected error in api_export_user_data")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["GET", "PATCH", "PUT"])
@permission_classes([IsAuthenticated])
def api_feature_preferences(request):
    """Read / update the per-account optional-feature toggles.

    GET    -> {"status": "success", "data": {"feature_preferences": {...}}}
    PATCH  -> partial update, body e.g. {"reminders": true, "websearch": false}
    """
    try:
        if request.method == "GET":
            return Response(
                {
                    "status": "success",
                    "data": {"feature_preferences": FeaturePreferences.get(request.user)},
                }
            )

        result = FeaturePreferences.update(request.user, request.data)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_feature_preferences")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def api_update_profile(request):
    try:
        result = Auth.update_profile(request.user, request.data)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_update_profile")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_delete_user_permanently(request):
    try:
        result = Auth.hard_delete_user(request.user)

        if result["status"] != "success":
            return Response(result, status=400)

        response = Response(result, status=200)
        _clear_refresh_cookie(response)

        return response

    except Exception:
        logger.exception("Unexpected error in api_delete_user_permanently")
        response = Response({"status": "fail", "data": "server_error"}, status=500)
        _clear_refresh_cookie(response)
        return response


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_change_password(request):
    try:
        result = Auth.change_password(request.user, request.data)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_change_password")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([RequestPasswordResetThrottle])
def api_request_password_reset(request):
    try:
        result = Auth.request_password_reset(request.data)
        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_request_password_reset")
        return Response(
            {
                "status": "success",
                "data": {
                    "message": "if_account_exists_recovery_email_sent",
                },
            },
            status=200,
        )


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ConfirmPasswordResetThrottle])
def api_confirm_password_reset(request):
    try:
        result = Auth.confirm_password_reset(request.data)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_confirm_password_reset")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def api_csrf(request):
    return JsonResponse({"status": "success", "data": {"csrfToken": get_token(request)}})


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def api_verify_email(request):
    try:
        result = Auth.verify_email(request.data)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_verify_email")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def api_resend_verification_code(request):
    try:
        result = Auth.resend_verification_code(request.data)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_resend_verification_code")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([GoogleAuthThrottle])
def api_google_auth_user(request):
    try:
        result = Auth.google_auth_user(request.data, request=request)

        if result["status"] != "success":
            return Response(result, status=400)

        if result["data"].get("linked") is True:
            # Account linking is not login. In particular, a late response
            # must never overwrite another tab/account's refresh cookie.
            return Response(result, status=200)

        response_data = {
            "status": "success",
            "data": {
                "message": result["data"]["message"],
                "access_token": result["data"]["access_token"],
                "user": result["data"]["user"],
                "spark_pool": result["data"]["spark_pool"],
            },
        }

        response = Response(response_data, status=200)
        _set_refresh_cookie(response, result["data"]["refresh_token"])

        # Deliberately no guest claim here. ``Auth.google_auth_user`` never
        # creates an account — it answers ``google_signup_not_allowed`` when no
        # account matches the Google identity — so every success on this path is
        # a login to a pre-existing account, which must never absorb guest state.
        return response

    except Exception:
        logger.exception("Unexpected error in api_google_auth_user")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([SigninThrottle])
def api_signin_user(request):
    try:
        result = Auth.signin_user(request.data, request=request)

        if result["status"] != "success":
            return Response(result, status=400)

        response_data = {
            "status": "success",
            "data": {
                "message": result["data"]["message"],
                "access_token": result["data"]["access_token"],
                "user": result["data"]["user"],
            },
        }

        response = Response(response_data, status=200)
        _set_refresh_cookie(response, result["data"]["refresh_token"])
        _claim_guest_session(request, response, result["data"]["user"]["user_id"])

        return response

    except Exception:
        logger.exception("Unexpected error in api_signin_user")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_api
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([RefreshTokenThrottle])
def api_refresh_access_token(request):
    try:
        refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)

        result = Auth.refresh_access_token(refresh_token)

        if result["status"] != "success":
            response = Response(result, status=401)
            _clear_refresh_cookie(response)
            return response

        response_data = {
            "status": "success",
            "data": {
                "message": result["data"]["message"],
                "access_token": result["data"]["access_token"],
                "user": result["data"]["user"],
            },
        }

        response = Response(response_data, status=200)
        _set_refresh_cookie(response, result["data"]["refresh_token"])

        return response

    except Exception:
        logger.exception("Unexpected error in api_refresh_access_token")
        response = Response({"status": "fail", "data": "server_error"}, status=500)
        _clear_refresh_cookie(response)
        return response


@csrf_protect_for_guests
@api_view(["POST"])
@authentication_classes([OptionalAccountJWTAuthentication])
@permission_classes([AllowAny])
@throttle_classes([SignoutThrottle])
def api_signout_user(request):
    try:
        # Clean up non-persistent BYOK config before signing out
        if request.user and request.user.is_authenticated:
            try:
                from capp.classes.byok.byok_service import BYOKService

                BYOKService.cleanup_non_persistent(request.user)
            except Exception:
                logger.warning("Failed to cleanup non-persistent BYOK config on signout")

        refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)

        result = Auth.signout_user(refresh_token)

        response = Response(result, status=200 if result["status"] == "success" else 400)
        _clear_refresh_cookie(response)

        # The guest cookie is deliberately left alone. A guest who signs in to an
        # existing account transfers nothing, so their session is still live and
        # theirs to come back to; deleting the cookie here would strand the
        # conversations and Sparks behind it. A cookie whose identity *was*
        # claimed is already dead — ``GuestIdentityService.resolve`` rejects a
        # claimed identity — so leaving it cannot revive anything either.
        return response

    except Exception:
        logger.exception("Unexpected error in api_signout_user")
        response = Response({"status": "fail", "data": "server_error"}, status=500)
        _clear_refresh_cookie(response)
        return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_get_authenticated_user(request):
    try:
        result = Auth.get_authenticated_user_data(request.user)

        if result["status"] != "success":
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_get_authenticated_user")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([SignupThrottle])
def api_signup_user(request):
    """
    Create an account.

    Signup still transfers nothing — the account cannot prove control of the
    email address yet. What it does do is record *eligibility*: if the caller
    presents a live guest credential cookie, a ``GuestTransferGrant`` binding the
    new account to that server-resolved guest identity is written in the same
    transaction as the account. The transfer itself happens on that account's
    first verified login, and only because this grant exists.

    A ``session_id`` in the body is ignored, as it has been since the guest
    identity became server-minted: it used to nominate the guest session to hand
    over, which let any caller claim another guest's conversations, sparks and
    purchases just by knowing their ID.
    """
    try:
        result = Auth.signup_user(request.data, request=request)

        if result["status"] != "success":
            return Response(result, status=400)

        response_data = {
            "status": "success",
            "data": {
                "message": result["data"]["message"],
                "user": result["data"]["user"],
                "spark_pool": result["data"]["spark_pool"],
            },
        }

        return Response(response_data, status=201)

    except Exception:
        logger.exception("Unexpected error in api_signup_user")
        return Response({"status": "fail", "data": "server_error"}, status=500)
