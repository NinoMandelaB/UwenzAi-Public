# capp/tasks.py

import logging
import re
import time
import uuid
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, Retry, SoftTimeLimitExceeded
from django.db import OperationalError, connection, transaction
from django.utils import timezone
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from capp.classes.chat import unread_service
from capp.classes.chat.proactive_chat_service import ProactiveChatService
from capp.classes.push.push_service import PushService

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
from capp.constants import BYOK_MESSAGE_PAIR_COST, CHARACTER_PHOTO_OFFER_COST
from capp.classes.search.base import get_search_provider
from capp.classes.search.tool_loop import (
    run_search_tool_calls,
    run_open_url_tool_calls,
    SEARCH_TOOL_SPEC,
    OPEN_URL_TOOL_SPEC,
)

from capp.classes.memory.memory_service import MemoryService
from capp.classes.relationship.relationship_service import RelationshipService
from capp.classes.summary.summary_service import ConversationSummaryService
from capp.classes.billing.balances import debit_locked
from capp.classes.billing.charge_service import _push_spark_pool_balance
from capp.classes.billing.idempotency import result_key
from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature, ReservationError
from capp.classes.billing.reservation_service import BillingReservationService
from capp.classes.moderation.image_prompt_policy import ImagePromptPolicy

logger = logging.getLogger(__name__)


def _run_offered_tool_calls(tool_calls, conversation_id):
    """
    Execute whichever of the offered tools (web_search, open_url) the model
    actually called, each independently capped at its own
    settings.SEARCH_MAX_CALLS_PER_MESSAGE / OPEN_URL_MAX_CALLS_PER_MESSAGE.

    Used by both the Cortecs and BYOK branches of process_ai_response so the
    two paths behave identically once a tool-calling model is involved --
    search execution (and open_url fetching) is provider-independent.

    Returns (condensed_block, search_calls_used, open_url_calls_used).
    """
    search_condensed, search_calls_used = run_search_tool_calls(tool_calls)
    open_url_condensed, open_url_calls_used = run_open_url_tool_calls(tool_calls)

    if search_calls_used or open_url_calls_used:
        logger.info(
            "Tool calls used for conv=%s: web_search=%d open_url=%d; continuing generation",
            conversation_id,
            search_calls_used,
            open_url_calls_used,
        )

    blocks = [b for b in (search_condensed, open_url_condensed) if b]
    return ("\n\n".join(blocks), search_calls_used, open_url_calls_used)


# The character may offer to send a photo of itself by ending its reply with a
# marker carrying a generation-ready description, e.g.
#   [PHOTO_OFFER:standing in a sunny market, smiling at the camera]
# The marker never reaches the user: process_ai_response splits it off, stores
# the visible text as the reply and a separate `photo_offer` message the client
# renders as an accept/decline card (accepting runs generate_chat_image at the
# discounted CHARACTER_PHOTO_OFFER_COST). Willingness — and the trust threshold
# for intimate photos — is the model's call, driven by the system prompt.
PHOTO_OFFER_MARKER_RE = re.compile(r"\[PHOTO_OFFER:(.*?)\]", re.IGNORECASE | re.DOTALL)


def _extract_photo_offer(text: str):
    """Split a trailing [PHOTO_OFFER:...] marker off an AI reply.

    Returns (clean_text, offer_prompt_or_None). Only replies that are safe to
    persist are touched here; billing and policy are unchanged because the
    marker path never calls a provider — generation only happens if the user
    later accepts the offer, through the normal image task.
    """
    match = PHOTO_OFFER_MARKER_RE.search(text or "")
    if not match:
        return text, None
    prompt = match.group(1).strip()
    clean = (text[: match.start()] + text[match.end() :]).strip()  # noqa: E203
    if not prompt or len(prompt) > 600:
        return clean or text.replace(match.group(0), "").strip(), None
    return clean, prompt


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


