# capp/views/conversation_views.py

import logging
import time
import uuid
from zoneinfo import available_timezones

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, OuterRef, Subquery
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.csrf import csrf_protect_for_guests
from capp.models import (
    BillingReservation,
    Character,
    Conversation,
    CustomUser,
    Message,
    MessageAttachment,
    GuestTokenQuota,
    UserBYOKConfig,
)
from capp.classes.ai.language_detector import LanguageDetector
from capp.classes.ai.model_vision import supports_vision
from capp.classes.billing import idempotency
from capp.classes.billing.idempotency import InvalidIdempotencyKey
from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature, ReservationError
from capp.classes.characters.character_service import CharacterService
from capp.classes.chat import message_history, unread_service
from capp.classes.chat.attachment_service import (
    PhotoValidationError,
    photo_uploads_enabled,
    photo_upload_limits,
    prepare_photo,
    read_attachment_bytes,
    record_screening_failure,
    sign_attachment_url,
    store_encrypted_photo,
    verify_attachment_signature,
)
from capp.classes.moderation.image_prompt_policy import ImagePromptPolicy
from capp.classes.moderation.safety_ledger import (
    BLOCKED_CODE as SAFETY_BLOCKED_CODE,
    LOCKED_CODE as SAFETY_LOCKED_CODE,
    SafetyLedgerService,
    blocked_copy_for,
    ledger_mode,
    locked_copy_for,
)
from capp.classes.moderation.text_safety_policy import TextSafetyPolicy
from capp.classes.moderation.photo_moderation import (
    PhotoModerationBlocked,
    PhotoModerationUnavailable,
)
from capp.constants import CHARACTER_PHOTO_OFFER_COST
from capp.tasks import (
    ChatWriteUnavailable,
    chat_write_error,
    lock_writable_chat,
    process_ai_response,
    generate_chat_image,
)
from capp.throttles import ChatMessageThrottle

logger = logging.getLogger(__name__)


def _reserve_chat_image(**kwargs):
    deadline = time.monotonic() + float(getattr(settings, "IMAGE_BILLING_DB_MAX_WAIT_SECONDS", 2))
    return CharacterService._run_image_billing_operation(
        lambda: PaidFeature.reserve(**kwargs),
        deadline,
    )


_VALID_IANA_TIMEZONES = available_timezones()


def _maybe_refresh_user_timezone(request, user) -> None:
    """
    Opportunistically keep CustomUser.timezone current.

    Today it's only ever set once, at registration -- if the user travels or
    the device clock/locale changes, every downstream feature that reasons
    about "the user's local time" (reminders, get_time_context, proactive
    send windows) silently drifts. The client may send its current IANA
    timezone (e.g. from Intl.DateTimeFormat().resolvedOptions().timeZone) as
    an ``X-Client-Timezone`` header on any authenticated send; if it's a
    valid zone and differs from what's stored, update it. Best-effort and
    silent: never blocks or fails the send if the header is missing or bad.
    """
    client_tz = request.META.get("HTTP_X_CLIENT_TIMEZONE", "").strip()
    if not client_tz or client_tz not in _VALID_IANA_TIMEZONES:
        return
    if client_tz != user.timezone:
        user.timezone = client_tz
        user.save(update_fields=["timezone", "updated_at"])
        logger.info("Refreshed timezone for user=%s -> %s", user.user_id, client_tz)


def _refund_chat_image(reservation, reason):
    deadline = time.monotonic() + float(getattr(settings, "IMAGE_BILLING_DB_MAX_WAIT_SECONDS", 2))
    CharacterService._refund_image_reservation(
        reservation,
        reason=reason,
        deadline=deadline,
    )


def get_character_chat_state(character):
    if character.status == Character.StatusChoices.ARCHIVED:
        return "archived"
    if character.status == Character.StatusChoices.REJECTED:
        return "rejected"
    if character.status == Character.StatusChoices.APPROVED and not character.is_active:
        return "suspended"
    return "active"


@csrf_protect_for_guests
@api_view(["DELETE"])
@permission_classes([AllowAny])
def api_reset_conversation(request, conversation_id):
    """
    Delete all messages in a conversation.
    The requesting user must own the conversation (or be the guest who owns it).
    """
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

    # Permission check
    if request.user.is_authenticated:
        if conversation.user != request.user:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None or conversation.guest_session_id != identity.owner_key:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)

    # Delete all messages
    count, _ = Message.objects.filter(conversation=conversation).delete()
    return Response({"status": "success", "data": {"deleted": count}})


@csrf_protect_for_guests
@api_view(["DELETE"])
@permission_classes([AllowAny])
def api_delete_message(request, conversation_id, message_id):
    """
    Delete a single message from a conversation.
    """
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

    # Permission check
    if request.user.is_authenticated:
        if conversation.user != request.user:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None or conversation.guest_session_id != identity.owner_key:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)

    try:
        message = Message.objects.get(message_id=message_id, conversation=conversation)
    except Message.DoesNotExist:
        return Response({"status": "fail", "data": "message_not_found"}, status=404)

    message.delete()
    return Response({"status": "success", "data": "message_deleted"})


