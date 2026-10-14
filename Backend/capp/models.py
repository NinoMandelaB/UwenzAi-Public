import uuid
import logging
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.db.models.signals import pre_save, pre_delete, post_delete
from django.dispatch import receiver

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction
from capp.fields import EncryptedTextField
from django.db.models import Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify

from pgvector.django import VectorField
from capp.constants import GUEST_INITIAL_SPARKS


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CustomUserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("The Username field must be set")

        full_name = extra_fields.get("full_name")
        if full_name is not None:
            extra_fields["full_name"] = full_name.strip() or None

        email_encrypted = extra_fields.get("email_encrypted")
        email_hash = extra_fields.get("email_hash")

        if not email_encrypted or not email_hash:
            raise ValueError("Email must be provided (encrypted + hash required)")

        username = username.strip().lower()

        auth_provider = extra_fields.get("auth_provider", CustomUser.AuthProvider.USERNAME)
        google_sub = extra_fields.get("google_sub")

        phone_number_encrypted = extra_fields.get("phone_number_encrypted")
        phone_number_hash = extra_fields.get("phone_number_hash")

        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_verified", False)
        extra_fields.setdefault("is_deleted", False)
        extra_fields.setdefault("deleted_at", None)
        extra_fields.setdefault("account_status", CustomUser.AccountStatus.PENDING)
        extra_fields.setdefault("preferred_language", CustomUser.LanguageChoices.ENGLISH)

        extra_fields.setdefault("age_verified", False)
        extra_fields.setdefault("age_verified_at", None)
        extra_fields.setdefault("age_verification_method", None)
        extra_fields.setdefault("compliance_version", None)
        extra_fields.setdefault("ban_reason", None)

        extra_fields.setdefault("daily_token_quota", 0)
        extra_fields.setdefault("total_token_quota", 0)
        extra_fields.setdefault("daily_tokens_used", 0)
        extra_fields.setdefault("total_tokens_used", 0)
        extra_fields.setdefault("daily_quota_reset_at", None)

        extra_fields.setdefault("kdf_salt", None)
        extra_fields.setdefault("encrypted_dek", None)
        extra_fields.setdefault("recovery_encrypted_dek", None)
        extra_fields.setdefault("recovery_verifier", None)

        if auth_provider == CustomUser.AuthProvider.GOOGLE and not google_sub:
            raise ValueError("Google users must have google_sub set.")

        if auth_provider == CustomUser.AuthProvider.USERNAME and google_sub:
            raise ValueError("Username-auth users should not have google_sub set.")

        if phone_number_encrypted and not phone_number_hash:
            raise ValueError("Encrypted phone number must have phone_number_hash set.")

        user = self.model(username=username, **extra_fields)

        if auth_provider == CustomUser.AuthProvider.USERNAME:
            if not password:
                raise ValueError("Password must be set for username-auth users.")
            user.set_password(password)
        else:
            if password:
                user.set_password(password)
            else:
                user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("auth_provider", CustomUser.AuthProvider.USERNAME)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_verified", True)
        extra_fields.setdefault("is_deleted", False)
        extra_fields.setdefault("account_status", CustomUser.AccountStatus.ACTIVE)
        extra_fields.setdefault("age_verified", True)
        extra_fields.setdefault("age_verified_at", timezone.now())
        extra_fields.setdefault(
            "age_verification_method",
            CustomUser.AgeVerificationMethod.ADMIN_OVERRIDE,
        )

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        if extra_fields.get("auth_provider") != CustomUser.AuthProvider.USERNAME:
            raise ValueError("Superuser must use username auth.")

        if not password:
            raise ValueError("Superuser must have a password.")

        return self.create_user(username, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    class LanguageChoices(models.TextChoices):
        ENGLISH = "en", "English"
        SWAHILI = "sw", "Swahili"
        SHENG = "sh", "Sheng"

    class AccountStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        BANNED = "banned", "Banned"
        DEACTIVATED = "deactivated", "Deactivated"
        # Automatic cross-session safety lock (repeated or critical safety
        # violations). Deliberately NOT in the banned/suspended family: the
        # user may still sign in and read their status / appeal via support,
        # but every generation action (chat, images) is refused until an
        # admin reviews and unlocks. See
        # capp/classes/moderation/safety_ledger.py.
        SAFETY_LOCKED = "safety_locked", "Safety Locked"

    class AuthProvider(models.TextChoices):
        USERNAME = "username", "Username"
        GOOGLE = "google", "Google"

    class AgeVerificationMethod(models.TextChoices):
        SELF_DECLARATION = "self_declaration", "Self Declaration"
        ADMIN_OVERRIDE = "admin_override", "Admin Override"

    # NEW: Gender choices
    class GenderChoices(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        NON_BINARY = "non_binary", "Non-binary"
        PREFER_NOT_TO_SAY = "prefer_not_to_say", "Prefer not to say"
        OTHER = "other", "Other"

    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    username = models.CharField(max_length=50, unique=True)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    display_name = models.CharField(max_length=100, blank=True, null=True)

    email_encrypted = models.TextField(blank=True, null=True)
    email_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)

    phone_number_encrypted = models.TextField(blank=True, null=True)
    phone_number_hash = models.CharField(max_length=64, unique=True, blank=True, null=True)

    avatar = models.ImageField(upload_to="users/avatars/", blank=True, null=True)

    auth_provider = models.CharField(
        max_length=20,
        choices=AuthProvider.choices,
        default=AuthProvider.USERNAME,
    )
    google_sub = models.CharField(max_length=255, blank=True, null=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)

    account_status = models.CharField(
        max_length=20,
        choices=AccountStatus.choices,
        default=AccountStatus.PENDING,
    )
    ban_reason = models.TextField(blank=True, null=True)

    age_verified = models.BooleanField(default=False)
    age_verified_at = models.DateTimeField(blank=True, null=True)
    age_verification_method = models.CharField(
        max_length=30,
        choices=AgeVerificationMethod.choices,
        blank=True,
        null=True,
    )
    compliance_version = models.CharField(max_length=50, blank=True, null=True)

    # NEW: optional gender field
    gender = models.CharField(
        max_length=20,
        choices=GenderChoices.choices,
        blank=True,
        null=True,
        help_text="User's gender identity",
    )

    preferred_language = models.CharField(
        max_length=5,
        choices=LanguageChoices.choices,
        default=LanguageChoices.ENGLISH,
    )

    daily_token_quota = models.PositiveBigIntegerField(default=0)
    total_token_quota = models.PositiveBigIntegerField(default=0)
    daily_tokens_used = models.PositiveBigIntegerField(default=0)
    total_tokens_used = models.PositiveBigIntegerField(default=0)
    daily_quota_reset_at = models.DateTimeField(blank=True, null=True)

    kdf_salt = models.CharField(max_length=255, blank=True, null=True)
    encrypted_dek = models.TextField(blank=True, null=True)
    recovery_encrypted_dek = models.TextField(blank=True, null=True)
    recovery_verifier = models.CharField(max_length=255, blank=True, null=True)
    timezone = models.CharField(
        max_length=50,
        default="Africa/Nairobi",
        blank=True,
        null=True,
        help_text="IANA timezone name (e.g., Africa/Nairobi, America/New_York)",
    )

    # Per-account feature visibility toggles (Settings → Advanced). Keys are
    # managed by capp.classes.auth.feature_preferences — arbitrary keys are
    # rejected there, so this field only ever holds known feature flags.
    # Default: {} → use FeaturePreferences availability defaults, without
    # enabling per-chat search/deep reasoning. Saved choices override defaults.
    # BYOK keeps its own always-available section and is not a flag here.
    feature_preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional chat features the user has enabled (reminders, deepthink, websearch, memory_editor)",
    )

    # Audience analytics: server-timestamped site-visit recording. Written only
    # by the /api/v1/analytics/visit/ endpoint (see
    # capp/classes/analytics/visit_service.py) — never by the auth or chat
    # flows — so "visit" keeps its own meaning (the public app was opened),
    # distinct from logins, token refreshes and chat activity. first_visit_at is
    # set once and never rewritten; last_visit_at is coalesced server-side.
    # Both stay null for accounts that never triggered a recorded visit after
    # this feature shipped, which is an honest "unknown", not a zero.
    first_visit_at = models.DateTimeField(blank=True, null=True)
    last_visit_at = models.DateTimeField(blank=True, null=True)
    # Approximate country of the latest recorded visit, taken from the trusted
    # edge's country header (see VISIT_COUNTRY_HEADER in settings). Country
    # level only — accounts have no IP stored for this. Nulled on erasure.
    visit_country_code = models.CharField(max_length=2, blank=True, null=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    class Meta:
        db_table = "custom_users"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["account_status"]),
            models.Index(fields=["auth_provider"]),
            models.Index(fields=["is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["google_sub"],
                condition=Q(google_sub__isnull=False),
                name="unique_google_sub_not_null",
            ),
            models.UniqueConstraint(
                fields=["email_hash"],
                condition=Q(email_hash__isnull=False),
                name="unique_email_hash_not_null",
            ),
            models.UniqueConstraint(
                fields=["phone_number_hash"],
                condition=Q(phone_number_hash__isnull=False),
                name="unique_phone_number_hash_not_null",
            ),
        ]

    def __str__(self):
        return self.get_display_name()

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.display_name or self.username

    def get_display_name(self):
        return self.full_name or self.display_name or self.username

    @property
    def is_banned(self):
        return self.account_status == self.AccountStatus.BANNED

    def mark_age_verified(
        self,
        method=AgeVerificationMethod.SELF_DECLARATION,
        compliance_version=None,
    ):
        self.age_verified = True
        self.age_verified_at = timezone.now()
        self.age_verification_method = method

        if compliance_version:
            self.compliance_version = compliance_version

        self.save(
            update_fields=[
                "age_verified",
                "age_verified_at",
                "age_verification_method",
                "compliance_version",
                "updated_at",
            ]
        )

    def get_decrypted_email(self) -> str:
        """Return the plaintext email, or an empty string if none set."""
        if not self.email_encrypted:
            return ""
        return EncryptedTextField.decrypt_text(self.email_encrypted)

    def soft_delete(self):
        with transaction.atomic():
            type(self).objects.select_for_update().get(pk=self.pk)
            # Fence pending playground saves even on a soft-delete-only path.
            AgentPlaygroundHistory.objects.filter(user=self).delete()
            # Stamp revoked_at so the revoked-key retention task can purge
            # these keys; without it they would linger in the key list forever.
            # Keep the original timestamp of keys that were already revoked
            # (same rule as AgentKeyService.revoke_key) so their retention
            # window does not restart.
            AgentAPIKey.objects.filter(user=self).update(
                is_active=False, revoked_at=Coalesce("revoked_at", Value(timezone.now()))
            )
            self.is_deleted = True
            self.deleted_at = timezone.now()
            self.is_active = False
            self.account_status = self.AccountStatus.DEACTIVATED
            self.save(
                update_fields=[
                    "is_deleted",
                    "deleted_at",
                    "is_active",
                    "account_status",
                    "updated_at",
                ]
            )

    def mark_email_verified(self):
        self.is_verified = True
        if self.account_status == self.AccountStatus.PENDING:
            self.account_status = self.AccountStatus.ACTIVE

        self.save(update_fields=["is_verified", "account_status", "updated_at"])


class UserBYOKConfig(TimeStampedModel):
    """
    Bring-Your-Own-Key configuration stored per user.

    The API key is encrypted at rest with FIELD_ENCRYPTION_KEY (Fernet),
    exactly like email / phone PII on CustomUser.

    The system_prompt is stored in **clear text** intentionally so that
    platform moderators can review it for ToS compliance.

    If `store_on_platform` is False the row is deleted immediately after the
    session is ended (handled by the view / service layer). When True, the
    row persists so the user does not have to re-enter credentials.
    """

    user = models.OneToOneField(
        "capp.CustomUser",
        on_delete=models.CASCADE,
        related_name="byok_config",
        verbose_name="User",
    )

    # ── Connection settings ───────────────────────────────────────────────────
    api_url = models.CharField(
        max_length=512,
        help_text="Base URL of the OpenAI-compatible API, e.g. https://api.cortecs.ai/v1",
    )
    api_key_encrypted = models.TextField(
        help_text="Fernet-encrypted API key (FIELD_ENCRYPTION_KEY). Never stored in plain text.",
    )
    model_name = models.CharField(
        max_length=256,
        help_text="Model identifier passed to the API, e.g. deepseek-v4-flash",
    )

    # ── Optional system-prompt override ──────────────────────────────────────
    # Stored CLEAR TEXT for moderation. Users are warned at input time.
    # Scope: replaces ONLY the "=== PLATFORM RULES (System) ===" section of
    # the assembled prompt (see PromptBuilder._build_system_prompt). The
    # character card (personality), memory, relationship, summary and
    # safety sections are never affected by this override.
    system_prompt = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Optional system prompt override. Stored in clear text for moderation. "
            "Must comply with platform Terms of Service."
        ),
    )

    # ── Persistence preference ────────────────────────────────────────────────
    store_on_platform = models.BooleanField(
        default=False,
        help_text=(
            "If True the config persists between sessions. " "If False it should be deleted when the user logs out."
        ),
    )

    # ── Status ────────────────────────────────────────────────────────────────
    is_active = models.BooleanField(
        default=True,
        help_text="User can toggle BYOK off without deleting credentials.",
    )

    class Meta:
        verbose_name = "User BYOK Config"
        verbose_name_plural = "User BYOK Configs"

    def __str__(self):
        return f"BYOKConfig({self.user.username}, model={self.model_name}, active={self.is_active})"

    # ── Encryption helpers ────────────────────────────────────────────────────
    def get_decrypted_api_key(self) -> str:
        from capp.utils.encryption import EncryptionService

        return EncryptionService.decrypt_value(self.api_key_encrypted) or ""

    @classmethod
    def create_or_update(
        cls,
        user,
        api_url: str,
        api_key: str,
        model_name: str,
        system_prompt: str = "",
        store_on_platform: bool = False,
    ):
        from capp.utils.encryption import EncryptionService

        encrypted_key = EncryptionService.encrypt_value(api_key)
        obj, created = cls.objects.update_or_create(
            user=user,
            defaults={
                "api_url": api_url.strip().rstrip("/"),
                "api_key_encrypted": encrypted_key,
                "model_name": model_name.strip(),
                "system_prompt": system_prompt.strip(),
                "store_on_platform": store_on_platform,
                "is_active": True,
            },
        )
        return obj, created


