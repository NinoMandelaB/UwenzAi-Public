import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.feedback.feedback_service import FeedbackService

logger = logging.getLogger(__name__)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_delete_feedback(request, feedback_id):
    """
    DELETE /api/v1/admin/feedback/<uuid:feedback_id>/delete/
    Delete feedback (admin only).
    """
    try:
        result = FeedbackService.delete_feedback(feedback_id=feedback_id, actor=request.user)

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            if result["data"] == "not_found":
                return Response(result, status=404)
            return Response(result, status=400)

        return Response(result, status=200)
    except Exception:
        logger.exception("Unexpected error in api_delete_feedback")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([AllowAny])
def api_submit_feedback(request):
    """
    POST /api/v1/feedback/submit/
    Submit user feedback with optional rating.
    IP‑based throttling (5 per hour) is applied in the service layer.
    """
    try:
        message = request.data.get("message", "")
        rating = request.data.get("rating")

        # Parse rating to int or None
        try:
            rating = int(rating) if rating else None
        except (ValueError, TypeError):
            rating = None

        # Pass authenticated user if available, else None
        user = request.user if request.user.is_authenticated else None

        result = FeedbackService.submit_feedback(
            message=message,
            rating=rating,
            user=user,
            request=request,
        )

        if result["status"] != "success":
            data = result.get("data")
            if data == "rate_limit_exceeded":
                return Response(result, status=429)  # Too Many Requests
            elif data in ("message_required", "message_too_long"):
                return Response(result, status=400)
            return Response(result, status=400)

        return Response(result, status=201)

    except Exception:
        logger.exception("Unexpected error in api_submit_feedback")
        return Response(
            {"status": "fail", "data": "server_error"},
            status=500,
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_feedback(request):
    """
    GET /api/v1/admin/feedback/
    List all feedback (admin only).
    """
    try:
        result = FeedbackService.list_feedback(actor=request.user)

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_list_feedback")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_mark_feedback_read(request, feedback_id):
    """
    POST /api/v1/admin/feedback/<uuid:feedback_id>/read/
    Mark feedback as read (admin only).
    """
    try:
        result = FeedbackService.mark_as_read(feedback_id=feedback_id, actor=request.user)

        if result["status"] != "success":
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            if result["data"] == "not_found":
                return Response(result, status=404)
            return Response(result, status=400)

        return Response(result, status=200)

    except Exception:
        logger.exception("Unexpected error in api_mark_feedback_read")
        return Response({"status": "fail", "data": "server_error"}, status=500)
