# capp/classes/relationship/relationship_service.py

import logging
import json
import re

from django.db import transaction
from django.utils import timezone

from capp.models import (
    RelationshipState,
    Conversation,
    Message,
    CustomUser,
    Character,
)
from capp.classes.ai.mistral_client import MistralClient

logger = logging.getLogger(__name__)


class RelationshipService:

    # ---------- Retrieval ----------

    @classmethod
    def get_relationship(cls, user: CustomUser, character: Character) -> dict:
        """
        Return a dictionary with current relationship state for prompt assembly.
        If no state exists, return defaults.
        """
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
        Should be called from a Celery task.
        """
        try:

            if not conversation.user:
                # Guest – skip relationship updates
                return

            user = conversation.user
            character = conversation.character

            # Get previous AI message for context
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
                context_prompt = f"Character's previous message: {previous_ai_message.content}\n"

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
                '- "note": short explanation.\n\n'
                f"{context_prompt}"
                f"User message: {user_message.content}\n"
            )

            response = MistralClient.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.2,
            )

            content = response["choices"][0]["message"]["content"]

            # --- Clean potential markdown fences ---
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
            suggested_tone = analysis.get("tone_state", RelationshipState.ToneChoices.NEUTRAL)
            # validate tone
            valid_tones = [choice[0] for choice in RelationshipState.ToneChoices.choices]
            if suggested_tone not in valid_tones:
                suggested_tone = RelationshipState.ToneChoices.NEUTRAL

            with transaction.atomic():
                state, created = RelationshipState.objects.get_or_create(
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

                # Update scores
                state.affinity_score = max(-100, min(100, state.affinity_score + affinity_change))
                state.interaction_count += 1
                # Familiarity grows slowly with interactions
                state.familiarity_score = min(100, state.interaction_count // 5)
                # Trust can be nudged by positive affinity
                if affinity_change > 0:
                    state.trust_score = min(100, state.trust_score + 1)
                elif affinity_change < 0:
                    state.trust_score = max(0, state.trust_score - 1)

                # Update tone if AI suggested a different one
                if suggested_tone and suggested_tone != state.tone_state:
                    state.tone_state = suggested_tone

                state.last_interaction_at = timezone.now()
                state.save()

            logger.info(
                "Updated relationship for user %s, character %s: affinity=%d, tone=%s",
                user.user_id,
                character.character_id,
                state.affinity_score,
                state.tone_state,
            )

        except Exception:
            logger.exception(
                "Relationship update failed for conversation %s",
                conversation.conversation_id,
            )
