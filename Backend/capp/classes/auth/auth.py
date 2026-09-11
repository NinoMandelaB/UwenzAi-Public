import logging
from typing import Any, Dict

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from capp.models import (
    ComplianceConfirmationLog,
    CustomUser,
    EmailVerification,
    MemoryFact,
    PasswordRecovery,
    ReminderDeliveryAttempt,
    UserSubscription,
    SparkPool,
    Conversation,
    Message,
    SparkTransaction,
)

from capp.classes.auth.email_verification_service import EmailVerificationService
from capp.classes.auth.codes import code_matches
from capp.authentication import account_can_authenticate
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.auth.lockout import LoginLockoutService
from capp.classes.auth.recaptcha import RecaptchaService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.classes.guest.guest_transfer_grant_service import GuestTransferGrantService

from capp.classes.email.password_recovery_email_service import (
    PasswordRecoveryEmailService,
)
from capp.classes.email.verification_email_service import VerificationEmailService

from capp.utils.encryption import EncryptionService
from capp.constants import SIGNUP_SPARKS

logger = logging.getLogger(__name__)


class Auth:

    @staticmethod
    def _password_error(password, user=None):
        """New credentials only. Existing sign-in passwords remain unchanged."""
        if not isinstance(password, str):
            return "invalid_password"
        if len(password) < 8:
            return "password_too_short"
        if len(password) > 128:
            return "password_too_long"
        try:
            validate_password(password, user=user)
        except ValidationError:
            return "password_validation_failed"
        return None

    @classmethod
    @transaction.atomic
    def request_email_change(cls, user: CustomUser, payload: Dict[str, Any], request=None) -> Dict[str, Any]:
        try:
            from capp.models import EmailChangeRequest

            user = CustomUser.objects.select_for_update().get(pk=user.pk)

            new_email = (payload.get("new_email") or "").strip()
            if not new_email:
                return {"status": "fail", "data": "new_email_required"}

            # Prepare encrypted fields
            email_fields = EncryptionService.prepare_email_fields(new_email)
            if not email_fields["email_hash"]:
                return {"status": "fail", "data": "invalid_email"}

            # Check if new email already used by another active user (excluding current)
            existing_user = (
                CustomUser.objects.filter(email_hash=email_fields["email_hash"]).exclude(user_id=user.user_id).first()
            )
            if existing_user:
                return {"status": "fail", "data": "email_already_exists"}

            # Check account state
            if (
                user.is_deleted
                or user.account_status in [CustomUser.AccountStatus.BANNED, CustomUser.AccountStatus.SUSPENDED]
                or not user.is_active
            ):
                return {"status": "fail", "data": "account_inactive"}

            # Invalidate any previous pending requests for this user
            EmailChangeRequest.objects.filter(user=user, is_used=False).update(is_used=True)

            # Generate code and create request
            code = EmailChangeRequest.generate_code()
            expires_at = timezone.now() + timedelta(minutes=15)  # 15 minutes expiry

            EmailChangeRequest.objects.create(
                user=user,
                new_email_hash=email_fields["email_hash"],
                new_email_encrypted=email_fields["email_encrypted"],
                code=code,
                expires_at=expires_at,
            )

            # Send code to the new email
            full_name = user.full_name or user.display_name or user.username
            from capp.classes.email.verification_email_service import (
                VerificationEmailService,
            )

            sent = VerificationEmailService.send_verification_code(
                to_email=new_email,
                full_name=full_name,
                code=code,
            )
            if not sent:
                return {"status": "fail", "data": "email_send_failed"}

            logger.info(
                "Email change verification sent for user_id=%s",
                user.user_id,
            )
            return {
                "status": "success",
                "data": {"message": "verification_code_sent_to_new_email"},
            }

        except Exception:
            logger.exception("Unexpected error in request_email_change")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    @transaction.atomic
    def confirm_email_change(cls, user: CustomUser, payload: Dict[str, Any], request=None) -> Dict[str, Any]:
        try:
            user = CustomUser.objects.select_for_update().get(pk=user.pk)
            if not account_can_authenticate(user):
                return {"status": "fail", "data": "account_inactive"}
            code = (payload.get("code") or "").strip()
            if not code:
                return {"status": "fail", "data": "verification_code_required"}

            from capp.models import EmailChangeRequest

            # Find the most recent pending request for this user
            change_request = (
                EmailChangeRequest.objects.select_for_update()
                .filter(
                    user=user,
                    is_used=False,
                )
                .order_by("-created_at")
                .first()
            )

            if not change_request:
                return {"status": "fail", "data": "invalid_or_expired_code"}

            if change_request.is_expired():
                return {"status": "fail", "data": "code_expired"}

            if change_request.attempts >= 5:
                return {"status": "fail", "data": "too_many_attempts"}

            if not code_matches(code, change_request.code, user.pk, "email_change"):
                change_request.attempts += 1
                change_request.save(update_fields=["attempts", "updated_at"])
                return {"status": "fail", "data": "invalid_code"}

            # Code valid – update user's email
            with transaction.atomic():
                user.email_encrypted = change_request.new_email_encrypted
                user.email_hash = change_request.new_email_hash
                user.is_verified = True  # new email is considered verified
                user.save(
                    update_fields=[
                        "email_encrypted",
                        "email_hash",
                        "is_verified",
                        "updated_at",
                    ]
                )

                change_request.mark_used()

            logger.info("Email changed successfully for user_id=%s", user.user_id)
            return {
                "status": "success",
                "data": {
                    "message": "email_updated_successfully",
                    "user": cls._serialize_user(user),
                },
            }

        except Exception:
            logger.exception("Unexpected error in confirm_email_change")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def export_user_data(cls, user: CustomUser) -> Dict[str, Any]:
        """
        Export user's personal data including their own messages.
        Excludes: all IDs, media paths, prompts, system instructions, AI messages,
        spark_pool/payment/usage logs, character data, and any platform internals.
        """
        try:
            # ---------- Personal information (decrypted, no IDs) ----------
            email = EncryptionService.decrypt_value(user.email_encrypted)
            phone = EncryptionService.decrypt_value(user.phone_number_encrypted)

            personal_info = {
                "username": user.username,
                "full_name": user.full_name,
                "display_name": user.display_name,
                "email": email,
                "phone_number": phone,
                "preferred_language": user.preferred_language,
                "auth_provider": user.auth_provider,
                "account_status": user.account_status,
                "is_verified": user.is_verified,
                "age_verified": user.age_verified,
                "age_verified_at": (user.age_verified_at.isoformat() if user.age_verified_at else None),
                "created_at": user.created_at.isoformat(),
                "updated_at": user.updated_at.isoformat(),
            }

            # ---------- Compliance / consent logs (no IDs) ----------
            compliance_consents = []
            for log in ComplianceConfirmationLog.objects.filter(user=user):
                compliance_consents.append(
                    {
                        "confirmation_type": log.confirmation_type,
                        "accepted": log.accepted,
                        "version": log.version,
                        "accepted_at": log.accepted_at.isoformat(),
                        "ip_address": log.ip_address,
                        "user_agent": log.user_agent,
                    }
                )

            # ---------- Memory facts (exclude foreign keys) ----------
            memory_facts = []
            for fact in MemoryFact.objects.filter(user=user, is_active=True):
                memory_facts.append(
                    {
                        "fact_text": fact.fact_text,
                        "fact_type": fact.fact_type,
                        "confidence_score": float(fact.confidence_score),
                        "importance_score": fact.importance_score,
                        "extracted_at": fact.extracted_at.isoformat(),
                        "is_active": fact.is_active,
                    }
                )

            # ---------- User messages (only what the user typed) ----------
            user_messages = []
            # Get all conversations belonging to this user
            for conv in Conversation.objects.filter(user=user):
                # Get user messages from this conversation, ordered by time
                for msg in Message.objects.filter(conversation=conv, sender_type=Message.SenderType.USER).order_by(
                    "created_at"
                ):
                    user_messages.append(
                        {
                            "timestamp": msg.created_at.isoformat(),
                            "content": msg.content,  # plain text (EncryptedTextField is decrypted by the field)
                            "language_code": msg.language_code,
                        }
                    )

            # ---------- Activity logs (from available event models) ----------
            activity_logs = []

            # 1) Email verification attempts
            for ev in EmailVerification.objects.filter(user=user):
                activity_logs.append(
                    {
                        "type": "email_verification",
                        "timestamp": ev.created_at.isoformat(),
                        "details": {
                            "status": ev.status,
                            "attempts": ev.attempts,
                            "expired": (ev.expires_at < timezone.now() if ev.expires_at else None),
                        },
                    }
                )

            # 2) Password recovery requests
            for pr in PasswordRecovery.objects.filter(user=user):
                activity_logs.append(
                    {
                        "type": "password_reset_request",
                        "timestamp": pr.created_at.isoformat(),
                        "details": {
                            "used": pr.is_used,
                            "attempts": pr.attempts,
                            "expired": (
                                pr.is_expired()
                                if hasattr(pr, "is_expired")
                                else (pr.expires_at < timezone.now() if pr.expires_at else None)
                            ),
                        },
                    }
                )

            # 3) Subscription changes
            for sub in UserSubscription.objects.filter(user=user):
                plan_name = sub.subscription_plan.name if sub.subscription_plan else None
                activity_logs.append(
                    {
                        "type": "subscription_change",
                        "timestamp": sub.started_at.isoformat(),
                        "details": {
                            "plan": plan_name,
                            "status": sub.status,
                            "started_at": sub.started_at.isoformat(),
                            "ends_at": sub.ends_at.isoformat() if sub.ends_at else None,
                            "cancelled_at": (sub.cancelled_at.isoformat() if sub.cancelled_at else None),
                            "auto_renew": sub.auto_renew,
                        },
                    }
                )
                if sub.cancelled_at:
                    activity_logs.append(
                        {
                            "type": "subscription_cancelled",
                            "timestamp": sub.cancelled_at.isoformat(),
                            "details": {"plan": plan_name, "status": sub.status},
                        }
                    )

            # 4) Last login
            if user.last_login:
                activity_logs.append(
                    {
                        "type": "login",
                        "timestamp": user.last_login.isoformat(),
                        "details": {"note": "last successful login (IP/user agent not stored)"},
                    }
                )

            activity_logs.sort(key=lambda x: x["timestamp"])

            # ---------- BYOK config (if any) ----------
            byok_data = None
            try:
                from capp.models import UserBYOKConfig
                from capp.classes.byok.byok_service import BYOKService

                byok = UserBYOKConfig.objects.get(user=user)
                raw_key = byok.get_decrypted_api_key()
                byok_data = {
                    "api_url": byok.api_url,
                    "model_name": byok.model_name,
                    "api_key_hint": BYOKService._key_hint(raw_key),
                    "system_prompt": byok.system_prompt,
                    "store_on_platform": byok.store_on_platform,
                    "is_active": byok.is_active,
                    "created_at": byok.created_at.isoformat(),
                    "note": (
                        "Full API key is not included in exports for security reasons. "
                        "The hint shows the last 4 characters only."
                    ),
                }
            except Exception:
                pass  # No BYOK config or decryption error

            # ---------- Build final export ----------
            export_data = {
                "exported_at": timezone.now().isoformat(),
                "personal_info": personal_info,
                "compliance_consents": compliance_consents,
                "memory_facts": memory_facts,
                "user_messages": user_messages,
                "activity_logs": activity_logs,
                "byok_config": byok_data,
            }

            return {
                "status": "success",
                "data": export_data,
            }

        except Exception:
            logger.exception("Unexpected error in export_user_data")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def export_user_data_dep(cls, user: CustomUser) -> Dict[str, Any]:
        """Return a serialisable dictionary containing all personal data for the user."""
        from capp.models import (
            Character,
            Conversation,
            Message,
            MemoryFact,
            RelationshipState,
            SparkPool,
            SparkTransaction,
            UserSubscription,
            TokenUsageLog,
            UsageCharge,
            PaymentTransaction,
            Feedback,
            ComplianceConfirmationLog,
            ModerationFlag,
        )
        from capp.classes.characters.character_service import CharacterService

        # Profile – already serialised cleanly
        profile = cls._serialize_user(user)

        # Characters created by this user
        characters = []
        for char in Character.objects.filter(created_by=user):
            characters.append(CharacterService._serialize_character(char, request=None))

        # Conversations owned by this user
        conversations = list(
            Conversation.objects.filter(user=user).values(
                "conversation_id",
                "character_id",
                "title",
                "status",
                "started_at",
                "last_message_at",
                "last_summarized_at",
                "preferred_language",
            )
        )
        # Messages belonging to those conversations
        conv_ids = [c["conversation_id"] for c in conversations]
        messages = list(
            Message.objects.filter(conversation_id__in=conv_ids).values(
                "message_id",
                "conversation_id",
                "sender_type",
                "content",
                "language_code",
                "status",
                "created_at",
            )
        )

        # Memory facts collected about this user
        memory_facts = list(
            MemoryFact.objects.filter(user=user).values(
                "memory_fact_id",
                "fact_text",
                "fact_type",
                "confidence_score",
                "importance_score",
                "extracted_at",
                "is_active",
            )
        )

        # Relationship states
        relationships = list(
            RelationshipState.objects.filter(user=user).values(
                "relationship_state_id",
                "character_id",
                "familiarity_score",
                "affinity_score",
                "trust_score",
                "interaction_count",
                "tone_state",
                "last_interaction_at",
            )
        )

        # SparkPool & transactions
        spark_pool = None
        spark_pool_obj = SparkPool.objects.filter(user=user, is_active=True).first()
        if spark_pool_obj:
            spark_pool = {
                "spark_pool_id": str(spark_pool_obj.spark_pool_id),
                "sparks": spark_pool_obj.sparks,
                "token_balance": spark_pool_obj.token_balance,
            }
            transactions = list(SparkTransaction.objects.filter(spark_pool=spark_pool_obj).values())
        else:
            transactions = []

        # Subscriptions
        subscriptions = list(UserSubscription.objects.filter(user=user).values())

        # Feedback submitted
        feedback_items = list(Feedback.objects.filter(user=user).values())

        # Compliance logs
        compliance_logs = list(ComplianceConfirmationLog.objects.filter(user=user).values())

        # Token / usage logs
        usage_logs = list(TokenUsageLog.objects.filter(user=user).values())
        usage_charges = list(UsageCharge.objects.filter(user=user).values())

        # Moderation flags (where user was flagged)
        flags = list(ModerationFlag.objects.filter(user=user).values())

        # Payment transactions
        payments = list(PaymentTransaction.objects.filter(user=user).values())

        return {
            "status": "success",
            "data": {
                "exported_at": timezone.now().isoformat(),
                "profile": profile,
                "characters": characters,
                "conversations": conversations,
                "messages": messages,
                "memory_facts": memory_facts,
                "relationships": relationships,
                "spark_pool": spark_pool,
                "spark_transactions": transactions,
                "subscriptions": subscriptions,
                "feedback": feedback_items,
                "compliance_logs": compliance_logs,
                "usage_logs": usage_logs,
                "usage_charges": usage_charges,
                "moderation_flags": flags,
                "payment_transactions": payments,
            },
        }

    @classmethod
    def update_profile(cls, user: CustomUser, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if not user:
                return {"status": "fail", "data": "user_required"}

            update_fields = []

            # --- Username change (immediate, with uniqueness check) ---
            new_username = payload.get("username")
            if new_username is not None:
                new_username = str(new_username).strip().lower()
                if new_username != user.username:
                    # Check uniqueness
                    if CustomUser.objects.filter(username=new_username).exists():
                        return {"status": "fail", "data": "username_already_exists"}
                    user.username = new_username
                    update_fields.append("username")

            # --- Display name ---
            new_display = payload.get("display_name", user.display_name)
            if new_display is not None:
                new_display = str(new_display).strip() or None
                if new_display != user.display_name:
                    user.display_name = new_display
                    update_fields.append("display_name")

            # --- Preferred language ---
            new_lang = payload.get("preferred_language", user.preferred_language)
            if new_lang:
                new_lang = str(new_lang).strip().lower()
                if new_lang in dict(CustomUser.LanguageChoices.choices).keys():
                    if new_lang != user.preferred_language:
                        user.preferred_language = new_lang
                        update_fields.append("preferred_language")

            new_timezone = payload.get("timezone")
            if new_timezone is not None:
                new_timezone = str(new_timezone).strip()
                if new_timezone and user.timezone != new_timezone:
                    # Optional: validate against zoneinfo.available_timezones()
                    user.timezone = new_timezone
                    update_fields.append("timezone")

            # --- Phone number (encrypted) ---
            if "phone_number" in payload:
                raw_phone = payload.get("phone_number")
                if raw_phone:
                    phone_fields = EncryptionService.prepare_phone_fields(raw_phone)
                    new_encrypted = phone_fields["phone_number_encrypted"]
                    new_hash = phone_fields["phone_number_hash"]
                else:
                    new_encrypted = None
                    new_hash = None

                if user.phone_number_encrypted != new_encrypted or user.phone_number_hash != new_hash:
                    user.phone_number_encrypted = new_encrypted
                    user.phone_number_hash = new_hash
                    update_fields.extend(["phone_number_encrypted", "phone_number_hash"])

            if update_fields:
                update_fields.append("updated_at")
                user.save(update_fields=update_fields)

            return {
                "status": "success",
                "data": {
                    "message": "profile_updated",
                    "user": cls._serialize_user(user),
                },
            }

        except Exception:
            logger.exception("Unexpected error during profile update")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def hard_delete_user(cls, user: CustomUser) -> Dict[str, Any]:
        try:
            if not user:
                return {"status": "fail", "data": "user_required"}

            existing_user = CustomUser.objects.filter(user_id=user.user_id).first()

            if not existing_user:
                return {"status": "fail", "data": "user_not_found"}

            with transaction.atomic():
                from capp.classes.auth.account_erasure import erase_private_content

                # Serialize account erasure with publication of user-owned
                # content. Writers lock the actor before the character.
                existing_user = CustomUser.objects.select_for_update().filter(pk=existing_user.pk).first()
                if existing_user is None:
                    return {"status": "fail", "data": "user_not_found"}
                erase_private_content(existing_user)

                # Reminder delivery attempts hold a PROTECT reference from their
                # billing reservation, which would otherwise block the cascade
                # delete of the user's own reservations below (ProtectedError on
                # every account deletion for users with reminder history). The
                # attempts are per-user operational lease records — the billing
                # ledger itself is untouched — so drop them first. The second
                # arm is defensive: any attempt referencing one of the user's
                # reservations blocks that reservation's deletion regardless of
                # who owns the attempt row.
                ReminderDeliveryAttempt.objects.filter(
                    Q(user=existing_user) | Q(reservation__user=existing_user)
                ).delete()

                # Delete the user itself. All user-owned relations with CASCADE/OneToOne
                # will be permanently removed automatically.
                existing_user.delete()

            logger.warning("User permanently deleted user_id=%s", user.user_id)

            return {
                "status": "success",
                "data": {
                    "message": "user_permanently_deleted",
                },
            }

        except Exception:
            logger.exception("Unexpected error during permanent user deletion")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def change_password(cls, user: CustomUser, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if not user:
                return {"status": "fail", "data": "user_required"}

            current_password = payload.get("current_password")
            new_password = payload.get("new_password")
            confirm_password = payload.get("confirm_password")

            if not current_password:
                return {"status": "fail", "data": "current_password_required"}

            if not new_password:
                return {"status": "fail", "data": "new_password_required"}

            if not confirm_password:
                return {"status": "fail", "data": "confirm_password_required"}

            if new_password != confirm_password:
                return {"status": "fail", "data": "password_mismatch"}

            error = cls._password_error(new_password, user)
            if error:
                return {"status": "fail", "data": error}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            if user.account_status == CustomUser.AccountStatus.BANNED:
                return {"status": "fail", "data": "account_banned"}

            if user.account_status == CustomUser.AccountStatus.SUSPENDED:
                return {"status": "fail", "data": "account_suspended"}

            if not user.is_active:
                return {"status": "fail", "data": "account_inactive"}

            if not user.check_password(current_password):
                return {"status": "fail", "data": "current_password_incorrect"}

            if current_password == new_password:
                return {"status": "fail", "data": "new_password_must_differ"}

            user.set_password(new_password)
            user.save(update_fields=["password", "updated_at"])

            logger.info("Password changed successfully for user_id=%s", user.user_id)

            return {
                "status": "success",
                "data": {
                    "message": "password_changed_successfully",
                },
            }

        except Exception:
            logger.exception("Unexpected error during password change")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    @transaction.atomic
    def confirm_password_reset(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            email = (payload.get("email") or "").strip()
            code = (payload.get("code") or "").strip()
            password = payload.get("password")
            confirm_password = payload.get("confirm_password")

            if not email:
                return {"status": "fail", "data": "email_required"}

            if not code:
                return {"status": "fail", "data": "recovery_code_required"}

            if not password:
                return {"status": "fail", "data": "password_required"}

            if not confirm_password:
                return {"status": "fail", "data": "confirm_password_required"}

            if password != confirm_password:
                return {"status": "fail", "data": "password_mismatch"}

            error = cls._password_error(password)
            if error:
                return {"status": "fail", "data": error}

            email_fields = EncryptionService.prepare_email_fields(email)
            email_hash = email_fields.get("email_hash")

            if not email_hash:
                return {"status": "fail", "data": "invalid_email"}

            user = CustomUser.objects.select_for_update().filter(email_hash=email_hash).first()

            if not user:
                return {"status": "fail", "data": "invalid_recovery_code"}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            if user.account_status == CustomUser.AccountStatus.BANNED:
                return {"status": "fail", "data": "account_banned"}

            if user.account_status == CustomUser.AccountStatus.SUSPENDED:
                return {"status": "fail", "data": "account_suspended"}

            if not user.is_active:
                return {"status": "fail", "data": "account_inactive"}

            recovery = (
                PasswordRecovery.objects.select_for_update()
                .filter(
                    user=user,
                    is_used=False,
                )
                .order_by("-created_at")
                .first()
            )

            if not recovery:
                return {"status": "fail", "data": "invalid_recovery_code"}

            if recovery.is_expired():
                return {"status": "fail", "data": "recovery_code_expired"}

            if recovery.attempts >= 5:
                return {"status": "fail", "data": "recovery_attempts_exceeded"}

            if not code_matches(code, recovery.code, user.pk, "password_reset"):
                recovery.attempts += 1
                recovery.save(update_fields=["attempts", "updated_at"])

                if recovery.attempts >= 5:
                    return {"status": "fail", "data": "recovery_attempts_exceeded"}

                return {"status": "fail", "data": "invalid_recovery_code"}

            # User-aware validators (name/e-mail similarity) run only after the
            # code is proven, so an unauthenticated caller cannot probe them.
            error = cls._password_error(password, user)
            if error:
                return {"status": "fail", "data": error}

            with transaction.atomic():
                user.set_password(password)
                user.save(update_fields=["password", "updated_at"])

                recovery.mark_used()

                PasswordRecovery.objects.filter(
                    user=user,
                    is_used=False,
                ).exclude(recovery_id=recovery.recovery_id).update(
                    is_used=True,
                    used_at=timezone.now(),
                )

            logger.info("Password reset successful for user_id=%s", user.user_id)

            return {
                "status": "success",
                "data": {
                    "message": "password_reset_successful",
                },
            }

        except Exception:
            logger.exception("Unexpected error during password reset confirmation")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def request_password_reset(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            email = (payload.get("email") or "").strip()

            generic_response = {
                "status": "success",
                "data": {
                    "message": "if_account_exists_recovery_email_sent",
                },
            }

            if not email:
                return generic_response

            email_fields = EncryptionService.prepare_email_fields(email)
            email_hash = email_fields.get("email_hash")

            if not email_hash:
                return generic_response

            user = CustomUser.objects.filter(email_hash=email_hash).first()

            if not user:
                return generic_response

            if user.is_deleted:
                return generic_response

            if user.account_status == CustomUser.AccountStatus.BANNED:
                return generic_response

            if user.account_status == CustomUser.AccountStatus.SUSPENDED:
                return generic_response

            if not user.is_active:
                return generic_response

            to_email = EncryptionService.decrypt_value(user.email_encrypted)
            if not to_email:
                return generic_response

            recovery = PasswordRecovery.create_for_user(user=user, expiry_minutes=10)

            sent = PasswordRecoveryEmailService.send_recovery_code(
                to_email=to_email,
                full_name=user.full_name or user.display_name or user.username,
                code=recovery.raw_code,
            )

            if sent:
                logger.info("Password recovery code sent for user_id=%s", user.user_id)
            else:
                logger.warning("Password recovery email failed for user_id=%s", user.user_id)

            return generic_response

        except Exception:
            logger.exception("Unexpected error during password reset request")
            return {
                "status": "success",
                "data": {
                    "message": "if_account_exists_recovery_email_sent",
                },
            }

    @classmethod
    def verify_email(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user_id = (payload.get("user_id") or "").strip()
            code = (payload.get("code") or "").strip()

            if not user_id:
                return {"status": "fail", "data": "user_id_required"}

            if not code:
                return {"status": "fail", "data": "verification_code_required"}

            user = CustomUser.objects.filter(user_id=user_id).first()

            if not user:
                return {"status": "fail", "data": "user_not_found"}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            result = EmailVerificationService.verify_code(user=user, code=code)

            if result["status"] != "success":
                return result

            logger.info("Email verified for user_id=%s", user.user_id)

            return {
                "status": "success",
                "data": {
                    "message": "email_verified",
                },
            }

        except Exception:
            logger.exception("Unexpected error during email verification")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def resend_verification_code(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user_id = (payload.get("user_id") or "").strip()

            if not user_id:
                return {"status": "fail", "data": "user_id_required"}

            user = CustomUser.objects.filter(user_id=user_id).first()

            if not user:
                return {"status": "fail", "data": "user_not_found"}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            if user.is_verified:
                return {"status": "fail", "data": "email_already_verified"}

            to_email = EncryptionService.decrypt_value(user.email_encrypted)
            if not to_email:
                return {"status": "fail", "data": "invalid_email"}

            verification = EmailVerificationService.create_verification_for_user(user)

            sent = VerificationEmailService.send_verification_code(
                to_email=to_email,
                full_name=user.full_name or user.display_name or user.username,
                code=verification["code"],
            )

            if not sent:
                return {"status": "fail", "data": "email_send_failed"}

            logger.info("Verification email resent for user_id=%s", user.user_id)

            return {
                "status": "success",
                "data": {
                    "message": "verification_email_resent",
                },
            }

        except Exception:
            logger.exception("Unexpected error during resend verification code")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def signup_user(cls, payload: Dict[str, Any], request=None) -> Dict[str, Any]:
        try:
            username = (payload.get("username") or "").strip().lower()
            full_name = (payload.get("full_name") or "").strip()
            email = payload.get("email")
            password = payload.get("password")
            confirm_password = payload.get("confirm_password")
            recaptcha_token = payload.get("recaptcha_token")

            display_name = (payload.get("display_name") or "").strip() or None
            phone_number = payload.get("phone_number")
            preferred_language = (payload.get("preferred_language") or CustomUser.LanguageChoices.ENGLISH).strip()

            # --- NEW: extract optional gender ---
            gender = payload.get("gender")  # Could be None or one of GenderChoices values

            user_timezone = payload.get("timezone", "Africa/Nairobi").strip()
            age_confirmed = payload.get("age_confirmed", False)
            terms_accepted = payload.get("terms_accepted", False)
            privacy_accepted = payload.get("privacy_accepted", False)
            compliance_version = (payload.get("compliance_version") or "v1").strip()

            if not username:
                return {"status": "fail", "data": "username_required"}

            if not email:
                return {"status": "fail", "data": "email_required"}

            if not password:
                return {"status": "fail", "data": "password_required"}

            if not confirm_password:
                return {"status": "fail", "data": "confirm_password_required"}

            if password != confirm_password:
                return {"status": "fail", "data": "password_mismatch"}

            error = cls._password_error(password, CustomUser(username=username, full_name=full_name))
            if error:
                return {"status": "fail", "data": error}

            if age_confirmed is not True:
                return {"status": "fail", "data": "age_confirmation_required"}

            if terms_accepted is not True:
                return {"status": "fail", "data": "terms_acceptance_required"}

            if privacy_accepted is not True:
                return {"status": "fail", "data": "privacy_acceptance_required"}

            ip_address, user_agent = cls._extract_request_metadata(request)
            if not user_timezone:
                user_timezone = "Africa/Nairobi"

            recaptcha_result = RecaptchaService.verify_token(
                recaptcha_token,
                remote_ip=ip_address,
            )

            if recaptcha_result["success"] is not True:
                logger.warning(
                    "Recaptcha failed for signup username=%s ip=%s code=%s",
                    username,
                    ip_address,
                    recaptcha_result["code"],
                )
                return {
                    "status": "fail",
                    "data": recaptcha_result["code"],
                }

            email_fields = EncryptionService.prepare_email_fields(email)
            phone_fields = EncryptionService.prepare_phone_fields(phone_number)

            if not email_fields["email_hash"]:
                return {"status": "fail", "data": "invalid_email"}

            if CustomUser.objects.filter(username=username).exists():
                return {"status": "fail", "data": "username_already_exists"}

            if CustomUser.objects.filter(email_hash=email_fields["email_hash"]).exists():
                return {"status": "fail", "data": "email_already_exists"}

            verification_payload = {}

            logger.info("Starting atomic signup block for username=%s", username)

            with transaction.atomic():
                user = CustomUser.objects.create_user(
                    username=username,
                    password=password,
                    full_name=full_name,
                    display_name=display_name,
                    preferred_language=preferred_language,
                    gender=gender,  # <--- NEW: pass gender (may be None)
                    auth_provider=CustomUser.AuthProvider.USERNAME,
                    account_status=CustomUser.AccountStatus.PENDING,
                    is_verified=False,
                    age_verified=True,
                    age_verified_at=timezone.now(),
                    age_verification_method=CustomUser.AgeVerificationMethod.SELF_DECLARATION,
                    compliance_version=compliance_version,
                    daily_token_quota=0,
                    total_token_quota=0,
                    daily_tokens_used=0,
                    total_tokens_used=0,
                    daily_quota_reset_at=timezone.now(),
                    email_encrypted=email_fields["email_encrypted"],
                    email_hash=email_fields["email_hash"],
                    phone_number_encrypted=phone_fields["phone_number_encrypted"],
                    phone_number_hash=phone_fields["phone_number_hash"],
                    timezone=user_timezone,
                )

                logger.info("User created: user_id=%s", user.user_id)

                # Bind this account to the guest session it was created from, if
                # there is one. The identity comes from the request's HttpOnly
                # credential cookie and nothing else, so the payload cannot
                # nominate a guest session the caller does not hold. Written here
                # so the account and its entitlement commit together; without a
                # grant the account simply never claims anything.
                GuestTransferGrantService.grant_at_signup(
                    user,
                    GuestIdentityService.resolve(request) if request is not None else None,
                )

                ComplianceConfirmationLog.objects.create(
                    user=user,
                    confirmation_type=ComplianceConfirmationLog.ConfirmationType.AGE_GATE,
                    accepted=True,
                    version=compliance_version,
                    accepted_at=timezone.now(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

                ComplianceConfirmationLog.objects.create(
                    user=user,
                    confirmation_type=ComplianceConfirmationLog.ConfirmationType.TERMS,
                    accepted=True,
                    version=compliance_version,
                    accepted_at=timezone.now(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

                ComplianceConfirmationLog.objects.create(
                    user=user,
                    confirmation_type=ComplianceConfirmationLog.ConfirmationType.PRIVACY,
                    accepted=True,
                    version=compliance_version,
                    accepted_at=timezone.now(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

                # ---------- Create spark_pool with signup bonus ----------
                spark_pool = SparkPool.objects.create(
                    user=user,
                    sparks=SIGNUP_SPARKS,
                    token_balance=0,
                    is_active=True,
                )

                logger.info(
                    "SparkPool created: spark_pool_id=%s sparks=%d",
                    spark_pool.spark_pool_id,
                    spark_pool.sparks,
                )

                SparkTransaction.objects.create(
                    spark_pool=spark_pool,
                    user=user,
                    entry_type=SparkTransaction.EntryType.CREDIT,
                    source=SparkTransaction.SourceChoices.BONUS,
                    spark_amount=SIGNUP_SPARKS,
                    sparks_before=0,
                    sparks_after=SIGNUP_SPARKS,
                    description="Sign-up bonus sparks",
                )

                logger.info(
                    "SparkTransaction created for signup bonus of %d sparks",
                    SIGNUP_SPARKS,
                )

                verification = EmailVerificationService.create_verification_for_user(user)

                verification_payload = {
                    "to_email": email,
                    "full_name": user.full_name,
                    "code": verification["code"],
                    "user_id": str(user.user_id),
                }

                transaction.on_commit(
                    lambda: VerificationEmailService.send_verification_code(
                        to_email=verification_payload["to_email"],
                        full_name=verification_payload["full_name"],
                        code=verification_payload["code"],
                    )
                )

            logger.info(
                "User signup successful and verification email queued for username=%s",
                username,
            )

            return {
                "status": "success",
                "data": {
                    "message": "verification_email_sent",
                    "user": {
                        "user_id": str(user.user_id),
                        "username": user.username,
                        "full_name": user.full_name,
                        "display_name": user.display_name,
                        "preferred_language": user.preferred_language,
                        "gender": user.gender,  # <--- NEW: include gender in response
                        "auth_provider": user.auth_provider,
                        "account_status": user.account_status,
                        "is_verified": user.is_verified,
                    },
                    "spark_pool": {
                        "spark_pool_id": str(spark_pool.spark_pool_id),
                        "sparks": int(spark_pool.sparks),
                        "token_balance": int(spark_pool.token_balance),
                    },
                },
            }

        except IntegrityError:
            logger.exception("IntegrityError during signup for username=%s", payload.get("username"))
            return {"status": "fail", "data": "integrity_error"}

        except Exception:
            logger.exception("Unexpected error during signup")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def signin_user(cls, payload: Dict[str, Any], request=None) -> Dict[str, Any]:
        try:
            username = (payload.get("username") or "").strip().lower()
            password = payload.get("password")
            recaptcha_token = payload.get("recaptcha_token")
            if not username:
                return {"status": "fail", "data": "username_required"}

            if not password:
                return {"status": "fail", "data": "password_required"}

            ip_address, _ = cls._extract_request_metadata(request)

            lockout_status = LoginLockoutService.get_lockout_status(
                username=username,
                ip_address=ip_address,
            )
            if lockout_status.is_locked:
                logger.warning(
                    "Blocked locked-out signin attempt for username=%s ip=%s retry_after=%s",
                    username,
                    ip_address,
                    lockout_status.retry_after_seconds,
                )
                return {
                    "status": "fail",
                    "data": {
                        "code": "account_locked",
                        "retry_after_seconds": lockout_status.retry_after_seconds,
                    },
                }

            # reCAPTCHA is enforced for ALL accounts — including staff
            # and superusers. The admin panel (uwenzAi-Admin) now carries
            # its own reCAPTCHA widget, so the staff bypass is no longer
            # needed. Removing it also closes a username-enumeration oracle
            # where a missing recaptcha_token returned different errors for
            # staff vs non-staff accounts.
            recaptcha_result = RecaptchaService.verify_token(
                recaptcha_token,
                remote_ip=ip_address,
            )

            if recaptcha_result["success"] is not True:
                logger.warning(
                    "Recaptcha failed for username=%s ip=%s code=%s",
                    username,
                    ip_address,
                    recaptcha_result["code"],
                )
                return {
                    "status": "fail",
                    "data": recaptcha_result["code"],
                }

            user = CustomUser.objects.filter(username=username).first()

            if not user:
                lockout_status = LoginLockoutService.record_failure(
                    username=username,
                    ip_address=ip_address,
                )
                logger.warning(
                    "Signin failed: invalid username username=%s ip=%s locked=%s retry_after=%s",
                    username,
                    ip_address,
                    lockout_status.is_locked,
                    lockout_status.retry_after_seconds,
                )
                if lockout_status.is_locked:
                    return {
                        "status": "fail",
                        "data": {
                            "code": "account_locked",
                            "retry_after_seconds": lockout_status.retry_after_seconds,
                        },
                    }
                return {"status": "fail", "data": "invalid_credentials"}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            if user.account_status == CustomUser.AccountStatus.BANNED:
                return {"status": "fail", "data": "account_banned"}

            if user.account_status == CustomUser.AccountStatus.SUSPENDED:
                return {"status": "fail", "data": "account_suspended"}

            if not user.is_active:
                return {"status": "fail", "data": "account_inactive"}

            authenticated_user = authenticate(username=username, password=password)

            if not authenticated_user:
                lockout_status = LoginLockoutService.record_failure(
                    username=username,
                    ip_address=ip_address,
                )
                logger.warning(
                    "Signin failed: bad password username=%s ip=%s locked=%s retry_after=%s",
                    username,
                    ip_address,
                    lockout_status.is_locked,
                    lockout_status.retry_after_seconds,
                )
                if lockout_status.is_locked:
                    return {
                        "status": "fail",
                        "data": {
                            "code": "account_locked",
                            "retry_after_seconds": lockout_status.retry_after_seconds,
                        },
                    }
                return {"status": "fail", "data": "invalid_credentials"}

            LoginLockoutService.clear_failures(
                username=username,
                ip_address=ip_address,
            )

            if not authenticated_user.is_verified:
                logger.info(
                    "Signin blocked: email not verified for username=%s",
                    username,
                )
                return {
                    "status": "fail",
                    "data": {
                        "code": "email_verification_required",
                        "user_id": str(authenticated_user.user_id),
                        "is_verified": False,
                        "email": EncryptionService.decrypt_value(authenticated_user.email_encrypted),
                    },
                }

            refresh = RefreshToken.for_user(authenticated_user)

            # Track last-seen timestamp for the admin panel ("zuletzt online").
            authenticated_user.last_login = timezone.now()
            authenticated_user.save(update_fields=["last_login"])

            logger.info("User signin successful for username=%s", username)

            return {
                "status": "success",
                "data": {
                    "message": "signin_successful",
                    "access_token": str(refresh.access_token),
                    "refresh_token": str(refresh),
                    "user": cls._serialize_user(authenticated_user),
                },
            }

        except Exception:
            logger.exception("Unexpected error during signin")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def google_auth_user(cls, payload: Dict[str, Any], request=None) -> Dict[str, Any]:
        try:
            credential = payload.get("credential")

            if not credential:
                return {"status": "fail", "data": "google_credential_required"}

            if not settings.GOOGLE_WEB_CLIENT_ID:
                logger.error("GOOGLE_WEB_CLIENT_ID is not configured.")
                return {"status": "fail", "data": "google_auth_not_configured"}

            idinfo = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                audience=settings.GOOGLE_WEB_CLIENT_ID,
            )

            google_sub = idinfo.get("sub")
            email = idinfo.get("email")
            email_verified = idinfo.get("email_verified", False)

            if not google_sub:
                return {"status": "fail", "data": "invalid_google_sub"}

            if not email:
                return {"status": "fail", "data": "google_email_missing"}

            if email_verified is not True:
                return {"status": "fail", "data": "google_email_not_verified"}

            email_fields = EncryptionService.prepare_email_fields(email)
            if not email_fields["email_hash"]:
                return {"status": "fail", "data": "invalid_google_email"}

            user = CustomUser.objects.filter(google_sub=google_sub).first()

            if payload.get("confirm_link") is True:
                # Linking is an account-bound action, not an alternate login.
                # Also enforce this for an already-linked Google subject:
                # otherwise choosing another Google account would issue its
                # refresh cookie and switch the signed-in account.
                confirming_user = getattr(request, "user", None)
                if (
                    not getattr(confirming_user, "is_authenticated", False)
                    or confirming_user.email_hash != email_fields["email_hash"]
                    or (user is not None and confirming_user.pk != user.pk)
                ):
                    return {"status": "fail", "data": "google_link_confirmation_required"}

            if not user:
                existing_email_user = CustomUser.objects.filter(email_hash=email_fields["email_hash"]).first()

                if existing_email_user:
                    # Google email possession is not authority to attach a new
                    # provider identity to an existing account. First sign in
                    # using that account's credentials, then explicitly confirm
                    # this link with the same authenticated account's JWT.
                    confirming_user = getattr(request, "user", None)
                    if (
                        payload.get("confirm_link") is not True
                        or not getattr(confirming_user, "is_authenticated", False)
                        or confirming_user.pk != existing_email_user.pk
                    ):
                        return {"status": "fail", "data": "google_link_confirmation_required"}
                    # Never replace an existing different Google binding.
                    if existing_email_user.google_sub:
                        return {"status": "fail", "data": "google_account_already_linked"}

                    if existing_email_user.is_deleted:
                        return {"status": "fail", "data": "account_deleted"}

                    if existing_email_user.account_status == CustomUser.AccountStatus.BANNED:
                        return {"status": "fail", "data": "account_banned"}

                    if existing_email_user.account_status == CustomUser.AccountStatus.SUSPENDED:
                        return {"status": "fail", "data": "account_suspended"}

                    if not existing_email_user.is_active:
                        return {"status": "fail", "data": "account_inactive"}

                    existing_email_user.google_sub = google_sub

                    updated_fields = ["google_sub"]

                    if not existing_email_user.is_verified:
                        existing_email_user.is_verified = True
                        updated_fields.append("is_verified")

                    if existing_email_user.account_status == CustomUser.AccountStatus.PENDING:
                        existing_email_user.account_status = CustomUser.AccountStatus.ACTIVE
                        updated_fields.append("account_status")

                    updated_fields.append("updated_at")
                    # Compare-and-set prevents two concurrent confirmations
                    # replacing each other's provider identities.
                    changes = {field: getattr(existing_email_user, field) for field in updated_fields}
                    changes["updated_at"] = timezone.now()
                    linked = (
                        CustomUser.objects.filter(
                            pk=existing_email_user.pk,
                            google_sub__isnull=True,
                            email_hash=email_fields["email_hash"],
                            is_active=True,
                            is_deleted=False,
                        )
                        .exclude(account_status__in=["banned", "suspended"])
                        .update(**changes)
                    )
                    if not linked:
                        return {"status": "fail", "data": "google_account_already_linked"}

                    user = existing_email_user
                else:
                    return {"status": "fail", "data": "google_signup_not_allowed"}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            if user.account_status == CustomUser.AccountStatus.BANNED:
                return {"status": "fail", "data": "account_banned"}

            if user.account_status == CustomUser.AccountStatus.SUSPENDED:
                return {"status": "fail", "data": "account_suspended"}

            if not user.is_active:
                return {"status": "fail", "data": "account_inactive"}

            if user.email_hash != email_fields["email_hash"]:
                logger.warning(
                    "Google email mismatch for user_id=%s during Google sign-in.",
                    user.user_id,
                )
                return {"status": "fail", "data": "google_email_mismatch"}

            updated_fields = []

            if not user.is_verified:
                user.is_verified = True
                updated_fields.append("is_verified")

            if user.account_status == CustomUser.AccountStatus.PENDING:
                user.account_status = CustomUser.AccountStatus.ACTIVE
                updated_fields.append("account_status")

            if updated_fields:
                updated_fields.append("updated_at")
                user.save(update_fields=updated_fields)

            if payload.get("confirm_link") is True:
                # This mutates only the authenticated account's provider
                # binding. It must not rotate tokens/cookies or recreate a
                # login session if the browser changed accounts meanwhile.
                return {
                    "status": "success",
                    "data": {
                        "message": "google_link_successful",
                        "linked": True,
                        "user": {"user_id": str(user.pk)},
                    },
                }

            # ✅ FIXED SPARK_POOL CREATION
            spark_pool = getattr(user, "spark_pool", None)
            if not spark_pool:
                spark_pool = SparkPool.objects.create(
                    user=user,
                    sparks=SIGNUP_SPARKS,
                    token_balance=0,
                    is_active=True,
                )

                SparkTransaction.objects.create(
                    spark_pool=spark_pool,
                    user=user,
                    entry_type=SparkTransaction.EntryType.CREDIT,
                    source=SparkTransaction.SourceChoices.BONUS,
                    spark_amount=SIGNUP_SPARKS,
                    sparks_before=0,
                    sparks_after=SIGNUP_SPARKS,
                    description="Sign-up bonus sparks",
                )

            refresh = RefreshToken.for_user(user)

            # Track last-seen timestamp for the admin panel ("zuletzt online").
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])

            logger.info("Google auth successful for user_id=%s", user.user_id)

            return {
                "status": "success",
                "data": {
                    "message": "google_auth_successful",
                    "access_token": str(refresh.access_token),
                    "refresh_token": str(refresh),
                    "user": cls._serialize_user(user),
                    "spark_pool": {
                        "spark_pool_id": str(spark_pool.spark_pool_id),
                        "sparks": int(spark_pool.sparks),
                        "token_balance": int(spark_pool.token_balance),
                    },
                },
            }

        except ValueError:
            logger.exception("Google token verification failed.")
            return {"status": "fail", "data": "invalid_google_credential"}

        except Exception:
            logger.exception("Unexpected error during Google auth")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def refresh_access_token(cls, refresh_token: str) -> Dict[str, Any]:
        try:
            if not refresh_token:
                return {"status": "fail", "data": "refresh_token_required"}

            old_refresh = RefreshToken(refresh_token)
            user_id = old_refresh.get("user_id")

            user = CustomUser.objects.filter(user_id=user_id).first()

            if not user:
                return {"status": "fail", "data": "user_not_found"}

            if not account_can_authenticate(user):
                return {"status": "fail", "data": "account_inactive"}

            try:
                old_refresh.blacklist()
            except Exception:
                logger.warning("Token blacklist failed for user_id=%s", user_id)

            new_refresh = RefreshToken.for_user(user)

            # Access tokens are short-lived (15 min), so a refresh happens
            # automatically while the app is open. This makes last_login a much
            # better "last active" proxy for the admin panel than login-time alone.
            user.last_login = timezone.now()
            user.save(update_fields=["last_login"])

            return {
                "status": "success",
                "data": {
                    "message": "token_refreshed",
                    "access_token": str(new_refresh.access_token),
                    "refresh_token": str(new_refresh),
                    "user": cls._serialize_user(user),
                },
            }

        except TokenError:
            return {"status": "fail", "data": "invalid_refresh_token"}

        except Exception:
            logger.exception("Error refreshing token")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def signout_user(cls, refresh_token: str) -> Dict[str, Any]:
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
            return {"status": "success", "data": "signout_successful"}

        except TokenError:
            return {"status": "fail", "data": "invalid_refresh_token"}

        except Exception:
            logger.exception("Error during signout")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def get_authenticated_user_data(cls, user: CustomUser) -> Dict[str, Any]:
        try:
            if not user:
                return {"status": "fail", "data": "user_required"}

            if user.is_deleted:
                return {"status": "fail", "data": "account_deleted"}

            return {
                "status": "success",
                "data": {
                    "user": cls._serialize_user(user),
                },
            }

        except Exception:
            logger.exception("Error serializing user")
            return {"status": "fail", "data": "server_error"}

    @classmethod
    def _serialize_user(cls, user: CustomUser) -> Dict[str, Any]:
        return {
            "user_id": str(user.user_id),
            "username": user.username,
            "email": EncryptionService.decrypt_value(user.email_encrypted),
            "full_name": user.full_name,
            "display_name": user.display_name,
            "phone_number": EncryptionService.decrypt_value(user.phone_number_encrypted),
            "preferred_language": user.preferred_language,
            "auth_provider": user.auth_provider,
            "account_status": user.account_status,
            "is_verified": user.is_verified,
            "age_verified": user.age_verified,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "daily_token_quota": user.daily_token_quota,
            "total_token_quota": user.total_token_quota,
            "daily_tokens_used": user.daily_tokens_used,
            "total_tokens_used": user.total_tokens_used,
            "timezone": user.timezone,
            "feature_preferences": FeaturePreferences.get(user),
        }

    @classmethod
    def _extract_request_metadata(cls, request=None):
        ip_address = None
        user_agent = None

        if request:
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(",")[0].strip()
            else:
                ip_address = request.META.get("REMOTE_ADDR")

            user_agent = request.META.get("HTTP_USER_AGENT")

        return ip_address, user_agent

    @classmethod
    def _generate_unique_username(cls, email: str, full_name: str) -> str:
        base_username = None

        if email:
            normalized_email = EncryptionService.normalize_email(email)
            if normalized_email and "@" in normalized_email:
                base_username = normalized_email.split("@")[0]

        if not base_username and full_name:
            base_username = "".join(ch for ch in full_name.lower() if ch.isalnum())

        if not base_username:
            base_username = "user"

        base_username = base_username[:30]
        candidate = base_username
        counter = 1

        while CustomUser.objects.filter(username=candidate).exists():
            suffix = str(counter)
            candidate = f"{base_username[:30 - len(suffix)]}{suffix}"
            counter += 1

        return candidate
