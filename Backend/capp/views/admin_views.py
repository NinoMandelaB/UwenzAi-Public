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

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from capp.models import (
    Character,
    Conversation,
    CustomUser,
    SparkPool,
    SparkTransaction,
    UserBYOKConfig,
)
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

        user.account_status = new_status
        user.is_active = new_status not in restrictive_statuses
        user.ban_reason = reason if new_status in restrictive_statuses else ""
        user.save(update_fields=["account_status", "is_active", "ban_reason", "updated_at"])

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
    (capp/urls/character_urls.py), which only soft-deletes/archives. This
    is intentionally locked down for compliance reasons: a character can
    only be hard-deleted while it has zero related data (no
    conversations/messages, gallery images, proactive-chat preferences,
    relationship state, or flags). The moment a character has ever been
    chatted with, its conversation/message rows may contain user data
    that must be preserved (or explicitly retained for audit/compliance
    purposes) - archiving via the existing endpoint is the only option
    at that point, even for admins.
    """
    try:
        character = Character.objects.filter(character_id=character_id).first()
        if not character:
            return Response({"status": "fail", "data": "character_not_found"}, status=404)

        blocking_relations = []
        if character.conversations.exists():
            blocking_relations.append("conversations")
        if character.gallery_images.exists():
            blocking_relations.append("gallery_images")
        if character.proactive_chat_preferences.exists():
            blocking_relations.append("proactive_chat_preferences")
        if character.relationship_states.exists():
            blocking_relations.append("relationship_states")
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
        character.delete()

        logger.info(
            "Admin %s hard-deleted character_id=%s name=%s (no related data existed)",
            request.user.username,
            character_id_str,
            character_name,
        )

        return Response(
            {"status": "success", "data": {"character_id": character_id_str, "deleted": True}},
            status=200,
        )
    except Exception:
        logger.exception("Error in admin_hard_delete_character")
        return Response({"status": "fail", "data": "server_error"}, status=500)
