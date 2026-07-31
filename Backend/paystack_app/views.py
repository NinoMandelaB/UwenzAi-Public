# paystack_app/views.py
import logging
import json

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.csrf import csrf_protect_for_guests
from paystack_app import callbacks
from paystack_app.services import PaystackService
from paystack_app.models import PaystackTransaction
from capp.models import SparkPool, GuestTokenQuota

logger = logging.getLogger(__name__)

# Every ownership failure and every unknown reference answers identically, so a
# caller holding a reference it does not own cannot learn whether that payment
# exists. Probing another account's reference is indistinguishable from probing
# a reference that was never issued.
NOT_FOUND_RESPONSE = {"status": "fail", "data": "transaction_not_found"}


def _owned_transaction(request):
    """
    Resolve the transaction this caller owns, or explain the refusal.

    Returns ``(txn, guest_owner_key, error_response)``. Ownership is always
    derived server-side: from the authenticated user, or from the guest
    credential cookie. The reference in the URL is a lookup key, never proof of
    ownership — it travels in a redirect and is visible to anyone who sees it.
    """
    reference = request.query_params.get("reference")
    if not reference:
        return None, None, Response({"status": "fail", "data": "reference_required"}, status=400)

    if request.user.is_authenticated:
        txn = PaystackTransaction.objects.filter(reference=reference, user=request.user).first()
        if txn is None:
            return None, None, Response(NOT_FOUND_RESPONSE, status=404)
        return txn, None, None

    identity = GuestIdentityService.resolve(request)
    if identity is None:
        # Distinct from 404 on purpose: this says nothing about any payment, only
        # that the caller presented no usable guest credential.
        return None, None, Response({"status": "fail", "data": "guest_session_required"}, status=401)

    owner_key = identity.owner_key
    # Only the *current* guest owner may verify. A claimed purchase has moved to
    # the account and its guest_session_id is released, so a stale cookie cannot
    # reach it — origin_guest_session_id is provenance, not authorization.
    txn = PaystackTransaction.objects.filter(reference=reference, guest_session_id=owner_key).first()
    if txn is None:
        return None, None, Response(NOT_FOUND_RESPONSE, status=404)
    return txn, owner_key, None


@api_view(["GET"])
@permission_classes([AllowAny])
def verify_payment(request):
    txn, guest_owner_key, error = _owned_transaction(request)
    if error is not None:
        return error

    # The customer may be back from the redirect before the webhook landed. Ask
    # Paystack directly, then fulfill through the same validated, row-locked path
    # the webhook uses — whichever arrives second sees the fulfillment already
    # recorded and credits nothing.
    if txn.status == PaystackTransaction.StatusChoices.PENDING:
        PaystackService.verify_and_fulfill(txn.reference)
        txn.refresh_from_db()

    sparks = 0
    if txn.status == PaystackTransaction.StatusChoices.SUCCESS:
        if txn.user_id:
            spark_pool, _ = SparkPool.objects.get_or_create(
                user=txn.user,
                defaults={"sparks": 0, "token_balance": 0, "is_active": True},
            )
            sparks = spark_pool.sparks
        else:
            quota = GuestTokenQuota.objects.filter(session_id=guest_owner_key).first()
            sparks = quota.sparks if quota else 0

    return Response(
        {
            "status": "success",
            "data": {
                "reference": txn.reference,
                "status": txn.status,
                "amount": str(txn.amount),
                "currency": txn.currency,
                # Lets the UI distinguish "paid and credited" from "paid but held
                # for review" instead of showing a success that never arrived.
                "fulfillment_state": txn.fulfillment_state,
                "spark_pool_balance": sparks,
            },
        }
    )


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def initiate_payment(request):
    """
    Initiate a Paystack payment using a predefined package slug.

    The client chooses only *what* to buy. Price, currency and the return route
    are all resolved server-side and recorded as the purchase intent before the
    customer reaches Paystack.
    """
    try:
        package_slug = request.data.get("package_slug")
        package_type = request.data.get("package_type")
        guest_session_id = None

        if not package_slug or not package_type:
            return Response({"status": "fail", "data": "package_slug_and_type_required"}, status=400)

        # A client may select among server-configured return routes, but never
        # supply a destination. Anything unrecognised is refused outright rather
        # than quietly replaced with the default, so an open-redirect attempt is
        # visible instead of succeeding at a different URL.
        requested_callback = request.data.get("callback_url") or request.data.get("callback_path")
        if requested_callback:
            callback_path = callbacks.resolve_callback_path(requested_callback)
            if not callback_path:
                return Response({"status": "fail", "data": "invalid_callback_destination"}, status=400)
        else:
            callback_path = callbacks.default_path()

        # Guests can only buy spark packages
        if not request.user.is_authenticated:
            if package_type != "spark":
                return Response({"status": "fail", "data": "guests_can_only_buy_sparks"}, status=400)
            identity = GuestIdentityService.resolve(request)
            if identity is None:
                return Response({"status": "fail", "data": "guest_session_required"}, status=401)
            guest_session_id = identity.owner_key

        from capp.classes.billing.charge_service import ChargeService

        result = ChargeService.initiate_payment_from_package(
            user=request.user if request.user.is_authenticated else None,
            guest_session_id=guest_session_id,
            package_slug=package_slug,
            package_type=package_type,
            callback_path=callback_path,
        )

        if result["status"] != "success":
            return Response(result, status=400)
        return Response(result, status=200)

    except Exception:
        logger.exception("Error in initiate_payment view")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([AllowAny])
def paystack_webhook(request):
    """Receive Paystack webhook events."""
    try:
        body = request.body
        signature = request.headers.get("x-paystack-signature")
        payload = json.loads(body)

        result = PaystackService.process_webhook(payload, signature, raw_body=body)

        if result["status"] != "success":
            logger.error("Webhook processing failed: %s", result.get("data"))
            auth_failures = {
                "webhook_secret_not_configured",
                "missing_signature",
                "invalid_signature",
            }
            status_code = 401 if result.get("data") in auth_failures else 400
            return Response(result, status=status_code)

        return Response(result, status=200)

    except json.JSONDecodeError:
        return Response({"status": "fail", "data": "invalid_json"}, status=400)
    except Exception:
        logger.exception("Error in paystack_webhook view")
        return Response({"status": "fail", "data": "server_error"}, status=500)
