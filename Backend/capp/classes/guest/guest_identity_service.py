"""
Server-minted anonymous guest identity.

The browser only ever holds an opaque, high-entropy bearer credential in an
HttpOnly cookie. The server stores its SHA-256 hash and maps it to a
``GuestIdentity`` whose primary key is the *stable ownership ID* used by every
``guest_session_id`` column in the schema.

Consequences that the rest of the codebase relies on:

* Guest ownership is only derivable by presenting the cookie. A client cannot
  name a guest it does not hold the credential for, so ``session_id`` values in
  request bodies and query strings are no longer trusted anywhere.
* Rotating the credential does not re-key any guest-owned row, because the
  ownership ID is independent of the credential.
* The raw credential is never logged, returned in a response body, or placed in
  a URL. Log lines identify a guest by its ownership ID only.
"""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from capp.models import Conversation, GuestIdentity, GuestTokenQuota

logger = logging.getLogger(__name__)

# 48 bytes of urandom → 64 URL-safe characters, ~384 bits of entropy.
CREDENTIAL_BYTES = 48


def hash_credential(raw_credential: str) -> str:
    return hashlib.sha256(raw_credential.encode("utf-8")).hexdigest()


class GuestIdentityService:
    @staticmethod
    def _new_credential() -> str:
        return secrets.token_urlsafe(CREDENTIAL_BYTES)

    @staticmethod
    def _expiry(now=None):
        now = now or timezone.now()
        return now + timedelta(seconds=settings.GUEST_IDENTITY_TTL_SECONDS)

    @classmethod
    def mint(cls, guest_id=None, legacy_session_id: str = None) -> tuple:
        """
        Create a new guest identity.

        Returns ``(identity, raw_credential)``. The raw credential is returned
        exactly once, for the caller to place in the response cookie.
        """
        now = timezone.now()
        raw_credential = cls._new_credential()

        fields = {
            "credential_hash": hash_credential(raw_credential),
            "status": GuestIdentity.Status.ACTIVE,
            "expires_at": cls._expiry(now),
            "last_seen_at": now,
        }
        if guest_id is not None:
            fields["guest_id"] = guest_id
        if legacy_session_id is not None:
            # The legacy localStorage UUID becomes the ownership ID so existing
            # guest-owned rows keep resolving, and is recorded separately as
            # permanent migration provenance. It is never again a credential.
            fields["migrated_from_legacy_session_id"] = legacy_session_id
            fields["migrated_at"] = now
            fields["rotated_at"] = now
            fields["rotation_count"] = 1

        identity = GuestIdentity.objects.create(**fields)
        logger.info("Minted guest identity %s (legacy_migration=%s)", identity.guest_id, bool(legacy_session_id))
        return identity, raw_credential

    @classmethod
    def resolve(cls, request):
        """
        Return the ``GuestIdentity`` for this request, or None.

        Resolution is *only* from the HttpOnly cookie. Expired, revoked and
        already-claimed identities are rejected, so a stale cookie left behind
        after a claim can never resurrect the guest.
        """
        raw_credential = request.COOKIES.get(settings.GUEST_CREDENTIAL_COOKIE_NAME)
        if not raw_credential:
            return None

        identity = GuestIdentity.objects.filter(credential_hash=hash_credential(raw_credential)).first()
        if identity is None:
            return None

        now = timezone.now()
        if not identity.is_usable(now):
            return None

        cls._touch(identity, now)
        return identity

    @staticmethod
    def _touch(identity: GuestIdentity, now) -> None:
        """Refresh ``last_seen_at``/``expires_at``, but not on every request."""
        staleness = timedelta(seconds=settings.GUEST_LAST_SEEN_REFRESH_SECONDS)
        if identity.last_seen_at and now - identity.last_seen_at < staleness:
            return

        identity.last_seen_at = now
        identity.expires_at = now + timedelta(seconds=settings.GUEST_IDENTITY_TTL_SECONDS)
        GuestIdentity.objects.filter(pk=identity.pk, status=GuestIdentity.Status.ACTIVE).update(
            last_seen_at=identity.last_seen_at,
            expires_at=identity.expires_at,
            updated_at=now,
        )

    @classmethod
    def rotate_credential(cls, identity: GuestIdentity) -> str:
        """Issue a fresh credential for an existing identity, keeping ownership."""
        now = timezone.now()
        raw_credential = cls._new_credential()

        identity.previous_credential_hash = identity.credential_hash
        identity.credential_hash = hash_credential(raw_credential)
        identity.rotated_at = now
        identity.rotation_count = identity.rotation_count + 1
        identity.expires_at = cls._expiry(now)
        identity.save(
            update_fields=[
                "previous_credential_hash",
                "credential_hash",
                "rotated_at",
                "rotation_count",
                "expires_at",
                "updated_at",
            ]
        )
        logger.info("Rotated credential for guest identity %s", identity.guest_id)
        return raw_credential

    @classmethod
    def revoke(cls, identity: GuestIdentity) -> None:
        now = timezone.now()
        GuestIdentity.objects.filter(pk=identity.pk).update(
            status=GuestIdentity.Status.REVOKED,
            revoked_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # Legacy localStorage transition
    # ------------------------------------------------------------------
    @staticmethod
    def legacy_migration_state():
        """
        Return ``(enabled, reason)``. The endpoint is closed unless the flag is
        on *and* the cutoff has not passed, so forgetting to flip the flag off
        still fails closed on the cutoff date.
        """
        if not settings.GUEST_LEGACY_MIGRATION_ENABLED:
            return False, "legacy_migration_disabled"

        cutoff_raw = (settings.GUEST_LEGACY_MIGRATION_CUTOFF or "").strip()
        if not cutoff_raw:
            return False, "legacy_migration_cutoff_not_configured"

        try:
            cutoff = datetime.fromisoformat(cutoff_raw)
        except ValueError:
            logger.error("GUEST_LEGACY_MIGRATION_CUTOFF is not a valid ISO-8601 value; failing closed")
            return False, "legacy_migration_cutoff_invalid"

        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=dt_timezone.utc)

        if timezone.now() >= cutoff:
            return False, "legacy_migration_expired"

        return True, None

    @staticmethod
    def parse_legacy_session_id(value):
        """
        Strictly validate a legacy localStorage guest ID.

        Only the canonical lowercase hyphenated form of a real UUID is accepted;
        braced, urn: and hex-only spellings are rejected so one legacy ID cannot
        be presented in several forms.
        """
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            return None
        if str(parsed) != value.lower():
            return None
        return parsed

    @classmethod
    def migrate_legacy_session(cls, legacy_session_id: str) -> dict:
        """
        One-time exchange of a legacy localStorage guest ID for a server-minted
        credential.

        Deliberately *not* idempotent across browsers: because legacy IDs were
        bearer credentials, only the first caller may claim one. A second
        attempt is refused rather than handed a second credential.
        """
        enabled, reason = cls.legacy_migration_state()
        if not enabled:
            return {"status": "fail", "data": reason}

        parsed = cls.parse_legacy_session_id(legacy_session_id)
        if parsed is None:
            return {"status": "fail", "data": "invalid_legacy_session_id"}

        owner_key = str(parsed)

        if GuestIdentity.objects.filter(guest_id=parsed).exists():
            return {"status": "fail", "data": "legacy_session_already_migrated"}

        quota = GuestTokenQuota.objects.filter(session_id=owner_key).first()
        if quota is not None and quota.claimed_by_id is not None:
            return {"status": "fail", "data": "legacy_session_already_claimed"}

        has_conversations = Conversation.objects.filter(
            guest_session_id=owner_key,
            user__isnull=True,
        ).exists()
        if quota is None and not has_conversations:
            # Nothing to preserve — the caller should simply start a fresh guest.
            return {"status": "fail", "data": "legacy_session_not_found"}

        try:
            with transaction.atomic():
                identity, raw_credential = cls.mint(guest_id=parsed, legacy_session_id=owner_key)
        except IntegrityError:
            # Concurrent migration of the same legacy ID; the first one wins.
            return {"status": "fail", "data": "legacy_session_already_migrated"}

        logger.info("Migrated legacy guest session to identity %s", identity.guest_id)
        return {
            "status": "success",
            "data": {"identity": identity, "raw_credential": raw_credential},
        }
