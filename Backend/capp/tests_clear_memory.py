"""
Tests for MemoryService.clear_memory_for_character scope isolation.

Verifies:
- All (user, character)-scoped memory data is deleted: facts, relations,
  summaries, and RelationshipState.
- Orphaned MemoryEntity rows (no surviving relation) are swept.
- MemoryEntity rows still referenced by other characters' relations survive.
- Other users' data is untouched.
- Other characters' data (for the same user) is untouched.
"""

from decimal import Decimal

from django.test import TestCase

from capp.classes.memory.memory_service import MemoryService
from capp.models import (
    Conversation,
    ConversationSummary,
    MemoryEntity,
    MemoryFact,
    MemoryRelation,
    RelationshipState,
)
from capp.tests import _make_character, _make_user


class ClearMemoryScopeIsolationTests(TestCase):
    """Verify clear_memory_for_character never touches data outside its scope."""

    def setUp(self):
        # Two users, two characters — full cross-product of test data.
        self.alice = _make_user(username="alice", email="alice@example.com")
        self.bob = _make_user(username="bob", email="bob@example.com")
        self.amina = _make_character(name="Amina", slug="amina")
        self.zawadi = _make_character(name="Zawadi", slug="zawadi")

        # Conversations
        self.alice_amina_conv = Conversation.objects.create(user=self.alice, character=self.amina)
        self.alice_zawadi_conv = Conversation.objects.create(user=self.alice, character=self.zawadi)
        self.bob_amina_conv = Conversation.objects.create(user=self.bob, character=self.amina)

        # ---- Facts ----
        # Target: alice + amina (should be deleted)
        MemoryFact.objects.create(user=self.alice, character=self.amina, fact_text="target-fact-1")
        MemoryFact.objects.create(user=self.alice, character=self.amina, fact_text="target-fact-2")
        # Other character for alice
        MemoryFact.objects.create(user=self.alice, character=self.zawadi, fact_text="alice-zawadi-fact")
        # Other user, same character
        MemoryFact.objects.create(user=self.bob, character=self.amina, fact_text="bob-amina-fact")

        # ---- Entities (user-scoped, no character FK) ----
        # Entity used ONLY by target relations → orphan sweep should delete.
        self.entity_only_used_by_target = MemoryEntity.objects.create(
            user=self.alice, name="Kilifi", normalized_name="kilifi"
        )
        # Entity used by BOTH target and non-target relations → must survive.
        self.entity_shared_across_characters = MemoryEntity.objects.create(
            user=self.alice, name="Mother", normalized_name="mother"
        )
        # Entity used only by another character's relation (alice + zawadi).
        self.entity_other_character_only = MemoryEntity.objects.create(
            user=self.alice, name="Nairobi", normalized_name="nairobi"
        )
        # Bob's entity — must never be touched.
        self.entity_bob = MemoryEntity.objects.create(user=self.bob, name="Dodoma", normalized_name="dodoma")

        # ---- Relations ----
        # Target (alice + amina) — will be deleted.
        MemoryRelation.objects.create(
            user=self.alice,
            character=self.amina,
            subject=self.entity_only_used_by_target,
            predicate="lives_in",
            object_text="the coast",
            confidence_score=Decimal("0.90"),
        )
        MemoryRelation.objects.create(
            user=self.alice,
            character=self.amina,
            subject=self.entity_shared_across_characters,
            predicate="mother_of",
            object_text="alice",
            confidence_score=Decimal("0.95"),
        )
        # Alice's other character — same entity_shared_across_characters, must
        # both survive AND keep the entity alive during orphan sweep.
        MemoryRelation.objects.create(
            user=self.alice,
            character=self.zawadi,
            subject=self.entity_shared_across_characters,
            predicate="mother_of",
            object_text="alice",
            confidence_score=Decimal("0.90"),
        )
        MemoryRelation.objects.create(
            user=self.alice,
            character=self.zawadi,
            subject=self.entity_other_character_only,
            predicate="lives_in",
            object_text="kenya",
            confidence_score=Decimal("0.85"),
        )
        # Bob's relation on Amina — must never be touched.
        MemoryRelation.objects.create(
            user=self.bob,
            character=self.amina,
            subject=self.entity_bob,
            predicate="lives_in",
            object_text="tanzania",
            confidence_score=Decimal("0.90"),
        )

        # ---- Conversation summaries ----
        ConversationSummary.objects.create(
            conversation=self.alice_amina_conv, summary_text="alice-amina target summary"
        )
        ConversationSummary.objects.create(
            conversation=self.alice_zawadi_conv, summary_text="alice-zawadi summary — survives"
        )
        ConversationSummary.objects.create(
            conversation=self.bob_amina_conv, summary_text="bob-amina summary — survives"
        )

        # ---- RelationshipState ----
        RelationshipState.objects.create(
            user=self.alice,
            character=self.amina,
            affinity_score=7,
            trust_score=6,
            familiarity_score=4,
            interaction_count=12,
            tone_state="warm",
            relationship_notes="remembered birthday\nshared a fear",
        )
        RelationshipState.objects.create(
            user=self.alice,
            character=self.zawadi,
            affinity_score=3,
            trust_score=2,
            familiarity_score=1,
            interaction_count=5,
            tone_state="neutral",
        )
        RelationshipState.objects.create(
            user=self.bob,
            character=self.amina,
            affinity_score=8,
            trust_score=7,
            familiarity_score=5,
            interaction_count=20,
            tone_state="playful",
        )

        # Call under test.
        self.result = MemoryService.clear_memory_for_character(self.alice, self.amina)

    # ---------------- Target-scope deletion ----------------

    def test_all_target_facts_deleted(self):
        self.assertFalse(MemoryFact.objects.filter(user=self.alice, character=self.amina).exists())

    def test_all_target_relations_deleted(self):
        self.assertFalse(MemoryRelation.objects.filter(user=self.alice, character=self.amina).exists())

    def test_target_summaries_deleted(self):
        self.assertFalse(ConversationSummary.objects.filter(conversation=self.alice_amina_conv).exists())

    def test_target_relationship_state_deleted(self):
        self.assertFalse(RelationshipState.objects.filter(user=self.alice, character=self.amina).exists())

    # ---------------- Orphan entity sweep ----------------

    def test_orphaned_entity_swept(self):
        """Entity that was only referenced by target relations is deleted."""
        self.assertFalse(MemoryEntity.objects.filter(pk=self.entity_only_used_by_target.pk).exists())

    def test_entity_shared_across_characters_survives(self):
        """Entity still referenced by non-target relations must survive."""
        self.assertTrue(MemoryEntity.objects.filter(pk=self.entity_shared_across_characters.pk).exists())

    def test_entity_used_by_other_character_survives(self):
        self.assertTrue(MemoryEntity.objects.filter(pk=self.entity_other_character_only.pk).exists())

    # ---------------- Cross-user isolation ----------------

    def test_other_users_facts_untouched(self):
        self.assertTrue(MemoryFact.objects.filter(user=self.bob, character=self.amina).exists())

    def test_other_users_relations_untouched(self):
        self.assertTrue(MemoryRelation.objects.filter(user=self.bob, character=self.amina).exists())

    def test_other_users_summaries_untouched(self):
        self.assertTrue(ConversationSummary.objects.filter(conversation=self.bob_amina_conv).exists())

    def test_other_users_relationship_state_untouched(self):
        self.assertTrue(RelationshipState.objects.filter(user=self.bob, character=self.amina).exists())

    def test_other_users_entity_untouched(self):
        self.assertTrue(MemoryEntity.objects.filter(pk=self.entity_bob.pk).exists())

    # ---------------- Cross-character isolation (same user) ----------------

    def test_same_users_other_character_facts_untouched(self):
        self.assertTrue(MemoryFact.objects.filter(user=self.alice, character=self.zawadi).exists())

    def test_same_users_other_character_relations_untouched(self):
        self.assertTrue(MemoryRelation.objects.filter(user=self.alice, character=self.zawadi).exists())

    def test_same_users_other_character_summaries_untouched(self):
        self.assertTrue(ConversationSummary.objects.filter(conversation=self.alice_zawadi_conv).exists())

    def test_same_users_other_character_relationship_state_untouched(self):
        self.assertTrue(RelationshipState.objects.filter(user=self.alice, character=self.zawadi).exists())

    # ---------------- Return-value shape ----------------

    def test_return_counts(self):
        self.assertEqual(self.result["facts_deleted"], 2)
        self.assertEqual(self.result["relations_deleted"], 2)
        self.assertEqual(self.result["summaries_deleted"], 1)
        self.assertEqual(self.result["relationship_reset"], 1)
        # entity_only_used_by_target is the single orphan.
        self.assertEqual(self.result["entities_deleted"], 1)


class ClearMemoryEmptyStateTests(TestCase):
    """Idempotent when nothing exists for (user, character)."""

    def test_clearing_when_nothing_exists_is_a_no_op(self):
        user = _make_user(username="charlie", email="charlie@example.com")
        character = _make_character(name="Solo", slug="solo")

        result = MemoryService.clear_memory_for_character(user, character)

        self.assertEqual(result["facts_deleted"], 0)
        self.assertEqual(result["relations_deleted"], 0)
        self.assertEqual(result["summaries_deleted"], 0)
        self.assertEqual(result["relationship_reset"], 0)
        self.assertEqual(result["entities_deleted"], 0)
