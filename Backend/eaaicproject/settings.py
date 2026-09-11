"""
Django settings for eaaicproject project.
"""

import os
import ipaddress
from urllib.parse import urlsplit
from pathlib import Path
from datetime import timedelta
from celery.schedules import crontab
from kombu import Exchange, Queue

import dj_database_url
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# ------------------------------------------------------------------------------
# Base
# ------------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

env_file = BASE_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file)


# ------------------------------------------------------------------------------
# Environment helpers
# ------------------------------------------------------------------------------
ENV_TRUE_VALUES = frozenset({"true", "1", "yes", "y", "on", "t"})
ENV_FALSE_VALUES = frozenset({"false", "0", "no", "n", "off", "f"})


def env_bool(name: str, default: bool = False) -> bool:
    """
    Read a boolean from the environment, or raise if it is not a boolean.

    Unset and empty both fall back to ``default``: a platform that exports a
    variable with no value has not expressed an opinion, and reading that as
    False would silently switch off a flag whose default is True.

    An unrecognised spelling raises instead of returning False. Several callers
    here gate security behaviour, and a typo (``ture``, ``enabled``) that reads
    as False disables the protection with no other symptom.
    """
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if not value:
        return default
    if value in ENV_TRUE_VALUES:
        return True
    if value in ENV_FALSE_VALUES:
        return False

    raise ImproperlyConfigured(
        f"{name} must be a boolean (one of {sorted(ENV_TRUE_VALUES | ENV_FALSE_VALUES)}); got {raw!r}"
    )


# ------------------------------------------------------------------------------
# Core
# ------------------------------------------------------------------------------
DEBUG = env_bool("DEBUG", False)
IS_PRODUCTION = not DEBUG

MAIL_SERVER = os.getenv("MAIL_SERVER")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_FROM = os.getenv("MAIL_FROM")
MAIL_USE_TLS = env_bool("MAIL_USE_TLS", True)

GOOGLE_WEB_CLIENT_ID = os.getenv("GOOGLE_WEB_CLIENT_ID")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY")
MESSAGE_ENCRYPTION_KEY = os.getenv("MESSAGE_ENCRYPTION_KEY")

# Web Push (VAPID). Both keys unset disables the feature cleanly: the
# public-key endpoint answers 503 and every send is a no-op, so environments
# without keys (CI, local dev) keep working. Generate a pair with
# `vapid --gen` (ships with py-vapid). The public key is safe to expose —
# the private key must never leave the server.
WEBPUSH_VAPID_PUBLIC_KEY = os.getenv("WEBPUSH_VAPID_PUBLIC_KEY", "")
WEBPUSH_VAPID_PRIVATE_KEY = os.getenv("WEBPUSH_VAPID_PRIVATE_KEY", "")
WEBPUSH_VAPID_SUBJECT = os.getenv("WEBPUSH_VAPID_SUBJECT", "mailto:support@uwenzai.com")
WEBPUSH_ALLOWED_ORIGINS = tuple(
    origin.strip().lower().rstrip("/")
    for origin in os.getenv(
        "WEBPUSH_ALLOWED_ORIGINS",
        "https://fcm.googleapis.com," "https://updates.push.services.mozilla.com," "https://web.push.apple.com",
    ).split(",")
    if origin.strip()
)
WEBPUSH_TIMEOUT_SECONDS = max(1.0, min(float(os.getenv("WEBPUSH_TIMEOUT_SECONDS", "5")), 15.0))
WEBPUSH_MAX_SUBSCRIPTIONS_PER_USER = max(1, min(int(os.getenv("WEBPUSH_MAX_SUBSCRIPTIONS_PER_USER", "10")), 20))

STABILITY_API_KEY = os.getenv("STABILITY_API_KEY")
STABILITY_API_URL = os.getenv("STABILITY_API_URL", "https://api.stability.ai/v2beta/stable-image/generate/core")
STABILITY_MODEL = os.getenv("STABILITY_MODEL", "sd3.5-large-turbo")

