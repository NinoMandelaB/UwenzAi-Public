import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.classes.moderation.message_flag_service import MessageFlagService
from capp.csrf import csrf_protect_for_guests
from capp.models import MessageFlag

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([AllowAny])
def user_flagged_messages(request, conversation_id):
    """Return message IDs that the current user/guest has already flagged in this conversation."""
    user = request.user if request.user.is_authenticated else None
    guest_id = None
    if not user:
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)
        guest_id = identity.owner_key

    if user:
        flags = MessageFlag.objects.filter(
            reported_by=user,
            message__conversation_id=conversation_id,
        )
    else:
        flags = MessageFlag.objects.filter(
            guest_session_id=guest_id,
            message__conversation_id=conversation_id,
        )

    flagged_ids = list(flags.values_list("message_id", flat=True))
    return Response(
        {
            "status": "success",
            "data": {"flagged_message_ids": [str(mid) for mid in flagged_ids]},
        }
    )


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def submit_message_flag(request):
    """Submit a flag on a message (authenticated or guest)."""
    try:
        message_id = request.data.get("message_id")
        reason = request.data.get("reason", "")
        user = request.user if request.user.is_authenticated else None
        guest_session_id = None
        if not user:
            identity = GuestIdentityService.resolve(request)
            if identity is None:
                return Response({"status": "fail", "data": "guest_session_required"}, status=401)
            guest_session_id = identity.owner_key

        if not message_id:
            return Response({"status": "fail", "data": "message_id_required"}, status=400)

        result = MessageFlagService.submit_flag(
            message_id=message_id,
            reason=reason,
            user=user,
            guest_session_id=guest_session_id,
        )

        if result["status"] != "success":
            code = 400
            if result["data"] == "already_flagged":
                code = 409
            elif result["data"] == "message_not_found":
                code = 404
            return Response(result, status=code)

        return Response(result, status=201)
    except Exception:
        logger.exception("Error submitting message flag")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_list_message_flags(request):
    """List all message flags (admin only) with pagination and optional filter by review status."""
    try:
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))

        # Extract and parse the "reviewed" query parameter
        reviewed_param = request.query_params.get("reviewed")
        reviewed = None
        if reviewed_param is not None:
            if reviewed_param.lower() == "true":
                reviewed = True
            elif reviewed_param.lower() == "false":
                reviewed = False
            # If value is something else, treat as None (no filter)

        result = MessageFlagService.list_flags(
            actor=request.user,
            page=page,
            page_size=page_size,
            reviewed=reviewed,
        )
        if result["status"] != "success":
            return Response(result, status=403)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error listing message flags")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_review_message_flag(request, flag_id):
    """Mark a message flag as reviewed (admin only)."""
    try:
        resolution_note = request.data.get("resolution_note", "")
        dismiss = request.data.get("dismiss", False)
        result = MessageFlagService.review_flag(
            flag_id=flag_id,
            actor=request.user,
            resolution_note=resolution_note,
            dismiss=dismiss,
        )
        if result["status"] != "success":
            code = 403 if result["data"] == "permission_denied" else 404
            return Response(result, status=code)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error reviewing message flag")
        return Response({"status": "fail", "data": "server_error"}, status=500)
