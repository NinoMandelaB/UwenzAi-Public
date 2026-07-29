"""Response-cookie helpers for the anonymous guest credential."""

from django.conf import settings


def set_guest_cookie(response, raw_credential: str):
    response.set_cookie(
        key=settings.GUEST_CREDENTIAL_COOKIE_NAME,
        value=raw_credential,
        max_age=settings.GUEST_CREDENTIAL_COOKIE_AGE,
        httponly=settings.GUEST_CREDENTIAL_COOKIE_HTTPONLY,
        secure=settings.GUEST_CREDENTIAL_COOKIE_SECURE,
        samesite=settings.GUEST_CREDENTIAL_COOKIE_SAMESITE,
        domain=settings.GUEST_CREDENTIAL_COOKIE_DOMAIN,
        path=settings.GUEST_CREDENTIAL_COOKIE_PATH,
    )
    return response


def clear_guest_cookie(response):
    response.delete_cookie(
        key=settings.GUEST_CREDENTIAL_COOKIE_NAME,
        samesite=settings.GUEST_CREDENTIAL_COOKIE_SAMESITE,
        domain=settings.GUEST_CREDENTIAL_COOKIE_DOMAIN,
        path=settings.GUEST_CREDENTIAL_COOKIE_PATH,
    )
    return response