# Hard ceilings on one reply. Without them a single slow upstream (a provider
# that never closes the connection, or a slow-drip page fetched by open_url)
# occupies a worker slot forever; enough of those and the whole chat queue
# stalls with no error surfaced to anyone. `soft_time_limit` raises
# SoftTimeLimitExceeded inside the task so the handler below can refund the
# Spark reservation and mark the message failed; `time_limit` is the
# unconditional backstop if the soft signal is somehow swallowed.
# AI_TASK_SOFT_TIME_LIMIT must exceed OPEN_URL_TOTAL_TIMEOUT_SECONDS so a slow
# fetch aborts itself first and the user still gets a reply.
AI_TASK_SOFT_TIME_LIMIT = int(getattr(settings, "AI_TASK_SOFT_TIME_LIMIT", 180))
AI_TASK_TIME_LIMIT = int(getattr(settings, "AI_TASK_TIME_LIMIT", AI_TASK_SOFT_TIME_LIMIT + 20))


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    soft_time_limit=AI_TASK_SOFT_TIME_LIMIT,
    time_limit=AI_TASK_TIME_LIMIT,
)
def process_ai_response(self, conversation_id, user_message_id, is_guest=False, guest_session_id=None):
    logger.info(
        "TASK START: conv=%s msg=%s guest=%s session=%s",
        conversation_id,
        user_message_id,
        is_guest,
        guest_session_id,
    )

    reservation = None
    reply_committed = False
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
            unread_service.broadcast_unread(conversation)
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

        # -- Web search + open_url tools (per-chat toggle) --------------------------
        # Search execution is provider-independent, so both the Cortecs and
        # BYOK paths get the same tools whenever the toggle is on and a
        # search provider is configured. Gracefully degrades (no tool
        # offered, no outbound call) when the toggle is off or when the
        # selected provider's API key is unset.
        # open_url is gated behind the exact same condition as web_search --
        # it is never offered on its own -- and capped at
        # settings.OPEN_URL_MAX_CALLS_PER_MESSAGE (separately from
        # SEARCH_MAX_CALLS_PER_MESSAGE) once the model actually calls it.
        search_tools = None
        search_calls_used = 0
        open_url_calls_used = 0
        # The queued task receives the authoritative guest marker from the
        # send endpoint.  A user-less conversation is also necessarily a guest
        # conversation, which keeps this fail-closed if a malformed task were
        # ever enqueued without that marker.
        is_guest_conversation = is_guest or conversation.user_id is None
        if not is_guest_conversation and conversation.web_search_enabled and get_search_provider() is not None:
            search_tools = [SEARCH_TOOL_SPEC, OPEN_URL_TOOL_SPEC]
            logger.info(
                "Web search + open_url tools offered for conv=%s (byok=%s)",
                conversation_id,
                bool(byok),
            )

        # Guests always use the default fast behavior even if a stale or
        # manually altered conversation row says otherwise.
        reasoning_effort = (
            Conversation.ReasoningEffort.FAST
            if is_guest_conversation
            else conversation.reasoning_effort or Conversation.ReasoningEffort.FAST
        )

        # -- AI call ---------------------------------------------------------------
        logger.info(
            "Calling AI via %s model=%s max_tokens=%d reasoning_effort=%s search=%s",
            "BYOK" if byok else "Cortecs",
            byok.model_name if byok else (model or "default"),
            api_max_tokens,
            reasoning_effort,
            bool(search_tools),
        )
        try:
            if byok:
                # BYOK: reasoning_effort is a BEST-EFFORT OpenAI-compatible
                # mapping (see capp/classes/ai/reasoning.py) -- the endpoint
                # is arbitrary and third-party, so this is not verified
                # against specific provider docs the way Cortecs/Mistral are.
                # BYOKService.chat_completion() retries once without the
                # reasoning param if the endpoint rejects it, so this can
                # never break the chat. Search/open_url tools are offered
                # the same way as the Cortecs path since search execution is
                # provider-independent.
                byok_call_kwargs = {}
                if search_tools:
                    byok_call_kwargs["tools"] = search_tools
                response = BYOKService.chat_completion(
                    byok_config=byok,
                    messages=messages,
                    max_tokens=api_max_tokens,
                    temperature=0.7,
                    reasoning_effort=reasoning_effort,
                    **byok_call_kwargs,
                )

                tool_calls = response.get("choices", [{}])[0].get("message", {}).get("tool_calls")
                if tool_calls and search_tools:
                    condensed, search_calls_used, open_url_calls_used = _run_offered_tool_calls(
                        tool_calls, conversation_id
                    )
                    if condensed:
                        follow_up_messages = PromptBuilder.inject_untrusted_web_results(messages, condensed)
                        response = BYOKService.chat_completion(
                            byok_config=byok,
                            messages=follow_up_messages,
                            max_tokens=api_max_tokens,
                            temperature=0.7,
                            reasoning_effort=reasoning_effort,
                        )
            else:
                call_kwargs = {}
                if search_tools:
                    call_kwargs["tools"] = search_tools
                response = CortecsClient.chat_completion(
                    messages=messages,
                    model=model,
                    max_tokens=api_max_tokens,
                    temperature=0.7,
                    reasoning_effort=reasoning_effort,
                    **call_kwargs,
                )

                # Tool-calling loop: if the model asked to search and/or
                # open a URL, execute server-side (capped at
                # settings.SEARCH_MAX_CALLS_PER_MESSAGE /
                # settings.OPEN_URL_MAX_CALLS_PER_MESSAGE respectively),
                # inject the explicitly untrusted results immediately before the
                # safety sandwich (never after the safety/photo tail), and
                # continue generation once. We do not loop
                # further even if the model asks again, to keep worst-case
                # latency bounded to a single extra round trip.
                tool_calls = response.get("choices", [{}])[0].get("message", {}).get("tool_calls")
                if tool_calls and search_tools:
                    condensed, search_calls_used, open_url_calls_used = _run_offered_tool_calls(
                        tool_calls, conversation_id
                    )
                    if condensed:
                        follow_up_messages = PromptBuilder.inject_untrusted_web_results(messages, condensed)
                        response = CortecsClient.chat_completion(
                            messages=follow_up_messages,
                            model=model,
                            max_tokens=api_max_tokens,
                            temperature=0.7,
                            reasoning_effort=reasoning_effort,
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

        # ── Character-initiated photo offer ──────────────────────────────
        # The reply may end with [PHOTO_OFFER:description] when the character,
        # based on personality and relationship, is willing to send a photo.
        # Guests never get offers: accepting one is a paid, authenticated-only
        # feature, so the marker is simply stripped for them.
        offer_prompt = None
        if not is_guest:
            from capp.classes.characters.character_service import CharacterService

            ai_content, offer_prompt = _extract_photo_offer(ai_content)
            if offer_prompt is not None:
                # Re-assert the prohibited-subject policy on the model-written
                # description before it is stored: the same gate every other
                # image prompt passes through.
                violation = ImagePromptPolicy.find_violation(
                    offer_prompt,
                    *CharacterService.image_policy_texts(character),
                )
                if violation:
                    logger.warning(
                        "photo offer suppressed by image policy conversation=%s category=%s",
                        conversation_id,
                        violation,
                    )
                    offer_prompt = None
                elif not ai_content:
                    # A marker-only reply would leave an empty bubble; keep a
                    # minimal line so the transcript still reads naturally.
                    ai_content = "..."

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
                    "reasoning_effort": reasoning_effort,
                    "search_calls_used": search_calls_used,
                    "open_url_calls_used": open_url_calls_used,
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
        reply_committed = True

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

        # ── Persist and push the photo offer, if the reply carried one ──
        # The offer message owns its own operation key (offer:<uuid>), which is
        # what the accept view presents to generate_chat_image later: accepting
        # reserves the discounted CHARACTER_PHOTO_OFFER_COST against that key
        # and the image task links the delivered picture back to this offer.
        if offer_prompt is not None:
            try:
                offer_operation_id = f"offer:{uuid.uuid4()}"
                offer_message = Message.objects.create(
                    conversation=conversation,
                    sender_type=Message.SenderType.AI,
                    content="",
                    status=Message.StatusChoices.SENT,
                    metadata={
                        "type": "photo_offer",
                        "prompt": offer_prompt,
                        "offer_cost": CHARACTER_PHOTO_OFFER_COST,
                        "offer_status": "pending",
                        "offer_operation_id": offer_operation_id,
                    },
                )
                conversation.last_message_at = offer_message.created_at
                conversation.save(update_fields=["last_message_at", "updated_at"])
                _send_photo_offer_message(conversation.conversation_id, offer_message)
            except Exception:
                # The reply itself is delivered; a lost offer costs nothing and
                # the user can always ask for a picture again.
                logger.exception("Failed to persist/broadcast photo offer for conv=%s", conversation_id)

        unread_service.broadcast_unread(conversation)

        # Push only after every optional response message is durable, so the
        # closed-app badge carries the same final unread count as the socket.
        if not is_guest and user is not None:
            try:
                PushService.send_new_message_push(user, character, conversation)
            except SoftTimeLimitExceeded:
                raise
            except Exception:
                logger.exception("Push failed for message %s", ai_message.message_id)

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
    except SoftTimeLimitExceeded:
        if reply_committed:
            logger.warning(
                "process_ai_response hit its soft limit after reply commit; "
                "leaving settled reply and user-message status intact: conv=%s msg=%s",
                conversation_id,
                user_message_id,
            )
            return
        # The task blew its wall-clock budget. Handled explicitly (before the
        # generic handler) so the user is NOT charged for a reply they never
        # received, and is not retried: a retry would just burn the same
        # budget again against the same stuck upstream.
        logger.error(
            "process_ai_response exceeded soft time limit (%ss): conv=%s msg=%s",
            AI_TASK_SOFT_TIME_LIMIT,
            conversation_id,
            user_message_id,
        )
        if reservation is not None:
            PaidFeature.refund(reservation, reason="task_soft_time_limit")
        _mark_message_failed_by_id(user_message_id, "The reply took too long and was cancelled. Please try again.")
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

IMAGE_TASK_SOFT_TIME_LIMIT = int(getattr(settings, "IMAGE_TASK_SOFT_TIME_LIMIT", 210))
IMAGE_TASK_TIME_LIMIT = int(getattr(settings, "IMAGE_TASK_TIME_LIMIT", IMAGE_TASK_SOFT_TIME_LIMIT + 20))


def _set_image_billing_db_timeout(deadline):
    if connection.vendor != "postgresql":
        return
    remaining = max(0.001, deadline - time.monotonic())
    max_wait = float(getattr(settings, "IMAGE_BILLING_DB_MAX_WAIT_SECONDS", 2))
    timeout_ms = max(1, int(min(remaining, max_wait) * 1000))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true), " "set_config('statement_timeout', %s, true)",
            [f"{timeout_ms}ms", f"{timeout_ms}ms"],
        )