@api_view(["GET"])
@permission_classes([AllowAny])
def api_list_conversations(request):
    """
    Return a list of conversations for the current user or guest.
    Each item includes character info (name, avatar_url, short_description)
    and the last message.
    """
    if request.user.is_authenticated:
        conversations = unread_service.visible_conversations(user_id=request.user.pk)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)
        conversations = unread_service.visible_conversations(guest_session_id=identity.owner_key)
    conversations = conversations.select_related("character")

    # Annotate with last message details using a subquery. We surface the last
    # message's metadata as well so the client can render a preview for messages
    # that legitimately carry no text (e.g. an AI-generated image whose content
    # is stored as an empty string – see tasks.generate_chat_image).
    last_message_subquery = Message.objects.filter(conversation=OuterRef("pk")).order_by("-created_at", "-message_id")[
        :1
    ]

    conversations = conversations.annotate(
        last_msg_content=Subquery(last_message_subquery.values("content")),
        last_msg_sender=Subquery(last_message_subquery.values("sender_type")),
        last_msg_time=Subquery(last_message_subquery.values("created_at")),
        last_msg_metadata=Subquery(last_message_subquery.values("metadata")),
    )

    conversations = unread_service.annotate_unread(conversations)

    # Only surface conversations that actually contain at least one message.
    # ``last_msg_time`` is NULL only when a conversation has no messages (e.g. a
    # character was opened but nothing was ever sent). This intentionally does
    # NOT filter on message *content*: an image-only last message has empty
    # content but is a real chat and must still be listed.
    conversations = conversations.filter(last_msg_time__isnull=False)

    # Deterministic ordering: newest activity first, with a stable tie-breaker so
    # conversations that share an identical last-message timestamp keep a fixed
    # order across requests (prevents flicker / apparent "missing" rows on ties).
    conversations = conversations.order_by(F("last_msg_time").desc(nulls_last=True), "-conversation_id")

    def _preview_for(content, metadata):
        """Non-empty preview text for the chat list row."""
        if content and content.strip():
            return content
        meta = metadata if isinstance(metadata, dict) else {}
        msg_type = meta.get("type")
        if msg_type in ("generated_image", "image", "image_request", "user_photo"):
            return "\U0001f4f7 Photo"
        if msg_type == "photo_offer":
            return "\U0001f4f7 Photo offer"
        return ""

    result = []
    for conv in conversations:
        character = conv.character
        avatar_url = None
        if character.avatar and hasattr(character.avatar, "url"):
            raw_avatar_url = character.avatar.url
            try:
                avatar_url = request.build_absolute_uri(raw_avatar_url)
            except Exception:
                avatar_url = raw_avatar_url  # fallback

        meta = conv.last_msg_metadata if isinstance(conv.last_msg_metadata, dict) else {}
        result.append(
            {
                "conversation_id": str(conv.conversation_id),
                "character": {
                    "character_id": str(character.character_id),
                    "name": character.name,
                    "avatar_url": avatar_url,
                    "short_description": character.short_description or "",
                },
                "last_message": {
                    "content": conv.last_msg_content or "",
                    "preview": _preview_for(conv.last_msg_content, meta),
                    "sender_type": conv.last_msg_sender or "",
                    "type": meta.get("type") or "",
                },
                "last_message_at": (conv.last_msg_time.isoformat() if conv.last_msg_time else None),
                "status": conv.status,
                "unread_count": conv.unread_count,
            }
        )

    logger.info(
        "api_list_conversations: returned %d conversation(s) for %s",
        len(result),
        (f"user={request.user.pk}" if request.user.is_authenticated else "guest"),
    )
    return Response({"status": "success", "data": {"conversations": result}})


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def api_mark_conversation_read(request, conversation_id):
    """
    Clear a conversation's unread badge for its owner.
    """
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

    if request.user.is_authenticated:
        if conversation.user != request.user:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None or conversation.guest_session_id != identity.owner_key:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)

    unread_service.mark_read(conversation)
    payload = unread_service.unread_payload(conversation)
    # Other tabs and devices on the same account are showing a stale badge.
    unread_service.broadcast_unread(conversation)
    return Response({"status": "success", "data": payload})


