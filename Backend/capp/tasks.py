# capp/tasks.py

import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, Retry
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from capp.classes.chat.proactive_chat_service import ProactiveChatService

from capp.models import (
    BillingReservation,
    Conversation,
    GuestIdentity,
    Message,
    TokenUsageLog,
    GuestTokenQuota,
    UserSubscription,
    SparkPool,
    SparkTransaction,
    UserBYOKConfig,
    CharacterGalleryImage,
)
from capp.classes.ai.cortecs_client import CortecsClient
from capp.classes.ai.prompt_builder import PromptBuilder
from capp.classes.byok.byok_service import BYOKService
from capp.constants import BYOK_MESSAGE_PAIR_COST

from capp.classes.memory.memory_service import MemoryService
from capp.classes.relationship.relationship_service import RelationshipService
from capp.classes.summary.summary_service import ConversationSummaryService
from capp.classes.billing.balances import debit_locked
from capp.classes.billing.charge_service import _push_spark_pool_balance
from capp.classes.billing.idempotency import result_key
from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature, ReservationError
from capp.classes.moderation.image_prompt_policy import ImagePromptPolicy

logger = logging.getLogger(__name__)

# Shown in chat when an image request involves a prohibited subject. Adult
# explicit imagery is allowed; children, animals and deceased people are not.
PROHIBITED_SUBJECT_SYSTEM_MESSAGE = (
    "That image request cannot be generated. Images involving children, "
    "animals, or deceased people are not allowed. No sparks were charged."
)

# Guest chat blockers. Only the true out-of-sparks case offers the spark
# purchase link: the daily and lifetime caps are token quotas that buying
# sparks does not lift, so pointing guests at checkout there would mislead.
GUEST_INSUFFICIENT_SPARKS_MESSAGE = (
    "You don't have enough sparks. [Sign up](/register) to get extra sparks and keep chatting, "
    "or [buy sparks to continue chatting](/spark_pool).\n"
    "\n"
    "Are you in psychological distress? Get help here: https://findahelpline.com"
)

GUEST_DAILY_LIMIT_MESSAGE = "You've reached your daily free limit. [Sign up](/register) to continue chatting."

GUEST_LIFETIME_LIMIT_MESSAGE = (
    "You've used up your free guest allowance. [Sign up](/register) to keep chatting.\n"
    "\n"
    "Are you in psychological distress? Get help here: https://findahelpline.com"
)


