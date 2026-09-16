"""
Media storage backend selection for Railway's S3-compatible Bucket.

Railway Buckets are private, S3-compatible object stores. Both the web service
and the Celery worker read/write the SAME bucket using the SAME credentials, so
media written by one service is immediately readable by the other. This replaces
the earlier local-volume approach where the worker could not see avatars written
by the web service.

Configuration is env-driven and uses the conventional AWS/django-storages
variable names so no secrets, endpoints, bucket names or regions are hardcoded:

    AWS_STORAGE_BUCKET_NAME   bucket name (canonical)
    AWS_ACCESS_KEY_ID         access key
    AWS_SECRET_ACCESS_KEY     secret key
    AWS_S3_ENDPOINT_URL       S3-compatible endpoint (Railway Bucket endpoint)
    AWS_S3_REGION_NAME        region (optional, default "auto")

Optional tuning (all have safe defaults):

    AWS_S3_ADDRESSING_STYLE   "path" (default) or "virtual"
    AWS_QUERYSTRING_EXPIRE    presigned-URL lifetime in seconds (default 3600)

Compatibility alias:

    BUCKET_NAME               Temporary fallback for the bucket name ONLY, used
                              when AWS_STORAGE_BUCKET_NAME is unset (some Railway
                              Bucket setups inject the name as BUCKET_NAME). The
                              canonical AWS_STORAGE_BUCKET_NAME always wins when
                              both are set. Prefer the canonical name; this alias
                              exists so operators need not duplicate the value.

Per-service opt-out (production only):

    MEDIA_STORAGE_VALIDATION   Set to "off" on a service that runs this settings
                               module but never touches media (e.g. the agent
                               service). The service then boots with a local
                               media backend and a loud log warning instead of
                               failing closed — no bucket credentials needed on
                               a service that has no business reading or
                               writing media. Default (unset): fail closed.

Behaviour:
  * All four required vars present  -> private S3 bucket backend.
  * Some but not all present        -> misconfiguration: raise in production,
                                        warn + local fallback in dev/test.
  * None present                    -> raise in production, local filesystem in
                                        dev/test.
  * Production + MEDIA_STORAGE_VALIDATION=off
                                    -> local backend + warning (explicit
                                        per-service opt-out for media-less
                                        services).

Production fails closed in both the partial and the absent case. There is no
longer a Railway volume behind ``MEDIA_ROOT``, so a local-filesystem fallback in
production would write uploads onto an ephemeral container filesystem that the
Celery worker cannot read and that is lost on every redeploy.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Env var name for each logical setting. Conventional AWS-compatible names are
# used so Railway variables can be mapped to them directly (see PR / deploy docs).
_BUCKET = "AWS_STORAGE_BUCKET_NAME"
# Temporary compatibility alias for the bucket name only (see module docstring).
# Canonical _BUCKET takes precedence whenever it is set.
_BUCKET_ALIAS = "BUCKET_NAME"
_ACCESS = "AWS_ACCESS_KEY_ID"
_SECRET = "AWS_SECRET_ACCESS_KEY"
_ENDPOINT = "AWS_S3_ENDPOINT_URL"
# Explicit per-service opt-out of the production fail-closed check, for services
# that run this settings module but never touch media (e.g. the agent service).
# Off is opt-in per service; the default remains fail-closed.
_VALIDATION = "MEDIA_STORAGE_VALIDATION"
_REGION = "AWS_S3_REGION_NAME"
_ADDRESSING = "AWS_S3_ADDRESSING_STYLE"
_EXPIRE = "AWS_QUERYSTRING_EXPIRE"

_REQUIRED = (_BUCKET, _ACCESS, _SECRET, _ENDPOINT)
_REQUIRED_LIST = ", ".join(_REQUIRED)

S3_BACKEND = "storages.backends.s3.S3Storage"
LOCAL_BACKEND = "django.core.files.storage.FileSystemStorage"
STATIC_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def resolve_bucket_config(env: Mapping[str, str]) -> Dict[str, Any]:
    """
    Inspect ``env`` and report bucket configuration completeness.

    Returns a dict with:
      complete (bool), partial (bool), missing (list[str]),
      present (list[str]), values (dict of the resolved required values).
    """
    values = {key: _clean(env.get(key)) for key in _REQUIRED}
    # Bucket-name compatibility: fall back to BUCKET_NAME only when the canonical
    # AWS_STORAGE_BUCKET_NAME is unset, so operators need not duplicate the value.
    # Completeness/error reporting still uses the canonical name.
    if not values[_BUCKET]:
        values[_BUCKET] = _clean(env.get(_BUCKET_ALIAS))
    present = [key for key, val in values.items() if val]
    missing = [key for key, val in values.items() if not val]
    complete = not missing
    partial = bool(present) and not complete
    return {
        "complete": complete,
        "partial": partial,
        "present": present,
        "missing": missing,
        "values": values,
    }


def _s3_backend(env: Mapping[str, str], cfg: Dict[str, Any]) -> Dict[str, Any]:
    values = cfg["values"]
    region = _clean(env.get(_REGION)) or "auto"
    addressing = _clean(env.get(_ADDRESSING)) or "path"

    try:
        expire = int(_clean(env.get(_EXPIRE)) or "3600")
    except ValueError:
        expire = 3600

    return {
        "BACKEND": S3_BACKEND,
        "OPTIONS": {
            "bucket_name": values[_BUCKET],
            "access_key": values[_ACCESS],
            "secret_key": values[_SECRET],
            "endpoint_url": values[_ENDPOINT],
            "region_name": region,
            # --- Privacy: objects are PRIVATE, never public. ---
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": expire,
            "signature_version": "s3v4",
            "addressing_style": addressing,
            # Never clobber an existing object with a same-named upload.
            "file_overwrite": False,
        },
    }


def _local_backend() -> Dict[str, Any]:
    return {"BACKEND": LOCAL_BACKEND}


def _storages(media: Dict[str, Any]) -> Dict[str, Any]:
    return {"default": media, "staticfiles": {"BACKEND": STATIC_BACKEND}}


def build_storages(
    env: Mapping[str, str],
    is_production: bool,
) -> Tuple[Dict[str, Any], str, Optional[str]]:
    """
    Build Django's ``STORAGES`` mapping.

    Returns ``(storages, media_mode, warning)`` where ``media_mode`` is either
    ``"s3"`` or ``"local"`` and ``warning`` is a human-readable string when the
    bucket configuration is incomplete in a non-production environment.

    Raises ``ImproperlyConfigured`` in production whenever the bucket is not
    fully configured — partially or not at all — so production never falls back
    to a local filesystem that no longer has durable storage behind it.
    """
    cfg = resolve_bucket_config(env)

    if cfg["complete"]:
        return _storages(_s3_backend(env, cfg)), "s3", None

    if cfg["partial"]:
        message = (
            "Railway Bucket storage is partially configured: present="
            f"{sorted(cfg['present'])} missing={sorted(cfg['missing'])}. "
            f"Set ALL of {_REQUIRED_LIST} on EVERY service running this "
            "codebase (web, Celery worker, agent service), or unset them all "
            "to use local media in development."
        )
    else:
        message = (
            "Railway Bucket storage is not configured. Production requires a "
            f"shared private bucket: set ALL of {_REQUIRED_LIST} on EVERY "
            "service running this codebase (web, Celery worker, agent "
            "service). Local media is only supported in development and tests."
        )

    if is_production:
        validation_off = _clean(env.get(_VALIDATION)).lower() in {"off", "false", "0"}
        if validation_off:
            # Explicitly opted out for a service that never touches media.
            # Any accidental media write would land on ephemeral container
            # disk, so keep the misconfiguration visible in the logs.
            logger.warning(
                "MEDIA_STORAGE_VALIDATION is off and the bucket is not fully "
                "configured. This service must never read or write media: "
                "uploads would go to ephemeral container storage. %s",
                message,
            )
            return _storages(_local_backend()), "local", message

        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(message)

    # An entirely absent configuration is the normal development default, so only
    # a *partial* configuration is surfaced as a warning.
    return _storages(_local_backend()), "local", (message if cfg["partial"] else None)
