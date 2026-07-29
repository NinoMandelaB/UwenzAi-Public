# capp/views/proactive_chat_views.py

import json
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.models import Character
from capp.classes.chat.proactive_chat_service import ProactiveChatService

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
def api_get_proactive_preference(request, character_id):
    try:
        character = Character.objects.get(character_id=character_id)
        pref = ProactiveChatService.get_or_create_preference(request.user, character)
        return Response(
            {
                "status": "success",
                "data": {
                    "enabled": pref.enabled,
                    "interval_hours": pref.interval_hours,
                },
            }
        )
    except Character.DoesNotExist:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    except Exception:
        logger.exception("Error in api_get_proactive_preference")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def api_update_proactive_preference(request, character_id):
    try:
        data = parse_request_data(request)
        logger.info("Update proactive preference payload: %s", data)

        enabled = data.get("enabled", False)
        interval_hours = data.get("interval_hours", 24)

        # Validate interval
        if interval_hours < 6 or interval_hours > 168:
            return Response(
                {"status": "fail", "data": "interval_must_be_between_6_and_168"},
                status=400,
            )

        character = Character.objects.get(character_id=character_id)
        pref = ProactiveChatService.update_preference(request.user, character, enabled, interval_hours)

        return Response(
            {
                "status": "success",
                "data": {
                    "enabled": pref.enabled,
                    "interval_hours": pref.interval_hours,
                },
            }
        )
    except Character.DoesNotExist:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)
    except Exception:
        logger.exception("Error in api_update_proactive_preference")
        return Response({"status": "fail", "data": "server_error"}, status=500)