class PasswordRecovery(models.Model):
    recovery_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "capp.CustomUser",
        on_delete=models.CASCADE,
        related_name="password_recoveries",
    )
    code = models.CharField(max_length=128, db_index=True)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(blank=True, null=True)
    attempts = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "password_recoveries"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_used"]),
            models.Index(fields=["expires_at"]),
        ]

    @classmethod
    def generate_code(cls, length=6) -> str:
        from capp.classes.auth.codes import generate_code

        return generate_code(length)

    def save(self, *args, **kwargs):
        from capp.classes.auth.codes import digest_code

        if isinstance(self.code, str) and len(self.code) == 6 and self.code.isascii() and self.code.isdigit():
            self.raw_code = self.code  # transient: email only; never persisted
            self.code = digest_code(self.code, self.user_id, "password_reset")
        super().save(*args, **kwargs)

    @classmethod
    @transaction.atomic
    def create_for_user(cls, user, expiry_minutes=10):
        CustomUser.objects.select_for_update().get(pk=user.pk)
        cls.objects.filter(user=user, is_used=False).update(
            is_used=True,
            used_at=timezone.now(),
        )

        code = cls.generate_code()

        recovery = cls.objects.create(
            user=user,
            code=code,
            expires_at=timezone.now() + timedelta(minutes=expiry_minutes),
        )

        return recovery

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_used(self):
        self.is_used = True
        self.used_at = timezone.now()
        self.save(update_fields=["is_used", "used_at", "updated_at"])


class CharacterFlag(TimeStampedModel):
    flag_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    character = models.ForeignKey(
        "Character",
        on_delete=models.CASCADE,
        related_name="character_flags",
    )
    reported_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_character_flags",
    )
    guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    reason = models.TextField()

    class Meta:
        db_table = "character_flags"
        ordering = ["-created_at"]
        constraints = [
            # Authenticated user can only flag a character once
            models.UniqueConstraint(
                fields=["reported_by", "character"],
                condition=Q(reported_by__isnull=False),
                name="unique_flag_per_user",
            ),
            # Guest can only flag a character once per session
            models.UniqueConstraint(
                fields=["guest_session_id", "character"],
                condition=Q(guest_session_id__isnull=False),
                name="unique_flag_per_guest",
            ),
        ]

    def __str__(self):
        if self.reported_by:
            who = self.reported_by.username
        else:
            guest_hint = self.guest_session_id[:8] if self.guest_session_id else "?"
            who = f"guest:{guest_hint}"  # noqa: E231
        return f"{who} flagged {self.character.name}"


class EmailChangeRequest(TimeStampedModel):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="email_change_requests")
    new_email_hash = models.CharField(max_length=64)
    new_email_encrypted = models.TextField()
    code = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "email_change_requests"
        indexes = [
            models.Index(fields=["user", "is_used"]),
            models.Index(fields=["expires_at"]),
        ]

    @classmethod
    def generate_code(cls, length=6) -> str:
        from capp.classes.auth.codes import generate_code

        return generate_code(length)

    def save(self, *args, **kwargs):
        from capp.classes.auth.codes import digest_code

        if isinstance(self.code, str) and len(self.code) == 6 and self.code.isascii() and self.code.isdigit():
            self.code = digest_code(self.code, self.user_id, "email_change")
        super().save(*args, **kwargs)

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_used(self):
        self.is_used = True
        self.save(update_fields=["is_used", "updated_at"])


class EmailVerification(TimeStampedModel):
    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        USED = "used", "Used"
        EXPIRED = "expired", "Expired"

    verification_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("capp.CustomUser", on_delete=models.CASCADE, related_name="email_verifications")
    code_hash = models.CharField(max_length=128)
    new_email_hash = models.CharField(max_length=64, blank=True, null=True)
    new_email_encrypted = models.TextField(blank=True, null=True)
    expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "email_verifications"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["expires_at"]),
        ]


class ComplianceConfirmationLog(TimeStampedModel):
    class ConfirmationType(models.TextChoices):
        AGE_GATE = "age_gate", "Age Gate"
        TERMS = "terms", "Terms"
        PRIVACY = "privacy", "Privacy"
        AGENT_GATEWAY = "agent_gateway", "Agent Gateway"

    compliance_confirmation_log_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="compliance_logs")
    confirmation_type = models.CharField(max_length=30, choices=ConfirmationType.choices)
    accepted = models.BooleanField(default=True)
    version = models.CharField(max_length=50)
    accepted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "compliance_confirmation_logs"
        ordering = ["-accepted_at"]
        indexes = [
            models.Index(fields=["user", "confirmation_type"]),
        ]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} - {self.confirmation_type} - {self.version}"


class Character(TimeStampedModel):
    class LanguageChoices(models.TextChoices):
        ENGLISH = "en", "English"
        SWAHILI = "sw", "Swahili"
        SHENG = "sh", "Sheng"
        MULTI = "multi", "Multilingual"

    class StatusChoices(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    class CategoryChoices(models.TextChoices):
        SUPPORTIVE = "supportive", "Supportive"
        FUN = "fun", "Fun"
        ROMANTIC = "romantic", "Romantic"

    # ---------- Gender ----------
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    # ---------- Wizard Step 1: Visual Style ----------
    class VisualStyle(models.TextChoices):
        REALISTIC = "realistic", "Realistic"
        ANIME = "anime", "Anime"
        CARTOON = "cartoon", "Cartoon"

    # ---------- Wizard Step 2: Skin Color ----------
    class SkinColor(models.TextChoices):
        FAIR = "fair", "Fair"
        LIGHT = "light", "Light"
        MEDIUM = "medium", "Medium"
        OLIVE = "olive", "Olive"
        TAN = "tan", "Tan"
        BROWN = "brown", "Brown"
        DARK = "dark", "Dark"
        EBONY = "ebony", "Ebony"

    # ---------- Wizard Step 3: Hairstyle & Hair Color ----------
    class Hairstyle(models.TextChoices):
        SHORT = "short", "Short"
        MEDIUM = "medium", "Medium"
        LONG = "long", "Long"
        CURLY = "curly", "Curly"
        WAVY = "wavy", "Wavy"
        STRAIGHT = "straight", "Straight"
        BALD = "bald", "Bald"
        BUZZ_CUT = "buzz_cut", "Buzz Cut"
        PONYTAIL = "ponytail", "Ponytail"
        BRAIDS = "braids", "Braids"
        DREADLOCKS = "dreadlocks", "Dreadlocks"
        AFRO = "afro", "Afro"
        BUN = "bun", "Bun"
        BANGS = "bangs", "Bangs"
        SHAVED = "shaved", "Shaved"

    class HairLength(models.TextChoices):
        BALD = "bald", "Bald"
        SHORT = "short", "Short"
        MEDIUM = "medium", "Medium"
        LONG = "long", "Long"

    class HairColor(models.TextChoices):
        BLACK = "black", "Black"
        BROWN = "brown", "Brown"
        BLONDE = "blonde", "Blonde"
        RED = "red", "Red"
        GRAY = "gray", "Gray"
        WHITE = "white", "White"
        BLUE = "blue", "Blue"
        PINK = "pink", "Pink"
        GREEN = "green", "Green"
        PURPLE = "purple", "Purple"

    class EyeColor(models.TextChoices):
        BROWN = "brown", "Brown"
        BLUE = "blue", "Blue"
        GREEN = "green", "Green"
        HAZEL = "hazel", "Hazel"
        GRAY = "gray", "Gray"
        AMBER = "amber", "Amber"
        BLACK = "black", "Black"

    # ---------- Wizard Step 4: Body Type (Common) ----------
    class BodyType(models.TextChoices):
        SLIM = "slim", "Slim"
        ATHLETIC = "athletic", "Athletic"
        AVERAGE = "average", "Average"
        CURVY = "curvy", "Curvy"
        MUSCULAR = "muscular", "Muscular"
        CHUBBY = "chubby", "Chubby"
        PETITE = "petite", "Petite"
        TALL = "tall", "Tall"

    # Gender‑specific (female)
    class BreastSize(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        LARGE = "large", "Large"
        EXTRA_LARGE = "extra_large", "Extra Large"
        NA = "na", "N/A"

    class ButtSize(models.TextChoices):
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        LARGE = "large", "Large"
        EXTRA_LARGE = "extra_large", "Extra Large"
        NA = "na", "N/A"

    # Gender‑specific (male)
    class ChestHair(models.TextChoices):
        NONE = "none", "None"
        LIGHT = "light", "Light"
        MEDIUM = "medium", "Medium"
        HEAVY = "heavy", "Heavy"
        NA = "na", "N/A"

    class MuscleDefinition(models.TextChoices):
        SLIM = "slim", "Slim"
        TONED = "toned", "Toned"
        MUSCULAR = "muscular", "Muscular"
        BULKY = "bulky", "Bulky"
        NA = "na", "N/A"

    class BeardStyle(models.TextChoices):
        CLEAN_SHAVEN = "clean_shaven", "Clean Shaven"
        STUBBLE = "stubble", "Stubble"
        FULL_BEARD = "full_beard", "Full Beard"
        GOATEE = "goatee", "Goatee"
        MUSTACHE = "mustache", "Mustache"
        NA = "na", "N/A"

    # ---------- Wizard Step 5: Personality ----------
    class Personality(models.TextChoices):
        WITTY = "witty", "Witty"
        EMPATHETIC = "empathetic", "Empathetic"
        ADVENTUROUS = "adventurous", "Adventurous"
        SHY = "shy", "Shy"
        CONFIDENT = "confident", "Confident"
        ROMANTIC = "romantic", "Romantic"
        FUNNY = "funny", "Funny"
        INTELLECTUAL = "intellectual", "Intellectual"
        CARING = "caring", "Caring"
        MYSTERIOUS = "mysterious", "Mysterious"
        FRIENDLY = "friendly", "Friendly"

    # ---------- Wizard Step 7: Relationship & Clothing ----------
    class RelationshipStyle(models.TextChoices):
        FRIEND = "friend", "Friend"
        BOYFRIEND = "boyfriend", "Boyfriend"
        GIRLFRIEND = "girlfriend", "Girlfriend"
        MENTOR = "mentor", "Mentor"
        COMPANION = "companion", "Companion"
        FLIRTY = "flirty", "Flirty"
        ROMANTIC = "romantic", "Romantic"

    class ClothingStyle(models.TextChoices):
        CASUAL = "casual", "Casual"
        FORMAL = "formal", "Formal"
        SPORTY = "sporty", "Sporty"
        ELEGANT = "elegant", "Elegant"
        STREETWEAR = "streetwear", "Streetwear"
        BOHEMIAN = "bohemian", "Bohemian"
        GOTHIC = "gothic", "Gothic"
        PREPPY = "preppy", "Preppy"
        LINGERIE = "lingerie", "Lingerie / Underwear"
        # Traditional Kenyan / East African attire. Values stay <= 20 chars
        # (the column width) and double as the prompt-builder lookup keys.
        KITENGE = "kitenge", "Kitenge"
        KANGA = "kanga", "Kanga"
        SWAHILI_COASTAL = "swahili_coastal", "Swahili Coastal"
        MAASAI = "maasai", "Maasai Shuka"
        GOMESI = "gomesi", "Gomesi (Uganda)"
        HABESHA = "habesha", "Habesha Kemis (Ethiopia / Eritrea)"

    class AvatarSource(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded by Admin"
        AI_GENERATED = "ai_generated", "AI Generated"

    # ---------- Core Fields (Existing) ----------
    character_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=150)
    gender = models.CharField(
        max_length=20,
        choices=Gender.choices,
        default=Gender.FEMALE,
    )
    slug = models.SlugField(max_length=180, unique=True, blank=True)

    avatar = models.ImageField(upload_to="characters/avatars/")

    short_description = models.CharField(max_length=255)
    mood = models.CharField(max_length=80)
    category = models.CharField(
        max_length=30,
        choices=CategoryChoices.choices,
        default=CategoryChoices.SUPPORTIVE,
    )
    intro = models.CharField(max_length=255)

    personality_description = models.TextField()
    prompt_template = models.TextField(
        default=(
            "You are {name}, a {gender}, {occupation} at age {age}. "
            "Your personality is {personality} and your tone is {tone_style}.\n"
            "Your hobbies are {hobbies}.\n"
            "Your appearance: {skin_color} skin, {eye_color} eyes, "
            "{hair_length} {hair_color} {hairstyle} hair, "
            "{body_type} body type{appearance_extras}.\n"
            "Your detailed background story is: {personality_description}\n"
            "{system_instructions}\n"
        )
    )
    system_instructions = models.TextField(blank=True, null=True)
    greeting_message = models.TextField()
    tone_style = models.CharField(max_length=100)

    primary_language = models.CharField(
        max_length=10,
        choices=LanguageChoices.choices,
        default=LanguageChoices.MULTI,
    )

    is_active = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True)
    nsfw_enabled = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.PENDING,
    )

    created_by = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        related_name="created_characters",
        blank=True,
        null=True,
    )
    reviewed_by = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        related_name="reviewed_characters",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    cashback_awarded = models.BooleanField(default=False)

    # ---------- NEW FIELDS (Wizard Steps) ----------
    # Location
    country = models.CharField(max_length=100, default="Kenya", blank=True)
    town = models.CharField(max_length=100, default="Nairobi", blank=True)
    timezone = models.CharField(
        max_length=50,
        default="Africa/Nairobi",
        blank=True,
        null=True,
        help_text="IANA timezone name (auto‑populated from country if not set)",
    )

    # Wizard Step 1
    visual_style = models.CharField(max_length=20, choices=VisualStyle.choices, default=VisualStyle.REALISTIC)

    # Wizard Step 2
    age = models.PositiveSmallIntegerField(default=25)
    skin_color = models.CharField(max_length=20, choices=SkinColor.choices, default=SkinColor.MEDIUM)
    eye_color = models.CharField(max_length=20, choices=EyeColor.choices, default=EyeColor.BROWN, blank=True)

    # Wizard Step 3
    hairstyle = models.CharField(max_length=30, choices=Hairstyle.choices, default=Hairstyle.MEDIUM)
    hair_length = models.CharField(max_length=20, choices=HairLength.choices, default=HairLength.MEDIUM, blank=True)
    hair_color = models.CharField(max_length=20, choices=HairColor.choices, default=HairColor.BROWN)

    # Wizard Step 4 (common)
    body_type = models.CharField(max_length=20, choices=BodyType.choices, default=BodyType.AVERAGE)

    # Gender‑specific (all nullable with 'na' default)
    breast_size = models.CharField(
        max_length=20,
        choices=BreastSize.choices,
        blank=True,
        null=True,
        default=BreastSize.NA,
    )
    butt_size = models.CharField(
        max_length=20,
        choices=ButtSize.choices,
        blank=True,
        null=True,
        default=ButtSize.NA,
    )
    chest_hair = models.CharField(
        max_length=20,
        choices=ChestHair.choices,
        blank=True,
        null=True,
        default=ChestHair.NA,
    )
    muscle_definition = models.CharField(
        max_length=20,
        choices=MuscleDefinition.choices,
        blank=True,
        null=True,
        default=MuscleDefinition.NA,
    )
    beard_style = models.CharField(
        max_length=20,
        choices=BeardStyle.choices,
        blank=True,
        null=True,
        default=BeardStyle.NA,
    )

    # Wizard Step 5
    personalities = models.ManyToManyField("Personality", blank=True, related_name="characters")

    # Wizard Step 6
    occupation = models.ForeignKey(
        "Occupation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="characters",
    )
    hobbies = models.ManyToManyField("Hobby", blank=True, related_name="characters")

    # Wizard Step 7
    relationship_style = models.CharField(
        max_length=20,
        choices=RelationshipStyle.choices,
        default=RelationshipStyle.COMPANION,
    )
    clothing_style = models.CharField(max_length=20, choices=ClothingStyle.choices, default=ClothingStyle.CASUAL)

    # Avatar source tracking
    avatar_source = models.CharField(
        max_length=20,
        choices=AvatarSource.choices,
        default=AvatarSource.AI_GENERATED,
        blank=True,
    )

    # Operation ID of the paid creation that produced this character, so a
    # replayed create request returns the row the first attempt wrote instead
    # of generating a second persona and billing for it.
    client_operation_id = models.CharField(max_length=300, blank=True, null=True)

    class Meta:
        db_table = "characters"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["created_by", "client_operation_id"],
                condition=Q(client_operation_id__isnull=False),
                name="unique_character_per_creator_operation",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["is_featured"]),
            models.Index(fields=["primary_language"]),
            models.Index(fields=["category"]),
            models.Index(fields=["created_by", "status"]),
            # New indexes
            models.Index(fields=["visual_style"]),
            models.Index(fields=["country"]),
        ]

    def __str__(self):
        creator = self.created_by.get_display_name() if self.created_by else "System"
        return f"{self.name} - {self.status} - {creator}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug(self.name, self.character_id)
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        update_fields = kwargs.get("update_fields")
        pending_avatar = (
            self.avatar
            if (update_fields is None or "avatar" in update_fields) and self.avatar and not self.avatar._committed
            else None
        )
        # Avatar replacement queues erasure in pre_save. Commit that job only
        # together with the replacement row, never before the new save succeeds.
        try:
            with transaction.atomic(using=using):
                super().save(*args, **kwargs)
        except BaseException:
            # FileField can finish an upload before the SQL write fails. Queue
            # its actual storage key outside the failed savepoint, not the old
            # avatar whose replacement (and deletion job) was rolled back.
            if pending_avatar is not None and pending_avatar._committed:
                try:
                    delete_file_after_commit(pending_avatar, using)
                except Exception:
                    logging.getLogger(__name__).error("Could not enqueue failed avatar upload cleanup")
            raise

    @classmethod
    def _generate_unique_slug(cls, name: str, character_id=None) -> str:
        base = slugify(name)[:160] or "character"
        if character_id:
            # Example: "amina-ef0088095d6e41fd"
            short_id = character_id.hex[:12]  # or use str(uuid)[:8]
            return f"{base}-{short_id}"
        # fallback for pre‑save when instance has no pk yet
        return f"{base}-{uuid.uuid4().hex[:8]}"