FAL_API_KEY = os.getenv("FAL_API_KEY", "")
# fal.ai is the primary image-generation provider (Stability AI is fallback only).
# Model identifiers are centralised here and env-overridable so future swaps do
# not require code changes. Defaults point at ByteDance Seedream v5 Lite.
FAL_API_BASE_URL = os.getenv("FAL_API_BASE_URL", "https://fal.run")
FAL_TEXT_TO_IMAGE_MODEL = os.getenv("FAL_TEXT_TO_IMAGE_MODEL", "bytedance/seedream/v5/lite/text-to-image")
FAL_IMAGE_EDIT_MODEL = os.getenv("FAL_IMAGE_EDIT_MODEL", "bytedance/seedream/v5/lite/edit")
FAL_IMAGE_TIMEOUT = float(os.getenv("FAL_IMAGE_TIMEOUT", "120"))
FAL_IMAGE_MAX_RESPONSE_BYTES = max(
    1_000_000, min(int(os.getenv("FAL_IMAGE_MAX_RESPONSE_BYTES", str(15 * 1024 * 1024))), 25 * 1024 * 1024)
)
FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS = tuple(
    host.strip().lower().rstrip(".")
    for host in os.getenv("FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS", "fal.media").split(",")
    if host.strip()
)
IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS = max(
    30.0,
    min(float(os.getenv("IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS", "180")), 300.0),
)
IMAGE_BILLING_DB_MAX_WAIT_SECONDS = max(
    0.05,
    min(float(os.getenv("IMAGE_BILLING_DB_MAX_WAIT_SECONDS", "2")), 5.0),
)
IMAGE_BILLING_RECOVERY_GRACE_SECONDS = max(
    30,
    min(int(os.getenv("IMAGE_BILLING_RECOVERY_GRACE_SECONDS", "60")), 600),
)
IMAGE_BILLING_RECOVERY_BATCH_SIZE = max(
    1,
    min(int(os.getenv("IMAGE_BILLING_RECOVERY_BATCH_SIZE", "100")), 500),
)
IMAGE_IO_RECYCLE_PROCESS_ON_TIMEOUT = os.getenv(
    "IMAGE_IO_RECYCLE_PROCESS_ON_TIMEOUT",
    "true" if IS_PRODUCTION else "false",
).lower() in ("1", "true", "yes", "on")
IMAGE_TASK_SOFT_TIME_LIMIT = max(
    int(IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS) + 10,
    int(os.getenv("IMAGE_TASK_SOFT_TIME_LIMIT", "210")),
)
IMAGE_TASK_TIME_LIMIT = max(
    IMAGE_TASK_SOFT_TIME_LIMIT + 1,
    int(os.getenv("IMAGE_TASK_TIME_LIMIT", str(IMAGE_TASK_SOFT_TIME_LIMIT + 20))),
)
# Payload knobs consumed by capp.classes.ai.fal_capabilities.build_fal_payload.
# Default False preserves the long-standing product behaviour of sending
# enable_safety_checker=False; note fal only honours that for authorized
# accounts (unauthorized requests are always checked).
FAL_ENABLE_SAFETY_CHECKER = env_bool("FAL_ENABLE_SAFETY_CHECKER", False)
# String enum, only forwarded to endpoints whose capability entry supports it
# (the FLUX Pro family). "2" is the fal-side default; out-of-range values fall
# back to the endpoint default with a warning inside build_fal_payload.
FAL_SAFETY_TOLERANCE = os.getenv("FAL_SAFETY_TOLERANCE", "2")

CORTECS_API_KEY = os.getenv("CORTECS_API_KEY")
CORTECS_BASE_URL = os.getenv("CORTECS_BASE_URL", "https://api.cortecs.ai/v1")
CORTECS_MODEL = os.getenv("CORTECS_MODEL", "deepseek-v4-flash")
CORTECS_BACKUP_MODEL = os.getenv("CORTECS_BACKUP_MODEL", CORTECS_MODEL)
CORTECS_SWAHILI_MODEL = os.getenv("CORTECS_SWAHILI_MODEL", CORTECS_MODEL)
# Dedicated model for memory extraction. Defaults to a stronger tier than
# the chat model because prompt-adherence and structured-JSON quality are
# far more important here than latency or cost per message.
CORTECS_MEM_MODEL = os.getenv("CORTECS_MEM_MODEL", "deepseek-v4-flash-0731")
CORTECS_TIMEOUT = float(os.getenv("CORTECS_TIMEOUT", "60"))
CORTECS_EMBED_URL = os.getenv("CORTECS_EMBED_URL", "https://api.cortecs.ai/v1/embedding")
CORTECS_EMBED_MODEL = os.getenv("CORTECS_EMBED_MODEL", "qwen3-embedding-8b")

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = os.getenv("MISTRAL_API_URL")
MISTRAL_EMBED_BASE_URL = os.getenv("MISTRAL_EMBED_BASE_URL", "https://api.mistral.ai")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL")
MISTRAL_EMBED_MODEL = os.getenv("MISTRAL_EMBED_MODEL")
MISTRAL_TOKENIZER_MODEL = os.getenv("MISTRAL_TOKENIZER_MODEL")

