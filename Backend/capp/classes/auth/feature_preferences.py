"""
Per-account feature visibility preferences (Settings → Advanced).

Settings control availability, not the active modes of individual chats.
Unset account preferences default reminders, deep thinking and web search
to available; memory editing stays opt-in. Explicit saved choices win.

Scope:
- reminders ........ new reminder creation. Existing schedules still deliver,
                     and listing, updating and cancelling remain available.
- deepthink ........ the per-chat "deep thinking" reasoning-effort toggle
- websearch ........ the per-chat web-search toggle + search/open_url tools
- memory_editor .... the brain icon that opens the memory editor popup

BYOK is deliberately NOT a flag: it keeps its own always-available section
in Settings and is unaffected by these preferences.

Guests, unknown keys and malformed saved values remain fail-closed.
"""

import logging
from typing import Any, Dict, Tuple

from django.db import transaction

from capp.models import CustomUser

logger = logging.getLogger(__name__)


class FeaturePreferences:
    """Read/validate/persist the per-user feature visibility flags."""

    #: The only keys ever accepted or returned.
    FEATURE_KEYS: Tuple[str, ...] = (
        "reminders",
        "deepthink",
        "websearch",
        "memory_editor",
    )
    DEFAULTS = {"reminders": True, "deepthink": True, "websearch": True, "memory_editor": False}

    @classmethod
    def get(cls, user: CustomUser) -> Dict[str, bool]:
        """Return the effective preference dict for ``user`` (all keys, all bools).

        Unknown keys are dropped; missing keys use availability defaults.
        Reading never writes preferences or changes per-chat mode settings.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            # Guests / anonymous: every optional feature is unavailable.
            return {key: False for key in cls.FEATURE_KEYS}

        raw = getattr(user, "feature_preferences", None)
        if not isinstance(raw, dict):
            return {key: False for key in cls.FEATURE_KEYS}

        prefs: Dict[str, bool] = {}
        for key in cls.FEATURE_KEYS:
            value = raw.get(key, cls.DEFAULTS[key])
            prefs[key] = value is True
        return prefs

    @classmethod
    def is_enabled(cls, user: CustomUser, key: str) -> bool:
        """True when a known feature is effectively available to the account."""
        if key not in cls.FEATURE_KEYS:
            return False
        return cls.get(user).get(key) is True

    @classmethod
    def update(cls, user: CustomUser, payload: Any) -> Dict[str, Any]:
        """Validate and persist a partial update.

        ``payload`` must be a JSON object whose keys are a subset of
        FEATURE_KEYS and whose values are booleans. Returns the standard
        ``{"status": ..., "data": ...}`` envelope; on success ``data``
        contains the full effective preference dict.
        """
        if user is None:
            return {"status": "fail", "data": "user_required"}

        if not isinstance(payload, dict):
            return {"status": "fail", "data": "invalid_payload"}

        unknown_keys = [k for k in payload if k not in cls.FEATURE_KEYS]
        if unknown_keys:
            return {
                "status": "fail",
                "data": f"unknown_feature:{','.join(sorted(unknown_keys))}",
            }

        for key, value in payload.items():
            if not isinstance(value, bool):
                return {"status": "fail", "data": f"invalid_value:{key}"}

        # Serialize partial patches against a stable owner row; a request's
        # loaded JSON snapshot may predate an acknowledged opt-out.
        with transaction.atomic():
            locked = CustomUser.objects.select_for_update().get(pk=user.pk)
            current = cls.get(locked)
            current.update(payload)
            locked.feature_preferences = dict(current)
            locked.save(update_fields=["feature_preferences", "updated_at"])
        user.feature_preferences = dict(current)

        logger.info(
            "Feature preferences updated for user %s: %s",
            user.user_id,
            current,
        )
        return {"status": "success", "data": {"feature_preferences": current}}