@shared_task(name="expire_subscriptions")
def expire_subscriptions():
    """
    Find all active subscriptions that have ended,
    remove unused subscription sparks (up to sparks_remaining),
    and mark them EXPIRED.
    """
    now = timezone.now()
    expired_subs = UserSubscription.objects.filter(
        status=UserSubscription.StatusChoices.ACTIVE,
        ends_at__lt=now,
    ).select_related("user")

    for sub in expired_subs:
        try:
            with transaction.atomic():
                spark_pool = SparkPool.objects.select_for_update().get(user=sub.user)
                sparks_to_remove = min(sub.sparks_remaining, spark_pool.sparks)

                if sparks_to_remove > 0:
                    balance_before = int(spark_pool.sparks)
                    debit_locked(spark_pool, sparks_to_remove)

                    SparkTransaction.objects.create(
                        spark_pool=spark_pool,
                        user=sub.user,
                        entry_type=SparkTransaction.EntryType.DEBIT,
                        source=SparkTransaction.SourceChoices.ADJUSTMENT,
                        spark_amount=-sparks_to_remove,
                        sparks_before=balance_before,
                        sparks_after=spark_pool.sparks,
                        description=f"Subscription expired – {sparks_to_remove} unused subscription sparks removed",
                    )

                sub.sparks_remaining = 0
                sub.status = UserSubscription.StatusChoices.EXPIRED
                sub.save(update_fields=["sparks_remaining", "status", "updated_at"])

                _push_spark_pool_balance(sub.user)

                logger.info(
                    "Expired subscription %s for user %s, removed %d sparks",
                    sub.user_subscription_id,
                    sub.user.user_id,
                    sparks_to_remove,
                )
        except SparkPool.DoesNotExist:
            sub.status = UserSubscription.StatusChoices.EXPIRED
            sub.sparks_remaining = 0
            sub.save(update_fields=["sparks_remaining", "status", "updated_at"])
        except Exception:
            logger.exception("Failed to expire subscription %s", sub.user_subscription_id)


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def process_ai_response(self, conversation_id, user_message_id, is_guest=False, guest_session_id=None):
    logger.info(
        "TASK START: conv=%s msg=%s guest=%s session=%s",
        conversation_id,
        user_message_id,
        is_guest,
        guest_session_id,
    )

    reservation = None
    try:
        conversation = Conversation.objects.select_related("user", "character").get(conversation_id=conversation_id)
        user_message = Message.objects.select_related("conversation").get(message_id=user_message_id)
        user = conversation.user
        character = conversation.character

        logger.info(
            "Fetched user=%s char=%s",
            user.username if user else "None",
            character.name if character else "None",
        )

        # The operation ID the browser minted for this send. It is the
        # reservation's idempotency key and the tag on both message rows, so a
        # redelivered task finds the reply the first attempt already wrote.
        # Pre-idempotency rows fall back to the message ID, which is equally
        # stable across retries of this same task.
        operation_id = user_message.client_operation_id or str(user_message.message_id)
        reply_key = result_key(operation_id)

        existing_reply = Message.objects.filter(conversation=conversation, client_operation_id=reply_key).first()
        if existing_reply is not None:
            logger.info("Replaying stored reply for conv=%s operation=%s", conversation_id, operation_id)
            _send_ai_message(conversation.conversation_id, existing_reply)
            return

        if is_guest and guest_session_id:
            quota = GuestTokenQuota.get_or_create_quota(guest_session_id)
            logger.info(
                "Guest quota: daily_used=%d lifetime_used=%d",
                quota.daily_tokens_used,
                quota.lifetime_tokens_used,
            )

            if quota.daily_tokens_used >= settings.GUEST_DAILY_TOKEN_LIMIT:
                logger.warning("Guest daily limit reached: %s", guest_session_id)
                _mark_message_blocked(user_message, "Guest daily limit reached")
                _send_system_message(
                    conversation.conversation_id,
                    GUEST_DAILY_LIMIT_MESSAGE,
                    code="guest_daily_limit",
                )
                return

            if quota.lifetime_tokens_used >= settings.GUEST_LIFETIME_TOKEN_LIMIT:
                logger.warning("Guest lifetime limit reached: %s", guest_session_id)
                _mark_message_blocked(user_message, "Guest lifetime limit reached")
                _send_system_message(
                    conversation.conversation_id,
                    GUEST_LIFETIME_LIMIT_MESSAGE,
                    code="guest_lifetime_limit",
                )
                return

            byok = None
            byok_cost = None
            effective_max = 5000
            api_max_tokens = 600

        else:
            if not user:
                logger.error("No user associated with conversation (non‑guest)")
                _mark_message_failed(user_message, "No user associated with conversation")
                return

            if not user.is_active or user.is_deleted:
                logger.warning("User inactive or deleted: %s", user.username)
                _mark_message_failed(user_message, "User account is inactive or deleted.")
                return

            # BYOK is resolved before reserving because it changes the price.
            byok = None
            byok_cost = None
            try:
                byok = UserBYOKConfig.objects.get(user=user, is_active=True)
                byok_cost = BYOK_MESSAGE_PAIR_COST
                logger.info("BYOK active for user=%s model=%s", user.user_id, byok.model_name)
            except UserBYOKConfig.DoesNotExist:
                pass

            # ── Reset proactive counter ──
            if user and character:
                ProactiveChatService.reset_counter(user, character)

            effective_max = 10000
            api_max_tokens = 1000

        # ── Hold the Sparks before any provider work ──────────────────────
        # Nothing below this point may call a provider or persist a reply until
        # the hold exists, and every path out of the provider call either
        # settles or refunds it.
        try:
            reservation = _reserve_message_pair(
                conversation=conversation,
                operation_id=operation_id,
                user=None if is_guest else user,
                guest_session_id=guest_session_id if is_guest else None,
                cost_override=byok_cost,
            )
        except InsufficientBalance:
            logger.warning("Message pair blocked for %s: insufficient sparks", conversation_id)
            _mark_message_blocked(user_message, "Insufficient sparks")
            if is_guest:
                _send_system_message(
                    conversation.conversation_id,
                    GUEST_INSUFFICIENT_SPARKS_MESSAGE,
                    code="guest_insufficient_sparks",
                )
            else:
                _send_system_message(
                    conversation.conversation_id,
                    (
                        "You don't have enough sparks or you've reached your usage limit. "
                        "[Go to spark_pool](/spark_pool) to continue.\n\n"
                        "Are you in psychological distress? Get help here: https://findahelpline.com"
                    ),
                )
            return
        except ReservationError as e:
            logger.exception("Could not reserve sparks for conversation %s", conversation_id)
            _mark_message_failed(user_message, f"Billing error: {e}")
            return

        if reservation.status == BillingReservation.Status.REFUNDED:
            # A previous attempt at this operation failed terminally and gave
            # the Sparks back. Redoing the work now would be free, so the user
            # has to send again — which mints a new operation ID.
            logger.warning("Operation %s already refunded; not re-running", operation_id)
            _mark_message_failed(user_message, "This message already failed. Please send it again.")
            return
        if reservation.status == BillingReservation.Status.SETTLED:
            # Settled but no reply row matched the key above: the reply was
            # written before this task learned about operation IDs, or was
            # deleted. Either way the user already paid, so do not charge again.
            logger.warning("Operation %s already settled but no reply found", operation_id)
            return

        model = getattr(settings, "CORTECS_MODEL", None)

        effective_lang = (
            conversation.preferred_language
            or (character.primary_language if character else None)
            or user_message.language_code
        )
        logger.info(
            "LANG CHECK: conv_lang=%s  char_lang=%s  msg_lang=%s  effective_lang=%s",
            conversation.preferred_language,
            character.primary_language if character else None,
            user_message.language_code,
            effective_lang,
        )

        # Only apply Swahili model switching when NOT using BYOK
        if not byok and effective_lang in ("sw", "sh"):
            swahili_model = getattr(settings, "CORTECS_SWAHILI_MODEL", None)
            if swahili_model:
                model = swahili_model
                logger.info("Swahili/Sheng conversation - switching model to %s", model)

        logger.info(
            "Building prompt with max_context=%d, model=%s",
            effective_max,
            model or "default",
        )
        try:
            messages = PromptBuilder.build_messages(
                conversation=conversation,
                user_message=user_message,
                max_context_tokens=effective_max,
                model=byok.model_name if byok else model,
            )
            logger.info("Prompt built - system + %d history messages", len(messages) - 1)
        except Exception as e:
            logger.exception("Prompt construction failed")
            PaidFeature.refund(reservation, reason="prompt_build_failed")
            _mark_message_failed(user_message, f"Prompt error: {str(e)}")
            return

        # -- BYOK system-prompt override -------------------------------------------
        if byok and byok.system_prompt:
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = byok.system_prompt
                logger.info("BYOK system prompt applied (%d chars)", len(byok.system_prompt))

        # -- AI call ---------------------------------------------------------------
        logger.info(
            "Calling AI via %s model=%s max_tokens=%d",
            "BYOK" if byok else "Cortecs",
            byok.model_name if byok else (model or "default"),
            api_max_tokens,
        )
        try:
            if byok:
                response = BYOKService.chat_completion(
                    byok_config=byok,
                    messages=messages,
                    max_tokens=api_max_tokens,
                    temperature=0.7,
                )
            else:
                response = CortecsClient.chat_completion(
                    messages=messages,
                    model=model,
                    max_tokens=api_max_tokens,
                    temperature=0.7,
                )
            logger.info("AI response received: id=%s", response.get("id"))
        except Exception as e:
            if byok:
                logger.warning("BYOK AI call failed for user=%s: %s", user.user_id, e)
                PaidFeature.refund(reservation, reason="byok_provider_failed")
                _mark_message_failed(user_message, f"BYOK API error: {str(e)}")
                _send_system_message(
                    conversation.conversation_id,
                    "Your API returned an error. Please check your BYOK settings "
                    "and try again. No sparks were charged.",
                )
                return
            logger.exception("AI call failed")
            try:
                # No refund here: the retry re-presents the same operation ID,
                # gets this very reservation back and reuses the held Sparks.
                self.retry(exc=e)
            except MaxRetriesExceededError:
                logger.error("Max retries exceeded for AI call")
                PaidFeature.refund(reservation, reason="provider_failed_after_retries")
                _mark_message_failed(user_message, f"AI error after retries: {str(e)}")
                _send_system_message(
                    conversation.conversation_id,
                    "Sorry, the AI encountered an error. Please try again later.",
                )
            return

        # Nothing here streams: chat_completion returns the whole reply or
        # nothing. A response with no choices, or with empty content, produced
        # no durable answer and is therefore a terminal failure, not a partial
        # success the user should pay for.
        choices = response.get("choices", [])
        if not choices:
            logger.error("AI response contained no choices")
            PaidFeature.refund(reservation, reason="provider_returned_no_choices")
            _mark_message_failed(user_message, "AI returned no choices.")
            return

        ai_content = choices[0].get("message", {}).get("content", "").strip()
        if not ai_content:
            logger.error("AI returned empty content")
            PaidFeature.refund(reservation, reason="provider_returned_empty")
            _mark_message_failed(user_message, "AI generated empty response.")
            return

        logger.info("AI response length: %d chars", len(ai_content))

        usage = response.get("usage", {})
        input_tokens_used = usage.get("prompt_tokens", 0)
        output_tokens_used = usage.get("completion_tokens", 0)
        total_tokens_used = usage.get("total_tokens", input_tokens_used + output_tokens_used)
        provider_message_id = response.get("id")

        logger.info(
            "Tokens: input=%d output=%d total=%d",
            input_tokens_used,
            output_tokens_used,
            total_tokens_used,
        )

        with transaction.atomic():
            ai_message = Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.AI,
                content=ai_content,
                status=Message.StatusChoices.SENT,
                input_tokens=input_tokens_used,
                output_tokens=output_tokens_used,
                provider_message_id=provider_message_id,
                client_operation_id=reply_key,
                metadata={
                    "model": (byok.model_name if byok else (model or settings.CORTECS_MODEL or "mistral")),
                    "byok": bool(byok),
                    "reservation_id": str(reservation.reservation_id),
                },
            )

            conversation.last_message_at = ai_message.created_at
            conversation.save(update_fields=["last_message_at", "updated_at"])

            if not is_guest:
                TokenUsageLog.objects.create(
                    user=user,
                    conversation=conversation,
                    message=ai_message,
                    character=character,
                    usage_type=TokenUsageLog.UsageType.CHAT,
                    provider="byok" if byok else "cortecs",
                    model_name=(byok.model_name if byok else (model or settings.CORTECS_MODEL or "mistral")),
                    input_tokens=input_tokens_used,
                    output_tokens=output_tokens_used,
                    total_tokens=total_tokens_used,
                    estimated_cost=_calculate_cost(input_tokens_used, output_tokens_used),
                    metadata={"response_id": provider_message_id},
                )

            # Settled in the same transaction as the reply, so the two cannot
            # disagree: either the user has an answer and has paid for it, or
            # neither happened and the hold is still refundable.
            PaidFeature.settle(reservation, reason="ai_reply_persisted")

        # Delivery is best effort from here. The reply is durable and paid for,
        # so a failed WebSocket push is not a reason to refund or regenerate —
        # the client recovers it by reloading history.
        try:
            _send_ai_message(conversation.conversation_id, ai_message)
        except Exception:
            logger.exception(
                "Broadcast failed for message %s; reply is persisted and settled",
                ai_message.message_id,
            )

        if not is_guest:
            transaction.on_commit(
                lambda: chain_sub_tasks(
                    str(conversation.conversation_id),
                    str(user_message.message_id),
                    str(ai_message.message_id),
                )
            )

        logger.info(
            "TASK SUCCESS: conv=%s tokens=%d guest=%s",
            conversation_id,
            total_tokens_used,
            is_guest,
        )

    except Retry:
        # Celery's own control flow. Re-raising keeps the hold in place for the
        # next attempt, which presents the same operation ID.
        raise
    except Conversation.DoesNotExist:
        logger.error("Conversation not found: %s", conversation_id)
    except Message.DoesNotExist:
        logger.error("User message not found: %s", user_message_id)
    except Exception as e:
        logger.exception("Unhandled error in process_ai_response")
        if reservation is not None:
            PaidFeature.refund(reservation, reason="unhandled_error")
        _mark_message_failed_by_id(user_message_id, f"Unexpected error: {str(e)}")


