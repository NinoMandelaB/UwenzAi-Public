# capp/classes/chat/attachment_service.py
"""
User-sent chat photos: validation, normalisation, encrypted storage and
serving.

Pipeline for one uploaded photo (all server-side, before the message row
exists):

  1. Validate the upload: format allowlist verified against the actual
     pixels (not the declared MIME), size cap, sane dimensions.
  2. Normalise to an EXIF-free "master": every file is re-encoded through
     Pillow, which drops EXIF/GPS metadata and applies orientation. The
     master keeps full resolution.
  3. Screen a bounded, metadata-free copy with the photo moderation layer
     (downscaled to ``PHOTO_MODERATION_MAX_DIMENSION``, default 1024px — the
     child-safety signal does not need full resolution, and bounding the
     input bounds provider tokens and send latency). The audit sha256 hashes
     the full master. Blocked or unavailable -> nothing is stored, the send
     is refused.
  4. Produce the storage copy: downscaled to at most
     ``PHOTO_MAX_DIMENSION`` (2048px is plenty for both the chat UI and the
     vision models, which downscale further anyway) and re-encoded.
  5. Envelope-encrypt the storage copy and write it to the PRIVATE bucket
     under ``chat/attachments/<uuid>.bin``. Only ciphertext is stored; the
     object carries no filename, MIME, or user-identifying metadata.

Serving: photos never get public bucket URLs. The browser loads them from a
decryption endpoint that accepts either the owner's normal JWT auth or a
short-lived HMAC-signed URL (generated per history response, never stored),
because ``<img>`` tags cannot send Authorization headers.
"""

import hashlib
import hmac
import io
import logging
import time
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image

from capp.classes.chat import attachment_crypto
from capp.classes.moderation.photo_moderation import (
    ModerationDecision,
    PhotoModerationBlocked,
    moderate_photo,
)
from capp.models import PhotoScreeningEvent
from capp.classes.moderation.safety_ledger import SafetyLedgerService

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")


def _photo_max_pixels() -> int:
    return max(1, int(getattr(settings, "PHOTO_MAX_PIXELS", 25 * 1000 * 1000)))


# Defense in depth for every Pillow decode in this process (uploads, avatars,
# gallery): Pillow errors above 2x this limit and warns above 1x. Set once at
# import AND re-synced inside _load_image: override_settings in tests (or a
# settings reload) would otherwise leave the backstop at the import-time value.
Image.MAX_IMAGE_PIXELS = _photo_max_pixels()

# Pillow save format per content type.
_SAVE_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