@api_view(["GET"])
@permission_classes([AllowAny])
def api_unread_summary(request):
    """
    Unread counts for the nav badge, without paying for the full chat list.
    """
    if request.user.is_authenticated:
        conversations = unread_service.visible_conversations(user_id=request.user.pk)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)
        conversations = unread_service.visible_conversations(guest_session_id=identity.owner_key)

    rows = unread_service.annotate_unread(conversations).values("conversation_id", "unread_count")
    unread = [
        {"conversation_id": str(row["conversation_id"]), "unread_count": row["unread_count"]}
        for row in rows
        if row["unread_count"] > 0
    ]
    return Response(
        {
            "status": "success",
            "data": {
                "total_unread": sum(row["unread_count"] for row in unread),
                "conversations": unread,
            },
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def api_list_messages(request, conversation_id):
    """
    Return one page of a conversation's messages (newest page first), plus
    character info and the cursor for continuing into older history.

    Query params:
      - before : cursor from a previous response's ``pagination.next_before``
      - limit  : page size, capped at ``MAX_PAGE_SIZE``
    """
    try:
        conversation = Conversation.objects.select_related("character").get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

    # Access control
    if request.user.is_authenticated:
        if conversation.user != request.user:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None or conversation.guest_session_id != identity.owner_key:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)

    # Pagination
    raw_cursor = request.query_params.get("before", "").strip()
    cursor = None
    if raw_cursor:
        try:
            cursor = message_history.decode_cursor(raw_cursor, conversation=conversation)
        except message_history.InvalidCursor:
            return Response({"status": "fail", "data": message_history.InvalidCursor.code}, status=400)

    page_size = message_history.resolve_page_size(request.query_params.get("limit"))
    page, has_more, next_before = message_history.fetch_page(conversation, cursor=cursor, page_size=page_size)

    def _build_image_url(msg):
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}

        # Preferred: resolve a fresh URL from the durable storage key. With a
        # private bucket this yields a short-lived presigned URL (absolute); with
        # local storage it yields a relative /media/ path we make absolute below.
        name = meta.get("image_name")
        if name:
            try:
                url = default_storage.url(name)
                if url.startswith("http://") or url.startswith("https://"):
                    return url
                return request.build_absolute_uri(url)
            except Exception:
                logger.warning("could not resolve image url for stored key")

        # Legacy fallback: messages created before the durable-key change stored
        # the (possibly relative, possibly stale) URL directly.
        raw = meta.get("image_url")
        if not raw:
            return None
        try:
            return request.build_absolute_uri(raw)
        except Exception:
            return raw

    def _serialize(msg, grouped_attachments):
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        row = {
            "message_id": str(msg.message_id),
            "sender_type": msg.sender_type,
            "content": msg.content,
            "image_url": _build_image_url(msg),
            "attachments": _serialize_attachments(
                grouped_attachments.get(msg.message_id, []),
                conversation.conversation_id,
                request,
            ),
            "created_at": msg.created_at.isoformat(),
            # ``reply_to_id`` rather than ``reply_to``: touching the relation
            # would fetch the parent row once per message.
            "reply_to": str(msg.reply_to_id) if msg.reply_to_id else None,
            "type": meta.get("type") or "",
            # Persisted system messages carry the same localisation code the
            # WebSocket sends, so history renders in the user's language too.
            "code": meta.get("code") or "",
        }
        # Photo offers are actionable cards: history must carry the same
        # payload the WebSocket pushes so a reloaded page can still show the
        # accept/decline state.
        if row["type"] == "photo_offer":
            row["offer"] = {
                "prompt": meta.get("prompt") or "",
                "cost": meta.get("offer_cost"),
                "status": meta.get("offer_status") or "pending",
            }
        return row

    grouped_attachments = _conversation_attachments(conversation, [m.message_id for m in page])
    messages_list = [_serialize(msg, grouped_attachments) for msg in page]

    # Build character info
    character = conversation.character
    avatar_url = None
    if character.avatar and hasattr(character.avatar, "url"):
        raw_avatar_url = character.avatar.url
        try:
            avatar_url = request.build_absolute_uri(raw_avatar_url)
        except Exception:
            avatar_url = raw_avatar_url

    character_info = {
        "name": character.name,
        "avatar_url": avatar_url,
        "short_description": character.short_description or "",
        "is_active": character.is_active,
        "review_status": character.status,
        "chat_state": get_character_chat_state(character),
    }

    return Response(
        {
            "status": "success",
            "data": {
                "messages": messages_list,
                "character": character_info,
                "pagination": {
                    "has_more": has_more,
                    "next_before": next_before,
                    "page_size": page_size,
                },
            },
        }
    )


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
def api_start_conversation(request):
    """
    Start a new conversation with a character.
    If an existing conversation (any status) already exists for this user/character,
    reactivate it and return that one. Otherwise create a new one.
    """
    raw_id = request.data.get("character_id", "")
    if isinstance(raw_id, dict):
        raw_id = raw_id.get("character_id") or raw_id.get("slug") or ""
    raw_id = str(raw_id).strip() if raw_id else ""

    if not raw_id:
        return Response({"status": "fail", "data": "character_id_required"}, status=400)

    # Try UUID first, then slug
    try:
        uuid.UUID(raw_id)
        character = Character.objects.filter(character_id=raw_id).first()
    except (ValueError, AttributeError):
        character = Character.objects.filter(slug=raw_id).first()

    if not character:
        return Response({"status": "fail", "data": "character_not_found"}, status=404)

    chat_state = get_character_chat_state(character)

    # --- Guard: only blocked character states should prevent chat ---
    if chat_state in ("archived", "rejected", "suspended"):
        return Response(
            {
                "status": "fail",
                "data": "character_unavailable",
                "message": "This character is no longer available for chat.",
                "chat_state": chat_state,
            },
            status=400,
        )

    if request.user.is_authenticated:
        # Look for ANY existing conversation for this user+character
        existing = Conversation.objects.filter(
            user=request.user,
            character=character,
        ).first()
        if existing:
            # If it's CLOSED (from a previous soft‑delete), reopen it
            if existing.status != Conversation.StatusChoices.ACTIVE:
                existing.status = Conversation.StatusChoices.ACTIVE
                existing.save(update_fields=["status", "updated_at"])
            return Response(
                {
                    "status": "success",
                    "data": {
                        "conversation_id": str(existing.conversation_id),
                        "is_new": False,
                    },
                },
                status=200,
            )

        # No existing conversation – create a new one
        try:
            conversation = Conversation.objects.create(
                user=request.user,
                character=character,
                status=Conversation.StatusChoices.ACTIVE,
            )
        except IntegrityError:
            # Race condition – another request created it, grab the new one
            existing = Conversation.objects.filter(
                user=request.user,
                character=character,
                status=Conversation.StatusChoices.ACTIVE,
            ).first()
            return Response(
                {
                    "status": "success",
                    "data": {
                        "conversation_id": str(existing.conversation_id),
                        "is_new": False,
                    },
                },
                status=200,
            )

    else:
        # Guest flow – same logic, but ownership comes only from the cookie.
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)
        session_id = identity.owner_key

        existing = Conversation.objects.filter(
            guest_session_id=session_id,
            character=character,
        ).first()
        if existing:
            if existing.status != Conversation.StatusChoices.ACTIVE:
                existing.status = Conversation.StatusChoices.ACTIVE
                existing.save(update_fields=["status", "updated_at"])
            return Response(
                {
                    "status": "success",
                    "data": {
                        "conversation_id": str(existing.conversation_id),
                        "is_new": False,
                    },
                },
                status=200,
            )

        try:
            conversation = Conversation.objects.create(
                character=character,
                guest_session_id=session_id,
                status=Conversation.StatusChoices.ACTIVE,
            )
        except IntegrityError:
            existing = Conversation.objects.filter(
                guest_session_id=session_id,
                character=character,
                status=Conversation.StatusChoices.ACTIVE,
            ).first()
            return Response(
                {
                    "status": "success",
                    "data": {
                        "conversation_id": str(existing.conversation_id),
                        "is_new": False,
                    },
                },
                status=200,
            )

    return Response(
        {
            "status": "success",
            "data": {
                "conversation_id": str(conversation.conversation_id),
                "is_new": True,
            },
        },
        status=201,
    )


