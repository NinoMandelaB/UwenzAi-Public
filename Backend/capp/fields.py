# capp/fields.py

from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken


def get_fernet():
    key = settings.MESSAGE_ENCRYPTION_KEY.encode()
    return Fernet(key)


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
        if value is None:
            return None
        if isinstance(value, str):
            value = value.encode("utf-8")
        return get_fernet().encrypt(value).decode("utf-8")

    def from_db_value(self, value, expression, connection):
        """
        Decrypt when reading from the database.
        If decryption fails (e.g., legacy plaintext data), return the value unchanged.
        """
        if value is None:
            return None
        try:
            return get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, Exception):
            # Not a valid encrypted token – return as plaintext
            return value

    def to_python(self, value):
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value
