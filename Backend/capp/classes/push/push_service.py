# capp/classes/push/push_service.py
"""
Web Push delivery (VAPID).

Three rules shape this module:

- Best-effort: sending never raises. Push runs after a message is persisted,
  and a delivery failure must not tear down the caller's flow.
- No message content in payloads. Everything the push service (FCM, Mozilla,
  ...) can read stays metadata — who messaged, not what they said. Messages
  are encrypted at rest; leaking plaintext into a third-party push pipeline
  would quietly undo that.
- Dead subscriptions are pruned on sight (push service answers 404/410).
  Browsers revoke endpoints all the time (permission withdrawn, site data
  cleared), and retrying those just burns quota.
"""

import json
import hashlib
import logging
import secrets
from urllib.parse import urlsplit

import requests
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import connection, transaction
from pywebpush import WebPushException, webpush

from capp.classes.chat.unread_service import total_unread
from capp.models import CustomUser, PushSubscription

logger = logging.getLogger(__name__)
MAX_ENDPOINT_LENGTH = 768

# Fallback TTL of one day: a "you have a message" ping for a week-old message
# is noise by the time the user comes back online.
DEFAULT_TTL_SECONDS = 24 * 60 * 60

# Keyed by CustomUser.preferred_language; anything unlisted falls back to en.
MESSAGE_BODY_BY_LANGUAGE = {
    "en": "sent you a message",
    "sw": "amekutumia ujumbe",
}


class PushSubscriptionOwnershipError(Exception):
    """Raised when an endpoint is already bound with different credentials."""


class _NoRedirectSession(requests.Session):
    """Requests session that never follows a push-provider redirect."""

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def is_allowed_push_endpoint(endpoint: str) -> bool:
    """Accept only exact, reviewed Web Push provider origins."""
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_LENGTH:
        return False
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        return False
    origin = f"https://{parsed.hostname.lower()}"
    return origin in set(settings.WEBPUSH_ALLOWED_ORIGINS)


class PushService:
    @classmethod
    def is_configured(cls) -> bool:
        return bool(
            str(settings.WEBPUSH_VAPID_PUBLIC_KEY).strip()
            and str(settings.WEBPUSH_VAPID_PRIVATE_KEY).strip()
            and str(settings.WEBPUSH_VAPID_SUBJECT).strip()
        )

    @classmethod
    def save_subscription(
        cls, user: CustomUser, *, endpoint: str, p256dh: str, auth: str, user_agent: str = ""
    ) -> PushSubscription:
        """Create/update a bounded subscription set with credential-bound transfer."""
        if not is_allowed_push_endpoint(endpoint):
            raise ValueError("invalid_endpoint")
        max_subscriptions = settings.WEBPUSH_MAX_SUBSCRIPTIONS_PER_USER
        with transaction.atomic():
            if connection.vendor == "postgresql":
                lock_id = int.from_bytes(hashlib.sha256(endpoint.encode("utf-8")).digest()[:8], "big", signed=True)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])
            CustomUser.objects.select_for_update().get(pk=user.pk)
            subscription = PushSubscription.objects.select_for_update().filter(endpoint=endpoint).first()
            if subscription is None:
                subscription = PushSubscription.objects.create(
                    user=user,
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    user_agent=user_agent[:255],
                )
            elif subscription.user_id != user.user_id:
                credentials_match = secrets.compare_digest(subscription.p256dh, p256dh) and secrets.compare_digest(
                    subscription.auth, auth
                )
                if not credentials_match:
                    raise PushSubscriptionOwnershipError
                subscription.user = user
                subscription.user_agent = user_agent[:255]
                subscription.save(update_fields=["user", "user_agent", "updated_at"])
            else:
                subscription.p256dh = p256dh
                subscription.auth = auth
                subscription.user_agent = user_agent[:255]
                subscription.save(update_fields=["p256dh", "auth", "user_agent", "updated_at"])

            stale_ids = list(
                PushSubscription.objects.filter(user=user)
                .order_by("-updated_at")
                .values_list("pk", flat=True)[max_subscriptions:]
            )
            if stale_ids:
                PushSubscription.objects.filter(pk__in=stale_ids).delete()
            return subscription

    @classmethod
    def delete_subscription(cls, user: CustomUser, *, endpoint: str) -> int:
        """Delete a subscription owned by ``user``. Returns rows deleted."""
        deleted, _ = PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
        return deleted

    @classmethod
    def send_new_message_push(cls, user: CustomUser, character, conversation) -> int:
        """
        Notify ``user`` that ``character`` sent a message — used for both
        proactive messages and replies that land while the app is closed.
        The service worker suppresses the notification while an app window is
        visible, so actively chatting users are not spammed.
        """
        body = MESSAGE_BODY_BY_LANGUAGE.get(user.preferred_language, MESSAGE_BODY_BY_LANGUAGE["en"])
        return cls.send_to_user(
            user,
            title=character.name,
            body=body,
            url=f"/conversations/{conversation.conversation_id}",
            # Computed after the triggering message was persisted, so the
            # count the service worker badges the app icon with includes it.
            unread_count=total_unread(user_id=user.user_id),
        )

    @classmethod
    def send_to_user(
        cls, user: CustomUser, *, title: str, body: str, url: str = "/", unread_count: int | None = None
    ) -> int:
        """
        Deliver a notification to every subscription of ``user``.
        Returns how many subscriptions the push was attempted on.

        ``unread_count`` lets the service worker mirror the nav badge onto
        the app icon (Badging API). A count is still metadata, so this does
        not bend the no-content rule.
        """
        if not cls.is_configured():
            logger.debug("Web Push not configured (VAPID keys unset) — skipping send")
            return 0

        max_subscriptions = settings.WEBPUSH_MAX_SUBSCRIPTIONS_PER_USER
        subscriptions = list(PushSubscription.objects.filter(user=user).order_by("-updated_at")[:max_subscriptions])
        if not subscriptions:
            return 0

        payload_data = {"title": title, "body": body, "url": url}
        if unread_count is not None:
            payload_data["unread_count"] = unread_count
        payload = json.dumps(payload_data)
        attempted = 0

        with _NoRedirectSession() as session:
            for subscription in subscriptions:
                if not is_allowed_push_endpoint(subscription.endpoint):
                    PushSubscription.objects.filter(pk=subscription.pk, user=user).delete()
                    logger.warning("Pruned disallowed push endpoint for user %s", user.user_id)
                    continue
                try:
                    webpush(
                        subscription_info={
                            "endpoint": subscription.endpoint,
                            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                        },
                        data=payload,
                        vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
                        vapid_claims={"sub": settings.WEBPUSH_VAPID_SUBJECT},
                        ttl=DEFAULT_TTL_SECONDS,
                        timeout=settings.WEBPUSH_TIMEOUT_SECONDS,
                        requests_session=session,
                    )
                    attempted += 1
                except SoftTimeLimitExceeded:
                    raise
                except WebPushException as exc:
                    status_code = getattr(exc.response, "status_code", None)
                    if status_code in (404, 410):
                        PushSubscription.objects.filter(pk=subscription.pk, user=user).delete()
                        logger.info("Pruned dead push subscription for %s (%s)", user.user_id, status_code)
                    else:
                        logger.warning("Push to %s failed with status %s", user.user_id, status_code)
                except Exception:
                    logger.exception("Push to %s raised unexpectedly", user.user_id)

        return attempted
