"""
Tests for the two behaviour changes shipped in this PR:

1. Entity + relation embeddings are populated on save (previously NULL, which
   silently disabled ``retrieve_graph_context`` in production).
2. Memory extraction routes through Cortecs first with Mistral fallback,
   controlled by the new ``CORTECS_MEM_MODEL`` env var.

We deliberately do NOT re-run the older Phase 1/2/3 temporal-memory
behaviour suite here: those tests exercise pgvector cosine similarity on
hand-crafted 1024-dim vectors, which is not numerically load-bearing on
this project's CI and which caused a Django parallel-runner pickling
crash that masked real assertions. The behaviour they cover is exercised
end-to-end through the production extraction path in staging.
"""

import json
from unittest import mock

from django.test import TestCase

from capp.classes.memory.memory_service import MemoryService
from capp.models import Conversation, MemoryEntity, MemoryRelation, Message
from capp.tests import _make_character, _make_message, _make_user


def _fake_embedding_1024(seed: float = 0.1) -> list:
    """Deterministic 1024-dim vector."""
    return [seed] * 1024


def _extraction_response(payload) -> str:
    """Serialize a payload the way the LLM would return it."""
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Embedding population (bug fix: entities/relations were saved with NULL embedding)
# ---------------------------------------------------------------------------


class GraphEmbeddingPopulationTests(TestCase):
    """
    Regression tests for the entity+relation embedding population fix.

    Before this fix ``MemoryEntity.embedding`` and ``MemoryRelation.embedding``
    were left NULL on every extraction, which silently disabled Phase 3
    ``retrieve_graph_context`` (its ``.filter(embedding__isnull=False)``
    clause dropped every row). These tests lock in that both fields are
    populated on save.
    """

    def setUp(self):
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.user_message = _make_message(self.conversation, Message.SenderType.USER, "Zawadi lives in Nairobi.")
        self.ai_message = _make_message(self.conversation, Message.SenderType.AI, "Nice, tell me more.")

    def test_entities_and_relations_get_embeddings_on_save(self):
        payload = {
            "facts": [],
            "relations": [
                {
                    "subject": "Zawadi",
                    "subject_type": "person",
                    "predicate": "lives_in",
                    "object": "Nairobi",
                    "object_type": "place",
                }
            ],
        }
        with mock.patch.object(
            MemoryService, "_get_embedding", return_value=(_fake_embedding_1024(0.3), "test-embed")
        ), mock.patch.object(MemoryService, "_extraction_chat_completion", return_value=_extraction_response(payload)):
            MemoryService.extract_and_save_facts(
                conversation=self.conversation,
                user_message=self.user_message,
                ai_message=self.ai_message,
            )

        subj = MemoryEntity.objects.get(user=self.user, normalized_name="zawadi")
        obj = MemoryEntity.objects.get(user=self.user, normalized_name="nairobi")
        rel = MemoryRelation.objects.get(user=self.user)

        # Entity embeddings populated (this was the reported bug).
        self.assertIsNotNone(subj.embedding, "MemoryEntity.embedding must be populated on save")
        self.assertEqual(subj.embedding_model, "test-embed")
        self.assertIsNotNone(obj.embedding)
        self.assertEqual(obj.embedding_model, "test-embed")

        # Relation embeddings populated (retrieval depends on this).
        self.assertIsNotNone(rel.embedding, "MemoryRelation.embedding must be populated on save")
        self.assertEqual(rel.embedding_model, "test-embed")

    def test_relation_triple_text_helper_renders_snake_case_predicate(self):
        subj = MemoryEntity.objects.create(
            user=self.user,
            name="Zawadi",
            normalized_name="zawadi",
            entity_type=MemoryEntity.EntityType.PERSON,
        )
        obj = MemoryEntity.objects.create(
            user=self.user,
            name="Nairobi",
            normalized_name="nairobi",
            entity_type=MemoryEntity.EntityType.PLACE,
        )
        text = MemoryService._relation_triple_text(subj, "lives_in", obj, None)
        self.assertEqual(text, "Zawadi lives in Nairobi")

        # Object-text fallback (no object_entity).
        text2 = MemoryService._relation_triple_text(subj, "works_as", None, "nurse")
        self.assertEqual(text2, "Zawadi works as nurse")

    def test_embedding_failure_still_persists_entity_and_relation(self):
        """Fail-silent: rows must be saved even when the embedding backend errors out."""
        payload = {
            "facts": [],
            "relations": [
                {
                    "subject": "Amina",
                    "subject_type": "person",
                    "predicate": "sibling_of",
                    "object": "Zawadi",
                    "object_type": "person",
                }
            ],
        }
        with mock.patch.object(MemoryService, "_get_embedding", return_value=None), mock.patch.object(
            MemoryService, "_extraction_chat_completion", return_value=_extraction_response(payload)
        ):
            MemoryService.extract_and_save_facts(
                conversation=self.conversation,
                user_message=self.user_message,
                ai_message=self.ai_message,
            )
        # Entities still exist; embedding is simply not set.
        self.assertEqual(MemoryEntity.objects.filter(user=self.user).count(), 2)
        self.assertEqual(MemoryRelation.objects.filter(user=self.user).count(), 1)
        rel = MemoryRelation.objects.get(user=self.user)
        self.assertIsNone(rel.embedding)


