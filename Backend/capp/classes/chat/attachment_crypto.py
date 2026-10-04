# capp/classes/chat/attachment_crypto.py
"""
Envelope encryption for user-sent chat photos.

Photos must be unreadable in the object store itself: the Railway S3 bucket is
shared with character avatars and gallery images, and a bucket leak must not
expose user photos. Every image is therefore encrypted in the application
BEFORE it is written, and only ciphertext ever reaches storage.

Scheme (AES-256-GCM envelope encryption):

  1. A fresh 32-byte data key is generated per image. The image bytes are
     encrypted with AESGCM(data key, nonce) directly.
  2. The data key never persists in the clear: it is wrapped with the
     versioned master key (AESGCM again) and only the wrapped form is stored
     in the ``MessageAttachment`` row next to the ciphertext reference.
  3. Master keys come from settings (``IMAGE_ENCRYPTION_KEYS``), versioned so
     rotation is possible: encrypt with the current version, decrypt with
     whichever version the row records. The master key material itself lives
     only in Railway environment variables and the settings module — never in
     the bucket, never in the database.

Additional authenticated data (AAD) binds the ciphertext to the attachment's
identity, content type and plaintext hash, so a ciphertext/metadata swap
between two attachments fails to decrypt instead of silently returning the
wrong image.
"""

import base64
import logging
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

logger = logging.getLogger(__name__)

_NONCE_BYTES = 12  # 96-bit nonces are the AES-GCM standard
_AAD_PREFIX = "uwenzai-photo-attachment"


class AttachmentCryptoError(Exception):
    """Raised when attachment crypto is misconfigured or decryption fails."""

    code = "photo_unavailable"


@dataclass(frozen=True)
class EncryptedAttachment:
    """Envelope-encrypted photo bytes plus the metadata needed to decrypt."""

    ciphertext: bytes
    wrapped_key: str  # base64 data key encrypted under the master key
    nonce: str  # base64 image-nonce || wrap-nonce (12 + 12 bytes)
    key_version: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _master_keys() -> dict:
    """Versioned master keys: ``{"v1": <32 raw bytes>, ...}``."""
    raw = getattr(settings, "IMAGE_ENCRYPTION_KEYS", None)
    if not isinstance(raw, dict) or not raw:
        raise AttachmentCryptoError("IMAGE_ENCRYPTION_KEYS is not configured.")
    keys = {}
    for version, encoded in raw.items():
        if not encoded:
            continue
        try:
            material = _unb64(str(encoded).strip())
        except Exception as exc:
            raise AttachmentCryptoError(f"Bad key material for version {version}.") from exc
        if len(material) != 32:
            raise AttachmentCryptoError(f"Key version {version} is not 32 bytes.")
        keys[str(version)] = material
    if not keys:
        raise AttachmentCryptoError("IMAGE_ENCRYPTION_KEYS has no usable versions.")
    return keys


def _current_version() -> str:
    version = getattr(settings, "IMAGE_ENCRYPTION_CURRENT_VERSION", None)
    keys = _master_keys()
    if version and str(version) in keys:
        return str(version)
    # Fall back to the lexically highest version so a missing explicit
    # "current" pin still encrypts under a real key.
    return sorted(keys)[-1]


def _aad(attachment_id: str, content_type: str, sha256: str) -> bytes:
    return f"{_AAD_PREFIX}|{attachment_id}|{content_type}|{sha256}".encode("utf-8")


def encrypt_photo(
    plaintext: bytes,
    *,
    attachment_id: str,
    content_type: str,
    sha256: str,
) -> EncryptedAttachment:
    """Encrypt ``plaintext`` under a fresh per-image data key."""
    keys = _master_keys()
    version = _current_version()
    master = keys[version]

    data_key = os.urandom(32)
    image_nonce = os.urandom(_NONCE_BYTES)
    wrap_nonce = os.urandom(_NONCE_BYTES)

    try:
        ciphertext = AESGCM(data_key).encrypt(image_nonce, plaintext, _aad(attachment_id, content_type, sha256))
        wrapped = AESGCM(master).encrypt(wrap_nonce, data_key, None)
    except Exception as exc:
        raise AttachmentCryptoError("Photo encryption failed.") from exc

    return EncryptedAttachment(
        ciphertext=ciphertext,
        wrapped_key=_b64(wrapped),
        nonce=_b64(image_nonce + wrap_nonce),
        key_version=version,
    )


def decrypt_photo(
    ciphertext: bytes,
    *,
    wrapped_key: str,
    nonce: str,
    key_version: str,
    attachment_id: str,
    content_type: str,
    sha256: str,
) -> bytes:
    """Decrypt an envelope back to the original image bytes.

    Any tampering — with the ciphertext, the wrapped key, the nonce, or the
    row's identity/content-type/hash metadata — raises
    :class:`AttachmentCryptoError` (AES-GCM auth failure), never returns
    wrong-but-plausible bytes.
    """
    keys = _master_keys()
    master = keys.get(str(key_version))
    if master is None:
        raise AttachmentCryptoError(f"Key version {key_version} is not configured.")

    try:
        nonce_bytes = _unb64(nonce)
        image_nonce, wrap_nonce = nonce_bytes[:_NONCE_BYTES], nonce_bytes[_NONCE_BYTES:]
        if len(image_nonce) != _NONCE_BYTES or len(wrap_nonce) != _NONCE_BYTES:
            raise ValueError("nonce has the wrong length")
        data_key = AESGCM(master).decrypt(wrap_nonce, _unb64(wrapped_key), None)
        return AESGCM(data_key).decrypt(image_nonce, ciphertext, _aad(attachment_id, content_type, sha256))
    except AttachmentCryptoError:
        raise
    except Exception as exc:
        logger.error("Attachment decryption failed (attachment=%s).", attachment_id)
        raise AttachmentCryptoError("Attachment decryption failed.") from exc


def generate_master_key() -> str:
    """Generate a fresh base64 32-byte master key (for operator tooling)."""
    return _b64(os.urandom(32))
