# capp/classes/memory/memory_service.py

import logging
import json
import re
import time as time_module
from typing import Dict, List, Optional, Tuple

import requests
from django.conf import settings
from django.db import models
from django.utils import timezone
from pgvector.django import CosineDistance

from capp.models import MemoryFact, Message, Conversation, CustomUser, Character
from capp.classes.ai.mistral_client import MistralClient

logger = logging.getLogger(__name__)


class MemoryService:
    """
    Handles extraction, storage, and retrieval of memory facts.
    Embeddings go through Cortecs first and fall back to Mistral; fact extraction
    calls Mistral directly. Semantic search is pgvector.
    """

    @classmethod
    def clear_memory_for_character(cls, user: CustomUser, character: Character) -> Dict[str, int]:
        """
        Permanently delete all memory-related data for a user in the context of a specific character.
        This includes:
        - All MemoryFact records tied to this character.
        - All ConversationSummary records for conversations with this character.

        Returns a dict with counts of deleted items.
        """
        # 1. Delete memory facts for this character
        facts_deleted, _ = MemoryFact.objects.filter(user=user, character=character).delete()

        # 2. Delete conversation summaries for conversations with this character
        from capp.models import ConversationSummary

        conv_ids = Conversation.objects.filter(user=user, character=character).values_list("conversation_id", flat=True)
        summaries_deleted, _ = ConversationSummary.objects.filter(conversation_id__in=conv_ids).delete()

        logger.info(
            "Cleared memory for user %s, character %s: %d facts, %d summaries deleted.",
            user.user_id,
            character.character_id,
            facts_deleted,
            summaries_deleted,
        )

        return {
            "facts_deleted": facts_deleted,
            "summaries_deleted": summaries_deleted,
        }

    # ------------------------------------------------------------------
    # Public retrieval methods
    # ------------------------------------------------------------------

    @classmethod
    def get_active_facts(
        cls,
        user: CustomUser,
        character: Optional[Character] = None,
        limit: int = 5,
        max_tokens: int = 300,
    ) -> List[dict]:
        """
        Return a list of active memory facts for the user, prioritised by importance
        and least recently used, within a token budget.
        (Non‑semantic fallback – kept for backward compatibility.)
        """
        from capp.classes.ai.token_utils import TokenUtils

        queryset = MemoryFact.objects.filter(user=user, is_active=True)
        if character:
            queryset = queryset.filter(models.Q(character=character) | models.Q(character__isnull=True))
        else:
            queryset = queryset.filter(character__isnull=True)

        queryset = queryset.filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()))
        queryset = queryset.order_by("-importance_score", "last_used_at")

        selected_facts = []
        total_tokens = 0

        for fact in queryset:
            fact_tokens = TokenUtils.count_tokens(fact.fact_text)
            if total_tokens + fact_tokens > max_tokens or len(selected_facts) >= limit:
                break
            selected_facts.append(
                {
                    "id": str(fact.memory_fact_id),
                    "fact_text": fact.fact_text,
                    "created_at": fact.created_at,
                }
            )
            total_tokens += fact_tokens

        return selected_facts

    @classmethod
    def retrieve_relevant(
        cls,
        user: CustomUser,
        query_text: str,
        character: Optional[Character] = None,
        top_k: int = 5,
        min_similarity: float = 0.75,
    ) -> List[MemoryFact]:
        """
        Semantically retrieve the most relevant active facts for a user query.
        Uses the configured embedding provider + pgvector similarity.
        Falls back to empty list if embedding fails (no user‑facing delay).
        """
        emb_result = cls._get_embedding(query_text)
        if emb_result is None:
            return []
        query_emb, _ = emb_result  # ignore model name

        qs = MemoryFact.objects.filter(user=user, is_active=True)
        if character:
            qs = qs.filter(models.Q(character=character) | models.Q(character__isnull=True))
        else:
            qs = qs.filter(character__isnull=True)

        qs = qs.filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()))

        facts = (
            qs.annotate(similarity=1 - CosineDistance("embedding", query_emb))
            .filter(similarity__gte=min_similarity)
            .order_by("-importance_score", "-similarity")[:top_k]
        )

        # Update last_used_at in bulk
        now = timezone.now()
        for f in facts:
            f.last_used_at = now
        MemoryFact.objects.bulk_update(facts, ["last_used_at"])

        return facts

    # ------------------------------------------------------------------
    # Fact marking
    # ------------------------------------------------------------------

    @classmethod
    def mark_facts_used(cls, fact_ids: List[str]):
        if not fact_ids:
            return
        MemoryFact.objects.filter(memory_fact_id__in=fact_ids).update(last_used_at=timezone.now())

    # ------------------------------------------------------------------
    # Extraction (with embeddings + semantic duplicate check)
    # ------------------------------------------------------------------

    @classmethod
    def extract_and_save_facts(
        cls,
        conversation: Conversation,
        user_message: Message,
        ai_message: Message,
        context_message_count: int = 4,
    ):
        """Extract facts from conversation and store with embeddings."""
        try:
            user = conversation.user
            character = conversation.character

            # Gather recent messages
            recent_messages = Message.objects.filter(conversation=conversation).order_by("-created_at")[
                : context_message_count + 2
            ]
            recent_messages = list(recent_messages)[::-1]

            # Determine timezone for timestamps
            from zoneinfo import ZoneInfo
            from django.utils import timezone as django_timezone

            user_tz_str = getattr(user, "timezone", None) or "Africa/Nairobi"
            try:
                user_tz = ZoneInfo(user_tz_str)
            except Exception:
                user_tz = ZoneInfo("Africa/Nairobi")

            # Current date in user's timezone — needed for resolving relative dates
            now_local = django_timezone.now().astimezone(user_tz)
            current_date_str = now_local.strftime("%Y-%m-%d (%A)")
            current_tz_abbr = now_local.strftime("%Z")

            # Build prompt
            messages_text = []
            for msg in recent_messages:
                if msg.sender_type == Message.SenderType.USER:
                    role = "User"
                elif msg.sender_type == Message.SenderType.AI:
                    role = "Character"
                else:
                    role = "System"
                # Add timestamp prefix to each message
                try:
                    local_ts = msg.created_at.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    local_ts = "unknown time"
                messages_text.append(f"[{local_ts} {current_tz_abbr}] {role}: {msg.content}")

            prompt = (
                f"Today's date is {current_date_str} ({current_tz_abbr}).\n"
                "Extract up to 8 memorable facts from the conversation below.\n"
                "Each message is prefixed with a timestamp \u2014 use these to resolve relative time "
                "expressions (e.g. 'tomorrow', 'next week', 'yesterday') into actual calendar dates.\n\n"
                "Extract facts across these categories:\n"
                "1. Personal facts about the User (name, location, job, family, age, etc.)\n"
                "2. User preferences and interests\n"
                "3. Topics discussed (what subjects came up, even briefly)\n"
                "4. Memorable moments (something funny, emotional, or significant that happened)\n"
                "5. Plans, goals, or intentions mentioned by either party\n"
                "6. Things the Character said, promised, or did that were notable\n\n"
                "For each fact, provide:\n"
                '- "fact_text": a concise statement (max 25 words). For time-sensitive facts, include '
                '  the resolved date (e.g. "on 2026-06-19") instead of relative words. '
                "  Include known names and locations where relevant.\n"
                '- "fact_type": one of "personal", "preference", "goal", "context", "topic", "moment", "other"\n'
                '- "confidence": float between 0.0 and 1.0\n'
                '- "importance_score": integer between 1 and 10. '
                "  Time-sensitive events score 7+. Memorable moments score 6+. Casual topic mentions score 3-5.\n"
                "Output only a JSON array of objects. If nothing worth recording, output an empty array.\n\n"
                "Conversation:\n" + "\n".join(messages_text)
            )

            response = MistralClient.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.2,
            )

            content = response["choices"][0]["message"]["content"]

            # Clean markdown fences
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            try:
                extracted = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Failed to parse memory extraction JSON: %s", cleaned[:200])
                return

            saved = 0
            for item in extracted:
                if not isinstance(item, dict):
                    continue

                fact_text = item.get("fact_text", "").strip()
                if not fact_text:
                    continue

                # Generate embedding (background – retry on overload)
                embed_result = cls._get_embedding(fact_text, retry_on_overload=True)
                if embed_result is None:
                    continue
                embedding, used_model = embed_result

                # Duplicate check (semantic + prefix)
                if cls._is_duplicate(user, fact_text, embedding):
                    continue

                fact_type = item.get("fact_type", "other")
                confidence = float(item.get("confidence", 0.7))
                importance = int(item.get("importance_score", 5))

                MemoryFact.objects.create(
                    user=user,
                    character=character,
                    conversation=conversation,
                    source_message=user_message,
                    fact_text=fact_text,
                    fact_type=fact_type,
                    confidence_score=min(max(confidence, 0), 1),
                    importance_score=min(max(importance, 1), 10),
                    embedding=embedding,
                    embedding_model=used_model,  # now stores actual model used
                    extracted_at=timezone.now(),
                    is_active=True,
                )
                saved += 1

            logger.info("Extracted %d memory facts for user %s", saved, user.user_id)

        except Exception:
            logger.exception(
                "Memory extraction failed for conversation %s",
                conversation.conversation_id,
            )

    # ------------------------------------------------------------------
    # Embedding helpers (Cortecs primary, Mistral fallback)
    # ------------------------------------------------------------------

    @classmethod
    def _get_embedding(cls, text: str, retry_on_overload: bool = False) -> Optional[Tuple[List[float], str]]:
        """
        Generate an embedding vector using Cortecs first (CORTECS_EMBED_MODEL),
        falling back to direct Mistral on any failure.

        Returns:
            (embedding_vector, model_name) or None if failed.
        """
        cortecs_api_key = getattr(settings, "CORTECS_API_KEY", None)
        cortecs_base_url = getattr(settings, "CORTECS_BASE_URL", "https://api.cortecs.ai/v1")
        cortecs_embed_model = getattr(settings, "CORTECS_EMBED_MODEL", "bge-m3")
        mistral_embed_model = getattr(settings, "MISTRAL_EMBED_MODEL", "mistral-embed")

        # ---------- Try Cortecs first ----------
        if cortecs_api_key:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=cortecs_api_key, base_url=cortecs_base_url)
                response = client.embeddings.create(
                    model=cortecs_embed_model,
                    input=[text],
                )
                embedding = response.data[0].embedding
                return (embedding, cortecs_embed_model)
            except Exception as e:
                logger.warning(
                    "Cortecs embedding failed (model=%s): %s – falling back to Mistral",
                    cortecs_embed_model,
                    e,
                )
        # ----------------------------------------

        # ---------- Fallback to direct Mistral ----------
        embed_base = getattr(settings, "MISTRAL_EMBED_BASE_URL", "https://api.mistral.ai")
        url = embed_base.rstrip("/") + "/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": mistral_embed_model,
            "input": [text],
        }

        max_retries = 3 if retry_on_overload else 1

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                embedding = data["data"][0]["embedding"]
                return (embedding, mistral_embed_model)
            except requests.exceptions.RequestException as e:
                if ("503" in str(e) or "429" in str(e)) and attempt < max_retries:
                    wait = 2**attempt
                    logger.warning(
                        "Embedding overload (attempt %d/%d) – retrying in %ds…",
                        attempt,
                        max_retries,
                        wait,
                    )
                    time_module.sleep(wait)
                else:
                    logger.error("Failed to generate embedding: %s", e)
                    return None
        return None

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    @classmethod
    def _is_duplicate(cls, user: CustomUser, text: str, embedding: List[float]) -> bool:
        """
        Check for near‑duplicate facts:
        1) Prefix match (exact first 60 chars)
        2) Semantic cosine similarity > 0.92 via pgvector
        """
        prefix = text[:60]
        if MemoryFact.objects.filter(user=user, fact_text__startswith=prefix, is_active=True).exists():
            return True

        similar = (
            MemoryFact.objects.filter(user=user, is_active=True)
            .annotate(similarity=1 - CosineDistance("embedding", embedding))
            .filter(similarity__gt=0.92)
            .first()
        )
        return similar is not None

    # ------------------------------------------------------------------
    # Deactivation
    # ------------------------------------------------------------------

    @classmethod
    def deactivate_fact(cls, fact_id: str):
        MemoryFact.objects.filter(memory_fact_id=fact_id).update(is_active=False)
