"""Personal-data minimization without undoing financial anti-replay state."""

from unittest import mock
import uuid

from django.core.files.storage import Storage
from django.test import TestCase
from django.utils import timezone

from capp.classes.auth.auth import Auth
from capp.models import (
    CharacterFlag,
    CharacterGalleryImage,
    Conversation,
    GuestIdentity,
    GuestTokenQuota,
    GuestWebSocketTicket,
    MediaDeletionJob,
    Message,
    MessageFlag,
    SparkPool,
    SparkTransaction,
)
from capp.tasks import retry_media_deletions
from capp.tests import _make_character, _make_user


class ErasureRetentionTests(TestCase):
    def setUp(self):
        self.user = _make_user(username="erase-metadata", email="erase@example.test")
        self.other = _make_user(username="keep-metadata", email="keep@example.test")

    def test_claimed_guest_reports_credentials_and_network_metadata_are_scrubbed(self):
        identity = GuestIdentity.objects.create(
            credential_hash="a" * 64,
            previous_credential_hash="b" * 64,
            expires_at=timezone.now(),
            status=GuestIdentity.Status.CLAIMED,
            claimed_by=self.user,
            claimed_at=timezone.now(),
        )
        quota = GuestTokenQuota.objects.create(
            session_id=identity.owner_key,
            ip_address="192.0.2.1",
            user_agent="Private device",
            claimed_by=self.user,
            claimed_at=timezone.now(),
            sparks=0,
        )
        ticket = GuestWebSocketTicket.objects.create(
            guest_identity=identity, ticket_hash="c" * 64, channel="spark_pool", expires_at=timezone.now()
        )
        character = _make_character(name="Public", slug="retention-public")
        character_flag = CharacterFlag.objects.create(
            character=character, guest_session_id=identity.owner_key, reason="Guest PII"
        )
        conversation = Conversation.objects.create(user=self.other, character=character)
        message = Message.objects.create(conversation=conversation, sender_type="ai", content="Public test")
        message_flag = MessageFlag.objects.create(
            message=message, guest_session_id=identity.owner_key, reason="Guest PII", resolution_note="Guest PII"
        )
        unrelated = CharacterFlag.objects.create(
            character=character, guest_session_id=str(uuid.uuid4()), reason="Keep unrelated report"
        )

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        identity.refresh_from_db()
        quota.refresh_from_db()
        self.assertIsNone(identity.claimed_by_id)
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)
        self.assertNotEqual(identity.credential_hash, "a" * 64)
        self.assertIsNone(identity.previous_credential_hash)
        self.assertFalse(GuestWebSocketTicket.objects.filter(pk=ticket.pk).exists())
        self.assertIsNone(quota.ip_address)
        self.assertIsNone(quota.user_agent)
        self.assertIsNotNone(quota.claimed_at)
        for flag in (character_flag, message_flag):
            flag.refresh_from_db()
            self.assertIsNone(flag.guest_session_id)
            self.assertEqual(flag.reason, "Reporter account deleted")
        self.assertIsNone(message_flag.resolution_note)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.reason, "Keep unrelated report")

    def test_deleted_admin_identity_is_removed_from_another_users_financial_ledger(self):
        pool = SparkPool.objects.create(user=self.other, sparks=10)
        ledger = SparkTransaction.objects.create(
            user=self.other,
            spark_pool=pool,
            entry_type="credit",
            source="adjustment",
            spark_amount=10,
            description="Private administrator name",
            metadata={
                "admin_user_id": str(self.user.pk),
                "admin_username": self.user.username,
                "admin_adjustment": True,
                "reason": "Private reason",
            },
        )
        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        ledger.refresh_from_db()
        self.assertEqual(ledger.user_id, self.other.pk)
        self.assertEqual(ledger.spark_amount, 10)
        self.assertEqual(ledger.metadata, {"admin_adjustment": True})
        self.assertIsNone(ledger.description)

    def test_storage_failure_keeps_durable_job_and_retry_removes_it(self):
        character = _make_character(name="Shared", slug="storage-shared")
        image = CharacterGalleryImage.objects.create(
            character=character, created_by=self.user, visibility="private", image="private/test-erasure.png"
        )
        image_id = image.pk
        storage = mock.Mock(spec=Storage)
        with mock.patch.object(CharacterGalleryImage._meta.get_field("image"), "storage", storage):
            storage.delete.side_effect = OSError("storage temporarily unavailable")
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
            self.assertFalse(CharacterGalleryImage.objects.filter(pk=image_id).exists())
            job = MediaDeletionJob.objects.get(file_name="private/test-erasure.png")
            storage.delete.side_effect = None
            self.assertEqual(retry_media_deletions(), 1)
            self.assertFalse(MediaDeletionJob.objects.filter(pk=job.pk).exists())
            self.assertEqual(storage.delete.call_count, 2)

    def test_shared_file_reference_is_not_destroyed_with_private_gallery_row(self):
        character = _make_character(name="Shared", slug="same-file")
        for owner, visibility in ((self.user, "private"), (self.other, "public")):
            CharacterGalleryImage.objects.create(
                character=character, created_by=owner, visibility=visibility, image="shared/still-in-use.png"
            )
        storage = mock.Mock(spec=Storage)
        with mock.patch.object(CharacterGalleryImage._meta.get_field("image"), "storage", storage):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        storage.delete.assert_not_called()
        self.assertTrue(CharacterGalleryImage.objects.filter(created_by=self.other).exists())
        self.assertFalse(MediaDeletionJob.objects.exists())
