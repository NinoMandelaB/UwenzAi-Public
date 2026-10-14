"""Local media lifecycle regressions; providers and every storage field are mocked.

Most races are deterministic interleavings at the provider/publication boundary.
The PostgreSQL-only avatar test additionally exercises two real connections and
waits for an observed database lock, not a timing-dependent sleep.
"""

import threading
import time
import uuid
from unittest import mock, skipUnless

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.db import IntegrityError, connection, connections, transaction
from django.db.models.signals import pre_save
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from capp.classes.auth.auth import Auth
from capp.classes.characters.character_gallery_service import CharacterGalleryService
from capp.classes.characters.character_service import CharacterService
from capp.models import (
    BillingReservation,
    Character,
    CharacterGalleryImage,
    Conversation,
    CustomUser,
    MediaDeletionJob,
    SparkPool,
)
from capp.tasks import retry_media_deletions
from capp.tests import _make_character, _make_user


class MockMediaMixin:
    def setUp(self):
        super().setUp()
        self.storage = mock.Mock(spec=Storage)
        self.storage.generate_filename.side_effect = lambda name: name
        # Deliberately unlike the requested UUID-based key: cleanup must use
        # what storage actually wrote, not the caller's suggested filename.
        self.stored_name = "characters/test-storage/actual-returned-key.webp"
        self.storage.save.return_value = self.stored_name
        self.storage.url.side_effect = lambda name: f"https://media.example.test/{name}"
        for model, field_name in (
            (CustomUser, "avatar"),
            (Character, "avatar"),
            (CharacterGalleryImage, "image"),
        ):
            self._patch(mock.patch.object(model._meta.get_field(field_name), "storage", self.storage))
        self._patch(override_settings(FAL_API_KEY="local-test-only"))
        self.provider = self._patch(
            mock.patch.object(CharacterService, "_generate_fal_seedream_image", return_value=b"local-image-bytes")
        )
        self.fallback = self._patch(mock.patch.object(CharacterService, "_generate_ai_image", return_value=None))
        # These tests exercise the erasure and approval flows, not the
        # child-safety screen. The screen fails closed without a provider and
        # also fails closed on the dummy `local-image-bytes` fal mock (not a
        # real image), so it is bypassed here entirely (audit B3). Tests that
        # do exercise the screen live in tests_generated_image_screening.
        self.screen = self._patch(mock.patch.object(CharacterService, "_screen_generated_image", return_value=None))
        self._patch(mock.patch.object(CharacterService, "_get_character_reference_bytes", return_value=None))
        self.free_actor = self._patch(
            mock.patch("capp.classes.characters.character_service.is_free_actor", return_value=True)
        )
        self._patch(mock.patch("capp.classes.characters.character_service.logger.exception"))
        self.owner = _make_user(username="media-owner", email="media-owner@example.test")
        self.other = _make_user(username="media-other", email="media-other@example.test")
        self.character = _make_character(slug="media-race", created_by=self.owner)

    def _patch(self, patcher):
        # override_settings uses enable/disable rather than start/stop.
        if isinstance(patcher, override_settings):
            patcher.enable()
            self.addCleanup(patcher.disable)
            return patcher
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def _gallery(self, **kwargs):
        return CharacterService.generate_gallery_image(
            character=self.character,
            actor=kwargs.pop("actor", self.owner),
            prompt="Fictional adult standing in a garden",
            operation_id=kwargs.pop("operation_id", str(uuid.uuid4())),
            **kwargs,
        )

    def _avatar(self, **kwargs):
        return CharacterService.generate_avatar(
            character=self.character,
            actor=kwargs.pop("actor", self.owner),
            prompt="Fictional adult portrait",
            **kwargs,
        )

    def _old_avatar(self):
        self.character.avatar = "characters/avatars/previous-local-avatar.webp"
        self.character.save(update_fields=["avatar"])
        return self.character.avatar.name

    def _fail_gallery_persistence(self, sender, instance, **kwargs):
        if instance.image:
            raise RuntimeError("Simulated gallery row persistence failure")