class ProactiveChatPreference(TimeStampedModel):
    """
    User preference for proactive (interactive) chat per character.
    When enabled, the character will send a contextual message after the specified interval
    from the last message in any conversation with that character.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="proactive_chat_preferences")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="proactive_chat_preferences")
    enabled = models.BooleanField(default=False)
    interval_hours = models.PositiveIntegerField(default=24, validators=[MinValueValidator(6), MaxValueValidator(168)])
    consecutive_proactive_without_reply = models.PositiveIntegerField(default=0)
    last_proactive_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "proactive_chat_preferences"
        unique_together = ("user", "character")
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "enabled"]),
            models.Index(fields=["character", "enabled"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.character.name} ({'enabled' if self.enabled else 'disabled'})"


class Reminder(TimeStampedModel):
    """
    A one-off, user-requested reminder ("remind me to call Paul tomorrow
    evening"), extracted via the create_reminder tool call in
    capp/classes/reminders/tool_loop.py.

    Deliberately independent of ProactiveChatPreference: a reminder is
    something the user explicitly asked for in this exact conversation turn,
    not an unprompted re-engagement nudge, so it must still fire even if the
    user has proactive/re-engagement messages switched off for this
    character. Never gate delivery on ProactiveChatPreference.enabled.
    """

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        CANCELLED = "cancelled", "Cancelled"
        FAILED_INSUFFICIENT_SPARKS = "failed_insufficient_sparks", "Failed - insufficient sparks"
        FAILED_ERROR = "failed_error", "Failed - error"

    reminder_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="reminders")
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="reminders")
    conversation = models.ForeignKey("Conversation", on_delete=models.CASCADE, related_name="reminders")
    source_message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
        help_text="The user message this reminder was extracted from, for audit/debugging.",
    )

    reminder_text = EncryptedTextField(help_text="What to remind the user about, e.g. 'call Paul'.")

    due_at = models.DateTimeField(help_text="Absolute UTC time the reminder should fire.")
    timezone_snapshot = models.CharField(
        max_length=50,
        help_text="IANA timezone used to resolve the user's relative request at creation time.",
    )

    status = models.CharField(max_length=30, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    # Every edit invalidates an in-flight delivery snapshot.
    revision = models.PositiveIntegerField(default=1)
    sent_at = models.DateTimeField(blank=True, null=True)
    failure_reason = models.TextField(blank=True, null=True)
    notice_sent = models.BooleanField(
        default=False,
        help_text=(
            "Whether the user has already been sent a zero-cost 'still retrying' "
            "system notice for this reminder. Guards against re-notifying on every "
            "5-minute retry tick during the grace period -- the notice should fire "
            "at most once per reminder, not once per failed attempt."
        ),
    )

    delivery_message = models.ForeignKey(
        "Message",
        on_delete=models.SET_NULL,
        related_name="+",
        blank=True,
        null=True,
        help_text="The AI message actually delivered when this reminder fired.",
    )

    class Meta:
        db_table = "reminders"
        ordering = ["due_at"]
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"Reminder({self.user.username} @ {self.due_at.isoformat()}: {self.reminder_text[:40]!r})"


class ReminderDeliveryAttempt(TimeStampedModel):
    """Durable recovery record, deliberately not cascade-linked to a schedule."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REFUND_PENDING = "refund_pending", "Refund pending"
        REFUNDED = "refunded", "Closed without delivery"
        DELIVERED = "delivered", "Delivered"

    attempt_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reminder_key = models.UUIDField(db_index=True)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="+")
    revision = models.PositiveIntegerField()
    lease_until = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    reservation = models.OneToOneField(
        "BillingReservation", on_delete=models.PROTECT, null=True, blank=True, related_name="reminder_attempt"
    )

    class Meta:
        db_table = "reminder_delivery_attempts"
        constraints = [
            models.UniqueConstraint(
                fields=["reminder_key"], condition=Q(status="active"), name="unique_active_reminder_attempt"
            ),
        ]


