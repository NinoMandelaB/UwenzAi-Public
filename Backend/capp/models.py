import uuid
from decimal import Decimal
import random
import string
from datetime import timedelta

from django.db.models.signals import pre_save, post_delete
from django.dispatch import receiver

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from capp.fields import EncryptedTextField
from django.db.models import Q
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
    code = models.CharField(max_length=6, db_index=True)
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
        return "".join(random.choices(string.digits, k=length))

    @classmethod
    def create_for_user(cls, user, expiry_minutes=10):
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
    code = models.CharField(max_length=6)
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
        return "".join(random.choices(string.digits, k=length))

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
        super().save(*args, **kwargs)

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
    # Read watermark for the single owner of this conversation. Unread is
    # derived from it rather than stored as a counter so that deleting or
    # resetting messages can never leave a badge stuck on a stale number.
    last_read_at = models.DateTimeField(default=timezone.now)

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

    # ----- pgvector fields -----
    embedding = VectorField(dimensions=1024, null=True, blank=True)
    embedding_model = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "memory_facts"
        ordering = ["-importance_score", "-extracted_at"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["character", "is_active"]),
        ]

    def __str__(self):
        name = self.user.get_display_name()
        return f"{name} - {self.fact_type}"


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
    spark_pool = models.ForeignKey(SparkPool, on_delete=models.CASCADE, related_name="transactions")
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="spark_transactions")

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
        name = self.user.get_display_name()
        return f"{name} - {self.entry_type} - sparks:{self.spark_amount} tokens:{self.token_amount}"  # noqa: E231


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
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="payment_transactions")
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
        name = self.user.get_display_name()
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
        on_delete=models.CASCADE,
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
        on_delete=models.CASCADE,
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
        name = self.user.get_display_name()
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
    # authenticated principals: SparkTransaction requires a pool and a user, so
    # for unclaimed guests this reservation row is itself the audit record.
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


# Signal to delete image file when gallery image is deleted
@receiver(post_delete, sender=CharacterGalleryImage)
def delete_gallery_image_file(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)


# Signal to delete old avatar when changed
@receiver(pre_save, sender=Character)
def delete_old_avatar_on_change(sender, instance, **kwargs):
    if not instance.pk:
        return
    try:
        old_avatar = Character.objects.get(pk=instance.pk).avatar
    except Character.DoesNotExist:
        return
    new_avatar = instance.avatar
    if old_avatar and old_avatar != new_avatar:
        old_avatar.delete(save=False)