def _reserve_message_pair(*, conversation, operation_id, user, guest_session_id, cost_override):
    """Hold one message pair's Sparks for whichever principal owns the chat."""
    guest_identity = None
    if user is None:
        guest_identity = GuestIdentity.objects.filter(guest_id=guest_session_id).first()
        if guest_identity is None:
            raise ReservationError(f"No guest identity for session {guest_session_id}")

    return PaidFeature.reserve(
        feature=BillingReservation.Feature.MESSAGE_PAIR,
        operation_id=operation_id,
        user=user,
        guest_identity=guest_identity,
        operation_reference=f"message_pair:{conversation.conversation_id}:{operation_id}",
        cost_override=cost_override,
    )


def chain_sub_tasks(conv_id, user_msg_id, ai_msg_id):
    from celery import chain

    chain(
        extract_memory_facts.si(conv_id, user_msg_id, ai_msg_id),
        update_relationship.si(conv_id, user_msg_id, ai_msg_id),
        maybe_summarize_conversation.si(conv_id),
    ).apply_async()


@shared_task(bind=True, max_retries=1)
def extract_memory_facts(self, conversation_id, user_message_id, ai_message_id):
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
        user_message = Message.objects.get(message_id=user_message_id)
        ai_message = Message.objects.get(message_id=ai_message_id)
        MemoryService.extract_and_save_facts(conversation, user_message, ai_message)
    except Exception as e:
        logger.warning("Memory extraction task failed: %s", str(e))