def _serialize_attachments(attachments, conversation_id, request):
    """Signed, short-lived URLs for a message's photo attachments."""
    serialized = []
    for attachment in attachments:
        if attachment.moderation_status != MessageAttachment.ModerationStatus.APPROVED:
            continue
        path = reverse(
            "get-attachment",
            kwargs={
                "conversation_id": conversation_id,
                "attachment_id": attachment.attachment_id,
            },
        )
        serialized.append(
            {
                "attachment_id": str(attachment.attachment_id),
                "url": request.build_absolute_uri(path + sign_attachment_url(attachment, conversation_id)),
                "content_type": attachment.content_type,
            }
        )
    return serialized


def _conversation_attachments(conversation, message_ids):
    """Approved attachments for a set of messages, grouped by message id."""
    if not message_ids:
        return {}
    rows = MessageAttachment.objects.filter(
        message_id__in=message_ids,
        moderation_status=MessageAttachment.ModerationStatus.APPROVED,
    ).order_by("created_at")
    grouped = {}
    for row in rows:
        grouped.setdefault(row.message_id, []).append(row)
    return grouped


def _platform_photo_model_error(conversation, content):
    """``None`` when the platform-routed model for this send can view photos.

    Mirrors the model resolution ``process_ai_response`` performs (language →
    Swahili pin → default) so a photo send is refused BEFORE storage, billing
    or a task when the model that will answer cannot see images. BYOK is
    deliberately not checked here: pointing UwenzAi at your own endpoint makes
    image support your responsibility (documented in Settings).
    """
    character = conversation.character
    effective_lang = (
        conversation.preferred_language
        or (character.primary_language if character else None)
        or LanguageDetector.detect(content or "")
    )
    model = getattr(settings, "CORTECS_MODEL", None)
    if effective_lang in ("sw", "sh"):
        model = getattr(settings, "CORTECS_SWAHILI_MODEL", None) or model
    if not supports_vision(model):
        logger.info("Photo refused for conv=%s: model=%s cannot view images", conversation.conversation_id, model)
        return "photo_not_supported_by_model"
    return None


