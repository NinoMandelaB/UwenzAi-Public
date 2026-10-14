# capp/views/admin_views.py
"""
Read/write API surface for the separate uwenzAi-Admin panel.

Everything here is protected by IsAdminOrSuperuser (is_staff or is_superuser).
The user, character, Sparks and BYOK endpoints read/write fields that already
existed; the safety-ledger endpoints expose the SafetyEvent table added by the
cross-session safety feature (category/severity metadata only — never message
text, see capp/models.py::SafetyEvent).
"""

import logging
import uuid

from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import (
    CharField,
    Count,
    DateTimeField,
    Exists,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, TruncDate
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.classes.billing.balances import (
    InsufficientSparks,
    credit_locked,
    debit_locked,
    lock_spark_pool,
)
from capp.classes.billing.promo_code_service import (
    delete as promo_delete,
    generate_codes as promo_generate_codes,
    revoke as promo_revoke,
    set_active as promo_set_active,
)
from capp.classes.billing.charge_service import _push_spark_pool_balance
from capp.classes.moderation.safety_ledger import ATTENTION_EVENT_THRESHOLD, SafetyLedgerService, ledger_mode
from capp.classes.support.support_service import SupportService
from capp.models import (
    AdminActionLog,
    Character,
    Conversation,
    CustomUser,
    GuestIdentity,
    Message,
    PromoCode,
    SafetyEvent,
    SparkPool,
    SparkTransaction,
    UserBYOKConfig,
)
from paystack_app.models import PaystackTransaction
from capp.permissions import IsAdminOrSuperuser

logger = logging.getLogger(__name__)


def _paginate(request, default_page_size=20, max_page_size=100):
    try:
        page = int(request.query_params.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get("page_size", default_page_size))
    except (TypeError, ValueError):
        page_size = default_page_size

    page = max(page, 1)
    page_size = min(max(page_size, 1), max_page_size)
    return page, page_size


def _serialize_user_summary(user: CustomUser) -> dict:
    return {
        "user_id": str(user.user_id),
        "username": user.username,
        "display_name": user.get_display_name(),
        "auth_provider": user.auth_provider,
        "account_status": user.account_status,
        "is_active": user.is_active,
        "is_staff": user.is_staff,
        "is_verified": user.is_verified,
        "ban_reason": user.ban_reason,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "created_at": user.created_at.isoformat(),
        # New profile fields surfaced on the admin User detail page.
        # `gender` is the self-reported gender identity choice on
        # CustomUser.GenderChoices (may be null / "prefer_not_to_say").
        # `timezone` is the user's *current* IANA timezone as sent by
        # their client (mutable via profile update in
        # capp/classes/auth/auth.py). No historical login-location
        # tracking is done on the backend by design - that concern is
        # covered by the edge/CDN analytics layer (Cloudflare).
        "gender": user.gender,
        "timezone": user.timezone,
        # Promo campaign: which flyer code this account redeemed (at most one —
        # enforced by a DB constraint), or None. Cheap read via the related
        # name; safe on both list (annotated) and detail queries.
        "promo_code": _user_promo_summary(user),
    }


def _user_promo_summary(user) -> dict | None:
    """The promo code redeemed by this account, if any (metadata only)."""
    promo = getattr(user, "redeemed_promo_code_prefetched", None)
    if promo is None:
        try:
            promo = user.redeemed_promo_codes.first()
        except Exception:  # pragma: no cover - defensive
            promo = None
    if promo is None:
        return None
    return {
        "code": promo.code,
        "sparks_amount": promo.sparks_amount,
        "redeemed_at": promo.redeemed_at.isoformat() if promo.redeemed_at else None,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_list_users(request):
    """
    GET /api/v1/admin/users/

    Query params:
      - page, page_size
      - search: matches username / display_name (case-insensitive)
      - account_status: filter by CustomUser.AccountStatus value
      - promo_used: "true" for accounts that redeemed a promo code,
        "false" for accounts that never did (flyer campaign analytics).
    """
    try:
        page, page_size = _paginate(request)
        search = request.query_params.get("search", "").strip()
        account_status = request.query_params.get("account_status", "").strip()
        promo_used = request.query_params.get("promo_used", "").strip().lower()

        qs = (
            CustomUser.objects.filter(is_deleted=False).prefetch_related("redeemed_promo_codes").order_by("-created_at")
        )

        if promo_used in ("true", "false"):
            # At most one redemption per account (DB-unique constraint), so the
            # annotated count is only ever 0 or 1.
            qs = qs.annotate(promo_count=Count("redeemed_promo_codes"))
            qs = qs.filter(promo_count__gt=0) if promo_used == "true" else qs.filter(promo_count=0)

        if search:
            qs = qs.filter(
                Q(username__icontains=search) | Q(display_name__icontains=search) | Q(full_name__icontains=search)
            )

        if account_status:
            valid_statuses = [s[0] for s in CustomUser.AccountStatus.choices]
            if account_status not in valid_statuses:
                return Response({"status": "fail", "data": "invalid_account_status"}, status=400)
            qs = qs.filter(account_status=account_status)

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        users = qs[start:end]

        users = list(users)
        # At-a-glance safety flag (counts and a level only, never message
        # content) so an admin sees which accounts need a look without opening
        # every profile. Detail stays on the per-user safety page.
        attention = SafetyLedgerService.attention_summaries([u.user_id for u in users])
        data = []
        for u in users:
            item = _serialize_user_summary(u)
            item["safety"] = attention[u.user_id]
            data.append(item)
        return Response(
            {
                "status": "success",
                "data": {
                    "users": data,
                    "safety_attention_threshold": ATTENTION_EVENT_THRESHOLD,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_list_users")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_get_user_detail(request, user_id):
    """
    GET /api/v1/admin/users/<user_id>/

    Returns the base user summary plus:
      - last_character: the character the user most recently chatted with
      - byok: BYOK config summary (provider, model, system_prompt) if any
      - spark_balance / token_balance
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        data = _serialize_user_summary(user)

        # ── Last character chatted with ──────────────────────────────────
        last_conversation = (
            Conversation.objects.filter(user=user, last_message_at__isnull=False)
            .select_related("character")
            .order_by("-last_message_at")
            .first()
        )
        if last_conversation:
            data["last_character"] = {
                "character_id": str(last_conversation.character.character_id),
                "name": last_conversation.character.name,
                "conversation_id": str(last_conversation.conversation_id),
                "last_message_at": last_conversation.last_message_at.isoformat(),
            }
        else:
            data["last_character"] = None

        # ── BYOK summary ──────────────────────────────────────────────────
        byok = UserBYOKConfig.objects.filter(user=user).first()
        if byok:
            data["byok"] = {
                "api_url": byok.api_url,
                "model_name": byok.model_name,
                "system_prompt": byok.system_prompt,
                "is_active": byok.is_active,
                "store_on_platform": byok.store_on_platform,
                "updated_at": byok.updated_at.isoformat(),
            }
        else:
            data["byok"] = None

        # ── Spark / token balance ──────────────────────────────────────────
        pool = SparkPool.objects.filter(user=user).first()
        data["spark_balance"] = pool.sparks if pool else None
        data["token_balance"] = pool.token_balance if pool else None
        data["transaction_count"] = SparkTransaction.objects.filter(user=user).count()

        return Response({"status": "success", "data": data}, status=200)
    except Exception:
        logger.exception("Error in admin_get_user_detail")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_update_user_status(request, user_id):
    """
    POST /api/v1/admin/users/<user_id>/status/
    Body: { "account_status": "deactivated" | "suspended" | "banned" | "active" | "pending",
            "reason": "..." }

    "reason" is required whenever account_status != "active"/"pending" and is
    stored on the existing CustomUser.ban_reason field (used for any
    non-active status, not only bans, to avoid adding a second column).
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        new_status = (request.data.get("account_status") or "").strip().lower()
        reason = (request.data.get("reason") or "").strip()

        valid_statuses = [s[0] for s in CustomUser.AccountStatus.choices]
        if new_status not in valid_statuses:
            return Response({"status": "fail", "data": "invalid_account_status"}, status=400)

        restrictive_statuses = {
            CustomUser.AccountStatus.SUSPENDED,
            CustomUser.AccountStatus.BANNED,
            CustomUser.AccountStatus.DEACTIVATED,
            CustomUser.AccountStatus.SAFETY_LOCKED,
        }

        if new_status in restrictive_statuses and not reason:
            return Response({"status": "fail", "data": "reason_required"}, status=400)

        if user.is_superuser:
            return Response({"status": "fail", "data": "cannot_modify_superuser"}, status=403)

        old_status = user.account_status
        user.account_status = new_status
        # safety_locked deliberately keeps is_active=True: the user must
        # still be able to sign in, read their status and appeal through
        # support. Generation actions are refused per-send instead.
        login_blocked_statuses = restrictive_statuses - {CustomUser.AccountStatus.SAFETY_LOCKED}
        user.is_active = new_status not in login_blocked_statuses
        user.ban_reason = reason if new_status in restrictive_statuses else ""
        user.save(update_fields=["account_status", "is_active", "ban_reason", "updated_at"])

        AdminActionLog.objects.create(
            admin_user=request.user,
            action_type=AdminActionLog.ActionType.USER_STATUS_CHANGE,
            target_user=user,
            reason=reason,
            metadata={
                "old_status": old_status,
                "new_status": new_status,
            },
        )

        logger.info(
            "Admin %s set account_status=%s for user=%s (reason=%s)",
            request.user.username,
            new_status,
            user.username,
            reason,
        )

        return Response({"status": "success", "data": _serialize_user_summary(user)}, status=200)
    except Exception:
        logger.exception("Error in admin_update_user_status")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_list_user_transactions(request, user_id):
    """
    GET /api/v1/admin/users/<user_id>/transactions/

    Query params: page, page_size, start_date, end_date (all optional;
    unlike the user-facing endpoint this defaults to full history, not
    "today", since admins need the complete ledger).
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        page, page_size = _paginate(request)
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        qs = SparkTransaction.objects.filter(user=user).order_by("-created_at")

        if start_date:
            try:
                qs = qs.filter(created_at__gte=timezone.datetime.fromisoformat(start_date))
            except ValueError:
                return Response({"status": "fail", "data": "invalid_start_date"}, status=400)
        if end_date:
            try:
                qs = qs.filter(created_at__lte=timezone.datetime.fromisoformat(end_date))
            except ValueError:
                return Response({"status": "fail", "data": "invalid_end_date"}, status=400)

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        transactions = qs[start:end]

        data = [
            {
                "spark_transaction_id": str(t.spark_transaction_id),
                "entry_type": t.entry_type,
                "source": t.source,
                "spark_amount": t.spark_amount,
                "sparks_before": t.sparks_before,
                "sparks_after": t.sparks_after,
                "token_amount": t.token_amount,
                "payment_amount": str(t.payment_amount) if t.payment_amount is not None else None,
                "payment_currency": t.payment_currency,
                "reference": t.reference,
                "description": t.description,
                "created_at": t.created_at.isoformat(),
            }
            for t in transactions
        ]

        return Response(
            {
                "status": "success",
                "data": {
                    "transactions": data,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_list_user_transactions")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# Hard cap on a single manual adjustment. Prevents fat-finger disasters like a
# stray extra zero silently granting millions of Sparks. Anything genuinely
# larger has to be issued as multiple ledger entries, which is intentional -
# it forces a second reason/description per batch and stays auditable.
MAX_MANUAL_SPARK_ADJUSTMENT = 1_000_000


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_adjust_user_sparks(request, user_id):
    """
    POST /api/v1/admin/users/<user_id>/sparks/adjust/
    Body: {
        "spark_delta": int,        # positive = credit, negative = debit
        "reason": "...",           # internal audit trail (required)
        "description": "...",      # user-visible ledger note (required),
                                    # shown to the user in their Sparks
                                    # transaction history on UwenzAi.
    }

    Manually credits or debits Sparks on an authenticated user's SparkPool
    and records a matching SparkTransaction row so the change shows up in
    the user's own "Recent transactions" screen (see
    capp/views/billing_views.py::spark_transactions, which filters directly
    on user=request.user and returns tx.description).

    Uses the same locking/ledger primitives as every other paid path
    (lock_spark_pool + credit_locked/debit_locked in capp.classes.billing.balances)
    so admin adjustments cannot race concurrent chat charges or Paystack
    settlements. Debits refuse to overdraw, matching the DB-level
    spark_pool_sparks_non_negative constraint.

    The admin's internal reason is stored in SparkTransaction.metadata
    rather than SparkTransaction.description, so the customer sees a note
    an admin wrote for them ("Compensation for outage on ...") and never a
    private review string.
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        if user.is_deleted:
            return Response({"status": "fail", "data": "user_deleted"}, status=400)

        raw_delta = request.data.get("spark_delta")
        try:
            spark_delta = int(raw_delta)
        except (TypeError, ValueError):
            return Response({"status": "fail", "data": "invalid_spark_delta"}, status=400)

        if spark_delta == 0:
            return Response({"status": "fail", "data": "spark_delta_must_be_nonzero"}, status=400)

        if abs(spark_delta) > MAX_MANUAL_SPARK_ADJUSTMENT:
            return Response(
                {
                    "status": "fail",
                    "data": {
                        "code": "spark_delta_out_of_range",
                        "max_abs": MAX_MANUAL_SPARK_ADJUSTMENT,
                    },
                },
                status=400,
            )

        reason = (request.data.get("reason") or "").strip()
        description = (request.data.get("description") or "").strip()

        if not reason:
            return Response({"status": "fail", "data": "reason_required"}, status=400)
        if not description:
            return Response({"status": "fail", "data": "description_required"}, status=400)

        # Length guards so a runaway textarea can't blow past the ledger's
        # TextField / metadata JSON expectations. Kept generous enough for a
        # real explanation.
        if len(reason) > 500:
            return Response({"status": "fail", "data": "reason_too_long"}, status=400)
        if len(description) > 500:
            return Response({"status": "fail", "data": "description_too_long"}, status=400)

        is_credit = spark_delta > 0
        magnitude = abs(spark_delta)

        with db_transaction.atomic():
            pool = lock_spark_pool(user)
            sparks_before = int(pool.sparks)

            try:
                if is_credit:
                    sparks_after = credit_locked(pool, magnitude)
                else:
                    sparks_after = debit_locked(pool, magnitude)
            except InsufficientSparks as exc:
                return Response(
                    {
                        "status": "fail",
                        "data": {
                            "code": "insufficient_sparks",
                            "required": exc.required,
                            "available": exc.balance,
                        },
                    },
                    status=409,
                )

            spark_tx = SparkTransaction.objects.create(
                spark_pool=pool,
                user=user,
                entry_type=(SparkTransaction.EntryType.CREDIT if is_credit else SparkTransaction.EntryType.DEBIT),
                source=SparkTransaction.SourceChoices.ADJUSTMENT,
                spark_amount=spark_delta,  # signed, matches other adjustment rows
                sparks_before=sparks_before,
                sparks_after=sparks_after,
                description=description,
                metadata={
                    "admin_adjustment": True,
                    "admin_user_id": str(request.user.user_id),
                    "admin_username": request.user.username,
                    "reason": reason,
                },
            )

            AdminActionLog.objects.create(
                admin_user=request.user,
                action_type=AdminActionLog.ActionType.SPARK_ADJUSTMENT,
                target_user=user,
                reason=reason,
                metadata={
                    "spark_delta": spark_delta,
                    "sparks_before": sparks_before,
                    "sparks_after": sparks_after,
                    "spark_transaction_id": str(spark_tx.spark_transaction_id),
                    "description": description,
                },
            )

            # Push the new balance out to any connected UwenzAi tabs the
            # user has open so their Sparks badge updates without a reload.
            # Deferred to on_commit so the WS message never announces a
            # balance that a rolled-back adjustment never actually wrote.
            db_transaction.on_commit(lambda: _push_spark_pool_balance(user))

        logger.info(
            "Admin %s adjusted sparks for user=%s delta=%+d before=%d after=%d reason=%r",
            request.user.username,
            user.username,
            spark_delta,
            sparks_before,
            sparks_after,
            reason,
        )

        return Response(
            {
                "status": "success",
                "data": {
                    "spark_transaction_id": str(spark_tx.spark_transaction_id),
                    "entry_type": spark_tx.entry_type,
                    "source": spark_tx.source,
                    "spark_amount": spark_tx.spark_amount,
                    "sparks_before": spark_tx.sparks_before,
                    "sparks_after": spark_tx.sparks_after,
                    "description": spark_tx.description,
                    "created_at": spark_tx.created_at.isoformat(),
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_adjust_user_sparks")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_list_byok_users(request):
    """
    GET /api/v1/admin/byok-users/

    Lists every user who has a BYOK config, with provider (api_url), model
    and the clear-text system_prompt (stored unencrypted specifically for
    moderation review, see UserBYOKConfig docstring).
    """
    try:
        page, page_size = _paginate(request)
        qs = UserBYOKConfig.objects.select_related("user").order_by("-updated_at")

        active_only = request.query_params.get("active_only")
        if active_only and active_only.lower() == "true":
            qs = qs.filter(is_active=True)

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        configs = qs[start:end]

        data = [
            {
                "user_id": str(c.user.user_id),
                "username": c.user.username,
                "display_name": c.user.get_display_name(),
                "api_url": c.api_url,
                "model_name": c.model_name,
                "system_prompt": c.system_prompt,
                "is_active": c.is_active,
                "store_on_platform": c.store_on_platform,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in configs
        ]

        return Response(
            {
                "status": "success",
                "data": {
                    "byok_configs": data,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_list_byok_users")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_character_stats(request):
    """
    GET /api/v1/admin/characters/stats/

    Chat-count leaderboard: how many conversations / messages each
    character has received, most-chatted first.
    """
    try:
        qs = Character.objects.annotate(
            conversation_count=Count("conversations", distinct=True),
            message_count=Count("conversations__messages", distinct=True),
        ).order_by("-message_count", "-conversation_count")

        data = [
            {
                "character_id": str(c.character_id),
                "name": c.name,
                "status": c.status,
                "conversation_count": c.conversation_count,
                "message_count": c.message_count,
            }
            for c in qs
        ]

        return Response({"status": "success", "data": {"characters": data}}, status=200)
    except Exception:
        logger.exception("Error in admin_character_stats")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# Minimum bucket size for any "distribution" bucket returned by
# admin_user_stats. Buckets with fewer than this many users are folded
# into an "other" row so a single user can't be uniquely identified
# from the combination of small buckets (e.g. only one user in a rare
# timezone with a rare gender + preferred language). Aggregation is
# already high-level, but this is the k-anonymity floor for each
# individual dimension. Change with care: too high and small user
# bases get useless "other"-heavy charts; too low and rare
# combinations become identifying.
_USER_STATS_MIN_BUCKET_SIZE = 5

# How many entries to include in the most-active-users leaderboard.
# Kept small on purpose - the leaderboard is meant as an at-a-glance
# signal about who your power users are, not a full ranked directory.
_USER_STATS_LEADERBOARD_SIZE = 20


def _bucketize(rows, label_key, count_key="count", min_bucket=_USER_STATS_MIN_BUCKET_SIZE):
    """
    Given an iterable of {label_key: <value>, count_key: <n>} rows,
    return the same shape but with every bucket smaller than
    ``min_bucket`` folded into a single {label: "other", count: sum}
    row. Buckets are returned largest-first.

    The "other" fold-in is the k-anonymity guardrail for
    admin_user_stats: on a small userbase, a raw distribution of e.g.
    gender x timezone would leak enough to re-identify individuals.
    """
    kept = []
    other_total = 0
    for row in rows:
        label = row.get(label_key)
        count = row.get(count_key) or 0
        if count >= min_bucket and label is not None and label != "":
            kept.append({label_key: label, count_key: count})
        else:
            other_total += count
    kept.sort(key=lambda r: r[count_key], reverse=True)
    if other_total > 0:
        kept.append({label_key: "other", count_key: other_total})
    return kept


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_user_stats(request):
    """
    GET /api/v1/admin/users/stats/

    Privacy-first userbase analytics for the admin panel. Everything
    returned here is either an aggregate count or a small top-N
    leaderboard - no PII beyond username/display_name on the
    leaderboard, which the admin can already see on the Users page.

    Privacy guardrails baked into this endpoint:

      1. Soft-deleted users (is_deleted=True) are excluded from every
         count and every distribution - the moment a user asks for
         deletion, they should stop appearing in analytics.
      2. Every distribution (gender / preferred_language / timezone /
         auth_provider / account_status) is bucketed and any bucket
         below _USER_STATS_MIN_BUCKET_SIZE is folded into an "other"
         row via _bucketize(). On a small userbase, a raw distribution
         is a re-identification risk - the k-anonymity floor makes
         sure no chart cell corresponds to a single identifiable user.
      3. The signups-per-day series is aggregated to date only (no
         time-of-day) and days with fewer than the floor are dropped
         from the series (not folded, because a time series with holes
         is more honest than a fake "other" row).
      4. The most-active-users leaderboard is capped at
         _USER_STATS_LEADERBOARD_SIZE, only exposes
         username/display_name/message_count, and only counts messages
         from Message.SenderType.USER on conversations owned by the
         user (guest / SYSTEM / AI messages excluded). It mirrors the
         existing admin_character_stats page - if that leaderboard is
         acceptable, this one is too.

    No IP addresses, no geolocation lookups, no per-session history -
    those concerns are covered at the CDN/edge layer (Cloudflare).
    """
    try:
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        seven_days_ago = now - timedelta(days=7)

        # Base queryset for anything user-shaped. Soft-deleted users
        # are excluded everywhere on purpose (see docstring).
        users = CustomUser.objects.filter(is_deleted=False)

        # ── Headline totals ───────────────────────────────────────────
        totals = {
            "total_users": users.count(),
            # "Active" here means the user has actually opened the app
            # recently enough for the auth layer to have refreshed
            # last_login - not just is_active=True (which is the moderation
            # flag). See capp/classes/auth/auth.py for where last_login is
            # written.
            "active_last_7_days": users.filter(last_login__gte=seven_days_ago).count(),
            "active_last_30_days": users.filter(last_login__gte=thirty_days_ago).count(),
            "new_last_7_days": users.filter(created_at__gte=seven_days_ago).count(),
            "new_last_30_days": users.filter(created_at__gte=thirty_days_ago).count(),
            "age_verified": users.filter(age_verified=True).count(),
            "byok_users": UserBYOKConfig.objects.filter(user__is_deleted=False).count(),
        }

        # ── Signups per day, last 30 days ────────────────────────────
        # TruncDate keeps this to "which date" only - no time-of-day
        # info leaks into the response.
        signup_rows = (
            users.filter(created_at__gte=thirty_days_ago)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("user_id"))
            .order_by("day")
        )
        signups_per_day = [
            {"day": r["day"].isoformat(), "count": r["count"]}
            for r in signup_rows
            if r["count"] >= _USER_STATS_MIN_BUCKET_SIZE
        ]

        # ── Distributions ─────────────────────────────────────────────
        def _distribution(field_name):
            rows = list(users.values(field_name).annotate(count=Count("user_id")).order_by("-count"))
            return _bucketize(rows, label_key=field_name)

        distributions = {
            "gender": _distribution("gender"),
            "preferred_language": _distribution("preferred_language"),
            "timezone": _distribution("timezone"),
            "auth_provider": _distribution("auth_provider"),
            "account_status": _distribution("account_status"),
        }

        # ── Most-active users leaderboard ─────────────────────────────
        # Counts only user-authored messages on conversations owned by
        # the user. Guest conversations (user__isnull=True) and
        # AI/SYSTEM messages are excluded so the number matches "how
        # much this person actually types".
        active_users_qs = (
            users.annotate(
                message_count=Count(
                    "conversations__messages",
                    filter=Q(conversations__messages__sender_type="user"),
                    distinct=True,
                )
            )
            .filter(message_count__gt=0)
            .order_by("-message_count")[:_USER_STATS_LEADERBOARD_SIZE]
        )
        most_active_users = [
            {
                "user_id": str(u.user_id),
                "username": u.username,
                "display_name": u.get_display_name(),
                "message_count": u.message_count,
            }
            for u in active_users_qs
        ]

        return Response(
            {
                "status": "success",
                "data": {
                    "totals": totals,
                    "signups_per_day": signups_per_day,
                    "distributions": distributions,
                    "most_active_users": most_active_users,
                    "privacy": {
                        "min_bucket_size": _USER_STATS_MIN_BUCKET_SIZE,
                        "leaderboard_size": _USER_STATS_LEADERBOARD_SIZE,
                        "excludes_soft_deleted": True,
                        "excludes_guest_conversations": True,
                    },
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_user_stats")
        return Response({"status": "fail", "data": "server_error"}, status=500)


def _serialize_character_summary(character) -> dict:
    created_by = None
    if character.created_by:
        created_by = {
            "user_id": str(character.created_by.user_id),
            "display_name": character.created_by.get_display_name(),
        }

    return {
        "character_id": str(character.character_id),
        "name": character.name,
        "category": character.category,
        "status": character.status,
        "is_active": character.is_active,
        "is_public": character.is_public,
        "is_featured": character.is_featured,
        "nsfw_enabled": character.nsfw_enabled,
        "created_by": created_by,
        "created_at": character.created_at.isoformat(),
        "conversation_count": getattr(character, "conversation_count", 0),
        "message_count": getattr(character, "message_count", 0),
        "flag_count": getattr(character, "flag_count", 0),
    }


def _annotated_character_qs():
    return Character.objects.select_related("created_by").annotate(
        conversation_count=Count("conversations", distinct=True),
        message_count=Count("conversations__messages", distinct=True),
        flag_count=Count("character_flags", distinct=True),
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_list_characters(request):
    """
    GET /api/v1/admin/characters/

    Lists ALL characters regardless of visibility/status - this is the
    admin management view, distinct from the public-facing /characters/
    endpoint (which hides private/unapproved characters from regular users)
    and from admin_character_stats (a read-only leaderboard, no filters).

    Query params:
      - page, page_size
      - search: matches character name / creator username / display_name
      - status: filter by Character.StatusChoices value
      - visibility: "public" | "private" (maps to Character.is_public)
      - category: filter by Character.CategoryChoices value
    """
    try:
        page, page_size = _paginate(request)
        search = request.query_params.get("search", "").strip()
        status_filter = request.query_params.get("status", "").strip()
        visibility = request.query_params.get("visibility", "").strip()
        category = request.query_params.get("category", "").strip()

        qs = _annotated_character_qs().order_by("-created_at")

        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(created_by__username__icontains=search)
                | Q(created_by__display_name__icontains=search)
            )

        if status_filter:
            valid_statuses = [s[0] for s in Character.StatusChoices.choices]
            if status_filter not in valid_statuses:
                return Response({"status": "fail", "data": "invalid_status"}, status=400)
            qs = qs.filter(status=status_filter)

        if visibility:
            if visibility == "public":
                qs = qs.filter(is_public=True)
            elif visibility == "private":
                qs = qs.filter(is_public=False)
            else:
                return Response({"status": "fail", "data": "invalid_visibility"}, status=400)

        if category:
            valid_categories = [c[0] for c in Character.CategoryChoices.choices]
            if category not in valid_categories:
                return Response({"status": "fail", "data": "invalid_category"}, status=400)
            qs = qs.filter(category=category)

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        characters = qs[start:end]

        data = [_serialize_character_summary(c) for c in characters]
        return Response(
            {
                "status": "success",
                "data": {
                    "characters": data,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_list_characters")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_get_character_detail(request, character_id):
    """
    GET /api/v1/admin/characters/<character_id>/

    Returns the character summary plus detail fields (description,
    personality, review history, avatar) and the most recent flags raised
    against it, so an admin can decide whether to archive it without
    switching to the end-user app.
    """
    try:
        character = _annotated_character_qs().select_related("reviewed_by").filter(character_id=character_id).first()
        if not character:
            return Response({"status": "fail", "data": "character_not_found"}, status=404)

        data = _serialize_character_summary(character)
        data["short_description"] = character.short_description
        data["mood"] = character.mood
        data["intro"] = character.intro
        data["personality_description"] = character.personality_description
        data["primary_language"] = character.primary_language
        data["reviewed_by"] = (
            {
                "user_id": str(character.reviewed_by.user_id),
                "display_name": character.reviewed_by.get_display_name(),
            }
            if character.reviewed_by
            else None
        )
        data["reviewed_at"] = character.reviewed_at.isoformat() if character.reviewed_at else None
        data["rejection_reason"] = character.rejection_reason
        data["updated_at"] = character.updated_at.isoformat()

        avatar_url = None
        if character.avatar and hasattr(character.avatar, "url"):
            try:
                avatar_url = request.build_absolute_uri(character.avatar.url)
            except Exception:
                avatar_url = None
        data["avatar_url"] = avatar_url

        flags = list(character.character_flags.select_related("reported_by").order_by("-created_at")[:20])
        data["recent_flags"] = [
            {
                "flag_id": str(f.flag_id),
                "reason": f.reason,
                "reported_by": f.reported_by.get_display_name() if f.reported_by else "Guest",
                "created_at": f.created_at.isoformat(),
            }
            for f in flags
        ]

        return Response({"status": "success", "data": {"character": data}}, status=200)
    except Exception:
        logger.exception("Error in admin_get_character_detail")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_hard_delete_character(request, character_id):
    """
    POST /api/v1/admin/characters/<character_id>/hard-delete/

    Permanently removes a character row - unlike api_delete_character
    (capp/urls/character_urls.py), which only soft-deletes/archives.

    Blocking relations (cannot be hard-deleted; use archive instead):
      - conversations: carry real user chat/message history, must be
        preserved or retained for compliance.
      - character_flags: moderation audit trail against the character.

    Cascading relations (deleted alongside the character):
      - gallery_images: the character's own media. Rows cascade via the
        FK and each ImageField's file is cleaned up by the existing
        post_delete signal in capp/models.py.
      - proactive_chat_preferences: per-user "should this character
        proactively message me" toggles - orphaned data once the
        character is gone.
      - relationship_states: per-user affinity/tone counters vs. this
        character - orphaned data once the character is gone.

    The cascading relations were previously treated as blockers, which
    made every character that had ever been shown in the UI un-deletable
    (opening the character page silently creates a RelationshipState /
    ProactiveChatPreference row). That defeated the whole point of the
    endpoint, which is to clean up test/never-chatted characters.
    """
    try:
        character = Character.objects.filter(character_id=character_id).first()
        if not character:
            return Response({"status": "fail", "data": "character_not_found"}, status=404)

        blocking_relations = []
        if character.conversations.exists():
            blocking_relations.append("conversations")
        if character.character_flags.exists():
            blocking_relations.append("character_flags")

        if blocking_relations:
            return Response(
                {
                    "status": "fail",
                    "data": {
                        "code": "related_data_exists",
                        "blocking_relations": blocking_relations,
                    },
                },
                status=409,
            )

        character_id_str = str(character.character_id)
        character_name = character.name

        # Snapshot the cascade counts before the delete so the log line
        # (and the response) show exactly what was cleaned up, which
        # matters for after-the-fact review of a destructive admin action.
        cascaded = {
            "gallery_images": character.gallery_images.count(),
            "proactive_chat_preferences": character.proactive_chat_preferences.count(),
            "relationship_states": character.relationship_states.count(),
        }

        # Wrap in atomic() so a partial cascade cannot leave the character
        # row deleted while some dependents survive (or vice versa).
        with db_transaction.atomic():
            character.delete()

        AdminActionLog.objects.create(
            admin_user=request.user,
            action_type=AdminActionLog.ActionType.CHARACTER_HARD_DELETE,
            target_character_id=character_id,
            target_character_name=character_name,
            metadata={
                "cascaded": cascaded,
            },
        )

        logger.info(
            "Admin %s hard-deleted character_id=%s name=%s cascaded=%s",
            request.user.username,
            character_id_str,
            character_name,
            cascaded,
        )

        return Response(
            {
                "status": "success",
                "data": {
                    "character_id": character_id_str,
                    "deleted": True,
                    "cascaded": cascaded,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_hard_delete_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


def _serialize_audit_log_entry(entry):
    return {
        "log_id": str(entry.log_id),
        "source": "admin_action",
        "type": entry.action_type,
        "admin_user": (
            {
                "user_id": str(entry.admin_user.user_id),
                "username": entry.admin_user.username,
            }
            if entry.admin_user_id
            else None
        ),
        "target_user": (
            {
                "user_id": str(entry.target_user.user_id),
                "username": entry.target_user.username,
                "display_name": entry.target_user.get_display_name(),
            }
            if entry.target_user_id
            else None
        ),
        "target_character_id": str(entry.target_character_id) if entry.target_character_id else None,
        "target_character_name": entry.target_character_name,
        "reason": entry.reason,
        "metadata": entry.metadata,
        "amount": None,
        "currency": None,
        "reference": None,
        "created_at": entry.created_at.isoformat(),
    }


def _serialize_payment_entry(txn):
    status_to_type = {
        PaystackTransaction.StatusChoices.PENDING: "payment_pending",
        PaystackTransaction.StatusChoices.SUCCESS: "payment_success",
        PaystackTransaction.StatusChoices.FAILED: "payment_failed",
        PaystackTransaction.StatusChoices.REVERSED: "payment_reversed",
    }
    return {
        "log_id": str(txn.transaction_id),
        "source": "payment",
        "type": status_to_type.get(txn.status, f"payment_{txn.status}"),
        "admin_user": None,
        "target_user": (
            {
                "user_id": str(txn.user.user_id),
                "username": txn.user.username,
                "display_name": txn.user.get_display_name(),
            }
            if txn.user_id
            else None
        ),
        "target_character_id": None,
        "target_character_name": None,
        "reason": None,
        "metadata": {
            "reference": txn.reference,
            "package_slug": txn.package_slug,
            "package_type": txn.package_type,
            "fulfillment_state": txn.fulfillment_state,
            "paystack_reference": txn.paystack_reference,
        },
        "amount": str(txn.amount),
        "currency": txn.currency,
        "reference": txn.reference,
        "created_at": txn.created_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_audit_log(request):
    """
    GET /api/v1/admin/audit-log/

    Unified audit trail: admin actions (status changes, spark adjustments,
    character hard-deletes) and Paystack payment events, in a single
    filterable, sortable, paginated list.

    Query params:
      - source: "admin_action" | "payment" | "all" (default: all)
      - action_type: filter by AdminActionLog.ActionType value
      - user_id: filter by target user (matches target_user on admin
        actions and user on payments)
      - admin_user_id: filter by who performed the action (admin actions only)
      - start_date, end_date: ISO date range filter on created_at
      - ordering: "created_at" or "-created_at" (default: -created_at)
      - page, page_size
    """
    try:
        page, page_size = _paginate(request, default_page_size=25, max_page_size=200)
        source = request.query_params.get("source", "all").strip().lower()
        event_type = request.query_params.get("action_type", "").strip()
        user_id = request.query_params.get("user_id", "").strip()
        admin_user_id = request.query_params.get("admin_user_id", "").strip()
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        ordering = request.query_params.get("ordering", "-created_at").strip()
        if ordering not in ("created_at", "-created_at"):
            ordering = "-created_at"

        # Validate UUIDs early so partial/invalid inputs don't cause 500s
        if user_id:
            try:
                uuid.UUID(user_id)
            except (ValueError, AttributeError):
                return Response({"status": "fail", "data": "invalid_user_id"}, status=400)
        if admin_user_id:
            try:
                uuid.UUID(admin_user_id)
            except (ValueError, AttributeError):
                return Response({"status": "fail", "data": "invalid_admin_user_id"}, status=400)

        # Parse dates. start_date = beginning of that day,
        # end_date = end of that day (inclusive).
        date_filter = {}
        if start_date:
            try:
                parsed = timezone.datetime.fromisoformat(start_date)
                date_filter["created_at__gte"] = timezone.make_aware(parsed, timezone.get_current_timezone())
            except ValueError:
                return Response({"status": "fail", "data": "invalid_start_date"}, status=400)
        if end_date:
            try:
                parsed = timezone.datetime.fromisoformat(end_date)
                # Inclusive: end of the selected day
                parsed = parsed.replace(hour=23, minute=59, second=59)
                date_filter["created_at__lte"] = timezone.make_aware(parsed, timezone.get_current_timezone())
            except ValueError:
                return Response({"status": "fail", "data": "invalid_end_date"}, status=400)

        # Classify event_type: admin action types vs payment status types
        admin_action_types = [t[0] for t in AdminActionLog.ActionType.choices]
        payment_status_map = {
            "payment_pending": PaystackTransaction.StatusChoices.PENDING,
            "payment_success": PaystackTransaction.StatusChoices.SUCCESS,
            "payment_failed": PaystackTransaction.StatusChoices.FAILED,
            "payment_reversed": PaystackTransaction.StatusChoices.REVERSED,
        }
        is_admin_type = event_type in admin_action_types if event_type else False
        is_payment_type = event_type in payment_status_map if event_type else False
        if event_type and not is_admin_type and not is_payment_type:
            return Response({"status": "fail", "data": "invalid_action_type"}, status=400)

        events = []

        if source in ("all", "admin_action") and (not event_type or is_admin_type):
            admin_qs = AdminActionLog.objects.select_related("admin_user", "target_user").filter(**date_filter)
            if event_type and is_admin_type:
                admin_qs = admin_qs.filter(action_type=event_type)
            if user_id:
                admin_qs = admin_qs.filter(target_user_id=user_id)
            if admin_user_id:
                admin_qs = admin_qs.filter(admin_user_id=admin_user_id)
            events.extend((_serialize_audit_log_entry(e) for e in admin_qs))

        if source in ("all", "payment") and (not event_type or is_payment_type):
            pay_qs = PaystackTransaction.objects.select_related("user").filter(**date_filter)
            if event_type and is_payment_type:
                pay_qs = pay_qs.filter(status=payment_status_map[event_type])
            if user_id:
                pay_qs = pay_qs.filter(user_id=user_id)
            events.extend((_serialize_payment_entry(t) for t in pay_qs))

        # Sort in Python (merged from two querysets)
        reverse = ordering.startswith("-")
        events.sort(key=lambda e: e["created_at"], reverse=reverse)

        total = len(events)
        start = (page - 1) * page_size
        end = start + page_size
        page_events = events[start:end]

        return Response(
            {
                "status": "success",
                "data": {
                    "events": page_events,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": ((total + page_size - 1) // page_size if page_size else 0),
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_audit_log")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# =====================================================================
# Cross-session safety ledger (see capp/classes/moderation/safety_ledger.py)
# =====================================================================
# Privacy boundary: these endpoints expose category/severity metadata ONLY.
# Message text is never stored in SafetyEvent and can never appear in these
# responses; the user's own words stay visible only to the user.


def _serialize_safety_event(event: SafetyEvent) -> dict:
    # conversation_id is deliberately NOT exposed: it is a correlation
    # handle an admin could use to pull the conversation through other
    # endpoints. The review surface is category/severity metadata only.
    return {
        "event_id": str(event.event_id),
        "category": event.category,
        "severity": event.severity,
        "action": event.action,
        "is_care": event.is_care,
        "was_aggravated": event.was_aggravated,
        "confidence": event.confidence,
        "classifier_version": event.classifier_version,
        "created_at": event.created_at,
        "is_reversed": event.is_reversed,
        "reversed_at": event.reversed_at,
        "reversal_reason": event.reversal_reason,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_user_safety(request, user_id):
    """
    GET /api/v1/admin/users/<user_id>/safety/

    Safety-ledger summary plus a paginated event list for one user. Shows the
    aggregate state an admin needs to review an account (score, categories,
    lock state) and each recorded event's metadata — never any message text.
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        page, page_size = _paginate(request)
        events = SafetyEvent.objects.filter(user_id=user.pk)
        total = events.count()
        start = (page - 1) * page_size
        end = start + page_size
        page_events = [_serialize_safety_event(event) for event in events[start:end]]

        category_counts = dict(
            SafetyEvent.objects.filter(user_id=user.pk, is_reversed=False)
            .values_list("category")
            .annotate(n=Count("pk"))
        )

        # Advisory model-refusal surface: refusals the companion model produced
        # itself (see capp/classes/moderation/model_refusal.py). They never
        # score and never lock; at the configured threshold the system only
        # SUGGESTS that an administrator consider blocking the account — and
        # stays silent when the account is already restricted, so the panel
        # never nudges an admin toward an action that is already taken.
        model_refusal_count = SafetyLedgerService.model_refusal_count(user.pk)

        summary = {
            "account_status": user.account_status,
            "is_safety_locked": user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED,
            "lock_reason": (user.ban_reason if user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED else None),
            "active_score": SafetyLedgerService.active_score(user.pk),
            "lock_threshold": settings.SAFETY_LOCK_SCORE_THRESHOLD,
            "decay_days": settings.SAFETY_EVENT_DECAY_DAYS,
            "ledger_mode": ledger_mode(),
            "category_counts": category_counts,
            "care_event_count": SafetyEvent.objects.filter(user_id=user.pk, is_care=True).count(),
            # Blocked photo uploads (audit F4): the screening refusal count,
            # whatever the reason (child safety, bestiality, gore or death),
            # next to the other signals. Metadata only.
            "photo_block_count": SafetyEvent.objects.filter(
                user_id=user.pk,
                category__in=(
                    SafetyEvent.Category.PHOTO_CHILD_SAFETY.value,
                    SafetyEvent.Category.PHOTO_BESTIALITY.value,
                    SafetyEvent.Category.PHOTO_GORE.value,
                    SafetyEvent.Category.PHOTO_DEATH.value,
                ),
            ).count(),
            "shadowed_event_count": SafetyEvent.objects.filter(
                user_id=user.pk, action=SafetyEvent.Action.SHADOWED
            ).count(),
            "model_refusal_count": model_refusal_count,
            "model_refusal_suggest_threshold": settings.SAFETY_MODEL_REFUSAL_SUGGEST_THRESHOLD,
            "suggest_block": (
                model_refusal_count >= settings.SAFETY_MODEL_REFUSAL_SUGGEST_THRESHOLD
                and user.account_status == CustomUser.AccountStatus.ACTIVE
            ),
            "reversed_event_count": SafetyEvent.objects.filter(user_id=user.pk, is_reversed=True).count(),
            "total_events": total,
            # Whether the account currently warrants priority attention (the
            # same "priority" definition as the Users list flag): at least
            # one unreversed, non-advisory care or critical/high event. Gates
            # the direct-outreach option on the User detail page.
            "has_priority_events": SafetyLedgerService.has_priority_events(user.pk),
        }

        return Response(
            {
                "status": "success",
                "data": {
                    "summary": summary,
                    "events": page_events,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_user_safety")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_message_user(request, user_id):
    """
    POST /api/v1/admin/users/<user_id>/message/

    Direct admin outreach: open a support ticket on behalf of the user with
    the admin's message as the first entry (see
    SupportService.create_admin_outreach_ticket). The user sees it in their
    Support inbox and can reply there; admins continue the thread from the
    normal ticket surfaces.

    Body: {"message": str (required), "title": str (optional, defaults to
    "Message from the UwenzAi team")}

    The endpoint itself is deliberately not gated on the user's safety state:
    administrators are trusted and every outreach ticket is attributable to
    its sender. The admin panel only OFFERS the option for high-priority
    safety situations (has_priority_events); keeping the API permissive
    means other admin workflows can use it without a product change.
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user or user.is_deleted:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        data = request.data if not isinstance(request.data, str) else {}
        message = str(data.get("message", "")).strip()
        title = str(data.get("title", "")).strip()

        result = SupportService.create_admin_outreach_ticket(
            request.user,
            user,
            message,
            title=title or None,
        )
        if result["status"] != "success":
            code = result.get("data")
            status = 404 if code == "user_not_found" else 400
            return Response(result, status=status)
        return Response(result, status=201)
    except Exception:
        logger.exception("Error in admin_message_user")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_reverse_safety_event(request, user_id, event_id):
    """
    POST /api/v1/admin/users/<user_id>/safety/events/<event_id>/reverse/
    Body: { "reason": "..." }

    Soft-reverse one safety event (false positive) and recompute the account
    state. If the account is safety-locked and this reversal drops the score
    below the lock threshold with no unreversed critical events left, the
    account unlocks automatically. Locks held by a remaining critical event
    need the explicit unlock endpoint.
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"status": "fail", "data": "reason_required"}, status=400)

        event = SafetyEvent.objects.filter(event_id=event_id, user_id=user.pk).first()
        if not event:
            return Response({"status": "fail", "data": "event_not_found"}, status=404)

        result = SafetyLedgerService.reverse_event(event=event, admin_user=request.user, reason=reason)
        return Response(
            {
                "status": "success",
                "data": {
                    "event": _serialize_safety_event(result["event"]),
                    "unlocked": result["unlocked"],
                    "already_reversed": result["already_reversed"],
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_reverse_safety_event")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_unlock_safety(request, user_id):
    """
    POST /api/v1/admin/users/<user_id>/safety/unlock/
    Body: { "reason": "..." }

    Explicitly unlock a safety-locked account after review. Requires a
    reason (audit trail) and is refused for accounts that are not currently
    safety-locked — other statuses are managed through the normal status
    change endpoint.
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"status": "fail", "data": "reason_required"}, status=400)

        if user.account_status != CustomUser.AccountStatus.SAFETY_LOCKED:
            return Response({"status": "fail", "data": "account_not_safety_locked"}, status=409)

        updated = SafetyLedgerService.unlock_account(user=user, admin_user=request.user, reason=reason)
        return Response({"status": "success", "data": _serialize_user_summary(updated)}, status=200)
    except Exception:
        logger.exception("Error in admin_unlock_safety")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_reset_model_refusals(request, user_id):
    """
    POST /api/v1/admin/users/<user_id>/safety/model-refusals/reset/
    Body: { "reason": "..." }

    Soft-reverse every unreversed advisory model-refusal flag for one
    account, dropping the effective count (and any block suggestion) back to
    zero. Refusal flags can NEVER be removed by the user — not by clearing
    memory, deleting messages or conversations, or erasing the account —
    so this admin action (or the per-event reversal endpoint for selective
    review) is the only way the count changes. The reset is audit-logged to
    AdminActionLog with the affected count and the admin's reason.
    """
    try:
        user = CustomUser.objects.filter(user_id=user_id).first()
        if not user:
            return Response({"status": "fail", "data": "user_not_found"}, status=404)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"status": "fail", "data": "reason_required"}, status=400)

        result = SafetyLedgerService.reset_model_refusals(user=user, admin_user=request.user, reason=reason)
        return Response(
            {
                "status": "success",
                "data": {
                    "reversed_count": result["reversed_count"],
                    "model_refusal_count": result["remaining_count"],
                    "suggest_block": (
                        result["remaining_count"] >= settings.SAFETY_MODEL_REFUSAL_SUGGEST_THRESHOLD
                        and result["user"].account_status == CustomUser.AccountStatus.ACTIVE
                    ),
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_reset_model_refusals")
        return Response({"status": "fail", "data": "server_error"}, status=500)


_SHADOW_OVERVIEW_SORTS = ("score", "count", "recent")
_SHADOW_OVERVIEW_EXCLUDED_CATEGORIES = (
    SafetyEvent.Category.SELF_HARM.value,
    SafetyEvent.Category.MODEL_REFUSAL.value,
)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_shadow_overview(request):
    """
    GET /api/v1/admin/safety/shadow-overview/
    Query: days (1-365, default 30), category, min_severity, account_status,
           sort (score | count | recent), page, page_size

    One list of every account the classifier flagged while the ledger ran in
    shadow mode (recorded, never enforced), so an administrator can decide
    who needs a closer look without opening each user. The decision itself
    stays manual: block, suspend or lock through the existing user status
    controls.

    Metadata only, by design: counts, categories, severities and timestamps.
    No message text, fingerprint or conversation id is ever returned.
    Self-harm care events (support context, not violations) and advisory
    model refusals are excluded from the counts.
    """
    try:
        try:
            days = int(request.query_params.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        days = min(max(days, 1), 365)

        category = (request.query_params.get("category") or "").strip()
        valid_categories = [c for c in SafetyEvent.Category.values if c not in _SHADOW_OVERVIEW_EXCLUDED_CATEGORIES]
        if category and category not in valid_categories:
            return Response({"status": "fail", "data": "invalid_category"}, status=400)

        min_severity = (request.query_params.get("min_severity") or "").strip()
        if min_severity and min_severity not in SafetyEvent.Severity.values:
            return Response({"status": "fail", "data": "invalid_severity"}, status=400)

        account_status = (request.query_params.get("account_status") or "").strip()
        if account_status and account_status not in CustomUser.AccountStatus.values:
            return Response({"status": "fail", "data": "invalid_account_status"}, status=400)

        sort = (request.query_params.get("sort") or "score").strip()
        if sort not in _SHADOW_OVERVIEW_SORTS:
            return Response({"status": "fail", "data": "invalid_sort"}, status=400)

        page, page_size = _paginate(request)
        result = SafetyLedgerService.shadow_overview(
            days=days,
            category=category or None,
            min_severity=min_severity or None,
            account_status=account_status or None,
            sort=sort,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

        users_by_id = {
            u.user_id: u for u in CustomUser.objects.filter(user_id__in=[r["user_id"] for r in result["rows"]])
        }
        users = []
        for row in result["rows"]:
            user = users_by_id.get(row["user_id"])
            if user is None:
                continue
            users.append(
                {
                    "user_id": str(user.user_id),
                    "username": user.username,
                    "display_name": user.get_display_name(),
                    "account_status": user.account_status,
                    "shadowed_event_count": row["event_count"],
                    "shadow_score": row["shadow_score"],
                    "exceeds_threshold": row["exceeds_threshold"],
                    "critical_count": row["critical_count"],
                    "high_count": row["high_count"],
                    "categories": row["categories"],
                    "first_event_at": row["first_event_at"],
                    "last_event_at": row["last_event_at"],
                    "model_refusal_count": row["model_refusal_count"],
                    # A prompt to look, never a prompt to block: silent for
                    # accounts that are already restricted.
                    "suggest_review": (
                        (row["critical_count"] > 0 or row["exceeds_threshold"])
                        and user.account_status == CustomUser.AccountStatus.ACTIVE
                    ),
                }
            )

        total = result["total"]
        return Response(
            {
                "status": "success",
                "data": {
                    "summary": {**result["summary"], "ledger_mode": ledger_mode()},
                    "users": users,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_shadow_overview")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# Audience analytics: everyone using www.uwenzai.com — accounts AND guests.
#
# Unlike admin_user_stats (which deliberately excludes guests and does no IP
# geolocation), this view is a per-person table. It is admin-only and shows:
#   * approximate country of the latest recorded visit (edge-injected
#     CF-IPCountry captured by the visit endpoint — never a stored raw IP for
#     accounts, never a third-party IP lookup),
#   * first/last seen with an honest source label ("recorded visit" once the
#     visit ping has shipped, otherwise earliest known chat/identity activity),
#   * account creation date (null for guests),
#   * which characters the person chatted with and how many messages were
#     exchanged per character (sent user + AI messages only — pending,
#     streaming, failed, blocked and system rows are not exchanged messages).
#
# People are guests (unclaimed GuestIdentity rows, plus legacy guest sessions
# that pre-date server-minted identities and have no identity row) and accounts
# (soft-deleted accounts excluded). A guest that was claimed by an account is
# NOT listed separately: its history was re-keyed to the account at claim time,
# so listing it would double-count. The claim carries the guest's first
# recorded visit over to the account (see GuestClaimService).
# ─────────────────────────────────────────────────────────────────────────────

# Column list shared by all three arms so their .values() projections line up
# for the UNION (order and names must be identical).
_AUDIENCE_COLS = (
    "person_key",
    "visitor_type",
    "label",
    "display_name",
    "country_code",
    "first_visit_at",
    "last_visit_at",
    "earliest_activity_at",
    "latest_activity_at",
    "claimed_guest_first_visit",
    "record_created_at",
    "last_login_at",
    "identity_last_seen_at",
    "account_created_at",
    "total_messages",
    "first_seen",
    "last_seen",
)

# "exchanged" = a sent user or AI message. Everything else (system, pending,
# streaming, failed, blocked) is not part of a conversation the person had.
_AUDIENCE_SENDER_TYPES = [Message.SenderType.USER, Message.SenderType.AI]
_AUDIENCE_MESSAGE_STATUS = Message.StatusChoices.SENT

_AUDIENCE_ORDERINGS = {"last_seen", "first_seen", "created", "messages", "country", "label"}

# Cap on per-person character breakdown rows in the response. The full set is
# summarised by character_count; the list itself is the top characters.
_AUDIENCE_MAX_CHARACTERS = 10


def _audience_end_of_day(moment):
    """A YYYY-MM-DD filter is date-granular, so a "before" bound includes that
    whole day."""
    return moment + timedelta(hours=23, minutes=59, seconds=59)


def _audience_date(request, name):
    """A YYYY-MM-DD query param as an aware datetime, or None.

    Raises ValueError on a malformed value so the caller can 400 instead of
    silently ignoring the filter. Date granularity only (no time-of-day),
    mirroring the signups-per-day series in admin_user_stats.
    """
    raw = (request.query_params.get(name) or "").strip()
    if not raw:
        return None
    parsed = datetime.strptime(raw, "%Y-%m-%d")  # raises ValueError
    return timezone.make_aware(parsed, dt_timezone.utc)


def _audience_arms(request, skip_country=False):
    """The per-person arms, annotated and filtered, ready for .values().

    ``skip_country`` rebuilds the arms without the country filter, for the
    country facet counts.

    Returns (arms, meta) where arms is a list of (kind, queryset) with kind in
    {"account", "guest", "orphan"} and meta carries the parsed filter values.
    """
    params = request.query_params
    visitor_type = params.get("visitor_type", "all").strip() or "all"
    country = params.get("country", "").strip().upper() or ""
    search = params.get("search", "").strip()
    character_id = (params.get("character_id") or "").strip()
    ordering = params.get("ordering", "-last_seen").strip() or "-last_seen"

    if visitor_type not in ("all", "guests", "accounts"):
        raise ValueError("invalid_visitor_type")
    if ordering.lstrip("-") not in _AUDIENCE_ORDERINGS:
        raise ValueError("invalid_ordering")
    if character_id:
        try:
            character_id = str(uuid.UUID(character_id))
        except (ValueError, AttributeError):
            raise ValueError("invalid_character_id")

    first_after = _audience_date(request, "first_visit_after")
    first_before = _audience_date(request, "first_visit_before")
    last_after = _audience_date(request, "last_visit_after")
    last_before = _audience_date(request, "last_visit_before")
    created_after = _audience_date(request, "created_after")
    created_before = _audience_date(request, "created_before")

    for start, end, label in (
        (first_after, first_before, "first_visit"),
        (last_after, last_before, "last_visit"),
        (created_after, created_before, "created"),
    ):
        if start and end and start > end:
            raise ValueError(f"invalid_{label}_range")

    # Guest arms cannot match account-creation filters: guests have no account.
    include_accounts = visitor_type != "guests"
    include_guests = visitor_type != "accounts" and not (created_after or created_before)

    # ── Shared subquery builders ──────────────────────────────────────────
    def _sent_messages(**conversation_filters):
        return Message.objects.filter(
            sender_type__in=_AUDIENCE_SENDER_TYPES,
            status=_AUDIENCE_MESSAGE_STATUS,
            **conversation_filters,
        )

    def _dt_subquery(base, group_field, aggregate):
        return Subquery(
            base.values(group_field).annotate(m=aggregate).values("m")[:1],
            output_field=DateTimeField(),
        )

    def _count_subquery(base, group_field):
        return Subquery(
            base.values(group_field).annotate(c=Count("pk")).values("c")[:1],
            output_field=IntegerField(),
        )

    arms = []

    # ── Arm 1: accounts ──────────────────────────────────────────────────
    if include_accounts:
        account_conversations = Conversation.objects.filter(user=OuterRef("pk"))
        earliest_convo = _dt_subquery(account_conversations, "user_id", Min("started_at"))
        latest_convo = _dt_subquery(
            account_conversations,
            "user_id",
            Max(Coalesce("last_message_at", "started_at", output_field=DateTimeField())),
        )
        claimed_guest_first_visit = Subquery(
            GuestIdentity.objects.filter(claimed_by=OuterRef("pk"))
            .values("claimed_by_id")
            .annotate(m=Min("first_visit_at"))
            .values("m")[:1],
            output_field=DateTimeField(),
        )
        account_messages = _sent_messages(conversation__user=OuterRef("pk"))

        accounts = CustomUser.objects.filter(is_deleted=False).annotate(
            person_key=Concat(Value("user:"), Cast("user_id", output_field=CharField()), output_field=CharField()),
            visitor_type=Value("account", output_field=CharField()),
            label=F("username"),
            country_code=F("visit_country_code"),
            earliest_activity_at=earliest_convo,
            latest_activity_at=latest_convo,
            claimed_guest_first_visit=claimed_guest_first_visit,
            record_created_at=F("created_at"),
            last_login_at=F("last_login"),
            identity_last_seen_at=Value(None, output_field=DateTimeField()),
            account_created_at=F("created_at"),
            total_messages=_count_subquery(account_messages, "conversation__user_id"),
            first_seen=Coalesce(
                "first_visit_at",
                claimed_guest_first_visit,
                earliest_convo,
                "created_at",
                output_field=DateTimeField(),
            ),
            last_seen=Greatest("last_visit_at", latest_convo, "last_login"),
        )

        if search:
            accounts = accounts.filter(
                Q(username__icontains=search) | Q(display_name__icontains=search) | Q(full_name__icontains=search)
            )
        if country and not skip_country:
            if country == "UNKNOWN":
                accounts = accounts.filter(visit_country_code__isnull=True)
            else:
                accounts = accounts.filter(visit_country_code=country)
        if character_id:
            accounts = accounts.filter(
                Exists(
                    _sent_messages(
                        conversation__user=OuterRef("pk"),
                        conversation__character_id=character_id,
                    )
                )
            )
        if first_after:
            accounts = accounts.filter(first_seen__gte=first_after)
        if first_before:
            accounts = accounts.filter(first_seen__lte=_audience_end_of_day(first_before))
        if last_after:
            accounts = accounts.filter(last_seen__gte=last_after)
        if last_before:
            accounts = accounts.filter(last_seen__lte=_audience_end_of_day(last_before))
        if created_after:
            accounts = accounts.filter(created_at__gte=created_after)
        if created_before:
            accounts = accounts.filter(created_at__lte=_audience_end_of_day(created_before))

        arms.append(("account", accounts))

    # ── Arm 2: guest identities (not claimed) ──────────────────────────────
    if include_guests:
        # Cast for annotations resolved on the GuestIdentity queryset itself.
        guest_key = Cast("guest_id", output_field=CharField())
        # Cast for expressions resolved INSIDE correlated subqueries against the
        # outer GuestIdentity row (a plain Cast here would resolve "guest_id"
        # against Conversation and raise FieldError).
        outer_key = Cast(OuterRef("guest_id"), output_field=CharField())
        guest_conversations = Conversation.objects.filter(guest_session_id=outer_key)
        earliest_convo_g = _dt_subquery(guest_conversations, "guest_session_id", Min("started_at"))
        latest_convo_g = _dt_subquery(
            guest_conversations,
            "guest_session_id",
            Max(Coalesce("last_message_at", "started_at", output_field=DateTimeField())),
        )
        guest_messages = _sent_messages(conversation__guest_session_id=outer_key)

        guests = GuestIdentity.objects.exclude(status=GuestIdentity.Status.CLAIMED).annotate(
            person_key=Concat(Value("guest:"), guest_key, output_field=CharField()),
            visitor_type=Value("guest", output_field=CharField()),
            label=guest_key,
            display_name=Value(None, output_field=CharField()),
            country_code=F("visit_country_code"),
            earliest_activity_at=earliest_convo_g,
            latest_activity_at=latest_convo_g,
            claimed_guest_first_visit=Value(None, output_field=DateTimeField()),
            record_created_at=F("created_at"),
            last_login_at=Value(None, output_field=DateTimeField()),
            identity_last_seen_at=F("last_seen_at"),
            account_created_at=Value(None, output_field=DateTimeField()),
            total_messages=_count_subquery(guest_messages, "conversation__guest_session_id"),
            first_seen=Coalesce(
                "first_visit_at",
                earliest_convo_g,
                "created_at",
                output_field=DateTimeField(),
            ),
            last_seen=Greatest("last_visit_at", latest_convo_g, "last_seen_at"),
        )

        if search:
            guests = guests.annotate(search_key=guest_key).filter(search_key__icontains=search)
        if country and not skip_country:
            if country == "UNKNOWN":
                guests = guests.filter(visit_country_code__isnull=True)
            else:
                guests = guests.filter(visit_country_code=country)
        if character_id:
            guests = guests.filter(
                Exists(
                    _sent_messages(
                        conversation__guest_session_id=outer_key,
                        conversation__character_id=character_id,
                    )
                )
            )
        if first_after:
            guests = guests.filter(first_seen__gte=first_after)
        if first_before:
            guests = guests.filter(first_seen__lte=_audience_end_of_day(first_before))
        if last_after:
            guests = guests.filter(last_seen__gte=last_after)
        if last_before:
            guests = guests.filter(last_seen__lte=_audience_end_of_day(last_before))

        arms.append(("guest", guests))

    # ── Arm 3: legacy orphan guest sessions ────────────────────────────────
    # Conversations still keyed on a guest_session_id that has no GuestIdentity
    # row: legacy client-generated UUIDs that never went through the one-time
    # migration. They are real people with real chats, so they appear with
    # unknown visit fields rather than being silently dropped.
    if include_guests:
        identity_keys = GuestIdentity.objects.annotate(key=Cast("guest_id", output_field=CharField())).values("key")
        orphan_conversations = Conversation.objects.filter(user__isnull=True, guest_session_id__isnull=False).exclude(
            guest_session_id__in=identity_keys
        )

        orphans = orphan_conversations.values("guest_session_id").annotate(
            person_key=Concat(Value("guest:"), "guest_session_id", output_field=CharField()),
            visitor_type=Value("guest", output_field=CharField()),
            label=F("guest_session_id"),
            display_name=Value(None, output_field=CharField()),
            country_code=Value(None, output_field=CharField()),
            first_visit_at=Value(None, output_field=DateTimeField()),
            last_visit_at=Value(None, output_field=DateTimeField()),
            earliest_activity_at=Min("started_at"),
            latest_activity_at=Max(Coalesce("last_message_at", "started_at", output_field=DateTimeField())),
            claimed_guest_first_visit=Value(None, output_field=DateTimeField()),
            record_created_at=Min("started_at"),
            last_login_at=Value(None, output_field=DateTimeField()),
            identity_last_seen_at=Value(None, output_field=DateTimeField()),
            account_created_at=Value(None, output_field=DateTimeField()),
            total_messages=Subquery(
                _sent_messages(conversation__guest_session_id=OuterRef("guest_session_id"))
                .values("conversation__guest_session_id")
                .annotate(c=Count("pk"))
                .values("c")[:1],
                output_field=IntegerField(),
            ),
            first_seen=Min("started_at"),
            last_seen=Max(Coalesce("last_message_at", "started_at", output_field=DateTimeField())),
        )

        if search:
            orphans = orphans.filter(guest_session_id__icontains=search)
        # Orphans have no recorded visits and no known country: a specific
        # country filter excludes them; "unknown" keeps them.
        if country and country != "UNKNOWN" and not skip_country:
            orphans = orphans.none()
        if character_id:
            orphans = orphans.annotate(
                has_character=Exists(
                    _sent_messages(
                        conversation__guest_session_id=OuterRef("guest_session_id"),
                        conversation__character_id=character_id,
                    )
                )
            ).filter(has_character=True)
        if first_after:
            orphans = orphans.filter(first_seen__gte=first_after)
        if first_before:
            orphans = orphans.filter(first_seen__lte=_audience_end_of_day(first_before))
        if last_after:
            orphans = orphans.filter(last_seen__gte=last_after)
        if last_before:
            orphans = orphans.filter(last_seen__lte=_audience_end_of_day(last_before))

        arms.append(("orphan", orphans))

    return arms, {
        "visitor_type": visitor_type,
        "country": country,
        "search": search,
        "character_id": character_id,
        "ordering": ordering,
    }


def _audience_order_by(ordering):
    """The ORDER BY expression list for the union, with deterministic ties.

    NULLs sort last regardless of direction so that e.g. guests (no account
    date) don't leap to the top of a "newest accounts" list.
    """
    direction = "desc" if ordering.startswith("-") else "asc"
    # The public ordering names map onto the union's column names.
    field = {
        "messages": "total_messages",
        "created": "account_created_at",
    }.get(ordering.lstrip("-"), ordering.lstrip("-"))
    expr = F(field)
    order = expr.desc(nulls_last=True) if direction == "desc" else expr.asc(nulls_last=True)
    return [order, F("person_key").asc()]


def _audience_first_seen_source(row):
    if row["first_visit_at"]:
        return "recorded_visit"
    if row["claimed_guest_first_visit"]:
        return "claimed_guest_visit"
    if row["earliest_activity_at"]:
        return "conversation"
    return "record_created"


def _audience_last_seen_source(row):
    if row["last_visit_at"]:
        return "recorded_visit"
    if row["latest_activity_at"]:
        return "conversation"
    if row["last_login_at"]:
        return "login"
    if row["identity_last_seen_at"]:
        return "api_activity"
    return "record_created"


def _audience_character_breakdowns(page_rows):
    """Per-person character/message aggregates for just the page's people.

    Returns {person_key: {"characters": [...], "character_count": int}} with
    each character entry {character_id, name, user_messages, ai_messages,
    total_messages} sorted by total descending.
    """
    user_ids = [r["person_key"][5:] for r in page_rows if r["visitor_type"] == "account"]
    guest_keys = [r["person_key"][6:] for r in page_rows if r["visitor_type"] == "guest"]
    per_person = {}

    def _collect(rows, owner_field):
        for row in rows:
            key = (
                "user:" + str(row[owner_field]) if owner_field.endswith("user_id") else "guest:" + str(row[owner_field])
            )
            entry = per_person.setdefault(key, {"characters": {}, "character_count": 0})
            char_id = str(row["conversation__character_id"])
            char_entry = entry["characters"].setdefault(char_id, {"user_messages": 0, "ai_messages": 0})
            if row["sender_type"] == Message.SenderType.USER:
                char_entry["user_messages"] += row["c"]
            else:
                char_entry["ai_messages"] += row["c"]

    message_filter = {
        "sender_type__in": _AUDIENCE_SENDER_TYPES,
        "status": _AUDIENCE_MESSAGE_STATUS,
    }
    if user_ids:
        rows = (
            Message.objects.filter(conversation__user_id__in=user_ids, **message_filter)
            .values("conversation__user_id", "conversation__character_id", "sender_type")
            .annotate(c=Count("pk"))
        )
        _collect(rows, "conversation__user_id")
    if guest_keys:
        rows = (
            Message.objects.filter(conversation__guest_session_id__in=guest_keys, **message_filter)
            .values("conversation__guest_session_id", "conversation__character_id", "sender_type")
            .annotate(c=Count("pk"))
        )
        _collect(rows, "conversation__guest_session_id")

    character_ids = {cid for entry in per_person.values() for cid in entry["characters"]}
    names = {
        str(character_id): name
        for character_id, name in Character.objects.filter(character_id__in=character_ids).values_list(
            "character_id", "name"
        )
    }

    result = {}
    for person_key, entry in per_person.items():
        characters = []
        for cid, counts in entry["characters"].items():
            total = counts["user_messages"] + counts["ai_messages"]
            characters.append(
                {
                    "character_id": cid,
                    "name": names.get(cid, "Unknown character"),
                    "user_messages": counts["user_messages"],
                    "ai_messages": counts["ai_messages"],
                    "total_messages": total,
                }
            )
        characters.sort(key=lambda c: (-c["total_messages"], c["name"]))
        result[person_key] = {
            "characters": characters[:_AUDIENCE_MAX_CHARACTERS],
            "character_count": len(characters),
        }
    return result


def _audience_country_facets(arms):
    """Country counts for the filter dropdown.

    Computed per arm (annotating over a UNION is not supported). The arms are
    built with skip_country=True so the dropdown shows what selecting each
    value would yield.
    """
    counts = {}

    def _add(code, n):
        if n:
            counts[code] = counts.get(code, 0) + n

    for kind, qs in arms:
        if kind == "account":
            rows = qs.values("country_code").annotate(c=Count("user_id"))
            for row in rows:
                _add(row["country_code"], row["c"])
        elif kind == "guest":
            rows = qs.values("country_code").annotate(c=Count("guest_id"))
            for row in rows:
                _add(row["country_code"], row["c"])
        else:
            # Orphans always have unknown country.
            _add(None, qs.count())

    return [
        {"country_code": code, "count": n}
        for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] is not None, str(kv[0])))
    ]


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_audience_overview(request):
    """GET /api/v1/admin/audience/

    Per-person analytics across accounts and guests. Query params:

      - page, page_size
      - visitor_type: all (default) | guests | accounts
      - country: ISO-3166 alpha-2 code, or "unknown" for no recorded country
      - character_id: only people who exchanged at least one sent message
        with that character
      - first_visit_after / first_visit_before, last_visit_after /
        last_visit_before (YYYY-MM-DD): bounds on first/last seen
      - created_after / created_before (YYYY-MM-DD): account creation bounds
        (excludes guests, who have no account)
      - search: substring of username/display name/full name (accounts) or
        guest id (guests)
      - ordering: -last_seen (default), last_seen, -first_seen, first_seen,
        -created, created, -messages, messages, country, -country, label,
        -label

    Privacy: country is the edge-injected country of the latest recorded
    visit (approximate, never a raw IP); message counts are aggregates;
    message text is never touched. First/last seen carry a source label so
    the admin can tell a recorded visit from earliest-known-activity
    evidence — history from before the visit ping shipped is not invented.
    """
    try:
        page, page_size = _paginate(request, default_page_size=20, max_page_size=100)
        arms, meta = _audience_arms(request)
        if not arms:
            return Response(
                {
                    "status": "success",
                    "data": {
                        "people": [],
                        "page": page,
                        "page_size": page_size,
                        "total": 0,
                        "total_pages": 0,
                        "summary": {"accounts": 0, "guests": 0, "with_recorded_visit": 0},
                        "countries": [],
                    },
                },
                status=200,
            )

        projected = [qs.values(*_AUDIENCE_COLS) for _, qs in arms]
        combined = projected[0].union(*projected[1:], all=True)

        order_expressions = _audience_order_by(meta["ordering"])
        combined = combined.order_by(*order_expressions)

        total = combined.count()
        start = (page - 1) * page_size
        end = start + page_size
        page_rows = list(combined[start:end])

        breakdowns = _audience_character_breakdowns(page_rows)

        # Type counts over the same filtered set (so the summary matches the
        # table). Country facets drop only the country filter itself.
        summary = {"accounts": 0, "guests": 0, "with_recorded_visit": 0}
        for kind, qs in arms:
            if kind == "account":
                summary["accounts"] = qs.count()
                summary["with_recorded_visit"] += qs.filter(first_visit_at__isnull=False).count()
            else:
                summary["guests"] += qs.count()
                if kind == "guest":
                    summary["with_recorded_visit"] += qs.filter(first_visit_at__isnull=False).count()

        facet_arms, _ = _audience_arms(request, skip_country=True)
        countries = _audience_country_facets(facet_arms)

        people = []
        for row in page_rows:
            person_key = row["person_key"]
            breakdown = breakdowns.get(person_key, {"characters": [], "character_count": 0})
            people.append(
                {
                    "person_key": person_key,
                    "visitor_type": row["visitor_type"],
                    "label": row["label"],
                    "display_name": row["display_name"],
                    "country_code": row["country_code"],
                    "first_seen_at": row["first_seen"].isoformat() if row["first_seen"] else None,
                    "first_seen_source": _audience_first_seen_source(row),
                    "last_seen_at": row["last_seen"].isoformat() if row["last_seen"] else None,
                    "last_seen_source": _audience_last_seen_source(row),
                    "account_created_at": (
                        row["account_created_at"].isoformat() if row["account_created_at"] else None
                    ),
                    "total_messages": row["total_messages"] or 0,
                    "characters": breakdown["characters"],
                    "character_count": breakdown["character_count"],
                }
            )

        return Response(
            {
                "status": "success",
                "data": {
                    "people": people,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                    "summary": summary,
                    "countries": countries,
                },
            },
            status=200,
        )
    except ValueError as exc:
        return Response({"status": "fail", "data": str(exc)}, status=400)
    except Exception:
        logger.exception("Error in admin_audience_overview")
        return Response({"status": "fail", "data": "server_error"}, status=500)


# ==============================================================================
# Promo codes (flyer campaign)
# ==============================================================================


def _serialize_promo_code(promo: PromoCode) -> dict:
    """Admin-facing promo code row. ``redeemed_by`` is the account that used the
    code (at most one — single-use), with a deleted-account placeholder kept
    because ``redeemed_at`` remains set after erasure."""
    redeemed_by = None
    if promo.redeemed_by_id is not None:
        # select_related("redeemed_by") in the list view.
        redeemed_by_user = promo.redeemed_by
        if redeemed_by_user is not None:
            redeemed_by = {
                "user_id": str(redeemed_by_user.user_id),
                "username": redeemed_by_user.username,
                "display_name": redeemed_by_user.get_display_name(),
                "account_deleted": False,
            }
        else:
            # The link exists but the user row is gone (hard-deleted account).
            redeemed_by = {
                "user_id": str(promo.redeemed_by_id),
                "username": "(deleted account)",
                "display_name": "(deleted account)",
                "account_deleted": True,
            }
    elif promo.redeemed_at is not None:
        # Account erasure nulls redeemed_by (SET_NULL) while redeemed_at stays
        # set — the code remains consumed. Show the placeholder rather than
        # an empty cell so the campaign history stays readable.
        redeemed_by = {
            "user_id": None,
            "username": "(deleted account)",
            "display_name": "(deleted account)",
            "account_deleted": True,
        }
    return {
        "promo_code_id": str(promo.promo_code_id),
        "code": promo.code,
        "sparks_amount": promo.sparks_amount,
        "is_active": promo.is_active,
        "is_revoked": promo.is_revoked,
        "redeemed_by": redeemed_by,
        "redeemed_at": promo.redeemed_at.isoformat() if promo.redeemed_at else None,
        "note": promo.note,
        "created_at": promo.created_at.isoformat(),
        "updated_at": promo.updated_at.isoformat(),
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_list_promo_codes(request):
    """
    GET /api/v1/admin/promo-codes/

    Query params:
      - page, page_size
      - search: matches code (case-insensitive)
      - status: available | redeemed | inactive | revoked
    """
    try:
        page, page_size = _paginate(request)
        search = request.query_params.get("search", "").strip()
        status = request.query_params.get("status", "").strip().lower()

        qs = PromoCode.objects.select_related("redeemed_by").order_by("-created_at")

        if search:
            qs = qs.filter(code__icontains=search)

        if status == "available":
            qs = qs.filter(redeemed_at__isnull=True, is_revoked=False, is_active=True)
        elif status == "redeemed":
            qs = qs.filter(redeemed_at__isnull=False)
        elif status == "inactive":
            qs = qs.filter(redeemed_at__isnull=True, is_revoked=False, is_active=False)
        elif status == "revoked":
            qs = qs.filter(is_revoked=True)
        elif status:
            return Response({"status": "fail", "data": "invalid_status"}, status=400)

        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        codes = list(qs[start:end])

        return Response(
            {
                "status": "success",
                "data": {
                    "promo_codes": [_serialize_promo_code(p) for p in codes],
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": (total + page_size - 1) // page_size if page_size else 0,
                },
            },
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_list_promo_codes")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_create_promo_codes(request):
    """
    POST /api/v1/admin/promo-codes/

    Body: { count: int, sparks_amount: int, note?: string }

    Generates `count` unique single-use codes, each worth `sparks_amount` Sparks,
    ready to print on flyers. The codes are returned once, in full, in the
    response (they are also always visible in the list endpoint).
    """
    try:
        raw_count = request.data.get("count", 1)
        raw_sparks = request.data.get("sparks_amount")
        note = (request.data.get("note") or "").strip()

        try:
            count = int(raw_count)
            sparks_amount = int(raw_sparks)
        except (TypeError, ValueError):
            return Response({"status": "fail", "data": "invalid_count_or_sparks"}, status=400)

        try:
            codes = promo_generate_codes(
                count=count,
                sparks_amount=sparks_amount,
                note=note,
                created_by=request.user,
            )
        except ValueError as exc:
            return Response({"status": "fail", "data": str(exc)}, status=400)

        return Response(
            {
                "status": "success",
                "data": {
                    "created": len(codes),
                    "sparks_amount": sparks_amount,
                    "promo_codes": [_serialize_promo_code(p) for p in codes],
                },
            },
            status=201,
        )
    except Exception:
        logger.exception("Error in admin_create_promo_codes")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_set_promo_code_active(request, promo_code_id):
    """
    POST /api/v1/admin/promo-codes/<promo_code_id>/active/

    Body: { is_active: bool }

    Reversible pause/resume of an unredeemed, unrevoked code. Revoked codes are
    permanently withdrawn and cannot be reactivated; redeemed codes are fixed
    history.
    """
    try:
        raw = request.data.get("is_active")
        if not isinstance(raw, bool):
            return Response({"status": "fail", "data": "is_active_boolean_required"}, status=400)

        try:
            promo = promo_set_active(promo_code_id, is_active=raw, admin_user=request.user)
        except LookupError:
            return Response({"status": "fail", "data": "promo_code_not_found"}, status=404)
        except ValueError as exc:
            return Response({"status": "fail", "data": str(exc)}, status=409)

        return Response({"status": "success", "data": _serialize_promo_code(promo)}, status=200)
    except Exception:
        logger.exception("Error in admin_set_promo_code_active")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_revoke_promo_code(request, promo_code_id):
    """
    POST /api/v1/admin/promo-codes/<promo_code_id>/revoke/

    Permanently withdraw an unredeemed code. Refused (409 already_redeemed) for
    redeemed codes — credited Sparks live in the append-only ledger.
    """
    try:
        try:
            promo = promo_revoke(promo_code_id, admin_user=request.user)
        except LookupError:
            return Response({"status": "fail", "data": "promo_code_not_found"}, status=404)
        except ValueError as exc:
            return Response({"status": "fail", "data": str(exc)}, status=409)

        return Response({"status": "success", "data": _serialize_promo_code(promo)}, status=200)
    except Exception:
        logger.exception("Error in admin_revoke_promo_code")
        return Response({"status": "fail", "data": "server_error"}, status=500)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsAdminOrSuperuser])
def admin_delete_promo_code(request, promo_code_id):
    """
    DELETE /api/v1/admin/promo-codes/<promo_code_id>/

    Remove an unredeemed code outright. Redeemed codes are kept forever: the
    campaign history (who used which flyer) is audit-relevant. Backend refuses
    with 409 already_redeemed in that case.
    """
    try:
        try:
            promo_delete(promo_code_id, admin_user=request.user)
        except LookupError:
            return Response({"status": "fail", "data": "promo_code_not_found"}, status=404)
        except ValueError as exc:
            return Response({"status": "fail", "data": str(exc)}, status=409)

        return Response({"status": "success", "data": "deleted"}, status=200)
    except Exception:
        logger.exception("Error in admin_delete_promo_code")
        return Response({"status": "fail", "data": "server_error"}, status=500)
