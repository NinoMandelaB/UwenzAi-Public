"""Offline regressions for Sep-10 memory/editor and summary races."""

import json
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from django.core.cache import cache
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from capp.classes.memory.memory_editor_service import MemoryEditorError, MemoryEditorService as Editor
from capp.classes.memory.memory_service import MemoryService as Memory
from capp.classes.summary.summary_service import ConversationSummaryService as Summaries
from capp.models import Conversation, ConversationSummary, MemoryEntity, MemoryFact, MemoryRelation, Message
from capp.tests import _make_character, _make_user
from capp.views import memory_views as views

LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def response(text="automatic summary"):
    return {"choices": [{"message": {"content": text}}]}


@override_settings(CACHES=LOCAL_CACHE)
class MemoryHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.factory = APIRequestFactory()
        patcher = mock.patch.object(Memory, "_get_embedding", return_value=None)
        self.embedding = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, view, method="post", payload=None, user=None, **kwargs):
        request = getattr(self.factory, method)("/", payload if payload is not None else {}, format="json")
        force_authenticate(request, user=user or self.user)
        return view(request, **kwargs)

    def fact(self, text="original fact", **kwargs):
        return MemoryFact.objects.create(user=self.user, character=self.character, fact_text=text, **kwargs)

    def summary(self, text="original summary"):
        return ConversationSummary.objects.create(conversation=self.conversation, summary_text=text, is_current=True)

    def message(self, text="source", conversation=None):
        return Message.objects.create(
            conversation=conversation or self.conversation,
            content=text,
            sender_type=Message.SenderType.USER,
        )

    def relation(self, subject="Sensitive person", obj="Sensitive treatment"):
        payload = Editor.create_relation(self.user, self.character, subject, "receives", obj)
        return MemoryRelation.objects.get(pk=payload["memory_relation_id"])

    def assert_conflict(self, call):
        with self.assertRaises(MemoryEditorError) as caught:
            call()
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "stale_memory"))

    def test_summary_exact_character_limit_and_multilingual_overflow(self):
        self.summary()
        for text in ("x" * 12000, "你好！" * 4000, ".,!?" * 3000):
            result = self.request(
                views.api_update_memory_summary,
                "patch",
                {"summary_text": text},
                character_id=self.character.pk,
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data["data"]["summary_text"], text)
        result = self.request(
            views.api_update_memory_summary,
            "patch",
            {"summary_text": "好" * 12001},
            character_id=self.character.pk,
        )
        self.assertEqual((result.status_code, result.data["data"]), (400, "summary_too_long"))

    def test_malformed_root_shapes_return_400(self):
        for value in ([], ["x"], True, 2, "text"):
            result = self.request(views.api_create_memory_fact, payload=value, character_id=self.character.pk)
            self.assertEqual(result.status_code, 400, repr(value))
        self.embedding.assert_not_called()

    def test_all_field_type_errors_return_400_before_embedding(self):
        fact = self.fact()
        relation = self.relation()
        self.summary()
        self.embedding.reset_mock()
        calls = [
            (
                views.api_create_memory_fact,
                "post",
                {"fact_text": "x"},
                "fact_text",
                {"character_id": self.character.pk},
            ),
            (
                views.api_create_memory_fact,
                "post",
                {"fact_text": "x"},
                "fact_type",
                {"character_id": self.character.pk},
            ),
            (views.api_update_memory_fact, "patch", {}, "fact_text", {"fact_id": fact.pk}),
            (views.api_update_memory_summary, "patch", {}, "summary_text", {"character_id": self.character.pk}),
        ]
        for view, method, payload, field, kwargs in calls:
            for bad in ([], {}, True, 1.5, None):
                with self.subTest(field=field, bad=bad):
                    result = self.request(view, method, {**payload, field: bad}, **kwargs)
                    self.assertEqual(result.status_code, 400)
        for field in ("subject", "predicate", "object"):
            for bad in ([], {}, True, 1, None):
                result = self.request(
                    views.api_update_memory_relation,
                    "patch",
                    {**dict(subject="s", predicate="p", object="o"), field: bad},
                    relation_id=relation.pk,
                )
                self.assertEqual(result.status_code, 400)
        self.embedding.assert_not_called()

    def test_importance_rejects_non_integer_without_provider_work(self):
        for bad in ([], {}, True, 1.5, None, "7"):
            with self.assertRaises(MemoryEditorError):
                Editor.create_fact(self.user, self.character, "x", importance_score=bad)
        self.embedding.assert_not_called()

    def test_normalized_unicode_lengths_cannot_leave_partial_graph(self):
        for values in (("new subject", "p", "İ" * 120), ("İ" * 120, "p", "object"), ("s", "İ" * 60, "o")):
            with self.assertRaises(MemoryEditorError):
                Editor.create_relation(self.user, self.character, *values)
        self.assertFalse(MemoryEntity.objects.exists())
        self.assertFalse(MemoryRelation.objects.exists())
        self.embedding.assert_not_called()

    def test_graph_persistence_is_atomic_if_edge_save_fails(self):
        with mock.patch.object(MemoryRelation.objects, "create", side_effect=RuntimeError("db failure")):
            with self.assertRaises(RuntimeError):
                Editor.create_relation(self.user, self.character, "new subject", "p", "new object")
        self.assertEqual(MemoryEntity.objects.count(), 0)

    def test_owner_and_default_off_checks_remain_enforced(self):
        fact = self.fact()
        outsider = _make_user(username="outsider", email="outsider@example.test")
        result = self.request(
            views.api_update_memory_fact, "patch", {"fact_text": "attack"}, user=outsider, fact_id=fact.pk
        )
        self.assertEqual(result.status_code, 404)
        self.user.feature_preferences = {}
        self.user.save(update_fields=["feature_preferences"])
        result = self.request(views.api_update_memory_fact, "patch", {"fact_text": "attack"}, fact_id=fact.pk)
        self.assertEqual(result.status_code, 403)

    def test_superseded_expired_and_inactive_facts_conflict(self):
        for kwargs in ({"valid_to": timezone.now()}, {"expires_at": timezone.now()}, {"is_active": False}):
            fact = self.fact(**kwargs)
            result = self.request(views.api_update_memory_fact, "patch", {"fact_text": "incorrect"}, fact_id=fact.pk)
            self.assertEqual(result.status_code, 409)
            fact.refresh_from_db()
            self.assertEqual(fact.fact_text, "original fact")
        self.embedding.assert_not_called()

    def test_supersession_during_embedding_conflicts(self):
        old = self.fact()

        def supersede(*args, **kwargs):
            current = self.fact("current successor")
            Memory._supersede_fact(str(old.pk), current, timezone.now())
            return None, ""

        with mock.patch.object(Editor, "_embed_or_none", side_effect=supersede):
            self.assert_conflict(lambda: Editor.update_fact(self.user, old.pk, "stale edit"))
        old.refresh_from_db()
        self.assertEqual(old.fact_text, "original fact")
        self.assertIsNotNone(old.valid_to)

    def test_two_edits_during_embedding_do_not_silently_overwrite(self):
        fact = self.fact()

        def competing_edit(*args, **kwargs):
            with mock.patch.object(Editor, "_embed_or_none", return_value=(None, "")):
                Editor.update_fact(self.user, fact.pk, "acknowledged correction")
            return None, ""

        with mock.patch.object(Editor, "_embed_or_none", side_effect=competing_edit):
            self.assert_conflict(lambda: Editor.update_fact(self.user, fact.pk, "late stale edit"))
        fact.refresh_from_db()
        self.assertEqual(fact.fact_text, "acknowledged correction")

    def test_deletion_during_embedding_conflicts_without_recreating_fact(self):
        fact = self.fact()
        with mock.patch.object(
            Editor, "_embed_or_none", side_effect=lambda *a: (Editor.delete_fact(self.user, fact.pk) and (None, ""))
        ):
            self.assert_conflict(lambda: Editor.update_fact(self.user, fact.pk, "late edit"))
        self.assertFalse(MemoryFact.objects.exists())

    def test_optional_version_preconditions_detect_stale_clients(self):
        fact = self.fact()
        token = fact.updated_at.isoformat()
        Editor.update_fact(self.user, fact.pk, "first", expected_updated_at=token)
        self.assert_conflict(lambda: Editor.update_fact(self.user, fact.pk, "second", expected_updated_at=token))
        self.assert_conflict(lambda: Editor.delete_fact(self.user, fact.pk, expected_updated_at=token))
        summary = self.summary()
        Editor.update_summary(
            self.user,
            self.character,
            "first",
            expected_summary_id=str(summary.pk),
            expected_updated_at=summary.updated_at.isoformat(),
        )
        self.assert_conflict(
            lambda: Editor.update_summary(
                self.user, self.character, "second", expected_updated_at=summary.updated_at.isoformat()
            )
        )

    def test_relation_invalidated_or_expired_conflicts(self):
        relation = self.relation()
        for field in ("invalidated_at", "valid_to"):
            MemoryRelation.objects.filter(pk=relation.pk).update(**{field: timezone.now()})
            self.assert_conflict(lambda: Editor.update_relation(self.user, relation.pk, "s", "p", "o"))
            MemoryRelation.objects.filter(pk=relation.pk).update(**{field: None})

    def test_relation_delete_collects_only_unreferenced_nodes(self):
        relation = self.relation()
        Editor.delete_relation(self.user, relation.pk)
        self.assertEqual(MemoryEntity.objects.count(), 0)
        self.assertEqual(Memory._build_entity_snapshot(self.user), [])

    def test_relation_replacement_collects_abandoned_endpoint(self):
        relation = self.relation()
        old_id = relation.object_entity_id
        Editor.update_relation(self.user, relation.pk, relation.subject.name, "receives", "corrected object")
        self.assertFalse(MemoryEntity.objects.filter(pk=old_id).exists())
        self.assertNotIn("Sensitive treatment", Memory._build_entity_snapshot(self.user))

    def test_shared_and_historical_graph_references_prevent_cascade(self):
        relation = self.relation()
        other = _make_character(name="Other", slug="other")
        live = MemoryRelation.objects.create(
            user=self.user,
            character=other,
            subject=relation.subject,
            predicate="knows",
            object_text="shared literal",
        )
        historical = MemoryRelation.objects.create(
            user=self.user,
            character=other,
            subject=relation.object_entity,
            predicate="historical",
            invalidated_at=timezone.now(),
        )
        Editor.delete_relation(self.user, relation.pk)
        self.assertEqual(MemoryRelation.objects.count(), 2)
        self.assertTrue(MemoryEntity.objects.filter(pk=live.subject_id).exists())
        self.assertTrue(MemoryEntity.objects.filter(pk=historical.subject_id).exists())
        self.assertEqual(Memory._build_entity_snapshot(self.user), ["Sensitive person"])

    def test_relation_update_racing_delete_creates_no_orphan_nodes(self):
        relation = self.relation()
        original_prepare = Memory.prepare_relation

        def prepare_then_delete(*args, **kwargs):
            prepared = original_prepare(*args, **kwargs)
            Editor.delete_relation(self.user, relation.pk)
            return prepared

        with mock.patch.object(Memory, "prepare_relation", side_effect=prepare_then_delete):
            self.assert_conflict(lambda: Editor.update_relation(self.user, relation.pk, "new", "p", "nodes"))
        self.assertEqual(MemoryEntity.objects.count(), 0)

    def test_manual_additions_deduplicate_before_reembedding(self):
        first = Editor.create_fact(self.user, self.character, "I like café")
        second = Editor.create_fact(self.user, self.character, " I  like cafe\u0301 ")
        self.assertEqual(first["memory_fact_id"], second["memory_fact_id"])
        self.assertEqual(self.embedding.call_count, 1)
        self.assertEqual(MemoryFact.objects.count(), 1)
        self.embedding.reset_mock()
        first = Editor.create_relation(self.user, self.character, "The user", "likes", "tea")
        self.embedding.reset_mock()
        second = Editor.create_relation(self.user, self.character, "the USER", "likes", "TEA")
        self.assertEqual(first["memory_relation_id"], second["memory_relation_id"])
        self.embedding.assert_not_called()

    def test_legacy_ciphertext_dedup_and_character_scope(self):
        legacy = self.fact("exact old fact")
        result = Editor.create_fact(self.user, self.character, "exact old fact")
        self.assertEqual(result["memory_fact_id"], str(legacy.pk))
        self.embedding.assert_not_called()
        other = _make_character(name="Other", slug="other")
        result = Editor.create_fact(self.user, other, "exact old fact")
        self.assertNotEqual(result["memory_fact_id"], str(legacy.pk))

    def test_addition_throttle_does_not_apply_to_listing_or_automatic_extraction(self):
        for i in range(20):
            result = self.request(
                views.api_create_memory_fact, payload={"fact_text": f"manual {i}"}, character_id=self.character.pk
            )
            self.assertEqual(result.status_code, 201)
        result = self.request(
            views.api_create_memory_fact, payload={"fact_text": "blocked"}, character_id=self.character.pk
        )
        self.assertEqual(result.status_code, 429)
        self.assertEqual(self.request(views.api_list_memory, "get", character_id=self.character.pk).status_code, 200)
        message = self.message()
        with mock.patch.object(
            Memory,
            "_extraction_chat_completion",
            return_value=json.dumps({"facts": [{"fact_text": "automatic after throttle"}]}),
        ):
            Memory.extract_and_save_facts(self.conversation, message, message)
        self.assertEqual(MemoryFact.objects.count(), 21)

    def test_pagination_is_bounded_and_keeps_existing_payload_fields(self):
        MemoryFact.objects.bulk_create(
            [MemoryFact(user=self.user, character=self.character, fact_text=f"stored {i}") for i in range(125)]
        )
        self.summary()
        for kwargs in ({}, {"page": 2, "page_size": 100}, {"page": 8}):
            data = Editor.list_memory(self.user, self.character, **kwargs)
            self.assertLessEqual(len(data["facts"]), kwargs.get("page_size", 50))
            self.assertEqual(data["pagination"]["facts"]["total"], 125)
            self.assertIsInstance(data["relations"], list)
            self.assertEqual(data["summary"]["summary_text"], "original summary")
        first = Editor.list_memory(self.user, self.character)
        second = Editor.list_memory(self.user, self.character, page=2)
        self.assertFalse({f["memory_fact_id"] for f in first["facts"]} & {f["memory_fact_id"] for f in second["facts"]})
        for kwargs in ({"page": "x"}, {"page": 0}, {"page": 1.5}, {"page_size": 101}, {"page_size": -1}):
            with self.assertRaises(MemoryEditorError):
                Editor.list_memory(self.user, self.character, **kwargs)

    def test_summary_history_windows_59_60_61_and_large(self):
        for count in (59, 60, 61, 125):
            with self.subTest(count=count):
                Message.objects.all().delete()
                ConversationSummary.objects.all().delete()
                self.summary()
                messages = [self.message(f"UNIQUE_MESSAGE_{i:03d}") for i in range(count)]
                with mock.patch(
                    "capp.classes.summary.summary_service.MistralClient.chat_completion", return_value=response()
                ) as model:
                    with CaptureQueriesContext(connection) as queries:
                        Summaries.generate_summary(self.conversation)
                model.assert_called_once()
                prompt = model.call_args.kwargs["messages"][0]["content"]
                self.assertEqual(prompt.count("UNIQUE_MESSAGE_"), min(count, 60))
                message_table = connection.ops.quote_name(Message._meta.db_table)
                self.assertTrue(
                    any(
                        f"FROM {message_table}" in q["sql"] and "LIMIT 60" in q["sql"] for q in queries.captured_queries
                    )
                )
                self.assertIn(messages[-1].content, prompt)
                if count > 60:
                    self.assertNotIn(messages[0].content, prompt)
                summary = Summaries.get_latest_summary(self.conversation)
                self.assertEqual(summary.covered_until_message_id, messages[-1].pk)
                self.assertEqual(ConversationSummary.objects.filter(is_current=True).count(), 1)

    def test_summary_inflight_edit_cannot_overwrite_acknowledged_correction(self):
        self.message()
        self.summary()

        def edit(*args, **kwargs):
            Editor.update_summary(self.user, self.character, "USER_CORRECTION")
            return response("STALE_AUTOMATIC")

        with mock.patch("capp.classes.summary.summary_service.MistralClient.chat_completion", side_effect=edit):
            Summaries.generate_summary(self.conversation)
        self.assertEqual(Summaries.get_latest_summary(self.conversation).summary_text, "USER_CORRECTION")

    def test_summary_inflight_delete_or_reset_cannot_resurrect(self):
        for action in (Editor.delete_summary, Memory.clear_memory_for_character):
            self.message()
            self.summary()

            def delete(*args, **kwargs):
                action(self.user, self.character)
                return response("STALE_AUTOMATIC")

            with mock.patch("capp.classes.summary.summary_service.MistralClient.chat_completion", side_effect=delete):
                Summaries.generate_summary(self.conversation)
            self.assertIsNone(Summaries.get_latest_summary(self.conversation))

    def test_summary_two_generators_across_conversations_have_one_winner(self):
        self.message()
        other = Conversation.objects.create(user=self.user, character=self.character)

        def competing(*args, **kwargs):
            with mock.patch(
                "capp.classes.summary.summary_service.MistralClient.chat_completion",
                return_value=response("newer winner"),
            ):
                Summaries.generate_summary(other)
            return response("stale loser")

        with mock.patch("capp.classes.summary.summary_service.MistralClient.chat_completion", side_effect=competing):
            Summaries.generate_summary(self.conversation)
        self.assertEqual(Summaries.get_latest_summary(self.conversation).summary_text, "newer winner")
        self.assertEqual(ConversationSummary.objects.filter(is_current=True).count(), 1)

    def test_summary_tombstone_excludes_old_source_even_in_new_conversation(self):
        self.message("SENSITIVE_OLD_SOURCE")
        self.summary()
        Editor.delete_summary(self.user, self.character)
        other = Conversation.objects.create(user=self.user, character=self.character)
        with mock.patch(
            "capp.classes.summary.summary_service.MistralClient.chat_completion", return_value=response()
        ) as model:
            Summaries.generate_summary(other)
            model.assert_not_called()
            self.message("NEW_SOURCE", conversation=other)
            Summaries.generate_summary(other)
        prompt = model.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("SENSITIVE_OLD_SOURCE", prompt)
        self.assertIn("NEW_SOURCE", prompt)
        with mock.patch.object(Summaries, "generate_summary") as generate:
            Summaries.maybe_generate_summary(other, min_messages_since_last=1)
        generate.assert_not_called()

    def test_extraction_started_before_manual_edit_is_discarded(self):
        fact = self.fact()
        message = self.message()

        def model(*args, **kwargs):
            Editor.update_fact(self.user, fact.pk, "acknowledged manual correction")
            return json.dumps(
                {
                    "facts": [{"fact_text": "stale automatic correction", "supersedes": str(fact.pk)}],
                    "relations": [{"subject": "stale", "predicate": "has", "object": "data"}],
                }
            )

        with mock.patch.object(Memory, "_extraction_chat_completion", side_effect=model):
            Memory.extract_and_save_facts(self.conversation, message, message)
        fact.refresh_from_db()
        self.assertEqual(fact.fact_text, "acknowledged manual correction")
        self.assertIsNone(fact.valid_to)
        self.assertEqual(MemoryFact.objects.count(), 1)
        self.assertEqual(MemoryEntity.objects.count(), 0)

    def test_extraction_relation_only_started_before_delete_is_discarded(self):
        relation = self.relation()
        message = self.message()

        def model(*args, **kwargs):
            Editor.delete_relation(self.user, relation.pk)
            return json.dumps(
                {
                    "relations": [
                        {"subject": "Sensitive person", "predicate": "receives", "object": "Sensitive treatment"}
                    ]
                }
            )

        with mock.patch.object(Memory, "_extraction_chat_completion", side_effect=model):
            Memory.extract_and_save_facts(self.conversation, message, message)
        self.assertEqual(MemoryEntity.objects.count(), 0)
        self.assertEqual(MemoryRelation.objects.count(), 0)

    def test_automatic_extraction_has_no_stored_count_quota_and_handles_missing_vector(self):
        MemoryFact.objects.bulk_create(
            [MemoryFact(user=self.user, character=self.character, fact_text=f"stored {i}") for i in range(150)]
        )
        message = self.message()
        with mock.patch.object(
            Memory,
            "_extraction_chat_completion",
            return_value=json.dumps({"facts": [{"fact_text": "automatic number 151"}]}),
        ):
            Memory.extract_and_save_facts(self.conversation, message, message)
        self.assertEqual(MemoryFact.objects.count(), 151)
        self.assertTrue(MemoryFact.objects.filter(content_key=Memory.fact_content_key("automatic number 151")).exists())

    def test_vector_dedup_failure_does_not_break_transaction(self):
        with transaction.atomic():
            with mock.patch("capp.classes.memory.memory_service.CosineDistance", side_effect=RuntimeError("no vector")):
                self.assertFalse(Memory._is_duplicate(self.user, "new", [0.1] * 1024))
            self.fact()
        self.assertEqual(MemoryFact.objects.count(), 1)

    def test_missing_embedding_clears_stale_fact_vector_on_edit(self):
        fact = self.fact(embedding=[0.1] * 1024, embedding_model="old-model")
        Editor.update_fact(self.user, fact.pk, "corrected fact")
        fact.refresh_from_db()
        self.assertIsNone(fact.embedding)
        self.assertEqual(fact.embedding_model, "")

    def test_bounded_current_fact_helper_for_reminder_integration(self):
        self.fact("current for this character")
        MemoryFact.objects.create(user=self.user, character=None, fact_text="current shared")
        self.fact("superseded", valid_to=timezone.now(), importance_score=10)
        self.fact("expired", expires_at=timezone.now(), importance_score=10)
        self.fact("inactive", is_active=False, importance_score=10)
        other = _make_character(name="Other", slug="other")
        MemoryFact.objects.create(user=self.user, character=other, fact_text="other character")
        outsider = _make_user(username="outsider", email="outsider@example.test")
        MemoryFact.objects.create(user=outsider, character=self.character, fact_text="other user")
        with mock.patch("capp.classes.ai.token_utils.TokenUtils.count_tokens", return_value=5):
            facts = Memory.get_active_facts(self.user, self.character, limit=5, max_tokens=300)
        self.assertEqual({fact["fact_text"] for fact in facts}, {"current for this character", "current shared"})

    def test_manual_edit_during_extraction_embedding_fences_commit(self):
        fact = self.fact()
        message = self.message()

        def edit_then_embed(*args, **kwargs):
            with mock.patch.object(Editor, "_embed_or_none", return_value=(None, "")):
                Editor.update_fact(self.user, fact.pk, "corrected during embedding")
            return None

        with mock.patch.object(Memory, "_get_embedding", side_effect=edit_then_embed), mock.patch.object(
            Memory,
            "_extraction_chat_completion",
            return_value=json.dumps({"facts": [{"fact_text": "stale from model", "supersedes": str(fact.pk)}]}),
        ):
            Memory.extract_and_save_facts(self.conversation, message, message)
        fact.refresh_from_db()
        self.assertEqual(fact.fact_text, "corrected during embedding")
        self.assertIsNone(fact.valid_to)
        self.assertEqual(MemoryFact.objects.count(), 1)