@csrf_protect_for_guests
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ChatMessageThrottle])
def api_send_message(request, conversation_id):
    """
    Accepts a chat message from an authenticated user or a guest.

    Authenticated callers are identified by their JWT; guests are identified
    solely by the guest credential cookie. A ``session_id`` in the body is
    never consulted.

    Body:
      - content : str (optional when an image is attached)
      - image   : file (multipart only; authenticated users only)
    """
    # Validate USER input before language detection, ownership/DB work or queueing.
    from capp.constants import MAX_MESSAGE_CHARACTERS

    if not isinstance(request.data, dict):
        return Response({"status": "fail", "data": "invalid_message_payload"}, status=400)
    content = request.data.get("content", "")
    if not isinstance(content, str):
        return Response({"status": "fail", "data": "message_content_must_be_string"}, status=400)
    if len(content) > MAX_MESSAGE_CHARACTERS:
        return Response(
            {"status": "fail", "data": "message_too_long", "max_characters": MAX_MESSAGE_CHARACTERS}, status=400
        )
    content = content.strip()

    uploaded_image = request.FILES.get("image")
    if uploaded_image is not None:
        # Photo sends are an authenticated-user feature (same boundary as
        # chat image generation): anonymous upload moderation is a materially
        # larger abuse surface, and a paid Vision turn needs an account.
        if not request.user.is_authenticated:
            return Response({"status": "fail", "data": "photo_requires_account"}, status=403)
        if not photo_uploads_enabled():
            return Response({"status": "fail", "data": "photo_uploads_disabled"}, status=400)
        if not content:
            content = ""
    elif not content:
        return Response({"status": "fail", "data": "message_content_required"}, status=400)

    try:
        operation_id, _client_supplied = idempotency.resolve(request)
    except InvalidIdempotencyKey:
        return Response({"status": "fail", "data": InvalidIdempotencyKey.code}, status=400)

    # ---------- Determine user / guest ----------
    if request.user.is_authenticated:
        conversation = get_object_or_404(
            Conversation.objects.filter(user=request.user),
            conversation_id=conversation_id,
        )
        is_guest = False
        guest_session_id = None
        _maybe_refresh_user_timezone(request, request.user)
    else:
        # Guest flow
        identity = GuestIdentityService.resolve(request)
        if identity is None:
            return Response({"status": "fail", "data": "guest_session_required"}, status=401)
        session_id = identity.owner_key

        conversation = get_object_or_404(
            Conversation.objects.filter(guest_session_id=session_id),
            conversation_id=conversation_id,
        )

        # ✅ GUEST QUOTA CHECK
        ip_addr = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:1000]

        logger.info(f"Guest IP: {ip_addr}, UA: {user_agent[:50]}")

        quota = GuestTokenQuota.get_or_create_quota(
            session_id=session_id,
            ip_address=ip_addr,
            user_agent=user_agent,
        )

        # Use configurable limits from settings
        if quota.daily_tokens_used >= settings.GUEST_DAILY_TOKEN_LIMIT:
            return Response(
                {
                    "status": "fail",
                    "data": "guest_daily_limit_reached",
                    "message": "You have reached the daily limit. Create an account to continue chatting.",
                },
                status=429,
            )

        if quota.lifetime_tokens_used >= settings.GUEST_LIFETIME_TOKEN_LIMIT:
            return Response(
                {
                    "status": "fail",
                    "data": "guest_lifetime_limit_reached",
                    "message": "You've used all your free tokens. Sign up for more!",
                },
                status=429,
            )

        is_guest = True
        guest_session_id = session_id

    # User-archived chats remain writable; a closed chat or retired character
    # does not, even if an older closure only updated ACTIVE conversations.
    write_error = chat_write_error(conversation)
    if write_error:
        return Response(
            {
                "status": "fail",
                "data": write_error,
                "message": "This conversation is closed because the character is no longer available. "
                "You can still view and delete your past messages.",
            },
            status=400,
        )

    # ---------- Cross-session safety screen ----------
    # Runs before storage, billing, prompt building and the LLM call, and
    # re-checks the account status per send, so an account locked moments
    # ago is refused even if a WebSocket from before the lock is still open.
    if not is_guest:
        if request.user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED:
            return Response(
                {
                    "status": "fail",
                    "data": SAFETY_LOCKED_CODE,
                    "message": locked_copy_for(request.user.preferred_language),
                },
                status=403,
            )
        safety_outcome = SafetyLedgerService.screen_message(
            user=request.user,
            conversation_id=conversation.conversation_id,
            text=content,
            language=request.user.preferred_language or conversation.preferred_language,
        )
        if safety_outcome is not None and safety_outcome.blocked:
            return Response(
                {
                    "status": "fail",
                    "data": safety_outcome.code or SAFETY_BLOCKED_CODE,
                    "message": safety_outcome.message,
                },
                status=400,
            )
    elif ledger_mode() == "enforce" and TextSafetyPolicy.evaluate_guest(content):
        # Guests have no account to lock, but the deterministic floor still
        # blocks outright violations before they reach the model — only
        # while the ledger enforces. In shadow/off modes guests pass to the
        # model-level backstop, exactly like authenticated users. Care and
        # concern messages pass through in every mode.
        return Response(
            {
                "status": "fail",
                "data": SAFETY_BLOCKED_CODE,
                "message": blocked_copy_for(getattr(conversation, "preferred_language", None)),
            },
            status=400,
        )

    # ---------- Replay of the same send ----------
    # A double click or a retried POST carries the operation ID the browser
    # already used. Returning the first message keeps one send to one reply and
    # to one charge; the task reserves against this same ID.
    existing = Message.objects.filter(conversation=conversation, client_operation_id=operation_id).first()
    if existing is not None:
        replay_attachments = _serialize_attachments(
            _conversation_attachments(conversation, [existing.message_id]).get(existing.message_id, []),
            conversation.conversation_id,
            request,
        )
        return idempotency.echo(
            Response(
                {
                    "status": "success",
                    "data": {
                        "message_id": str(existing.message_id),
                        "content": existing.content,
                        "attachments": replay_attachments,
                    },
                },
                status=200,
            ),
            operation_id,
        )

    # ---------- Photo attachment processing ----------
    # Runs BEFORE any storage, message row, task or billing. Rejected or
    # unscreenable images never touch the bucket; the user gets a precise
    # error code and no sparks are held (the reservation happens in the task).
    prepared_photo = None
    if uploaded_image is not None:
        # BYOK conversations skip the vision-capability gate by design: the
        # user pointed UwenzAi at their own endpoint, so image support is their
        # responsibility (Settings documents this). The platform path must
        # route to a model that can actually see the photo.
        uses_byok = (
            request.user.is_authenticated and UserBYOKConfig.objects.filter(user=request.user, is_active=True).exists()
        )
        if not uses_byok:
            model_error = _platform_photo_model_error(conversation, content)
            if model_error:
                return Response({"status": "fail", "data": model_error}, status=400)

        try:
            prepared_photo = prepare_photo(uploaded_image.read())
        except PhotoModerationBlocked as exc:
            record_screening_failure(
                owner=request.user if request.user.is_authenticated else None,
                conversation=conversation,
                blocked=exc.blocked,
            )
            logger.warning(
                "Photo blocked by moderation for conv=%s user=%s",
                conversation_id,
                getattr(request.user, "user_id", None),
            )
            return Response({"status": "fail", "data": "photo_moderation_blocked"}, status=400)
        except PhotoModerationUnavailable:
            # Fail closed: an unscreenable image is never stored.
            logger.warning("Photo moderation unavailable for conv=%s", conversation_id)
            return Response({"status": "fail", "data": "photo_moderation_unavailable"}, status=503)
        except PhotoValidationError as exc:
            # Size/dimension refusals carry the limits so the client can show
            # exactly what is allowed instead of a generic "too large".
            details = None
            if exc.code in ("photo_too_large", "photo_dimensions", "photo_empty"):
                details = photo_upload_limits()
            body = {"status": "fail", "data": exc.code}
            if details:
                body["details"] = details
            return Response(body, status=400)
        except Exception:
            logger.exception("Photo processing failed for conv=%s", conversation_id)
            return Response({"status": "fail", "data": "photo_processing_failed"}, status=400)

    # ---------- Language detection ----------
    lang = LanguageDetector.detect(content)

    # Language detection and other request work can race erasure. Lock in the
    # same order as erasure and recheck current ownership/lifecycle at the write.
    try:
        with transaction.atomic():
            conversation = lock_writable_chat(
                conversation_id=conversation_id,
                character_id=conversation.character_id,
                user_id=None if is_guest else request.user.pk,
                guest_session_id=guest_session_id,
            )
            existing = Message.objects.filter(conversation=conversation, client_operation_id=operation_id).first()
            if existing is not None:
                replay_attachments = _serialize_attachments(
                    _conversation_attachments(conversation, [existing.message_id]).get(existing.message_id, []),
                    conversation.conversation_id,
                    request,
                )
                return idempotency.echo(
                    Response(
                        {
                            "status": "success",
                            "data": {
                                "message_id": str(existing.pk),
                                "content": existing.content,
                                "attachments": replay_attachments,
                            },
                        },
                        status=200,
                    ),
                    operation_id,
                )
            user_message = Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.USER,
                content=content,
                language_code=lang,
                status=Message.StatusChoices.SENT,
                client_operation_id=operation_id,
                metadata={"type": "user_photo"} if prepared_photo is not None else {},
            )
            if prepared_photo is not None:
                store_encrypted_photo(
                    message=user_message,
                    owner=request.user if request.user.is_authenticated else None,
                    prepared=prepared_photo,
                )
            conversation.last_message_at = user_message.created_at
            conversation.save(update_fields=["last_message_at", "updated_at"])
    except ChatWriteUnavailable as exc:
        error = str(exc)
        status = 404 if error == "conversation_not_found" else (403 if error == "permission_denied" else 400)
        return Response({"status": "fail", "data": error}, status=status)

    # ---------- Fire Celery task ----------
    process_ai_response.delay(
        conversation_id=str(conversation.conversation_id),
        user_message_id=str(user_message.message_id),
        is_guest=is_guest,
        guest_session_id=guest_session_id,
    )

    response_attachments = []
    if prepared_photo is not None:
        response_attachments = _serialize_attachments(
            _conversation_attachments(conversation, [user_message.message_id]).get(user_message.message_id, []),
            conversation.conversation_id,
            request,
        )

    return idempotency.echo(
        Response(
            {
                "status": "success",
                "data": {
                    "message_id": str(user_message.message_id),
                    "content": user_message.content,
                    "attachments": response_attachments,
                },
            },
            status=201,
        ),
        operation_id,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def api_get_attachment(request, conversation_id, attachment_id):
    """
    Serve one photo attachment: decrypt server-side, stream the bytes.

    Access requires EITHER normal conversation ownership (JWT auth / guest
    cookie) OR a valid short-lived HMAC signature on the URL — ``<img>``
    tags cannot send Authorization headers, so history responses hand the
    owner freshly-signed URLs instead. Signatures cover the conversation id,
    attachment id and an expiry, are generated per response and never stored.
    """
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

    is_owner = False
    if request.user.is_authenticated:
        is_owner = conversation.user_id == request.user.pk
    else:
        identity = GuestIdentityService.resolve(request)
        is_owner = identity is not None and conversation.guest_session_id == identity.owner_key

    if not is_owner:
        signature_ok = False
        attachment_probe = MessageAttachment.objects.filter(
            attachment_id=attachment_id, message__conversation=conversation
        ).first()
        if attachment_probe is not None:
            signature_ok = verify_attachment_signature(
                attachment_probe,
                conversation_id,
                request.query_params.get("expires", ""),
                request.query_params.get("signature", ""),
            )
        if not signature_ok:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)

    attachment = (
        MessageAttachment.objects.select_related("message")
        .filter(
            attachment_id=attachment_id,
            message__conversation=conversation,
            moderation_status=MessageAttachment.ModerationStatus.APPROVED,
        )
        .first()
    )
    if attachment is None:
        return Response({"status": "fail", "data": "attachment_not_found"}, status=404)

    try:
        image_bytes = read_attachment_bytes(attachment)
    except Exception:
        logger.exception("Attachment fetch failed (%s).", "attachment=%s" % attachment.attachment_id)
        return Response({"status": "fail", "data": "photo_unavailable"}, status=500)

    response = HttpResponse(image_bytes, content_type=attachment.content_type)
    response["Cache-Control"] = "private, max-age=300"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ChatMessageThrottle])
