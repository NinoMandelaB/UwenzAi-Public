# capp/views/conversation_views.py

import logging
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.db.models import F, OuterRef, Subquery
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.csrf import csrf_protect_for_guests
from capp.models import BillingReservation, Character, Conversation, Message, GuestTokenQuota
from capp.classes.ai.language_detector import LanguageDetector
from capp.classes.billing import idempotency
from capp.classes.billing.idempotency import InvalidIdempotencyKey
from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature, ReservationError
from capp.classes.characters.character_service import CharacterService
from capp.classes.chat import message_history, unread_service
from capp.classes.moderation.image_prompt_policy import ImagePromptPolicy
from capp.tasks import process_ai_response, generate_chat_image
from capp.throttles import ChatMessageThrottle

logger = logging.getLogger(__name__)


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
        if msg_type in ("generated_image", "image", "image_request"):
            return "\U0001f4f7 Photo"
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

    def _serialize(msg):
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        return {
            "message_id": str(msg.message_id),
            "sender_type": msg.sender_type,
            "content": msg.content,
            "image_url": _build_image_url(msg),
            "created_at": msg.created_at.isoformat(),
            # ``reply_to_id`` rather than ``reply_to``: touching the relation
            # would fetch the parent row once per message.
            "reply_to": str(msg.reply_to_id) if msg.reply_to_id else None,
            "type": meta.get("type") or "",
            # Persisted system messages carry the same localisation code the
            # WebSocket sends, so history renders in the user's language too.
            "code": meta.get("code") or "",
        }

    messages_list = [_serialize(msg) for msg in page]

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
      - content : str
    """
    content = request.data.get("content", "").strip()
    if not content:
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

    # ---------- Block messages to closed conversations ----------
    if conversation.status == Conversation.StatusChoices.CLOSED:
        return Response(
            {
                "status": "fail",
                "data": "conversation_closed",
                "message": "This conversation is closed because the character is no longer available. "
                "You can still view and delete your past messages.",
            },
            status=400,
        )

    # ---------- Replay of the same send ----------
    # A double click or a retried POST carries the operation ID the browser
    # already used. Returning the first message keeps one send to one reply and
    # to one charge; the task reserves against this same ID.
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

    # ---------- Language detection ----------
    lang = LanguageDetector.detect(content)

    # ---------- Create user message ----------
    user_message = Message.objects.create(
        conversation=conversation,
        sender_type=Message.SenderType.USER,
        content=content,
        language_code=lang,
        status=Message.StatusChoices.SENT,
        client_operation_id=operation_id,
    )

    # Update conversation timestamp
    conversation.last_message_at = user_message.created_at
    conversation.save(update_fields=["last_message_at", "updated_at"])

    # ---------- Fire Celery task ----------
    process_ai_response.delay(
        conversation_id=str(conversation.conversation_id),
        user_message_id=str(user_message.message_id),
        is_guest=is_guest,
        guest_session_id=guest_session_id,
    )

    return idempotency.echo(
        Response(
            {
                "status": "success",
                "data": {
                    "message_id": str(user_message.message_id),
                    "content": user_message.content,
                },
            },
            status=201,
        ),
        operation_id,
    )


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
        reservation = PaidFeature.reserve(
            feature=BillingReservation.Feature.IMAGE_GENERATION,
            operation_id=operation_id,
            user=request.user,
            operation_reference=f"gallery_image:{conversation.character.character_id}:{operation_id}",
        )
    except InsufficientBalance:
        return Response({"status": "fail", "data": "insufficient_sparks"}, status=400)
    except ReservationError:
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
        PaidFeature.refund(reservation, reason="request_persist_failed")
        raise

    generate_chat_image.delay(
        conversation_id=str(conversation.conversation_id),
        user_message_id=str(user_message.message_id),
        prompt=prompt,
        operation_id=operation_id,
    )

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