# Web search tool (per-chat opt-in). If unset, search gracefully degrades:
# no tool is offered to the model and no outbound search calls are made.
#
# Staan (https://staan.ai) is the only provider: EU-based (France),
# GDPR-native, with EU-only routing available and no US CLOUD Act exposure,
# which fits UwenzAi's privacy positioning. If STAAN_API_KEY is missing,
# search gracefully degrades (no tool offered, no crash).
#
# SEARCH_PROVIDER is NOT a selection knob -- there is only one provider.
# It is read purely as a deprecation guard: a deployment that still sets it
# to something other than "staan" fails closed (see get_search_provider in
# capp/classes/search/base.py) rather than silently sending queries to a
# different processor than the operator asked for. Defaults to empty (the
# normal case) so an unset variable is never treated as a misconfiguration.
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "")

STAAN_API_KEY = os.getenv("STAAN_API_KEY")
STAAN_API_URL = os.getenv("STAAN_API_URL", "https://api.staan.ai/v2/search/web")
STAAN_TIMEOUT = float(os.getenv("STAAN_TIMEOUT", "10"))
STAAN_MARKET = os.getenv("STAAN_MARKET", "en-us").strip() or None
# Hard cap on search calls per user message (latency/cost bound).
SEARCH_MAX_CALLS_PER_MESSAGE = int(os.getenv("SEARCH_MAX_CALLS_PER_MESSAGE", "2"))

# open_url tool (fetch + read a single web page). Gated behind the same
# per-chat web_search_enabled toggle as web_search -- never offered on its
# own. See capp/classes/search/open_url.py for the SSRF/size/timeout policy
# these settings control.
OPEN_URL_TIMEOUT_SECONDS = float(os.getenv("OPEN_URL_TIMEOUT_SECONDS", "10"))
# Total wall-clock budget for one open_url fetch, spanning every redirect hop
# and the body read. OPEN_URL_TIMEOUT_SECONDS above is only an INACTIVITY
# timeout, so a server that sends one byte every 9s never trips it and would
# otherwise hold a Celery worker open indefinitely. Must stay comfortably
# below CELERY_TASK_SOFT_TIME_LIMIT so the fetch aborts itself before the
# whole AI task is killed.
OPEN_URL_TOTAL_TIMEOUT_SECONDS = float(os.getenv("OPEN_URL_TOTAL_TIMEOUT_SECONDS", "25"))
# Wall-clock ceilings for one AI reply task (capp.tasks.process_ai_response).
# The soft limit raises SoftTimeLimitExceeded inside the task so it can refund
# the Spark reservation and mark the message failed; the hard limit kills the
# worker process as a backstop. Keep the soft limit well above
# OPEN_URL_TOTAL_TIMEOUT_SECONDS so a slow page aborts itself and the user
# still gets a reply, and above the provider timeouts plus retry backoff.
AI_TASK_SOFT_TIME_LIMIT = int(os.getenv("AI_TASK_SOFT_TIME_LIMIT", "180"))
AI_TASK_TIME_LIMIT = int(os.getenv("AI_TASK_TIME_LIMIT", str(AI_TASK_SOFT_TIME_LIMIT + 20)))
OPEN_URL_MAX_RESPONSE_BYTES = int(os.getenv("OPEN_URL_MAX_RESPONSE_BYTES", str(2_000_000)))
OPEN_URL_MAX_EXTRACTED_CHARS = int(os.getenv("OPEN_URL_MAX_EXTRACTED_CHARS", "6000"))
OPEN_URL_MAX_REDIRECTS = int(os.getenv("OPEN_URL_MAX_REDIRECTS", "5"))
# Hard cap on open_url calls per user message (separate from, and stricter
# than, SEARCH_MAX_CALLS_PER_MESSAGE -- fetching full pages is far more
# expensive than a search snippet lookup).
OPEN_URL_MAX_CALLS_PER_MESSAGE = int(os.getenv("OPEN_URL_MAX_CALLS_PER_MESSAGE", "1"))
# Comma-separated list of domains (and subdomains) open_url must never fetch,
# in addition to the built-in SSRF protections (private/loopback/link-local
# IPs and cloud metadata endpoints are always blocked regardless of this
# list).
OPEN_URL_BLOCKED_DOMAINS = [d.strip() for d in os.getenv("OPEN_URL_BLOCKED_DOMAINS", "").split(",") if d.strip()]

