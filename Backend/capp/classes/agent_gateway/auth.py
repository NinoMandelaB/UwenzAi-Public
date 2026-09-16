"""
Agent Gateway Authentication

Custom DRF authentication class for UwenzAi API keys.
Authenticates requests with "Authorization: Bearer uwenzai-dev-..." headers.
"""

import hashlib
import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.throttling import BaseThrottle

from capp.models import AgentAPIKey, CustomUser
from capp.authentication import account_can_authenticate

logger = logging.getLogger(__name__)


class AgentGatewayAuthentication(BaseAuthentication):
    """
    Authentication class for Agent Gateway API keys.

    Extracts the API key from the Authorization header, hashes it,
    and looks up the corresponding AgentAPIKey record.

    A cache-backed limiter caps FAILED key lookups per client address.
    DRF runs view throttles only AFTER authentication succeeds, so they
    cannot protect the indexed DB lookup below against unlimited
    unauthenticated attempts; this guard runs before the lookup. Key
    brute-force is infeasible (~165-bit keys), but without this limit a
    single host can generate unbounded hashing + DB work and flood logs.

    Usage:
        In views, add to authentication_classes:
        authentication_classes = [AgentGatewayAuthentication]
    """

    keyword = "Bearer"
    scheme = "bearer"

    def _get_key_from_header(self, request) -> str | None:
        """
        Extract the API key from the Authorization header.

        Expects: Authorization: Bearer uwenzai-dev-xxxxx...
        or:      Authorization: Bearer uwenzai-live-xxxxx...
        """
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth:
            return None

        parts = auth.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword.lower():
            return None

        return parts[1]

    def _compute_key_hash(self, key: str) -> str:
        """
        Compute SHA-256 hash of the API key.

        Args:
            key: The plaintext API key

        Returns:
            Hex-encoded SHA-256 hash
        """
        return hashlib.sha256(key.encode()).hexdigest()

    def _get_key_record(self, key_hash: str) -> AgentAPIKey | None:
        """
        Look up the AgentAPIKey record by hash.

        Args:
            key_hash: SHA-256 hash of the key

        Returns:
            AgentAPIKey record or None if not found
        """
        try:
            return AgentAPIKey.objects.select_related("user").get(key_hash=key_hash)
        except AgentAPIKey.DoesNotExist:
            return None

    def authenticate(self, request) -> tuple[CustomUser, Any] | None:
        """
        Authenticate the request using an Agent Gateway API key.

        Args:
            request: The DRF request object

        Returns:
            Tuple of (user, auth_info) if authenticated, None otherwise

        Raises:
            AuthenticationFailed: If key is invalid, revoked, or expired
        """
        # Fail-closed limiter for repeated failed lookups from one address.
        # Runs BEFORE any hashing or DB work; successful auth never touches it.
        self._enforce_auth_failure_limit(request)

        raw_key = self._get_key_from_header(request)
        if raw_key is None:
            return None

        # Compute hash for lookup
        key_hash = self._compute_key_hash(raw_key)

        # Look up the key record
        key_record = self._get_key_record(key_hash)
        if key_record is None:
            # Debug, not warning: this path is attacker-driven and would
            # otherwise flood logs one line per brute-force attempt.
            logger.debug(
                "Agent Gateway: authentication failed - unknown key hash: %s",
                key_hash[:16],  # Log only first 16 chars of hash
            )
            self._record_auth_failure(request)
            raise AuthenticationFailed(
                detail={
                    "status": "invalid_api_key",
                    "message": "Invalid API key",
                },
                code="invalid_api_key",
            )

        if not account_can_authenticate(key_record.user):
            raise AuthenticationFailed("Account is unavailable.", code="user_inactive")

        # Check if key is active
        if not key_record.is_active:
            logger.warning(
                "Agent Gateway: authentication failed - revoked key: %s",
                key_record.agent_api_key_id,
            )
            raise AuthenticationFailed(
                detail={
                    "status": "invalid_api_key",
                    "message": "API key has been revoked",
                },
                code="invalid_api_key",
            )

        # Check if key is expired
        if key_record.is_expired():
            logger.warning(
                "Agent Gateway: authentication failed - expired key: %s",
                key_record.agent_api_key_id,
            )
            raise AuthenticationFailed(
                detail={
                    "status": "invalid_api_key",
                    "message": "API key has expired",
                },
                code="invalid_api_key",
            )

        # Update last_used_at (throttled to once per minute to avoid write-per-request)
        now = timezone.now()
        if key_record.last_used_at is None or (now - key_record.last_used_at).total_seconds() > 60:
            key_record.last_used_at = now
            key_record.save(update_fields=["last_used_at", "updated_at"])

        # Return the user and key record as auth info
        return (key_record.user, key_record)

    # Cache-backed failed-lookup limiter, keyed by client address only.
    # Limits are per address (not per key): a valid key from another
    # address is never affected, and failures expire via the cache TTL.
    # Read at call time (not import time) so tests/overrides apply cleanly.

    @staticmethod
    def _client_ident(request) -> str:
        # get_ident honours NUM_PROXIES / X-Forwarded-For consistently with
        # the DRF throttles used elsewhere in this project.
        return BaseThrottle().get_ident(request) or "unknown"

    def _fail_cache_key(self, request) -> str:
        return "agent_gateway:auth_fail:{}".format(self._client_ident(request))

    @property
    def auth_failure_limit(self) -> int:
        return int(getattr(settings, "AGENT_AUTH_FAILURE_LIMIT", 20))

    def _enforce_auth_failure_limit(self, request) -> None:
        """Raise Throttled once an address exceeded the failed-lookup limit.

        Runs before any DB access, so a flooding address costs nothing
        beyond a cache read. Cache errors never block authentication.
        """
        if self.auth_failure_limit <= 0:
            return
        try:
            fails = cache.get(self._fail_cache_key(request))
        except Exception:  # pragma: no cover - cache backend unavailable
            return
        if fails is not None and fails >= self.auth_failure_limit:
            raise Throttled(
                detail={
                    "status": "rate_limited",
                    "message": (
                        "Too many failed authentication attempts from this address. " "Wait and try again later."
                    ),
                }
            )

    def _record_auth_failure(self, request) -> None:
        """Count one failed key lookup against the client address."""
        if self.auth_failure_limit <= 0:
            return
        cache_key = self._fail_cache_key(request)
        try:
            try:
                # incr() keeps the original TTL: the window does not extend
                # while an attacker keeps hammering.
                cache.incr(cache_key)
            except ValueError:
                cache.add(
                    cache_key,
                    1,
                    timeout=int(getattr(settings, "AGENT_AUTH_FAILURE_WINDOW_SECONDS", 900)),
                )
        except Exception:  # pragma: no cover - cache backend unavailable
            return

    def authenticate_header(self, request) -> str:
        """
        Return the authentication header for 401 responses.

        Args:
            request: The DRF request object

        Returns:
            Authentication header string
        """
        return f'{self.keyword} realm="UwenzAi Agent Gateway"'


def generate_api_key(prefix: str = "dev") -> str:
    """
    Generate a new UwenzAi API key.

    Format: uwenzai-{prefix}-{random_32_chars}

    Args:
        prefix: Prefix for the key (e.g., "dev", "live", "test")

    Returns:
        New API key string
    """
    import secrets
    import string

    # Generate 32 random characters (URL-safe)
    alphabet = string.ascii_lowercase + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(32))

    return f"uwenzai-{prefix}-{random_part}"


def hash_api_key(key: str) -> str:
    """
    Compute SHA-256 hash of an API key.

    Args:
        key: The plaintext API key

    Returns:
        Hex-encoded SHA-256 hash
    """
    return hashlib.sha256(key.encode()).hexdigest()


def get_key_hint(key: str) -> str:
    """
    Get the last 4 characters of an API key for display.

    Args:
        key: The plaintext API key

    Returns:
        Last 4 characters
    """
    return key[-4:]
