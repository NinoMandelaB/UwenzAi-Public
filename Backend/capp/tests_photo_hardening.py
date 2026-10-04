# capp/tests_photo_hardening.py
"""
Audit 2026-10-04 photo findings F3, F4 and the client-config surface (F6).

F3: the pixel cap is enforced on the image HEADER before any pixel is
decoded, so a small crafted file declaring a huge canvas (decompression
bomb) is rejected before the multi-gigabyte allocation happens.

F4: a BLOCKED child-safety photo reaches the cross-session safety ledger as
a HIGH ``photo_child_safety`` event — visible next to the text violations,
accumulating toward the lock threshold, but never locking an account on its
own and never scoring in shadow mode.

F6: auth/me carries the effective photo-uploads flag and the upload limits
so the client can hide the camera button when photos are off and warn with
the exact maximums. Refusals carry the limits as ``details``.
"""

import io
import struct
import zlib
import uuid
from unittest import mock

from django.test import Client, TestCase, override_settings
from PIL import Image
from rest_framework_simplejwt.tokens import RefreshToken

from capp.classes.chat import attachment_service
from capp.classes.moderation import photo_moderation
from capp.classes.moderation.safety_ledger import SafetyLedgerService
from capp.models import Conversation, CustomUser, Message, SafetyEvent
from capp.tests import _make_character, _make_user
from capp.tests_photo_attachments import ENCRYPTION_SETTINGS, _png_bytes

SEND_MODE = dict(
    DEBUG=True,
    **ENCRYPTION_SETTINGS,
    PHOTO_UPLOADS_ENABLED=True,
    CORTECS_MODEL="deepseek-flash",
)


def _huge_header_png(width, height):
    """A tiny PNG whose IHDR declares a huge canvas.

    Built from a real 8x8 PNG by patching the IHDR width/height: the header
    (and therefore the declared allocation) is huge while the file itself
    stays a few hundred bytes — exactly the decompression-bomb shape. With
    the old decode-first order Pillow would try to allocate width*height
    pixels before anyone looked at the dimensions.
    """
    raw = bytearray(_png_bytes(width=8, height=8))
    # PNG: 8 signature, 4 length, 4 "IHDR", then big-endian width and height.
    struct.pack_into(">II", raw, 16, width, height)
    # The chunk CRC covers type + data, so it must be recomputed or Pillow
    # rejects the file as broken before the size is even read.
    crc = zlib.crc32(raw[12:29]) & 0xFFFFFFFF
    struct.pack_into(">I", raw, 29, crc)
    return bytes(raw)


class PhotoPixelCapTests(TestCase):
    """F3: dimension checks run on the header, before the decode."""

    @override_settings(**SEND_MODE)
    def test_oversized_header_canvas_is_rejected_before_decode(self):
        # 8000x8000 = 64 Mpx: each side is below the 12000 sanity limit, so
        # only the pixel cap catches it.
        with self.assertRaises(attachment_service.PhotoValidationError) as ctx:
            attachment_service.prepare_photo(_huge_header_png(8000, 8000))
        self.assertEqual(ctx.exception.code, "photo_dimensions")

    @override_settings(**SEND_MODE)
    def test_per_side_limit_still_rejects_tall_and_wide_images(self):
        with self.assertRaises(attachment_service.PhotoValidationError) as ctx:
            attachment_service.prepare_photo(_huge_header_png(20000, 100))
        self.assertEqual(ctx.exception.code, "photo_dimensions")
        with self.assertRaises(attachment_service.PhotoValidationError) as ctx:
            attachment_service.prepare_photo(_huge_header_png(100, 20000))
        self.assertEqual(ctx.exception.code, "photo_dimensions")

    @override_settings(**SEND_MODE)
    def test_normal_photo_within_cap_passes(self):
        prepared = attachment_service.prepare_photo(_png_bytes(width=64, height=48))
        self.assertEqual((prepared.width, prepared.height), (64, 48))

    @override_settings(**SEND_MODE)
    def test_pillow_global_limit_is_aligned_with_the_cap(self):
        # Backstop: every Pillow decode in this process (uploads, avatars,
        # gallery) is bounded by the same cap.
        self.assertEqual(Image.MAX_IMAGE_PIXELS, attachment_service._photo_max_pixels())

    @override_settings(**SEND_MODE, PHOTO_MAX_PIXELS=25_000_000)
    def test_cap_is_configurable(self):
        with self.assertRaises(attachment_service.PhotoValidationError):
            attachment_service.prepare_photo(_huge_header_png(6000, 6000))  # 36 Mpx > 25
        # 24 Mpx is inside the cap: accepted, and the storage copy is simply
        # downscaled to the usual display limit.
        prepared = attachment_service.prepare_photo(_png_bytes(width=4800, height=5000))
        self.assertLessEqual(max(prepared.width, prepared.height), 2048)


