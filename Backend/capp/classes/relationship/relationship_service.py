# capp/classes/relationship/relationship_service.py

import json
import logging
import re
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from capp.classes.ai.mistral_client import MistralClient
from capp.models import (
    Character,
    Conversation,
    CustomUser,
    Message,
    RelationshipState,
)

logger = logging.getLogger(__name__)

# Number of recent Mistral/Cortecs "note" strings kept in relationship_notes.
# Small enough that the prompt block stays well under ~120 tokens.
RELATIONSHIP_NOTES_MAX = 5
# Trust only nudges up when the affinity change is meaningfully positive.
# Casual smalltalk (+1/+2) should build affinity but not trust.
TRUST_MIN_ABS_AFFINITY = 3


class RelationshipService:

    # ---------- Retrieval ----------

    @classmethod
    def get_relationship(cls, user: Optional[CustomUser], character: Character) -> dict:
        """
        Return a dictionary with current relationship state for prompt assembly.

        Guests (``user is None``) do not have persisted relationship state — the
        prompt builder already skips this call for them, but we defend in depth
        by returning defaults instead of creating a NULL-user row.
        """
        if user is None:
            return cls._default_relationship_dict()

        state, _ = RelationshipState.objects.get_or_create(
            user=user,
            character=character,
            defaults={
                "familiarity_score": 0,
                "affinity_score": 0,
                "trust_score": 0,
                "interaction_count": 0,
                "tone_state": RelationshipState.ToneChoices.NEUTRAL,
            },
        )

        return {
            "tone_state": state.tone_state,
            "affinity_score": state.affinity_score,
            "familiarity_score": state.familiarity_score,
            "trust_score": state.trust_score,
            "interaction_count": state.interaction_count,
            "relationship_notes": state.relationship_notes or "",
        }

    @staticmethod
    def _default_relationship_dict() -> dict:
        return {
            "tone_state": RelationshipState.ToneChoices.NEUTRAL,
            "affinity_score": 0,
            "familiarity_score": 0,
            "trust_score": 0,
            "interaction_count": 0,
            "relationship_notes": "",
        }

    # ---------- Update ----------

    @classmethod
    def update_relationship_state(
        cls,
        conversation: Conversation,
        user_message: Message,
        ai_message: Message,
    ):
        """
        Analyse the user message and AI context, then update the relationship scores.
        Called from the ``update_relationship`` Celery task after every AI reply
        for authenticated users. Guests are short-circuited at the task layer,
        but we defend in depth here.

        Idempotent per AI message: repeated calls with the same
        ``ai_message.message_id`` are skipped so a Celery retry (or any
        future ``autoretry_for`` promotion) cannot double-count.
        """
        try:
            if not conversation.user:
                # Guest – skip relationship updates (defence in depth).
                return

            user = conversation.user
            character = conversation.character

            # ---- Idempotency short-circuit -----------------------------------
            existing = RelationshipState.objects.filter(user=user, character=character).first()
            if existing and str(existing.last_processed_ai_message_id) == str(ai_message.message_id):
                logger.debug(
                    "Skipping relationship update for user=%s character=%s: ai_message=%s already processed",
                    user.user_id,
                    character.character_id,
                    ai_message.message_id,
                )
                return

            # ---- Build analysis prompt ---------------------------------------
            previous_ai_message = (
                Message.objects.filter(
                    conversation=conversation,
                    sender_type=Message.SenderType.AI,
                    created_at__lt=ai_message.created_at,
                )
                .order_by("-created_at")
                .first()
            )

            context_prompt = ""
            if previous_ai_message:
                context_prompt = f"Character's previous message: {previous_ai_message.content}\n\n"

            prompt = (
                "Analyse the user's message towards the character. "
                "Determine the sentiment and suggest changes.\n"
                "Output a JSON object with:\n"
                '- "affinity_change": integer between -5 and 5 '
                "(positive if user shows warmth, liking, or affection; "
                "negative if anger, dislike, or coldness).\n"
                '- "tone_state": the suggested new tone for the character '
                "(neutral, warm, playful, flirty, distant).\n"
                '- "note": short explanation (max 20 words).\n\n'
                f"{context_prompt}"
                f"User message: {user_message.content}\n"
            )

            content = cls._analysis_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.2,
            )
            if content is None:
                logger.warning("Relationship analysis returned no content for conv=%s", conversation.conversation_id)
                return

            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            try:
                analysis = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Failed to parse relationship analysis JSON: %s", cleaned[:200])
                return

            affinity_change = int(analysis.get("affinity_change", 0))
            # Clamp any runaway model output to the promised range.
            affinity_change = max(-5, min(5, affinity_change))

            suggested_tone = analysis.get("tone_state", RelationshipState.ToneChoices.NEUTRAL)
            valid_tones = [choice[0] for choice in RelationshipState.ToneChoices.choices]
            if suggested_tone not in valid_tones:
                suggested_tone = RelationshipState.ToneChoices.NEUTRAL

            note = (analysis.get("note") or "").strip()

            # ---- Persist ------------------------------------------------------
            with transaction.atomic():
                state, _ = RelationshipState.objects.select_for_update().get_or_create(
                    user=user,
                    character=character,
                    defaults={
                        "familiarity_score": 0,
                        "affinity_score": 0,
                        "trust_score": 0,
                        "interaction_count": 0,
                        "tone_state": RelationshipState.ToneChoices.NEUTRAL,
                    },
                )

                # Re-check inside the transaction — another worker may have
                # processed this exact ai_message between our earlier read and
                # this SELECT FOR UPDATE.
                if str(state.last_processed_ai_message_id) == str(ai_message.message_id):
                    return

                # Update scalar scores.
                state.affinity_score = max(-100, min(100, state.affinity_score + affinity_change))
                state.interaction_count += 1
                state.familiarity_score = min(100, state.interaction_count // 5)

                # Trust moves slower than affinity: only nudge on meaningful
                # positive signals, and decay on any negative signal.
                if affinity_change >= TRUST_MIN_ABS_AFFINITY:
                    state.trust_score = min(100, state.trust_score + 1)
                elif affinity_change < 0:
                    state.trust_score = max(0, state.trust_score - 1)

                if suggested_tone and suggested_tone != state.tone_state:
                    state.tone_state = suggested_tone

                # Append the note to a rolling window kept in relationship_notes.
                if note:
                    state.relationship_notes = cls._append_note(state.relationship_notes, note)

                state.last_interaction_at = timezone.now()
                state.last_processed_ai_message_id = ai_message.message_id
                state.save()

            logger.info(
                "Updated relationship for user %s, character %s: affinity=%d trust=%d tone=%s",
                user.user_id,
                character.character_id,
                state.affinity_score,
                state.trust_score,
                state.tone_state,
            )

        except Exception:
            logger.exception(
                "Relationship update failed for conversation %s",
                conversation.conversation_id,
            )

    # ---------- Helpers ----------

    @staticmethod
    def _append_note(existing_notes: Optional[str], new_note: str) -> str:
        """
        Maintain a rolling window of the most recent Mistral/Cortecs notes,
        newest first, one per line. Truncates each note to keep the block
        small in the eventual prompt.
        """
        new_note = new_note.strip().replace("\n", " ")
        if len(new_note) > 160:
            new_note = new_note[:157].rstrip() + "..."
        lines = [ln for ln in (existing_notes or "").splitlines() if ln.strip()]
        lines.insert(0, new_note)
        return "\n".join(lines[:RELATIONSHIP_NOTES_MAX])

    @classmethod
    def _analysis_chat_completion(
        cls,
        messages,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        """
        Route the relationship-analysis LLM call through Cortecs first, then
        fall back to Mistral on any error — mirrors the memory-extraction
        router in ``MemoryService._extraction_chat_completion``.

        We reuse ``CORTECS_MEM_MODEL`` (default ``deepseek-v4-flash-0731``)
        for the same reason we use it for memory extraction: better prompt
        adherence and structured-JSON adherence than the chat-tier model.
        """
        cortecs_api_key = getattr(settings, "CORTECS_API_KEY", None)
        cortecs_base_url = getattr(settings, "CORTECS_BASE_URL", "https://api.cortecs.ai/v1")
        cortecs_mem_model = getattr(settings, "CORTECS_MEM_MODEL", "deepseek-v4-flash-0731")

        if cortecs_api_key:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=cortecs_api_key, base_url=cortecs_base_url)
                response = client.chat.completions.create(
                    model=cortecs_mem_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                content = response.choices[0].message.content if response.choices else None
                if content:
                    return content
                logger.warning(
                    "Cortecs relationship analysis returned empty content (model=%s) — falling back to Mistral",
                    cortecs_mem_model,
                )
            except Exception as e:
                logger.warning(
                    "Cortecs relationship analysis failed (model=%s): %s — falling back to Mistral",
                    cortecs_mem_model,
                    e,
                )

        # ---------- Mistral fallback ----------
        try:
            response = MistralClient.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("Mistral relationship analysis fallback failed: %s", e)
            return None