class MediaErasureRaceTests(MockMediaMixin, TestCase):
    def _admin_upload(self, validation_effect=None):
        self.other.is_staff = True
        self.other.save(update_fields=["is_staff"])
        with mock.patch.object(
            CharacterGalleryService, "_validate_gallery_image_file", side_effect=validation_effect, return_value=None
        ):
            return CharacterGalleryService.upload_gallery_image(
                str(self.character.pk),
                ContentFile(b"local-only-image", name="admin-upload.webp"),
                actor=self.other,
            )

    def test_admin_upload_rechecks_erased_private_character_after_validation(self):
        Character.objects.filter(pk=self.character.pk).update(is_public=False)
        Conversation.objects.create(user=self.other, character=self.character)

        def erase_owner(_file):
            self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")

        result = self._admin_upload(erase_owner)
        self.assertEqual(result, {"status": "fail", "data": "character_unavailable"})
        self.storage.save.assert_not_called()
        self.assertFalse(CharacterGalleryImage.objects.exists())

    def test_admin_upload_rechecks_erased_actor_after_validation(self):
        def erase_actor(_file):
            self.assertEqual(Auth.hard_delete_user(self.other)["status"], "success")

        result = self._admin_upload(erase_actor)
        self.assertEqual(result, {"status": "fail", "data": "permission_denied"})
        self.storage.save.assert_not_called()

    def test_admin_upload_rechecks_revoked_privileges(self):
        def revoke(_file):
            CustomUser.objects.filter(pk=self.other.pk).update(is_staff=False, is_superuser=False)

        result = self._admin_upload(revoke)
        self.assertEqual(result, {"status": "fail", "data": "permission_denied"})
        self.storage.save.assert_not_called()

    def test_admin_upload_sql_failure_queues_actual_uploaded_key(self):
        def fail_after_upload(instance, *args, **kwargs):
            field = instance._meta.get_field("image")
            field.pre_save(instance, add=True)
            raise IntegrityError("Simulated SQL insert failure")

        with mock.patch.object(CharacterGalleryImage, "_save_table", fail_after_upload):
            with self.captureOnCommitCallbacks(execute=False):
                result = self._admin_upload()
        self.assertEqual(result, {"status": "fail", "data": "server_error"})
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.assertEqual(MediaDeletionJob.objects.get().file_name, self.stored_name)
        self.storage.delete.assert_not_called()
        self.assertEqual(retry_media_deletions(), 1)
        self.storage.delete.assert_called_once_with(self.stored_name)

    def test_gallery_actor_deleted_during_provider_cannot_publish_on_another_owners_public_character(self):
        Character.objects.filter(pk=self.character.pk).update(created_by=self.other)
        self.character.refresh_from_db()

        def erase_actor(**kwargs):
            self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")
            return b"local-image-bytes"

        self.provider.side_effect = erase_actor
        result = self._gallery()
        self.assertEqual(result, {"status": "fail", "data": "permission_denied"})
        self.assertFalse(CustomUser.objects.filter(pk=self.owner.pk).exists())
        self.character.refresh_from_db()
        self.assertEqual(self.character.created_by_id, self.other.pk)
        self.assertTrue(self.character.is_public)
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.storage.save.assert_not_called()
        self.storage.delete.assert_not_called()

    def test_gallery_deleted_paid_actor_returns_controlled_failure_after_its_reservation_is_cascaded(self):
        self.free_actor.return_value = False
        SparkPool.objects.update_or_create(user=self.owner, defaults={"sparks": 10000})
        Character.objects.filter(pk=self.character.pk).update(created_by=self.other)
        self.character.refresh_from_db()

        def erase_paid_actor(**kwargs):
            self.assertTrue(BillingReservation.objects.filter(user=self.owner).exists())
            self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")
            self.assertFalse(BillingReservation.objects.filter(user=self.owner).exists())
            return b"local-image-bytes"

        self.provider.side_effect = erase_paid_actor
        self.assertEqual(self._gallery(), {"status": "fail", "data": "permission_denied"})
        self.provider.assert_called_once()
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.storage.save.assert_not_called()

    def test_gallery_private_character_erased_during_provider_cannot_be_repopulated_even_by_admin(self):
        Character.objects.filter(pk=self.character.pk).update(is_public=False)
        self.character.refresh_from_db()
        self.other.is_staff = True
        self.other.save(update_fields=["is_staff"])
        conversation = Conversation.objects.create(user=self.other, character=self.character)

        def erase_owner(**kwargs):
            self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")
            return b"local-image-bytes"

        self.provider.side_effect = erase_owner
        result = self._gallery(actor=self.other, is_public=True)
        self.assertEqual(result, {"status": "fail", "data": "character_unavailable"})
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.ARCHIVED)
        self.assertFalse(self.character.avatar)
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.StatusChoices.CLOSED)
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.storage.save.assert_not_called()

    def test_gallery_permission_revoked_during_provider_is_rechecked_before_storage(self):
        def make_private(**kwargs):
            Character.objects.filter(pk=self.character.pk).update(is_public=False)
            return b"local-image-bytes"

        self.provider.side_effect = make_private
        self.assertEqual(self._gallery(actor=self.other), {"status": "fail", "data": "permission_denied"})
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.storage.save.assert_not_called()

    def test_gallery_public_visibility_uses_current_not_stale_admin_privileges(self):
        self.other.is_staff = True
        self.other.save(update_fields=["is_staff"])

        def demote_actor(**kwargs):
            CustomUser.objects.filter(pk=self.other.pk).update(is_staff=False)
            return b"local-image-bytes"

        self.provider.side_effect = demote_actor
        result = self._gallery(actor=self.other, is_public=True)
        self.assertEqual(result["status"], "success")
        image = CharacterGalleryImage.objects.get()
        self.assertEqual(image.created_by_id, self.other.pk)
        self.assertEqual(image.visibility, CharacterGalleryImage.VisibilityChoices.PRIVATE)
        self.assertEqual(image.image.name, self.stored_name)
        self.storage.save.assert_called_once()
        self.storage.delete.assert_not_called()

    def test_gallery_persistence_failure_rolls_back_row_and_deletes_actual_upload_after_commit(self):
        pre_save.connect(self._fail_gallery_persistence, sender=CharacterGalleryImage, weak=False)
        self.addCleanup(pre_save.disconnect, self._fail_gallery_persistence, sender=CharacterGalleryImage)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self._gallery(), {"status": "fail", "data": "server_error"})
            self.assertFalse(CharacterGalleryImage.objects.exists())
            job = MediaDeletionJob.objects.get()
            self.assertEqual(job.file_name, self.stored_name)
            self.assertEqual(job.model_label, CharacterGalleryImage._meta.label)
            self.assertEqual(job.field_name, "image")
            self.storage.delete.assert_not_called()
        self.storage.save.assert_called_once()
        self.assertNotEqual(self.storage.save.call_args.args[0], self.stored_name)
        self.storage.delete.assert_called_once_with(self.stored_name)
        self.assertFalse(MediaDeletionJob.objects.exists())

    def test_avatar_persistence_failure_preserves_old_file_and_compensates_new_upload(self):
        old_name = self._old_avatar()

        def fail_new_avatar(sender, instance, **kwargs):
            if instance.avatar.name == self.stored_name:
                raise RuntimeError("Simulated avatar row persistence failure")

        # Registered after the model's pre_save handler: its old-file outbox
        # write must roll back with the failed Character.save savepoint.
        pre_save.connect(fail_new_avatar, sender=Character, weak=False)
        self.addCleanup(pre_save.disconnect, fail_new_avatar, sender=Character)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self._avatar(), {"status": "fail", "data": "server_error"})
            self.character.refresh_from_db()
            self.assertEqual(self.character.avatar.name, old_name)
            self.assertEqual(list(MediaDeletionJob.objects.values_list("file_name", flat=True)), [self.stored_name])
            self.storage.delete.assert_not_called()
        self.storage.delete.assert_called_once_with(self.stored_name)
        self.assertFalse(MediaDeletionJob.objects.exists())

    def test_worker_recovers_erasure_when_commit_callback_is_skipped(self):
        image = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.owner,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
            image="characters/gallery/skipped-callback.webp",
        )
        file_name = image.image.name
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")
        self.assertEqual(len(callbacks), 1)
        self.storage.delete.assert_not_called()
        self.assertFalse(CharacterGalleryImage.objects.filter(pk=image.pk).exists())
        self.assertTrue(MediaDeletionJob.objects.filter(file_name=file_name).exists())
        self.assertEqual(retry_media_deletions(), 1)
        self.storage.delete.assert_called_once_with(file_name)
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.assertEqual(retry_media_deletions(), 0)
        self.storage.delete.assert_called_once_with(file_name)

    def test_worker_recovers_failed_publication_when_compensation_callback_is_skipped(self):
        pre_save.connect(self._fail_gallery_persistence, sender=CharacterGalleryImage, weak=False)
        self.addCleanup(pre_save.disconnect, self._fail_gallery_persistence, sender=CharacterGalleryImage)
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(self._gallery(), {"status": "fail", "data": "server_error"})
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.storage.delete.assert_not_called()
        self.assertEqual(retry_media_deletions(), 1)
        self.storage.delete.assert_called_once_with(self.stored_name)
        self.assertFalse(MediaDeletionJob.objects.exists())

    def test_rolled_back_gallery_delete_has_no_outbox_or_storage_side_effect(self):
        image = CharacterGalleryImage.objects.create(
            character=self.character, created_by=self.owner, image="characters/gallery/rollback.webp"
        )
        image_id = image.pk
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesRegex(RuntimeError, "rollback"), transaction.atomic():
                image.delete()
                self.assertTrue(MediaDeletionJob.objects.exists())
                self.storage.delete.assert_not_called()
                raise RuntimeError("rollback")
        self.assertEqual(callbacks, [])
        self.assertTrue(CharacterGalleryImage.objects.filter(pk=image_id).exists())
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.storage.delete.assert_not_called()

    def test_stale_avatar_save_cannot_repopulate_an_erased_private_shell(self):
        Character.objects.filter(pk=self.character.pk).update(is_public=False)
        self.character.refresh_from_db()
        Conversation.objects.create(user=self.other, character=self.character)
        self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")
        self.character.avatar = ContentFile(b"local-upload", name="stale-avatar.webp")
        with self.assertRaisesRegex(ValueError, "archived character"):
            self.character.save(update_fields=["avatar"])
        self.character.refresh_from_db()
        self.assertFalse(self.character.avatar)
        self.assertEqual(self.character.status, Character.StatusChoices.ARCHIVED)
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.storage.save.assert_not_called()

    def test_stale_plain_avatar_save_cannot_reinsert_a_hard_deleted_character(self):
        Character.objects.filter(pk=self.character.pk).update(is_public=False)
        self.assertEqual(Auth.hard_delete_user(self.owner)["status"], "success")
        self.character.avatar = ContentFile(b"local-upload", name="stale-avatar.webp")
        with self.assertRaises(Character.DoesNotExist):
            self.character.save()
        self.assertFalse(Character.objects.filter(pk=self.character.pk).exists())
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.storage.save.assert_not_called()

    def test_non_avatar_update_does_not_enqueue_deletion_for_a_stale_in_memory_avatar(self):
        old_name = self._old_avatar()
        self.character.avatar = "characters/avatars/not-being-saved.webp"
        self.character.mood = "calm"
        with self.captureOnCommitCallbacks(execute=True):
            self.character.save(update_fields=["mood"])
        self.character.refresh_from_db()
        self.assertEqual(self.character.avatar.name, old_name)
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.storage.delete.assert_not_called()

    @skipUnless(connection.vendor == "postgresql", "Requires real SELECT FOR UPDATE SQL")
    def test_generation_locks_actor_before_character(self):
        for generate in (self._avatar, self._gallery):
            with self.subTest(generate=generate.__name__), CaptureQueriesContext(connection) as queries:
                self.assertEqual(generate()["status"], "success")
            locked_selects = [
                q["sql"] for q in queries if "FOR UPDATE" in q["sql"].upper() or "FOR NO KEY UPDATE" in q["sql"].upper()
            ]
            self.assertGreaterEqual(len(locked_selects), 2)
            self.assertIn(f'FROM "{CustomUser._meta.db_table}"', locked_selects[0])
            self.assertIn("FOR NO KEY UPDATE", locked_selects[0])
            self.assertIn(f'FROM "{Character._meta.db_table}"', locked_selects[1])


