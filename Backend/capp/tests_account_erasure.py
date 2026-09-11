"""Local regressions for account-erasure content and commit-safe file cleanup."""

from unittest import mock

from django.core.files.storage import Storage
from django.db.models.signals import pre_delete
from django.test import TestCase

from capp.classes.auth.auth import Auth
from capp.models import (
    Character,
    CharacterGalleryImage,
    Conversation,
    ConversationSummary,
    CustomUser,
    Feedback,
    Hobby,
    MemoryEntity,
    MemoryFact,
    MemoryRelation,
    MediaDeletionJob,
    Message,
    Occupation,
    Personality,
    RelationshipState,
)
from capp.tests import _make_character, _make_message, _make_user


class AccountErasureContentTests(TestCase):
    def setUp(self):
        # Assign stored names, never uploads. Every file-bearing field uses the
        # same mock so no test can reach local or remote media storage.
        self.storage = mock.Mock(spec=Storage)
        for model, field_name in (
            (CustomUser, "avatar"),
            (Character, "avatar"),
            (CharacterGalleryImage, "image"),
        ):
            patcher = mock.patch.object(model._meta.get_field(field_name), "storage", self.storage)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.storage.save.assert_not_called)

        self.owner = _make_user(username="erasure-owner", email="erasure-owner@example.test")
        self.other = _make_user(username="erasure-other", email="erasure-other@example.test")
        self.owner.avatar = "users/avatars/erasure-owner.png"
        self.owner.save(update_fields=["avatar"])

    def _character(self, slug, *, is_public=False, created_by=None):
        character = _make_character(
            name=f"Fictional {slug}",
            slug=slug,
            is_public=is_public,
            created_by=self.owner if created_by is None else created_by,
        )
        character.avatar = f"characters/avatars/{slug}.png"
        character.is_featured = True
        character.short_description = "Fictional profile description"
        character.mood = "Fictional mood"
        character.intro = "Fictional introduction"
        character.personality_description = "Fictional private backstory"
        character.prompt_template = "Fictional custom prompt for {name}"
        character.system_instructions = "Fictional custom instructions"
        character.greeting_message = "Fictional greeting"
        character.tone_style = "Fictional tone"
        character.country = "Fictional country"
        character.town = "Fictional town"
        character.timezone = "Etc/UTC"
        character.occupation = Occupation.objects.create(name=f"Test occupation {slug}")
        character.rejection_reason = "Fictional creator-only review note"
        character.client_operation_id = f"test-operation-{slug}"
        character.save()
        character.personalities.add(Personality.objects.create(name=f"Test personality {slug}"))
        character.hobbies.add(Hobby.objects.create(name=f"Test hobby {slug}"))
        return character

    def _gallery(self, character, label, *, visibility, created_by=None):
        return CharacterGalleryImage.objects.create(
            character=character,
            image=f"characters/gallery/{character.slug}-{label}.png",
            caption=f"Fictional {label} caption",
            visibility=visibility,
            created_by=self.owner if created_by is None else created_by,
        )

    def _history(self, user, character):
        """Build all content locally; no chat, embedding, or image providers."""
        conversation = Conversation.objects.create(
            user=user, character=character, title=f"Fictional chat for {user.username}"
        )
        message = _make_message(
            conversation, Message.SenderType.USER, "Fictional private user message", metadata={"dummy": "user"}
        )
        reply = _make_message(
            conversation, Message.SenderType.AI, "Fictional private reply", metadata={"dummy": "reply"}
        )
        reply.reply_to = message
        reply.save(update_fields=["reply_to"])
        summary = ConversationSummary.objects.create(
            conversation=conversation,
            summary_text="Fictional private summary",
            covered_until_message=reply,
        )
        fact = MemoryFact.objects.create(
            user=user,
            character=character,
            conversation=conversation,
            source_message=message,
            fact_text="Fictional private memory fact",
        )
        entity = MemoryEntity.objects.create(
            user=user,
            name=f"Fictional contact {character.slug}",
            normalized_name=f"fictional-contact-{character.pk.hex}",
        )
        relation = MemoryRelation.objects.create(
            user=user,
            character=character,
            conversation=conversation,
            subject=entity,
            predicate="knows",
            object_text="Fictional private graph detail",
        )
        relationship = RelationshipState.objects.create(
            user=user,
            character=character,
            interaction_count=2,
            relationship_notes="Fictional private relationship notes",
        )
        feedback = Feedback.objects.create(user=user, message="Fictional private feedback", rating=4)
        return conversation, [conversation, message, reply, summary, fact, entity, relation, relationship, feedback]

    def _snapshot(self, *rows):
        return {(type(row), row.pk): type(row).objects.values().get(pk=row.pk) for row in rows}

    def _assert_unchanged(self, snapshot):
        for (model, pk), expected in snapshot.items():
            with self.subTest(model=model.__name__, pk=pk):
                self.assertEqual(model.objects.values().get(pk=pk), expected)

    def _assert_deleted(self, *rows):
        for row in rows:
            with self.subTest(model=type(row).__name__, pk=row.pk):
                self.assertFalse(type(row).objects.filter(pk=row.pk).exists())

    def _erase_owner(self, *character_file_names):
        # TestCase's outer transaction never commits. Executing captured
        # callbacks simulates that boundary without a TransactionTestCase.
        with self.captureOnCommitCallbacks(execute=True):
            result = Auth.hard_delete_user(self.owner)
            self.assertEqual(result["status"], "success", result)
            self._assert_deleted(self.owner)
            self.storage.delete.assert_not_called()

        self.assertCountEqual(
            self.storage.delete.call_args_list,
            [mock.call(self.owner.avatar.name), *(mock.call(name) for name in character_file_names)],
        )

    def _assert_archived_shell(self, character):
        character.refresh_from_db()
        self.assertEqual(character.status, Character.StatusChoices.ARCHIVED)
        self.assertEqual(character.name, "Archived character")
        self.assertEqual(character.slug, f"archived-{character.pk.hex}")
        self.assertFalse(character.is_public)
        self.assertFalse(character.is_active)
        self.assertFalse(character.is_featured)
        self.assertIsNone(character.created_by_id)
        self.assertEqual(character.avatar.name, "")
        for field in (
            "short_description",
            "mood",
            "intro",
            "personality_description",
            "prompt_template",
            "greeting_message",
            "tone_style",
            "country",
            "town",
        ):
            with self.subTest(field=field):
                self.assertEqual(getattr(character, field), "")
        for field in (
            "system_instructions",
            "timezone",
            "occupation_id",
            "rejection_reason",
            "client_operation_id",
        ):
            with self.subTest(field=field):
                self.assertIsNone(getattr(character, field))
        self.assertFalse(character.personalities.exists())
        self.assertFalse(character.hobbies.exists())
        self.assertFalse(character.gallery_images.exists())

    def test_owned_public_character_and_other_users_history_are_preserved(self):
        character = self._character("public-shared", is_public=True)
        _, other_rows = self._history(self.other, character)
        character_before = self._snapshot(character)
        expected = character_before[(Character, character.pk)]
        expected.update(created_by_id=None, client_operation_id=None, rejection_reason=None)
        other_before = self._snapshot(self.other, *other_rows)
        personality_ids = set(character.personalities.values_list("pk", flat=True))
        hobby_ids = set(character.hobbies.values_list("pk", flat=True))

        self._erase_owner()

        # Public persona content, avatar, approval, activity, and timestamps
        # stay intact; only attribution and creator-only metadata are removed.
        self._assert_unchanged(character_before)
        self._assert_unchanged(other_before)
        self.assertSetEqual(set(character.personalities.values_list("pk", flat=True)), personality_ids)
        self.assertSetEqual(set(character.hobbies.values_list("pk", flat=True)), hobby_ids)

    def test_never_chatted_private_character_and_all_its_files_are_hard_deleted(self):
        character = self._character("private-unused")
        private_image = self._gallery(character, "private", visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE)
        public_image = self._gallery(
            character,
            "public-labelled",
            visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC,
            created_by=self.other,
        )
        self.assertFalse(character.conversations.exists())

        self._erase_owner(character.avatar.name, private_image.image.name, public_image.image.name)

        self._assert_deleted(character, private_image, public_image)
        self.assertTrue(CustomUser.objects.filter(pk=self.other.pk).exists())

    def test_chatted_private_character_is_scrubbed_and_other_users_chat_survives_closed(self):
        character = self._character("private-shared")
        _, owner_rows = self._history(self.owner, character)
        other_conversation, other_rows = self._history(self.other, character)
        other_before = self._snapshot(*other_rows[1:])
        conversation_before = Conversation.objects.values().get(pk=other_conversation.pk)
        conversation_before.pop("updated_at")
        conversation_before["status"] = Conversation.StatusChoices.CLOSED
        private_image = self._gallery(character, "private", visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE)
        public_image = self._gallery(
            character,
            "public-labelled",
            visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC,
            created_by=self.other,
        )

        self._erase_owner(character.avatar.name, private_image.image.name, public_image.image.name)

        self._assert_archived_shell(character)
        self._assert_deleted(*owner_rows, private_image, public_image)
        self._assert_unchanged(other_before)
        conversation_after = Conversation.objects.values().get(pk=other_conversation.pk)
        conversation_after.pop("updated_at")
        self.assertEqual(conversation_after, conversation_before)

    def test_private_character_chatted_only_by_deleting_owner_still_becomes_a_shell(self):
        character = self._character("private-owner-only")
        conversation, owner_rows = self._history(self.owner, character)
        # Past/closed history counts too, and must be inspected before the
        # owner's conversations are erased.
        Conversation.objects.filter(pk=conversation.pk).update(status=Conversation.StatusChoices.CLOSED)
        self.assertEqual(list(character.conversations.values_list("user_id", flat=True)), [self.owner.pk])

        self._erase_owner(character.avatar.name)

        self._assert_archived_shell(character)
        self._assert_deleted(*owner_rows)
        self.assertFalse(character.conversations.exists())

    def test_public_gallery_is_detached_but_owners_private_gallery_is_deleted(self):
        own_public = self._character("gallery-own", is_public=True)
        other_public = self._character("gallery-other", is_public=True, created_by=self.other)
        other_character_before = self._snapshot(other_public)
        public_images = []
        private_images = []
        other_private_images = []
        for character in (own_public, other_public):
            public_images.append(
                self._gallery(character, "shared", visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC)
            )
            private_images.append(
                self._gallery(character, "mine", visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE)
            )
            other_private_images.append(
                self._gallery(
                    character,
                    "theirs",
                    visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
                    created_by=self.other,
                )
            )
        public_before = self._snapshot(*public_images)
        for expected in public_before.values():
            expected["created_by_id"] = None
        other_images_before = self._snapshot(*other_private_images)

        self._erase_owner(*(image.image.name for image in private_images))

        self._assert_deleted(*private_images)
        self._assert_unchanged(public_before)
        self._assert_unchanged(other_images_before)
        self._assert_unchanged(other_character_before)
        own_public.refresh_from_db()
        self.assertIsNone(own_public.created_by_id)
        self.assertTrue(own_public.is_public)
        self.assertTrue(own_public.is_active)

    def test_user_conversations_messages_memory_and_feedback_are_removed_on_retained_character(self):
        character = self._character("content-other", is_public=True, created_by=self.other)
        _, owner_rows = self._history(self.owner, character)
        _, other_rows = self._history(self.other, character)
        global_fact = MemoryFact.objects.create(user=self.owner, fact_text="Fictional user-global memory")
        orphan_entity = MemoryEntity.objects.create(
            user=self.owner, name="Fictional orphan", normalized_name="fictional-orphan"
        )
        global_relation = MemoryRelation.objects.create(
            user=self.owner,
            subject=orphan_entity,
            predicate="likes",
            object_text="Fictional user-global preference",
        )
        other_before = self._snapshot(character, self.other, *other_rows)

        self._erase_owner()

        self._assert_deleted(*owner_rows, global_fact, orphan_entity, global_relation)
        self._assert_unchanged(other_before)
        self.assertFalse(Conversation.objects.filter(user=self.owner).exists())
        self.assertFalse(Feedback.objects.filter(user=self.owner).exists())

    def test_pre_delete_failure_rolls_back_content_changes_and_discards_file_deletions(self):
        public_character = self._character("rollback-public", is_public=True)
        unused_character = self._character("rollback-unused")
        chatted_character = self._character("rollback-chatted")
        _, owner_rows = self._history(self.owner, chatted_character)
        _, other_rows = self._history(self.other, chatted_character)
        galleries = [
            self._gallery(public_character, "private", visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE),
            self._gallery(public_character, "shared", visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC),
            self._gallery(unused_character, "private", visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE),
            self._gallery(chatted_character, "private", visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE),
            self._gallery(
                chatted_character,
                "public-labelled",
                visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC,
                created_by=self.other,
            ),
        ]
        characters = [public_character, unused_character, chatted_character]
        before = self._snapshot(self.owner, self.other, *characters, *owner_rows, *other_rows, *galleries)
        associations_before = {
            character.pk: (
                set(character.personalities.values_list("pk", flat=True)),
                set(character.hobbies.values_list("pk", flat=True)),
            )
            for character in characters
        }
        failure = mock.Mock(side_effect=RuntimeError("Simulated account deletion failure"))
        pre_delete.connect(failure, sender=CustomUser, weak=False)
        self.addCleanup(pre_delete.disconnect, receiver=failure, sender=CustomUser)

        with mock.patch("capp.classes.auth.auth.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                result = Auth.hard_delete_user(self.owner)
                self.assertEqual(result, {"status": "fail", "data": "server_error"})
                self.storage.delete.assert_not_called()

        # Ensure this was the intended failure after content erasure, not an
        # unrelated exception that happened to return the same API error.
        failure.assert_called_once()
        self.assertEqual(failure.call_args.kwargs["instance"].pk, self.owner.pk)
        self.assertEqual(callbacks, [])
        self.storage.delete.assert_not_called()
        self.assertFalse(MediaDeletionJob.objects.exists())
        self._assert_unchanged(before)
        for character in characters:
            with self.subTest(character=character.pk):
                self.assertEqual(
                    (
                        set(character.personalities.values_list("pk", flat=True)),
                        set(character.hobbies.values_list("pk", flat=True)),
                    ),
                    associations_before[character.pk],
                )
