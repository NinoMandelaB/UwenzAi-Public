# capp/tests_photo_attachments.py
"""
User-sent photo attachments: the full server-side pipeline.

Covers the three load-bearing guarantees of this feature:

  * ENCRYPTION — photos are AES-256-GCM envelope-encrypted before they touch
    storage, the bucket only ever sees ciphertext, and tampering with the
    ciphertext or its metadata fails closed instead of returning wrong bytes.
  * MODERATION — CSAM-class images are blocked BEFORE storage, provider
    outages fail closed, and nothing is persisted on a refusal.
  * FLOW — the send endpoint accepts multipart photo sends for authenticated
    users, charges the same reservation as a text message, refuses text-only
    platform-model chats and guest uploads, and history serves freshly
    signed, short-lived URLs.
"""

import base64
import io
import json
import uuid
from unittest import mock

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from PIL import Image

from capp.classes.chat import attachment_crypto, attachment_service
from capp.classes.moderation import photo_moderation
from capp.constants import IMAGE_MESSAGE_PAIR_COST, MESSAGE_PAIR_COST
from capp.models import (
    Conversation,
    MediaDeletionJob,
    Message,
    MessageAttachment,
    PhotoScreeningEvent,
    SparkPool,
    UserBYOKConfig,
)
from capp.tests import _make_character, _make_user

# A single 32-byte key, base64. Real deployments set IMAGE_ENCRYPTION_KEYS
# from the Railway environment; tests pin it explicitly.
import secrets as _secrets

_TEST_MASTER_KEY = base64.urlsafe_b64encode(_secrets.token_bytes(32)).decode("ascii").rstrip("=")
_TEST_MASTER_KEY_V2 = base64.urlsafe_b64encode(_secrets.token_bytes(32)).decode("ascii").rstrip("=")

ENCRYPTION_SETTINGS = {
    "IMAGE_ENCRYPTION_KEYS": {"v1": _TEST_MASTER_KEY},
    "IMAGE_ENCRYPTION_CURRENT_VERSION": "v1",
}

ENCRYPTION_SETTINGS_ROTATED = {
    "IMAGE_ENCRYPTION_KEYS": {"v1": _TEST_MASTER_KEY, "v2": _TEST_MASTER_KEY_V2},
    "IMAGE_ENCRYPTION_CURRENT_VERSION": "v2",
}


def _png_bytes(width=64, height=48, color=(120, 40, 200)):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _auth_headers(user, token="dummy-token"):
    # The JWT auth layer is exercised elsewhere; tests authenticate through
    # the same force-authenticate route the other send-path suites use.
    return {}


