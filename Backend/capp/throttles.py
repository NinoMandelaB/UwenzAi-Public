import hashlib
import logging

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle

logger = logging.getLogger(__name__)


class BaseScopedAnonThrottle(AnonRateThrottle):
    def _normalize(self, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _hash_value(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def get_request_identifier(self, request):
        return None

    def get_cache_key(self, request, view):
        if self.rate is None:
            return None

        ip_ident = self.get_ident(request)
        request_ident = self.get_request_identifier(request)

        if request_ident:
            combined_ident = f"{ip_ident}:{self._hash_value(request_ident.lower())}"  # noqa: E231
        else:
            combined_ident = ip_ident

        return self.cache_format % {
            "scope": self.scope,
            "ident": combined_ident,
        }


class ChatMessageThrottle(BaseScopedAnonThrottle):
    """
    Rate limiting for chat message sending.
    - Authenticated users: identified by user ID (hashed with IP for safety).
    - Anonymous guests: identified by client IP only. The client-supplied
      session_id is rotatable and must NOT be used to key this cost-bearing
      endpoint, otherwise the limit is trivially bypassable.
    """

    scope = "chat_message"
    rate = "10/minute"  # adjust as needed

    def get_request_identifier(self, request):
        if request.user.is_authenticated:
            return str(request.user.pk)
        # Guests: fall back to IP-only keying (get_ident already honours
        # NUM_PROXIES / X-Forwarded-For), so return no extra identifier.
        return None


class GuestSessionThrottle(BaseScopedAnonThrottle):
    """
    Limits how often a caller may ask for a *new* guest identity. Keyed on IP
    only: a caller with no valid cookie has no identity to key on yet, and that
    is exactly the request worth limiting.
    """

    scope = "guest_session"


class GuestLegacyMigrationThrottle(BaseScopedAnonThrottle):
    """
    Deliberately tight. The legacy endpoint accepts an ID that used to be a
    bearer credential, so it is the one place where guessing has any value.
    """

    scope = "guest_legacy_migration"


class GuestWebSocketTicketThrottle(BaseScopedAnonThrottle):
    """
    Bounds ticket minting per guest credential (hashed by the base class), so one
    guest cannot mint tickets without limit while legitimate reconnects on shared
    IPs still work.
    """

    scope = "guest_ws_ticket"

    def get_request_identifier(self, request):
        if request.user.is_authenticated:
            return str(request.user.pk)
        return self._normalize(request.COOKIES.get(settings.GUEST_CREDENTIAL_COOKIE_NAME))


class SignupThrottle(BaseScopedAnonThrottle):
    scope = "signup"

    def get_request_identifier(self, request):
        username = self._normalize(request.data.get("username"))
        email = self._normalize(request.data.get("email"))

        parts = []
        if username:
            parts.append(username.lower())
        if email:
            parts.append(email.lower())

        return "|".join(parts) if parts else None


class SigninThrottle(BaseScopedAnonThrottle):
    scope = "signin"

    def get_request_identifier(self, request):
        username = self._normalize(request.data.get("username"))
        return username.lower() if username else None


class GoogleAuthThrottle(BaseScopedAnonThrottle):
    scope = "google_auth"

    def get_request_identifier(self, request):
        credential = self._normalize(request.data.get("credential"))
        return credential


class RefreshTokenThrottle(BaseScopedAnonThrottle):
    scope = "refresh_token"

    def get_request_identifier(self, request):
        cookie_name = settings.JWT_REFRESH_COOKIE_NAME
        refresh_token = request.COOKIES.get(cookie_name)

        if not refresh_token:
            refresh_token = request.data.get("refresh_token")

        return self._normalize(refresh_token)


class SignoutThrottle(BaseScopedAnonThrottle):
    scope = "signout"

    def get_request_identifier(self, request):
        cookie_name = settings.JWT_REFRESH_COOKIE_NAME
        refresh_token = request.COOKIES.get(cookie_name)

        if not refresh_token:
            refresh_token = request.data.get("refresh_token")

        return self._normalize(refresh_token)


class RequestPasswordResetThrottle(BaseScopedAnonThrottle):
    scope = "request_password_reset"

    def get_request_identifier(self, request):
        email = self._normalize(request.data.get("email"))
        return email.lower() if email else None


class ConfirmPasswordResetThrottle(BaseScopedAnonThrottle):
    scope = "confirm_password_reset"

    def get_request_identifier(self, request):
        email = self._normalize(request.data.get("email"))
        code = self._normalize(request.data.get("code"))

        parts = []
        if email:
            parts.append(email.lower())
        if code:
            parts.append(code)

        return "|".join(parts) if parts else None


class AgentGatewayKeyThrottle(BaseScopedAnonThrottle):
    """
    Rate limiting for Agent Gateway requests, keyed by the API key.

    Each API key gets its own rate limit to prevent one key from being
    abused without affecting other keys or the user's regular app usage.
    """

    scope = "agent_gateway_key"
    rate = "60/minute"  # Adjust as needed for agent workloads

    def get_request_identifier(self, request):
        # The agent gateway uses Bearer token auth with uwenzai-dev-* keys
        # Get the API key from the Authorization header
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:].strip()  # Remove "Bearer " prefix
            # Hash the API key for the cache key (don't store plaintext)
            return self._hash_value(api_key)
        return None


class AgentPlaygroundUserThrottle(BaseScopedAnonThrottle):
    """Browser calls count against the account, never a rotatable key."""

    scope = "agent_playground_user"
    rate = "20/minute"

    def get_request_identifier(self, request):
        return str(request.user.pk)

    def get_cache_key(self, request, view):
        # Account-scoped: rotating IP addresses or API keys cannot reset this limit.
        return "agent-playground-throttle:{}".format(request.user.pk)

    def allow_request(self, request, view):
        from capp.classes.agent_gateway.guards import GatewayUnavailable

        try:
            return super().allow_request(request, view)
        except Exception as exc:
            raise GatewayUnavailable() from exc
