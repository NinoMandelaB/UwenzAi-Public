# capp/classes/chat/proactive_chat_service.py
import logging
import uuid
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from capp.models import (
    BillingReservation,
    ProactiveChatPreference,
    Conversation,
    Message,
    CustomUser,
    Character,
    MemoryFact,
)
from capp.classes.ai.cortecs_client import CortecsClient
from capp.classes.billing.paid_features import (
    InsufficientBalance,
    PaidFeature,
    ReservationError,
)
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# Fixed namespace so the derived operation ID is stable across workers and
# restarts: the same preference following the same message is the same send.
PROACTIVE_NAMESPACE = uuid.UUID("4a8c9c2e-0f1b-4e9a-9f2c-2b1e6a7d5c30")


class ProactiveChatService:
    """
    Service for handling proactive (AI-initiated) chat messages.
    """

    MAX_PROACTIVE_WITHOUT_REPLY = 4

    # ─────────────────────────────────────────────────────────────
    # Preference CRUD
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def get_or_create_preference(cls, user: CustomUser, character: Character) -> ProactiveChatPreference:
        """Get or create a proactive chat preference for a user-character pair."""
        pref, _ = ProactiveChatPreference.objects.get_or_create(
            user=user,
            character=character,
            defaults={
                "enabled": False,
                "interval_hours": 24,
                "consecutive_proactive_without_reply": 0,
                "last_proactive_sent_at": None,
            },
        )
        return pref

    @classmethod
    def update_preference(
        cls,
        user: CustomUser,
        character: Character,
        enabled: bool,
        interval_hours: int = None,
    ) -> ProactiveChatPreference:
        """Update a proactive chat preference."""
        pref = cls.get_or_create_preference(user, character)
        pref.enabled = enabled
        if interval_hours is not None:
            pref.interval_hours = max(6, min(168, interval_hours))
        pref.save(update_fields=["enabled", "interval_hours", "updated_at"])
        return pref

    @classmethod
    def reset_counter(cls, user: CustomUser, character: Character) -> None:
        """Reset the consecutive proactive counter when user replies."""
        pref = cls.get_or_create_preference(user, character)
        if pref.consecutive_proactive_without_reply > 0:
            pref.consecutive_proactive_without_reply = 0
            pref.save(update_fields=["consecutive_proactive_without_reply", "updated_at"])
            logger.debug("Reset proactive counter for %s - %s", user.username, character.name)

    # ─────────────────────────────────────────────────────────────
    # Main entry point for Celery task
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def check_and_send_proactive_messages(cls) -> int:
        """
        Check all enabled preferences and send proactive messages if due.
        Called by Celery Beat every 30 minutes.
        Returns the number of messages sent.
        """
        now = timezone.now()
        preferences = ProactiveChatPreference.objects.filter(enabled=True).select_related("user", "character")
        sent_count = 0

        for pref in preferences:
            try:
                if cls._should_send_proactive(pref, now):
                    cls._send_proactive_message(pref)
                    sent_count += 1
            except Exception as e:
                logger.exception(
                    "Error checking proactive for pref %s (user=%s, char=%s): %s",
                    pref.id,
                    pref.user.username,
                    pref.character.name,
                    e,
                )

        logger.info("Proactive chat check completed. Sent %d messages.", sent_count)
        return sent_count

    # ─────────────────────────────────────────────────────────────
    # Condition checks
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def _should_send_proactive(cls, pref: ProactiveChatPreference, now: timezone.datetime) -> bool:
        """
        Check all conditions for sending a proactive message:
        - Enabled
        - User has at least one prior conversation with this character
        - Interval has elapsed since last message
        - Not exceeded max consecutive without reply
        - Sparks available
        """
        user = pref.user
        character = pref.character

        # 1. Check if there is at least one prior conversation
        conversation = Conversation.objects.filter(user=user, character=character).order_by("-last_message_at").first()

        if not conversation:
            logger.debug(
                "No prior conversation for %s - %s, skipping",
                user.username,
                character.name,
            )
            return False

        # 2. Check if interval has elapsed since last message
        last_msg = Message.objects.filter(conversation=conversation).order_by("-created_at").first()

        if not last_msg:
            # Should not happen if conversation exists, but just in case
            return False

        time_since_last = now - last_msg.created_at
        if time_since_last.total_seconds() < pref.interval_hours * 3600:
            logger.debug(
                "Interval not elapsed for %s - %s: %d hours remaining",
                user.username,
                character.name,
                int((pref.interval_hours * 3600 - time_since_last.total_seconds()) / 3600),
            )
            return False

        # 3. Check max consecutive without reply
        if pref.consecutive_proactive_without_reply >= cls.MAX_PROACTIVE_WITHOUT_REPLY:
            logger.debug(
                "Max consecutive proactive reached (%d) for %s - %s",
                pref.consecutive_proactive_without_reply,
                user.username,
                character.name,
            )
            return False

        # 4. Sparks are not checked here. The send path reserves them, which is
        # the only check that cannot go stale between deciding and spending.

        return True

    # ─────────────────────────────────────────────────────────────
    # Sending proactive message
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def _send_proactive_message(cls, pref: ProactiveChatPreference) -> None:
        """Generate and send a proactive message."""
        reservation = None
        try:
            user = pref.user
            character = pref.character

            # Get the most recent conversation
            conversation = (
                Conversation.objects.filter(user=user, character=character).order_by("-last_message_at").first()
            )

            if not conversation:
                # Should not happen because _should_send_proactive checked this
                logger.warning("No conversation found for %s - %s", user.username, character.name)
                return

            # A proactive send is identified by the message it follows, so a
            # redelivered beat task in the same window reuses the reservation
            # instead of paying for a second uninvited message.
            last_msg = Message.objects.filter(conversation=conversation).order_by("-created_at").first()
            operation_id = str(uuid.uuid5(PROACTIVE_NAMESPACE, f"{pref.pk}:{last_msg.pk if last_msg else 'none'}"))

            existing = Message.objects.filter(conversation=conversation, client_operation_id=operation_id).first()
            if existing is not None:
                logger.info("Proactive message %s already sent for pref %s", existing.message_id, pref.pk)
                return

            try:
                reservation = PaidFeature.reserve(
                    feature=BillingReservation.Feature.MESSAGE_PAIR,
                    operation_id=operation_id,
                    user=user,
                    operation_reference=f"proactive:{conversation.conversation_id}:{operation_id}",
                )
            except InsufficientBalance:
                logger.debug("Skipping proactive message for %s: insufficient sparks", user.username)
                return
            except ReservationError:
                logger.exception("Could not reserve sparks for proactive message")
                return

            if reservation.status != BillingReservation.Status.RESERVED:
                logger.info("Proactive operation %s already settled or refunded", operation_id)
                return

            # Build context
            context = cls._build_context(conversation, character, user)

            # Build prompt with NSFW instructions
            prompt = cls._build_prompt(context, character)

            # Generate message
            response = CortecsClient.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.8,
            )

            choices = response.get("choices", [])
            if not choices:
                logger.warning("No AI response for proactive message for %s", user.user_id)
                PaidFeature.refund(reservation, reason="provider_returned_no_choices")
                return

            content = choices[0].get("message", {}).get("content", "").strip()
            if not content:
                logger.warning("Empty AI response for proactive message for %s", user.user_id)
                PaidFeature.refund(reservation, reason="provider_returned_empty")
                return

            # Optional: Run through moderation (if service exists)
            # For now, we assume it's safe

            # Create the AI message
            with transaction.atomic():
                ai_message = Message.objects.create(
                    conversation=conversation,
                    sender_type=Message.SenderType.AI,
                    content=content,
                    status=Message.StatusChoices.SENT,
                    client_operation_id=operation_id,
                    metadata={"proactive": True, "reservation_id": str(reservation.reservation_id)},
                )

                conversation.last_message_at = ai_message.created_at
                conversation.save(update_fields=["last_message_at", "updated_at"])

                # Update preference
                pref.last_proactive_sent_at = timezone.now()
                pref.consecutive_proactive_without_reply += 1
                pref.save(
                    update_fields=[
                        "last_proactive_sent_at",
                        "consecutive_proactive_without_reply",
                        "updated_at",
                    ]
                )

                PaidFeature.settle(reservation, reason="proactive_message_persisted")

            # Delivery is outside the transaction: it is a network write, and a
            # failed push does not undo a message the user can still reload.
            try:
                cls._send_websocket_notification(conversation, ai_message)
            except Exception:
                logger.exception("Proactive broadcast failed for message %s", ai_message.message_id)

            logger.info(
                "Proactive message sent to %s from %s (counter: %d/%d)",
                user.username,
                character.name,
                pref.consecutive_proactive_without_reply,
                cls.MAX_PROACTIVE_WITHOUT_REPLY,
            )

        except Exception:
            logger.exception("Failed to send proactive message for pref %s", pref.id)
            if reservation is not None:
                PaidFeature.refund(reservation, reason="unhandled_error")

    # ─────────────────────────────────────────────────────────────
    # Context and prompt building
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def _build_context(cls, conversation: Conversation, character: Character, user: CustomUser) -> dict:
        """Build context for proactive message."""
        # Get last 5 messages
        recent_messages = Message.objects.filter(conversation=conversation).order_by("-created_at")[:5]

        history_lines = []
        for msg in reversed(recent_messages):
            sender = "You" if msg.sender_type == Message.SenderType.USER else character.name
            history_lines.append(f"{sender}: {msg.content}")
        history = "\n".join(history_lines) if history_lines else "No prior conversation."

        # Get memory facts (top 5 by importance, limited to this character or general)
        memory_facts = MemoryFact.objects.filter(
            Q(user=user, character=character) | Q(user=user, character__isnull=True),
            is_active=True,
        ).order_by("-importance_score")[:5]

        facts_text = "\n".join([f"- {f.fact_text}" for f in memory_facts]) if memory_facts else "No specific facts."

        return {
            "character_name": character.name,
            "character_personality": character.personality_description or "friendly and warm",
            "character_relationship": character.relationship_style or "companion",
            "user_name": user.get_display_name() or user.username,
            "current_time": timezone.now().strftime("%A, %B %d, %Y at %I:%M %p"),
            "history": history,
            "memory_facts": facts_text,
        }

    @classmethod
    def _build_prompt(cls, context: dict, character: Character) -> str:
        """Build the AI prompt for proactive message with NSFW instruction."""
        nsfw_instruction = ""
        if character.nsfw_enabled:
            nsfw_instruction = (
                "The character may be flirtatious, romantic, or explict depending on previous conversation. "
                "avoid illegal content."
            )
        else:
            nsfw_instruction = (
                "Keep the message completely clean, friendly, and appropriate for all audiences. "
                "Do not include any romantic or suggestive content."
            )

        prompt_lines = [
            (
                f"You are {context['character_name']}, a {context['character_personality']} "
                f"{context['character_relationship']} to the user."
            ),
            "",
            f"User's name: {context['user_name']}",
            f"Current time: {context['current_time']}",
            "",
            f"Important facts I know about {context['user_name']}:",
            f"{context['memory_facts']}",
            "",
            "Last few messages:",
            f"{context['history']}",
            "",
            nsfw_instruction,
            "",
            "Task: Write a short, natural message (1-2 sentences) to re-engage the user.",
            "Your message should:",
            "- Be warm and consistent with your personality.",
            "- Reference one of the memory facts naturally (weave it into the conversation).",
            "- Ask a gentle, open-ended question to invite a reply.",
            "- Avoid sounding robotic or pushy.",
            '- Do not mention that you are an AI, or that this is a "proactive" message.',
            "",
            "Write only the message content, no prefixes or quotes.",
        ]
        return "\n".join(prompt_lines)

    # ─────────────────────────────────────────────────────────────
    # WebSocket helper
    # ─────────────────────────────────────────────────────────────

    @classmethod
    def _send_websocket_notification(cls, conversation: Conversation, message: Message) -> None:
        """Send WebSocket notification for the new message."""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"conversation_{conversation.conversation_id}",
                {
                    "type": "chat.message",
                    "message": {
                        "message_id": str(message.message_id),
                        "sender_type": "ai",
                        "content": message.content,
                        "created_at": message.created_at.isoformat(),
                        "reply_to": None,
                    },
                },
            )
            logger.debug(
                "WebSocket notification sent for proactive message %s",
                message.message_id,
            )
        except Exception:
            logger.exception("Failed to send WebSocket notification")