class PhotoRefusalDetailsTests(TestCase):
    """Size and dimension refusals carry the limits (frontend warning text)."""

    def setUp(self):
        self.user = _make_user("limituser")
        self.character = _make_character(created_by=self.user)
        self.conversation = Conversation.objects.create(
            user=self.user,
            character=self.character,
            status=Conversation.StatusChoices.ACTIVE,
        )

    def _send(self, image_bytes):
        client = Client()
        access = str(RefreshToken.for_user(self.user).access_token)
        return client.post(
            f"/api/v1/conversations/{self.conversation.conversation_id}/messages/",
            data={"content": "hi", "image": io.BytesIO(image_bytes)},
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {access}",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )

    @override_settings(**SEND_MODE)
    def test_dimension_refusal_carries_pixel_limits(self):
        response = self._send(_huge_header_png(9000, 9000))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["data"], "photo_dimensions")
        self.assertEqual(body["details"]["max_pixels"], 25_000_000)
        self.assertEqual(body["details"]["max_megapixels"], 25)

    @override_settings(**SEND_MODE)
    def test_size_refusal_carries_byte_limits(self):
        response = self._send(b"#" * (10 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["data"], "photo_too_large")
        self.assertEqual(body["details"]["max_bytes"], 10 * 1024 * 1024)
        self.assertEqual(body["details"]["max_mb"], 10)


class PhotoBlockLedgerTests(TestCase):
    """F4: blocked photos reach the ledger without instant-lock semantics."""

    def setUp(self):
        self.user = _make_user("photoblock")
        self.character = _make_character(created_by=self.user)
        self.conversation = Conversation.objects.create(
            user=self.user,
            character=self.character,
            status=Conversation.StatusChoices.ACTIVE,
        )

    def _block(self, sha256="a" * 64):
        blocked = photo_moderation.ModerationDecision(
            action="block", provider="mistral", categories=["csam_classifier"]
        )
        attachment_service.record_screening_failure(
            owner=self.user,
            conversation=self.conversation,
            blocked=attachment_service.BlockedPhoto(decision=blocked, sha256=sha256),
        )

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_block_records_high_photo_event_that_scores(self):
        self._block()
        event = SafetyEvent.objects.get()
        self.assertEqual(event.category, "photo_child_safety")
        self.assertEqual(event.severity, "high")
        self.assertEqual(event.action, SafetyEvent.Action.BLOCKED)
        self.assertEqual(event.is_care, False)
        self.assertEqual(event.classifier_version, "photo:mistral")
        self.assertEqual(event.fingerprint, "a" * 64)
        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 3)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_one_block_never_locks_the_account(self):
        self._block()
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_repeated_blocks_accumulate_toward_the_threshold(self):
        for index in range(4):
            self._block(sha256=f"{index:064x}")
        # 4 x high = 12 points: at or above the default threshold of 10.
        self.assertGreaterEqual(SafetyLedgerService.active_score(self.user.pk), 10)
        # Still no automatic lock from photos alone: the threshold can only
        # trip through a text violation or an administrator.
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    @override_settings(SAFETY_LEDGER_MODE="shadow")
    def test_shadow_mode_records_but_never_scores(self):
        self._block()
        event = SafetyEvent.objects.get()
        self.assertEqual(event.action, SafetyEvent.Action.SHADOWED)
        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 0)

    @override_settings(SAFETY_LEDGER_MODE="off")
    def test_off_mode_records_nothing(self):
        self._block()
        self.assertEqual(SafetyEvent.objects.count(), 0)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_same_photo_within_dedup_window_records_once(self):
        self._block(sha256="b" * 64)
        self._block(sha256="b" * 64)
        self.assertEqual(SafetyEvent.objects.count(), 1)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_reversed_photo_event_stops_counting(self):
        self._block()
        event = SafetyEvent.objects.get()
        event.is_reversed = True
        event.save(update_fields=["is_reversed"])
        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 0)

    def test_unavailable_screening_records_no_safety_event(self):
        unavailable = photo_moderation.ModerationDecision(action="unavailable", provider="mistral")
        attachment_service.record_screening_failure(
            owner=self.user,
            conversation=self.conversation,
            blocked=attachment_service.BlockedPhoto(decision=unavailable, sha256="c" * 64),
        )
        self.assertEqual(SafetyEvent.objects.count(), 0)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_guest_owner_records_nothing(self):
        blocked = photo_moderation.ModerationDecision(
            action="block", provider="mistral", categories=["csam_classifier"]
        )
        attachment_service.record_screening_failure(
            owner=None,
            conversation=None,
            blocked=attachment_service.BlockedPhoto(decision=blocked, sha256="d" * 64),
        )
        self.assertEqual(SafetyEvent.objects.count(), 0)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_ledger_failure_never_breaks_the_refusal(self):
        with mock.patch.object(
            SafetyLedgerService,
            "record_photo_block",
            side_effect=RuntimeError("ledger down"),
        ):
            self._block()  # must not raise
        self.assertEqual(SafetyEvent.objects.count(), 0)

    @override_settings(**{**SEND_MODE, "SAFETY_LEDGER_MODE": "shadow"})
    def test_blocked_send_creates_both_audit_row_and_ledger_event(self):
        pool_user = self.user
        blocked = photo_moderation.ModerationDecision(
            action="block", provider="mistral", categories=["csam_classifier"]
        )

        def fake_moderate(image_bytes, content_type):
            raise photo_moderation.PhotoModerationBlocked(
                attachment_service.BlockedPhoto(decision=blocked, sha256="e" * 64)
            )

        client = Client()
        access = str(RefreshToken.for_user(pool_user).access_token)
        with mock.patch.object(attachment_service, "moderate_photo", side_effect=fake_moderate):
            response = client.post(
                f"/api/v1/conversations/{self.conversation.conversation_id}/messages/",
                data={"content": "look", "image": io.BytesIO(_png_bytes())},
                format="multipart",
                HTTP_AUTHORIZATION=f"Bearer {access}",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "photo_moderation_blocked")
        self.assertEqual(Message.objects.count(), 0)
        event = SafetyEvent.objects.get()
        self.assertEqual(event.category, "photo_child_safety")
        self.assertEqual(event.action, SafetyEvent.Action.SHADOWED)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_admin_safety_summary_reports_photo_block_count(self):
        from capp.models import SparkPool

        SparkPool.objects.create(user=self.user, sparks=100, token_balance=0, is_active=True)
        admin = _make_user("photoblockadmin", email="photoblockadmin@example.com", verified=True, is_staff=True)
        self._block()
        client = Client()
        access = str(RefreshToken.for_user(admin).access_token)
        response = client.get(
            f"/api/v1/admin/users/{self.user.user_id}/safety/",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(response.status_code, 200)
        summary = response.json()["data"]["summary"]
        self.assertEqual(summary["photo_block_count"], 1)
        self.assertEqual(summary["category_counts"].get("photo_child_safety"), 1)


class PhotoClientConfigTests(TestCase):
    """F6: auth/me carries the flag and the limits."""

    def test_me_includes_photo_availability_and_limits(self):
        user = _make_user("configuser", verified=True)
        client = Client()
        access = str(RefreshToken.for_user(user).access_token)
        response = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]["user"]
        # Default deployment: env opt-in is off, so the client hides the
        # camera button instead of offering a dead feature.
        self.assertIs(payload["photo_uploads_enabled"], False)
        self.assertEqual(payload["photo_max_bytes"], 10 * 1024 * 1024)
        self.assertEqual(payload["photo_max_megapixels"], 25)

    @override_settings(DEBUG=True, PHOTO_UPLOADS_ENABLED=True)
    def test_me_reflects_enabled_photos(self):
        user = _make_user("configuser2", verified=True)
        client = Client()
        access = str(RefreshToken.for_user(user).access_token)
        response = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {access}")
        payload = response.json()["data"]["user"]
        # DEBUG provides the screening provider, so the feature is on.
        self.assertIs(payload["photo_uploads_enabled"], True)