# Paystack
PAYSTACK_BASE_URL = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_SECRET_KEY")

# Currencies we are willing to capture. A package priced in anything else is
# refused at initiation rather than silently charged in the wrong currency.
PAYSTACK_SUPPORTED_CURRENCIES = [
    code.strip().upper() for code in os.getenv("PAYSTACK_SUPPORTED_CURRENCIES", "KES").split(",") if code.strip()
]

# Where a customer may be returned to after Paystack. Only these exact paths on
# an allowlisted origin are ever used to build a redirect, so a client cannot
# steer the return anywhere else.
PAYMENT_CALLBACK_ALLOWED_PATHS = [
    path.strip() for path in os.getenv("PAYMENT_CALLBACK_ALLOWED_PATHS", "/payment/verify").split(",") if path.strip()
]
PAYMENT_CALLBACK_DEFAULT_PATH = os.getenv("PAYMENT_CALLBACK_DEFAULT_PATH", "/payment/verify")

# Origins the callback may be built against. Falls back to CORS_ALLOWED_ORIGINS
# (defined further down) when unset; the first entry is the origin the server
# uses when it builds the redirect itself.
payment_callback_origins = os.getenv("PAYMENT_CALLBACK_ALLOWED_ORIGINS", "")
PAYMENT_CALLBACK_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/") for origin in payment_callback_origins.split(",") if origin.strip()
]

GUEST_DAILY_TOKEN_LIMIT = int(os.getenv("GUEST_DAILY_TOKEN_LIMIT", "10000"))
GUEST_LIFETIME_TOKEN_LIMIT = int(os.getenv("GUEST_LIFETIME_TOKEN_LIMIT", "50000"))

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production.")

if not SECRET_KEY:
    SECRET_KEY = os.getenv("DJANGO_SPARE_SECRET_KEY")

ALLOWED_HOSTS = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()]

if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


# ------------------------------------------------------------------------------
# Installed apps
# ------------------------------------------------------------------------------
INSTALLED_APPS = [
    "channels",
    "corsheaders",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "paystack_app",
    "capp",
]


# ------------------------------------------------------------------------------
# Middleware
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "capp.middleware.PostgresWakeUpMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ------------------------------------------------------------------------------
# URL / ASGI / WSGI
# ------------------------------------------------------------------------------
ROOT_URLCONF = "eaaicproject.urls"
WSGI_APPLICATION = "eaaicproject.wsgi.application"
ASGI_APPLICATION = "eaaicproject.asgi.application"


