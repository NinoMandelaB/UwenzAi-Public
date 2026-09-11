# capp/tests_memory_editor.py
"""
Tests for the user-facing memory editor REST endpoints
(/api/v1/memory/editor/...) — the "brain" popup in the chat UI.

Verifies, above all, ISOLATION:
- A user only ever sees their own facts/relations/summaries.
- Character scoping: another character's facts/relations are not listed.
- A user can never edit or delete another user's rows (404, row untouched).
- The summary endpoints are scoped to the requesting user's conversations.
- RelationshipState (affinity/trust/tone/relationship notes) is never exposed
  or modified by the editor.
- Unauthenticated requests are rejected.

Plus basic CRUD behavior: create/edit/delete facts and relations, edit/delete
the current summary, superseded/inactive facts hidden.
"""

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from capp.models import (
    Conversation,
    ConversationSummary,
    MemoryEntity,
    MemoryFact,
    MemoryRelation,
    RelationshipState,
)
from capp.tests import _make_character, _make_user


def _make_conversation(user, character, **kwargs):
    return Conversation.objects.create(user=user, character=character, **kwargs)


class MemoryEditorApiTests(TestCase):
    def setUp(self):
        self.alice = _make_user(username="alice", email="alice@example.com")
        self.bob = _make_user(username="bob", email="bob@example.com")
        self.amina = _make_character(name="Amina", slug="amina-editor")
        self.zawadi = _make_character(name="Zawadi", slug="zawadi-editor")

        self.alice_amina_conv = _make_conversation(self.alice, self.amina)

        # Embedding provider is never available in tests.
        patcher = mock.patch(
            "capp.classes.memory.memory_service.MemoryService._get_embedding",
            return_value=None,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- helpers -------------------------------------------------------

    def _token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    def _auth(self, user):
        return {"HTTP_AUTHORIZATION": f"Bearer {self._token(user)}"}

    def _url_list(self, character=None):
        character = character or self.amina
        return f"/api/v1/memory/editor/{character.character_id}/"

    def _make_fact(self, user, character=None, text="A fact", **kwargs):
        return MemoryFact.objects.create(
            user=user,
            character=character,
            fact_text=text,
            fact_type=kwargs.pop("fact_type", "personal"),
            confidence_score=kwargs.pop("confidence_score", Decimal("0.90")),
            importance_score=kwargs.pop("importance_score", 5),
            is_active=kwargs.pop("is_active", True),
            **kwargs,
        )

    def _make_relation(self, user, character=None, subject="the user", predicate="lives_in", obj="Nairobi"):
        subj, _ = MemoryEntity.objects.get_or_create(
            user=user,
            normalized_name=subject.lower(),
            defaults={"name": subject, "entity_type": "person"},
        )
        return MemoryRelation.objects.create(
            user=user,
            character=character,
            subject=subj,
            predicate=predicate,
            object_text=obj,
        )

    # ---- listing / isolation -------------------------------------------

    def test_list_returns_own_character_facts_and_global_facts_only(self):
        self._make_fact(self.alice, self.amina, "Alice's Amina fact")
        self._make_fact(self.alice, None, "Alice's global fact")  # shared
        self._make_fact(self.alice, self.zawadi, "Alice's Zawadi fact")  # other character
        self._make_fact(self.bob, self.amina, "Bob's Amina fact")  # other user

        resp = self.client.get(self._url_list(), **self._auth(self.alice))

        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        texts = {f["fact_text"]: f for f in data["facts"]}
        self.assertEqual(len(texts), 2)
        self.assertIn("Alice's Amina fact", texts)
        self.assertIn("Alice's global fact", texts)
        self.assertNotIn("Alice's Zawadi fact", texts)
        self.assertNotIn("Bob's Amina fact", texts)
        self.assertFalse(texts["Alice's Amina fact"]["shared"])
        self.assertTrue(texts["Alice's global fact"]["shared"])

    def test_list_hides_superseded_and_inactive_facts(self):
        current = self._make_fact(self.alice, self.amina, "Current fact")
        superseded = self._make_fact(self.alice, self.amina, "Old fact", valid_to=timezone.now())
        inactive = self._make_fact(self.alice, self.amina, "Inactive fact", is_active=False)

        resp = self.client.get(self._url_list(), **self._auth(self.alice))

        texts = [f["fact_text"] for f in resp.json()["data"]["facts"]]
        self.assertIn("Current fact", texts)
        self.assertNotIn("Old fact", texts)
        self.assertNotIn("Inactive fact", texts)
        self.assertTrue(current.pk)
        self.assertTrue(superseded.pk)
        self.assertTrue(inactive.pk)

    def test_list_includes_relations_and_summary(self):
        self._make_relation(self.alice, self.amina)
        ConversationSummary.objects.create(
            conversation=self.alice_amina_conv,
            summary_text="They talked about Nairobi.",
            is_current=True,
        )

        resp = self.client.get(self._url_list(), **self._auth(self.alice))

        data = resp.json()["data"]
        self.assertEqual(len(data["relations"]), 1)
        rel = data["relations"][0]
        self.assertEqual(rel["subject"], "the user")
        self.assertEqual(rel["predicate"], "lives_in")
        self.assertEqual(rel["object"], "Nairobi")
        self.assertEqual(data["summary"]["summary_text"], "They talked about Nairobi.")

    def test_list_never_exposes_relationship_state(self):
        RelationshipState.objects.create(
            user=self.alice,
            character=self.amina,
            affinity_score=80,
            trust_score=90,
            tone_state="flirty",
            relationship_notes="Alice loves late-night chats",
        )

        resp = self.client.get(self._url_list(), **self._auth(self.alice))

        payload = resp.json()
        self.assertNotIn("affinity", str(payload))
        self.assertNotIn("trust", str(payload))
        self.assertNotIn("tone", str(payload))
        self.assertNotIn("relationship_notes", str(payload))
        self.assertNotIn("Alice loves late-night chats", str(payload))

    def test_unauthenticated_requests_rejected(self):
        for method, url in [
            ("get", self._url_list()),
            ("post", f"{self._url_list()}facts/"),
            ("patch", "/api/v1/memory/editor/facts/00000000-0000-0000-0000-000000000000/"),
            ("delete", "/api/v1/memory/editor/facts/00000000-0000-0000-0000-000000000000/delete/"),
        ]:
            resp = getattr(self.client, method)(url)
            self.assertIn(resp.status_code, (401, 403), f"{method} {url}")

    # ---- facts CRUD -----------------------------------------------------

    def test_create_fact(self):
        resp = self.client.post(
            f"{self._url_list()}facts/",
            '{"fact_text": "I teach math in Kisumu"}',
            content_type="application/json",
            **self._auth(self.alice),
        )

        self.assertEqual(resp.status_code, 201)
        fact = resp.json()["data"]
        self.assertEqual(fact["fact_text"], "I teach math in Kisumu")
        self.assertFalse(fact["shared"])
        self.assertEqual(MemoryFact.objects.filter(user=self.alice, character=self.amina).count(), 1)

    def test_create_fact_rejects_empty_and_oversized(self):
        resp = self.client.post(
            f"{self._url_list()}facts/",
            '{"fact_text": "   "}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post(
            f"{self._url_list()}facts/",
            '{"fact_text": "' + "x" * 501 + '"}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 400)

    def test_update_fact_rejects_cross_user_access(self):
        alice_fact = self._make_fact(self.alice, self.amina, "Alice's fact")

        resp = self.client.patch(
            f"/api/v1/memory/editor/facts/{alice_fact.memory_fact_id}/",
            '{"fact_text": "hacked"}',
            content_type="application/json",
            **self._auth(self.bob),
        )

        self.assertEqual(resp.status_code, 404)
        alice_fact.refresh_from_db()
        self.assertEqual(alice_fact.fact_text, "Alice's fact")

    def test_update_fact(self):
        fact = self._make_fact(self.alice, self.amina, "I live in Mombasa")

        resp = self.client.patch(
            f"/api/v1/memory/editor/facts/{fact.memory_fact_id}/",
            '{"fact_text": "I live in Nakuru now"}',
            content_type="application/json",
            **self._auth(self.alice),
        )

        self.assertEqual(resp.status_code, 200)
        fact.refresh_from_db()
        self.assertEqual(fact.fact_text, "I live in Nakuru now")

    def test_delete_fact_rejects_cross_user_access(self):
        alice_fact = self._make_fact(self.alice, self.amina, "Alice's fact")

        resp = self.client.delete(
            f"/api/v1/memory/editor/facts/{alice_fact.memory_fact_id}/delete/",
            **self._auth(self.bob),
        )

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(MemoryFact.objects.filter(pk=alice_fact.pk).exists())

    def test_delete_fact(self):
        fact = self._make_fact(self.alice, self.amina, "Bye fact")

        resp = self.client.delete(
            f"/api/v1/memory/editor/facts/{fact.memory_fact_id}/delete/",
            **self._auth(self.alice),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(MemoryFact.objects.filter(pk=fact.pk).exists())

    # ---- relations CRUD ---------------------------------------------------

    def test_create_and_update_relation(self):
        resp = self.client.post(
            f"{self._url_list()}relations/",
            '{"subject": "my sister", "predicate": "works at", "object": "Safaricom"}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 201)
        rel = resp.json()["data"]
        self.assertEqual(rel["subject"], "my sister")
        self.assertEqual(rel["predicate"], "works_at")
        self.assertEqual(rel["object"], "Safaricom")

        resp = self.client.patch(
            f"/api/v1/memory/editor/relations/{rel['memory_relation_id']}/",
            '{"subject": "my sister", "predicate": "works at", "object": "Airtel"}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["object"], "Airtel")

    def test_relation_mutations_reject_cross_user_access(self):
        alice_rel = self._make_relation(self.alice, self.amina)

        resp = self.client.patch(
            f"/api/v1/memory/editor/relations/{alice_rel.memory_relation_id}/",
            '{"subject": "x", "predicate": "y", "object": "z"}',
            content_type="application/json",
            **self._auth(self.bob),
        )
        self.assertEqual(resp.status_code, 404)

        resp = self.client.delete(
            f"/api/v1/memory/editor/relations/{alice_rel.memory_relation_id}/delete/",
            **self._auth(self.bob),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(MemoryRelation.objects.filter(pk=alice_rel.pk).exists())

    def test_relation_entities_are_user_scoped(self):
        # Entities created via the editor are bound to the requesting user —
        # Bob resolving the same name must get his own entity, never Alice's.
        self.client.post(
            f"{self._url_list()}relations/",
            '{"subject": "my sister", "predicate": "lives in", "object": "Kisumu"}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.client.post(
            f"{self._url_list()}relations/",
            '{"subject": "my sister", "predicate": "lives in", "object": "Nakuru"}',
            content_type="application/json",
            **self._auth(self.bob),
        )

        self.assertEqual(MemoryEntity.objects.filter(user=self.alice).count(), 2)
        self.assertEqual(MemoryEntity.objects.filter(user=self.bob).count(), 2)

    # ---- summary -----------------------------------------------------------

    def test_summary_update_and_delete_scoped_to_own_conversations(self):
        ConversationSummary.objects.create(
            conversation=self.alice_amina_conv,
            summary_text="Alice and Amina discussed work.",
            is_current=True,
        )

        # Bob has no conversations with Amina → nothing to update.
        resp = self.client.patch(
            f"{self._url_list()}summary/",
            '{"summary_text": "hacked"}',
            content_type="application/json",
            **self._auth(self.bob),
        )
        self.assertEqual(resp.status_code, 404)

        # Alice edits her own summary.
        resp = self.client.patch(
            f"{self._url_list()}summary/",
            '{"summary_text": "Alice and Amina discussed teaching."}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            ConversationSummary.objects.get(conversation=self.alice_amina_conv).summary_text,
            "Alice and Amina discussed teaching.",
        )

        # Alice deletes it.
        resp = self.client.delete(
            f"{self._url_list()}summary/delete/",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ConversationSummary.objects.filter(conversation=self.alice_amina_conv).exists())

    def test_summary_edit_does_not_touch_relationship_state(self):
        ConversationSummary.objects.create(
            conversation=self.alice_amina_conv,
            summary_text="A summary.",
            is_current=True,
        )
        rs = RelationshipState.objects.create(
            user=self.alice,
            character=self.amina,
            affinity_score=42,
            relationship_notes="precious",
        )

        self.client.patch(
            f"{self._url_list()}summary/",
            '{"summary_text": "Edited."}',
            content_type="application/json",
            **self._auth(self.alice),
        )

        rs.refresh_from_db()
        self.assertEqual(rs.affinity_score, 42)
        self.assertEqual(rs.relationship_notes, "precious")

    # ---- global facts --------------------------------------------------------

    def test_owner_can_edit_and_delete_global_fact(self):
        global_fact = self._make_fact(self.alice, None, "Global truth")

        # Listed (shared) from any character's editor:
        resp = self.client.get(self._url_list(self.zawadi), **self._auth(self.alice))
        texts = [f["fact_text"] for f in resp.json()["data"]["facts"]]
        self.assertIn("Global truth", texts)

        resp = self.client.patch(
            f"/api/v1/memory/editor/facts/{global_fact.memory_fact_id}/",
            '{"fact_text": "Corrected global truth"}',
            content_type="application/json",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.client.delete(
            f"/api/v1/memory/editor/facts/{global_fact.memory_fact_id}/delete/",
            **self._auth(self.alice),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(MemoryFact.objects.filter(pk=global_fact.pk).exists())

    def test_other_user_cannot_edit_global_fact(self):
        global_fact = self._make_fact(self.alice, None, "Alice global")

        resp = self.client.patch(
            f"/api/v1/memory/editor/facts/{global_fact.memory_fact_id}/",
            '{"fact_text": "hacked"}',
            content_type="application/json",
            **self._auth(self.bob),
        )
        self.assertEqual(resp.status_code, 404)
        global_fact.refresh_from_db()
        self.assertEqual(global_fact.fact_text, "Alice global")
