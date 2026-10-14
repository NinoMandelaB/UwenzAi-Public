# capp/tasks.py

import logging
import re
import time
import uuid
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError, Retry, SoftTimeLimitExceeded
from django.db import OperationalError, close_old_connections, connection, transaction
from django.utils import timezone
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from capp.classes.chat import unread_service
from capp.classes.chat.proactive_chat_service import ProactiveChatService
from capp.classes.push.push_service import PushService

from capp.models import (
    BillingReservation,
    Character,
    Conversation,
    CustomUser,
    GuestIdentity,
    Message,
    MessageAttachment,
    TokenUsageLog,
    GuestTokenQuota,
    UserSubscription,
    SparkPool,
    SparkTransaction,
    UserBYOKConfig,
    CharacterGalleryImage,
)
from capp.classes.ai.cortecs_client import CortecsClient
from capp.classes.ai.model_vision import supports_vision
from capp.classes.ai.prompt_builder import PromptBuilder
from capp.classes.ai.token_utils import PromptBudgetExceeded
from capp.classes.byok.byok_service import BYOKService
from capp.classes.byok.transport import is_uwenzai_gateway_url
from capp.constants import BYOK_MESSAGE_PAIR_COST, CHARACTER_PHOTO_OFFER_COST
from capp.classes.search.base import get_search_provider
from capp.classes.search.tool_loop import (
    run_search_tool_calls,
    run_open_url_tool_calls,
    SEARCH_TOOL_SPEC,
    OPEN_URL_TOOL_SPEC,
    SEARCH_GROUNDING_NOTICE,
    TOOL_COMPLETION_NOTICE,
    SEARCH_INCOMPLETE_REPLY,
    is_lookup_handoff,
)
from capp.classes.reminders.tool_loop import (
    run_reminder_tool_calls,
    build_reminder_tool_specs,
    REMINDER_TOOL_NAMES,
    CREATE_REMINDER_TOOL_NAME,
    CANCEL_REMINDER_TOOL_NAME,
    _get_function,
    _get_function_name,
)
from capp.classes.profile.tool_loop import (
    run_profile_tool_calls,
    build_profile_tool_specs,
    PROFILE_TOOL_NAMES,
)

from capp.classes.memory.memory_service import MemoryService
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.relationship.relationship_service import RelationshipService
from capp.classes.summary.summary_service import ConversationSummaryService
from capp.classes.billing.balances import debit_locked
from capp.classes.billing.charge_service import _push_spark_pool_balance
from capp.classes.billing.idempotency import result_key
from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature, ReservationError
from capp.classes.billing.reservation_service import BillingReservationService
from capp.classes.moderation.model_refusal import extract_refusal
from capp.classes.moderation.safety_ledger import SafetyLedgerService
from capp.classes.moderation.image_prompt_policy import ImagePromptPolicy
from capp.classes.moderation.photo_moderation import (
    GENERATED_IMAGE_BLOCKED_REASON,
    GENERATED_IMAGE_UNAVAILABLE_REASON,
)

logger = logging.getLogger(__name__)


def _completed_reply_content(response):
    """Return (content, failure_reason) for a final, non-tool response.

    Apply this to every candidate accepted for persistence, including a
    reminder-repair response. Tools-disabled providers are not trusted to
    return a completed answer merely because a previous round was valid.
    """
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", "provider_returned_no_choices"
    choice = choices[0]
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        return "", "provider_returned_empty"
    if (
        message.get("tool_calls")
        or message.get("function_call")
        or choice.get("finish_reason")
        in (
            "tool_calls",
            "function_call",
        )
    ):
        return "", "provider_returned_unfinished_tool_response"
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return "", "provider_returned_empty"
    return content.strip(), None


