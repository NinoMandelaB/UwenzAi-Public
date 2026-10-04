# capp/classes/reminders/reminder_service.py
"""
Delivery of due Reminder rows created by the create_reminder tool.

Intentionally has NO dependency on ProactiveChatPreference: a reminder is
something the user explicitly asked for in a specific message, not an
unprompted re-engagement nudge, so it must fire even when the user has
proactive/re-engagement messages disabled for that character. If you're
tempted to gate this on `pref.enabled`, don't -- that's a different feature
with a different consent basis.

Lifecycle:
- A durable ReminderDeliveryAttempt claims one schedule revision for ten
  minutes. It owns one billing key/reference. Provider work is outside the
  claim/commit transactions; commit rechecks revision, lease and deadline.
  Every failed/abandoned attempt is refunded before a differently keyed
  attempt may reserve again. Refund-pending records survive schedule deletion.
- A reminder row only ever exists while it is PENDING. Both terminal
  outcomes -- successful delivery, or giving up after the grace period --
  DELETE the row rather than leaving a SENT/FAILED_* row behind forever.
  The delivered content itself is not lost: it lives on as a normal
  Message the user can scroll back to, tagged via
  ``metadata={"reminder": True, "reminder_id": ...}``.
- A transient delivery failure (insufficient Sparks, a billing hiccup, a
  provider error, a lost DB connection) does NOT immediately give up: the
  row is left PENDING so the next 5-minute Beat tick retries it, up to
  ``REMINDER_EXPIRY_GRACE_HOURS`` past the original due_at. Only once that
  grace period has elapsed do we send a single final apology and delete the
  row. This is what actually fixes the "DB went to sleep right as the
  reminder was due" class of failure: the old behaviour treated the very
  first failed attempt as permanent.
- The "still retrying" notice for a transient failure is sent at most once
  per reminder (guarded by ``notice_sent``), not once per failed attempt --
  otherwise a reminder that keeps failing every 5 minutes for hours would
  spam the user with the same notice over and over. Notices are durable
  zero-cost Messages; socket/push delivery is only a best-effort hint.
"""

import logging
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import OperationalError, transaction
from django.utils import timezone as django_timezone
from datetime import timedelta

