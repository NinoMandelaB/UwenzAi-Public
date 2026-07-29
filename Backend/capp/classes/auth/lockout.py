from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone


@dataclass
class LockoutStatus:
    is_locked: bool
    retry_after_seconds: int = 0


class LoginLockoutService:
    """
    Cache-backed login lockout service.

    Tracks failures by:
    - username
    - username + IP

    Locks after repeated failures within a rolling window.
    """

    FAILURE_WINDOW_SECONDS = 15 * 60
    LOCKOUT_SECONDS = 15 * 60
    MAX_FAILURES = 5

    @classmethod
    def _username_fail_key(cls, username: str) -> str:
        return f"auth:signin:fail:username:{username}"  # noqa: E231

    @classmethod
    def _username_ip_fail_key(cls, username: str, ip_address: str) -> str:
        return f"auth:signin:fail:username_ip:{username}:{ip_address}"  # noqa: E231

    @classmethod
    def _username_lock_key(cls, username: str) -> str:
        return f"auth:signin:lock:username:{username}"  # noqa: E231

    @classmethod
    def _username_ip_lock_key(cls, username: str, ip_address: str) -> str:
        return f"auth:signin:lock:username_ip:{username}:{ip_address}"  # noqa: E231

    @classmethod
    def _now_ts(cls) -> int:
        return int(timezone.now().timestamp())

    @classmethod
    def _seconds_remaining(cls, lock_until_ts) -> int:
        if not lock_until_ts:
            return 0

        remaining = int(lock_until_ts) - cls._now_ts()
        return max(0, remaining)

    @classmethod
    def _get_lock_status_for_key(cls, key: str) -> LockoutStatus:
        lock_until_ts = cache.get(key)

        if not lock_until_ts:
            return LockoutStatus(is_locked=False, retry_after_seconds=0)

        retry_after = cls._seconds_remaining(lock_until_ts)

        if retry_after <= 0:
            cache.delete(key)
            return LockoutStatus(is_locked=False, retry_after_seconds=0)

        return LockoutStatus(is_locked=True, retry_after_seconds=retry_after)

    @classmethod
    def get_lockout_status(cls, username: str, ip_address: str = None) -> LockoutStatus:
        username = (username or "").strip().lower()
        ip_address = (ip_address or "").strip()

        if not username:
            return LockoutStatus(is_locked=False, retry_after_seconds=0)

        username_status = cls._get_lock_status_for_key(cls._username_lock_key(username))
        if username_status.is_locked:
            return username_status

        if ip_address:
            username_ip_status = cls._get_lock_status_for_key(cls._username_ip_lock_key(username, ip_address))
            if username_ip_status.is_locked:
                return username_ip_status

        return LockoutStatus(is_locked=False, retry_after_seconds=0)

    @classmethod
    def _increment_failures(cls, key: str) -> int:
        current = cache.get(key, 0)
        current += 1
        cache.set(key, current, timeout=cls.FAILURE_WINDOW_SECONDS)
        return current

    @classmethod
    def _set_lock(cls, key: str):
        lock_until_ts = cls._now_ts() + cls.LOCKOUT_SECONDS
        cache.set(key, lock_until_ts, timeout=cls.LOCKOUT_SECONDS)

    @classmethod
    def record_failure(cls, username: str, ip_address: str = None) -> LockoutStatus:
        username = (username or "").strip().lower()
        ip_address = (ip_address or "").strip()

        if not username:
            return LockoutStatus(is_locked=False, retry_after_seconds=0)

        username_failures = cls._increment_failures(cls._username_fail_key(username))
        if username_failures >= cls.MAX_FAILURES:
            cls._set_lock(cls._username_lock_key(username))

        if ip_address:
            username_ip_failures = cls._increment_failures(cls._username_ip_fail_key(username, ip_address))
            if username_ip_failures >= cls.MAX_FAILURES:
                cls._set_lock(cls._username_ip_lock_key(username, ip_address))

        return cls.get_lockout_status(username, ip_address)

    @classmethod
    def clear_failures(cls, username: str, ip_address: str = None):
        username = (username or "").strip().lower()
        ip_address = (ip_address or "").strip()

        if not username:
            return

        cache.delete(cls._username_fail_key(username))
        cache.delete(cls._username_lock_key(username))

        if ip_address:
            cache.delete(cls._username_ip_fail_key(username, ip_address))
            cache.delete(cls._username_ip_lock_key(username, ip_address))
