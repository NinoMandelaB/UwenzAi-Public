"""
Anonymous guest identity endpoints.

All three are cookie-authenticated mutations, so all three are CSRF-protected.
None of them ever returns the guest credential or the stable ownership ID in a
response body — the credential travels only as an HttpOnly cookie, and the
ownership ID stays server-side.
"""

import logging

from capp.csrf import csrf_protect_api
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from capp.classes.guest.guest_cookie import set_guest_cookie
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.classes.guest.ws_ticket_service import GuestWebSocketTicketService
from capp.throttles import (
    GuestLegacyMigrationThrottle,
    GuestSessionThrottle,
    GuestWebSocketTicketThrottle,
)

logger = logging.getLogger(__name__)


@csrf_protect_api
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([GuestSessionThrottle])
def api_ensure_guest_session(request):
    """
    Return the caller's guest session, minting one if the cookie is missing,
    expired, revoked or already claimed.

    Authenticated callers get no guest identity: an account already owns its
    data, and minting one here would create a guest to be claimed later by the
    account that is already signed in.
    """
    if request.user.is_authenticated:
        return Response({"status": "success", "data": {"guest": False}}, status=200)

    identity = GuestIdentityService.resolve(request)
    if identity is not None:
        return Response({"status": "success", "data": {"guest": True, "created": False}}, status=200)

    identity, raw_credential = GuestIdentityService.mint()
    response = Response({"status": "success", "data": {"guest": True, "created": True}}, status=201)
    return set_guest_cookie(response, raw_credential)


@csrf_protect_api
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([GuestLegacyMigrationThrottle])
def api_migrate_legacy_guest_session(request):
    """
    TEMPORARY. Exchange a legacy client-generated localStorage guest ID for a
    server-minted credential, so guests who chatted before this release keep
    their conversations and sparks.

    Gated by GUEST_LEGACY_MIGRATION_ENABLED *and* GUEST_LEGACY_MIGRATION_CUTOFF.
    See Backend/docs/GUEST_IDENTITY.md for the rollout and removal plan.
    """
    if request.user.is_authenticated:
        return Response({"status": "fail", "data": "already_authenticated"}, status=400)

    if GuestIdentityService.resolve(request) is not None:
        # A caller that already holds a valid credential has nothing to migrate,
        # and must not be able to trade a guessed legacy ID for a second one.
        return Response({"status": "fail", "data": "guest_session_already_active"}, status=409)

    enabled, reason = GuestIdentityService.legacy_migration_state()
    if not enabled:
        return Response({"status": "fail", "data": reason}, status=410)

    result = GuestIdentityService.migrate_legacy_session(request.data.get("legacy_session_id"))

    if result["status"] != "success":
        code = 400
        if result["data"] == "legacy_session_not_found":
            code = 404
        elif result["data"] in ("legacy_session_already_migrated", "legacy_session_already_claimed"):
            code = 409
        elif result["data"].startswith("legacy_migration_"):
            code = 410
        return Response(result, status=code)

    response = Response({"status": "success", "data": {"migrated": True}}, status=200)
    return set_guest_cookie(response, result["data"]["raw_credential"])


@csrf_protect_api
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([GuestWebSocketTicketThrottle])
def api_issue_guest_ws_ticket(request):
    """
    Exchange the guest cookie for a short-lived, single-use WebSocket ticket.

    Authenticated clients do not need this: they already authenticate their
    WebSocket with a short-lived JWT access token.
    """
    if request.user.is_authenticated:
        return Response({"status": "fail", "data": "authenticated_clients_use_access_token"}, status=400)

    identity = GuestIdentityService.resolve(request)
    if identity is None:
        return Response({"status": "fail", "data": "guest_session_required"}, status=401)

    channel = (request.data.get("channel") or "").strip()
    conversation_id = request.data.get("conversation_id") or None

    result = GuestWebSocketTicketService.issue(
        identity=identity,
        channel=channel,
        conversation_id=conversation_id,
    )

    if result["status"] != "success":
        code = 404 if result["data"] == "conversation_not_found" else 400
        return Response(result, status=code)

    return Response(result, status=201)
