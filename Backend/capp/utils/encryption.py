import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


class EncryptionService:
    @classmethod
    def _get_fernet(cls) -> Fernet:
        key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)

        if not key:
            raise ValueError("FIELD_ENCRYPTION_KEY is not set in settings")

        if isinstance(key, str):
            key = key.strip().encode()

        try:
            return Fernet(key)
        except Exception as e:
            logger.exception("Invalid FIELD_ENCRYPTION_KEY configuration")
            raise ValueError("FIELD_ENCRYPTION_KEY is invalid") from e

    @classmethod
    def encrypt_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        fernet = cls._get_fernet()
        encrypted_value = fernet.encrypt(value.encode("utf-8"))
        return encrypted_value.decode("utf-8")

    @classmethod
    def decrypt_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        fernet = cls._get_fernet()

        try:
            decrypted_value = fernet.decrypt(value.encode("utf-8"))
            return decrypted_value.decode("utf-8")
        except InvalidToken as e:
            logger.exception("Failed to decrypt value: invalid token")
            raise ValueError("Encrypted value is invalid or corrupted") from e
        except Exception as e:
            logger.exception("Unexpected error while decrypting value")
            raise ValueError("Failed to decrypt value") from e

    @classmethod
    def hash_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = str(value).strip()
        if not value:
            return None

        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def normalize_email(cls, email: Optional[str]) -> Optional[str]:
        if email is None:
            return None

        email = str(email).strip().lower()
        return email if email else None

    @classmethod
    def normalize_phone(cls, phone_number: Optional[str]) -> Optional[str]:
        if phone_number is None:
            return None

        phone_number = str(phone_number).strip()

        if not phone_number:
            return None

        # Keep leading + if present, remove other spaces
        if phone_number.startswith("+"):
            normalized = "+" + "".join(ch for ch in phone_number[1:] if ch.isdigit())
        else:
            normalized = "".join(ch for ch in phone_number if ch.isdigit())

        return normalized if normalized else None

    @classmethod
    def prepare_email_fields(cls, email: Optional[str]) -> dict:
        normalized_email = cls.normalize_email(email)

        if not normalized_email:
            return {
                "email_encrypted": None,
                "email_hash": None,
            }

        return {
            "email_encrypted": cls.encrypt_value(normalized_email),
            "email_hash": cls.hash_value(normalized_email),
        }

    @classmethod
    def prepare_phone_fields(cls, phone_number: Optional[str]) -> dict:
        normalized_phone = cls.normalize_phone(phone_number)

        if not normalized_phone:
            return {
                "phone_number_encrypted": None,
                "phone_number_hash": None,
            }

        return {
            "phone_number_encrypted": cls.encrypt_value(normalized_phone),
            "phone_number_hash": cls.hash_value(normalized_phone),
        }

    @classmethod
    def generate_fernet_key(cls) -> str:
        return Fernet.generate_key().decode("utf-8")
