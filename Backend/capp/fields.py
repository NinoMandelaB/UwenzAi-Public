# capp/fields.py

import base64
import binascii
import logging

from django.db import models
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class EncryptedFieldError(ValueError):
    pass


class UnavailableEncryptedValue(str):
    """Safe display sentinel. Saving it is forbidden, never an empty overwrite."""

    def __new__(cls):
        return super().__new__(cls, "[Encrypted content unavailable]")


def get_fernet():
    key = getattr(settings, "MESSAGE_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured("MESSAGE_ENCRYPTION_KEY must be configured.")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        raise ImproperlyConfigured("MESSAGE_ENCRYPTION_KEY is not a valid Fernet key.") from None


def _looks_encrypted(value):
    if value.startswith("gAAAA"):
        return True
    try:
        # Legacy Fernet rows have no application prefix. Recognize their version
        # byte even when the usual timestamp prefix was altered.
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
        return len(decoded) >= 57 and decoded[0] == 0x80
    except (ValueError, UnicodeError, binascii.Error):
        return False


class EncryptedTextField(models.TextField):
    """
    A TextField that automatically encrypts and decrypts data at rest.
    Handles both plaintext (existing data) and encrypted (new data) seamlessly.
    """

    def decrypt_text(encrypted_text: str) -> str:
        """
        Decrypt a string that was encrypted using FIELD_ENCRYPTION_KEY.
        Returns an empty string if input is empty or decryption fails.
        """
        if not encrypted_text:
            return ""
        try:
            f = Fernet(settings.FIELD_ENCRYPTION_KEY)
            return f.decrypt(encrypted_text.encode()).decode()
        except (InvalidToken, Exception):
            return ""

    def get_prep_value(self, value):
        """Encrypt before saving to the database."""
        if isinstance(value, UnavailableEncryptedValue):
            raise EncryptedFieldError("Cannot overwrite an unreadable encrypted value.")
        if value is None:
            return None
        if isinstance(value, str):
            value = value.encode("utf-8")
        return get_fernet().encrypt(value).decode("utf-8")

    def from_db_value(self, value, expression, connection):
        """
        Decrypt when reading from the database.
        Retain known legacy plaintext. Ciphertext failures become a safe display
        sentinel and cannot be written back through this field.
        """
        if value is None:
            return None
        if not _looks_encrypted(value):
            return value
        try:
            return get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeError, ImproperlyConfigured):
            # Do not expose ciphertext/keys or take down every legacy row.
            logger.error("Encrypted field unavailable: check encryption key and data integrity.")
            return UnavailableEncryptedValue()

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value