@shared_task(bind=True, max_retries=8, default_retry_delay=15)
def refund_billing_reservation(self, reservation_id, reason):
    """Idempotently retry a refund that could not acquire its billing rows."""
    deadline = time.monotonic() + float(getattr(settings, "IMAGE_BILLING_DB_MAX_WAIT_SECONDS", 2))
    try:
        with transaction.atomic():
            _set_image_billing_db_timeout(deadline)
            return str(BillingReservationService.refund(reservation_id, reason=reason).reservation_id)
    except OperationalError as exc:
        raise self.retry(exc=exc)


def _image_reservation_has_durable_artifact(reservation):
    """Return whether this reservation's specific paid result was delivered."""
    reference = reservation.operation_reference or ""
    try:
        operation_type, target_id, reference_key = reference.split(":", 2)
    except ValueError:
        return False

    key = reservation.idempotency_key
    if not target_id or reference_key != key:
        return False

    if operation_type in {"chat_image", "chat_photo_offer"}:
        return (
            Message.objects.filter(
                conversation_id=target_id,
                conversation__user_id=reservation.user_id,
                client_operation_id=result_key(key),
                metadata__type="generated_image",
                metadata__reservation_id=str(reservation.reservation_id),
            )
            .exclude(metadata__image_name=None)
            .exclude(metadata__image_name="")
            .exists()
        )

    if operation_type == "gallery_image":
        return (
            CharacterGalleryImage.objects.filter(
                character_id=target_id,
                client_operation_id=key,
                created_by_id=reservation.user_id,
                image__isnull=False,
            )
            .exclude(image="")
            .exists()
        )

    # Avatar writes and their settlement are committed in one transaction, so
    # a genuinely stale avatar hold has no independently attributable success
    # marker. Unknown or malformed operation types also fail closed to refund.
    return False


