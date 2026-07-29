# paystack_app/callbacks.py
"""
Server-owned resolution of the URL Paystack returns a customer to.

The redirect target is never taken from the client. A request may *select* one
of the paths the server has been configured to allow, and the absolute URL is
then rebuilt from the configured origin. So the client controls at most which
of a handful of known routes is used, and never the scheme, host, port,
credentials, query string or fragment.

A full URL is still accepted for compatibility with already-deployed frontends
that send ``${window.location.origin}/payment/verify``, but only its path is
honoured, and only after its origin matches the allowlist.
"""

import logging
from urllib.parse import unquote, urlsplit

from django.conf import settings

logger = logging.getLogger(__name__)

# A path is re-decoded before validation so that %2f..%2f and friends cannot
# smuggle a different route past the allowlist. Decoding is repeated until it
# is stable, bounded so a decoding bomb cannot spin here.
_MAX_DECODE_PASSES = 5


def allowed_origins() -> list:
    """Origins a callback may be built against, most-preferred first."""
    configured = list(getattr(settings, "PAYMENT_CALLBACK_ALLOWED_ORIGINS", []) or [])
    if configured:
        return configured
    return [origin.rstrip("/") for origin in getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []]


def allowed_paths() -> list:
    return list(getattr(settings, "PAYMENT_CALLBACK_ALLOWED_PATHS", []) or [])


def default_path() -> str:
    configured = getattr(settings, "PAYMENT_CALLBACK_DEFAULT_PATH", "") or ""
    if configured and configured in allowed_paths():
        return configured
    paths = allowed_paths()
    return paths[0] if paths else ""


def _fully_decode(value: str) -> str:
    for _ in range(_MAX_DECODE_PASSES):
        decoded = unquote(value)
        if decoded == value:
            return decoded
        value = decoded
    return value


def _looks_hostile(raw: str) -> bool:
    """
    Reject anything that is trying to leave the site, before it is parsed.

    Backslashes are rejected because browsers and ``urlsplit`` disagree about
    whether ``/\\evil.com`` is a path or an authority, and control characters
    are rejected because they are stripped by browsers, which likewise changes
    where the URL points.
    """
    if any(ch in raw for ch in ("\\", "\r", "\n", "\t", "\x00")):
        return True
    # Protocol-relative ("//evil.com") resolves to another host in a browser.
    if raw.startswith("//"):
        return True
    return False


def resolve_callback_path(requested) -> str:
    """
    Return the allowlisted path this request should come back to, or "".

    An empty return means "nothing usable was supplied"; the caller should fall
    back to :func:`default_path`. A value that was supplied but is *not*
    allowlisted also returns "", after logging — the request is never honoured
    and never silently upgraded to the default by this function's caller
    without that being an explicit decision.
    """
    if requested is None:
        return ""
    if not isinstance(requested, str):
        return ""

    raw = requested.strip()
    if not raw:
        return ""

    decoded = _fully_decode(raw)
    if _looks_hostile(raw) or _looks_hostile(decoded):
        logger.warning("SECURITY: payment callback rejected — unsafe characters in requested destination")
        return ""

    parts = urlsplit(decoded)

    if parts.scheme or parts.netloc:
        # A full URL: it must be plain http(s), carry no credentials, and sit on
        # an allowlisted origin. Only its path survives.
        if parts.scheme not in ("http", "https"):
            logger.warning("SECURITY: payment callback rejected — disallowed scheme %r", parts.scheme)
            return ""
        if parts.username or parts.password or "@" in parts.netloc:
            logger.warning("SECURITY: payment callback rejected — credentials in destination")
            return ""
        origin = f"{parts.scheme}://{parts.netloc}".rstrip("/")
        if origin.lower() not in {allowed.lower() for allowed in allowed_origins()}:
            logger.warning("SECURITY: payment callback rejected — origin not allowlisted")
            return ""

    if parts.query or parts.fragment:
        logger.warning("SECURITY: payment callback rejected — query or fragment in destination")
        return ""

    path = parts.path or ""
    if not path.startswith("/"):
        logger.warning("SECURITY: payment callback rejected — destination is not an absolute path")
        return ""
    if ".." in path.split("/"):
        logger.warning("SECURITY: payment callback rejected — traversal segment in destination")
        return ""

    normalised = path.rstrip("/") or "/"
    for candidate in allowed_paths():
        if normalised == (candidate.rstrip("/") or "/"):
            return candidate

    logger.warning("SECURITY: payment callback rejected — destination is not an allowlisted route")
    return ""


def build_callback_url(path: str) -> str:
    """
    Build the absolute callback URL for an allowlisted path.

    Returns "" when no origin is configured, in which case no ``callback_url``
    is sent to Paystack at all and the dashboard-configured default applies.
    """
    if not path or path not in allowed_paths():
        return ""
    origins = allowed_origins()
    if not origins:
        return ""
    return f"{origins[0].rstrip('/')}{path}"