@shared_task(bind=True, max_retries=1)
def update_relationship(self, conversation_id, user_message_id, ai_message_id):
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
        user_message = Message.objects.get(message_id=user_message_id)
        ai_message = Message.objects.get(message_id=ai_message_id)
        RelationshipService.update_relationship_state(conversation, user_message, ai_message)
    except Exception as e:
        logger.warning("Relationship update task failed: %s", str(e))


@shared_task(bind=True, max_retries=1)
def maybe_summarize_conversation(self, conversation_id):
    try:
        conversation = Conversation.objects.get(conversation_id=conversation_id)
        ConversationSummaryService.maybe_generate_summary(conversation)
    except Exception as e:
        logger.warning("Summarization task failed: %s", str(e))


@shared_task(bind=True, max_retries=0)
def retrospective_memory_for_new_user(self, user_id: str):
    """
    Triggered after a guest's data is claimed by a registered account.
    Runs memory extraction and summary generation on all conversations that
    were just transferred from the guest session to the account.

    Queued by GuestClaimService once the claim transaction has committed, so
    the conversations have already been re-assigned (guest_session_id cleared,
    user set).

    This task is best-effort — failures are logged but do not affect
    the user's account. Memory will build up naturally from future
    conversations if this fails.
    """
    from capp.models import CustomUser
    from capp.classes.summary.summary_service import ConversationSummaryService

    try:
        user = CustomUser.objects.get(user_id=user_id)
    except CustomUser.DoesNotExist:
        logger.warning("retrospective_memory_for_new_user: user_id=%s not found", user_id)
        return

    logger.info("Starting retrospective memory generation for new user_id=%s", user_id)
    try:
        ConversationSummaryService.generate_summaries_for_claimed_conversations(user)
        logger.info("Retrospective memory generation complete for user_id=%s", user_id)
    except Exception:
        logger.exception("Retrospective memory generation failed for user_id=%s", user_id)