class PhotoValidationError(Exception):
    """The upload is not acceptable (bad type, size, pixels)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PreparedPhoto:
    """A validated, screened, storage-ready photo."""

    storage_bytes: bytes  # EXIF-free, downscaled, re-encoded
    content_type: str
    width: int
    height: int
    sha256: str  # of storage_bytes — dedupe/audit identity
    decision: ModerationDecision


@dataclass(frozen=True)
class BlockedPhoto:
    """A refused upload: what was blocked, and the audit hash."""

    decision: ModerationDecision
    sha256: str  # of the EXIF-free master — repeat-offender matching


def photo_uploads_enabled() -> bool:
    """Whether photo sending is available in this deployment.

    Production requires BOTH the env opt-in and a moderation provider (or
    DEBUG). Without a screening provider the feature stays off rather than
    storing unscreened photos.
    """
    if not getattr(settings, "PHOTO_UPLOADS_ENABLED", False):
        return False
    from capp.classes.moderation.photo_moderation import moderation_provider_name

    return moderation_provider_name() is not None or settings.DEBUG


def photo_upload_limits() -> dict:
    """The photo upload limits, for the client config and error details."""
    max_bytes = int(getattr(settings, "PHOTO_MAX_BYTES", 10 * 1024 * 1024))
    max_pixels = _photo_max_pixels()
    return {
        "max_bytes": max_bytes,
        "max_mb": round(max_bytes / (1024 * 1024)),
        "max_pixels": max_pixels,
        "max_megapixels": round(max_pixels / 1_000_000),
    }


def _validate_size(size: int) -> None:
    max_bytes = int(getattr(settings, "PHOTO_MAX_BYTES", 10 * 1024 * 1024))
    if size <= 0:
        raise PhotoValidationError("photo_empty")
    if size > max_bytes:
        raise PhotoValidationError("photo_too_large")


def _load_image(raw: bytes) -> Image.Image:
    """Decode the upload from its actual bytes (magic bytes, not filename).

    The dimension checks run on the HEADER before any pixel is decoded: a
    tiny crafted PNG/JPEG can declare a huge canvas and the decode itself is
    what exhausts memory (decompression bomb). Image.open reads the header
    only, so an oversized canvas is rejected before the multi-gigabyte
    allocation ever happens (audit F3).
    """
    max_pixels = _photo_max_pixels()
    # Keep Pillow's global backstop in sync with the current setting (it is
    # also set at import time, but the setting can change under this process).
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        image = Image.open(io.BytesIO(raw))
        width, height = image.size  # from the header, no decode yet
        if image.format not in ("JPEG", "PNG", "WEBP"):
            raise PhotoValidationError("photo_unsupported_format")
        if width < 8 or height < 8:
            raise PhotoValidationError("photo_invalid_image")
        if width > 12000 or height > 12000 or width * height > max_pixels:
            raise PhotoValidationError("photo_dimensions")
        image.load()
    except PhotoValidationError:
        raise
    except Image.DecompressionBombError:
        # Pillow's own backstop (2x MAX_IMAGE_PIXELS) fired at open time:
        # the header declares an absurd canvas. Same refusal, same code.
        raise PhotoValidationError("photo_dimensions") from None
    except Exception:
        raise PhotoValidationError("photo_invalid_image") from None
    return image


def _encode(image: Image.Image, content_type: str) -> bytes:
    buffer = io.BytesIO()
    fmt = _SAVE_FORMATS[content_type]
    if fmt == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.save(buffer, format=fmt, quality=90)
    return buffer.getvalue()


def _exif_free_master(image: Image.Image) -> Image.Image:
    """Orientation-baked, metadata-free copy of the decoded upload.

    ``ImageOps.exif_transpose`` bakes the EXIF orientation into the pixels
    (a rotated phone photo is stored upright); the re-encode in
    :func:`_encode` then drops all metadata blocks, including GPS.
    """
    from PIL import ImageOps

    transposed = ImageOps.exif_transpose(image)
    return image if transposed is None else transposed


def prepare_photo(raw: bytes) -> PreparedPhoto:
    """Validate, EXIF-strip, screen and downscale one upload.

    Raises :class:`PhotoValidationError` for unusable uploads,
    :class:`PhotoModerationBlocked` (carrying the audit hash) when the image
    matches the child-safety policy, and :class:`PhotoModerationUnavailable`
    when screening could not run — in every refusal case nothing is stored.
    """
    _validate_size(len(raw))
    image = _load_image(raw)

    # Content type follows the actual decoded format.
    content_type = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }[image.format]

    # --- Master: full resolution, EXIF stripped, orientation applied. ---
    master = _exif_free_master(image)
    master_bytes = _encode(master, content_type)
    master_sha256 = hashlib.sha256(master_bytes).hexdigest()

    # --- Screen a bounded, metadata-free copy BEFORE any storage. ---
    # Downscaled from the master (PHOTO_MODERATION_MAX_DIMENSION, default
    # 1024px): bounds provider tokens and send latency without losing the
    # signal child-safety classification needs. The master sha256 stays the
    # audit anchor; nothing is stored until the decision is "allow".
    moderation_image = master
    moderation_max = int(getattr(settings, "PHOTO_MODERATION_MAX_DIMENSION", 1024))
    if max(master.size) > moderation_max:
        moderation_image = master.copy()
        moderation_image.thumbnail((moderation_max, moderation_max), Image.LANCZOS)
    decision = moderate_photo(_encode(moderation_image, content_type), content_type)
    if decision.action == "block":
        raise PhotoModerationBlocked(BlockedPhoto(decision=decision, sha256=master_sha256))

    # --- Storage copy: downscaled for display + VLM. ---
    max_dim = int(getattr(settings, "PHOTO_MAX_DIMENSION", 2048))
    stored = master
    if max(master.size) > max_dim:
        stored = master.copy()
        stored.thumbnail((max_dim, max_dim), Image.LANCZOS)

    storage_bytes = _encode(stored, content_type)
    width, height = stored.size

    return PreparedPhoto(
        storage_bytes=storage_bytes,
        content_type=content_type,
        width=width,
        height=height,
        sha256=hashlib.sha256(storage_bytes).hexdigest(),
        decision=decision,
    )


def store_encrypted_photo(
    *,
    message,
    owner,
    prepared: PreparedPhoto,
) -> "object":
    """Encrypt and store a prepared photo, creating the MessageAttachment row.

    Returns the saved ``MessageAttachment``.
    """
    from capp.models import MessageAttachment

    decision = prepared.decision
    attachment_id = uuid.uuid4()
    encrypted = attachment_crypto.encrypt_photo(
        prepared.storage_bytes,
        attachment_id=str(attachment_id),
        content_type=prepared.content_type,
        sha256=prepared.sha256,
    )

    # Random opaque key: no filename, no user identity, no extension hints.
    storage_key = f"chat/attachments/{uuid.uuid4()}.bin"
    default_storage.save(storage_key, ContentFile(encrypted.ciphertext))

    attachment = MessageAttachment(
        attachment_id=attachment_id,
        message=message,
        owner=owner,
        storage_key=storage_key,
        content_type=prepared.content_type,
        byte_size=len(encrypted.ciphertext),
        width=prepared.width,
        height=prepared.height,
        sha256=prepared.sha256,
        key_version=encrypted.key_version,
        wrapped_key=encrypted.wrapped_key,
        nonce=encrypted.nonce,
        moderation_status=MessageAttachment.ModerationStatus.APPROVED,
        moderation_provider=decision.provider,
        moderation_categories=decision.detail if isinstance(decision.detail, dict) else {},
    )
    attachment.save()
    return attachment


def record_screening_failure(
    *,
    owner,
    conversation,
    blocked: BlockedPhoto,
) -> None:
    """Persist a minimal audit row for a BLOCKED/UNAVAILABLE screening.

    Only failures are recorded — allowed photos keep their moderation
    metadata on the (ephemeral, user-deletable) attachment row. The event
    keeps the operator-acting identifiers (user id, image hash, provider
    categories) required for child-safety escalation and legally mandated
    reporting, and nothing else: never the image bytes.
    """
    decision = blocked.decision
    try:
        PhotoScreeningEvent.objects.create(
            user_id=(owner.pk if owner is not None else None),
            conversation_id=(conversation.conversation_id if conversation is not None else None),
            sha256=blocked.sha256,
            provider=decision.provider,
            decision=decision.action,
            categories=decision.categories,
        )
    except Exception:
        # The audit row must never be the reason a refused upload 500s.
        logger.exception("Could not record photo screening event.")

    # A BLOCKED child-safety photo also reaches the cross-session safety
    # ledger so a repeated uploader accumulates toward the lock threshold
    # (audit F4). Unavailable screenings never do: a fail-closed refusal is
    # an outage, not a violation. The ledger call swallows its own errors by
    # design (see record_photo_block) — same rule as the audit row above.
    if decision.action == "block" and owner is not None:
        try:
            SafetyLedgerService.record_photo_block(
                user=owner,
                conversation_id=(conversation.conversation_id if conversation is not None else None),
                sha256=blocked.sha256,
                provider=decision.provider,
                categories=list(decision.categories or []),
            )
        except Exception:
            logger.exception("Could not record photo block in the safety ledger.")


# --------------------------------------------------------------------------
# Serving: decrypt + short-lived signed URLs
# --------------------------------------------------------------------------


def attachment_signing_secret() -> bytes:
    secret = getattr(settings, "PHOTO_URL_SIGNING_SECRET", None) or settings.SECRET_KEY
    return str(secret).encode("utf-8")


def sign_attachment_url(attachment, conversation_id, ttl_seconds: int = None) -> str:
    """HMAC-signed, expiry-bounded URL params for one attachment.

    The signature covers conversation id, attachment id and expiry, and is
    generated fresh on every serializer call — signed URLs are never stored.
    """
    ttl = int(ttl_seconds or getattr(settings, "PHOTO_URL_TTL_SECONDS", 900))
    expires = int(time.time()) + ttl
    message = f"{conversation_id}:{attachment.attachment_id}:{expires}".encode("utf-8")
    signature = hmac.new(attachment_signing_secret(), message, hashlib.sha256).hexdigest()
    return f"?expires={expires}&signature={signature}"


def verify_attachment_signature(attachment, conversation_id, expires: str, signature: str) -> bool:
    try:
        expires_int = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_int < int(time.time()):
        return False
    message = f"{conversation_id}:{attachment.attachment_id}:{expires_int}".encode("utf-8")
    expected = hmac.new(attachment_signing_secret(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature or ""))


def read_attachment_bytes(attachment) -> bytes:
    """Fetch, decrypt and return the photo bytes for a served attachment."""
    ciphertext = default_storage.open(attachment.storage_key, "rb").read()
    return attachment_crypto.decrypt_photo(
        ciphertext,
        wrapped_key=attachment.wrapped_key,
        nonce=attachment.nonce,
        key_version=attachment.key_version,
        attachment_id=str(attachment.attachment_id),
        content_type=attachment.content_type,
        sha256=attachment.sha256,
    )