def _run_offered_tool_calls(
    tool_calls,
    conversation_id,
    *,
    offered_tool_names,
    conversation=None,
    user=None,
    character=None,
    source_message=None,
    mutation_receipts=None,
):
    """
    Execute whichever of the offered tools (web_search, open_url,
    create_reminder, update_timezone) the model actually called, each
    independently capped at its own limit (settings.SEARCH_MAX_CALLS_PER_MESSAGE /
    OPEN_URL_MAX_CALLS_PER_MESSAGE / one reminder-tool and one profile-tool
    call per message).

    Used by both the Cortecs and BYOK branches of process_ai_response so the
    two paths behave identically once a tool-calling model is involved --
    search execution (and open_url fetching) is provider-independent.

    ``offered_tool_names`` is the exact immutable per-request allowlist,
    not the account's broader capabilities. All tools require an owned,
    authenticated conversation; reminder/profile mutations also receive
    the source message for durable per-turn deduplication. Reminder creation
    is opt-in, while existing-schedule management remains available off-state.

    Returns (condensed_block, search_calls_used, open_url_calls_used,
    reminder_calls_used). The last value tells the caller whether a
    create/update/cancel reminder tool was actually invoked this turn at
    all -- independent of whether that call then succeeded or failed --
    which is what the post-generation reminder-hallucination guard in
    process_ai_response needs: a model that never calls the tool but still
    writes "I've updated your reminder" is not caught by anything that only
    inspects the (nonexistent) tool result.

    When supplied, ``mutation_receipts`` collects escaped reminder/profile
    outcomes separately from optional web data. The four-value return shape
    and legacy combined-block behavior remain unchanged when it is None.
    """
    from capp.classes.search.sanitize import escape_untrusted

    # Provider output is untrusted. Exact per-turn permission is necessary,
    # even if another tool was offered. An allowlist may never be reconstructed
    # from the response or from broader account capabilities.
    allowed = frozenset(offered_tool_names)
    counts = {"web_search": 0, "open_url": 0, "reminder": 0, "profile": 0}
    caps = {
        "web_search": getattr(settings, "SEARCH_MAX_CALLS_PER_MESSAGE", 2),
        "open_url": getattr(settings, "OPEN_URL_MAX_CALLS_PER_MESSAGE", 1),
        "reminder": 1,
        "profile": 1,
    }
    blocks = []
    mutation_names = REMINDER_TOOL_NAMES | PROFILE_TOOL_NAMES

    def record_outcome(name, block):
        if mutation_receipts is not None and name in mutation_names:
            # These strings contain quoted user-controlled values. Preserve
            # the complete factual outcome, but never promote it to policy.
            receipt = escape_untrusted(block)
            if receipt not in mutation_receipts:
                mutation_receipts.append(receipt)
        else:
            blocks.append(block)

    timezone_failed = False
    for call in tool_calls or []:
        name = _get_function_name(_get_function(call))
        if not isinstance(name, str) or name not in allowed:
            record_outcome(
                name if isinstance(name, str) else None,
                "[Tool request rejected: this tool was not offered; no action taken]",
            )
            continue
        if user is None or conversation is None or conversation.user_id != user.pk:
            if mutation_receipts is not None and name in mutation_names:
                record_outcome(name, f"[{name} denied: no owned account conversation; no action taken]")
            continue  # Guests have no offered tools; do not change that boundary.
        # Re-read after model latency, and between dependent tools.
        current_user = CustomUser.objects.filter(pk=user.pk, is_active=True, is_deleted=False).first()
        if current_user is None:
            if mutation_receipts is not None and name in mutation_names:
                record_outcome(name, f"[{name} denied: account unavailable; no action taken]")
            continue
        if name in ("web_search", "open_url"):
            family = name
            permitted = (
                FeaturePreferences.is_enabled(current_user, "websearch")
                and Conversation.objects.filter(pk=conversation.pk, user=user, web_search_enabled=True).exists()
                and get_search_provider() is not None
            )
        elif name in REMINDER_TOOL_NAMES:
            family = "reminder"
            permitted = name != CREATE_REMINDER_TOOL_NAME or FeaturePreferences.is_enabled(current_user, "reminders")
        elif name in PROFILE_TOOL_NAMES:
            family, permitted = "profile", True
        else:
            continue
        if not permitted:
            record_outcome(name, f"[{name} failed: permission is currently disabled; no action taken]")
            continue
        if counts[family] >= caps[family]:
            if mutation_receipts is not None and name in mutation_names:
                record_outcome(name, f"[{name} skipped: per-turn mutation limit reached; no additional action taken]")
            continue
        if family == "reminder":
            if timezone_failed and name != CANCEL_REMINDER_TOOL_NAME:
                record_outcome(name, f"[{name} failed: preceding timezone update failed; no scheduling change made]")
                counts[family] += 1
                continue
            block, used = run_reminder_tool_calls(
                [call],
                conversation=conversation,
                user=current_user,
                character=character,
                source_message=source_message,
            )
        elif family == "profile":
            block, used = run_profile_tool_calls([call], user=current_user, source_message=source_message)
            timezone_failed = "update_timezone failed:" in block
        elif family == "web_search":
            block, used = run_search_tool_calls([call], max_calls=1, user=current_user)
        else:
            block, used = run_open_url_tool_calls([call], max_calls=1)
        counts[family] += used
        if block:
            record_outcome(name, block)
    return ("\n\n".join(blocks), counts["web_search"], counts["open_url"], counts["reminder"])