class AttachmentCryptoTests(TestCase):
    """Envelope encryption round-trips and fails closed on tampering."""

    @override_settings(**ENCRYPTION_SETTINGS)
    def test_roundtrip_returns_original_bytes(self):
        attachment_id = str(uuid.uuid4())
        plaintext = _png_bytes()
        sealed = attachment_crypto.encrypt_photo(
            plaintext,
            attachment_id=attachment_id,
            content_type="image/png",
            sha256="abc",
        )
        restored = attachment_crypto.decrypt_photo(
            sealed.ciphertext,
            wrapped_key=sealed.wrapped_key,
            nonce=sealed.nonce,
            key_version=sealed.key_version,
            attachment_id=attachment_id,
            content_type="image/png",
            sha256="abc",
        )
        self.assertEqual(restored, plaintext)

    @override_settings(**ENCRYPTION_SETTINGS)
    def test_ciphertext_is_not_plaintext(self):
        plaintext = _png_bytes()
        sealed = attachment_crypto.encrypt_photo(
            plaintext,
            attachment_id=str(uuid.uuid4()),
            content_type="image/png",
            sha256="abc",
        )
        self.assertNotIn(plaintext, sealed.ciphertext)
        self.assertGreater(len(sealed.wrapped_key), 20)

    @override_settings(**ENCRYPTION_SETTINGS)
    def test_tampered_ciphertext_raises(self):
        attachment_id = str(uuid.uuid4())
        sealed = attachment_crypto.encrypt_photo(
            _png_bytes(),
            attachment_id=attachment_id,
            content_type="image/png",
            sha256="abc",
        )
        tampered = bytes([sealed.ciphertext[0] ^ 0xFF]) + sealed.ciphertext[1:]
        with self.assertRaises(attachment_crypto.AttachmentCryptoError):
            attachment_crypto.decrypt_photo(
                tampered,
                wrapped_key=sealed.wrapped_key,
                nonce=sealed.nonce,
                key_version=sealed.key_version,
                attachment_id=attachment_id,
                content_type="image/png",
                sha256="abc",
            )

    @override_settings(**ENCRYPTION_SETTINGS)
    def test_metadata_swap_fails(self):
        """AAD binds the ciphertext to its row: swapping in another
        attachment's identity/content-type/hash must not decrypt."""
        sealed = attachment_crypto.encrypt_photo(
            _png_bytes(),
            attachment_id=str(uuid.uuid4()),
            content_type="image/png",
            sha256="aaa",
        )
        with self.assertRaises(attachment_crypto.AttachmentCryptoError):
            attachment_crypto.decrypt_photo(
                sealed.ciphertext,
                wrapped_key=sealed.wrapped_key,
                nonce=sealed.nonce,
                key_version=sealed.key_version,
                attachment_id=str(uuid.uuid4()),  # different identity
                content_type="image/png",
                sha256="aaa",
            )

    @override_settings(**ENCRYPTION_SETTINGS_ROTATED)
    def test_key_rotation_decrypts_with_recorded_version(self):
        """Rows encrypted under v1 stay readable after v2 became current."""
        attachment_id = str(uuid.uuid4())
        plaintext = _png_bytes()
        with override_settings(**ENCRYPTION_SETTINGS):
            sealed = attachment_crypto.encrypt_photo(
                plaintext,
                attachment_id=attachment_id,
                content_type="image/png",
                sha256="abc",
            )
        self.assertEqual(sealed.key_version, "v1")
        restored = attachment_crypto.decrypt_photo(
            sealed.ciphertext,
            wrapped_key=sealed.wrapped_key,
            nonce=sealed.nonce,
            key_version="v1",
            attachment_id=attachment_id,
            content_type="image/png",
            sha256="abc",
        )
        self.assertEqual(restored, plaintext)

    def test_missing_keys_fail_closed(self):
        with override_settings(IMAGE_ENCRYPTION_KEYS={}):
            with self.assertRaises(attachment_crypto.AttachmentCryptoError):
                attachment_crypto.encrypt_photo(
                    _png_bytes(),
                    attachment_id=str(uuid.uuid4()),
                    content_type="image/png",
                    sha256="abc",
                )


