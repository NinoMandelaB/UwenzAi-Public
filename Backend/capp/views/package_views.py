# capp/views/package_views.py
import logging
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.billing.charge_service import ChargeService
from capp.classes.billing.package_service import PackageService
from capp.models import SparkPool, SparkTransaction, UserSubscription, SubscriptionUsage

logger = logging.getLogger(__name__)


def _is_admin(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def _admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not _is_admin(request.user):
            return Response({"status": "fail", "data": "permission_denied"}, status=403)
        return view_func(request, *args, **kwargs)

    return wrapper


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_subscription(request):
    now = timezone.now()
    sub = (
        UserSubscription.objects.filter(
            user=request.user,
            status=UserSubscription.StatusChoices.ACTIVE,
            started_at__lte=now,
            ends_at__gt=now,
        )
        .select_related("subscription_plan")
        .first()
    )

    if not sub:
        logger.info("No active subscription for user %s", request.user.user_id)
        return Response({"status": "success", "data": {"subscription": None}})

    plan = sub.subscription_plan
    usage = SubscriptionUsage.objects.filter(
        user_subscription=sub,
        cycle_start__lte=now,
        cycle_end__gt=now,
    ).first()

    subscription_data = {
        "plan_name": plan.name,
        "slug": plan.slug,
        "billing_cycle": plan.billing_cycle,
        "price": str(plan.price),
        "currency": plan.currency,
        "included_sparks": plan.included_sparks,
        "sparks_granted": sub.sparks_granted,
        "sparks_remaining": sub.sparks_remaining,
        "included_message_pairs": plan.included_message_pairs,
        "included_characters": plan.included_characters,
        "included_images": plan.included_images,
        "fair_use_message_cap": plan.fair_use_message_cap,
        "fair_use_character_cap": plan.fair_use_character_cap,
        "fair_use_image_cap": plan.fair_use_image_cap,
        "started_at": sub.started_at.isoformat(),
        "ends_at": sub.ends_at.isoformat(),
        "daily_tokens_used": sub.daily_tokens_used,
        "tokens_used_in_cycle": sub.tokens_used_in_cycle,
        "usage": {
            "message_pairs_used": usage.message_pairs_used if usage else 0,
            "characters_created": usage.characters_created if usage else 0,
            "images_generated": usage.images_generated if usage else 0,
        },
    }

    logger.info(
        "Returning subscription data for user %s: %s",
        request.user.user_id,
        subscription_data["usage"],
    )
    return Response({"status": "success", "data": {"subscription": subscription_data}})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spark_pool_balance(request):
    try:
        spark_pool, _ = SparkPool.objects.get_or_create(
            user=request.user,
            defaults={"sparks": 0, "token_balance": 0, "is_active": True},
        )
        return Response(
            {
                "status": "success",
                "data": {
                    "sparks": spark_pool.sparks,
                    "token_balance": spark_pool.token_balance,  # will be 0 in new system
                },
            }
        )
    except Exception:
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spark_transactions(request):
    try:
        txs = SparkTransaction.objects.filter(user=request.user).order_by("-created_at")[:10]
        data = [
            {
                "id": str(tx.spark_transaction_id),
                "type": tx.entry_type,
                "amount": (str(tx.spark_amount) if tx.spark_amount else str(tx.token_amount)),
                "currency": "sparks" if tx.spark_amount else "tokens",
                "description": tx.description,
                "created_at": tx.created_at.isoformat(),
            }
            for tx in txs
        ]
        return Response({"status": "success", "data": {"transactions": data}})
    except Exception:
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ------------------------- Public -------------------------
@api_view(["GET"])
@permission_classes([AllowAny])
def list_packages(request):
    """Return all active spark packages and subscription plans (public)."""
    try:
        result = ChargeService.get_available_packages()
        if result.get("status") != "success":
            return Response(result, status=400)
        return Response(result, status=200)
    except Exception:
        logger.exception("Error listing public packages")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ------------------------- Admin: Spark Packages -------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_list_spark_packages(request):
    try:
        result = PackageService.list_spark_packages(active_only=False)
        return Response(result, status=200 if result["status"] == "success" else 400)
    except Exception:
        logger.exception("admin_list_spark_packages view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_create_spark_package(request):
    try:
        result = PackageService.create_spark_package(request.data)
        status = 200 if result["status"] == "success" else 400
        return Response(result, status=status)
    except Exception:
        logger.exception("admin_create_spark_package view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_update_spark_package(request, slug):
    try:
        result = PackageService.update_spark_package(slug, request.data)
        status = 200 if result["status"] == "success" else 404 if result["data"] == "package_not_found" else 400
        return Response(result, status=status)
    except Exception:
        logger.exception("admin_update_spark_package view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_delete_spark_package(request, slug):
    try:
        result = PackageService.delete_spark_package(slug)
        status = 200 if result["status"] == "success" else 404 if result["data"] == "package_not_found" else 400
        return Response(result, status=status)
    except Exception:
        logger.exception("admin_delete_spark_package view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ------------------------- Admin: Subscription Plans -------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_list_subscription_plans(request):
    try:
        result = PackageService.list_subscription_plans(active_only=False)
        return Response(result, status=200 if result["status"] == "success" else 400)
    except Exception:
        logger.exception("admin_list_subscription_plans view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_create_subscription_plan(request):
    try:
        result = PackageService.create_subscription_plan(request.data)
        status = 200 if result["status"] == "success" else 400
        return Response(result, status=status)
    except Exception:
        logger.exception("admin_create_subscription_plan view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_update_subscription_plan(request, slug):
    try:
        result = PackageService.update_subscription_plan(slug, request.data)
        status = 200 if result["status"] == "success" else 404 if result["data"] == "plan_not_found" else 400
        return Response(result, status=status)
    except Exception:
        logger.exception("admin_update_subscription_plan view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@_admin_required
def admin_delete_subscription_plan(request, slug):
    try:
        result = PackageService.delete_subscription_plan(slug)
        status = 200 if result["status"] == "success" else 404 if result["data"] == "plan_not_found" else 400
        return Response(result, status=status)
    except Exception:
        logger.exception("admin_delete_subscription_plan view error")
        return Response({"status": "fail", "data": "server_error"}, status=500)
