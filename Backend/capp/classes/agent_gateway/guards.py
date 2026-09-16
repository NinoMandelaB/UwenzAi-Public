"""Shared fail-closed execution guards; no prompt content or credentials in logs."""

import hashlib
from contextlib import contextmanager

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from capp.authentication import account_can_authenticate

from capp.models import ComplianceConfirmationLog

# Version of the developer notice actually displayed before explicit acceptance.
# Keep historical logs as recorded; never backfill acceptance of a newer notice.
AGENT_GATEWAY_CONSENT_VERSION = "2026-09-14"


class GatewayUnavailable(APIException):
    status_code = 503
    default_detail = "Gateway coordination is unavailable. Please try again shortly."


class GatewayBusy(APIException):
    status_code = 409
    default_detail = "Another gateway request is running. Wait for it to finish before retrying."


def has_gateway_consent(user):
    """Current workspace acknowledgement; not a new external-key auth condition."""
    return ComplianceConfirmationLog.objects.filter(
        user=user,
        confirmation_type=ComplianceConfirmationLog.ConfirmationType.AGENT_GATEWAY,
        accepted=True,
        version=AGENT_GATEWAY_CONSENT_VERSION,
    ).exists()


@contextmanager
def gateway_lock(identity):
    """Redis token-owned lock, shared by browser and external requests.

    No unsafe get/delete unlock and no process-local fallback. Durable operation
    claims also fence retries if a worker dies or its lock lease expires.
    """
    url = getattr(settings, "REDIS_URL", None)
    if not url:
        raise GatewayUnavailable()
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        lock = client.lock(f"agent-gateway:{identity}", timeout=180, blocking_timeout=0)
        acquired = lock.acquire(blocking=False)
    except RedisError as exc:
        client.close()
        raise GatewayUnavailable() from exc
    if not acquired:
        client.close()
        raise GatewayBusy()
    try:
        yield
    finally:
        try:
            lock.release()
        except RedisError:
            # Never change an already-settled outcome because the cache failed.
            pass
        client.close()


def gateway_user_lock(user):
    return gateway_lock(f"playground-user:{user.pk}")


def gateway_operation_lock(key, operation_id):
    digest = hashlib.sha256(operation_id.encode()).hexdigest()
    return gateway_lock(f"operation:{key.pk}:{digest}")


class GatewayAccountPermission(IsAuthenticated):
    message = "Account is unavailable. Sign in with an active account."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and account_can_authenticate(request.user)