def api_generate_chat_image(request, conversation_id):
    """
    Generate an AI image of the conversation's character on demand and add it
    to the current chat.

    Only authenticated users may generate chat images: the generated picture is
    stored as a PRIVATE gallery image owned by the requester, so it is visible
    only to them (in chat and in that character's gallery). Charging matches the
    normal picture-generation flow (image_generation) and happens asynchronously
    inside the Celery task.

    Body: { prompt }
    """
    prompt = request.data.get("prompt", "").strip()
    if not prompt:
        return Response({"status": "fail", "data": "image_prompt_required"}, status=400)

    try:
        operation_id, _client_supplied = idempotency.resolve(request)
    except InvalidIdempotencyKey:
        return Response({"status": "fail", "data": InvalidIdempotencyKey.code}, status=400)

    if len(prompt) > 600:
        return Response({"status": "fail", "data": "image_prompt_too_long"}, status=400)

    conversation = get_object_or_404(
        Conversation.objects.filter(user=request.user),
        conversation_id=conversation_id,
    )

    if conversation.status == Conversation.StatusChoices.CLOSED:
        return Response({"status": "fail", "data": "conversation_closed"}, status=400)

    # ---------- Cross-session safety screen ----------
    # Same boundary as api_send_message: a safety-locked account cannot
    # generate images, and the prompt text runs through the same cross-session
    # safety ledger as chat messages so a violation cannot dodge the ledger by
    # switching surfaces.
    if request.user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED:
        return Response(
            {
                "status": "fail",
                "data": SAFETY_LOCKED_CODE,
                "message": locked_copy_for(request.user.preferred_language),
            },
            status=403,
        )
    safety_outcome = SafetyLedgerService.screen_message(
        user=request.user,
        conversation_id=conversation.conversation_id,
        text=prompt,
        language=request.user.preferred_language or conversation.preferred_language,
    )
    if safety_outcome is not None and safety_outcome.blocked:
        return Response(
            {
                "status": "fail",
                "data": safety_outcome.code or SAFETY_BLOCKED_CODE,
                "message": safety_outcome.message,
            },
            status=400,
        )

    # Prohibited-subject policy. Adult explicit prompts are allowed; images
    # involving children, animals or deceased people are not. Screened here —
    # before any message is stored, any task is dispatched, or any spark is
    # spent — across the user's prompt AND the character metadata that also
    # reaches the image provider. The service and the provider helpers re-assert
    # the same policy so a direct API or Celery call cannot bypass it.
    violation = ImagePromptPolicy.find_violation(
        prompt,
        *CharacterService.image_policy_texts(conversation.character),
    )
    if violation:
        logger.warning(
            "chat image prompt blocked by image policy conversation=%s user=%s category=%s",
            conversation_id,
            getattr(request.user, "user_id", None),
            violation,
        )
        return Response({"status": "fail", "data": ImagePromptPolicy.ERROR_CODE}, status=400)

    # Replay of the same request: return the message the first one created
    # rather than queueing a second generation against the same hold.
    existing = Message.objects.filter(conversation=conversation, client_operation_id=operation_id).first()
    if existing is not None:
        return idempotency.echo(
            Response(
                {
                    "status": "success",
                    "data": {
                        "message_id": str(existing.message_id),
                        "content": existing.content,
                    },
                },
                status=200,
            ),
            operation_id,
        )

    # Hold the Sparks here rather than in the worker, so a user who cannot
    # afford the picture is told so in the response instead of by a system
    # message minutes later. The task presents the same operation ID and gets
    # this reservation back; it settles or refunds it.
    try:
        reservation = _reserve_chat_image(
            feature=BillingReservation.Feature.IMAGE_GENERATION,
            operation_id=operation_id,
            user=request.user,
            operation_reference=f"chat_image:{conversation.conversation_id}:{operation_id}",
        )
    except InsufficientBalance:
        return Response({"status": "fail", "data": "insufficient_sparks"}, status=400)
    except (ReservationError, OperationalError):
        logger.exception("Could not reserve sparks for chat image")
        return Response({"status": "fail", "data": "billing_error"}, status=400)

    if reservation.status != BillingReservation.Status.RESERVED:
        return Response({"status": "fail", "data": "previous_attempt_failed"}, status=400)

    try:
        # Record the request as a user message so it appears in the chat transcript.
        user_message = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.USER,
            content=prompt,
            language_code=LanguageDetector.detect(prompt),
            status=Message.StatusChoices.SENT,
            client_operation_id=operation_id,
            metadata={"type": "image_request"},
        )

        conversation.last_message_at = user_message.created_at
        conversation.save(update_fields=["last_message_at", "updated_at"])
    except Exception:
        logger.exception("Failed to record chat image request")
        _refund_chat_image(reservation, "request_persist_failed")
        raise

    try:
        generate_chat_image.delay(
            conversation_id=str(conversation.conversation_id),
            user_message_id=str(user_message.message_id),
            prompt=prompt,
            operation_id=operation_id,
        )
    except Exception:
        logger.exception("Failed to enqueue chat image")
        _refund_chat_image(reservation, "image_task_dispatch_failed")
        return Response({"status": "fail", "data": "service_unavailable"}, status=503)

    return idempotency.echo(
        Response(
            {
                "status": "success",
                "data": {
                    "message_id": str(user_message.message_id),
                    "content": user_message.content,
                },
            },
            status=202,
        ),
        operation_id,
    )