class ReminderToolExecution(TimeStampedModel):
    """One logical mutation per source turn/family, independent of provider IDs.

    Source UUID is not a foreign key: deleting the source or reminder must not
    permit a delayed task to repeat a previously acknowledged side effect.
    Results include private reminder text and are encrypted just like Messages.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="+")
    source_message_key = models.UUIDField()
    family = models.CharField(max_length=20)
    tool_name = models.CharField(max_length=40)
    result = EncryptedTextField()

    class Meta:
        db_table = "reminder_tool_executions"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source_message_key", "family"], name="unique_reminder_tool_execution"
            ),
        ]


# ============================================================
# SUPPORT / TICKET SYSTEM
# ============================================================
class SupportTicket(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    ticket_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(help_text="Initial message from the user")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)

    created_by = models.ForeignKey("CustomUser", on_delete=models.CASCADE, related_name="support_tickets")
    assigned_to = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_support_tickets",
    )
    metadata = models.JSONField(default=dict, blank=True)

    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "support_tickets"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["created_by", "status"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class TicketMessage(TimeStampedModel):
    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("CustomUser", on_delete=models.CASCADE, related_name="support_messages")
    content = models.TextField()
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies")
    # Optional: mark as internal admin note (not visible to user)
    is_internal = models.BooleanField(
        default=False,
        help_text="If True, only admins can see this message (private note).",
    )

    class Meta:
        db_table = "ticket_messages"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["ticket", "created_at"]),
        ]

    def __str__(self):
        return f"Message by {self.sender.username} on {self.ticket.title[:20]}"


class TicketView(TimeStampedModel):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="ticket_views")
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="views")
    last_viewed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ticket_views"
        unique_together = ("user", "ticket")
        ordering = ["-last_viewed_at"]
        indexes = [
            models.Index(fields=["user", "last_viewed_at"]),
            models.Index(fields=["ticket", "last_viewed_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} viewed {self.ticket.title[:20]} at {self.last_viewed_at}"


class Feedback(TimeStampedModel):
    feedback_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback",
    )
    message = models.TextField()
    rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    is_read = models.BooleanField(default=False)
    read_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="read_feedback",
    )
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "feedback"
        ordering = ["-created_at"]

    def __str__(self):
        user_display = self.user.get_display_name() if self.user else "Guest"
        return f"Feedback from {user_display} ({self.created_at.date()})"


class Personality(TimeStampedModel):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "personalities"
        ordering = ["name"]
        verbose_name_plural = "Personalities"

    def __str__(self):
        return self.name


class Occupation(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "occupations"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Hobby(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "hobbies"
        ordering = ["name"]
        verbose_name_plural = "Hobbies"

    def __str__(self):
        return self.name


class Trait(TimeStampedModel):
    """Optional: if you want a flexible trait system separate from personality."""

    name = models.CharField(max_length=50, unique=True)
    category = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "traits"
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class CharacterGalleryImage(TimeStampedModel):
    class VisibilityChoices(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="gallery_images")
    image = models.ImageField(upload_to="characters/gallery/")
    caption = models.CharField(max_length=200, blank=True)
    order = models.PositiveIntegerField(default=0)
    source = models.CharField(
        max_length=20,
        choices=Character.AvatarSource.choices,
        default=Character.AvatarSource.AI_GENERATED,
        blank=True,
    )

    # Operation ID of the paid generation that produced this image, so a Celery
    # redelivery finds the picture the first attempt already stored instead of
    # generating and billing a second one.
    client_operation_id = models.CharField(max_length=300, blank=True, null=True)

    # ---- NEW FIELDS ----
    created_by = models.ForeignKey(
        "CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_gallery_images",
    )
    visibility = models.CharField(
        max_length=10,
        choices=VisibilityChoices.choices,
        default=VisibilityChoices.PUBLIC,
    )
    # Set on images the owner generated for the character's own public gallery
    # (creation-setup and edit pages) while the character was not yet approved.
    # They stay PRIVATE until an admin approves the character, at which point
    # they are published and this flag is cleared. Chat and detail-page images
    # never carry it, so they stay private for good.
    # db_default keeps a database-level DEFAULT on the column: during a rolling
    # deploy, processes still running the previous release insert gallery rows
    # without this column, and NOT NULL without a default would reject them.
    publish_on_approval = models.BooleanField(default=False, db_default=False)

    class Meta:
        db_table = "character_gallery_images"
        ordering = ["order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["character", "client_operation_id"],
                condition=Q(client_operation_id__isnull=False),
                name="unique_gallery_image_per_character_operation",
            ),
        ]

    def __str__(self):
        return f"Gallery image for {self.character.name} (#{self.order}) [{self.visibility}]"


class Conversation(TimeStampedModel):
    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"
        CLOSED = "closed", "Closed"

    class ReasoningEffort(models.TextChoices):
        # "Fast" preserves current behavior exactly (no reasoning/thinking params
        # sent to the provider). "Deep" opts into provider-supported reasoning
        # effort where verified against current official docs -- see
        # capp/classes/ai/reasoning.py for the per-provider mapping and doc citations.
        FAST = "fast", "Fast"
        DEEP = "deep", "Deep"

    conversation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
    )
    guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    # Immutable provenance: the guest that originally owned this conversation.
    # Set once when an account claims the guest and never cleared, so guest-origin
    # history stays attributable after ``guest_session_id`` is released.
    origin_guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    claimed_from_guest_at = models.DateTimeField(blank=True, null=True)
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="conversations")

    title = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.ACTIVE)
    preferred_language = models.CharField(
        max_length=5,
        choices=CustomUser.LanguageChoices.choices,
        blank=True,
        null=True,
    )

    started_at = models.DateTimeField(default=timezone.now)
    last_message_at = models.DateTimeField(blank=True, null=True)
    last_summarized_at = models.DateTimeField(blank=True, null=True)
    # Persisted fences survive summary deletion. Writers lock the owning user
    # before comparing/updating these values across the relevant conversations.
    memory_generation = models.PositiveBigIntegerField(default=0)
    summary_generation = models.PositiveBigIntegerField(default=0)
    summary_source_cutoff = models.DateTimeField(blank=True, null=True)
    # Read watermark for the single owner of this conversation. Unread is
    # derived from it rather than stored as a counter so that deleting or
    # resetting messages can never leave a badge stuck on a stale number.
    last_read_at = models.DateTimeField(default=timezone.now)

    # --- Per-chat toggles ---------------------------------------------------
    # Web search: when enabled, the reply pipeline may offer/execute a search
    # tool (see capp/classes/search/). Default False: current behavior unchanged.
    web_search_enabled = models.BooleanField(default=False)
    # Reasoning/thinking effort: "fast" (default, unchanged behavior) or "deep"
    # (opt-in; mapped to provider-specific reasoning params where documented
    # support exists -- see capp/classes/ai/reasoning.py).
    reasoning_effort = models.CharField(
        max_length=10,
        choices=ReasoningEffort.choices,
        default=ReasoningEffort.FAST,
    )

    class Meta:
        db_table = "conversations"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["character", "status"]),
        ]

    def __str__(self):
        if self.user:
            name = self.user.get_display_name()
        else:
            name = f"Guest ({self.guest_session_id[:8]}…)"
        return f"{name} - {self.character.name}"


class Message(TimeStampedModel):
    class SenderType(models.TextChoices):
        USER = "user", "User"
        AI = "ai", "AI"
        SYSTEM = "system", "System"

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        STREAMING = "streaming", "Streaming"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked"

    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender_type = models.CharField(max_length=20, choices=SenderType.choices)
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="replies",
        blank=True,
        null=True,
    )

    content = EncryptedTextField()
    language_code = models.CharField(max_length=10, blank=True, null=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.SENT)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_estimate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    provider_message_id = models.CharField(max_length=255, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    # Operation ID of the paid action that produced this row (see
    # capp.classes.billing.idempotency). The user's request carries the bare
    # key and the AI reply carries its ``#result`` form, so a replayed request
    # or a redelivered Celery task can find what the first attempt already
    # wrote instead of generating and billing a second reply. Also the audit
    # link back to the BillingReservation, which shares the bare key.
    client_operation_id = models.CharField(max_length=300, blank=True, null=True)

    class Meta:
        db_table = "messages"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["sender_type", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_operation_id"],
                condition=Q(client_operation_id__isnull=False),
                name="unique_message_per_conversation_operation",
            ),
        ]

    def __str__(self):
        return f"{self.sender_type} - {self.conversation.conversation_id}"


class ConversationSummary(TimeStampedModel):
    class SummaryType(models.TextChoices):
        ROLLING = "rolling", "Rolling"
        SNAPSHOT = "snapshot", "Snapshot"

    summary_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="summaries")
    summary_text = EncryptedTextField()  # encrypted at rest with MESSAGE_ENCRYPTION_KEY
    summary_type = models.CharField(
        max_length=20,
        choices=SummaryType.choices,
        default=SummaryType.ROLLING,
    )
    covered_until_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="summary_checkpoints",
        blank=True,
        null=True,
    )
    is_current = models.BooleanField(default=True)

    class Meta:
        db_table = "conversation_summaries"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["conversation", "is_current"]),
        ]

    def __str__(self):
        return f"Summary - {self.conversation.conversation_id}"


class MemoryFact(TimeStampedModel):
    class FactType(models.TextChoices):
        PERSONAL = "personal", "Personal"
        PREFERENCE = "preference", "Preference"
        GOAL = "goal", "Goal"
        CONTEXT = "context", "Context"
        OTHER = "other", "Other"

    memory_fact_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="memory_facts")
    character = models.ForeignKey(
        Character,
        on_delete=models.SET_NULL,
        related_name="memory_facts",
        blank=True,
        null=True,
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        related_name="memory_facts",
        blank=True,
        null=True,
    )
    source_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="extracted_memory_facts",
        blank=True,
        null=True,
    )

    fact_text = EncryptedTextField()  # encrypted at rest with MESSAGE_ENCRYPTION_KEY
    # Keyed digest for exact deduplication without querying encrypted text.
    # Legacy rows are matched lazily; no automatic-memory storage cap.
    content_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    fact_type = models.CharField(max_length=30, choices=FactType.choices, default=FactType.OTHER)

    confidence_score = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.50"),
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    importance_score = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    last_used_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    extracted_at = models.DateTimeField(default=timezone.now)

    # ----- Temporal fields (Phase 1: bi-temporal facts) -----
    # Event time: when the stated fact became true in the user's world
    # (resolved from relative expressions at extraction time).
    valid_from = models.DateTimeField(blank=True, null=True)
    # System time: when this fact stopped being the current truth, set when
    # a newer fact supersedes it. NULL = currently believed true.
    valid_to = models.DateTimeField(blank=True, null=True)
    # Link to the newer fact that replaced this one (append-only history).
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="supersedes",
        blank=True,
        null=True,
    )

    # ----- pgvector fields -----
    embedding = VectorField(dimensions=1024, null=True, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "memory_facts"
        ordering = ["-importance_score", "-extracted_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["character", "is_active"]),
            # Name omitted on purpose: a custom name desyncs makemigrations
            # (Django renames long auto-hashed indexes); default keeps them in sync.
            models.Index(fields=["user", "valid_to"]),
        ]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} - {self.fact_type}"


class MemoryEntity(TimeStampedModel):
    """
    A canonical person, place, organisation, or thing known about a user.

    Entities are the nodes of the memory graph: extraction canonicalizes
    mentions ("mpenzi wangu", "my girlfriend", "Zawadi") into one node so
    relations accumulate on a single identity across conversations.
    Entities are user-global; per-character scoping lives on MemoryRelation.
    """

    class EntityType(models.TextChoices):
        PERSON = "person", "Person"
        PLACE = "place", "Place"
        ORG = "org", "Organisation"
        THING = "thing", "Thing"
        OTHER = "other", "Other"

    memory_entity_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="memory_entities")

    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=120)
    entity_type = models.CharField(max_length=20, choices=EntityType.choices, default=EntityType.OTHER)

    # Optional semantic fallback for entity matching when the name lookup misses.
    embedding = VectorField(dimensions=1024, null=True, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "memory_entities"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "normalized_name"],
                name="uniq_memory_entity_user_name",
            ),
        ]
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.entity_type})"

    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Lowercase + whitespace-collapse; the cheap half of entity resolution."""
        return " ".join((name or "").strip().lower().split())


class MemoryRelation(TimeStampedModel):
    """
    A bi-temporal edge in the memory graph: (subject, predicate, object).

    - valid_from / valid_to: event time — when the relationship held in the
      user's world ("moved to Nairobi on 2026-03-01").
    - invalidated_at: system time — when we stopped believing it, i.e. a
      newer relation superseded it. NULL = currently believed true.
    """

    memory_relation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="memory_relations")
    # NULL character = user-global relation visible to all characters.
    character = models.ForeignKey(
        Character,
        on_delete=models.SET_NULL,
        related_name="memory_relations",
        blank=True,
        null=True,
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        related_name="memory_relations",
        blank=True,
        null=True,
    )

    subject = models.ForeignKey(MemoryEntity, on_delete=models.CASCADE, related_name="out_relations")
    predicate = models.CharField(max_length=60)  # snake_case: lives_in, works_at, sibling_of, ...
    object_entity = models.ForeignKey(
        MemoryEntity,
        on_delete=models.CASCADE,
        related_name="in_relations",
        blank=True,
        null=True,
    )
    # Free-text object for values that are not entities ("works as a nurse").
    object_text = EncryptedTextField(blank=True, null=True)

    confidence_score = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.50"),
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )

    # Bi-temporal validity
    valid_from = models.DateTimeField(blank=True, null=True)
    valid_to = models.DateTimeField(blank=True, null=True)
    extracted_at = models.DateTimeField(default=timezone.now)
    invalidated_at = models.DateTimeField(blank=True, null=True)
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="supersedes",
        blank=True,
        null=True,
    )

    # Embedded triple text ("Nino lives in Frankfurt") for hybrid retrieval.
    embedding = VectorField(dimensions=1024, null=True, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "memory_relations"
        indexes = [
            models.Index(fields=["user", "invalidated_at"]),
            models.Index(fields=["subject", "invalidated_at"]),
            models.Index(fields=["object_entity", "invalidated_at"]),
        ]

    def __str__(self):
        obj = self.object_entity.name if self.object_entity_id else (self.object_text or "?")
        return f"{self.subject.name} -{self.predicate}-> {obj}"


class RelationshipState(TimeStampedModel):
    class ToneChoices(models.TextChoices):
        NEUTRAL = "neutral", "Neutral"
        WARM = "warm", "Warm"
        PLAYFUL = "playful", "Playful"
        FLIRTY = "flirty", "Flirty"
        DISTANT = "distant", "Distant"

    relationship_state_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="relationship_states",
        null=True,  # ← ADD
        blank=True,  # ← ADD
    )
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name="relationship_states")

    familiarity_score = models.PositiveIntegerField(default=0)
    affinity_score = models.IntegerField(default=0)
    trust_score = models.IntegerField(default=0)
    interaction_count = models.PositiveIntegerField(default=0)
    tone_state = models.CharField(
        max_length=20,
        choices=ToneChoices.choices,
        default=ToneChoices.NEUTRAL,
    )
    relationship_notes = models.TextField(blank=True, null=True)
    last_interaction_at = models.DateTimeField(blank=True, null=True)
    # Idempotency guard for update_relationship_state Celery task retries.
    # See RelationshipService.update_relationship_state.
    last_processed_ai_message_id = models.UUIDField(blank=True, null=True)

    class Meta:
        db_table = "relationship_states"
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["user", "character"], name="unique_user_character_relationship")]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} - {self.character.name}"


