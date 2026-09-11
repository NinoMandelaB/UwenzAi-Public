# capp/classes/summary/summary_service.py
#
# KEY CHANGE FROM PREVIOUS VERSION
# ─────────────────────────────────
# Summaries are now stored and retrieved by (user + character), NOT by conversation.
# This means when a user starts a new conversation with the same character the next
# day, the summary from previous sessions is still found and injected into the prompt.
#
# The ConversationSummary model still has a FK to conversation (for traceability),
# but all lookups now go through conversation.user + conversation.character so they
# span across conversations.
#
# Summary trigger threshold is also lowered from 20 → 6 messages so summaries
# actually get generated within a normal session length.

import logging
from typing import Optional

from django.db.models import Max
from django.utils import timezone

from capp.models import (
    ConversationSummary,
    Conversation,
    Message,
)
from capp.classes.ai.mistral_client import MistralClient
from capp.classes.memory.mutation_guard import memory_write_lock, generation, advance_summary

logger = logging.getLogger(__name__)

# How many new messages must accumulate before a new summary is triggered.
# Previous value was 20 — too high for a typical session of 10-15 messages.
SUMMARY_TRIGGER_MESSAGES = 6

# Maximum number of recent messages to include in the summary prompt.
# Prevents token overflow on very long conversations.
SUMMARY_MAX_MESSAGES = 60


