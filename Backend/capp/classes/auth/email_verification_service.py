import hashlib
import logging
import random
from datetime import timedelta
from typing import Dict

from django.db import transaction
from django.utils import timezone
from typing import Optional

from capp.models import CustomUser, EmailVerification

logger = logging.getLogger(__name__)


class EmailVerificationService:
    """
    Handles creation and verification of email verification codes.
    """

    CODE_LENGTH = 6
    EXPIRY_MINUTES = 10
    MAX_ATTEMPTS = 5

    @classmethod
    def _generate_code(cls) -> str:
        return "".join(str(random.randint(0, 9)) for _ in range(cls.CODE_LENGTH))

    @classmethod
    def _hash_code(cls, code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def create_verification_for_user(
        cls,
        user: CustomUser,
        purpose: str = "email_verification",  # "email_verification" or "email_change"
        new_email_hash: Optional[str] = None,
        new_email_encrypted: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Creates a new verification code for a user.
        For normal email verification: just pass user.
        For email change: pass purpose="email_change" plus new_email_hash and new_email_encrypted.
        Invalidates previous pending codes of the same purpose.
        Returns the raw code (for sending via email).
        """
        if not user:
            raise ValueError("User is required")

        code = cls._generate_code()
        code_hash = cls._hash_code(code)
        expires_at = timezone.now() + timedelta(minutes=cls.EXPIRY_MINUTES)

        with transaction.atomic():
            # expire previous pending verifications of the same purpose
            # For email change, we only invalidate other email‑change requests
            if purpose == "email_change":
                EmailVerification.objects.filter(
                    user=user,
                    status=EmailVerification.VerificationStatus.PENDING,
                    new_email_hash__isnull=False,  # indicates email change
                ).update(status=EmailVerification.VerificationStatus.EXPIRED)
            else:
                # normal email verification: only expire those without new_email_hash
                EmailVerification.objects.filter(
                    user=user,
                    status=EmailVerification.VerificationStatus.PENDING,
                    new_email_hash__isnull=True,
                ).update(status=EmailVerification.VerificationStatus.EXPIRED)

            # Create the verification record
            verification = EmailVerification(
                user=user,
                code_hash=code_hash,
                expires_at=expires_at,
                status=EmailVerification.VerificationStatus.PENDING,
                attempts=0,
            )
            if purpose == "email_change":
                verification.new_email_hash = new_email_hash
                verification.new_email_encrypted = new_email_encrypted
            verification.save()

        logger.info(
            "Created %s verification for user_id=%s",
            purpose,
            user.user_id,
        )

        return {
            "code": code,
            "expires_at": expires_at,
        }

    @classmethod
    def verify_code(cls, user: CustomUser, code: str) -> Dict[str, str]:
        """
        Verifies a submitted code.
        """
        if not user:
            return {"status": "fail", "data": "user_required"}

        if not code:
            return {"status": "fail", "data": "verification_code_required"}

        verification = (
            EmailVerification.objects.filter(
                user=user,
                status=EmailVerification.VerificationStatus.PENDING,
            )
            .order_by("-created_at")
            .first()
        )

        if not verification:
            return {"status": "fail", "data": "verification_not_found"}

        # check expiry
        if verification.expires_at < timezone.now():
            verification.status = EmailVerification.VerificationStatus.EXPIRED
            verification.save(update_fields=["status", "updated_at"])
            return {"status": "fail", "data": "verification_expired"}

        # check attempts
        if verification.attempts >= cls.MAX_ATTEMPTS:
            verification.status = EmailVerification.VerificationStatus.EXPIRED
            verification.save(update_fields=["status", "updated_at"])
            return {"status": "fail", "data": "verification_attempts_exceeded"}

        # verify code
        code_hash = cls._hash_code(code)

        if code_hash != verification.code_hash:
            verification.attempts += 1
            verification.save(update_fields=["attempts", "updated_at"])

            return {"status": "fail", "data": "invalid_verification_code"}

        # success
        with transaction.atomic():
            verification.status = EmailVerification.VerificationStatus.USED
            verification.used_at = timezone.now()
            verification.save(update_fields=["status", "used_at", "updated_at"])

            user.mark_email_verified()

        logger.info("Email verified successfully for user_id=%s", user.user_id)

        return {"status": "success", "data": "email_verified"}