class TokenUsageLog(TimeStampedModel):
    class UsageType(models.TextChoices):
        CHAT = "chat", "Chat"
        SUMMARY = "summary", "Summary"
        MEMORY_EXTRACTION = "memory_extraction", "Memory Extraction"
        MODERATION = "moderation", "Moderation"
        OTHER = "other", "Other"

    token_usage_log_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="token_usage_logs")
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        related_name="token_usage_logs",
        blank=True,
        null=True,
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="token_usage_logs",
        blank=True,
        null=True,
    )
    character = models.ForeignKey(
        Character,
        on_delete=models.SET_NULL,
        related_name="token_usage_logs",
        blank=True,
        null=True,
    )

    usage_type = models.CharField(max_length=30, choices=UsageType.choices, default=UsageType.CHAT)
    provider = models.CharField(max_length=50, default="mistral")
    model_name = models.CharField(max_length=100)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "token_usage_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["usage_type", "created_at"]),
        ]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} - {self.total_tokens}"


class GuestIdentity(TimeStampedModel):
    """
    Durable server-side record of an anonymous guest.

    ``guest_id`` is the *stable ownership ID*: every guest-owned row across the
    schema (``Conversation.guest_session_id``, ``GuestTokenQuota.session_id``,
    ``UsageCharge.guest_session_id``, ``PaystackTransaction.guest_session_id``, …)
    is keyed on its string form. It is never sent to the browser.

    The browser holds a separate high-entropy bearer credential in an HttpOnly
    cookie; only its SHA-256 hash is stored here. Ownership is therefore only
    derivable by presenting the cookie, and rotating the credential never
    re-keys any guest-owned data.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLAIMED = "claimed", "Claimed"
        REVOKED = "revoked", "Revoked"

    guest_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    credential_hash = models.CharField(max_length=64, unique=True, db_index=True)
    previous_credential_hash = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    expires_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(default=timezone.now)

    claimed_at = models.DateTimeField(blank=True, null=True)
    claimed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="claimed_guest_identities",
        blank=True,
        null=True,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)

    rotated_at = models.DateTimeField(blank=True, null=True)
    rotation_count = models.PositiveIntegerField(default=0)

    # Set only for identities created by the one-time legacy localStorage
    # migration endpoint. Retained permanently for audit.
    migrated_from_legacy_session_id = models.CharField(max_length=64, blank=True, null=True)
    migrated_at = models.DateTimeField(blank=True, null=True)

    # Audience analytics: recorded site visits for this guest, written only by
    # the /api/v1/analytics/visit/ endpoint (see
    # capp/classes/analytics/visit_service.py). ``last_seen_at`` above is
    # refreshed by ANY guest-credential API call, so it stays the broader
    # "last activity" signal; these two fields mean specifically "the public
    # app was opened". When a guest is claimed, first_visit_at is carried over
    # to the account (see GuestClaimService) so the person's first recorded
    # visit survives the merge.
    first_visit_at = models.DateTimeField(blank=True, null=True)
    last_visit_at = models.DateTimeField(blank=True, null=True)
    # Approximate country of the latest recorded visit, from the trusted edge's
    # country header. Country level only. The guest's IP is separately retained
    # on GuestTokenQuota for abuse control; this field is what the admin
    # audience view reads.
    visit_country_code = models.CharField(max_length=2, blank=True, null=True)

    class Meta:
        db_table = "guest_identities"
        verbose_name_plural = "Guest identities"
        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["claimed_by", "claimed_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["migrated_from_legacy_session_id"],
                condition=Q(migrated_from_legacy_session_id__isnull=False),
                name="unique_legacy_guest_session_migration",
            ),
            models.CheckConstraint(
                condition=Q(status="claimed", claimed_at__isnull=False) | ~Q(status="claimed"),
                name="claimed_guest_identity_has_claimed_at",
            ),
        ]

    def __str__(self):
        return f"GuestIdentity {self.guest_id} ({self.status})"

    @property
    def owner_key(self) -> str:
        """The value stored in every ``guest_session_id`` column for this guest."""
        return str(self.guest_id)

    def is_usable(self, now=None) -> bool:
        now = now or timezone.now()
        return self.status == self.Status.ACTIVE and self.expires_at > now


class GuestTransferGrant(TimeStampedModel):
    """
    The single server-side authority for moving a guest's state into an account.

    A row here is created only by ``POST /api/v1/auth/signup/``, in the same
    transaction as the account itself, and only from the ``GuestIdentity`` the
    server resolved from the request's HttpOnly credential cookie. Nothing a
    client can say — a body field, a header, a guest ID it guessed — can cause
    one to exist. The claim path transfers state if and only if it finds a
    ``PENDING`` row matching *both* the authenticated user and the identity
    resolved from that same cookie, so signing in to an account that did not
    create this grant moves nothing.

    The two unique constraints are the load-bearing part:

    * one grant per guest identity, so a second signup cannot ask for state that
      a first signup is already entitled to, and
    * one grant per user, so an account cannot accumulate entitlements to several
      guest sessions.

    ``status`` is flipped to ``CONSUMED`` by a conditional update inside the
    transfer transaction, which is what makes a concurrent double-claim resolve
    to exactly one winner and a rollback restore the entitlement.

    The entitlement belongs to the guest session, not to whichever account first
    asked for it. An abandoned signup — an account created and never verified —
    must therefore not hold a guest's Sparks hostage: a ``PENDING`` grant whose
    holder has not verified their email is *moved* to the account of the next
    signup from the same identity, and ``previous_user`` / ``reassigned_at``
    record the handover. The row is reused rather than replaced, so "one grant
    per guest identity" stays a database constraint instead of a convention. A
    grant is never moved once its holder is verified or once it is spent.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONSUMED = "consumed", "Consumed"
        REVOKED = "revoked", "Revoked"

    grant_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    guest_identity = models.OneToOneField(
        GuestIdentity,
        on_delete=models.CASCADE,
        related_name="transfer_grant",
    )
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="guest_transfer_grant",
    )

    # The unverified account this entitlement was taken away from, if any. Kept
    # so an abandoned signup that a later one displaced is still auditable, and
    # so support can see that a guest retried rather than that data vanished.
    previous_user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="surrendered_guest_transfer_grants",
        blank=True,
        null=True,
    )
    reassigned_at = models.DateTimeField(blank=True, null=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    consumed_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    # Snapshot of the ownership ID at signup, kept even if the identity row is
    # later removed, so an audit can still say which guest an account absorbed.
    origin_guest_session_id = models.CharField(max_length=64, db_index=True)

    class Meta:
        db_table = "guest_transfer_grants"
        indexes = [
            models.Index(fields=["user", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="consumed", consumed_at__isnull=False) | ~Q(status="consumed"),
                name="consumed_guest_transfer_grant_has_consumed_at",
            ),
            models.CheckConstraint(
                condition=Q(previous_user__isnull=True) | Q(reassigned_at__isnull=False),
                name="reassigned_guest_transfer_grant_has_reassigned_at",
            ),
        ]

    def __str__(self):
        return f"GuestTransferGrant {self.grant_id} ({self.status})"


class GuestWebSocketTicket(TimeStampedModel):
    """
    Short-lived, single-use ticket that lets a guest open a WebSocket without
    putting its long-lived cookie credential in the URL.

    Only the SHA-256 hash of the ticket is stored. Each ticket is bound to one
    guest identity and one channel (plus one conversation for chat), and is
    consumed atomically on first use.
    """

    class Channel(models.TextChoices):
        CHAT = "chat", "Chat"
        SPARK_POOL = "spark_pool", "Spark pool"

    ticket_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guest_identity = models.ForeignKey(
        GuestIdentity,
        on_delete=models.CASCADE,
        related_name="websocket_tickets",
    )
    ticket_hash = models.CharField(max_length=64, unique=True, db_index=True)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    conversation_id = models.UUIDField(blank=True, null=True)

    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "guest_websocket_tickets"
        indexes = [
            models.Index(fields=["expires_at", "consumed_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(channel="chat", conversation_id__isnull=False) | ~Q(channel="chat"),
                name="chat_guest_ws_ticket_has_conversation",
            ),
        ]

    def __str__(self):
        return f"GuestWebSocketTicket {self.ticket_id} ({self.channel})"


class GuestTokenQuota(TimeStampedModel):
    """
    Tracks token usage for anonymous (guest) users identified by a session ID.
    Each session has a lifetime and daily cap to prevent abuse.
    """

    session_id = models.CharField(max_length=64, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)

    lifetime_tokens_used = models.PositiveBigIntegerField(default=0)
    daily_tokens_used = models.PositiveBigIntegerField(default=0)
    daily_reset_at = models.DateTimeField(default=timezone.now)
    sparks = models.PositiveBigIntegerField(default=0)

    # Set when the guest is claimed by an account. The row is deliberately kept
    # (never deleted) so guest usage history stays auditable; the spark balance
    # is moved to the account's SparkPool and zeroed here so it cannot be spent
    # twice.
    claimed_at = models.DateTimeField(blank=True, null=True)
    claimed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="claimed_guest_quotas",
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "guest_token_quotas"
        verbose_name_plural = "Guest token quotas"
        constraints = [
            models.CheckConstraint(condition=Q(sparks__gte=0), name="guest_quota_sparks_non_negative"),
        ]

    def __str__(self):
        return f"Guest {self.session_id[:12]}… – daily {self.daily_tokens_used} / lifetime {self.lifetime_tokens_used}"

    @classmethod
    def reset_daily_if_needed(cls, quota):
        """Reset daily usage if the reset time has passed."""
        now = timezone.now()
        if quota.daily_reset_at and now >= quota.daily_reset_at:
            quota.daily_tokens_used = 0
            quota.daily_reset_at = now + timedelta(hours=24)
            quota.save(update_fields=["daily_tokens_used", "daily_reset_at"])

    @classmethod
    def get_or_create_quota(cls, session_id, ip_address=None, user_agent=None):
        quota, created = cls.objects.get_or_create(
            session_id=session_id,
            defaults={
                "ip_address": ip_address,
                "user_agent": user_agent,
                "daily_reset_at": timezone.now() + timedelta(hours=24),
                "sparks": GUEST_INITIAL_SPARKS,  # <-- initial free sparks for new guests
            },
        )
        # Always update IP/UA if they are provided (even if unchanged)
        update_fields = []
        if ip_address is not None and quota.ip_address != ip_address:
            quota.ip_address = ip_address
            update_fields.append("ip_address")
        if user_agent is not None and quota.user_agent != user_agent:
            quota.user_agent = user_agent
            update_fields.append("user_agent")
        if update_fields:
            quota.save(update_fields=update_fields + ["updated_at"])
        cls.reset_daily_if_needed(quota)
        return quota


class SubscriptionPlan(TimeStampedModel):
    class BillingCycle(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"
        DAILY = "daily", "Daily"

    subscription_plan_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True, null=True)

    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    billing_cycle = models.CharField(max_length=20, choices=BillingCycle.choices)

    daily_token_limit = models.PositiveBigIntegerField(default=0)
    total_token_limit = models.PositiveBigIntegerField(default=0)

    included_sparks = models.PositiveIntegerField(default=0)
    max_tokens_per_request = models.PositiveIntegerField(default=0)

    # In SubscriptionPlan, after included_sparks = models.PositiveIntegerField(default=0) ...
    included_message_pairs = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="NULL means unlimited (fair‑use cap applies instead)",
    )
    included_characters = models.PositiveIntegerField(null=True, blank=True, help_text="NULL means unlimited")
    included_images = models.PositiveIntegerField(null=True, blank=True, help_text="NULL means unlimited")
    fair_use_message_cap = models.PositiveIntegerField(
        null=True, blank=True, help_text="Only used when included_message_pairs is NULL"
    )
    fair_use_character_cap = models.PositiveIntegerField(
        null=True, blank=True, help_text="Only used when included_characters is NULL"
    )
    fair_use_image_cap = models.PositiveIntegerField(
        null=True, blank=True, help_text="Only used when included_images is NULL"
    )

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "subscription_plans"
        ordering = ["price"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="unique_default_subscription_plan",
            )
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class SubscriptionUsage(TimeStampedModel):
    usage_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_subscription = models.ForeignKey(
        "capp.UserSubscription",
        on_delete=models.CASCADE,
        related_name="usage_records",
    )
    cycle_start = models.DateTimeField()
    cycle_end = models.DateTimeField()

    message_pairs_used = models.PositiveIntegerField(default=0)
    characters_created = models.PositiveIntegerField(default=0)
    images_generated = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "subscription_usage"
        ordering = ["-cycle_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_subscription", "cycle_start"],
                name="unique_usage_per_cycle",
            )
        ]


class SparkPackage(TimeStampedModel):
    spark_package_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    base_spark_amount = models.PositiveBigIntegerField()
    base_price_kes = models.DecimalField(max_digits=10, decimal_places=2)
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="e.g. 2.5 means 2.5% discount",
    )
    vat_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text=(
            "VAT applied on top of the discounted price, e.g. 16 means 16% VAT "
            "(Kenya standard rate for digital supplies). final_price_kes "
            "includes this VAT."
        ),
    )
    final_price_kes = models.DecimalField(max_digits=10, decimal_places=2)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "spark_packages"
        ordering = ["base_spark_amount"]

    def __str__(self):
        return f"{self.name} ({self.base_spark_amount} sparks)"