@override_settings(CACHES=LOCAL_CACHE)
class MemoryConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL transactions, not SQLite locking simulations."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks")
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)

    def test_parallel_fact_edits_one_success_one_conflict(self):
        fact = MemoryFact.objects.create(user=self.user, character=self.character, fact_text="initial")
        barrier = Barrier(2)

        def embed(text):
            barrier.wait(timeout=10)
            return None, ""

        def edit(text):
            close_old_connections()
            try:
                Editor.update_fact(self.user, fact.pk, text)
                return 200
            except MemoryEditorError as exc:
                return exc.status
            finally:
                close_old_connections()

        with mock.patch.object(Editor, "_embed_or_none", side_effect=embed):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(edit, ("first", "second")))
        self.assertEqual(sorted(outcomes), [200, 409])

    def test_parallel_relation_additions_dedup_atomically(self):
        barrier = Barrier(2)
        original = Memory.prepare_relation

        def prepare(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(timeout=10)
            return result

        def add(_):
            close_old_connections()
            try:
                return Editor.create_relation(self.user, self.character, "Shared person", "knows", "Shared object")
            finally:
                close_old_connections()

        with mock.patch.object(Memory, "_get_embedding", return_value=None), mock.patch.object(
            Memory, "prepare_relation", side_effect=prepare
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(add, range(2)))
        self.assertEqual(results[0]["memory_relation_id"], results[1]["memory_relation_id"])
        self.assertEqual(MemoryRelation.objects.count(), 1)
        self.assertEqual(MemoryEntity.objects.count(), 2)

    def test_parallel_relation_deletes_collect_shared_orphan_only_after_last_edge(self):
        with mock.patch.object(Memory, "_get_embedding", return_value=None):
            first = Editor.create_relation(self.user, self.character, "common", "knows", "first")
            second = Editor.create_relation(self.user, self.character, "common", "knows", "second")
        barrier = Barrier(2)

        def delete(relation_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                Editor.delete_relation(self.user, relation_id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(delete, [first["memory_relation_id"], second["memory_relation_id"]]))
        self.assertEqual(MemoryRelation.objects.count(), 0)
        self.assertEqual(MemoryEntity.objects.count(), 0)

    def test_parallel_summary_generation_across_conversations_has_one_current_row(self):
        Message.objects.create(conversation=self.conversation, sender_type=Message.SenderType.USER, content="source")
        other = Conversation.objects.create(user=self.user, character=self.character)
        barrier = Barrier(2)

        def model(*args, **kwargs):
            barrier.wait(timeout=10)
            return response()

        def generate(conversation):
            close_old_connections()
            try:
                Summaries.generate_summary(conversation)
            finally:
                close_old_connections()

        with mock.patch("capp.classes.summary.summary_service.MistralClient.chat_completion", side_effect=model):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(generate, [self.conversation, other]))
        self.assertEqual(ConversationSummary.objects.count(), 1)
        self.assertEqual(ConversationSummary.objects.filter(is_current=True).count(), 1)