@csrf_protect_for_guests
@api_view(["GET", "PATCH"])
@permission_classes([AllowAny])
def api_conversation_settings(request, conversation_id):
    """
    Get or update the per-chat toggles: web search and reasoning effort
    (Fast/Deep). Both are changeable mid-conversation at any time and take
    effect starting with the next message (process_ai_response reads them
    fresh from the Conversation row on every call).

    GET  -> {"status": "success", "data": {"web_search_enabled": bool, "reasoning_effort": "fast"|"deep"}}
    PATCH body: any subset of the same two keys.
    """
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"status": "fail", "data": "conversation_not_found"}, status=404)

    # Permission check (same pattern as api_reset_conversation / api_delete_message)
    if request.user.is_authenticated:
        if conversation.user != request.user:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)
    else:
        identity = GuestIdentityService.resolve(request)
        if identity is None or conversation.guest_session_id != identity.owner_key:
            return Response({"status": "fail", "data": "permission_denied"}, status=403)

    if request.method == "GET":
        # Guest conversations may predate this restriction or have been
        # modified outside this endpoint.  Do not expose a stale enabled state:
        # guests can read their settings but their effective configuration is
        # always search off and fast reasoning.
        is_guest = not request.user.is_authenticated
        return Response(
            {
                "status": "success",
                "data": {
                    "web_search_enabled": False if is_guest else conversation.web_search_enabled,
                    "reasoning_effort": (
                        Conversation.ReasoningEffort.FAST if is_guest else conversation.reasoning_effort
                    ),
                },
            }
        )

    # PATCH
    is_guest = not request.user.is_authenticated
    if is_guest:
        if request.data.get("web_search_enabled") is True:
            return Response({"status": "fail", "data": "web_search_requires_login"}, status=403)
        if request.data.get("reasoning_effort") == Conversation.ReasoningEffort.DEEP:
            return Response({"status": "fail", "data": "deep_reasoning_requires_login"}, status=403)

    update_fields = []

    if "web_search_enabled" in request.data:
        value = request.data["web_search_enabled"]
        if not isinstance(value, bool):
            return Response({"status": "fail", "data": "web_search_enabled must be a boolean"}, status=400)
        conversation.web_search_enabled = value
        update_fields.append("web_search_enabled")

    if "reasoning_effort" in request.data:
        value = request.data["reasoning_effort"]
        valid_values = {choice.value for choice in Conversation.ReasoningEffort}
        if value not in valid_values:
            return Response(
                {"status": "fail", "data": f"reasoning_effort must be one of {sorted(valid_values)}"},
                status=400,
            )
        conversation.reasoning_effort = value
        update_fields.append("reasoning_effort")

    if not update_fields:
        return Response({"status": "fail", "data": "no_valid_fields_provided"}, status=400)

    conversation.save(update_fields=update_fields + ["updated_at"])

    return Response(
        {
            "status": "success",
            "data": {
                "web_search_enabled": False if is_guest else conversation.web_search_enabled,
                "reasoning_effort": (Conversation.ReasoningEffort.FAST if is_guest else conversation.reasoning_effort),
            },
        }
    )