# -------------------------------------------------------------
# In-chat Image Generation Task
# -------------------------------------------------------------


@shared_task(bind=True, max_retries=1, default_retry_delay=5)
def generate_chat_image(self, conversation_id, user_message_id, prompt, operation_id=None):
    """
    Generate an AI image for the conversation's character on demand.

    Reuses CharacterService.generate_gallery_image so the image:
      - preserves the character (fal.ai Seedream v5 Lite edit + avatar reference),
      - is charged exactly like a normal gallery picture (image_generation),
      - is stored as a PRIVATE CharacterGalleryImage owned by the requesting
        user (visible only to them in that character's gallery).

    The resulting image is linked to the chat by creating an AI Message whose
    metadata carries the gallery image id + URL, then pushed over the chat
    WebSocket so it shows up live in the current conversation.

    ``operation_id`` is the hold the view already placed. Everything below is
    keyed on it, so a redelivered or retried task reuses the same reservation,
    finds any picture the previous attempt stored instead of generating a
    second one, and cannot produce two chat messages for one payment. The
    reservation stays held until that message exists, because a picture nobody
    can see in the chat is not what the user paid for.
    """
    from capp.classes.characters.character_service import CharacterService

    logger.info("IMAGE TASK START: conv=%s msg=%s", conversation_id, user_message_id)

    try:
        conversation = Conversation.objects.select_related("user", "character").get(conversation_id=conversation_id)
    except Conversation.DoesNotExist:
        logger.error("generate_chat_image: conversation %s not found", conversation_id)
        return

    user = conversation.user
    character = conversation.character

    if not user:
        logger.error("generate_chat_image: no user on conversation %s", conversation_id)
        return

    if not operation_id:
        operation_id = str(user_message_id)
    reply_key = result_key(operation_id)

    existing_reply = Message.objects.filter(conversation=conversation, client_operation_id=reply_key).first()
    if existing_reply is not None:
        logger.info("generate_chat_image replay: resending message %s", existing_reply.message_id)
        _send_chat_image_message(
            conversation.conversation_id,
            existing_reply,
            _gallery_image_url(existing_reply),
        )
        return

    reservation = BillingReservation.objects.filter(idempotency_key=operation_id, user=user).first()

    # Prohibited-subject policy, re-asserted inside the task so a direct
    # ``generate_chat_image.delay(...)`` (bypassing the view) still cannot reach
    # a provider or charge sparks. The service and provider helpers gate again.
    violation = ImagePromptPolicy.find_violation(prompt, *CharacterService.image_policy_texts(character))
    if violation:
        logger.warning(
            "generate_chat_image blocked by image policy conversation=%s category=%s",
            conversation_id,
            violation,
        )
        if reservation is not None:
            PaidFeature.refund(reservation, reason="prohibited_subject")
        _send_system_message(
            conversation.conversation_id,
            PROHIBITED_SUBJECT_SYSTEM_MESSAGE,
            code=ImagePromptPolicy.ERROR_CODE,
        )
        return

    try:
        result = CharacterService.generate_gallery_image(
            character=character,
            prompt=prompt,
            extra_prompt=prompt,
            actor=user,
            is_public=False,
            operation_id=operation_id,
            defer_settlement=True,
        )
    except Retry:
        raise
    except Exception as e:
        logger.exception("generate_chat_image: unexpected error")
        try:
            # The hold survives the retry: the next attempt presents the same
            # operation ID and picks the reservation up where this one left it.
            self.retry(exc=e)
        except MaxRetriesExceededError:
            if reservation is not None:
                PaidFeature.refund(reservation, reason="image_failed_after_retries")
            _send_system_message(
                conversation.conversation_id,
                "Sorry, the image could not be generated. No sparks were charged.",
            )
        return

    if result.get("status") != "success":
        reason = result.get("data", "ai_generation_failed")
        logger.warning("generate_chat_image failed: %s", reason)
        # generate_gallery_image already refunded its own reservation, which is
        # this one; refund is idempotent, so this closes the case where the
        # failure happened before it took ownership.
        if reservation is not None:
            PaidFeature.refund(reservation, reason=str(reason)[:255])
        if reason == ImagePromptPolicy.ERROR_CODE:
            _send_system_message(
                conversation.conversation_id,
                PROHIBITED_SUBJECT_SYSTEM_MESSAGE,
                code=ImagePromptPolicy.ERROR_CODE,
            )
        elif reason in ("Insufficient sparks or usage cap reached",) or "spark" in str(reason).lower():
            _send_system_message(
                conversation.conversation_id,
                "You don't have enough sparks to generate an image. " "[Go to spark_pool](/spark_pool) to top up.",
            )
        else:
            _send_system_message(
                conversation.conversation_id,
                "Sorry, the image could not be generated. No sparks were charged.",
            )
        return

    image_data = result["data"]["image"]
    image_url = image_data.get("image_url")
    gallery_image_id = image_data.get("id")

    # Persist the DURABLE storage key (image.name), never the signed URL: with a
    # private S3 bucket, ``.url`` is a short-lived presigned URL that would 403
    # once it expires. The chat read path resolves a fresh presigned URL from
    # this key on demand (see conversation_views._build_image_url).
    image_name = ""
    if gallery_image_id:
        try:
            gallery_image = CharacterGalleryImage.objects.get(id=gallery_image_id)
            if gallery_image.image:
                image_name = gallery_image.image.name or ""
        except CharacterGalleryImage.DoesNotExist:
            logger.warning("generate_chat_image: gallery image %s missing after create", gallery_image_id)

    with transaction.atomic():
        ai_message = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.AI,
            content="",
            status=Message.StatusChoices.SENT,
            client_operation_id=reply_key,
            metadata={
                "type": "generated_image",
                "image_name": image_name,
                "gallery_image_id": gallery_image_id,
                "prompt": prompt,
                "reservation_id": (str(reservation.reservation_id) if reservation is not None else None),
            },
        )

        conversation.last_message_at = ai_message.created_at
        conversation.save(update_fields=["last_message_at", "updated_at"])

        # The picture is stored and linked into the chat: the operation the
        # user paid for is complete.
        if reservation is not None:
            PaidFeature.settle(reservation, reason="chat_image_delivered")

    try:
        _send_chat_image_message(conversation.conversation_id, ai_message, image_url)
    except Exception:
        logger.exception(
            "Broadcast failed for chat image %s; message is persisted and settled",
            ai_message.message_id,
        )

    logger.info(
        "IMAGE TASK SUCCESS: conv=%s gallery_image=%s",
        conversation_id,
        gallery_image_id,
    )


