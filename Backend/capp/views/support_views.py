# capp/views/support_views.py
import json
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.classes.support.support_service import SupportService

logger = logging.getLogger(__name__)


def parse_request_data(request):
    """Safely parse request.data into a dict."""
    data = request.data
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_tickets(request):
    try:
        filters = {}
        if request.query_params.get("status"):
            filters["status"] = request.query_params.get("status")
        if request.query_params.get("priority"):
            filters["priority"] = request.query_params.get("priority")
        result = SupportService.list_tickets(request.user, filters)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error in api_list_tickets")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_ticket(request):
    try:
        data = parse_request_data(request)
        title = data.get("title", "").strip()
        description = data.get("description", "").strip()
        priority = data.get("priority", "medium").strip()
        result = SupportService.create_ticket(request.user, title, description, priority)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=201)
    except Exception:
        logger.exception("Error in api_create_ticket")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_get_ticket(request, ticket_id):
    try:
        result = SupportService.get_ticket(request.user, ticket_id)
        if result["status"] != "success":
            if result["data"] == "ticket_not_found":
                return Response(result, status=404)
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error in api_get_ticket")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_reply_to_ticket(request, ticket_id):
    try:
        data = parse_request_data(request)
        content = data.get("content", "").strip()
        parent_message_id = data.get("parent_message_id")
        is_internal = data.get("is_internal", False)

        result = SupportService.reply_to_ticket(
            request.user,
            ticket_id,
            content,
            parent_message_id,
            is_internal,
        )
        if result["status"] != "success":
            if result["data"] == "ticket_not_found":
                return Response(result, status=404)
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error in api_reply_to_ticket")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def api_update_ticket_status(request, ticket_id):
    try:
        data = parse_request_data(request)
        logger.info("Status update payload: %s", data)

        status = data.get("status")  # may be None

        # If the key exists, we want to update (even if value is None → treat as clear)
        assigned_to_id = data.get("assigned_to_id")
        if "assigned_to_id" in data and assigned_to_id is None:
            assigned_to_id = ""  # clear the assignee

        logger.info("Parsed: status=%s, assigned_to_id=%s", status, assigned_to_id)

        result = SupportService.update_ticket_status(
            request.user,
            ticket_id,
            status=status,
            assigned_to_id=assigned_to_id,
        )

        if result["status"] != "success":
            logger.warning("Status update failed: %s", result.get("data"))
            return Response(result, status=400)

        return Response(result, status=200)
    except Exception:
        logger.exception("Error in api_update_ticket_status")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_unread_count(request):
    try:
        result = SupportService.get_unread_count(request.user)
        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error in api_unread_count")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_mark_ticket_read(request, ticket_id):
    try:
        result = SupportService.mark_ticket_read(request.user, ticket_id)
        if result["status"] != "success":
            if result["data"] == "ticket_not_found":
                return Response(result, status=404)
            if result["data"] == "permission_denied":
                return Response(result, status=403)
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error in api_mark_ticket_read")
        return Response({"status": "fail", "data": "server_error"}, status=500)