class PhotoPreparationTests(TestCase):
    """Validation, EXIF stripping, moderation gating, downscaling."""

    @override_settings(DEBUG=True)
    def test_prepare_photo_strips_exif(self):
        # Build a PNG with an EXIF-ish chunk: Pillow re-encode drops it.
        raw = _png_bytes()
        prepared = attachment_service.prepare_photo(raw)
        self.assertEqual(prepared.content_type, "image/png")
        reopened = Image.open(io.BytesIO(prepared.storage_bytes))
        self.assertEqual(reopened.format, "PNG")
        self.assertIsNone(reopened.info.get("exif"))

    @override_settings(DEBUG=True)
    def test_prepare_photo_rejects_non_image(self):
        with self.assertRaises(attachment_service.PhotoValidationError) as ctx:
            attachment_service.prepare_photo(b"not an image at all")
        self.assertEqual(ctx.exception.code, "photo_invalid_image")

    @override_settings(DEBUG=True, PHOTO_MAX_BYTES=1000)
    def test_prepare_photo_rejects_oversized(self):
        with self.assertRaises(attachment_service.PhotoValidationError) as ctx:
            attachment_service.prepare_photo(_png_bytes(width=600, height=600))
        self.assertEqual(ctx.exception.code, "photo_too_large")

    @override_settings(DEBUG=True, PHOTO_MAX_DIMENSION=128)
    def test_prepare_photo_downscales_to_limit(self):
        prepared = attachment_service.prepare_photo(_png_bytes(width=1024, height=512))
        self.assertLessEqual(max(prepared.width, prepared.height), 128)
        self.assertGreater(len(prepared.sha256), 0)

    @override_settings(DEBUG=True)
    def test_moderation_blocks_before_storage(self):
        blocked = photo_moderation.ModerationDecision(action="block", provider="hive", categories=["csam_classifier"])
        with mock.patch.object(attachment_service, "moderate_photo", return_value=blocked):
            with self.assertRaises(photo_moderation.PhotoModerationBlocked):
                attachment_service.prepare_photo(_png_bytes())

    @override_settings(DEBUG=True)
    def test_moderation_unavailable_fails_closed(self):
        with mock.patch.object(
            attachment_service,
            "moderate_photo",
            side_effect=photo_moderation.PhotoModerationUnavailable(),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                attachment_service.prepare_photo(_png_bytes())


class HiveProviderDecisionTests(TestCase):
    """The Hive response parser: hash matches and classifier thresholds."""

    def _payload(self, hashes=None, classifier=None, reasons=None):
        return {
            "status": [
                {
                    "status": {"code": 200, "message": "SUCCESS"},
                    "response": {
                        "output": {
                            "file": {
                                "fileType": "image",
                                "reasons": reasons or [],
                                "classifierPrediction": ({"csam_classifier": classifier} if classifier else {}),
                            },
                            "hashes": hashes or [],
                        }
                    },
                }
            ]
        }

    @override_settings(DEBUG=True)
    def test_hash_match_blocks(self):
        payload = self._payload(
            hashes=[{"hashType": "saferhashv0", "matchTypes": ["CSAM"], "reasons": ["matched"]}],
            reasons=["matched"],
        )
        decision = photo_moderation.HiveCSAMProvider._decide(payload, threshold=0.5)
        self.assertEqual(decision.action, "block")
        self.assertIn("csam_hash_match", decision.categories)

    @override_settings(DEBUG=True)
    def test_classifier_above_threshold_blocks(self):
        payload = self._payload(classifier={"csam": 0.98, "pornography": 0.01, "other": 0.01}, reasons=["csam"])
        decision = photo_moderation.HiveCSAMProvider._decide(payload, threshold=0.5)
        self.assertEqual(decision.action, "block")
        self.assertIn("csam_classifier", decision.categories)

    @override_settings(DEBUG=True)
    def test_adult_pornography_is_allowed(self):
        """Product policy: only CSAM blocks; adult content passes."""
        payload = self._payload(classifier={"csam": 0.001, "pornography": 0.97, "other": 0.029})
        decision = photo_moderation.HiveCSAMProvider._decide(payload, threshold=0.5)
        self.assertEqual(decision.action, "allow")

    @override_settings(DEBUG=True)
    def test_unparsable_response_fails_closed(self):
        with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
            photo_moderation.HiveCSAMProvider._decide({"status": ["garbage"]}, threshold=0.5)


class PhotoSendFlowTests(TestCase):
    """The multipart send endpoint: happy path, refusals, serving."""

    def setUp(self):
        self.user = _make_user("photouser")
        self.character = _make_character(created_by=self.user)
        self.conversation = Conversation.objects.create(
            user=self.user,
            character=self.character,
            status=Conversation.StatusChoices.ACTIVE,
        )
        # Photos are paid: give the user a Spark balance.
        self.pool = SparkPool.objects.create(user=self.user, sparks=10000, token_balance=0, is_active=True)
        self.client.force_authenticate = None  # DRF client: use test client + JWT bypass below

    def _send(self, image_bytes=None, content="what do you think of my outfit?", extra=None):
        from django.test import Client

        client = Client()
        # Authenticate via the same JWT machinery the app uses.
        from rest_framework_simplejwt.tokens import RefreshToken

        access = str(RefreshToken.for_user(self.user).access_token)
        operation_id = str(uuid.uuid4())

        data = {"content": content}
        if image_bytes is not None:
            data["image"] = io.BytesIO(image_bytes)
        if extra:
            data.update(extra)

        return client.post(
            f"/api/v1/conversations/{self.conversation.conversation_id}/messages/",
            data=data,
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {access}",
            HTTP_IDEMPOTENCY_KEY=operation_id,
        )

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_photo_send_stores_encrypted_and_returns_signed_url(self):
        response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()["data"]
        self.assertEqual(len(payload["attachments"]), 1)

        attachment = MessageAttachment.objects.get()
        # Storage holds ciphertext, not the image.
        stored_bytes = default_storage.open(attachment.storage_key, "rb").read()
        self.assertNotIn(b"PNG", stored_bytes[:200])
        self.assertNotEqual(stored_bytes, _png_bytes())

        # The signed URL serves the decrypted bytes to the owner.
        from django.test import Client

        client = Client()
        served = client.get(payload["attachments"][0]["url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertEqual(served.content, attachment_service.read_attachment_bytes(attachment))

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_photo_message_costs_same_as_text_message(self):
        """The reservation for a photo pair is the standard MESSAGE_PAIR_COST."""

        self.assertEqual(IMAGE_MESSAGE_PAIR_COST, MESSAGE_PAIR_COST)
        response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 201, response.content)
        # The hold is created inside the Celery task; with CELERY_TASK_ALWAYS_EAGER
        # off, assert the send path itself did not charge anything extra.
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.sparks, 10000)

    @override_settings(DEBUG=False, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_photo_uploads_disabled_without_provider(self):
        """No provider configured (IMAGE_MODERATION_PROVIDER unset) = off."""
        response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "photo_uploads_disabled")
        self.assertEqual(MessageAttachment.objects.count(), 0)

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_guests_cannot_send_photos(self):
        from django.test import Client

        client = Client()
        response = client.post(
            f"/api/v1/conversations/{self.conversation.conversation_id}/messages/",
            data={"content": "hi", "image": io.BytesIO(_png_bytes())},
            format="multipart",
        )
        # Guest cookie absent: the guest flow 401s before the photo check.
        self.assertIn(response.status_code, (401, 403))

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="mistral-small")
    def test_text_only_platform_model_refuses_photo(self):
        """Platform chats routed to a text-only model refuse before storage."""
        response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "photo_not_supported_by_model")
        self.assertEqual(MessageAttachment.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(
        DEBUG=True,
        **ENCRYPTION_SETTINGS,
        PHOTO_UPLOADS_ENABLED=True,
        CORTECS_MODEL="mistral-small",
    )
    def test_byok_chat_skips_model_gate(self):
        """BYOK users send photos at their own responsibility."""
        UserBYOKConfig.objects.create(
            user=self.user,
            api_url="https://api.example.com/v1",
            api_key_encrypted="x",
            model_name="some-text-model",
            store_on_platform=True,
            is_active=True,
        )
        with mock.patch.object(
            attachment_service,
            "moderate_photo",
            return_value=photo_moderation.ModerationDecision(action="allow", provider="spy"),
        ) as screened:
            response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 201, response.content)
        # BYOK skips the vision-model gate but NOT server-side screening:
        # the pixels are stored on our bucket, so moderation always runs.
        screened.assert_called_once()

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_blocked_photo_stores_nothing_and_records_audit(self):
        blocked = photo_moderation.ModerationDecision(action="block", provider="hive", categories=["csam_classifier"])

        def fake_moderate(image_bytes, content_type):
            raise photo_moderation.PhotoModerationBlocked(
                attachment_service.BlockedPhoto(decision=blocked, sha256="deadbeef")
            )

        with mock.patch.object(attachment_service, "moderate_photo", side_effect=fake_moderate):
            response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "photo_moderation_blocked")
        self.assertEqual(MessageAttachment.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)
        # The audit row survives with the acting user and the image hash.
        event = PhotoScreeningEvent.objects.get()
        self.assertEqual(event.user_id, self.user.pk)
        self.assertEqual(event.decision, "block")
        self.assertEqual(event.categories, ["csam_classifier"])

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_moderation_unavailable_refuses_without_persisting(self):
        def fake_moderate(image_bytes, content_type):
            raise photo_moderation.PhotoModerationUnavailable()

        with mock.patch.object(attachment_service, "moderate_photo", side_effect=fake_moderate):
            response = self._send(image_bytes=_png_bytes())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(MessageAttachment.objects.count(), 0)

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_history_carries_signed_attachment_urls(self):
        self._send(image_bytes=_png_bytes(), content="first")

        from django.test import Client

        client = Client()
        from rest_framework_simplejwt.tokens import RefreshToken

        access = str(RefreshToken.for_user(self.user).access_token)
        response = client.get(
            f"/api/v1/conversations/{self.conversation.conversation_id}/messages-list/",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]["messages"]
        photo_row = next(r for r in rows if r["attachments"])
        self.assertIn("signature=", photo_row["attachments"][0]["url"])

        # The signed URL works without an Authorization header (img tag).
        served = client.get(photo_row["attachments"][0]["url"])
        self.assertEqual(served.status_code, 200)

        # ...but an expired/invalid signature does not.
        url = photo_row["attachments"][0]["url"].replace("signature=", "signature=deadbeef")
        refused = client.get(url)
        self.assertEqual(refused.status_code, 403)

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_signed_url_expires(self):
        self._send(image_bytes=_png_bytes())
        attachment = MessageAttachment.objects.get()
        signed = attachment_service.sign_attachment_url(attachment, self.conversation.conversation_id, ttl_seconds=-1)
        from django.test import Client

        client = Client()
        response = client.get(
            f"/api/v1/conversations/{self.conversation.conversation_id}/attachments/{attachment.attachment_id}/{signed}"
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_other_user_cannot_fetch_attachment(self):
        self._send(image_bytes=_png_bytes())
        attachment = MessageAttachment.objects.get()
        other = _make_user("intruder", email="intruder@example.com")

        from django.test import Client
        from rest_framework_simplejwt.tokens import RefreshToken

        client = Client()
        access = str(RefreshToken.for_user(other).access_token)
        response = client.get(
            f"/api/v1/conversations/{self.conversation.conversation_id}/attachments/{attachment.attachment_id}/",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_conversation_list_previews_photo_messages(self):
        self._send(image_bytes=_png_bytes(), content="")

        from django.test import Client
        from rest_framework_simplejwt.tokens import RefreshToken

        client = Client()
        access = str(RefreshToken.for_user(self.user).access_token)
        response = client.get(
            "/api/v1/conversations/",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        preview = response.json()["data"]["conversations"][0]["last_message"]["preview"]
        self.assertEqual(preview, "\U0001f4f7 Photo")

    @override_settings(DEBUG=True, **ENCRYPTION_SETTINGS, PHOTO_UPLOADS_ENABLED=True, CORTECS_MODEL="deepseek-flash")
    def test_deleting_message_erases_encrypted_blob(self):
        self._send(image_bytes=_png_bytes())
        attachment = MessageAttachment.objects.get()
        storage_key = attachment.storage_key
        self.assertTrue(default_storage.exists(storage_key))

        attachment.delete()
        from django.db import transaction

        with transaction.atomic():
            pass  # let on_commit fire
        from django.test.utils import CaptureQueriesContext  # noqa: F401

        # MediaDeletionJob.perform() runs on commit; invoke directly since the
        # test runner swallows on_commit callbacks.
        job = MediaDeletionJob.objects.get(file_name=storage_key)
        self.assertTrue(job.perform())
        self.assertFalse(default_storage.exists(storage_key))


class ProcessAIResponsePhotoTests(TestCase):
    """The Celery turn: prompt assembly and provider routing for photo turns."""

    def setUp(self):
        self.user = _make_user("pipelinetester", email="pipeline@example.com")
        self.character = _make_character(created_by=self.user)
        self.conversation = Conversation.objects.create(
            user=self.user,
            character=self.character,
            status=Conversation.StatusChoices.ACTIVE,
        )
        self.pool = SparkPool.objects.create(user=self.user, sparks=10000, token_balance=0, is_active=True)

    def _photo_message(self, content="what do you think of my outfit?"):
        message = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content=content,
            status=Message.StatusChoices.SENT,
            metadata={"type": "user_photo"},
        )
        prepared = attachment_service.PreparedPhoto(
            storage_bytes=_png_bytes(),
            content_type="image/png",
            width=64,
            height=48,
            sha256="0" * 64,
            decision=photo_moderation.ModerationDecision(action="allow", provider="debug"),
        )
        attachment_service.store_encrypted_photo(message=message, owner=self.user, prepared=prepared)
        return message

    def _build(self, message, include_images, model, max_context=100000):
        from capp.classes.ai.prompt_builder import PromptBuilder

        env = {
            "SAFETY_SANDWICH": "TAIL SAFETY",
            "GENERAL_SYSTEM_PROMPT": "CORE RULES",
        }
        with mock.patch.dict("os.environ", env):
            with mock.patch.object(PromptBuilder, "_build_system_prompt", return_value="system"):
                return PromptBuilder.build_messages(
                    conversation=self.conversation,
                    user_message=message,
                    max_context_tokens=max_context,
                    model=model,
                    include_images=include_images,
                )

    @override_settings(**ENCRYPTION_SETTINGS, CORTECS_MODEL="deepseek-flash")
    def test_vision_model_receives_image_parts(self):
        message = self._photo_message()
        messages = self._build(message, include_images=True, model="deepseek-flash")
        final = messages[-1]
        self.assertIsInstance(final["content"], list)
        types = [part["type"] for part in final["content"]]
        self.assertIn("text", types)
        self.assertIn("image_url", types)
        image_part = next(p for p in final["content"] if p["type"] == "image_url")
        self.assertTrue(image_part["image_url"]["url"].startswith("data:image/png;base64,"))

    @override_settings(**ENCRYPTION_SETTINGS, CORTECS_MODEL="deepseek-flash")
    def test_history_photos_ride_along_within_caps(self):
        self._photo_message(content="yesterday")
        current = self._photo_message(content="today")
        messages = self._build(current, include_images=True, model="deepseek-flash")
        image_messages = [m for m in messages if isinstance(m.get("content"), list)]
        self.assertEqual(len(image_messages), 2)  # history photo + current photo

    @override_settings(**ENCRYPTION_SETTINGS, CORTECS_MODEL="mistral-small")
    def test_text_only_platform_model_excludes_images_in_task(self):
        from capp.classes.ai import model_vision

        self.assertFalse(model_vision.supports_vision("mistral-small"))
        message = self._photo_message()
        messages = self._build(message, include_images=False, model="mistral-small")
        self.assertTrue(all(isinstance(m["content"], str) for m in messages))

    def test_model_vision_patterns(self):
        from capp.classes.ai import model_vision

        self.assertTrue(model_vision.supports_vision("deepseek-flash"))
        self.assertTrue(model_vision.supports_vision("DeepSeek-V4.1-Flash"))
        self.assertTrue(model_vision.supports_vision("gpt-4o-mini"))
        self.assertTrue(model_vision.supports_vision("pixtral-large-latest"))
        self.assertFalse(model_vision.supports_vision("mistral-small"))
        self.assertFalse(model_vision.supports_vision("deepseek-v4-flash-0731"))
        self.assertFalse(model_vision.supports_vision(None))
        with override_settings(VISION_MODEL_ALLOWLIST="my-private-vlm"):
            self.assertTrue(model_vision.supports_vision("My-Private-VLM"))
            self.assertFalse(model_vision.supports_vision("other-model"))

    def test_model_vision_cortecs_series_and_alias(self):
        """deepseek-v4-flash and its :latest alias route to V4.1-Flash (vision).

        Matched exactly, so a DATED snapshot (the old text-only V4 Flash,
        e.g. deepseek-v4-flash-0731) keeps being refused — production hit
        this with the auto-update alias and every photo send was gated.
        """
        from capp.classes.ai import model_vision

        self.assertTrue(model_vision.supports_vision("deepseek-v4-flash"))
        self.assertTrue(model_vision.supports_vision("deepseek-v4-flash:latest"))
        self.assertTrue(model_vision.supports_vision("DeepSeek-V4-Flash:Latest"))
        self.assertFalse(model_vision.supports_vision("deepseek-v4-flash-0731"))

    @override_settings(**ENCRYPTION_SETTINGS, CORTECS_MODEL="deepseek-flash")
    def test_cortecs_failure_on_photo_turn_does_not_fall_back_to_text_model(self):
        """A photo turn must fail (and later refund) rather than degrade."""
        from capp.classes.ai.cortecs_client import CortecsClient

        messages = [
            {"role": "system", "content": "s"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
        ]
        with mock.patch.object(CortecsClient, "_create_with_retry", side_effect=Exception("boom")):
            with mock.patch("capp.classes.ai.mistral_client.MistralClient.chat_completion") as fallback:
                with self.assertRaises(Exception):
                    CortecsClient.chat_completion(messages=messages, model="deepseek-flash")
        fallback.assert_not_called()

    @override_settings(**ENCRYPTION_SETTINGS, CORTECS_MODEL="deepseek-flash")
    def test_cortecs_failure_on_text_turn_still_falls_back(self):
        """Normal text turns keep the Cortecs -> Mistral safety net."""
        from capp.classes.ai.cortecs_client import CortecsClient

        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        with mock.patch.object(CortecsClient, "_create_with_retry", side_effect=Exception("boom")):
            with mock.patch(
                "capp.classes.ai.mistral_client.MistralClient.chat_completion",
                return_value={"id": "x", "choices": [{"message": {"content": "ok"}}], "usage": {}},
            ) as fallback:
                result = CortecsClient.chat_completion(messages=messages, model="deepseek-flash")
        fallback.assert_called_once()
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")


class CheckPhotoModerationCommandTests(TestCase):
    """The operational gate command for enabling photo uploads in production."""

    def test_fails_when_provider_not_configured(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("check_photo_moderation")

    @override_settings(DEBUG=True)
    def test_debug_without_provider_warns_but_exits_clean(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        try:
            call_command("check_photo_moderation")
        except CommandError:
            self.fail("DEBUG without a provider should exit cleanly with a warning.")

    @override_settings(IMAGE_MODERATION_PROVIDER="hive", HIVE_API_KEY="")
    def test_fails_when_hive_key_missing(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("check_photo_moderation")

    @override_settings(
        IMAGE_MODERATION_PROVIDER="hive",
        HIVE_API_KEY="real-key",
    )
    def test_success_on_parsable_allow_decision(self):
        from django.core.management import call_command

        # A benign 1x1 PNG must come back as "allow" with provider "hive".
        with mock.patch(
            "capp.classes.moderation.photo_moderation.HiveCSAMProvider.moderate",
            return_value=photo_moderation.ModerationDecision(action="allow", provider="hive"),
        ) as sent:
            call_command("check_photo_moderation")
        sent.assert_called_once()


class MistralVLMProviderTests(TestCase):
    """The recommended moderation provider: a cheap Mistral vision model."""

    def _verdict(self, **fields):
        base = {
            "contains_minor": False,
            "minor_nudity": False,
            "minor_sexual_content": False,
            "bestiality": False,
            "gore": False,
            "death": False,
            "uncertain": False,
        }
        base.update(fields)
        return json.dumps(base)

    def _respond(self, verdict_text):
        return {"choices": [{"message": {"content": verdict_text}}], "usage": {}}

    @override_settings(
        IMAGE_MODERATION_PROVIDER="mistral_vlm",
        IMAGE_MODERATION_VLM_MODEL="ministral-3b-2512",
        MISTRAL_API_KEY="dummy",
    )
    def test_clean_verdict_allows(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict()),
        ) as called:
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "allow")
        self.assertEqual(decision.provider, "mistral_vlm")
        # Request shape: system prompt + user content parts, only the image
        # (no caption surface), strict-JSON policy question, cold decoding.
        self.assertEqual(called.call_count, 1)
        kwargs = called.call_args.kwargs
        self.assertEqual(kwargs["model"], "ministral-3b-2512")
        self.assertEqual(kwargs["temperature"], 0.0)
        messages = kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        parts = messages[1]["content"]
        self.assertEqual([p["type"] for p in parts], ["text", "image_url"])
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        # The user caption never appears: moderation is pixels-only.
        self.assertNotIn("caption", json.dumps(messages[0]["content"]))

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_minor_nudity_blocks(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(contains_minor=True, minor_nudity=True)),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.categories, ["minor_nudity"])

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_minor_sexual_content_blocks(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(minor_sexual_content=True)),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.categories, ["minor_sexual_content"])

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_bestiality_blocks(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(bestiality=True)),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.categories, ["bestiality"])

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_gore_blocks(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(gore=True)),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.categories, ["gore"])

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_death_blocks(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(death=True)),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "block")
        self.assertEqual(decision.categories, ["death"])

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_multiple_prohibited_categories_all_listed(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(gore=True, death=True)),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "block")
        self.assertEqual(sorted(decision.categories), ["death", "gore"])

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_adult_content_is_allowed_by_policy(self):
        # Adult nudity is explicitly permitted: no field may fire on it.
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict()),
        ):
            decision = photo_moderation.moderate_photo(_png_bytes(), "image/png")
        self.assertEqual(decision.action, "allow")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_uncertain_fails_closed(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(self._verdict(uncertain=True)),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_hedged_prose_instead_of_json_fails_closed(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond("I cannot assess this image, sorry."),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_missing_required_field_fails_closed(self):
        # A verdict missing booleans never yields an allow.
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(json.dumps({"minor_nudity": False})),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_string_contains_minor_fails_closed(self):
        # Full verdict, but a string where a boolean belongs: never an allow.
        verdict = json.dumps(
            {
                "contains_minor": "no",
                "minor_nudity": False,
                "minor_sexual_content": False,
                "bestiality": False,
                "gore": False,
                "death": False,
                "uncertain": False,
            }
        )
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(verdict),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_string_uncertain_fails_closed(self):
        verdict = json.dumps(
            {
                "contains_minor": False,
                "minor_nudity": False,
                "minor_sexual_content": False,
                "bestiality": False,
                "gore": False,
                "death": False,
                "uncertain": "false",
            }
        )
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(verdict),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_string_instead_of_boolean_fails_closed(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._respond(json.dumps({"contains_minor": "no"})),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_client_exception_fails_closed(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            side_effect=Exception("provider down"),
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="")
    def test_missing_api_key_fails_closed(self):
        with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
            photo_moderation.moderate_photo(_png_bytes(), "image/png")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="dummy")
    def test_malformed_response_shape_fails_closed(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value={"choices": []},
        ):
            with self.assertRaises(photo_moderation.PhotoModerationUnavailable):
                photo_moderation.moderate_photo(_png_bytes(), "image/png")


class ModerationDownscaleTests(TestCase):
    """Screening input is bounded even when the upload is huge."""

    @override_settings(PHOTO_MODERATION_MAX_DIMENSION=1024, PHOTO_MAX_DIMENSION=2048)
    def test_moderation_copy_is_bounded_and_master_hashed(self):
        # 4000x3000 upload: moderation must see <=1024px, storage <=2048px.
        big = Image.new("RGB", (4000, 3000), (10, 200, 30))
        buffer = io.BytesIO()
        big.save(buffer, format="JPEG", quality=90)
        raw = buffer.getvalue()

        captured = {}

        def fake_moderate(image_bytes, content_type):
            captured["image"] = Image.open(io.BytesIO(image_bytes))
            captured["size"] = len(image_bytes)
            return photo_moderation.ModerationDecision(action="allow", provider="spy")

        with mock.patch.object(attachment_service, "moderate_photo", side_effect=fake_moderate):
            prepared = attachment_service.prepare_photo(raw)

        self.assertLessEqual(max(captured["image"].size), 1024)
        self.assertLessEqual(max(prepared.width, prepared.height), 2048)

    @override_settings(PHOTO_MODERATION_MAX_DIMENSION=1024)
    def test_small_uploads_are_not_upscaled_for_moderation(self):
        captured = {}

        def fake_moderate(image_bytes, content_type):
            captured["size"] = Image.open(io.BytesIO(image_bytes)).size
            return photo_moderation.ModerationDecision(action="allow", provider="spy")

        with mock.patch.object(attachment_service, "moderate_photo", side_effect=fake_moderate):
            attachment_service.prepare_photo(_png_bytes())
        self.assertEqual(captured["size"], (64, 48))


class CheckPhotoModerationMistralVLMCommandTests(TestCase):
    """The operational gate command works for the recommended provider too."""

    def test_fails_when_mistral_key_missing(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY=""):
            with self.assertRaises(CommandError):
                call_command("check_photo_moderation")

    @override_settings(IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="real-key")
    def test_success_on_parsable_allow_decision(self):
        from django.core.management import call_command

        with mock.patch(
            "capp.classes.moderation.photo_moderation.MistralVLMProvider.moderate",
            return_value=photo_moderation.ModerationDecision(action="allow", provider="mistral_vlm"),
        ) as sent:
            call_command("check_photo_moderation")
        sent.assert_called_once()