# -------------------------------------------------------------
# Proactive Chat Task
# -------------------------------------------------------------


@shared_task
def check_and_send_proactive_messages():
    """
    Celery task that checks all enabled proactive chat preferences
    and sends proactive messages where conditions are met.
    Runs every 30 minutes via Celery Beat.
    """
    logger.info("Starting proactive chat check...")
    try:
        sent_count = ProactiveChatService.check_and_send_proactive_messages()
        logger.info("Proactive chat check completed. Sent %d messages.", sent_count)
        return {"sent_count": sent_count}
    except Exception as e:
        logger.exception("Error in proactive chat task: %s", e)
        raise


# --------------------- WebSocket Helpers ---------------------
def _send_ai_message(conversation_id, ai_message: Message):
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"conversation_{conversation_id}",
            {
                "type": "chat.message",
                "message": {
                    "message_id": str(ai_message.message_id),
                    "sender_type": "ai",
                    "content": ai_message.content,
                    "created_at": ai_message.created_at.isoformat(),
                    "reply_to": (str(ai_message.reply_to_id) if ai_message.reply_to else None),
                },
            },
        )
        logger.info(
            "Sent AI message %s to WebSocket group conversation_%s",
            ai_message.message_id,
            conversation_id,
        )
    except Exception:
        logger.exception("Failed to send AI message over WebSocket")


