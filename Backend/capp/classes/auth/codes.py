"""Versioned, keyed OTP digests; no raw six-digit secrets at rest.

HMAC covers the legacy SHA256(code) digest so existing verification rows can
be upgraded without knowing their live codes. User and purpose bind each hash
to its record context. Key rotation invalidates outstanding codes (10/15 min).
"""

import hashlib
import hmac
import secrets

from django.conf import settings


def generate_code(length=6):
    return "".join(secrets.choice("0123456789") for _ in range(length))


def digest_legacy_hash(code_hash, user_id, purpose):
    key = getattr(settings, "AUTH_CODE_HMAC_KEY", None) or settings.SECRET_KEY
    payload = f"uwenzai:otp:v1:{purpose}:{user_id}:{code_hash}".encode()
    return "h1$" + hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()


def digest_code(code, user_id, purpose):
    return digest_legacy_hash(hashlib.sha256(code.encode()).hexdigest(), user_id, purpose)


def code_matches(code, stored, user_id, purpose):
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    return hmac.compare_digest(digest_code(code, user_id, purpose), stored)
