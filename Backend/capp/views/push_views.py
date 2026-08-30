# capp/views/push_views.py

import json
import logging

from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.push.push_service import (
    PushService,
    PushSubscriptionOwnershipError,
    is_allowed_push_endpoint,
)

logger = logging.getLogger(__name__)

MAX_KEY_LENGTH = 255


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
@permission_classes([AllowAny])
def api_get_vapid_public_key(request):
    """
    Hand out the VAPID public key. Public by design — the browser needs it to
    subscribe. 503 when push is not configured so the frontend can hide the
    notifications UI instead of failing at subscribe time.
    """
    if not PushService.is_configured():
        return Response({"status": "fail", "data": "push_not_configured"}, status=503)
    return Response({"status": "success", "data": {"public_key": settings.WEBPUSH_VAPID_PUBLIC_KEY}})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_subscribe_push(request):
    try:
        if not PushService.is_configured():
            return Response({"status": "fail", "data": "push_not_configured"}, status=503)
        data = parse_request_data(request)
        if not isinstance(data, dict):
            return Response({"status": "fail", "data": "invalid_payload"}, status=400)

        endpoint = data.get("endpoint")
        keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")

        if not is_allowed_push_endpoint(endpoint):
            return Response({"status": "fail", "data": "invalid_endpoint"}, status=400)
        if not all(isinstance(key, str) and 0 < len(key) <= MAX_KEY_LENGTH for key in (p256dh, auth)):
            return Response({"status": "fail", "data": "invalid_keys"}, status=400)

        user_agent = request.META.get("HTTP_USER_AGENT", "")
        PushService.save_subscription(
            request.user,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent if isinstance(user_agent, str) else "",
        )
        return Response({"status": "success", "data": {"subscribed": True}})
    except PushSubscriptionOwnershipError:
        return Response({"status": "fail", "data": "endpoint_already_registered"}, status=409)
    except Exception:
        logger.exception("Error in api_subscribe_push")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_unsubscribe_push(request):
    try:
        data = parse_request_data(request)
        endpoint = data.get("endpoint") if isinstance(data, dict) else None

        if not isinstance(endpoint, str) or not endpoint:
            return Response({"status": "fail", "data": "invalid_endpoint"}, status=400)

        # Scoped to the caller: one account can never detach another's device.
        deleted = PushService.delete_subscription(request.user, endpoint=endpoint)
        return Response({"status": "success", "data": {"unsubscribed": deleted > 0}})
    except Exception:
        logger.exception("Error in api_unsubscribe_push")
        return Response({"status": "fail", "data": "server_error"}, status=500)