class ConversationSummaryService:

    # ---------- Trigger Check ----------

    @classmethod
    def maybe_generate_summary(
        cls,
        conversation: Conversation,
        min_messages_since_last: int = SUMMARY_TRIGGER_MESSAGES,
    ):
        """
        Generate a new summary if enough new messages have accumulated since the
        last summary for this user+character pair.

        Falls back gracefully if the conversation has no user (guest — no summary
        generated for guests, but conversations are migrated to users on signup).
        """
        # Guests: no summary — their conversations will be migrated to a user on signup
        if conversation.user is None:
            return

        latest_summary = cls.get_latest_summary(conversation)
        cutoff = cls._source_cutoff(conversation)
        if latest_summary and latest_summary.covered_until_message:
            checkpoint = latest_summary.covered_until_message.created_at
            cutoff = max(cutoff, checkpoint) if cutoff else checkpoint
        if cutoff:
            newer_count = Message.objects.filter(
                conversation__user=conversation.user,
                conversation__character=conversation.character,
                created_at__gt=cutoff,
            ).count()
        else:
            # No summary yet — count all messages across all conversations for this pair
            newer_count = Message.objects.filter(
                conversation__user=conversation.user,
                conversation__character=conversation.character,
            ).count()

        logger.debug(
            "Summary check: user=%s character=%s newer_count=%d threshold=%d",
            conversation.user.user_id,
            conversation.character.character_id,
            newer_count,
            min_messages_since_last,
        )

        if newer_count >= min_messages_since_last:
            cls.generate_summary(conversation)

    # ---------- Generation ----------

    @classmethod
    def generate_summary(cls, conversation: Conversation):
        """
        Summarise the full conversation history across ALL conversations between
        this user+character pair, and store the result linked to the current
        conversation (most recent) but queryable by user+character.

        The previous summary (if any) is passed as context so the new one
        builds on it rather than starting from scratch.
        """
        if conversation.user is None:
            # Should not be called for guests, but guard anyway
            return

        try:
            expected_generation = generation(conversation, "summary_generation")
            if expected_generation is None:
                return
            cutoff = cls._source_cutoff(conversation)
            # Gather all messages across all conversations for this user+character pair,
            # ordered chronologically so the summary covers the full relationship history
            messages = (
                Message.objects.filter(
                    conversation__user=conversation.user,
                    conversation__character=conversation.character,
                )
                .order_by("created_at", "message_id")
                .select_related("conversation")
            )
            if cutoff:
                messages = messages.filter(created_at__gt=cutoff)

            # One bounded read, then restore chronology. A separate count and
            # open-ended offset slice can exceed the limit if messages arrive
            # between those two queries.
            messages = list(messages.order_by("-created_at", "-message_id")[:SUMMARY_MAX_MESSAGES])
            messages.reverse()
            if not messages:
                return

            # Build conversation text
            conv_lines = []
            for msg in messages:
                role = "User" if msg.sender_type == Message.SenderType.USER else "Character"
                conv_lines.append(f"{role}: {msg.content}")

            conv_text = "\n".join(conv_lines)

            # Include previous summary for continuity
            prev_summary_text = ""
            latest_summary = cls.get_latest_summary(conversation)
            if latest_summary:
                prev_summary_text = (  # noqa: E231 (colon in string literal)
                    f"Existing summary (build on this, don't repeat it):\n"  # noqa: E231
                    f"{latest_summary.summary_text[:12000]}\n\n"  # noqa: E231
                )

            prompt = (
                "Summarise the following conversation between a user and an AI character.\n"
                "Focus on:\n"
                "- Key topics discussed and decisions made\n"
                "- The emotional tone and relationship dynamic\n"
                "- Personal information the user shared about themselves\n"
                "- Memorable moments, stories, or things the character said or did\n"
                "- Any plans, intentions, or promises mentioned\n"
                "Keep the summary concise (max 300 tokens). "
                "Write it from the character's perspective as a memory aide.\n\n"
                f"{prev_summary_text}"
                "Conversation:\n"
                f"{conv_text}"
            )

            response = MistralClient.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.3,
            )

            summary_text = response["choices"][0]["message"]["content"].strip()

            if not summary_text:
                return
            with memory_write_lock(conversation.user):
                if generation(conversation, "summary_generation") != expected_generation:
                    logger.info("Discarded stale summary generation for conversation %s", conversation.pk)
                    return
                # Deactivate all previous summaries for this user+character pair
                # (not just this conversation — the old per-conversation scope)
                ConversationSummary.objects.filter(
                    conversation__user=conversation.user,
                    conversation__character=conversation.character,
                    is_current=True,
                ).update(is_current=False)

                # Create new summary linked to the current (most recent) conversation
                ConversationSummary.objects.create(
                    conversation=conversation,
                    summary_text=summary_text,
                    summary_type=ConversationSummary.SummaryType.ROLLING,
                    covered_until_message=messages[-1],
                    is_current=True,
                )
                advance_summary(conversation.user, conversation.character, manual=False)
                Conversation.objects.filter(pk=conversation.pk).update(last_summarized_at=timezone.now())

            logger.info(
                "Generated cross-session summary for user=%s character=%s",
                conversation.user.user_id,
                conversation.character.character_id,
            )

        except Exception:
            logger.exception(
                "Summary generation failed for conversation %s",
                conversation.conversation_id,
            )

    # ---------- Retrieval ----------

    @classmethod
    def _source_cutoff(cls, conversation):
        # Newly created conversations still honor a tombstone on older ones.
        return Conversation.objects.filter(user=conversation.user, character=conversation.character).aggregate(
            cutoff=Max("summary_source_cutoff")
        )["cutoff"]

    @classmethod
    def get_latest_summary(cls, conversation: Conversation) -> Optional[ConversationSummary]:
        """
        Retrieve the most recent active summary for this user+character pair.

        This crosses conversation boundaries — a summary from a previous
        conversation is returned here, which is exactly what we want for
        cross-session memory continuity.

        Falls back to conversation-scoped lookup for guest conversations
        (no user attached).
        """
        if conversation.user is not None:
            # Cross-conversation lookup: find the latest summary for this user+character
            # regardless of which conversation it was generated in
            return (
                ConversationSummary.objects.filter(
                    conversation__user=conversation.user,
                    conversation__character=conversation.character,
                    is_current=True,
                )
                .order_by("-created_at")
                .first()
            )
        else:
            # Guest: fall back to this conversation only
            return ConversationSummary.objects.filter(
                conversation=conversation,
                is_current=True,
            ).first()

    # ---------- Retrospective summary generation (guest → user migration) ----------

    @classmethod
    def generate_summaries_for_claimed_conversations(cls, user, character=None):
        """
        Called after a guest account is converted to a registered user.
        Generates memory facts and a summary for all conversations that were
        just transferred from the guest session to the user.

        This is called after GuestClaimService has committed a claim, once the
        conversations have been re-assigned to the new user.

        Args:
            user:      The newly registered CustomUser.
            character: Optional — limit to a specific character. If None,
                       processes all characters the user has conversations with.
        """
        from capp.classes.memory.memory_service import MemoryService

        # Find all conversations now owned by this user that have no summary yet
        qs = Conversation.objects.filter(user=user)
        if character:
            qs = qs.filter(character=character)

        # Group by character — we want one summary per user+character pair
        character_ids_done = set()

        for conv in qs.select_related("character").order_by("started_at"):
            char = conv.character

            # Retrospectively extract memory facts from each conversation
            try:
                messages = list(Message.objects.filter(conversation=conv).order_by("created_at"))
                if len(messages) >= 2:
                    # Use last user+AI message pair as trigger for extraction
                    user_msgs = [m for m in messages if m.sender_type == Message.SenderType.USER]
                    ai_msgs = [m for m in messages if m.sender_type == Message.SenderType.AI]
                    if user_msgs and ai_msgs:
                        MemoryService.extract_and_save_facts(
                            conv,
                            user_msgs[-1],
                            ai_msgs[-1],
                            context_message_count=len(messages),
                        )
                        logger.info(
                            "Retrospective memory extraction done for conv=%s user=%s",
                            conv.conversation_id,
                            user.user_id,
                        )
            except Exception:
                logger.exception(
                    "Retrospective memory extraction failed for conv=%s",
                    conv.conversation_id,
                )

            # Generate one cross-session summary per character (only once per char)
            if char.character_id not in character_ids_done:
                try:
                    cls.generate_summary(conv)
                    character_ids_done.add(char.character_id)
                    logger.info(
                        "Retrospective summary generated for user=%s character=%s",
                        user.user_id,
                        char.character_id,
                    )
                except Exception:
                    logger.exception("Retrospective summary failed for conv=%s", conv.conversation_id)
