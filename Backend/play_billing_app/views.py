# play_billing_app/views.py
import logging
import re

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from .services import PlayBillingService

logger = logging.getLogger(__name__)

# Play product ids: lowercase alphanumerics, underscores, periods. Purchase
# tokens are long opaque strings; order ids look like GPA.1234-5678-9012.
# The charset guards matter because both values are interpolated into the
# Play Developer API URL path server-side.
PRODUCT_ID_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
PURCHASE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,512}$")
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Map service outcomes to HTTP statuses so clients can distinguish "retry
# later" (provider/config unavailable) from "this purchase will never be
# credited" (business refusal).
STATUS_FOR_OUTCOME = {
    "play_billing_not_configured": 503,
    "play_api_unavailable": 502,
}


class PlayVerifyThrottle(UserRateThrottle):
    """
    Per-account limit for purchase verification.

    Every request costs a Play Developer API call, and that quota is shared by
    all accounts of the app, so the general 120/min user limit would let a few
    accounts exhaust it for everyone. A real client needs one call per
    purchase, plus one per outstanding purchase when the app opens.
    """

    scope = "play_verify"
    rate = "10/min"


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([PlayVerifyThrottle])
def verify_play_purchase(request):
    """
    POST /api/v1/billing/play/verify/
    Body: {
        "product_id": "sparks_small",          # Play in-app product id
        "purchase_token": "...",               # from the Digital Goods flow
        "order_id": "GPA.3304-...",             # optional, when provided
    }

    Verify a Google Play purchase server-side, credit the mapped Spark package
    exactly once, acknowledge the purchase, and report the outcome.

    Play purchases are authenticated-account-only by design: Google ties them
    to the device's Play account, and crediting paid Sparks to an ephemeral
    guest session risks exactly the "browser cleared, sparks gone" loss the
    guest warning box describes. The website Paystack flow is untouched.
    """
    try:
        product_id = str(request.data.get("product_id") or "").strip()
        purchase_token = str(request.data.get("purchase_token") or "").strip()
        raw_order_id = request.data.get("order_id")
        order_id = str(raw_order_id).strip() if raw_order_id not in (None, "") else None

        if not product_id or not purchase_token:
            return Response(
                {"status": "fail", "data": "product_id_and_purchase_token_required"},
                status=400,
            )
        if not PRODUCT_ID_RE.fullmatch(product_id):
            return Response({"status": "fail", "data": "invalid_product_id"}, status=400)
        if not PURCHASE_TOKEN_RE.fullmatch(purchase_token):
            return Response({"status": "fail", "data": "invalid_purchase_token"}, status=400)
        if order_id is not None and not ORDER_ID_RE.fullmatch(order_id):
            return Response({"status": "fail", "data": "invalid_order_id"}, status=400)

        result = PlayBillingService.verify_and_fulfill(
            user=request.user,
            product_id=product_id,
            purchase_token=purchase_token,
            order_id=order_id,
        )

        if result.get("status") != "success":
            outcome = str(result.get("data") or "")
            status_code = STATUS_FOR_OUTCOME.get(outcome, 400)
            return Response(result, status=status_code)
        return Response(result, status=200)

    except Exception:
        logger.exception("Error in verify_play_purchase view")
        return Response({"status": "fail", "data": "server_error"}, status=500)