# ------------------------------------------------------------------------------
# Templates
# ------------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ------------------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------------------
AUTH_USER_MODEL = "capp.CustomUser"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ------------------------------------------------------------------------------
# DRF / JWT
# ------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("capp.authentication.AccountJWTAuthentication",),
    "DEFAULT_PARSER_CLASSES": (
        "capp.request_limits.BoundedJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "120/min",
        "chat_message": "10/minute",
        "signup": "3/hour",
        "signin": "15/min",
        "google_auth": "10/hour",
        "refresh_token": "10/hour",
        "signout": "20/hour",
        "request_password_reset": "5/hour",
        "confirm_password_reset": "10/hour",
        "guest_session": "30/hour",
        "guest_legacy_migration": "5/hour",
        "guest_ws_ticket": "60/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "USER_ID_FIELD": "user_id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ------------------------------------------------------------------------------
# Cookie policy
# ------------------------------------------------------------------------------
# Local development over http://127.0.0.1:* should use Lax + non-secure cookies.
# Production cross-origin HTTPS should use None + Secure cookies.
if IS_PRODUCTION:
    COOKIE_SAMESITE = "None"
    COOKIE_SECURE = True
else:
    COOKIE_SAMESITE = "Lax"
    COOKIE_SECURE = False

# Refresh-token cookie settings
JWT_REFRESH_COOKIE_NAME = "refresh_token"
JWT_REFRESH_COOKIE_AGE = 7 * 24 * 60 * 60  # 7 days
JWT_REFRESH_COOKIE_HTTPONLY = True
JWT_REFRESH_COOKIE_SECURE = COOKIE_SECURE
JWT_REFRESH_COOKIE_SAMESITE = COOKIE_SAMESITE


# ------------------------------------------------------------------------------
# Anonymous guest identity
# ------------------------------------------------------------------------------
# Guests are identified by a server-minted, high-entropy opaque credential held
# in an HttpOnly cookie; only its SHA-256 hash is stored (capp.models.GuestIdentity).
# The cookie must survive browser restarts, so it is persistent rather than
# session-scoped. SameSite/Secure follow the same cross-origin policy as the
# refresh cookie: None+Secure in production, Lax+non-secure for local http dev.
GUEST_CREDENTIAL_COOKIE_NAME = os.getenv("GUEST_CREDENTIAL_COOKIE_NAME", "eaaic_guest")
GUEST_CREDENTIAL_COOKIE_AGE = int(os.getenv("GUEST_CREDENTIAL_COOKIE_AGE", str(180 * 24 * 60 * 60)))  # 180 days
GUEST_CREDENTIAL_COOKIE_HTTPONLY = True
GUEST_CREDENTIAL_COOKIE_SECURE = COOKIE_SECURE
GUEST_CREDENTIAL_COOKIE_SAMESITE = os.getenv("GUEST_CREDENTIAL_COOKIE_SAMESITE", COOKIE_SAMESITE)
# Leave unset for a host-only cookie (correct for the Railway split-domain
# deployment, where the API is its own host). Set to a registrable parent domain
# only if the API and frontend ever share one.
GUEST_CREDENTIAL_COOKIE_DOMAIN = os.getenv("GUEST_CREDENTIAL_COOKIE_DOMAIN") or None
GUEST_CREDENTIAL_COOKIE_PATH = "/"

# Idle expiry for a guest identity, refreshed on each resolved request.
GUEST_IDENTITY_TTL_SECONDS = int(os.getenv("GUEST_IDENTITY_TTL_SECONDS", str(180 * 24 * 60 * 60)))  # 180 days
# ``last_seen_at`` is only written when it is at least this stale, so ordinary
# guest traffic does not incur a row update per request.
GUEST_LAST_SEEN_REFRESH_SECONDS = int(os.getenv("GUEST_LAST_SEEN_REFRESH_SECONDS", "300"))

# Short-lived single-use WebSocket tickets (see capp.classes.guest.ws_ticket_service).
GUEST_WS_TICKET_TTL_SECONDS = int(os.getenv("GUEST_WS_TICKET_TTL_SECONDS", "30"))

# ------------------------------------------------------------------------------
# Legacy guest-ID transition (TEMPORARY — see Backend/docs/GUEST_IDENTITY.md)
# ------------------------------------------------------------------------------
# Production browsers hold a client-generated UUID in localStorage under
# "guest_session_id". Those UUIDs were bearer credentials, so accepting one is a
# known, bounded compatibility risk. The migration endpoint is therefore both
# feature-flagged and hard-stopped by a cutoff date, and the WebSocket query
# fallback is flagged separately so it can be switched off first.
GUEST_LEGACY_MIGRATION_ENABLED = env_bool("GUEST_LEGACY_MIGRATION_ENABLED", False)
GUEST_LEGACY_MIGRATION_CUTOFF = os.getenv("GUEST_LEGACY_MIGRATION_CUTOFF", "")
GUEST_LEGACY_WS_SESSION_ID_ENABLED = env_bool("GUEST_LEGACY_WS_SESSION_ID_ENABLED", False)


# ------------------------------------------------------------------------------
# Billing idempotency (see Backend/docs/BILLING_RESERVATIONS.md)
# ------------------------------------------------------------------------------
# Paid endpoints accept an Idempotency-Key header and mint one when it is
# missing, so browsers running a bundle from before this change keep working.
# A server-minted key still bills exactly once, but it cannot recognise a second
# POST as the same action. Turn this on once the frontend rollout has finished
# to reject keyless paid requests and close that window.
BILLING_REQUIRE_IDEMPOTENCY_KEY = env_bool("BILLING_REQUIRE_IDEMPOTENCY_KEY", False)


# ------------------------------------------------------------------------------
# Database
# Railway uses DATABASE_URL.
# Local dev uses DB_HOST / DB_PORT / DB_NAME / DB_USR / DB_PWD.
# ------------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=IS_PRODUCTION,
        )
    }
