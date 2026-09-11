# capp/classes/memory/memory_editor_service.py
#
# Read/modify access for the user-facing "Memory" editor popup (brain icon in
# the chat header). This is a *view layer* over the exact same rows the
# extraction pipeline writes — it must never change how extraction, retrieval,
# or summarization work, only let a user correct, remove, or add to what a
# character already knows about them.
#
# Scoping rules (enforced on EVERY method, defense in depth):
# - Every queryset is filtered by ``user`` first. A different user's rows can
#   never be listed, mutated, or deleted through this service.
# - List endpoints additionally scope to one character (plus the user's
#   character-global rows, which every character sees — they are labelled
#   "shared" in the payload).
# - RelationshipState (affinity/trust/tone/relationship notes) is deliberately
#   NOT exposed or editable here: it is the character's *feelings*, not the
#   user's facts, and the editor must not touch it.

import logging
from typing import Dict, Optional

from django.core.exceptions import ValidationError
from django.utils import timezone

from capp.models import (
    Character,
    ConversationSummary,
    CustomUser,
    MemoryEntity,
    MemoryFact,
    MemoryRelation,
)

from capp.classes.memory.memory_service import MemoryService
from capp.classes.memory.mutation_guard import memory_write_lock, advance_memory, advance_summary

logger = logging.getLogger(__name__)

# Input guards: keep user-authored content within the same bounds the
# extraction pipeline enforces, so a hand-written fact can never blow past
# what the prompt-side token budget assumes.
MAX_FACT_LENGTH = 500
MAX_ENTITY_NAME_LENGTH = 120
MAX_PREDICATE_LENGTH = 60
MAX_SUMMARY_LENGTH = 12000


