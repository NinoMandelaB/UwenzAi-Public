"""One live account eligibility rule for HTTP, refresh and WebSockets."""

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


def account_can_authenticate(user):
    return bool(user and user.is_active and not user.is_deleted and user.account_status not in ("banned", "suspended"))


class AccountJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if not account_can_authenticate(user):
            raise AuthenticationFailed("Account is unavailable.", code="user_inactive")
        return user


class OptionalAccountJWTAuthentication(AccountJWTAuthentication):
    """Treat an expired, revoked or malformed bearer token as anonymous.

    Only for endpoints whose effect is driven by the refresh cookie (sign-out):
    a client whose access token lapsed must still be able to blacklist the
    cookie. Anonymous requests keep the guest CSRF requirement.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except AuthenticationFailed:
            return None