class UserSubscription(TimeStampedModel):
    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        TRIAL = "trial", "Trial"
        PAST_DUE = "past_due", "Past Due"

    user_subscription_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="subscriptions")
    subscription_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="user_subscriptions",
    )
    sparks_granted = models.PositiveBigIntegerField(default=0)
    sparks_remaining = models.PositiveBigIntegerField(default=0)

    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.TRIAL)
    started_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(blank=True, null=True)
    auto_renew = models.BooleanField(default=False)

    daily_tokens_used = models.PositiveBigIntegerField(default=0)
    tokens_used_in_cycle = models.PositiveBigIntegerField(default=0)
    daily_reset_at = models.DateTimeField(blank=True, null=True)

    external_subscription_reference = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "user_subscriptions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} - {self.subscription_plan.name}"


class SparkPool(TimeStampedModel):
    spark_pool_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name="spark_pool")

    sparks = models.PositiveBigIntegerField(default=0)
    token_balance = models.PositiveBigIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "spark_pools"
        constraints = [
            # PositiveBigIntegerField already emits an unnamed >= 0 check on
            # PostgreSQL. Naming the invariant explicitly means it survives a
            # future change of field type instead of silently disappearing.
            models.CheckConstraint(condition=Q(sparks__gte=0), name="spark_pool_sparks_non_negative"),
        ]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} Spark Pool"


class SparkTransaction(TimeStampedModel):
    class EntryType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class SourceChoices(models.TextChoices):
        PAYMENT = "payment", "Payment"
        SUBSCRIPTION = "subscription", "Subscription"
        USAGE = "usage", "Usage"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"
        TOKEN_PURCHASE = "token_purchase", "Token Purchase"
        BONUS = "bonus", "Bonus"

    spark_transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Retain ledger entries when the private account and its balance are erased.
    spark_pool = models.ForeignKey(
        SparkPool, on_delete=models.SET_NULL, related_name="transactions", null=True, blank=True
    )
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, related_name="spark_transactions", null=True, blank=True
    )

    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    source = models.CharField(max_length=30, choices=SourceChoices.choices)

    spark_amount = models.BigIntegerField(default=0)
    sparks_before = models.PositiveBigIntegerField(default=0)
    sparks_after = models.PositiveBigIntegerField(default=0)

    token_amount = models.BigIntegerField(default=0)
    token_balance_before = models.PositiveBigIntegerField(default=0)
    token_balance_after = models.PositiveBigIntegerField(default=0)

    payment_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    payment_currency = models.CharField(max_length=10, blank=True, null=True)

    reference = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "spark_transactions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["spark_pool", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["source", "created_at"]),
        ]

    def __str__(self):
        name = self.user.get_display_name() if self.user_id else "Deleted account"
        return f"{name} - {self.entry_type} - sparks:{self.spark_amount} tokens:{self.token_amount}"  # noqa: E231


class PromoCode(TimeStampedModel):
    """
    A single-use signup promo code that credits extra Sparks on top of the
    normal sign-up bonus.

    Codes are printed on physical flyers, so each code is unique and can be
    redeemed exactly once. ``redeemed_at`` is the authoritative used state: a
    code is consumed if and only if that timestamp is set. ``redeemed_by`` is
    only a convenience link for the admin panel and is nulled when the account
    is erased, while ``redeemed_at`` keeps the code consumed so a recycled
    flyer can never be redeemed twice.

    The database enforces two invariants that the redemption service also checks
    under a row lock:

    * one promo bonus per account — a unique constraint on ``redeemed_by``
      (multiple NULLs are allowed, so unredeemed codes coexist), and
    * a positive Sparks amount.

    ``redeemed_by``/``redeemed_at`` are deliberately NOT tied together by a
    check constraint: account erasure nulls ``redeemed_by`` while ``redeemed_at``
    stays set, which is exactly what keeps a recycled flyer consumed.

    ``is_active`` is reversible (a paused campaign); ``is_revoked`` permanently
    withdraws a code and cannot be undone. Revoking never reverses Sparks
    that were already credited — the ledger is append-only.
    """

    promo_code_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Normalised by the service: uppercase, no separators, unambiguous alphabet.
    code = models.CharField(max_length=32, unique=True)

    sparks_amount = models.PositiveBigIntegerField()

    is_active = models.BooleanField(default=True)
    is_revoked = models.BooleanField(default=False)

    redeemed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="redeemed_promo_codes",
        null=True,
        blank=True,
    )
    redeemed_at = models.DateTimeField(null=True, blank=True)

    # The admin who generated the code (audit convenience; may be NULL for
    # codes created outside the admin panel).
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="created_promo_codes",
        null=True,
        blank=True,
    )

    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "promo_codes"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(sparks_amount__gt=0), name="promo_code_sparks_positive"),
            models.UniqueConstraint(fields=["redeemed_by"], name="promo_code_one_per_account"),
        ]
        indexes = [models.Index(fields=["is_active"]), models.Index(fields=["redeemed_at"])]

    def __str__(self):
        return f"{self.code} ({self.sparks_amount} sparks)"


class PaymentTransaction(TimeStampedModel):
    class PaymentMethod(models.TextChoices):
        MPESA = "mpesa", "M-Pesa"
        CARD = "card", "Card"

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    class PurposeChoices(models.TextChoices):
        SUBSCRIPTION = "subscription", "Subscription"
        SPARK_PURCHASE = "spark_purchase", "Spark Purchase"
        TOKEN_PURCHASE = "token_purchase", "Token Purchase"

    payment_transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, related_name="payment_transactions", null=True, blank=True
    )
    spark_pool = models.ForeignKey(
        SparkPool,
        on_delete=models.SET_NULL,
        related_name="payment_transactions",
        blank=True,
        null=True,
    )
    user_subscription = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name="payment_transactions",
        blank=True,
        null=True,
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    provider_reference = models.CharField(max_length=255, blank=True, null=True)
    merchant_request_id = models.CharField(max_length=255, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=255, blank=True, null=True)
    idempotency_key = models.CharField(max_length=255, blank=True, null=True, unique=True)

    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    purpose = models.CharField(max_length=30, choices=PurposeChoices.choices)

    sparks_awarded = models.PositiveBigIntegerField(default=0)
    tokens_awarded = models.PositiveBigIntegerField(default=0)

    initiated_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    failure_reason = models.TextField(blank=True, null=True)

    provider_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payment_transactions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["purpose", "status"]),
            models.Index(fields=["payment_method", "status"]),
        ]

    def __str__(self):
        name = self.user.get_display_name() if self.user_id else "Deleted account"
        return f"{name} - {self.amount} {self.currency} - {self.status}"


class MessageFlag(TimeStampedModel):
    flag_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        "Message",
        on_delete=models.CASCADE,
        related_name="flags",
    )
    reported_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_message_flags",
    )
    guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    reason = models.TextField()
    reviewed = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_message_flags",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    resolution_note = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "message_flags"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reported_by", "message"],
                condition=models.Q(reported_by__isnull=False),
                name="unique_message_flag_per_user",
            ),
            models.UniqueConstraint(
                fields=["guest_session_id", "message"],
                condition=models.Q(guest_session_id__isnull=False),
                name="unique_message_flag_per_guest",
            ),
        ]


class ModerationFlag(TimeStampedModel):
    class FlagType(models.TextChoices):
        SAFETY = "safety", "Safety"
        ABUSE = "abuse", "Abuse"
        NSFW = "nsfw", "NSFW"
        SPAM = "spam", "Spam"
        OTHER = "other", "Other"

    class SeverityChoices(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class StatusChoices(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWED = "reviewed", "Reviewed"
        DISMISSED = "dismissed", "Dismissed"
        ACTIONED = "actioned", "Actioned"

    moderation_flag_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="moderation_flags")
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.SET_NULL,
        related_name="moderation_flags",
        blank=True,
        null=True,
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="moderation_flags",
        blank=True,
        null=True,
    )

    flag_type = models.CharField(max_length=20, choices=FlagType.choices)
    reason = models.TextField()
    severity = models.CharField(max_length=20, choices=SeverityChoices.choices, default=SeverityChoices.LOW)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.OPEN)

    reviewed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="reviewed_moderation_flags",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    resolution_note = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "moderation_flags"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.flag_type} - {self.status}"


class UsageCharge(TimeStampedModel):
    class ChargeSource(models.TextChoices):
        SUBSCRIPTION = "subscription", "Subscription"
        SPARK_POOL = "spark_pool", "Spark Pool"
        FREE_TIER = "free_tier", "Free Tier"
        PURCHASED_TOKENS = "purchased_tokens", "Purchased Tokens"

    usage_charge_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="usage_charges",
        null=True,  # ← allow for guests
        blank=True,
    )
    guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    # Immutable provenance for charges incurred while anonymous — retained after
    # an account claims the guest so the ledger stays attributable.
    origin_guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    claimed_from_guest_at = models.DateTimeField(blank=True, null=True)
    token_usage_log = models.OneToOneField(
        TokenUsageLog,
        on_delete=models.SET_NULL,
        related_name="usage_charge",
        null=True,
        blank=True,
    )
    spark_transaction = models.ForeignKey(
        SparkTransaction,
        on_delete=models.SET_NULL,
        related_name="usage_charges",
        blank=True,
        null=True,
    )
    user_subscription = models.ForeignKey(
        UserSubscription,
        on_delete=models.SET_NULL,
        related_name="usage_charges",
        blank=True,
        null=True,
    )

    charge_source = models.CharField(max_length=20, choices=ChargeSource.choices)

    sparks_charged = models.PositiveBigIntegerField(default=0)
    tokens_charged = models.PositiveBigIntegerField(default=0)
    amount_charged = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "usage_charges"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["charge_source", "created_at"]),
        ]

    def __str__(self):
        name = self.user.get_display_name() if self.user_id else "Guest or Deleted account"
        return (  # noqa: E231
            f"{name} - sparks:{self.sparks_charged} tokens:{self.tokens_charged} - {self.charge_source}"
        )