class MemoryEditorError(Exception):
    """Validation/ownership error surfaced to the view layer as a 400/404."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


class MemoryEditorService:
    """User-facing CRUD over MemoryFact / MemoryRelation / ConversationSummary."""

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    @classmethod
    def list_memory(cls, user: CustomUser, character: Character, page=1, page_size=50) -> Dict:
        """
        Everything the character currently "knows" about ``user``: active
        facts, graph relations, and the current rolling summary.

        Uses the SAME truth filters as prompt injection
        (``MemoryService._current_facts_queryset`` /
        ``_current_relations_queryset``), so what the user sees in the editor
        is exactly what the model sees in the prompt — no stale or
        superseded rows.
        """
        try:
            if isinstance(page, bool) or isinstance(page_size, bool):
                raise ValueError
            if not str(page).isascii() or not str(page).isdigit():
                raise ValueError
            if not str(page_size).isascii() or not str(page_size).isdigit():
                raise ValueError
            page, page_size = int(page), int(page_size)
            if page < 1 or not 1 <= page_size <= 100 or page > 2147483647:
                raise ValueError
        except (TypeError, ValueError):
            raise MemoryEditorError("invalid_pagination")
        facts = (
            MemoryService._current_facts_queryset(user, character)
            .defer("embedding")
            .order_by("-importance_score", "-extracted_at", "pk")
        )
        relations = (
            MemoryService._current_relations_queryset(user, character)
            .select_related("subject", "object_entity")
            .defer("embedding", "subject__embedding", "object_entity__embedding")
            .order_by("-extracted_at", "pk")
        )
        summary = cls._current_summary(user, character)

        start = (page - 1) * page_size
        stop = start + page_size

        def metadata(qs):
            total = qs.count()
            pages = (total + page_size - 1) // page_size
            return {"total": total, "pages": pages, "has_next": page < pages, "has_previous": page > 1}

        pagination = {
            "page": page,
            "page_size": page_size,
            "facts": metadata(facts),
            "relations": metadata(relations),
        }
        return {
            "facts": [cls._serialize_fact(f) for f in facts[start:stop]],
            "relations": [cls._serialize_relation(r) for r in relations[start:stop]],
            "summary": cls._serialize_summary(summary) if summary else None,
            "pagination": pagination,
        }

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    @classmethod
    def create_fact(
        cls,
        user: CustomUser,
        character: Character,
        fact_text: str,
        fact_type: str = "other",
        importance_score: int = 7,
    ) -> Dict:
        fact_text = cls._text(fact_text, "fact", MAX_FACT_LENGTH)

        valid_types = dict(MemoryFact.FactType.choices)
        if not isinstance(fact_type, str):
            raise MemoryEditorError("invalid_fact_type")
        if fact_type not in valid_types:
            fact_type = "other"

        if type(importance_score) is not int:
            raise MemoryEditorError("invalid_importance_score")
        importance = min(max(importance_score, 1), 10)
        duplicate = MemoryService.find_exact_fact(user, character, fact_text)
        if duplicate:
            return cls._serialize_fact(duplicate)

        # User-asserted truth: full confidence, valid now.
        # Embedding is best-effort — the fact is saved even if the embedding
        # provider is down (semantic retrieval then simply skips it, exactly
        # like an extraction-time embedding failure).
        embedding, embedding_model = cls._embed_or_none(fact_text)

        with memory_write_lock(user):
            duplicate = MemoryService.find_exact_fact(user, character, fact_text)
            if duplicate:
                return cls._serialize_fact(duplicate)
            fact = MemoryFact.objects.create(
                user=user,
                character=character,
                fact_text=fact_text,
                content_key=MemoryService.fact_content_key(fact_text),
                fact_type=fact_type,
                confidence_score=1.0,
                importance_score=importance,
                embedding=embedding,
                embedding_model=embedding_model,
                is_active=True,
            )
            advance_memory(user)
        logger.info(
            "User %s added memory fact %s via editor (character %s)",
            user.user_id,
            fact.memory_fact_id,
            character.character_id,
        )
        return cls._serialize_fact(fact)

    @classmethod
    def update_fact(cls, user: CustomUser, fact_id: str, fact_text: str, expected_updated_at=None) -> Dict:
        fact = cls._get_fact(user, fact_id)
        cls._check_fact_current(fact)
        cls._check_revision(fact, expected_updated_at)
        fact_text = cls._text(fact_text, "fact", MAX_FACT_LENGTH)

        # Re-embed so semantic retrieval matches the corrected text. On
        # failure clear the stale vector — a wrong-text embedding is worse
        # than none (non-semantic retrieval still works without it).
        embedding, embedding_model = cls._embed_or_none(fact_text)
        with memory_write_lock(user):
            current = cls._get_fact(user, fact_id, stale_if_missing=True)
            cls._check_fact_current(current)
            cls._check_revision(current, fact.updated_at.isoformat())
            current.fact_text = fact_text
            current.content_key = MemoryService.fact_content_key(fact_text)
            current.embedding = embedding
            current.embedding_model = embedding_model
            current.save(update_fields=["fact_text", "content_key", "embedding", "embedding_model", "updated_at"])
            advance_memory(user)
            fact = current
        logger.info("User %s edited memory fact %s via editor", user.user_id, fact.memory_fact_id)
        return cls._serialize_fact(fact)

    @classmethod
    def delete_fact(cls, user: CustomUser, fact_id: str, expected_updated_at=None) -> Dict:
        with memory_write_lock(user):
            fact = cls._get_fact(user, fact_id)
            cls._check_fact_current(fact)
            cls._check_revision(fact, expected_updated_at)
            fact_id_str = str(fact.memory_fact_id)
            fact.delete()
            advance_memory(user)
        logger.info("User %s deleted memory fact %s via editor", user.user_id, fact_id_str)
        return {"memory_fact_id": fact_id_str}

    # ------------------------------------------------------------------
    # Relations (knowledge-graph edges)
    # ------------------------------------------------------------------

    @classmethod
    def create_relation(
        cls,
        user: CustomUser,
        character: Character,
        subject: str,
        predicate: str,
        object_text: str,
    ) -> Dict:
        subject, predicate, object_text = cls._normalize_triple(subject, predicate, object_text)

        duplicate = MemoryService.find_exact_relation(user, character, subject, predicate, object_text)
        if duplicate:
            return cls._serialize_relation(duplicate)
        prepared = MemoryService.prepare_relation(user, subject, predicate, object_text)
        with memory_write_lock(user):
            duplicate = MemoryService.find_exact_relation(user, character, subject, predicate, object_text)
            if duplicate:
                return cls._serialize_relation(duplicate)
            relation = MemoryRelation.objects.create(
                user=user,
                character=character,
                confidence_score=1.0,
                **MemoryService.relation_values(user, prepared),
            )
            advance_memory(user)
        logger.info(
            "User %s added memory relation %s via editor (character %s)",
            user.user_id,
            relation.memory_relation_id,
            character.character_id,
        )
        return cls._serialize_relation(relation)

    @classmethod
    def update_relation(
        cls,
        user: CustomUser,
        relation_id: str,
        subject: str,
        predicate: str,
        object_text: str,
        expected_updated_at=None,
    ) -> Dict:
        relation = cls._get_relation(user, relation_id)
        cls._check_relation_current(relation)
        cls._check_revision(relation, expected_updated_at)
        subject, predicate, object_text = cls._normalize_triple(subject, predicate, object_text)

        prepared = MemoryService.prepare_relation(user, subject, predicate, object_text)
        with memory_write_lock(user):
            current = cls._get_relation(user, relation_id, stale_if_missing=True)
            cls._check_relation_current(current)
            cls._check_revision(current, relation.updated_at.isoformat())
            old_nodes = [current.subject_id, current.object_entity_id]
            values = MemoryService.relation_values(user, prepared)
            for key, value in values.items():
                setattr(current, key, value)
            current.save(update_fields=[*values, "updated_at"])
            MemoryService.collect_orphan_entities(user, old_nodes)
            advance_memory(user)
            relation = current
        logger.info("User %s edited memory relation %s via editor", user.user_id, relation.memory_relation_id)
        return cls._serialize_relation(relation)

    @classmethod
    def delete_relation(cls, user: CustomUser, relation_id: str, expected_updated_at=None) -> Dict:
        with memory_write_lock(user):
            relation = cls._get_relation(user, relation_id)
            cls._check_relation_current(relation)
            cls._check_revision(relation, expected_updated_at)
            relation_id_str = str(relation.memory_relation_id)
            old_nodes = [relation.subject_id, relation.object_entity_id]
            relation.delete()
            MemoryService.collect_orphan_entities(user, old_nodes)
            advance_memory(user)
        logger.info("User %s deleted memory relation %s via editor", user.user_id, relation_id_str)
        return {"memory_relation_id": relation_id_str}

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    @classmethod
    def update_summary(
        cls,
        user: CustomUser,
        character: Character,
        summary_text: str,
        expected_updated_at=None,
        expected_summary_id=None,
    ) -> Dict:
        summary_text = cls._text(summary_text, "summary", MAX_SUMMARY_LENGTH)
        with memory_write_lock(user):
            summary = cls._current_summary(user, character)
            cls._check_summary(summary, expected_updated_at, expected_summary_id)
            if summary is None:
                raise MemoryEditorError("summary_not_found", status=404)
            summary.summary_text = summary_text
            summary.save(update_fields=["summary_text", "updated_at"])
            advance_summary(user, character)
        logger.info(
            "User %s edited conversation summary %s via editor (character %s)",
            user.user_id,
            summary.summary_id,
            character.character_id,
        )
        return cls._serialize_summary(summary)

    @classmethod
    def delete_summary(
        cls, user: CustomUser, character: Character, expected_updated_at=None, expected_summary_id=None
    ) -> Dict:
        with memory_write_lock(user):
            cls._check_summary(cls._current_summary(user, character), expected_updated_at, expected_summary_id)
            deleted, _ = ConversationSummary.objects.filter(
                conversation__user=user,
                conversation__character=character,
                is_current=True,
            ).delete()
            advance_summary(user, character)
        logger.info(
            "User %s deleted %d conversation summary row(s) via editor (character %s)",
            user.user_id,
            deleted,
            character.character_id,
        )
        return {"summaries_deleted": deleted}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @classmethod
    def _get_fact(cls, user: CustomUser, fact_id: str, stale_if_missing=False) -> MemoryFact:
        """
        Fetch a fact by id, ownership-checked. Filtering by ``user`` on the
        queryset (not after the fetch) means another user's fact id can never
        be loaded, mutated, or deleted here.
        """
        try:
            return MemoryFact.objects.get(memory_fact_id=fact_id, user=user)
        except (MemoryFact.DoesNotExist, ValueError, ValidationError):
            if stale_if_missing:
                raise MemoryEditorError("stale_memory", status=409)
            raise MemoryEditorError("fact_not_found", status=404)

    @classmethod
    def _get_relation(cls, user: CustomUser, relation_id: str, stale_if_missing=False) -> MemoryRelation:
        try:
            return MemoryRelation.objects.select_related("subject", "object_entity").get(
                memory_relation_id=relation_id, user=user
            )
        except (MemoryRelation.DoesNotExist, ValueError, ValidationError):
            if stale_if_missing:
                raise MemoryEditorError("stale_memory", status=409)
            raise MemoryEditorError("relation_not_found", status=404)

    @classmethod
    def _current_summary(cls, user: CustomUser, character: Character) -> Optional[ConversationSummary]:
        """
        The current rolling summary for this (user, character) pair — same
        lookup ConversationSummaryService.get_latest_summary uses, so the
        editor shows exactly the summary text injected into the prompt.
        """
        return (
            ConversationSummary.objects.filter(
                conversation__user=user,
                conversation__character=character,
                is_current=True,
            )
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def _embed_or_none(cls, text: str):
        """Best-effort embedding: NEVER let a provider failure block a user edit.

        (MemoryService._get_embedding already fails silent, but a settings
        misconfiguration could raise before its own try/except — belt and
        braces so the editor path can't 500 on an embedding problem.)
        """
        try:
            result = MemoryService._get_embedding(text, retry_on_overload=False)
        except Exception:
            logger.exception("Memory editor embedding failed — saving without vector")
            return None, ""
        if result is None:
            return None, ""
        return result

    @classmethod
    def _normalize_triple(cls, subject: str, predicate: str, object_text: str):
        for value, field in ((subject, "subject"), (predicate, "predicate"), (object_text, "object")):
            if not isinstance(value, str):
                raise MemoryEditorError("invalid_" + field)
        subject = subject.strip()
        predicate = predicate.strip().lower().replace(" ", "_")
        object_text = object_text.strip()
        if not subject:
            raise MemoryEditorError("invalid_subject")
        if not predicate:
            raise MemoryEditorError("invalid_predicate")
        if max(len(subject), len(MemoryEntity.normalize_name(subject))) > MAX_ENTITY_NAME_LENGTH:
            raise MemoryEditorError("subject_too_long")
        if len(predicate) > MAX_PREDICATE_LENGTH:
            raise MemoryEditorError("predicate_too_long")
        if max(len(object_text), len(MemoryEntity.normalize_name(object_text))) > MAX_ENTITY_NAME_LENGTH:
            raise MemoryEditorError("object_too_long")
        return subject, predicate, object_text

    @staticmethod
    def _text(value, field, maximum):
        if not isinstance(value, str):
            raise MemoryEditorError("invalid_" + field)
        value = value.strip()
        if not value:
            raise MemoryEditorError("empty_" + field)
        if len(value) > maximum:
            raise MemoryEditorError(field + "_too_long")
        return value

    @staticmethod
    def _check_revision(row, expected):
        if expected is not None:
            if not isinstance(expected, str):
                raise MemoryEditorError("invalid_expected_updated_at")
            if row is None or row.updated_at.isoformat() != expected:
                raise MemoryEditorError("stale_memory", status=409)

    @classmethod
    def _check_summary(cls, summary, expected_updated_at, expected_summary_id):
        cls._check_revision(summary, expected_updated_at)
        if expected_summary_id is not None:
            if not isinstance(expected_summary_id, str):
                raise MemoryEditorError("invalid_expected_summary_id")
            if summary is None or str(summary.pk) != expected_summary_id:
                raise MemoryEditorError("stale_memory", status=409)

    @staticmethod
    def _check_fact_current(fact):
        if (
            not fact.is_active
            or fact.valid_to is not None
            or (fact.expires_at is not None and fact.expires_at <= timezone.now())
        ):
            raise MemoryEditorError("stale_memory", status=409)

    @staticmethod
    def _check_relation_current(relation):
        if relation.invalidated_at is not None or (
            relation.valid_to is not None and relation.valid_to <= timezone.now()
        ):
            raise MemoryEditorError("stale_memory", status=409)

    @staticmethod
    def _serialize_fact(fact: MemoryFact) -> Dict:
        return {
            "memory_fact_id": str(fact.memory_fact_id),
            "fact_text": fact.fact_text,
            "fact_type": fact.fact_type,
            "importance_score": fact.importance_score,
            "confidence_score": float(fact.confidence_score),
            # character=NULL facts are user-global: visible to every character.
            "shared": fact.character_id is None,
            "extracted_at": fact.extracted_at.isoformat(),
            "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
            "updated_at": fact.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_relation(relation: MemoryRelation) -> Dict:
        return {
            "memory_relation_id": str(relation.memory_relation_id),
            "subject": relation.subject.name if relation.subject_id else "",
            "predicate": relation.predicate,
            "object": (relation.object_entity.name if relation.object_entity_id else (relation.object_text or "")),
            "shared": relation.character_id is None,
            "extracted_at": relation.extracted_at.isoformat(),
            "updated_at": relation.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_summary(summary: ConversationSummary) -> Dict:
        return {
            "summary_id": str(summary.summary_id),
            "summary_text": summary.summary_text,
            "created_at": summary.created_at.isoformat(),
            "updated_at": summary.updated_at.isoformat(),
        }