# ----------------------------------------------------------------------
# Character-initiated photo offers
# ----------------------------------------------------------------------
#
# When a reply carries the [PHOTO_OFFER:...] marker (see tasks.py), the worker
# stores a separate AI message with metadata.type="photo_offer", the model's
# generation-ready description, and its own ``offer_operation_id`` key. The
# client renders that message as an accept/decline card. Accepting reserves the
# discounted CHARACTER_PHOTO_OFFER_COST against the offer's key and queues the
# normal generate_chat_image task, so charging, policy gates, storage and
# replay behaviour are identical to a user-requested picture — only the price
# and who "asked" differ.


def _get_owned_photo_offer(request, conversation_id, message_id):
    """Fetch the conversation + offer message for the authenticated owner."""
    conversation = get_object_or_404(
        Conversation.objects.filter(user=request.user),
        conversation_id=conversation_id,
    )
    offer_message = get_object_or_404(
        Message.objects.filter(conversation=conversation, metadata__type="photo_offer"),
        message_id=message_id,
    )
    return conversation, offer_message


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ChatMessageThrottle])
def api_accept_photo_offer(request, conversation_id, message_id):
    """
    Accept a photo the character offered to send.

    Reserves the discounted CHARACTER_PHOTO_OFFER_COST (cheaper than the
    regular image_generation price, because the character initiated it) and
    queues generate_chat_image with the description the character wrote. The
    offer's own operation key is the idempotency anchor: a retried accept
    replays the same reservation and can never bill twice for one offer.
    """
    conversation, offer_message = _get_owned_photo_offer(request, conversation_id, message_id)

    if conversation.status == Conversation.StatusChoices.CLOSED:
        return Response({"status": "fail", "data": "conversation_closed"}, status=400)

    # Cross-session safety lock: a safety-locked account must not spend
    # Sparks accepting photo offers either (audit F2, 2026-10-04).
    if request.user.account_status == CustomUser.AccountStatus.SAFETY_LOCKED:
        return Response(
            {
                "status": "fail",
                "data": SAFETY_LOCKED_CODE,
                "message": locked_copy_for(request.user.preferred_language),
            },
            status=403,
        )

    meta = dict(offer_message.metadata or {})
    status_ = meta.get("offer_status") or "pending"
    if status_ != "pending":
        # Already resolved (accepted or declined): replay the outcome rather
        # than reserving again.
        return Response(
            {"status": "fail", "data": f"photo_offer_already_{status_}"},
            status=400,
        )

    prompt = (meta.get("prompt") or "").strip()
    operation_id = meta.get("offer_operation_id") or f"offer:{offer_message.message_id}"
    if not prompt:
        return Response({"status": "fail", "data": "photo_offer_invalid"}, status=400)

    # Prohibited-subject policy, re-asserted before any spark moves: the marker
    # description was model-written, so it is screened here exactly like a
    # user-typed prompt (and the task gates it once more before the provider).
    violation = ImagePromptPolicy.find_violation(
        prompt,
        *CharacterService.image_policy_texts(conversation.character),
    )
    if violation:
        logger.warning(
            "photo offer accept blocked by image policy conversation=%s user=%s category=%s",
            conversation_id,
            getattr(request.user, "user_id", None),
            violation,
        )
        return Response({"status": "fail", "data": ImagePromptPolicy.ERROR_CODE}, status=400)

    try:
        reservation = _reserve_chat_image(
            feature=BillingReservation.Feature.IMAGE_GENERATION,
            operation_id=operation_id,
            user=request.user,
            operation_reference=f"chat_photo_offer:{conversation.conversation_id}:{operation_id}",
            cost_override=CHARACTER_PHOTO_OFFER_COST,
        )
    except InsufficientBalance:
        return Response({"status": "fail", "data": "insufficient_sparks"}, status=400)
    except (ReservationError, OperationalError):
        logger.exception("Could not reserve sparks for photo offer accept")
        return Response({"status": "fail", "data": "billing_error"}, status=400)

    if reservation.status != BillingReservation.Status.RESERVED:
        return Response({"status": "fail", "data": "previous_attempt_failed"}, status=400)

    # In-progress now; the image task flips it to "accepted" when the picture
    # is delivered. If the task later refunds, the hold — not this status — is
    # the source of truth, and the user can see the failure system message.
    meta["offer_status"] = "accepted"
    offer_message.metadata = meta
    offer_message.save(update_fields=["metadata", "updated_at"])

    try:
        generate_chat_image.delay(
            conversation_id=str(conversation.conversation_id),
            user_message_id=str(offer_message.message_id),
            prompt=prompt,
            operation_id=operation_id,
            offer_message_id=str(offer_message.message_id),
        )
    except Exception:
        logger.exception("Failed to enqueue accepted photo offer")
        _refund_chat_image(reservation, "image_task_dispatch_failed")
        meta["offer_status"] = "pending"
        offer_message.metadata = meta
        offer_message.save(update_fields=["metadata", "updated_at"])
        return Response({"status": "fail", "data": "service_unavailable"}, status=503)

    return Response(
        {
            "status": "success",
            "data": {
                "message_id": str(offer_message.message_id),
                "offer_status": "accepted",
            },
        },
        status=202,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ChatMessageThrottle])
def api_decline_photo_offer(request, conversation_id, message_id):
    """
    Decline a photo the character offered to send. Costs nothing; the card
    stays in the transcript marked as declined.
    """
    conversation, offer_message = _get_owned_photo_offer(request, conversation_id, message_id)

    meta = dict(offer_message.metadata or {})
    if (meta.get("offer_status") or "pending") == "pending":
        meta["offer_status"] = "declined"
        offer_message.metadata = meta
        offer_message.save(update_fields=["metadata", "updated_at"])

    return Response(
        {
            "status": "success",
            "data": {
                "message_id": str(offer_message.message_id),
                "offer_status": meta.get("offer_status"),
            },
        },
        status=200,
    )