@shared_task
def reconcile_stale_image_reservations():
    """Resolve image holds left by a hard-killed web or Celery process."""
    cutoff = timezone.now() - timedelta(
        seconds=int(getattr(settings, "IMAGE_TASK_TIME_LIMIT", IMAGE_TASK_TIME_LIMIT))
        + int(getattr(settings, "IMAGE_BILLING_RECOVERY_GRACE_SECONDS", 60))
    )
    limit = int(getattr(settings, "IMAGE_BILLING_RECOVERY_BATCH_SIZE", 100))
    candidate_ids = list(
        BillingReservation.objects.filter(
            feature=BillingReservation.Feature.IMAGE_GENERATION,
            status=BillingReservation.Status.RESERVED,
            reserved_at__lt=cutoff,
            updated_at__lt=cutoff,
        )
        .order_by("reserved_at")
        .values_list("reservation_id", flat=True)[:limit]
    )
    reconciled = 0
    for reservation_id in candidate_ids:
        deadline = time.monotonic() + float(getattr(settings, "IMAGE_BILLING_DB_MAX_WAIT_SECONDS", 2))
        try:
            with transaction.atomic():
                _set_image_billing_db_timeout(deadline)
                reservation = (
                    BillingReservation.objects.select_for_update(skip_locked=True)
                    .filter(
                        reservation_id=reservation_id,
                        status=BillingReservation.Status.RESERVED,
                        updated_at__lt=cutoff,
                    )
                    .first()
                )
                if reservation is None:
                    continue

                has_durable_artifact = _image_reservation_has_durable_artifact(reservation)
                _set_image_billing_db_timeout(deadline)
                if has_durable_artifact:
                    BillingReservationService.settle(
                        reservation,
                        reason="stale_image_artifact_reconciled",
                    )
                else:
                    BillingReservationService.refund(
                        reservation,
                        reason="stale_image_operation_reconciled",
                    )
                reconciled += 1
        except OperationalError:
            logger.warning("Stale image reservation %s remained locked; retrying next sweep", reservation_id)
        except Exception:
            logger.exception("Could not reconcile stale image reservation %s", reservation_id)
    return reconciled


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=5,
    soft_time_limit=IMAGE_TASK_SOFT_TIME_LIMIT,
    time_limit=IMAGE_TASK_TIME_LIMIT,
)
def generate_chat_image(self, conversation_id, user_message_id, prompt, operation_id=None, offer_message_id=None):
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
    deadline = time.monotonic() + float(getattr(settings, "IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS", 180))

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
        unread_service.broadcast_unread(conversation)
        return

    reservation = BillingReservation.objects.filter(idempotency_key=operation_id, user=user).first()
    if reservation is not None:
        try:
            with transaction.atomic():
                _set_image_billing_db_timeout(deadline)
                touched = (
                    BillingReservation.objects.select_for_update()
                    .filter(
                        reservation_id=reservation.reservation_id,
                        status=BillingReservation.Status.RESERVED,
                    )
                    .first()
                )
                if touched is None:
                    return
                touched.save(update_fields=["updated_at"])
                reservation = touched
        except OperationalError:
            logger.warning("generate_chat_image: billing lease timed out")
            return

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
            CharacterService._refund_image_reservation(
                reservation,
                reason="prohibited_subject",
                deadline=deadline,
            )
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
            deadline=deadline,
        )
    except Retry:
        raise
    except SoftTimeLimitExceeded:
        if reservation is not None:
            CharacterService._refund_image_reservation(
                reservation,
                reason="image_task_soft_time_limit",
                deadline=deadline,
            )
        _send_system_message(
            conversation.conversation_id,
            "Sorry, the image took too long and was cancelled. No sparks were charged.",
        )
        raise
    except Exception as e:
        logger.exception("generate_chat_image: unexpected error")
        try:
            # The hold survives the retry: the next attempt presents the same
            # operation ID and picks the reservation up where this one left it.
            self.retry(exc=e)
        except MaxRetriesExceededError:
            if reservation is not None:
                CharacterService._refund_image_reservation(
                    reservation,
                    reason="image_failed_after_retries",
                    deadline=deadline,
                )
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
            CharacterService._refund_image_reservation(
                reservation,
                reason=str(reason)[:255],
                deadline=deadline,
            )
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
                "offer_message_id": (str(offer_message_id) if offer_message_id else None),
            },
        )

        conversation.last_message_at = ai_message.created_at
        conversation.save(update_fields=["last_message_at", "updated_at"])

        # An accepted offer is fulfilled: the card in the transcript switches
        # from its accept/decline state to the delivered state.
        if offer_message_id:
            try:
                offer_message = Message.objects.get(
                    message_id=offer_message_id,
                    conversation=conversation,
                    metadata__type="photo_offer",
                )
                offer_meta = dict(offer_message.metadata or {})
                offer_meta["offer_status"] = "accepted"
                offer_message.metadata = offer_meta
                offer_message.save(update_fields=["metadata", "updated_at"])
            except Message.DoesNotExist:
                logger.warning("generate_chat_image: offer message %s not found", offer_message_id)

        # The picture is stored and linked into the chat: the operation the
        # user paid for is complete.
        if reservation is not None:
            CharacterService._settle_image_reservation(
                reservation,
                reason="chat_image_delivered",
                deadline=deadline,
            )

    try:
        _send_chat_image_message(conversation.conversation_id, ai_message, image_url)
    except Exception:
        logger.exception(
            "Broadcast failed for chat image %s; message is persisted and settled",
            ai_message.message_id,
        )

    unread_service.broadcast_unread(conversation)

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


def _send_photo_offer_message(conversation_id, offer_message: Message):
    """Push a photo-offer card to the conversation's WebSocket group.

    Carries everything the client needs to render the accept/decline popup
    without a history reload: the character's description of the photo and the
    discounted price. Accepting POSTs the message id back to the offer-accept
    view, which bills and queues generate_chat_image.
    """
    try:
        meta = offer_message.metadata if isinstance(offer_message.metadata, dict) else {}
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"conversation_{conversation_id}",
            {
                "type": "chat.message",
                "message": {
                    "message_id": str(offer_message.message_id),
                    "sender_type": "ai",
                    "content": "",
                    "image_url": None,
                    "created_at": offer_message.created_at.isoformat(),
                    "reply_to": None,
                    "type": "photo_offer",
                    "offer": {
                        "prompt": meta.get("prompt") or "",
                        "cost": meta.get("offer_cost"),
                        "status": meta.get("offer_status") or "pending",
                    },
                },
            },
        )
        logger.info(
            "Sent photo offer message %s to WebSocket group conversation_%s",
            offer_message.message_id,
            conversation_id,
        )
    except Exception:
        logger.exception("Failed to send photo offer message over WebSocket")


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
