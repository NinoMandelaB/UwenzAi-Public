"""
Audience-analytics endpoints for the public frontend.

The single endpoint here is the visit ping: fire-and-forget, never fails the
caller, and the only writer of the visit-tracking columns (see
capp/classes/analytics/visit_service.py for the semantics).
"""

import logging

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from capp.classes.analytics.visit_service import VisitService
from capp.csrf import csrf_protect_api

logger = logging.getLogger(__name__)


class VisitPingThrottle(AnonRateThrottle):
    """Coarse abuse cap. The service itself coalesces writes, so honest
    clients (one ping per app open / tab-visible) never reach it."""

    scope = "visit_ping"
    rate = "30/minute"


@csrf_protect_api
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([VisitPingThrottle])
def api_record_visit(request):
    """POST /api/v1/analytics/visit/

    Record that the public app was opened by this browser. Authenticated
    visitors are attributed to their account; everyone else to the guest
    identity in their HttpOnly cookie (the frontend guarantees one exists
    before pinging). Always answers 200 — a throttled or unattributable
    visit must never break the caller.
    """
    written = VisitService.record(request)
    if written:
        logger.debug("Recorded visit (user=%s)", getattr(getattr(request, "user", None), "pk", "guest"))
    return Response({"status": "success", "data": {"recorded": written}}, status=200)