else:
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME")
    DB_USR = os.getenv("DB_USR")
    DB_PWD = os.getenv("DB_PWD")

    if not all([DB_NAME, DB_USR, DB_PWD]):
        raise RuntimeError(
            "Local database config is incomplete. " "Set DB_NAME, DB_USR, DB_PWD, and optionally DB_HOST, DB_PORT."
        )

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DB_NAME,
            "USER": DB_USR,
            "PASSWORD": DB_PWD,
            "HOST": DB_HOST,
            "PORT": DB_PORT,
            "CONN_MAX_AGE": 600,
        }
    }


# ------------------------------------------------------------------------------
# Timezone / i18n
# ------------------------------------------------------------------------------
TIME_ZONE = os.getenv("TIME_ZONE", "Africa/Nairobi")
LANGUAGE_CODE = "en-us"
USE_I18N = True
USE_TZ = True


# ------------------------------------------------------------------------------
# Redis / Celery
# ------------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

# worker_max_memory_per_child: after a child process has used this much
# resident memory it is retired and replaced (units are KiB). 500000 KiB ≈ 500 MB.
# This bounds leaks from long-running image jobs without killing tasks mid-run
# (the child is only recycled once the current task finishes). Tune per service
# via the CELERYD_MAX_MEMORY_PER_CHILD env var; leave unset for the safe default.
CELERYD_MAX_MEMORY_PER_CHILD = int(os.getenv("CELERYD_MAX_MEMORY_PER_CHILD", 500000))

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ------------------------------------------------------------------------------
# Celery queue isolation
# ------------------------------------------------------------------------------
# Two durable queues keep slow image generation from blocking fast interactive
# work (memory extraction, relationship updates, summaries, subscriptions,
# proactive chat, guest transfer):
#   - ``general``: default queue for every non-image task.
#   - ``images``:  only genuinely long-running image-generation tasks.
#
# Routing is declared centrally here so existing ``.delay()`` /
# ``.apply_async()`` call sites need no per-call ``queue=`` strings. A task with
# no explicit route falls through to CELERY_TASK_DEFAULT_QUEUE ("general"),
# which also covers Celery Beat periodic tasks unless they are image-related.
CELERY_TASK_DEFAULT_QUEUE = "general"

CELERY_TASK_ROUTES = {
    # Long-running fal.ai image generation — isolated so it never delays chat.
    "capp.tasks.generate_chat_image": {"queue": "images"},
    "capp.tasks.refund_billing_reservation": {"queue": "general"},
    "capp.tasks.reconcile_stale_image_reservations": {"queue": "general"},
}

# Explicitly declare both queues so a worker (or the broker) knows about them up
# front instead of relying solely on lazy auto-creation on first publish.
CELERY_TASK_QUEUES = (
    Queue("general", Exchange("general"), routing_key="general"),
    Queue("images", Exchange("images"), routing_key="images"),
)

# Concurrency is configured per worker service via the Procfile / Railway start
# command (see docs/DEPLOYMENT_QUEUES.md), not here, so the general and image
# workers can scale independently.


# ------------------------------------------------------------------------------
# Channels
# ------------------------------------------------------------------------------
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

# ------------------------------------------------------------------------------
# Django Cache (Redis-backed for throttling and shared cache use)
# ------------------------------------------------------------------------------
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "eaaic-local-cache",
        }
    }

# ------------------------------------------------------------------------------
# CORS
# Update these for your frontend domains in production.
# ------------------------------------------------------------------------------
CORS_ALLOW_CREDENTIALS = True