class BillingReservation(TimeStampedModel):
    """
    A Spark amount held for one billable operation.

    Sparks are debited once, up front, when the reservation is created. The
    reservation then records what happened to that debit:

    * ``reserved``  – held; the operation is in flight.
    * ``settled``   – the operation succeeded and the debit is final.
    * ``refunded``  – the operation failed and the Sparks were credited back.

    ``settled`` and ``refunded`` are terminal. Settling never charges a second
    time, because the charge already happened at reservation. This is what makes
    a paid feature safe to retry: the reservation, not the provider call, is the
    unit of exactly-once billing.

    There are deliberately no ``failed`` or ``expired`` statuses. An operation
    that dies without settling stays ``reserved`` and is surfaced by
    ``manage.py billing_reservations --list-stale``; an unattended state machine
    that auto-refunded would be a way to get free work out of the system by
    stalling. Releasing those holds is an explicit, audited human action.

    Principal
    ---------
    Exactly one of ``user`` or ``guest_identity`` is set, enforced in the
    database. The principal is never rewritten. When a guest is later claimed by
    an account the reservation keeps pointing at the original ``GuestIdentity``
    and records the claim in ``claimed_by`` / ``claimed_from_guest_at``, so the
    history of who incurred the charge stays intact — the same provenance rule
    ``Conversation`` and ``UsageCharge`` follow.
    """

    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        SETTLED = "settled", "Settled"
        REFUNDED = "refunded", "Refunded"

    TERMINAL_STATUSES = frozenset({Status.SETTLED, Status.REFUNDED})

    class Feature(models.TextChoices):
        MESSAGE_PAIR = "message_pair", "Message pair"
        CHARACTER_GENERATION = "character_generation", "Character generation"
        CHARACTER_EDIT = "character_edit", "Character edit"
        IMAGE_GENERATION = "image_generation", "Image generation"
        AGENT_GATEWAY = "agent_gateway", "Agent Gateway"

    reservation_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Supplied by the caller and stable across retries of the same logical
    # operation. Unique per principal (see constraints), not globally: two
    # different users may legitimately derive the same key.
    idempotency_key = models.CharField(max_length=255)

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="billing_reservations",
        blank=True,
        null=True,
    )
    guest_identity = models.ForeignKey(
        GuestIdentity,
        on_delete=models.PROTECT,
        related_name="billing_reservations",
        blank=True,
        null=True,
    )

    feature = models.CharField(max_length=40, choices=Feature.choices)
    amount_sparks = models.BigIntegerField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESERVED, db_index=True)

    reserved_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(blank=True, null=True)
    refunded_at = models.DateTimeField(blank=True, null=True)

    settlement_reason = models.TextField(blank=True, null=True)
    refund_reason = models.TextField(blank=True, null=True)

    # Free-form pointer to whatever the reservation paid for — a Celery task id,
    # a generated image id, a message id. Unique when set so one artifact can
    # never be billed by two reservations.
    operation_reference = models.CharField(max_length=255, blank=True, null=True, db_index=True)

    # Immutable provenance. Written once at reservation time and never updated,
    # so the ledger stays attributable even after a claim moves the balance.
    origin_guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    claimed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="claimed_billing_reservations",
        blank=True,
        null=True,
    )
    claimed_from_guest_at = models.DateTimeField(blank=True, null=True)

    # Ledger rows this reservation produced. One-to-one so a single reservation
    # can never be double-posted to the Spark ledger. Only populated for
    # authenticated principals: SparkTransaction is posted against an account
    # pool (its links are nulled on erasure). For unclaimed guests this
    # reservation row is itself the audit record.
    debit_spark_transaction = models.OneToOneField(
        SparkTransaction,
        on_delete=models.SET_NULL,
        related_name="reserved_by",
        blank=True,
        null=True,
    )
    refund_spark_transaction = models.OneToOneField(
        SparkTransaction,
        on_delete=models.SET_NULL,
        related_name="refunded_by",
        blank=True,
        null=True,
    )
    usage_charge = models.OneToOneField(
        UsageCharge,
        on_delete=models.SET_NULL,
        related_name="billing_reservation",
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "billing_reservations"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "reserved_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["guest_identity", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(user__isnull=False, guest_identity__isnull=True)
                    | Q(user__isnull=True, guest_identity__isnull=False)
                ),
                name="billing_reservation_exactly_one_principal",
            ),
            models.CheckConstraint(
                condition=Q(amount_sparks__gt=0),
                name="billing_reservation_positive_amount",
            ),
            # One reservation per (idempotency key, principal). Feature is
            # deliberately *not* part of the key: reusing a key for a different
            # feature is the conflict the service must reject, not a second
            # reservation it should allow.
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=Q(user__isnull=False),
                name="unique_billing_reservation_per_user_key",
            ),
            models.UniqueConstraint(
                fields=["guest_identity", "idempotency_key"],
                condition=Q(guest_identity__isnull=False),
                name="unique_billing_reservation_per_guest_key",
            ),
            models.UniqueConstraint(
                fields=["operation_reference"],
                condition=Q(operation_reference__isnull=False),
                name="unique_billing_reservation_operation_reference",
            ),
            # Status and its timestamps cannot disagree.
            models.CheckConstraint(
                condition=(
                    Q(status="reserved", settled_at__isnull=True, refunded_at__isnull=True)
                    | Q(status="settled", settled_at__isnull=False, refunded_at__isnull=True)
                    | Q(status="refunded", refunded_at__isnull=False, settled_at__isnull=True)
                ),
                name="billing_reservation_status_timestamps_agree",
            ),
            # A claim stamp only makes sense on a guest-owned reservation.
            # The third branch is the erased-claimer state: deleting an account
            # SET_NULLs ``claimed_by``, and the event's provenance (origin guest
            # identity + claim timestamp) has to outlive the erased person.
            models.CheckConstraint(
                condition=(
                    Q(claimed_by__isnull=True, claimed_from_guest_at__isnull=True)
                    | Q(
                        claimed_by__isnull=False,
                        claimed_from_guest_at__isnull=False,
                        guest_identity__isnull=False,
                    )
                    | Q(
                        claimed_by__isnull=True,
                        claimed_from_guest_at__isnull=False,
                        guest_identity__isnull=False,
                    )
                ),
                name="billing_reservation_claim_provenance_complete",
            ),
        ]

    def __str__(self):
        return f"BillingReservation {self.reservation_id} {self.feature} {self.amount_sparks} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    @property
    def principal_label(self) -> str:
        """Stable human-readable principal, used in logs and the CLI."""
        if self.user_id is not None:
            return f"user:{self.user_id}"
        return f"guest:{self.guest_identity_id}"


class AdminActionLog(TimeStampedModel):
    """
    Persistent audit trail for destructive or sensitive admin actions.

    Unlike stdout logs (which rotate away on Railway), this table keeps a
    durable, queryable record of every admin action that changes user status,
    adjusts Sparks, or permanently deletes a character. Required for compliance
    accountability under the Kenya Data Protection Act 2019 and good practice
    for any admin surface with real operational power.

    Spark adjustments are also recorded as SparkTransaction rows (the financial
    ledger), but this log captures the full admin context — who, what, why, and
    when — in a single place that covers non-financial actions too.
    """

    class ActionType(models.TextChoices):
        USER_STATUS_CHANGE = "user_status_change", "User Status Change"
        SPARK_ADJUSTMENT = "spark_adjustment", "Spark Adjustment"
        CHARACTER_HARD_DELETE = "character_hard_delete", "Character Hard Delete"
        SAFETY_EVENT_REVERSE = "safety_event_reverse", "Safety Event Reversal"
        SAFETY_ACCOUNT_UNLOCK = "safety_account_unlock", "Safety Account Unlock"
        SAFETY_MODEL_REFUSALS_RESET = "safety_model_refusals_reset", "Safety Model-Refusal Flags Reset"
        PROMO_CODE_CREATE = "promo_code_create", "Promo Code Create"
        PROMO_CODE_UPDATE = "promo_code_update", "Promo Code Update"
        PROMO_CODE_REVOKE = "promo_code_revoke", "Promo Code Revoke"
        PROMO_CODE_DELETE = "promo_code_delete", "Promo Code Delete"

    log_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The admin who performed the action.
    admin_user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="admin_actions_performed",
        null=True,
        blank=True,
    )

    action_type = models.CharField(max_length=40, choices=ActionType.choices)

    # The user affected by the action (if applicable).
    target_user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="admin_actions_received",
        blank=True,
        null=True,
    )

    # The character affected by the action (if applicable). Stored as a UUID
    # rather than a FK because hard-deleted characters no longer have a row.
    target_character_id = models.UUIDField(blank=True, null=True)
    target_character_name = models.CharField(max_length=255, blank=True, null=True)

    # Human-readable reason provided by the admin.
    reason = models.TextField(blank=True, null=True)

    # Structured details (old/new status, spark delta, cascade counts, etc.).
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "admin_action_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["admin_user", "created_at"]),
            models.Index(fields=["target_user", "created_at"]),
            models.Index(fields=["action_type", "created_at"]),
        ]

    def __str__(self):
        admin = self.admin_user.username if self.admin_user_id else "Deleted account"
        return f"{admin} — {self.action_type} — {self.created_at:%Y-%m-%d %H:%M}"  # noqa: E231


# ============================================================
# PUSH NOTIFICATIONS
# ============================================================
class PushSubscription(TimeStampedModel):
    """
    A Web Push subscription for one browser/device of a user.

    The endpoint is the push service's delivery address (FCM, Mozilla, ...)
    and doubles as the natural unique key: the same browser re-subscribing
    after a toggle off/on yields the same endpoint, so an upsert keeps one
    row per real device. Rows die with the user (CASCADE), and dead ones are
    pruned on send when the push service reports 404/410.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="push_subscriptions")
    # FCM endpoints can exceed 200 chars, so the URLField default is too small.
    endpoint = models.URLField(max_length=768, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    # Diagnostics only: tells devices apart when a user reports lost pushes.
    user_agent = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "push_subscriptions"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user.username} — {self.endpoint[:60]}"


class MediaDeletionJob(TimeStampedModel):
    """Retryable storage erasure; removed only after the object is deleted."""

    model_label = models.CharField(max_length=100)
    field_name = models.CharField(max_length=100)
    file_name = models.CharField(max_length=1024)

    def perform(self):
        from django.apps import apps
        from django.core.files.storage import default_storage

        # This table is populated only from server-owned FieldFile instances
        # and MessageAttachment storage keys.
        try:
            model = apps.get_model(self.model_label)
            field = model._meta.get_field(self.field_name)
            # Do not destroy an object still used by a surviving shared image.
            for owner_model, owner_field in (
                (CustomUser, "avatar"),
                (Character, "avatar"),
                (CharacterGalleryImage, "image"),
            ):
                if owner_model.objects.using(self._state.db).filter(**{owner_field: self.file_name}).exists():
                    self.delete()
                    return True
            # MessageAttachment stores an encrypted blob under a CharField
            # key rather than a FileField; fall back to the default storage.
            storage = getattr(field, "storage", None) or default_storage
            storage.delete(self.file_name)
        except Exception:
            # Log the queue ID, not possibly identifying file names/provider
            # exception payloads. The retained job remains available to retry.
            logging.getLogger(__name__).error("Media erasure job %s needs retry", self.pk)
            return False
        self.delete()
        return True


def delete_file_after_commit(field_file, using=None):
    """Never delete storage objects before a database deletion commits.

    The durable outbox shares the caller's DB transaction. A rollback drops the
    job; a crash or storage failure leaves it for the periodic retry worker.
    """
    if field_file:
        job = MediaDeletionJob.objects.using(using).create(
            model_label=field_file.field.model._meta.label,
            field_name=field_file.field.name,
            file_name=field_file.name,
        )
        transaction.on_commit(job.perform, using=using, robust=True)


@receiver(pre_delete, sender=CustomUser)
def scrub_deleted_account_metadata(sender, instance, using, **kwargs):
    from capp.classes.auth.account_erasure import scrub_retained_personal_data

    scrub_retained_personal_data(instance, using)


@receiver(post_delete, sender=CharacterGalleryImage)
def delete_gallery_image_file(sender, instance, using, **kwargs):
    delete_file_after_commit(instance.image, using)


@receiver(post_delete, sender=CustomUser)
@receiver(post_delete, sender=Character)
def delete_avatar_file(sender, instance, using, **kwargs):
    delete_file_after_commit(instance.avatar, using)


# Signal to delete old avatar when changed
@receiver(pre_save, sender=Character)
def delete_old_avatar_on_change(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if not instance.pk or (update_fields is not None and "avatar" not in update_fields):
        return
    try:
        # Character.save supplies the atomic boundary. Lock before taking the
        # storage snapshot, just as generation and account erasure do.
        current = Character.objects.using(kwargs.get("using")).select_for_update().get(pk=instance.pk)
    except Character.DoesNotExist:
        if not instance._state.adding:
            # A stale plain save must not fall back to INSERT after deletion.
            raise
        return
    old_avatar = current.avatar
    new_avatar = instance.avatar
    if current.status == Character.StatusChoices.ARCHIVED and new_avatar and new_avatar != old_avatar:
        raise ValueError("Cannot publish an avatar on an archived character")
    if old_avatar and old_avatar != new_avatar:
        delete_file_after_commit(old_avatar, kwargs.get("using"))


# ============================================================
# AGENT GATEWAY MODELS
# ============================================================


class AgentAPIKey(TimeStampedModel):
    """
    API keys for the UwenzAi Agent Gateway (UwenzAi-Dev feature).

    Users create these keys to allow external AI agents (Hermes, OpenClaw, etc.)
    to call the UwenzAi API. The plaintext key is NEVER stored — only a
    SHA-256 hash and the last 4 characters (hint) are persisted.

    Auth flow:
      1. Client presents: Authorization: Bearer uwenzai-dev-...
      2. Hash the presented key (SHA-256 hex)
      3. Look up by key_hash
      4. Check is_active and expires_at
      5. Update last_used_at on successful auth
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    agent_api_key_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_api_keys",
        verbose_name="User",
        help_text="Owner of this API key",
    )
    label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="User-chosen label for this key (e.g., 'Hermes agent', 'Production')",
    )
    # NEVER store plaintext. Hash is SHA-256 hex of the full key.
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the API key",
    )
    # Last 4 characters for display in UI (e.g., "...7f3a")
    key_hint = models.CharField(
        max_length=4,
        help_text="Last 4 characters of the key for display purposes only",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="If False, the key is revoked and will be rejected",
    )
    # Optional: per-key monthly spending cap in sparks
    monthly_spark_budget = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text="Optional: maximum sparks that can be spent with this key per calendar month",
    )
    # Optional expiration date
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="If set, key is invalid after this date",
    )
    # Track when this key was last used for a successful request
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time this key was used for a successful request",
    )
    # When the key was revoked (is_active=False). Drives the retention task
    # that hard-deletes revoked keys after AGENT_KEY_REVOKED_RETENTION_DAYS;
    # usage logs survive via SET_NULL plus the denormalized key_hint.
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this key was revoked; NULL while the key is active",
    )

    class Meta:
        db_table = "agent_api_keys"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} — {self.label or self.key_hint} — {'active' if self.is_active else 'revoked'}"

    @property
    def display_hint(self) -> str:
        """Return a displayable hint like '...7f3a' for the UI."""
        return f"...{self.key_hint}"

    def is_expired(self) -> bool:
        """Check if the key has expired."""
        if self.expires_at is None:
            return False
        return timezone.now() >= self.expires_at

    def can_be_used(self) -> bool:
        """Check if the key is active and not expired."""
        return self.is_active and not self.is_expired()


