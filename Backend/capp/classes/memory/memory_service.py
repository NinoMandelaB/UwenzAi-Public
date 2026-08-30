# capp/classes/memory/memory_service.py

import logging
import json
import re
import time as time_module
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from pgvector.django import CosineDistance

from capp.models import (
    Character,
    Conversation,
    CustomUser,
    MemoryEntity,
    MemoryFact,
    MemoryRelation,
    Message,
)
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
        Permanently delete all memory-related data for a user in the context of a
        specific character. Scoped strictly to ``(user, character)`` — other users'
        and other characters' data is never touched.

        Deletes:
        - MemoryFact rows for this (user, character).
        - MemoryRelation rows for this (user, character).
        - ConversationSummary rows for conversations between this user and character.
        - RelationshipState row for this (user, character) — resets affinity,
          trust, familiarity, tone_state, and the qualitative rolling notes.
        - MemoryEntity rows owned by this user that no longer have any relation
          referencing them (post-relation-delete orphan sweep).

        Returns a dict with per-model deletion counts.
        """
        from capp.models import ConversationSummary, RelationshipState

        with transaction.atomic():
            # 1. Facts for this (user, character).
            facts_deleted, _ = MemoryFact.objects.filter(user=user, character=character).delete()

            # 2. Character-scoped relations. Entities are user-global and may
            # still be referenced by relations with other characters, so we
            # sweep orphaned entities separately after the delete.
            relations_deleted, _ = MemoryRelation.objects.filter(user=user, character=character).delete()

            # 3. Conversation summaries for this (user, character).
            conv_ids = list(
                Conversation.objects.filter(user=user, character=character).values_list("conversation_id", flat=True)
            )
            summaries_deleted, _ = ConversationSummary.objects.filter(conversation_id__in=conv_ids).delete()

            # 4. Relationship state — affinity/trust/familiarity/tone_state +
            # the qualitative rolling notes accumulated by the analysis LLM.
            # A "clear memory" action almost certainly means the user also
            # wants the character's *feelings* about them reset.
            relationship_deleted, _ = RelationshipState.objects.filter(user=user, character=character).delete()

            # 5. Orphan entity sweep. MemoryEntity is user-scoped (no character
            # FK), so entities only referenced by the relations we just deleted
            # would otherwise linger with their 1024-dim embedding vectors.
            # Only sweep entities belonging to *this* user, and only those
            # whose remaining reference count from any surviving MemoryRelation
            # (any character, any scope) is zero.
            orphan_qs = (
                MemoryEntity.objects.filter(user=user)
                .annotate(
                    remaining_refs=models.Count("out_relations", distinct=True)
                    + models.Count("in_relations", distinct=True)
                )
                .filter(remaining_refs=0)
            )
            entities_deleted, _ = orphan_qs.delete()

        logger.info(
            "Cleared memory for user %s, character %s: "
            "%d facts, %d relations, %d summaries, %d relationship-state row(s), %d orphan entities deleted.",
            user.user_id,
            character.character_id,
            facts_deleted,
            relations_deleted,
            summaries_deleted,
            relationship_deleted,
            entities_deleted,
        )

        return {
            "facts_deleted": facts_deleted,
            "relations_deleted": relations_deleted,
            "summaries_deleted": summaries_deleted,
            "relationship_reset": relationship_deleted,
            "entities_deleted": entities_deleted,
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

        queryset = cls._current_facts_queryset(user, character)
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
                    "valid_from": fact.valid_from,
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

        qs = cls._current_facts_queryset(user, character)

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
    # Graph retrieval (Phase 3)
    # ------------------------------------------------------------------

    GRAPH_MIN_SIMILARITY = 0.65
    GRAPH_RECENCY_HALF_LIFE_DAYS = 30

    # ---- 2-hop expansion (thresholded, adaptive) ----
    # Second-hop edges are noisier than first-hop, so the similarity floor is
    # stricter (0.70 vs 0.65) and the per-endpoint cap is tighter (2 vs 4).
    # A hard total ceiling keeps the memory block bounded no matter how many
    # seeds hit a densely connected node.
    GRAPH_HOP2_MIN_SIMILARITY = 0.70
    GRAPH_HOP2_PER_ENDPOINT_CAP = 2
    GRAPH_TOTAL_EDGE_CEILING = 20

    @classmethod
    def retrieve_graph_context(
        cls,
        user: CustomUser,
        query_text: str,
        character: Optional[Character] = None,
        top_k: int = 4,
    ) -> List[MemoryRelation]:
        """
        Return the top ``top_k`` currently-valid relations most relevant to
        ``query_text`` for ``user`` in ``character``'s context, plus one-
        and two-hop neighbours of each hit so the prompt gets a small local
        subgraph rather than isolated edges.

        Seed ranking = cosine_similarity × (0.5 + 0.5 × recency), where
        recency decays with a 30-day half-life so a relation extracted today
        weighs roughly 2× as much as a two-month-old one at the same
        similarity.

        Neighbours are ranked purely by cosine_similarity to the query (not
        by recency): when a hub node like "mom" has many attributes, we want
        the neighbour that semantically answers the query — not merely the
        most recently extracted one.

        Hop-2 neighbours (adaptive, thresholded): for each hop-1 neighbour
        we pull up to ``GRAPH_HOP2_PER_ENDPOINT_CAP`` further neighbours,
        gated by a stricter ``GRAPH_HOP2_MIN_SIMILARITY`` (0.70 vs the 0.65
        seed floor). This unlocks natural "friend-of-a-friend" queries —
        "what does my brother's friend do?" — while the stricter floor
        keeps second-hop noise out on queries that don't chain.

        The whole subgraph is capped at ``GRAPH_TOTAL_EDGE_CEILING`` edges
        (default 20) so the memory block stays bounded on densely connected
        users. Typical non-chaining queries land close to 1-hop cost.

        Fail-silent: any exception (missing embedding model, pgvector not
        available, empty query) returns [] so retrieval never breaks chat.
        """
        if not query_text or user is None:
            return []
        try:
            base_qs = cls._current_relations_queryset(user, character).select_related("subject", "object_entity")
            if not base_qs.exists():
                return []

            emb_result = cls._get_embedding(query_text)
            if emb_result is None:
                return []
            query_emb, _ = emb_result  # ignore model name; only the vector goes to pgvector

            annotated = (
                base_qs.filter(embedding__isnull=False)
                .annotate(similarity=1 - CosineDistance("embedding", query_emb))
                .filter(similarity__gte=cls.GRAPH_MIN_SIMILARITY)
                .order_by("-similarity")[: top_k * 3]
            )

            now = timezone.now()
            half_life = cls.GRAPH_RECENCY_HALF_LIFE_DAYS
            scored: List[tuple] = []
            for rel in annotated:
                sim = float(getattr(rel, "similarity", 0.0) or 0.0)
                age_days = max((now - rel.extracted_at).total_seconds() / 86400.0, 0.0)
                recency = 0.5 ** (age_days / half_life)
                score = sim * (0.5 + 0.5 * recency)
                scored.append((score, rel))

            scored.sort(key=lambda x: x[0], reverse=True)
            hits = [rel for _, rel in scored[:top_k]]
            if not hits:
                return []

            # 1-hop expansion: for each hit, pull neighbours touching either
            # endpoint so the LLM sees adjacent context ("Zawadi lives in
            # Nairobi" → also include "Zawadi works at Safaricom").
            #
            # Two behaviours that matter more than adding a second hop:
            #   * Rank neighbours by query-similarity (not recency), so when
            #     the top hit is "mom has_pet Tim" and the user asked "where
            #     does Tim live?", the "mom lives_in Budapest" neighbour
            #     beats "mom age 45" — the former semantically matches
            #     "lives".
            #   * Cap at 4 (not 2) so entity-dense hubs (a much-mentioned
            #     "mom" node with many attributes) don't evict the neighbour
            #     that actually answers the query.
            seen_ids = {r.memory_relation_id for r in hits}
            expanded: List[MemoryRelation] = list(hits)
            hop1_relations: List[MemoryRelation] = []
            for hit in hits:
                endpoint_ids = [hit.subject_id]
                if hit.object_entity_id:
                    endpoint_ids.append(hit.object_entity_id)
                neighbours = (
                    base_qs.filter(models.Q(subject_id__in=endpoint_ids) | models.Q(object_entity_id__in=endpoint_ids))
                    .exclude(memory_relation_id__in=seen_ids)
                    .filter(embedding__isnull=False)
                    .annotate(neighbour_similarity=1 - CosineDistance("embedding", query_emb))
                    .order_by("-neighbour_similarity", "-extracted_at")[:4]
                )
                for n in neighbours:
                    seen_ids.add(n.memory_relation_id)
                    expanded.append(n)
                    hop1_relations.append(n)

            # 2-hop expansion (adaptive, thresholded).
            #
            # For each hop-1 neighbour, pull up to GRAPH_HOP2_PER_ENDPOINT_CAP
            # further neighbours of *its* endpoints, gated by the stricter
            # GRAPH_HOP2_MIN_SIMILARITY (0.70 vs 0.65 seed floor). This
            # unlocks friend-of-a-friend queries ("what does my brother's
            # friend do?") while the stricter floor filters out most
            # non-chaining queries so cost stays close to 1-hop for the
            # common case.
            #
            # The GRAPH_TOTAL_EDGE_CEILING cap is checked continuously so a
            # densely connected user never blows past the memory-block
            # budget, no matter how many seeds hit a hub node.
            hop2_floor = cls.GRAPH_HOP2_MIN_SIMILARITY
            hop2_cap = cls.GRAPH_HOP2_PER_ENDPOINT_CAP
            total_ceiling = cls.GRAPH_TOTAL_EDGE_CEILING

            for hop1 in hop1_relations:
                if len(expanded) >= total_ceiling:
                    break
                endpoint_ids = [hop1.subject_id]
                if hop1.object_entity_id:
                    endpoint_ids.append(hop1.object_entity_id)
                second_hop = (
                    base_qs.filter(models.Q(subject_id__in=endpoint_ids) | models.Q(object_entity_id__in=endpoint_ids))
                    .exclude(memory_relation_id__in=seen_ids)
                    .filter(embedding__isnull=False)
                    .annotate(hop2_similarity=1 - CosineDistance("embedding", query_emb))
                    .filter(hop2_similarity__gte=hop2_floor)
                    .order_by("-hop2_similarity", "-extracted_at")[:hop2_cap]
                )
                for n in second_hop:
                    if len(expanded) >= total_ceiling:
                        break
                    seen_ids.add(n.memory_relation_id)
                    expanded.append(n)

            return expanded
        except Exception:
            logger.exception("Graph retrieval failed for user %s", getattr(user, "user_id", None))
            return []

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

            # Guests have no user FK — memory is only persisted for accounts
            # (guest history is migrated on signup and extracted then).
            if user is None:
                return

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
                '- "valid_from": optional, YYYY-MM-DD, only when the fact describes something that '
                "  became true on a specific/resolvable date (e.g. moved house, started a job). Omit otherwise.\n"
                "Output only a JSON object with two keys:\n"
                '- "facts": array of fact objects as described above (empty array if none)\n'
                '- "relations": array of relationship triples in the form\n'
                '  {"subject": "<name>", "subject_type": "person|place|org|thing", '
                '  "predicate": "<snake_case verb, e.g. lives_in, works_at, sibling_of, married_to, '
                '  owns, likes>", "object": "<name>", "object_type": "person|place|org|thing", '
                '  "valid_from": "YYYY-MM-DD or null"}.\n'
                "  Extract a relation whenever the conversation states a durable relationship between "
                "  two named entities (people, places, organisations). Use the same names consistently. "
                'Use "the user" as subject when the user is one side of the relationship. '
                "Empty array if none.\n\n"
                "Conversation:\n" + "\n".join(messages_text)
            )

            # Load a compact temporal snapshot of currently-valid facts so the
            # extractor can reference them by id for corrections (supersession)
            # instead of the old dedup silently dropping updates.
            snapshot = cls._build_fact_snapshot(user, character)
            if snapshot:
                prompt += (
                    "\n\nCurrently known facts (id :: text):\n" + "\n".join(snapshot) + "\n\n"
                    'If a new fact updates or corrects a listed one, include "supersedes": "<id>" '
                    "on the new fact. Only supersede facts about the SAME subject that can no longer "
                    "both be true (e.g. a moved city, a changed job) — do not supersede facts that "
                    "merely overlap in topic."
                )

            # Known entities and current relations let the extractor reuse
            # canonical names and flag updates via "updates": "<id>" instead of
            # creating parallel edges that contradict each other.
            entity_names = cls._build_entity_snapshot(user)
            if entity_names:
                prompt += (
                    "\n\nKnown entities (reuse these exact names in relations when they "
                    "refer to the same thing): " + ", ".join(entity_names)
                )
            relation_snapshot = cls._build_relation_snapshot(user, character)
            if relation_snapshot:
                prompt += (
                    "\n\nCurrently known relations (id :: triple):\n" + "\n".join(relation_snapshot) + "\n\n"
                    "If a new relation replaces a listed one (same subject and predicate, different "
                    'object), include "updates": "<id>" on it.'
                )

            # Cortecs first (CORTECS_MEM_MODEL), Mistral fallback — mirrors the
            # embeddings path so both extractor and vectoriser share the same
            # provider ordering and failure semantics.
            content = cls._extraction_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900,
                temperature=0.2,
            )
            if content is None:
                logger.warning(
                    "Memory extraction chat completion returned no content for conversation %s",
                    conversation.conversation_id if conversation else None,
                )
                return

            # Clean markdown fences
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)

            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Failed to parse memory extraction JSON: %s", cleaned[:200])
                return

            # Phase 1 wrote a bare JSON array; Phase 2 writes
            # {"facts": [...], "relations": [...]}. Accept both so a provider
            # that ignores the new schema still yields facts.
            if isinstance(parsed, list):
                extracted, extracted_relations = parsed, []
            elif isinstance(parsed, dict):
                extracted = parsed.get("facts") or []
                extracted_relations = parsed.get("relations") or []
            else:
                extracted, extracted_relations = [], []

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

                now = timezone.now()
                new_fact = MemoryFact.objects.create(
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
                    extracted_at=now,
                    valid_from=cls._parse_valid_from(item.get("valid_from"), now_local),
                    is_active=True,
                )
                saved += 1

                # Temporal supersession: if the extractor flagged this fact as a
                # correction of an existing one, close the old fact's validity
                # window and link the history chain.
                supersedes_id = item.get("supersedes")
                if supersedes_id:
                    cls._supersede_fact(supersedes_id, new_fact, now)

            relations_saved = cls._save_relations(
                extracted_relations,
                user=user,
                character=character,
                conversation=conversation,
                reference=now_local,
            )

            logger.info(
                "Extracted %d memory facts and %d relations for user %s",
                saved,
                relations_saved,
                user.user_id,
            )

        except Exception:
            logger.exception(
                "Memory extraction failed for conversation %s",
                conversation.conversation_id,
            )

    # ------------------------------------------------------------------
    # Extraction chat helper (Cortecs primary, Mistral fallback)
    # ------------------------------------------------------------------

    @classmethod
    def _extraction_chat_completion(
        cls,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        """
        Run the memory-extraction LLM call against Cortecs first, then fall
        back to Mistral on any error.

        Cortecs is preferred because ``CORTECS_MEM_MODEL`` (default
        ``deepseek-v4-flash-0731``) benchmarks better on prompt adherence and
        agentic/JSON-structured tasks than the chat-tier model, which matters
        much more for extraction than for user-facing chat.

        Returns the assistant text or ``None`` on total failure. Never
        raises — extraction is a background side-effect and must not break
        message-send flow.
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
                    "Cortecs extraction returned empty content (model=%s) — falling back to Mistral",
                    cortecs_mem_model,
                )
            except Exception as e:
                logger.warning(
                    "Cortecs extraction failed (model=%s): %s — falling back to Mistral",
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
        except Exception:
            logger.exception("Mistral extraction fallback failed")
            return None

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
    # Temporal helpers (Phase 1)
    # ------------------------------------------------------------------

    @classmethod
    def _current_facts_queryset(cls, user: CustomUser, character: Optional[Character]):
        """
        Base queryset for "what is true right now": active, not expired, and
        not superseded (valid_to IS NULL). Superseded facts remain queryable
        for history but are never injected into prompts.
        """
        qs = MemoryFact.objects.filter(user=user, is_active=True, valid_to__isnull=True)
        if character:
            qs = qs.filter(models.Q(character=character) | models.Q(character__isnull=True))
        else:
            qs = qs.filter(character__isnull=True)
        return qs.filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()))

    # Facts shown to the extractor as supersession candidates. Kept small on
    # purpose: every line costs prompt tokens on each extraction call.
    SNAPSHOT_LIMIT = 15
    # Window around the message timestamp in which a date mention is treated
    # as resolvable from conversation context rather than hallucinated.
    VALID_FROM_MAX_FUTURE_DAYS = 180
    VALID_FROM_MAX_PAST_DAYS = 365 * 5

    @classmethod
    def _build_fact_snapshot(
        cls,
        user: CustomUser,
        character: Optional[Character],
        limit: Optional[int] = None,
    ) -> List[str]:
        """
        Compact "<short-id> :: <fact text>" lines for the extraction prompt.

        Only currently-valid facts (valid_to IS NULL) are candidates for
        supersession. Ranked by importance then recency so the lines that make
        the token budget are the ones most likely to be corrected.
        """
        if user is None:
            return []

        qs = cls._current_facts_queryset(user, character)
        qs = qs.order_by("-importance_score", "-extracted_at")

        return [f"{str(f.memory_fact_id)[:8]} :: {f.fact_text}" for f in qs[: (limit or cls.SNAPSHOT_LIMIT)]]

    @classmethod
    def _parse_valid_from(cls, raw: Optional[str], reference) -> Optional[datetime]:
        """
        Parse an LLM-provided YYYY-MM-DD into an aware datetime, validated
        against the conversation timeframe so a hallucinated date can never
        pin a fact to a wildly wrong era.

        ``reference`` may be aware (extraction path, user timezone) or naive
        (tests/direct calls) — both are handled.
        """
        if not raw or not isinstance(raw, str):
            return None
        try:
            parsed = datetime.strptime(raw.strip(), "%Y-%m-%d")
        except ValueError:
            return None
        parsed = parsed.replace(hour=12)
        if timezone.is_aware(reference):
            parsed = parsed.replace(tzinfo=reference.tzinfo)
        if parsed > reference + timedelta(days=cls.VALID_FROM_MAX_FUTURE_DAYS):
            return None
        if parsed < reference - timedelta(days=cls.VALID_FROM_MAX_PAST_DAYS):
            return None
        return parsed

    @classmethod
    def _supersede_fact(cls, old_fact_id: str, new_fact: MemoryFact, now) -> bool:
        """
        Close ``old_fact``'s validity window and point its history chain at
        ``new_fact``. Ownership and scope are verified so one user's extraction
        can never touch another user's (or another character's) memory.
        """
        # The extractor is only ever shown the 8-char short id (to save prompt
        # tokens), so resolve by prefix match against this user's currently
        # valid facts rather than requiring a full UUID.
        candidates = MemoryFact.objects.filter(
            user=new_fact.user,
            is_active=True,
            valid_to__isnull=True,
        ).exclude(memory_fact_id=new_fact.memory_fact_id)
        old_fact = next(
            (f for f in candidates if str(f.memory_fact_id).startswith(old_fact_id.strip().lower())),
            None,
        )
        if old_fact is None:
            logger.debug(
                "Supersession skipped: fact %s not found/already closed for user %s",
                old_fact_id,
                new_fact.user_id,
            )
            return False

        same_character = (
            old_fact.character_id == new_fact.character_id
            or old_fact.character_id is None
            or new_fact.character_id is None
        )
        if not same_character:
            logger.debug(
                "Supersession skipped: character scope mismatch (%s vs %s)",
                old_fact.character_id,
                new_fact.character_id,
            )
            return False

        old_fact.valid_to = now
        old_fact.superseded_by = new_fact
        old_fact.save(update_fields=["valid_to", "superseded_by", "updated_at"])
        logger.info(
            "Fact %s superseded by %s for user %s",
            old_fact.memory_fact_id,
            new_fact.memory_fact_id,
            new_fact.user_id,
        )
        return True

    # ------------------------------------------------------------------
    # Graph helpers (Phase 2)
    # ------------------------------------------------------------------

    ENTITY_SNAPSHOT_LIMIT = 25
    RELATION_SNAPSHOT_LIMIT = 10
    RELATION_SEMANTIC_DEDUP_THRESHOLD = 0.95

    @classmethod
    def _current_relations_queryset(cls, user: CustomUser, character: Optional[Character]):
        """
        Return the queryset of relations that are currently believed true for
        ``user`` in ``character``'s context. Mirrors ``_current_facts_queryset``
        so retrieval, snapshot, and cleanup share one truth filter.
        """
        qs = MemoryRelation.objects.filter(user=user, invalidated_at__isnull=True)
        if character:
            qs = qs.filter(models.Q(character=character) | models.Q(character__isnull=True))
        else:
            qs = qs.filter(character__isnull=True)
        # Event-time expiry (valid_to in the past) also drops the edge.
        qs = qs.filter(models.Q(valid_to__isnull=True) | models.Q(valid_to__gt=timezone.now()))
        return qs

    @classmethod
    def _build_entity_snapshot(cls, user: CustomUser, limit: Optional[int] = None) -> List[str]:
        """Return the user's most-referenced canonical entity names."""
        limit = limit or cls.ENTITY_SNAPSHOT_LIMIT
        return list(
            MemoryEntity.objects.filter(user=user).order_by("-updated_at").values_list("name", flat=True)[:limit]
        )

    @classmethod
    def _build_relation_snapshot(
        cls,
        user: CustomUser,
        character: Optional[Character],
        limit: Optional[int] = None,
    ) -> List[str]:
        """Compact 'shortid :: subj -pred-> obj' snapshot for prompt reuse."""
        limit = limit or cls.RELATION_SNAPSHOT_LIMIT
        qs = cls._current_relations_queryset(user, character).select_related("subject", "object_entity")
        return [f"{str(r.memory_relation_id)[:8]} :: {r}" for r in qs.order_by("-extracted_at")[:limit]]

    @classmethod
    def _resolve_entity(
        cls,
        user: CustomUser,
        name: str,
        entity_type: str,
    ) -> Optional[MemoryEntity]:
        """
        Get or create a canonical entity for ``user``. Semantic matching is
        intentionally NOT done here: without a curated alias list, embedding
        similarity is far too aggressive and merges unrelated lookalikes
        ("my sister Amina" and "Amina from work"). The prompt-side entity
        snapshot handles alias reuse.
        """
        if not name:
            return None
        normalized = MemoryEntity.normalize_name(name)
        if not normalized:
            return None

        entity_type = entity_type if entity_type in dict(MemoryEntity.EntityType.choices) else "other"
        entity, created = MemoryEntity.objects.get_or_create(
            user=user,
            normalized_name=normalized,
            defaults={"name": name.strip(), "entity_type": entity_type},
        )
        # Populate the embedding lazily so retrieval-side vector matching can
        # ever find these rows. Only compute when the field is empty so we
        # don't pay the embedding cost on every extraction round. Fail-silent:
        # if embedding is unavailable the entity is still saved.
        if not entity.embedding:
            emb = cls._get_embedding(entity.name or normalized)
            if emb is not None:
                entity.embedding, entity.embedding_model = emb
                entity.save(update_fields=["embedding", "embedding_model", "updated_at"])
        return entity

    @staticmethod
    def _relation_triple_text(
        subject: "MemoryEntity",
        predicate: str,
        object_entity: Optional["MemoryEntity"],
        object_text: Optional[str],
    ) -> str:
        """Render "subject predicate object" for embedding, snake_case → spaces."""
        subj_name = subject.name if subject is not None else ""
        obj_name = object_entity.name if object_entity is not None else (object_text or "")
        pred = (predicate or "").replace("_", " ").strip()
        return f"{subj_name} {pred} {obj_name}".strip()

    @classmethod
    def _save_relations(
        cls,
        raw_relations: List[dict],
        *,
        user: CustomUser,
        character: Optional[Character],
        conversation: Optional[Conversation],
        reference,
    ) -> int:
        """Persist newly-extracted relations, closing superseded edges and dedup'ing."""
        if not raw_relations:
            return 0

        saved = 0
        now = timezone.now()
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            subject_name = (item.get("subject") or "").strip()
            predicate = (item.get("predicate") or "").strip().lower().replace(" ", "_")
            object_name = (item.get("object") or "").strip()
            if not subject_name or not predicate:
                continue

            # Reserve length so a runaway model can't blow past the column limit.
            predicate = predicate[:60]

            subject_entity = cls._resolve_entity(user, subject_name, item.get("subject_type") or "other")
            if subject_entity is None:
                continue
            object_entity = (
                cls._resolve_entity(user, object_name, item.get("object_type") or "other") if object_name else None
            )

            # Exact duplicate of a currently-valid edge → skip silently.
            current = cls._current_relations_queryset(user, character)
            duplicate_qs = current.filter(subject=subject_entity, predicate=predicate)
            if object_entity is not None:
                duplicate_qs = duplicate_qs.filter(object_entity=object_entity)
            else:
                duplicate_qs = duplicate_qs.filter(object_text=object_name or None)
            if duplicate_qs.exists():
                continue

            # Embed the triple text so Phase 3 vector search can retrieve
            # this edge ("Zawadi lives in Nairobi"). Fail-silent: relation is
            # persisted without an embedding rather than blocking extraction.
            triple_text = cls._relation_triple_text(subject_entity, predicate, object_entity, object_name)
            emb = cls._get_embedding(triple_text)
            embedding_vec, embedding_model = emb if emb is not None else (None, "")

            new_relation = MemoryRelation.objects.create(
                user=user,
                character=character,
                conversation=conversation,
                subject=subject_entity,
                predicate=predicate,
                object_entity=object_entity,
                object_text=None if object_entity is not None else (object_name or None),
                valid_from=cls._parse_valid_from(item.get("valid_from"), reference),
                embedding=embedding_vec,
                embedding_model=embedding_model,
            )

            updates_id = item.get("updates")
            if updates_id:
                cls._supersede_relation(updates_id, new_relation, now)
            saved += 1

        return saved

    @classmethod
    def _supersede_relation(cls, old_relation_id: str, new_relation: MemoryRelation, now) -> bool:
        """Close ``old_relation``'s system-time validity; ownership-checked."""
        candidates = MemoryRelation.objects.filter(
            user=new_relation.user,
            invalidated_at__isnull=True,
        ).exclude(memory_relation_id=new_relation.memory_relation_id)
        old_relation = next(
            (r for r in candidates if str(r.memory_relation_id).startswith(old_relation_id.strip().lower())),
            None,
        )
        if old_relation is None:
            return False

        same_character = (
            old_relation.character_id == new_relation.character_id
            or old_relation.character_id is None
            or new_relation.character_id is None
        )
        if not same_character:
            return False

        old_relation.invalidated_at = now
        old_relation.superseded_by = new_relation
        old_relation.save(update_fields=["invalidated_at", "superseded_by", "updated_at"])
        return True

    # ------------------------------------------------------------------
    # Deactivation
    # ------------------------------------------------------------------

    @classmethod
    def deactivate_fact(cls, fact_id: str):
        MemoryFact.objects.filter(memory_fact_id=fact_id).update(is_active=False)