def _gallery_image_url(ai_message: Message):
    """Resolve a fresh URL from the durable storage key on an image message."""
    from django.core.files.storage import default_storage

    meta = ai_message.metadata if isinstance(ai_message.metadata, dict) else {}
    name = meta.get("image_name")
    if not name:
        return meta.get("image_url")
    try:
        return default_storage.url(name)
    except Exception:
        logger.warning("could not resolve image url for stored key %s", name)
        return None


def _send_chat_image_message(conversation_id, ai_message: Message, image_url: str):
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"conversation_{conversation_id}",
            {
                "type": "chat.message",
                "message": {
                    "message_id": str(ai_message.message_id),
                    "sender_type": "ai",
                    "content": ai_message.content,
                    "image_url": image_url,
                    "created_at": ai_message.created_at.isoformat(),
                    "reply_to": None,
                },
            },
        )
        logger.info(
            "Sent chat image message %s to WebSocket group conversation_%s",
            ai_message.message_id,
            conversation_id,
        )
    except Exception:
        logger.exception("Failed to send chat image message over WebSocket")


def _send_system_message(conversation_id, text: str, code: str = ""):
    """Push an ephemeral system message to the conversation's WebSocket group.

    ``code`` is a stable identifier the clients localise against; ``text`` stays
    on the wire as the fallback for any client that does not know the code.
    """
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"conversation_{conversation_id}",
            {
                "type": "chat.message",
                "message": {
                    "sender_type": "system",
                    "content": text,
                    "code": code,
                    "created_at": timezone.now().isoformat(),
                },
            },
        )
    except Exception:
        logger.exception("Failed to send system message over WebSocket")


def _calculate_cost(input_tokens: int, output_tokens: int) -> float:
    INPUT_PRICE = 0.133 / 1_000_000
    OUTPUT_PRICE = 0.266 / 1_000_000
    cost = (input_tokens * INPUT_PRICE) + (output_tokens * OUTPUT_PRICE)
    return round(cost, 8)


def _mark_message_failed(message: Message, reason: str):
    message.status = Message.StatusChoices.FAILED
    message.error_message = reason
    message.save(update_fields=["status", "error_message", "updated_at"])


def _mark_message_blocked(message: Message, reason: str):
    message.status = Message.StatusChoices.BLOCKED
    message.error_message = reason
    message.save(update_fields=["status", "error_message", "updated_at"])


def _mark_message_failed_by_id(message_id: str, reason: str):
    try:
        msg = Message.objects.get(message_id=message_id)
        _mark_message_failed(msg, reason)
    except Message.DoesNotExist:
        pass