class MediaErasureTransactionTests(MockMediaMixin, TransactionTestCase):
    def test_standalone_character_save_failure_restores_old_avatar_and_has_no_outbox(self):
        old_name = self._old_avatar()
        conflicting = _make_character(slug="media-conflicting-slug", created_by=self.other)
        self.character.avatar = "characters/avatars/never-persisted-replacement.webp"
        self.character.slug = conflicting.slug
        self.assertFalse(connection.in_atomic_block)
        with self.assertRaises(IntegrityError):
            self.character.save()
        self.assertFalse(connection.in_atomic_block)
        self.character.refresh_from_db()
        self.assertEqual(self.character.avatar.name, old_name)
        self.assertEqual(self.character.slug, "media-race")
        self.assertFalse(MediaDeletionJob.objects.exists())
        self.storage.save.assert_not_called()
        self.storage.delete.assert_not_called()

    def test_standalone_uploaded_avatar_failure_cleans_new_key_but_keeps_old_avatar(self):
        old_name = self._old_avatar()
        conflicting = _make_character(slug="media-conflicting-slug", created_by=self.other)
        self.character.avatar = ContentFile(b"local-upload", name="suggested-upload.webp")
        self.character.slug = conflicting.slug
        self.assertFalse(connection.in_atomic_block)
        with self.assertRaises(IntegrityError):
            self.character.save()
        self.character.refresh_from_db()
        self.assertEqual(self.character.avatar.name, old_name)
        self.storage.save.assert_called_once()
        self.storage.delete.assert_called_once_with(self.stored_name)
        self.assertFalse(MediaDeletionJob.objects.exists())

    def test_avatar_commit_failure_rolls_back_replacement_and_compensates_actual_storage_key(self):
        old_name = self._old_avatar()
        real_commit = connection.commit
        failed_once = False

        def fail_publication_commit():
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise IntegrityError("Simulated commit failure")
            return real_commit()

        self.assertFalse(connection.in_atomic_block)
        with mock.patch.object(connection, "commit", side_effect=fail_publication_commit):
            result = self._avatar()
        self.assertTrue(failed_once)
        self.assertEqual(result, {"status": "fail", "data": "server_error"})
        self.character.refresh_from_db()
        self.assertEqual(self.character.avatar.name, old_name)
        self.storage.delete.assert_called_once_with(self.stored_name)
        self.assertFalse(MediaDeletionJob.objects.exists())

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks and pg_blocking_pids")
    def test_erasure_waits_for_admin_avatar_generation_and_deletes_the_committed_new_avatar(self):
        old_name = self._old_avatar()
        Character.objects.filter(pk=self.character.pk).update(is_public=False)
        self.other.is_staff = True
        self.other.save(update_fields=["is_staff"])
        conversation = Conversation.objects.create(user=self.other, character=self.character)
        provider_entered = threading.Event()
        release_provider = threading.Event()
        erasure_connected = threading.Event()
        results = {}
        erasure_pid = []

        def paused_provider(**kwargs):
            provider_entered.set()
            if not release_provider.wait(timeout=15):
                raise RuntimeError("Timed out waiting to release the local provider")
            return b"local-image-bytes"

        self.provider.side_effect = paused_provider

        def generate():
            try:
                results["generation"] = self._avatar(actor=self.other)
            except BaseException as exc:
                results["generation"] = exc
            finally:
                connections.close_all()

        def erase():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    erasure_pid.append(cursor.fetchone()[0])
                erasure_connected.set()
                results["erasure"] = Auth.hard_delete_user(self.owner)
            except BaseException as exc:
                results["erasure"] = exc
            finally:
                connections.close_all()

        generator = threading.Thread(target=generate, daemon=True)
        eraser = threading.Thread(target=erase, daemon=True)
        generator.start()
        try:
            self.assertTrue(provider_entered.wait(timeout=10), results)
            eraser.start()
            self.assertTrue(erasure_connected.wait(timeout=10), results)
            # Confirm erasure really reached a blocked row lock before allowing
            # the replacement to commit. Without the erasure SELECT FOR UPDATE,
            # it snapshots the old key and blocks only at the archive UPDATE.
            limit = time.monotonic() + 10
            blocked = False
            while time.monotonic() < limit:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [erasure_pid[0]])
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                if "erasure" in results:
                    break
                time.sleep(0.01)
            self.assertTrue(blocked, results)
        finally:
            release_provider.set()
            generator.join(timeout=15)
            if eraser.ident is not None:
                eraser.join(timeout=15)
        self.assertFalse(generator.is_alive(), results)
        self.assertFalse(eraser.is_alive(), results)
        self.assertIsInstance(results.get("generation"), dict, results)
        self.assertIsInstance(results.get("erasure"), dict, results)
        self.assertEqual(results["generation"]["status"], "success")
        self.assertEqual(results["erasure"]["status"], "success")
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.ARCHIVED)
        self.assertFalse(self.character.avatar)
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.StatusChoices.CLOSED)
        self.storage.save.assert_called_once()
        self.assertCountEqual(self.storage.delete.call_args_list, [mock.call(old_name), mock.call(self.stored_name)])
        self.assertFalse(MediaDeletionJob.objects.exists())