def env_origins(name: str) -> list[str]:
    """
    Read a comma-separated list of origins from the environment.

    Values are normalised so harmless formatting differences do not create
    duplicate or mismatched entries:
    - surrounding whitespace is stripped
    - trailing "/" is removed
    - empty items are ignored

    Expected format example:
        https://app.example.com,https://admin.example.com,http://localhost:5173
    """
    raw = os.getenv(name, "")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def browser_origins_for_environment(origins):
    """Production never trusts development loopback origins, including env entries."""
    if DEBUG and not IS_PRODUCTION:
        return origins
    allowed = []
    for value in origins:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            continue
        try:
            if ipaddress.ip_address(host).is_loopback:
                continue
        except ValueError:
            pass
        allowed.append(value)
    return allowed


CORS_ALLOWED_ORIGINS = (
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
    if DEBUG and not IS_PRODUCTION
    else []
)

# Optional: allow extra origins from env. These must be plain comma-separated
# origins, not markdown links, for example:
#   CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
CORS_ALLOWED_ORIGINS += env_origins("CORS_ALLOWED_ORIGINS")

# Deduplicate while preserving order, so a local default repeated in env does
# not appear twice.
CORS_ALLOWED_ORIGINS = browser_origins_for_environment(list(dict.fromkeys(CORS_ALLOWED_ORIGINS)))

# Every paid endpoint carries ``Idempotency-Key`` (capp.classes.billing.idempotency.HEADER).
# It is not in django-cors-headers' defaults, so omitting it here makes the browser
# fail the preflight and drop the request before it is ever sent — the API sees no
# traffic at all, which looks like a backend outage rather than a CORS policy. The
# frontend and API are separate Railway services, so every send is cross-origin.
# Keep this in sync with the frontend: any new custom request header must be added
# here too (asserted by capp.tests_chat_send_path).
CORS_ALLOW_HEADERS = (*default_headers, "idempotency-key")

# The server mints an operation ID when the client did not send one and echoes it
# back on the response. A cross-origin caller cannot read a response header unless
# it is explicitly exposed, so without this the echo is invisible and a retry
# cannot reuse the key it was handed.
CORS_EXPOSE_HEADERS = ["Idempotency-Key"]

# ------------------------------------------------------------------------------
# CSRF Trusted Origins
# Must include every frontend origin that sends unsafe requests (POST, PUT,
# PATCH, DELETE) to the API. This is separate from CORS: allowing a browser to
# make a cross-origin request does not automatically trust that origin for
# Django's CSRF protection.
# ------------------------------------------------------------------------------
#
# The plaintext-http localhost defaults are development-only. Trusting them in
# production would let anything able to present one of those Origin values —
# another process on the host, a rogue extension proxying through a local dev
# server — pass CSRF on an origin the deployment does not actually serve.
# Production therefore takes its trusted origins from the environment alone,
# which is how this setting behaved before the local defaults were introduced.
CSRF_TRUSTED_ORIGINS = (
    []
    if IS_PRODUCTION or not DEBUG
    else [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
)

# Optional: allow extra trusted origins from env. These must include the scheme
# ("http://" or "https://") because Django matches full origins, not bare hostnames.
# Example:
#   CSRF_TRUSTED_ORIGINS=https://app.example.com,https://admin.example.com
CSRF_TRUSTED_ORIGINS += env_origins("CSRF_TRUSTED_ORIGINS")

# Deduplicate while preserving order.
CSRF_TRUSTED_ORIGINS = browser_origins_for_environment(list(dict.fromkeys(CSRF_TRUSTED_ORIGINS)))

# Periodic tasks are pinned to the ``general`` queue so scheduled work is never
# starved behind image jobs. Add "queue": "images" here only for a genuinely
# image-generating periodic task.
CELERY_BEAT_SCHEDULE = {
    "retry-media-deletions": {
        "task": "capp.tasks.retry_media_deletions",
        "schedule": crontab(minute=15),
        "options": {"queue": "general"},
    },
    "expire-subscriptions-every-hour": {
        "task": "expire_subscriptions",
        "schedule": crontab(minute=0),  # every hour at minute 0
        "options": {"queue": "general"},
    },
    "check_proactive_chat": {
        "task": "capp.tasks.check_and_send_proactive_messages",
        "schedule": crontab(minute="*/30"),  # every 30 minutes
        # Expire stale runs, but give a busy general worker a chance: 60 s was
        # tight enough to silently drop runs whenever slower tasks (memory
        # extraction, summaries) occupied the queue. The next tick is 30 min
        # away, and the task itself now retries with backoff up to ~5 times
        # on a cold-starting DB (see capp/tasks.py), so this window needs to
        # comfortably outlast that retry sequence instead of discarding the
        # message before the retries even get a chance to run.
        "options": {"expires": 900, "queue": "general"},
    },
    "reconcile-stale-image-reservations": {
        "task": "capp.tasks.reconcile_stale_image_reservations",
        "schedule": crontab(minute="*"),
        "options": {"expires": 55, "queue": "general"},
    },
    "check_due_reminders": {
        "task": "capp.tasks.check_and_send_due_reminders",
        # Finer-grained than check_proactive_chat: a reminder is a precise
        # time commitment ("tomorrow at 9am"), not a loose re-engagement
        # window, so it should fire close to its due time. A due-but-
        # undelivered reminder is not lost even if this exact run is
        # discarded: reminder_service.check_and_send_due() re-queries
        # everything still PENDING and due, so the next 5-minute tick picks
        # it back up regardless (bounded by REMINDER_EXPIRY_GRACE_HOURS).
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 250, "queue": "general"},
    },
}
# ------------------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "capp": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        # ADD THIS:
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}