class AgentModelPrice(TimeStampedModel):
    """
    Cost catalog for Agent Gateway models.

    Stores the upstream (provider) pricing per 1M tokens in the provider's
    native currency. UwenzAi adds a per-provider markup (configured via
    AGENT_MARKUP_PERCENT and the provider-specific settings) on top, then
    converts to KES using live FX.

    Editable via Django admin so price updates don't require a deploy.
    """

    provider = models.CharField(
        max_length=50,
        help_text="Provider name (e.g., 'cortecs', 'mistral', 'fikra', 'aki', 'ovh', 'inceptron')",
    )
    model = models.CharField(
        max_length=100,
        help_text="Model identifier (e.g., 'deepseek-v4-flash', 'mistral-large-latest')",
    )
    currency = models.CharField(
        max_length=10,
        default="EUR",
        help_text="Currency of the upstream price (e.g., 'EUR', 'USD')",
    )
    input_price_per_1m = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Upstream input token price per 1 million tokens (in native currency)",
    )
    output_price_per_1m = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Upstream output token price per 1 million tokens (in native currency)",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="If False, this model is hidden from GET /agent/models",
    )

    max_context = models.PositiveIntegerField(null=True, blank=True)
    max_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    capabilities = models.JSONField(
        default=list, blank=True, help_text="Verified capability labels only; [] means unknown"
    )

    class Meta:
        db_table = "agent_model_prices"
        ordering = ["provider", "model"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "model"],
                name="unique_provider_model",
            ),
        ]
        verbose_name = "Agent Model Price"
        verbose_name_plural = "Agent Model Prices"

    def __str__(self):
        return (
            f"{self.provider}/{self.model} — "
            f"in:{self.currency}{self.input_price_per_1m}/"
            f"out:{self.currency}{self.output_price_per_1m}"
        )


class AgentUsageLog(TimeStampedModel):
    """
    Usage log for Agent Gateway API calls.

    One row per gateway call. Does NOT store prompt/response content (v1 privacy rule).
    Contains only metadata: tokens, cost, status, latency, etc.
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        UPSTREAM_ERROR = "upstream_error", "Upstream Provider Error"
        REFUND_PENDING = "refund_pending", "Refund awaiting reconciliation"
        REJECTED = "rejected", "Rejected"
        INVALID_API_KEY = "invalid_api_key", "Invalid API Key"
        INSUFFICIENT_SPARKS = "insufficient_sparks", "Insufficient Sparks"
        BUDGET_EXCEEDED = "budget_exceeded", "Budget Exceeded"
        IDENTICAL_REQUEST = "identical_request", "Identical Request (Idempotency)"

    usage_log_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # The key that was used for this call (SET_NULL so logs survive key deletion)
    key = models.ForeignKey(
        AgentAPIKey,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_logs",
        help_text="The API key used for this call",
    )
    # Denormalize key_hint for display when key is deleted
    key_hint = models.CharField(
        max_length=4,
        blank=True,
        default="",
        help_text="Last 4 chars of the key, for display when key is deleted",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_usage_logs",
        help_text="User who owns the key that made this call",
    )

    class Source(models.TextChoices):
        API = "api", "Direct API"
        PLAYGROUND = "playground", "Workspace Playground"

    source = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        choices=Source.choices,
        help_text=(
            "Whether this call came through the external API or the workspace "
            "playground; NULL = logged before this field existed"
        ),
    )
    provider = models.CharField(
        max_length=50,
        help_text="Provider name (e.g., 'cortecs')",
    )
    model = models.CharField(
        max_length=100,
        help_text="Model identifier",
    )
    input_tokens = models.PositiveBigIntegerField(
        help_text="Input tokens from provider usage block",
    )
    output_tokens = models.PositiveBigIntegerField(
        help_text="Output tokens from provider usage block",
    )
    # Pre-markup upstream cost in provider's native currency
    cost_native = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Upstream cost in provider's native currency (before UwenzAi markup)",
    )
    cost_currency = models.CharField(
        max_length=10,
        default="EUR",
        help_text="Currency of the upstream cost (e.g., 'EUR', 'USD')",
    )
    # FX rate used for this request (for audit/reconciliation)
    fx_rate = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Exchange rate (native currency to KES) used for this charge",
    )
    # Final charge in sparks (after markup + FX conversion)
    sparks_charged = models.PositiveBigIntegerField(
        help_text="Final charge in sparks",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.SUCCESS,
        help_text="Status of this call",
    )
    latency_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Request latency in milliseconds",
    )
    # Idempotency: same client_operation_id = same charge, one log row
    client_operation_id = models.CharField(
        max_length=300,
        null=True,
        blank=True,
        help_text="Client-provided operation ID for idempotency",
    )

    class Meta:
        db_table = "agent_usage_logs"
        constraints = [
            models.UniqueConstraint(fields=["user", "key", "client_operation_id"], name="unique_agent_usage_operation")
        ]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["key", "created_at"]),
            models.Index(fields=["status"]),
            models.Index(fields=["provider", "model"]),
            models.Index(fields=["client_operation_id"]),
        ]
        verbose_name = "Agent Usage Log"
        verbose_name_plural = "Agent Usage Logs"

    def __str__(self):
        return f"{self.user.username} — {self.provider}/{self.model} — {self.status} — {self.sparks_charged} sparks"


class AgentPlaygroundHistory(TimeStampedModel):
    """Opt-in ciphertext only. Deleting the row fences any in-flight save."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_playground_histories"
    )
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)
    messages_encrypted = models.TextField(blank=True, default="", editable=False)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "provider", "model"], name="unique_playground_history")]


class AgentGatewayOperation(TimeStampedModel):
    """Durable execution claim: a lost lock or worker crash must not repeat inference."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.ForeignKey(AgentAPIKey, on_delete=models.CASCADE)
    client_operation_id = models.CharField(max_length=300)
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key", "client_operation_id"], name="unique_agent_operation")
        ]


# ============================================================
# USER-SENT PHOTO ATTACHMENTS
# ============================================================


class MessageAttachment(TimeStampedModel):
    """A photo the user attached to a chat message.

    The image itself lives in the private bucket as AES-256-GCM envelope-
    encrypted ciphertext (see ``capp.classes.chat.attachment_crypto``); this
    row holds the storage key, the wrapped per-image data key and the
    moderation outcome. Rows are deleted with their message (conversation
    delete / account erasure cascade), and the storage object is removed
    through the same MediaDeletionJob outbox used for avatars and gallery
    images.
    """

    class ModerationStatus(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    attachment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    # Kept for direct operator queries; the FK is SET_NULL so an erasure pass
    # can scrub ownership without orphaning storage cleanup.
    owner = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        related_name="chat_attachments",
        blank=True,
        null=True,
    )

    # --- Storage ---
    storage_key = models.CharField(max_length=512)
    content_type = models.CharField(max_length=32)
    byte_size = models.PositiveIntegerField()  # ciphertext length
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64)  # of the normalised plaintext

    # --- Envelope encryption metadata (never the key material itself) ---
    key_version = models.CharField(max_length=32)
    wrapped_key = models.TextField()  # base64 data key wrapped by master key
    nonce = models.CharField(max_length=64)  # base64 image+wrap nonces

    # --- Moderation outcome ---
    moderation_status = models.CharField(
        max_length=20,
        choices=ModerationStatus.choices,
        default=ModerationStatus.APPROVED,
    )
    moderation_provider = models.CharField(max_length=64, blank=True)
    moderation_categories = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "message_attachments"
        indexes = [
            models.Index(fields=["message"]),
            models.Index(fields=["sha256"]),
        ]

    def __str__(self):
        return f"Photo attachment {self.attachment_id} (message={self.message_id})"


class PhotoScreeningEvent(TimeStampedModel):
    """Minimal audit row for a REFUSED photo screening.

    Written only when an upload was blocked (or could not be screened) —
    allowed photos keep their moderation metadata on the user-deletable
    attachment row instead. Retained past account erasure on the child-safety
    reporting basis: it stores the acting user id, the image hash, the
    provider, the decision and the blocked categories, and deliberately
    nothing else (never image bytes, never captions). Operators with a
    reporting obligation (e.g. Kenyan Children Act / Computer Misuse duties)
    export from here.
    """

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Plain UUID columns (no FK): the row must survive account erasure.
    user_id = models.UUIDField(null=True, blank=True)
    conversation_id = models.UUIDField(null=True, blank=True)

    sha256 = models.CharField(max_length=64)
    provider = models.CharField(max_length=64)
    decision = models.CharField(max_length=20)  # block / unavailable
    categories = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "photo_screening_events"
        indexes = [
            models.Index(fields=["user_id", "created_at"]),
            models.Index(fields=["sha256"]),
        ]

    def __str__(self):
        return f"Photo screening {self.decision} ({self.provider})"


@receiver(post_delete, sender=MessageAttachment)
def delete_message_attachment_file(sender, instance, using, **kwargs):
    """Queue storage erasure for a deleted photo attachment.

    Attachment blobs are not FieldFiles, so this mirrors
    ``delete_file_after_commit`` with the default storage explicitly: the job
    resolves the model/field for audit but the deletion always runs against
    the bucket the encrypted blob was written to.
    """
    if instance.storage_key:
        job = MediaDeletionJob.objects.using(using).create(
            model_label="capp.MessageAttachment",
            field_name="storage_key",
            file_name=instance.storage_key,
        )
        transaction.on_commit(job.perform, using=using, robust=True)


class SafetyEvent(TimeStampedModel):
    """Cross-session safety ledger row for a classified user message.

    Written by ``capp/classes/moderation/safety_ledger.py`` when the
    deterministic text safety screen classifies a message. This is the
    account-level counterpart of the per-image ``PhotoScreeningEvent``:
    one row per recorded signal, so repeated or critical violations can lock
    the account regardless of which chat they happened in.

    Privacy by construction: the row stores ONLY structured metadata —
    category, severity, confidence, the action taken, timestamps and an
    irreversible HMAC fingerprint of the message used for deduplication.
    It never stores the message text, an embedding, a summary, or any FK to
    the message itself. Admins review counts and categories; the exact text
    remains visible only to the user who wrote it (and their own exports).

    Plain UUID columns (no FKs): rows must survive account erasure on the
    same child-safety / legal-reporting basis as ``PhotoScreeningEvent``,
    so an erased account still has its safety history for operators with a
    reporting obligation.

    Reversal: an admin can soft-reverse a specific event when it was a
    false positive. Reversal is an annotation (``is_reversed``), never a
    deletion — the audit trail of what was recorded and why it was undone
    stays queryable.
    """

    class Category(models.TextChoices):
        SELF_HARM = "self_harm", "Self-harm concern (care, non-punitive)"
        WEAPONS_CONTEXT = "weapons_context", "Weapons interest (concern signal)"
        WEAPONS_INSTRUCTIONS = "weapons_instructions", "Weapon construction instructions"
        VIOLENCE_PLANNING = "violence_planning", "Violence planning"
        THREATS = "threats_targeted", "Targeted threat against a person"
        TRAFFICKING = "human_trafficking", "Human trafficking"
        GENDER_VIOLENCE = "gender_violence", "Gender-based violence"
        CSAM = "csam", "Child sexual abuse material"
        MODEL_REFUSAL = "model_refusal", "Model refused a request (advisory, never punitive)"
        PHOTO_CHILD_SAFETY = "photo_child_safety", "Blocked child-safety photo upload"
        GENERATED_IMAGE_CHILD_SAFETY = (
            "generated_image_child_safety",
            "Blocked child-safety AI-generated image",
        )
        PHOTO_BESTIALITY = "photo_bestiality", "Blocked bestiality photo upload"
        PHOTO_GORE = "photo_gore", "Blocked gore photo upload"
        PHOTO_DEATH = "photo_death", "Blocked death photo upload"
        GENERATED_IMAGE_BESTIALITY = (
            "generated_image_bestiality",
            "Blocked bestiality AI-generated image",
        )
        GENERATED_IMAGE_GORE = (
            "generated_image_gore",
            "Blocked gore AI-generated image",
        )
        GENERATED_IMAGE_DEATH = (
            "generated_image_death",
            "Blocked death AI-generated image",
        )

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Action(models.TextChoices):
        LOGGED = "logged", "Logged (no message block)"
        WARNED = "warned", "Blocked with warning"
        BLOCKED = "blocked", "Blocked"
        LOCKED = "locked", "Blocked and account locked"
        SHADOWED = "shadowed", "Recorded in shadow mode (measured, not enforced)"

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Plain UUID columns (no FK): rows survive account erasure.
    user_id = models.UUIDField(null=False, blank=False)
    conversation_id = models.UUIDField(null=True, blank=True)

    category = models.CharField(max_length=40, choices=Category.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    action = models.CharField(max_length=10, choices=Action.choices)

    # True for self-harm care events: recorded for support context only,
    # excluded from every punitive computation.
    is_care = models.BooleanField(default=False)

    confidence = models.FloatField(default=1.0)
    classifier_version = models.CharField(max_length=40)

    # Irreversible keyed fingerprint of the normalised message text. Used
    # only to deduplicate rapid re-sends within a short window so spamming
    # the same violation does not stack the score. Not message content.
    fingerprint = models.CharField(max_length=64)

    # Aggravation marker: this violation was escalated because prior
    # concern signals existed when it was recorded (e.g. earlier weapons
    # interest in another conversation).
    was_aggravated = models.BooleanField(default=False)

    # Admin reversal (soft, never a delete).
    is_reversed = models.BooleanField(default=False)
    reversed_at = models.DateTimeField(blank=True, null=True)
    reversed_by = models.UUIDField(blank=True, null=True)
    reversal_reason = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "safety_events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_id", "created_at"]),
            models.Index(fields=["user_id", "is_reversed", "created_at"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        return f"Safety event {self.category} ({self.severity}) {'[reversed]' if self.is_reversed else ''}"