# ---------------------------------------------------------------------------
# Extraction provider router (Cortecs primary, Mistral fallback)
# ---------------------------------------------------------------------------


class ExtractionRouterTests(TestCase):
    """Cover the new Cortecs-first / Mistral-fallback ``_extraction_chat_completion``."""

    def _fake_cortecs_response(self, content: str):
        """Mimic the OpenAI SDK response shape used by the Cortecs branch."""
        choice = mock.Mock()
        choice.message = mock.Mock()
        choice.message.content = content
        response = mock.Mock()
        response.choices = [choice]
        return response

    def test_cortecs_success_returns_its_content_and_skips_mistral(self):
        fake_client_cls = mock.MagicMock()
        fake_client = mock.MagicMock()
        fake_client_cls.return_value = fake_client
        fake_client.chat.completions.create.return_value = self._fake_cortecs_response('{"ok": true}')

        with mock.patch("openai.OpenAI", fake_client_cls), mock.patch(
            "capp.classes.memory.memory_service.settings"
        ) as fake_settings, mock.patch(
            "capp.classes.memory.memory_service.MistralClient.chat_completion"
        ) as mistral_mock:
            fake_settings.CORTECS_API_KEY = "sk-cortecs"
            fake_settings.CORTECS_BASE_URL = "https://api.cortecs.ai/v1"
            fake_settings.CORTECS_MEM_MODEL = "deepseek-v4-flash-0731"

            result = MemoryService._extraction_chat_completion(
                messages=[{"role": "user", "content": "extract"}],
                max_tokens=900,
                temperature=0.2,
            )

        self.assertEqual(result, '{"ok": true}')
        fake_client.chat.completions.create.assert_called_once()
        # The model actually sent to Cortecs must be the memory-tier model.
        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "deepseek-v4-flash-0731")
        mistral_mock.assert_not_called()

    def test_falls_back_to_mistral_when_cortecs_raises(self):
        fake_client_cls = mock.MagicMock()
        fake_client = mock.MagicMock()
        fake_client_cls.return_value = fake_client
        fake_client.chat.completions.create.side_effect = RuntimeError("cortecs down")

        with mock.patch("openai.OpenAI", fake_client_cls), mock.patch(
            "capp.classes.memory.memory_service.settings"
        ) as fake_settings, mock.patch(
            "capp.classes.memory.memory_service.MistralClient.chat_completion",
            return_value={"choices": [{"message": {"content": '{"from": "mistral"}'}}]},
        ) as mistral_mock:
            fake_settings.CORTECS_API_KEY = "sk-cortecs"
            fake_settings.CORTECS_BASE_URL = "https://api.cortecs.ai/v1"
            fake_settings.CORTECS_MEM_MODEL = "deepseek-v4-flash-0731"

            result = MemoryService._extraction_chat_completion(
                messages=[{"role": "user", "content": "extract"}],
                max_tokens=900,
                temperature=0.2,
            )

        self.assertEqual(result, '{"from": "mistral"}')
        mistral_mock.assert_called_once()

    def test_skips_cortecs_when_no_api_key(self):
        with mock.patch("capp.classes.memory.memory_service.settings") as fake_settings, mock.patch(
            "capp.classes.memory.memory_service.MistralClient.chat_completion",
            return_value={"choices": [{"message": {"content": '{"only": "mistral"}'}}]},
        ) as mistral_mock:
            fake_settings.CORTECS_API_KEY = None

            result = MemoryService._extraction_chat_completion(
                messages=[{"role": "user", "content": "extract"}],
                max_tokens=900,
                temperature=0.2,
            )

        self.assertEqual(result, '{"only": "mistral"}')
        mistral_mock.assert_called_once()

    def test_total_failure_returns_none_and_never_raises(self):
        with mock.patch("capp.classes.memory.memory_service.settings") as fake_settings, mock.patch(
            "capp.classes.memory.memory_service.MistralClient.chat_completion",
            side_effect=RuntimeError("mistral down"),
        ):
            fake_settings.CORTECS_API_KEY = None
            result = MemoryService._extraction_chat_completion(
                messages=[{"role": "user", "content": "extract"}],
                max_tokens=900,
                temperature=0.2,
            )
        self.assertIsNone(result)
