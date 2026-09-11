# capp/views/reminder_views.py
"""
REST CRUD for reminders, backing the calendar/task icon + popup in the chat
UI (list/add/edit/delete). This is a second, UI-driven way to manage the
same Reminder rows the create_reminder/update_reminder/cancel_reminder chat
tools operate on (capp.classes.reminders.tool_loop) -- both paths share the
same model and the same delete-on-terminal-state lifecycle
(capp.classes.reminders.reminder_service).
"""

import json
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.classes.reminders.tool_loop import (
    DEFAULT_TIMEZONE,
    ReminderToolError,
    ReminderDisabled,
    ReminderLimitReached,
    ReminderNotFound,
    _reminder_uuid,
    _string_arg,
    create_reminder,
    update_reminder,
    cancel_reminder,
)
from capp.models import Conversation, Reminder

logger = logging.getLogger(__name__)


def parse_request_data(request):
    """Safely parse request.data into a dict."""
    data = request.data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            raise ReminderToolError("invalid JSON") from None
    if not isinstance(data, dict):
        raise ReminderToolError("payload must be a JSON object")
    return data


def _tool_error_response(exc):
    status = 400
    if isinstance(exc, ReminderDisabled):
        status = 403
    elif isinstance(exc, ReminderLimitReached):
        status = 429
    elif isinstance(exc, ReminderNotFound):
        status = 404
    return Response({"status": "fail", "data": str(exc)}, status=status)


def _serialize(reminder: Reminder) -> dict:
    tz_name = reminder.timezone_snapshot or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return {
        "reminder_id": str(reminder.reminder_id),
        "character_id": str(reminder.character_id),
        "character_name": reminder.character.name,
        "conversation_id": str(reminder.conversation_id),
        "reminder_text": reminder.reminder_text,
        "due_at": reminder.due_at.isoformat(),
        "due_at_local": reminder.due_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "timezone": tz_name,
        "status": reminder.status,
        "notice_sent": reminder.notice_sent,
        "created_at": reminder.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_list_reminders(request):
    """GET /api/v1/reminders/ -- every PENDING reminder for the current user, across all characters/chats."""
    try:
        reminders = (
            Reminder.objects.filter(user=request.user, status=Reminder.StatusChoices.PENDING)
            .select_related("character", "conversation")
            .order_by("due_at")
        )
        return Response({"status": "success", "data": [_serialize(r) for r in reminders]})
    except Exception:
        logger.exception("Error in api_list_reminders")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_reminder(request):
    """
    POST /api/v1/reminders/create/
    body: { conversation_id, reminder_text, due_date (YYYY-MM-DD), due_time (HH:MM) }
    due_date/due_time are interpreted in the user's stored timezone, same as the create_reminder tool.
    """
    try:
        data = parse_request_data(request)
        conversation_id = _reminder_uuid(_string_arg(data, "conversation_id", required=True))
        if conversation_id is None:
            raise ReminderToolError("invalid conversation_id")

        try:
            conversation = Conversation.objects.select_related("character").get(
                conversation_id=conversation_id, user=request.user
            )
        except (Conversation.DoesNotExist, ValueError, TypeError):
            return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

        reminder = create_reminder(data, user=request.user, character=conversation.character, conversation=conversation)
        return Response({"status": "success", "data": _serialize(reminder)}, status=201)
    except ReminderToolError as exc:
        return _tool_error_response(exc)
    except Exception:
        logger.exception("Error in api_create_reminder")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
def api_update_reminder(request, reminder_id):
    """
    PUT/PATCH /api/v1/reminders/<uuid:reminder_id>/update/
    body: any of { reminder_text, due_date (YYYY-MM-DD), due_time (HH:MM) } -- only provided fields change.
    """
    try:
        data = parse_request_data(request)
        reminder, _ = update_reminder(data, user=request.user, reminder_id=reminder_id, use_snapshot=True)
        return Response({"status": "success", "data": _serialize(reminder)})
    except ReminderToolError as exc:
        return _tool_error_response(exc)
    except Exception:
        logger.exception("Error in api_update_reminder")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_delete_reminder(request, reminder_id):
    """DELETE /api/v1/reminders/<uuid:reminder_id>/delete/ -- cancels a pending reminder."""
    try:
        cancel_reminder(user=request.user, reminder_id=reminder_id)
        return Response({"status": "success", "data": {"reminder_id": str(reminder_id)}})
    except ReminderToolError as exc:
        return _tool_error_response(exc)
    except Exception:
        logger.exception("Error in api_delete_reminder")
        return Response({"status": "fail", "data": "server_error"}, status=500)