from capp.classes.ai.cortecs_client import CortecsClient
from capp.classes.ai.background_prompts import (
    REMINDER_TEXT_LIMIT,
    build_background_messages,
    untrusted_facts,
    untrusted_history,
)
from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature, ReservationError
from capp.classes.billing.reservation_service import BillingReservationService
from capp.classes.byok.byok_service import BYOKService
from capp.classes.chat import unread_service
from capp.classes.push.push_service import PushService
from capp.classes.memory.memory_service import MemoryService
from capp.classes.search.sanitize import clip
from capp.constants import BYOK_MESSAGE_PAIR_COST, REMINDER_EXPIRY_GRACE_HOURS
from capp.authentication import account_can_generate
from capp.models import (
    BillingReservation,
    CustomUser,
    Message,
    Reminder,
    ReminderDeliveryAttempt,
    UserBYOKConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Africa/Nairobi"
DELIVERY_LEASE = timedelta(minutes=10)

# Static, zero-cost fallback text used only when we cannot even attempt an
# LLM-phrased delivery (insufficient balance, or the provider call itself
# failed) -- it must never require its own Spark charge, or a user who is
# out of Sparks could never learn why their reminder didn't arrive.
INSUFFICIENT_SPARKS_TEMPLATES = {
    "en": (
        "I couldn't send your reminder to {reminder_text} -- your Spark balance "
        "was below the {spark_cost} needed. Top up and I'll keep trying to remind "
        "you until {grace_deadline}!"
    ),
    "sw": (
        "Sikuweza kukutumia ukumbusho wa {reminder_text} -- salio lako la Spark "
        "lilikuwa chini ya {spark_cost} zinazohitajika. Jaza tena na nitaendelea "
        "kujaribu kukukumbusha hadi {grace_deadline}!"
    ),
}

DELIVERY_FAILED_TEMPLATES = {
    "en": (
        "I meant to remind you to {reminder_text} just now, but something went wrong on my end. "
        "I'll keep trying until {grace_deadline}!"
    ),
    "sw": (
        "Nilitaka kukukumbusha {reminder_text} sasa hivi, lakini kuna tatizo lililojitokeza. "
        "Nitaendelea kujaribu hadi {grace_deadline}!"
    ),
}

# Sent exactly once, right before the row is deleted for good -- the user's
# only signal that a reminder they were expecting is never coming.
GIVE_UP_TEMPLATES = {
    "en": (
        "I'm sorry -- I wasn't able to get your reminder to {reminder_text} to you in time, "
        "and I've stopped trying. Feel free to ask me again!"
    ),
    "sw": (
        "Samahani -- sikuweza kukufikishia ukumbusho wa {reminder_text} kwa wakati, "
        "na nimeacha kujaribu tena. Karibu uniulize tena!"
    ),
}


def _lang_key(conversation) -> str:
    lang = (conversation.preferred_language or "en") if conversation else "en"
    return "sw" if lang in ("sw", "sh") else "en"


def _local_deadline_str(due_at, timezone_snapshot: str) -> str:
    try:
        tz = ZoneInfo(timezone_snapshot or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    deadline = due_at + timedelta(hours=REMINDER_EXPIRY_GRACE_HOURS)
    return deadline.astimezone(tz).strftime("%A, %B %d at %I:%M %p")


class ReminderService:
    """Celery-Beat-driven delivery of due reminders."""

    @classmethod
    def check_and_send_due(cls) -> int:
        """
        Find every PENDING reminder whose due_at has passed and deliver it.
        Called by Celery Beat every 5 minutes. Returns the number sent.
        """
        # Recovery must run even if cancellation removed the last schedule.
        cls.recover_pending_attempts()
        now = django_timezone.now()
        due = (
            Reminder.objects.filter(
                status=Reminder.StatusChoices.PENDING,
                due_at__lte=now,
                user__is_active=True,
                user__is_deleted=False,
            )
            .select_related("user", "character", "conversation")
            .order_by("due_at")
        )

        sent_count = 0
        for reminder in due:
            try:
                if cls._deliver(reminder):
                    sent_count += 1
            except OperationalError:
                # The DB connection died mid-sweep (e.g. a managed Postgres
                # instance suspending itself while idle, then taking a beat
                # to wake back up). Abort the whole sweep rather than
                # silently failing every remaining reminder one-by-one --
                # letting this propagate lets the Celery task's own
                # retry/backoff (see capp/tasks.py) pick the sweep back up
                # shortly, once the connection is healthy again. Reminders
                # already left PENDING are safe: check_and_send_due is
                # idempotent and will simply pick them up again next tick.
                logger.warning(
                    "DB connection lost while delivering reminder %s; aborting sweep for retry",
                    reminder.reminder_id,
                )
                raise
            except Exception:
                logger.exception("Unhandled error delivering reminder %s", reminder.reminder_id)

        logger.info("Reminder delivery check completed. Sent %d reminders.", sent_count)
        return sent_count

    # ------------------------------------------------------------------

    @classmethod
    def _deliver(cls, reminder: Reminder) -> bool:
        # A sweep object is only an ID hint, never an authorization snapshot.
        reminder_id = reminder.pk
        with transaction.atomic():
            # Mutations use owner -> schedule too. Inserting an attempt takes
            # an owner FK key-share lock, so acquiring the owner first avoids
            # a FK-lock inversion with concurrent cancel/update.
            if not CustomUser.objects.select_for_update().filter(pk=reminder.user_id).exists():
                return False
            reminder = Reminder.objects.select_for_update().filter(pk=reminder_id).first()
            if reminder is None or reminder.status != Reminder.StatusChoices.PENDING:
                return False
            now = django_timezone.now()
            if not account_can_generate(reminder.user):
                return False
            if reminder.due_at > now:
                return False
            if cls._expired(reminder, now):
                cls._expire_locked(reminder)
                return False
            # Close expired/refunded attempts before choosing current BYOK terms.
            # Both the key AND operation_reference change on every new attempt.
            for old in ReminderDeliveryAttempt.objects.select_for_update().filter(
                reminder_key=reminder_id,
                status__in=[ReminderDeliveryAttempt.Status.ACTIVE, ReminderDeliveryAttempt.Status.REFUND_PENDING],
            ):
                if (
                    old.status == ReminderDeliveryAttempt.Status.ACTIVE
                    and old.revision == reminder.revision
                    and old.lease_until > now
                    and not cls._expired(reminder, now)
                ):
                    return False
                cls._close_attempt_locked(old)
            cls._refund_legacy_locked(reminder_id)
            # Cleanup/lock waits can cross the deadline too.
            now = django_timezone.now()
            if cls._expired(reminder, now):
                cls._expire_locked(reminder)
                return False
            byok = UserBYOKConfig.objects.filter(user=reminder.user, is_active=True).first()
            attempt = ReminderDeliveryAttempt.objects.create(
                reminder_key=reminder_id,
                user=reminder.user,
                revision=reminder.revision,
                lease_until=now + DELIVERY_LEASE,
            )
            try:
                with transaction.atomic():
                    reservation = PaidFeature.reserve(
                        feature=BillingReservation.Feature.MESSAGE_PAIR,
                        operation_id=str(attempt.pk),
                        user=reminder.user,
                        operation_reference=f"reminder:{reminder_id}:attempt:{attempt.pk}",
                        cost_override=BYOK_MESSAGE_PAIR_COST if byok else None,
                    )
                    if reservation.status != BillingReservation.Status.RESERVED:
                        raise ReservationError("New reminder attempt did not reserve funds")
                    attempt.reservation = reservation
                    attempt.save(update_fields=["reservation", "updated_at"])
            except ReservationError as exc:
                cls._fail_locked(reminder, attempt, insufficient=isinstance(exc, InsufficientBalance), byok=byok)
                return False

        # No transaction or row lock spans this provider call.
        try:
            content = cls._generate_delivery_text(reminder, byok, _lang_key(reminder.conversation))
        except Exception:
            logger.exception("Reminder delivery generation failed for %s", reminder_id)
            content = ""

        with transaction.atomic():
            CustomUser.objects.select_for_update().filter(pk=reminder.user_id).first()
            current = Reminder.objects.select_for_update().filter(pk=reminder_id).first()
            attempt = ReminderDeliveryAttempt.objects.select_for_update().get(pk=attempt.pk)
            now = django_timezone.now()
            if (
                current is None
                or current.status != Reminder.StatusChoices.PENDING
                or current.revision != attempt.revision
                or attempt.status != ReminderDeliveryAttempt.Status.ACTIVE
                or not account_can_generate(current.user)
            ):
                cls._queue_refund_locked(attempt)
                return False
            if cls._expired(current, now):
                cls._expire_locked(current)
                return False
            if attempt.lease_until <= now:
                cls._queue_refund_locked(attempt)
                return False
            if not content:
                cls._fail_locked(current, attempt)
                return False
            conversation = current.conversation
            ai_message = Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.AI,
                content=content,
                status=Message.StatusChoices.SENT,
                client_operation_id=str(attempt.pk),
                metadata={"reminder": True, "reminder_id": str(reminder_id)},
            )
            conversation.last_message_at = ai_message.created_at
            conversation.save(update_fields=["last_message_at", "updated_at"])
            PaidFeature.settle(attempt.reservation, reason="reminder_delivered")
            attempt.status = ReminderDeliveryAttempt.Status.DELIVERED
            attempt.save(update_fields=["status", "updated_at"])
            current.delete()
            transaction.on_commit(lambda: cls._broadcast_message(conversation, ai_message), robust=True)
        return True

    @staticmethod
    def _expired(reminder, now):
        return now >= reminder.due_at + timedelta(hours=REMINDER_EXPIRY_GRACE_HOURS)

    @classmethod
    def _fail_locked(cls, reminder, attempt, *, insufficient=False, byok=None):
        cls._queue_refund_locked(attempt)
        if cls._expired(reminder, django_timezone.now()):
            cls._expire_locked(reminder)
            return
        if not reminder.notice_sent:
            templates = INSUFFICIENT_SPARKS_TEMPLATES if insufficient else DELIVERY_FAILED_TEMPLATES
            cls._notice_locked(
                reminder,
                "retry",
                templates[_lang_key(reminder.conversation)].format(
                    reminder_text=reminder.reminder_text,
                    spark_cost=PaidFeature.cost(
                        BillingReservation.Feature.MESSAGE_PAIR,
                        cost_override=BYOK_MESSAGE_PAIR_COST if byok else None,
                    ),
                    grace_deadline=_local_deadline_str(reminder.due_at, reminder.timezone_snapshot),
                ),
                "reminder_insufficient_sparks" if insufficient else "reminder_delivery_failed",
            )
            reminder.notice_sent = True
        reminder.failure_reason = "insufficient sparks" if insufficient else "delivery attempt failed"
        reminder.save(update_fields=["notice_sent", "failure_reason", "updated_at"])

    @classmethod
    def invalidate_locked(cls, reminder):
        """Caller holds the schedule lock. Acknowledgment fences old workers.

        Refund failure cannot resurrect delivery: the durable refund-pending
        record survives schedule deletion and the next sweep retries cleanup.
        """
        for attempt in ReminderDeliveryAttempt.objects.select_for_update().filter(
            reminder_key=reminder.pk, status=ReminderDeliveryAttempt.Status.ACTIVE
        ):
            cls._queue_refund_locked(attempt)
        # Legacy holds have no attempt pointer. The scheduled recovery also
        # discovers them after deletion if this immediate cleanup fails.
        key = reminder.pk
        transaction.on_commit(lambda: cls._recover_legacy_safely(key), robust=True)

    @classmethod
    def _expire_locked(cls, reminder):
        cls.invalidate_locked(reminder)
        cls._notice_locked(
            reminder,
            "gave_up",
            GIVE_UP_TEMPLATES[_lang_key(reminder.conversation)].format(reminder_text=reminder.reminder_text),
            "reminder_gave_up",
        )
        reminder.delete()

    @classmethod
    def _queue_refund_locked(cls, attempt):
        if attempt.status == ReminderDeliveryAttempt.Status.ACTIVE:
            attempt.status = ReminderDeliveryAttempt.Status.REFUND_PENDING
            attempt.save(update_fields=["status", "updated_at"])
        if attempt.status == ReminderDeliveryAttempt.Status.REFUND_PENDING:
            key = attempt.pk
            transaction.on_commit(lambda: cls._recover_safely(key), robust=True)

    @staticmethod
    def _close_attempt_locked(attempt):
        if attempt.reservation_id:
            # Use the strict API: PaidFeature.refund intentionally swallows
            # reservation errors and therefore cannot prove cleanup succeeded.
            BillingReservationService.refund(attempt.reservation_id, reason="reminder_attempt_closed")
        attempt.status = ReminderDeliveryAttempt.Status.REFUNDED
        attempt.save(update_fields=["status", "updated_at"])

    @classmethod
    def _recover_attempt(cls, attempt_id):
        hint = ReminderDeliveryAttempt.objects.filter(pk=attempt_id).first()
        if hint is None:
            return
        with transaction.atomic():
            # Always owner -> schedule -> attempt -> reservation -> balance.
            CustomUser.objects.select_for_update().filter(pk=hint.user_id).first()
            reminder = Reminder.objects.select_for_update().filter(pk=hint.reminder_key).first()
            attempt = ReminderDeliveryAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.status not in (
                ReminderDeliveryAttempt.Status.ACTIVE,
                ReminderDeliveryAttempt.Status.REFUND_PENDING,
            ):
                return
            now = django_timezone.now()
            if (
                attempt.status == ReminderDeliveryAttempt.Status.ACTIVE
                and reminder is not None
                and reminder.status == Reminder.StatusChoices.PENDING
                and reminder.revision == attempt.revision
                and account_can_generate(reminder.user)
                and attempt.lease_until > now
                and not cls._expired(reminder, now)
            ):
                return
            cls._close_attempt_locked(attempt)

    @classmethod
    def _recover_safely(cls, attempt_id):
        try:
            cls._recover_attempt(attempt_id)
        except Exception:
            logger.exception("Reminder attempt %s remains recoverable", attempt_id)

    @staticmethod
    def _refund_legacy_locked(reminder_id):
        for reservation in BillingReservation.objects.filter(
            operation_reference=f"reminder:{reminder_id}", status=BillingReservation.Status.RESERVED
        ):
            BillingReservationService.refund(reservation, reason="legacy_reminder_attempt_recovered")

    @classmethod
    def _recover_legacy_safely(cls, reminder_id):
        try:
            with transaction.atomic():
                owner_id = (
                    BillingReservation.objects.filter(
                        operation_reference=f"reminder:{reminder_id}", status=BillingReservation.Status.RESERVED
                    )
                    .values_list("user_id", flat=True)
                    .first()
                )
                if owner_id:
                    CustomUser.objects.select_for_update().filter(pk=owner_id).first()
                Reminder.objects.select_for_update().filter(pk=reminder_id).first()
                cls._refund_legacy_locked(reminder_id)
        except Exception:
            logger.exception("Legacy reminder hold %s remains recoverable", reminder_id)

    @classmethod
    def recover_pending_attempts(cls):
        for attempt_id in (
            ReminderDeliveryAttempt.objects.filter(
                status__in=[ReminderDeliveryAttempt.Status.ACTIVE, ReminderDeliveryAttempt.Status.REFUND_PENDING]
            )
            .values_list("pk", flat=True)
            .iterator()
        ):
            try:
                cls._recover_attempt(attempt_id)
            except OperationalError:
                raise
            except Exception:
                # A single unreconciled hold must not prevent every unrelated
                # reminder from being recovered/delivered on this sweep.
                logger.exception("Reminder attempt %s remains recoverable", attempt_id)
        # Upgrade recovery for holds written before durable attempts existed,
        # including those whose schedule was already deleted by the old code.
        for reference in (
            BillingReservation.objects.filter(
                operation_reference__regex=r"^reminder:[0-9a-f-]{36}$",
                status=BillingReservation.Status.RESERVED,
            )
            .values_list("operation_reference", flat=True)
            .iterator()
        ):
            try:
                reminder_id = uuid.UUID(reference.split(":")[1])
            except ValueError:
                logger.warning("Ignoring malformed legacy reminder reservation reference")
                continue
            cls._recover_legacy_safely(reminder_id)

    @classmethod
    def _notice_locked(cls, reminder, kind, text, code):
        cls._send_free_system_notice(
            reminder.conversation,
            text,
            code,
            operation_id=f"reminder:{reminder.pk}:revision:{reminder.revision}:notice:{kind}",
            reminder_id=reminder.pk,
        )

    @classmethod
    def _broadcast_message(cls, conversation, message):
        cls._send_websocket_notification(conversation, message)
        try:
            PushService.send_new_message_push(conversation.user, conversation.character, conversation)
            unread_service.broadcast_unread(conversation)
        except Exception:
            logger.exception("Reminder push/unread broadcast failed for %s", message.pk)

    # ------------------------------------------------------------------

    @classmethod
    def _generate_delivery_text(cls, reminder: Reminder, byok, lang: str) -> str:
        character = reminder.character
        user = reminder.user
        conversation = reminder.conversation
        display_name = clip(user.get_display_name() or user.username, 80)

        try:
            tz = ZoneInfo(reminder.timezone_snapshot or DEFAULT_TIMEZONE)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        local_now = django_timezone.now().astimezone(tz).strftime("%A, %B %d, %Y at %I:%M %p")
        local_due = reminder.due_at.astimezone(tz).strftime("%A, %B %d, %Y at %I:%M %p")
        local_created = reminder.created_at.astimezone(tz).strftime("%A, %B %d, %Y at %I:%M %p")

        # Recent context, mirroring ProactiveChatService._build_context: this
        # is only used to make the delivery message feel like a natural
        # continuation of the conversation instead of a cold, generic ping.
        # Escape delimiter syntax at each leaf. The saved reminder is never
        # changed; its quoted text is context, not a trusted policy override.
        recent_messages = list(Message.objects.filter(conversation=conversation).order_by("-created_at")[:5])
        history_lines = []
        for msg in reversed(recent_messages):
            sender = display_name if msg.sender_type == Message.SenderType.USER else character.name
            history_lines.append((sender, msg.content))
        history = untrusted_history(history_lines) or "No prior conversation available."

        memory_facts = MemoryService.get_active_facts(user, character, limit=5, max_tokens=300)
        facts_text = untrusted_facts(fact["fact_text"] for fact in memory_facts) or "No specific facts recorded."

        language_instruction = (
            "Reply in Kiswahili." if lang == "sw" else "Reply in the same language the user set for this chat."
        )

        prompt = "\n".join(
            [
                f"You are {clip(character.name, 80)}, "
                f"a {clip(character.personality_description or 'friendly and warm', 2000)} "
                f"companion to {display_name}.",
                "",
                f"It is now {local_now} ({tz.key}) for {display_name}.",
                f"The saved notification schedule is {local_due} ({tz.key}). "
                f"The reminder was originally created on {local_created} ({tz.key}). "
                "This schedule is when to notify, not necessarily the time of the underlying event. "
                "Deliver now; do not say you will schedule or send this reminder later. "
                "Do not copy an old reminder time or relative phrase like 'tomorrow at 9' from chat history. "
                "The saved schedule supersedes earlier scheduling promises. Preserve any event deadlines "
                "explicitly present in the saved reminder wording, but do not invent an event time "
                "from the notification schedule. If relative wording in the saved text is ambiguous, "
                "quote it as the original request rather than presenting it as a new relative date.",
                f"Things you know about {display_name}:",
                facts_text,
                "",
                "Recent conversation, most recent last:",
                history,
                "",
                f"Earlier, {display_name} asked you to be reminded, in these exact words: "
                f'"{clip(reminder.reminder_text, REMINDER_TEXT_LIMIT)}". This wording describes '
                "what to remind them about -- do not paraphrase away specific details, names, "
                "times, or numbers in it.",
                "",
                "Task: deliver that reminder now, in character, as a short natural message "
                "(1-2 sentences) that reads like a continuation of the recent conversation above "
                "where relevant. Clearly convey what they asked to be reminded about, keeping the "
                "exact details from the reminder wording intact. Do not mention that you are an "
                "AI, Sparks, or that this is an automated message.",
                language_instruction,
                "",
                "Write only the message content, no prefixes or quotes.",
            ]
        )

        messages = build_background_messages(prompt, character)
        if byok:
            response = BYOKService.chat_completion(
                byok_config=byok,
                messages=messages,
                max_tokens=150,
                temperature=0.8,
            )
        else:
            response = CortecsClient.chat_completion(
                messages=messages,
                max_tokens=150,
                temperature=0.8,
            )

        choices = response.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "").strip()

    # ------------------------------------------------------------------

    @classmethod
    def _send_free_system_notice(cls, conversation, text: str, code: str, *, operation_id, reminder_id) -> None:
        """Persist before marking sent; offline clients get the notice in history."""
        message, created = Message.objects.get_or_create(
            conversation=conversation,
            client_operation_id=operation_id,
            defaults={
                "sender_type": Message.SenderType.SYSTEM,
                "content": text,
                "status": Message.StatusChoices.SENT,
                "metadata": {"code": code, "reminder_notice": True, "reminder_id": str(reminder_id)},
            },
        )
        if created:
            conversation.last_message_at = message.created_at
            conversation.save(update_fields=["last_message_at", "updated_at"])
            transaction.on_commit(lambda: cls._broadcast_message(conversation, message), robust=True)

    @staticmethod
    def _send_websocket_notification(conversation, message: Message) -> None:
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"conversation_{conversation.conversation_id}",
                {
                    "type": "chat.message",
                    "message": {
                        "message_id": str(message.message_id),
                        "sender_type": message.sender_type,
                        "content": message.content,
                        "created_at": message.created_at.isoformat(),
                        "reply_to": None,
                        "metadata": message.metadata,
                        "code": message.metadata.get("code"),
                    },
                },
            )
        except Exception:
            logger.exception("Failed to send WebSocket notification for reminder")