# The character may offer to send a photo of itself by ending its reply with a
# marker carrying a generation-ready description, e.g.
#   [PHOTO_OFFER:standing in a sunny market, smiling at the camera]
# The marker never reaches the user: process_ai_response splits it off, stores
# the visible text as the reply and a separate `photo_offer` message the client
# renders as an accept/decline card (accepting runs generate_chat_image at the
# discounted CHARACTER_PHOTO_OFFER_COST). Willingness — and the trust threshold
# for intimate photos — is the model's call, driven by the system prompt.
PHOTO_OFFER_MARKER_RE = re.compile(r"\[PHOTO_OFFER:(.*?)\]", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Reminder-hallucination guard
# ---------------------------------------------------------------------------
# PR #184's first commit strengthened the update_reminder tool description,
# success/no-op wording, and the pending-reminders prompt block so a model
# that DOES call the tool is much less likely to misreport a no-op as a real
# change. That is not enough on its own: a production transcript (see
# capp/tests_reminders.py::ReminderHallucinationGuardTests for the exact
# reproduction) showed a fast/small model simply never calling
# update_reminder across several turns of the same conversation, while still
# confidently writing "I've updated the reminder to 4pm / 5pm / 7:30pm" --
# almost certainly pattern-matching its own earlier (also false) "updated!"
# claims already baked into the conversation history rather than reasoning
# about whether it actually invoked anything this turn. No amount of prompt
# wording can force a model to call a tool, so this is a post-generation
# safety net: detect the claim-without-a-call pattern and force an honest
# rewrite if the shared continuation remains, otherwise use a local fallback.
#
# Match assertions, not merely a mutation verb near "reminder". Existing
# schedule descriptions, instructions and negated attempts are not mutations.
# This remains a bounded English heuristic, not verification of tool outcomes.
_REMINDER_CHANGE_VERBS = (
    r"(?:updat(?:e|ed|ing)|mov(?:e|ed|ing)|reschedul(?:e|ed|ing)|"
    r"chang(?:e|ed|ing)|adjust(?:ed|ing)?|cancell?(?:ed|ing)?|"
    r"delet(?:e|ed|ing)|remov(?:e|ed|ing)|creat(?:e|ed|ing)|"
    r"push(?:ed|ing)?(?:\s+back)?|bump(?:ed|ing)?)"
)
_REMINDER_ACTION_VERBS = rf"(?:{_REMINDER_CHANGE_VERBS}|set(?:ting)?|schedul(?:e|ed|ing))"
_REMINDER_DONE_VERBS = (
    r"(?:updated|moved|rescheduled|changed|adjusted|cancell?ed|deleted|removed|created|pushed|bumped|scheduled)"
)
_REMINDER_CLAIM_SUBJECT = r"\breminders?(?:\s+for(?:\s+[\w'-]+){1,3})?\s+"
_REMINDER_CLAIM_OBJECT = (
    r"(?:(?:(?:a|an|the|your|that|this|one|another|new)\s+(?:[\w'-]+\s+)?)?reminders?\b"
    # Keep the existing clock-time boundary for ambiguous pronoun objects.
    r"|(?:it|that|this)\b(?=[^.!?;\n]{0,30}\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b))"
)
_REMINDER_CLAIM_RE = re.compile(
    # Positive first-person actions/promises, not "I could not", "I can"
    # offers, or "you can update". Negation inside the verb phrase cannot match.
    r"\b(?P<actor>i(?:'ve|'ll|'m| have| will| am)?|we(?:'ve|'ll|'re| have| will| are)?)\s+"
    r"(?:(?:just|now|already|successfully|also|gone ahead and)\s+)*"
    rf"{_REMINDER_ACTION_VERBS}\s+(?:up\s+)?{_REMINDER_CLAIM_OBJECT}"
    # Passive changes. "Is set/scheduled" describes status; "has been set"
    # or "is now set" asserts a transition and still needs tool backing.
    rf"|{_REMINDER_CLAIM_SUBJECT}(?:"
    r"(?:(?:has|have|had)\s+(?:(?:just|already|now)\s+)?been|(?:is|are|was|were)\s+now)\s+"
    rf"{_REMINDER_ACTION_VERBS}\b"
    rf"|(?:is|are|was|were)\s+(?:(?:just|already|now)\s+)*{_REMINDER_CHANGE_VERBS}\b"
    r"|(?:is|are)\s+(?:set|scheduled)\b(?=[^,]{0,60}\bnow\s*$))"
    # A terse success confirmation can omit both the actor and auxiliary.
    rf"|\b(?:done|all set)[,:]?\s+{_REMINDER_CLAIM_OBJECT}\s+{_REMINDER_ACTION_VERBS}\b"
    rf"|^\s*{_REMINDER_DONE_VERBS}\s+{_REMINDER_CLAIM_OBJECT}"
    rf"|^\s*(?:(?:the|your|that|this)\s+)?{_REMINDER_CLAIM_SUBJECT}{_REMINDER_DONE_VERBS}\b",
    re.IGNORECASE,
)
_REMINDER_NONASSERTION_RE = re.compile(
    r"\b(?:not|never|cannot|can't|couldn't|haven't|hasn't|didn't|don't|unable|unsure|uncertain|if|unless|whether)\b"
    r"|\b(?:can|could|should|would|may|might|did|have|will)\s*$",
    re.IGNORECASE,
)


def _has_reminder_mutation_claim(ai_content):
    """Detect affirmative action clauses, not reminder-status mentions.

    Keep qualifications local: a failure/question about A must not hide a
    successful-action claim about B, nor should "No problem" negate a claim.
    Used for both the initial draft and the final repair candidate.
    """
    text = (ai_content or "").replace("’", "'")
    clauses = re.split(
        r"[.!?;\n—–]+|:\s+|\s+-\s+|,\s*then\b|\b(?:but|however|yet)\b"
        r"|\band(?=\s+(?:i|we|your|the|that|this|reminders?)\b)",
        text,
        flags=re.IGNORECASE,
    )
    for clause in clauses:
        if re.match(r"\s*(?:if|unless)\b", clause, re.IGNORECASE):
            continue  # Conditional offers, including "If you'd like, I'll ...".
        for claim in _REMINDER_CLAIM_RE.finditer(clause):
            prefix = clause[: claim.start()].rsplit(",", 1)[-1]  # noqa: E203
            actor = (claim.group("actor") or "").lower()
            if ("'ll" in actor or "will" in actor) and re.search(
                r"\b(?:if|unless)\b", clause[claim.end() :], re.IGNORECASE  # noqa: E203
            ):
                continue  # A conditional offer, not an unconditional promise.
            if not _REMINDER_NONASSERTION_RE.search(prefix):
                return True
    return False


def _reminder_hallucination_notice(ai_content, *, user, reminder_schedule_calls_used):
    """Return a PromptBuilder.inject_correction_notice instruction string if
    `ai_content` looks like it is claiming a reminder was created, changed, or
    cancelled even though NO create_reminder/update_reminder/cancel_reminder
    tool call actually happened this turn -- else return None.

    Gated tightly to keep false positives rare in ordinary conversation:
    fires only when (a) no reminder tool was actually called this turn, (b)
    the reply text matches the claim pattern above, and (c) the user has at
    least one PENDING reminder row at all -- so an unrelated line like "I'll
    set a reminder to text you later" from a user with zero reminders in the
    system can never trip it.
    """
    if reminder_schedule_calls_used or user is None:
        return None
    if not _has_reminder_mutation_claim(ai_content):
        return None

    from capp.models import Reminder  # local import: avoid app-loading cycles

    if not Reminder.objects.filter(user=user, status=Reminder.StatusChoices.PENDING).exists():
        return None

    return (
        "Your previous draft reply (never shown to the user) claimed a reminder was created, "
        "updated, or cancelled, but you did NOT call create_reminder, update_reminder, or "
        "cancel_reminder this turn -- so you have no tool confirmation of a mutation. Do not "
        "repeat that claim. Existing reminders and read-only status descriptions are not "
        "mutations. Answer the user's actual question, in your own voice and personality, "
        "using the provided reminder context. For a status question, describe existing "
        "reminders without suggesting a change was attempted. For a requested change, "
        "honestly say you did not perform it. Do not invent an attempted operation, database "
        "failure, unknown error, or rejection reason when no tool was called. If the user "
        "wants to change a reminder, they can update or cancel it manually using the calendar "
        "icon (if visible), or Settings > Advanced > Manage reminders. "
        "Never claim a reminder was created, changed, or cancelled unless you actually call the "
        "matching tool in this same turn."
    )


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
# explicit imagery is allowed; children, deceased people and sexual content
# involving animals are not.
PROHIBITED_SUBJECT_SYSTEM_MESSAGE = (
    "That image request cannot be generated. Images of children or deceased "
    "people are not allowed, and neither is sexual content involving animals. "
    "No sparks were charged."
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


class ChatWriteUnavailable(Exception):
    """The account, character or conversation stopped accepting this write."""


def chat_write_error(conversation):
    """Archiving one's chat is not closure; retiring its character is."""
    if conversation.status == Conversation.StatusChoices.CLOSED:
        return "conversation_closed"
    character = conversation.character
    if not character.is_active or character.status == Character.StatusChoices.ARCHIVED:
        return "character_unavailable"
    return None


def lock_writable_chat(*, conversation_id, character_id, user_id=None, guest_session_id=None):
    """Recheck a send under locks held by the caller's atomic transaction.

    User -> character -> conversation matches account erasure. PostgreSQL
    NO KEY UPDATE still excludes erasure, while allowing FK key-share locks
    taken by an independent refund's financial rows.
    """
    user = None
    if user_id is not None:
        user = CustomUser.objects.select_for_update(no_key=True).filter(pk=user_id).first()
        if user is None:
            raise ChatWriteUnavailable("conversation_not_found")
        if not user.is_active or user.is_deleted:
            raise ChatWriteUnavailable("account_unavailable")
    character = Character.objects.select_for_update(no_key=True).filter(pk=character_id).first()
    if character is None:
        raise ChatWriteUnavailable("conversation_not_found")
    conversation = (
        Conversation.objects.select_for_update(no_key=True)
        .filter(pk=conversation_id, character_id=character_id)
        .first()
    )
    if conversation is None:
        raise ChatWriteUnavailable("conversation_not_found")
    if user_id is not None:
        if conversation.user_id != user.pk:
            raise ChatWriteUnavailable("permission_denied")
    elif not guest_session_id or conversation.user_id is not None or conversation.guest_session_id != guest_session_id:
        raise ChatWriteUnavailable("permission_denied")
    conversation.character = character
    conversation.user = user
    error = chat_write_error(conversation)
    if error:
        raise ChatWriteUnavailable(error)
    return conversation


def _refund_unwritable_reply(*, reservation, conversation, operation_id, is_guest, guest_session_id):
    """Cancel an existing hold, including a retry archived before this run.

    Never reserve/recreate anything to cancel it. Erasure may already have
    removed an account's operational reservation; then there is no beneficiary.
    Called after the chat-lock transaction has exited.
    """
    holds = BillingReservation.objects.filter(status=BillingReservation.Status.RESERVED)
    if reservation is not None:
        held = holds.filter(pk=reservation.pk).first()
    else:
        holds = holds.filter(
            idempotency_key=operation_id,
            feature=BillingReservation.Feature.MESSAGE_PAIR,
        )
        if is_guest and guest_session_id:
            held = holds.filter(guest_identity_id=guest_session_id).first()
        elif conversation.user_id is not None:
            held = holds.filter(user_id=conversation.user_id).first()
        else:
            held = None
    if held is not None:
        try:
            BillingReservationService.refund(held, reason="chat_no_longer_writable")
        except ReservationError as exc:
            # A claimed guest's beneficiary may have been erased. Keep that
            # hold for operator review, never recreate or recredit a guest,
            # and finish the terminal cancellation rather than retrying it.
            logger.warning(
                "Chat cancellation left reservation=%s held for review reason=%s",
                held.pk,
                exc.code,
            )


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
        user_message = Message.objects.select_related("conversation").get(
            message_id=user_message_id, conversation=conversation
        )
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

        error = chat_write_error(conversation)
        if error:
            raise ChatWriteUnavailable(error)

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
            byok_uses_gateway = False
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
            byok_uses_gateway = False
            try:
                byok = UserBYOKConfig.objects.get(user=user, is_active=True)
                byok_cost = BYOK_MESSAGE_PAIR_COST
                byok_uses_gateway = is_uwenzai_gateway_url(byok.api_url)
                logger.info(
                    "BYOK active for user=%s model=%s (gateway=%s)",
                    user.user_id,
                    byok.model_name,
                    byok_uses_gateway,
                )
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

        # Whether this turn's photos ride along as image parts. BYOK: the user
        # chose their own endpoint, so image support is their responsibility
        # (Settings documents this); a text-only model there fails through the
        # normal refund path. Platform: only when the routed model can view
        # images — the send endpoint already refuses photo sends for
        # text-only platform models, so this is the second, defensive gate
        # (e.g. a mid-deploy model rename).
        turn_has_photos = (
            user_message.sender_type == Message.SenderType.USER
            and MessageAttachment.objects.filter(
                message_id=user_message.message_id,
                moderation_status=MessageAttachment.ModerationStatus.APPROVED,
            ).exists()
        )
        if byok:
            include_images = turn_has_photos
        else:
            effective_lang_for_model = (
                conversation.preferred_language
                or (character.primary_language if character else None)
                or user_message.language_code
            )
            logger.info(
                "LANG CHECK: conv_lang=%s  char_lang=%s  msg_lang=%s  effective_lang=%s",
                conversation.preferred_language,
                character.primary_language if character else None,
                user_message.language_code,
                effective_lang_for_model,
            )
            if effective_lang_for_model in ("sw", "sh"):
                swahili_model = getattr(settings, "CORTECS_SWAHILI_MODEL", None)
                if swahili_model:
                    model = swahili_model
                    logger.info("Swahili/Sheng conversation - switching model to %s", model)
            include_images = turn_has_photos and supports_vision(model)

        logger.info(
            "Building prompt with max_context=%d, model=%s",
            effective_max,
            model or "default",
        )
        # -- BYOK system-prompt override -------------------------------------------
        # The custom prompt replaces ONLY the PLATFORM RULES section inside
        # the assembled system prompt (folded in by PromptBuilder.build_messages).
        # The character card (personality), memory, relationship, summary and
        # the safety sandwich are immutable platform context — a BYOK user
        # must never be able to drop them by setting a system prompt. The old
        # wholesale messages[0] replacement here caused exactly that bug.
        byok_rules_override = byok.system_prompt if (byok and byok.system_prompt) else None

        try:
            messages = PromptBuilder.build_messages(
                conversation=conversation,
                user_message=user_message,
                max_context_tokens=effective_max,
                model=byok.model_name if byok else model,
                include_images=include_images,
                platform_rules_override=byok_rules_override,
            )
            logger.info(
                "Prompt built - system + %d history messages (images=%s)",
                len(messages) - 1,
                include_images,
            )
            if byok_rules_override:
                logger.info(
                    "BYOK system prompt applied to platform rules only (%d chars)",
                    len(byok_rules_override),
                )
        except Exception as e:
            logger.exception("Prompt construction failed")
            PaidFeature.refund(reservation, reason="prompt_build_failed")
            _mark_message_failed(user_message, f"Prompt error: {str(e)}")
            return

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
        # Set from _run_offered_tool_calls whenever the model actually invokes
        # create_reminder/update_reminder/cancel_reminder this turn (whether
        # or not that call then succeeds). Consumed after the AI call below
        # by the reminder-hallucination guard, which needs to tell "the tool
        # ran and failed" (already handled by the tool's own relayed failure
        # text) apart from "the model never called anything and just claimed
        # success in plain text" (not caught by anything else).
        reminder_schedule_calls_used = 0
        # The queued task receives the authoritative guest marker from the
        # send endpoint.  A user-less conversation is also necessarily a guest
        # conversation, which keeps this fail-closed if a malformed task were
        # ever enqueued without that marker.
        is_guest_conversation = is_guest or conversation.user_id is None
        # Per-account feature toggles (Settings → Advanced). Optional chat
        # features start disabled; each must be enabled by the user first.
        feature_prefs = FeaturePreferences.get(conversation.user) if not is_guest_conversation else None
        if (
            not is_guest_conversation
            and feature_prefs
            and feature_prefs.get("websearch")
            and conversation.web_search_enabled
            and get_search_provider() is not None
        ):
            search_tools = [SEARCH_TOOL_SPEC, OPEN_URL_TOOL_SPEC]
            logger.info(
                "Web search + open_url tools offered for conv=%s (byok=%s)",
                conversation_id,
                bool(byok),
            )

        # -- create_reminder tool ----------------------------------------------------
        # Offered independently of the web_search toggle above and gated on
        # having a durable account AND the user having enabled the reminders
        # feature in Settings → Advanced (a guest session has no reliable way
        # to be reachable "tomorrow", so it is never offered to guests, same
        # gating style as search_tools).
        reminder_tools = None
        if not is_guest_conversation:
            # Opt-out blocks new schedules, not management of existing ones.
            reminder_tools = build_reminder_tool_specs(
                allow_create=bool(feature_prefs and feature_prefs.get("reminders"))
            )

        # -- update_timezone tool -----------------------------------------------------
        # Same gating as reminder_tools: a guest has no durable CustomUser row
        # for a timezone update to persist against, so this is never offered
        # to guests either.
        profile_tools = None
        if not is_guest_conversation:
            profile_tools = build_profile_tool_specs()

        # UwenzAi's own Agent Gateway does not support OpenAI tool calling
        # yet: a request carrying `tools` is rejected as an unsupported
        # parameter, which would fail every message. Gateway-BYOK chats
        # therefore run plain completions — search/reminder/profile tools
        # are not offered (reminders can still be managed from the UI).
        if byok is not None and byok_uses_gateway:
            if search_tools or reminder_tools or profile_tools:
                logger.info(
                    "Skipping tool offers for Agent Gateway BYOK (conv=%s)",
                    conversation_id,
                )
            search_tools = None
            reminder_tools = None
            profile_tools = None

        offered_tools = (search_tools or []) + (reminder_tools or []) + (profile_tools or [])
        offered_tools = offered_tools or None
        offered_tool_names = frozenset(spec["function"]["name"] for spec in offered_tools or [])
        if search_tools:
            messages = PromptBuilder.inject_correction_notice(messages, SEARCH_GROUNDING_NOTICE)

        # Guests always use the default fast behavior even if a stale or
        # manually altered conversation row says otherwise. Accounts that have
        # not enabled the deepthink feature are pinned to fast as well — a
        # previously saved "deep" conversation setting must not keep burning
        # tokens after the user turned the feature off.
        reasoning_effort = (
            Conversation.ReasoningEffort.FAST
            if is_guest_conversation or not (feature_prefs and feature_prefs.get("deepthink"))
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
        continuation_used = False
        try:
            # The complete request differs from the build result: tool schemas
            # also consume context (any BYOK override is already folded into
            # the platform-rules section by build_messages).
            messages = PromptBuilder.enforce_budget(
                messages,
                max_context_tokens=effective_max,
                model=byok.model_name if byok else model,
                tools=offered_tools,
            )
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
                if offered_tools:
                    byok_call_kwargs["tools"] = offered_tools
                response = BYOKService.chat_completion(
                    byok_config=byok,
                    messages=messages,
                    max_tokens=api_max_tokens,
                    temperature=0.7,
                    reasoning_effort=reasoning_effort,
                    **byok_call_kwargs,
                )

            else:
                call_kwargs = {}
                if offered_tools:
                    call_kwargs["tools"] = offered_tools
                response = CortecsClient.chat_completion(
                    messages=messages,
                    model=model,
                    max_tokens=api_max_tokens,
                    temperature=0.7,
                    reasoning_effort=reasoning_effort,
                    **call_kwargs,
                )

            # One bounded finalization round, shared by both providers. The
            # presence of tool requests -- NOT successful execution/nonempty
            # results -- decides whether the first response is an interim
            # draft. Even denied/refused/malformed calls need a final answer.
            # Dispatch still enforces the exact original allowlist and live
            # permissions; no additional tools or network fallback are offered.
            initial_choices = response.get("choices") or []
            tool_calls = (initial_choices[0].get("message") or {}).get("tool_calls") if initial_choices else None
            if tool_calls:
                mutation_receipts = []
                (
                    condensed,
                    search_calls_used,
                    open_url_calls_used,
                    reminder_schedule_calls_used,
                ) = _run_offered_tool_calls(
                    tool_calls,
                    conversation_id,
                    offered_tool_names=offered_tool_names,
                    conversation=conversation,
                    user=user,
                    character=character,
                    source_message=user_message,
                    mutation_receipts=mutation_receipts,
                )
                if not condensed and not mutation_receipts:
                    condensed = "[Tool requests produced no usable outcome; no successful action is confirmed]"
                follow_up_messages = PromptBuilder.inject_untrusted_web_results(messages, condensed)
                follow_up_messages = PromptBuilder.inject_mutation_results(follow_up_messages, mutation_receipts)
                follow_up_messages = PromptBuilder.inject_correction_notice(follow_up_messages, TOOL_COMPLETION_NOTICE)
                follow_up_messages = PromptBuilder.enforce_budget(
                    follow_up_messages,
                    max_context_tokens=effective_max,
                    model=byok.model_name if byok else model,
                    tools=None,
                )
                messages = follow_up_messages
                continuation_used = True
                if byok:
                    response = BYOKService.chat_completion(
                        byok_config=byok,
                        messages=follow_up_messages,
                        max_tokens=api_max_tokens,
                        temperature=0.7,
                        reasoning_effort=reasoning_effort,
                    )
                else:
                    response = CortecsClient.chat_completion(
                        messages=follow_up_messages,
                        model=model,
                        max_tokens=api_max_tokens,
                        temperature=0.7,
                        reasoning_effort=reasoning_effort,
                    )

            logger.info("AI response received: id=%s", response.get("id"))
        except PromptBudgetExceeded as exc:
            logger.warning("Final prompt rejected for conv=%s: %s", conversation_id, exc)
            PaidFeature.refund(reservation, reason="prompt_budget_exceeded")
            _mark_message_failed(user_message, f"Prompt error: {exc}")
            return
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
                    (
                        "Sorry, the AI could not view your photo right now (the image model is "
                        "temporarily unavailable). No sparks were charged — please try again later."
                        if turn_has_photos
                        else "Sorry, the AI encountered an error. Please try again later."
                    ),
                )
            return

        # Nothing here streams: chat_completion returns the whole reply or
        # nothing. A response with no choices, or with empty content, produced
        # no durable answer and is therefore a terminal failure, not a partial
        # success the user should pay for.
        ai_content, response_problem = _completed_reply_content(response)
        if response_problem:
            logger.warning("AI response was not complete: %s", response_problem)
            PaidFeature.refund(reservation, reason=response_problem)
            _mark_message_failed(user_message, "AI did not return a completed answer.")
            return

        logger.info("AI response length: %d chars", len(ai_content))

        # ── Reminder-hallucination guard ──────────────────────────────────
        # See the _reminder_hallucination_notice / _has_reminder_mutation_claim block
        # near the top of this module for why this exists: a model can claim
        # a reminder was created/updated/cancelled in plain text without ever
        # calling the tool, and no amount of prompt wording reliably prevents
        # that on its own. It shares the single continuation with tool
        # finalization; a search follow-up can never trigger a third round.
        notice_text = _reminder_hallucination_notice(
            ai_content,
            user=user,
            reminder_schedule_calls_used=reminder_schedule_calls_used,
        )
        if notice_text:
            corrected_response = None
            if not continuation_used:
                logger.warning(
                    "Reminder-hallucination guard triggered for conv=%s; using the remaining continuation",
                    conversation_id,
                )
                # Use the latest assembled context, never rebuild from an
                # earlier prompt that could drop evidence or completion rules.
                corrected_messages = PromptBuilder.inject_correction_notice(
                    messages, notice_text + "\n" + TOOL_COMPLETION_NOTICE
                )
                try:
                    corrected_messages = PromptBuilder.enforce_budget(
                        corrected_messages,
                        max_context_tokens=effective_max,
                        model=byok.model_name if byok else model,
                        tools=None,
                    )
                    continuation_used = True
                    if byok:
                        corrected_response = BYOKService.chat_completion(
                            byok_config=byok,
                            messages=corrected_messages,
                            max_tokens=api_max_tokens,
                            temperature=0.7,
                            reasoning_effort=reasoning_effort,
                        )
                    else:
                        corrected_response = CortecsClient.chat_completion(
                            messages=corrected_messages,
                            model=model,
                            max_tokens=api_max_tokens,
                            temperature=0.7,
                            reasoning_effort=reasoning_effort,
                        )
                except PromptBudgetExceeded as exc:
                    logger.warning("Correction prompt rejected for conv=%s: %s", conversation_id, exc)
                    PaidFeature.refund(reservation, reason="prompt_budget_exceeded")
                    _mark_message_failed(user_message, f"Prompt error: {exc}")
                    return
                except Exception:
                    logger.exception(
                        "Reminder-hallucination correction failed for conv=%s; using a fixed honest message",
                        conversation_id,
                    )

            if corrected_response is not None:
                # Validate the actual last generation too. A repair response
                # containing another tool call or null content is not a paid
                # final answer, even if its prose no longer claims success.
                response = corrected_response
                ai_content, response_problem = _completed_reply_content(response)
                if response_problem:
                    logger.warning("AI correction was not complete: %s", response_problem)
                    PaidFeature.refund(reservation, reason=response_problem)
                    _mark_message_failed(user_message, "AI did not return a completed answer.")
                    return

            if corrected_response is None or _has_reminder_mutation_claim(ai_content):
                logger.warning(
                    "Reminder repair for conv=%s unavailable, budget exhausted, or repeated the claim; "
                    "using fixed fallback text",
                    conversation_id,
                )
                ai_content = (
                    "Sorry, I can't confirm that reminder change actually went through. Please use "
                    "the calendar icon (if visible), or Settings > Advanced > Manage reminders "
                    "to update or cancel it manually."
                )

        if (
            search_tools
            or any(_get_function_name(_get_function(call)) in ("web_search", "open_url") for call in tool_calls or [])
        ) and is_lookup_handoff(ai_content):
            # Run after ALL generation paths, including reminder repair. A
            # prose promise is not a background job; replace only this narrow
            # handoff without another provider call or another message.
            logger.warning("Replacing lookup-only handoff with an unverified-details notice")
            ai_content = SEARCH_INCOMPLETE_REPLY

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

        # ── Advisory model-refusal marker ─────────────────────────────────
        # The SAFETY CAUTION segment in the prompt tells the model to begin
        # refusals with [UWENZAI_SAFETY_REFUSAL]. Like [PHOTO_OFFER:...] it is
        # platform syntax: strip it before the reply is stored or shown. The
        # marker is stripped for guests too (they must never see it), but the
        # advisory flag itself is only recorded for authenticated users —
        # guests have no account to review. The raw text is kept for the
        # fingerprint so a redelivered task cannot double-count one refusal.
        model_refusal_raw = ai_content
        ai_content, model_refusal_detected = extract_refusal(ai_content)
        if model_refusal_detected and not ai_content:
            # A marker-only reply would leave an empty bubble; keep a minimal
            # line so the transcript still reads naturally.
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
            conversation = lock_writable_chat(
                conversation_id=conversation_id,
                character_id=character.pk,
                user_id=user.pk if user is not None and not is_guest else None,
                guest_session_id=guest_session_id if is_guest else None,
            )
            if not Message.objects.filter(pk=user_message_id, conversation=conversation).exists():
                raise ChatWriteUnavailable("message_not_found")
            # The pre-provider objects are stale after a long network call.
            user = conversation.user
            character = conversation.character
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

        # ── Advisory model-refusal flag (after the reply is durable) ──────
        # Recorded only once the reply is stored, settled and delivered: a
        # flagging failure must never break or refund a chat turn, so this
        # runs in its own guard. BYOK turns count the same as platform turns
        # — the caution segment is in every prompt either way.
        if model_refusal_detected and user is not None:
            try:
                SafetyLedgerService.record_model_refusal(
                    user=user,
                    conversation_id=conversation.conversation_id,
                    response_text=model_refusal_raw,
                    user_text=user_message.content,
                )
            except Exception:
                logger.exception(
                    "Model-refusal flag recording failed for conv=%s; reply is unaffected",
                    conversation_id,
                )

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
                with transaction.atomic():
                    conversation = lock_writable_chat(
                        conversation_id=conversation_id,
                        character_id=character.pk,
                        user_id=user.pk if user is not None else None,
                    )
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
            except ChatWriteUnavailable:
                # The answer committed first, so keep its settled charge; no
                # new optional message/notification may revive the closed chat.
                return
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

    except ChatWriteUnavailable as exc:
        _refund_unwritable_reply(
            reservation=reservation,
            conversation=conversation,
            operation_id=operation_id,
            is_guest=is_guest,
            guest_session_id=guest_session_id,
        )
        # Do not downgrade a completed turn on a late redelivery after closure.
        # A conditional UPDATE cannot recreate a message deleted by erasure.
        if not Message.objects.filter(conversation_id=conversation_id, client_operation_id=reply_key).exists():
            Message.objects.filter(pk=user_message_id, conversation_id=conversation_id).update(
                status=Message.StatusChoices.FAILED,
                error_message=str(exc),
                updated_at=timezone.now(),
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
        # Photo-only messages carry no user text to extract facts from; the AI
        # reply alone would tempt the extractor into inventing "user said"
        # facts about pixels it never saw.
        if not (user_message.content or "").strip():
            logger.info("Skipping memory extraction for photo-only message %s", user_message_id)
            return
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
            # The view already placed the hold for this operation ID — possibly
            # at the discounted photo-offer price, which differs from the
            # standard image price. Re-reserving at the standard price would
            # be rejected as an idempotency-key conflict, so the task must
            # present the exact terms of the hold it is resuming.
            cost_override=(int(reservation.amount_sparks) if reservation is not None else None),
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
        elif reason == GENERATED_IMAGE_BLOCKED_REASON:
            _send_system_message(
                conversation.conversation_id,
                "That image could not be generated because our content-safety "
                "screening blocked it. No sparks were charged.",
                code=str(reason),
            )
        elif reason == GENERATED_IMAGE_UNAVAILABLE_REASON:
            # An outage is not a violation — do not tell the user their request
            # was blocked when the screening simply could not run.
            _send_system_message(
                conversation.conversation_id,
                "We could not check that image, so it was not generated. No "
                "sparks were charged — please try again shortly.",
                code=str(reason),
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


# A managed Postgres instance may suspend itself while idle to save costs
# (see capp/classes/reminders/reminder_service.py docstring for the same
# note on the reminder task below). Celery workers never fire Django's
# request_started/request_finished signals -- those are what normally close
# stale connections between HTTP requests -- so a connection object that
# survived from a previous Beat tick can be a dead handle to a socket the DB
# already dropped. close_old_connections() clears that out before the first
# real query runs. If the DB is still waking up when that query executes, it
# raises OperationalError, which is retried below with backoff instead of
# permanently abandoning the run (the previous behaviour: a single failed
# tick meant proactive messages/reminders for that cycle were simply lost).
DB_RECONNECT_MAX_RETRIES = 5
DB_RECONNECT_BASE_DELAY_SECONDS = 10


@shared_task(bind=True, max_retries=DB_RECONNECT_MAX_RETRIES, default_retry_delay=DB_RECONNECT_BASE_DELAY_SECONDS)
def check_and_send_proactive_messages(self):
    """
    Celery task that checks all enabled proactive chat preferences
    and sends proactive messages where conditions are met.
    Runs every 30 minutes via Celery Beat.
    """
    if not connection.in_atomic_block:
        # Guarded: closing a connection while inside an atomic block (e.g. a
        # test wrapped in TestCase's transaction, or any future caller that
        # invokes this task from within its own transaction) would tear down
        # the connection out from under that transaction instead of just
        # clearing a genuinely stale one. A real Celery worker invocation
        # never has an outer atomic block at task entry, so this guard is a
        # no-op there and only protects the in-transaction call path.
        close_old_connections()
    logger.info("Starting proactive chat check...")
    try:
        sent_count = ProactiveChatService.check_and_send_proactive_messages()
        logger.info("Proactive chat check completed. Sent %d messages.", sent_count)
        return {"sent_count": sent_count}
    except OperationalError as exc:
        logger.warning(
            "DB unavailable during proactive chat check (retry %d/%d): %s",
            self.request.retries,
            self.max_retries,
            exc,
        )
        raise self.retry(exc=exc, countdown=DB_RECONNECT_BASE_DELAY_SECONDS * (2**self.request.retries))
    except Exception as e:
        logger.exception("Error in proactive chat task: %s", e)
        raise


# -------------------------------------------------------------
# Reminder Delivery Task
# -------------------------------------------------------------
# Deliberately its own Celery Beat entry, independent of
# check_and_send_proactive_messages above: a reminder is an explicit,
# per-instance user request ("remind me to call Paul tomorrow"), not a
# general opt-in re-engagement nudge, so it must never be throttled by
# ProactiveChatPreference.enabled or MAX_PROACTIVE_WITHOUT_REPLY. See
# capp/classes/reminders/reminder_service.py for the delivery logic.


@shared_task(bind=True, max_retries=DB_RECONNECT_MAX_RETRIES, default_retry_delay=DB_RECONNECT_BASE_DELAY_SECONDS)
def check_and_send_due_reminders(self):
    """
    Celery task that finds every due Reminder and delivers it.
    Runs every 5 minutes via Celery Beat (finer-grained than the 30-minute
    proactive check, since a reminder is a precise time commitment).
    """
    from capp.classes.reminders.reminder_service import ReminderService

    if not connection.in_atomic_block:
        close_old_connections()
    logger.info("Starting due-reminders check...")
    try:
        sent_count = ReminderService.check_and_send_due()
        logger.info("Due-reminders check completed. Sent %d reminders.", sent_count)
        return {"sent_count": sent_count}
    except OperationalError as exc:
        logger.warning(
            "DB unavailable during due-reminders check (retry %d/%d): %s",
            self.request.retries,
            self.max_retries,
            exc,
        )
        raise self.retry(exc=exc, countdown=DB_RECONNECT_BASE_DELAY_SECONDS * (2**self.request.retries))
    except Exception as e:
        logger.exception("Error in reminder delivery task: %s", e)
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


@shared_task(name="capp.tasks.cleanup_revoked_agent_keys")
def cleanup_revoked_agent_keys():
    """Hard-delete revoked Agent API keys past the retention window.

    Revoked keys are kept for a grace period (review/support), then removed so
    the key list cannot accumulate stale entries. Usage logs survive deletion:
    AgentUsageLog.key is SET_NULL with a denormalized key_hint, so billing
    history stays intact. Keys revoked without a revocation timestamp are
    never deleted (unknown revocation date).
    """
    from datetime import timedelta

    from django.conf import settings

    from capp.models import AgentAPIKey

    retention_days = getattr(settings, "AGENT_KEY_REVOKED_RETENTION_DAYS", 30)
    if retention_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=retention_days)
    stale = AgentAPIKey.objects.filter(is_active=False, revoked_at__lt=cutoff)
    count = stale.count()
    if count:
        stale.delete()
        logger.info(
            "Agent Gateway: removed %d revoked API key(s) past the %d-day retention window",
            count,
            retention_days,
        )
    return count


@shared_task(name="capp.tasks.sync_agent_catalog")
def sync_agent_catalog():
    """Refresh the Agent Gateway model catalog (AgentModelPrice) from configured providers.

    Scheduled daily; the workspace pricing view is database-only, so this task
    (plus opportunistic refreshes from external /models requests) is what keeps
    the catalog populated with current models and prices. Models served
    upstream without a price (new releases on providers whose /models endpoint
    does not publish pricing) are flagged here so the operator can add them to
    the settings price map the day they appear.
    """
    from capp.classes.agent_gateway.catalog_sync import run_catalog_sync

    summary = run_catalog_sync()
    for name, result in summary.items():
        new_skipped = result.get("new_skipped_models")
        if new_skipped:
            logger.warning(
                "Agent Gateway: %s now serves model(s) without a price — invisible in the catalog "
                "until added to the provider price map in settings: %s",
                name,
                ", ".join(new_skipped),
            )
    return summary


@shared_task(name="capp.tasks.retry_media_deletions")
def retry_media_deletions():
    """Finish committed erasures interrupted by storage downtime/process death."""
    from capp.models import MediaDeletionJob

    completed = 0
    # Failed objects rotate behind untouched jobs; a single bad object must not
    # permanently starve newer erasures.
    for job in MediaDeletionJob.objects.order_by("updated_at", "pk")[:200]:
        if job.perform():
            completed += 1
        else:
            MediaDeletionJob.objects.filter(pk=job.pk).update(updated_at=timezone.now())
    return completed


@shared_task(name="capp.tasks.send_support_message_push")
def send_support_message_push(user_id):
    """Web Push for a new support-ticket message (admin outreach).

    Scheduled on commit by SupportService.create_admin_outreach_ticket so the
    admin's request never pays for external push-endpoint latency. A failed
    push must never undo the ticket: every failure is logged and swallowed.

    Content-free by design — see PushService.send_support_message_push.
    """
    from capp.models import CustomUser

    user = CustomUser.objects.filter(user_id=user_id).first()
    if user is None:
        logger.warning("Support push skipped: user %s no longer exists", user_id)
        return 0
    try:
        return PushService.send_support_message_push(user)
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.exception("Support push failed for user %s", user_id)
        return 0
