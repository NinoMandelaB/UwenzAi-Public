# capp/views/admin_views.py
"""
Read/write API surface for the separate uwenzAi-Admin panel.

Everything here is protected by IsAdminOrSuperuser (is_staff or is_superuser).
No new database tables are introduced — every endpoint reads/writes fields
that already exist on CustomUser, Conversation, SparkTransaction,
UserBYOKConfig and Character. See PR description for the field-by-field
mapping.
"""

import logging
import uuid

from datetime import timedelta

from django.db import transaction as db_transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
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
from capp.classes.billing.charge_service import _push_spark_pool_balance
from capp.models import (
    AdminActionLog,
    Character,
    Conversation,
    CustomUser,
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
    """
    try:
        page, page_size = _paginate(request)
        search = request.query_params.get("search", "").strip()
        account_status = request.query_params.get("account_status", "").strip()

        qs = CustomUser.objects.filter(is_deleted=False).order_by("-created_at")

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

        data = [_serialize_user_summary(u) for u in users]
        return Response(
            {
                "status": "success",
                "data": {
                    "users": data,
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
        }

        if new_status in restrictive_statuses and not reason:
            return Response({"status": "fail", "data": "reason_required"}, status=400)

        if user.is_superuser:
            return Response({"status": "fail", "data": "cannot_modify_superuser"}, status=403)

        old_status = user.account_status
        user.account_status = new_status
        user.is_active = new_status not in restrictive_statuses
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
        "admin_user": {
            "user_id": str(entry.admin_user.user_id),
            "username": entry.admin_user.username,
        },
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
