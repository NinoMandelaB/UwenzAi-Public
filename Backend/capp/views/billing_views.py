# capp/views/billing/billing_views.py
import logging
from datetime import timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

from capp.models import Message, SparkTransaction, SparkPool

from capp.constants import (
    MESSAGE_PAIR_COST,
    CHARACTER_GENERATION_COST,
    CHARACTER_EDIT_COST,
    IMAGE_GENERATION_COST,
    CHARACTER_PHOTO_OFFER_COST,
    CASHBACK_PUBLIC_CHARACTER,
    SPARK_BASE_VALUE_KES,
    GUEST_INITIAL_SPARKS,
    MAX_MESSAGE_CHARACTERS,
    MAX_TOKENS_PER_MESSAGE_PAIR,
)

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_pricing(request):
    """
    GET /api/v1/pricing/
    Returns current spark costs and limits.
    """
    pricing_data = {
        "message_pair_cost": MESSAGE_PAIR_COST,
        "character_generation_cost": CHARACTER_GENERATION_COST,
        "character_edit_cost": CHARACTER_EDIT_COST,
        "image_generation_cost": IMAGE_GENERATION_COST,
        "character_photo_offer_cost": CHARACTER_PHOTO_OFFER_COST,
        "cashback_public_character": CASHBACK_PUBLIC_CHARACTER,
        "spark_base_value_kes": float(SPARK_BASE_VALUE_KES),
        "guest_initial_sparks": GUEST_INITIAL_SPARKS,
        "max_message_characters": MAX_MESSAGE_CHARACTERS,
        "max_tokens_per_message_pair": MAX_TOKENS_PER_MESSAGE_PAIR,
    }
    return Response({"status": "success", "data": pricing_data}, status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def messages_today_count(request):
    """Return the number of user messages sent today."""
    today = timezone.localdate()
    count = Message.objects.filter(
        conversation__user=request.user,
        sender_type=Message.SenderType.USER,
        created_at__date=today,
    ).count()
    return Response(
        {
            "status": "success",
            "data": {"count": count},
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spark_pool_balance(request):
    """Return the authenticated user's spark balance."""
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
                    "token_balance": spark_pool.token_balance,
                },
            }
        )
    except Exception:
        logger.exception("Error fetching spark_pool balance")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spark_transactions(request):
    """
    Paginated spark_pool transactions with search & date range.
    Query params:
      - page (int, default 1)
      - page_size (int, default 20, max 100)
      - search (str, optional)
      - start_date (ISO date, default today)
      - end_date (ISO date, default today)
    If no date range is provided, the default is today's transactions.
    """
    try:
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        search = request.query_params.get("search", "").strip()
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        if page < 1:
            page = 1
        if page_size < 1 or page_size > 100:
            page_size = 20

        txs_qs = SparkTransaction.objects.filter(user=request.user).order_by("-created_at")

        # Search
        if search:
            txs_qs = txs_qs.filter(description__icontains=search)

        # Default to today's date if no range given
        if not start_date and not end_date:
            today = timezone.localdate()
            start_date = today.isoformat()
            end_date = today.isoformat()

        # Apply date filters
        if start_date:
            try:
                start = timezone.datetime.fromisoformat(start_date)
                txs_qs = txs_qs.filter(created_at__gte=start)
            except ValueError:
                return Response({"status": "fail", "data": "invalid_start_date"}, status=400)

        if end_date:
            try:
                end = timezone.datetime.fromisoformat(end_date)
                txs_qs = txs_qs.filter(created_at__lt=end + timedelta(days=1))
            except ValueError:
                return Response({"status": "fail", "data": "invalid_end_date"}, status=400)

        total = txs_qs.count()
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        txs_page = txs_qs[start_idx:end_idx]

        data = [
            {
                "id": str(tx.spark_transaction_id),
                "type": tx.entry_type,
                "amount": (str(tx.spark_amount) if tx.spark_amount else str(tx.token_amount)),
                "currency": "sparks" if tx.spark_amount else "tokens",
                "description": tx.description,
                "created_at": tx.created_at.isoformat(),
            }
            for tx in txs_page
        ]

        return Response(
            {
                "status": "success",
                "data": {
                    "transactions": data,
                    "has_next": end_idx < total,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                },
            }
        )

    except Exception:
        logger.exception("Error fetching spark_pool transactions")
        return Response({"status": "fail", "data": "server_error"}, status=500)