# ------------------------------------------------------------------------------
# Static / Media
# ------------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Only used by the development / test FileSystemStorage fallback. Production
# always stores media in the private bucket (the Railway volume was removed), and
# Django never serves MEDIA_ROOT outside DEBUG — see eaaicproject/urls.py.
MEDIA_ROOT = BASE_DIR / "media"

MEDIA_URL = "/media/"

# Media/static storage backends. Media is stored in the private Railway
# S3-compatible Bucket that BOTH the web service and the Celery worker share, so
# files written by one service are immediately readable by the other. In
# production an absent OR partial bucket configuration raises
# ImproperlyConfigured (fail closed) rather than silently writing media to an
# ephemeral container filesystem. The local filesystem fallback exists only for
# development and tests. See eaaicproject/storage_config.py.
from eaaicproject.storage_config import build_storages  # noqa: E402

STORAGES, MEDIA_STORAGE_MODE, _STORAGE_WARNING = build_storages(os.environ, IS_PRODUCTION)

if _STORAGE_WARNING:
    import logging

    logging.getLogger(__name__).warning(_STORAGE_WARNING)


# ------------------------------------------------------------------------------
# Upload limits
# ------------------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600

# 12,000 astral Unicode characters JSON-escaped take 144,000 bytes. Keep room
# for metadata while bounding JSON parsing/ASGI buffering independently of files.
JSON_REQUEST_MAX_BYTES = 196608
BYOK_PRIVATE_ORIGINS = env_origins("BYOK_PRIVATE_ORIGINS")
AUTH_CODE_HMAC_KEY = os.getenv("AUTH_CODE_HMAC_KEY") or SECRET_KEY


# ------------------------------------------------------------------------------
# Security / Proxy
# ------------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_NAME = "eaaic_sessionid"
CSRF_COOKIE_NAME = "eaaic_csrftoken"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = COOKIE_SAMESITE
SESSION_COOKIE_SECURE = COOKIE_SECURE

# False so Django can issue/read the token normally, and the frontend can still
# work with the token returned by the CSRF endpoint.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = COOKIE_SAMESITE
CSRF_COOKIE_SECURE = COOKIE_SECURE

X_FRAME_OPTIONS = "DENY"

if IS_PRODUCTION:
    SECURE_SSL_REDIRECT = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


# ------------------------------------------------------------------------------
# Error monitoring (Sentry) — opt-in, no-op unless SENTRY_DSN is set
# ------------------------------------------------------------------------------
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
            environment=os.getenv("SENTRY_ENV", "production"),
        )
    except Exception:  # noqa: BLE001 - never let monitoring setup crash the app
        import logging

        logging.getLogger(__name__).exception("Failed to initialize Sentry")


# ------------------------------------------------------------------------------
# Defaults
# ------------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
