# capp/classes/profile/tool_loop.py
"""
Server-side tool for letting the character update the user's saved account
timezone from a conversational cue (e.g. "I'm actually in Switzerland right
now"), using the same offered-tool / condensed-result pattern as
capp/classes/search/tool_loop.py and capp/classes/reminders/tool_loop.py.

Why this exists: CustomUser.timezone is otherwise only kept fresh by an
opportunistic client-side refresh (see _maybe_refresh_user_timezone in
capp/views/conversation_views.py, driven by an X-Client-Timezone header the
real web client sends on every authenticated message). That mechanism has no
way to react to the user simply *telling* the character they're somewhere
else in chat -- so without this tool, the character could only acknowledge
the mismatch in text without ever being able to act on it, leaving every
future reminder/time reference quietly wrong until the user's device
timezone itself changes.

Design mirrors the reminder tools:
- The model already sees the user's current saved timezone in every prompt
  (see capp.classes.ai.prompt_builder.get_time_context, which renders
  "User: <local time> (<timezone>)"), so it can notice a mismatch on its own
  without any extra plumbing here.
- The tool description asks the model to confirm with the user before
  calling this (mirroring how a careful assistant should not silently
  change account state), but call to this is only a request/reminder
  aimed at the model -- confirmation is a prompting concern, not enforced
  in code, in keeping with this tool being explicitly user-invocable at
  their own request too ("please fix my timezone").
- Real IANA validation happens here (never trust the model's string as-is),
  matching the same defensive validation _maybe_refresh_user_timezone does
  for the client-header path.
"""

import json
import logging
from typing import Tuple
from zoneinfo import available_timezones

from capp.classes.reminders.tool_loop import ReminderToolError, _string_arg, run_durable_mutation

logger = logging.getLogger(__name__)

UPDATE_TIMEZONE_TOOL_NAME = "update_timezone"

PROFILE_TOOL_NAMES = {UPDATE_TIMEZONE_TOOL_NAME}

# Computed once at import time, same convention as
# capp/views/conversation_views.py's _VALID_IANA_TIMEZONES.
_VALID_IANA_TIMEZONES = available_timezones()

_UPDATE_TIMEZONE_SPEC = {
    "type": "function",
    "function": {
        "name": UPDATE_TIMEZONE_TOOL_NAME,
        "description": (
            "Call this to update the user's saved account timezone -- for example when they "
            "mention being in a different city/country than their currently saved timezone "
            "(shown to you above as 'User: <local time> (<timezone>)'), or when they directly "
            "ask you to fix/change their timezone. If you noticed the mismatch yourself rather "
            "than the user asking outright, confirm with them first in a normal reply (e.g. "
            "'Want me to update your saved timezone to Europe/Zurich?') and only call this tool "
            "after they say yes. Always pass a real IANA timezone name inferred from the place "
            "they mentioned (e.g. 'Europe/Zurich' for Switzerland, 'America/New_York' for New "
            "York, 'Africa/Nairobi' for Kenya) -- if the place is ambiguous (e.g. a large "
            "country spanning multiple zones), ask which city/region first instead of guessing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "The new IANA timezone name, e.g. 'Europe/Zurich' or 'America/New_York'.",
                },
            },
            "required": ["timezone"],
        },
    },
}


def build_profile_tool_specs() -> list:
    """Build the update_timezone tool spec.

    A static, cacheable prompt-prefix block like SEARCH_TOOL_SPEC/
    OPEN_URL_TOOL_SPEC/the reminder specs -- no live values are
    interpolated (the user's current saved timezone is already present
    elsewhere in the system prompt via get_time_context).
    """
    return [_UPDATE_TIMEZONE_SPEC]


def _get_function(call):
    """Normalize a tool call to its `.function`/["function"] payload.

    Mirrors capp/classes/reminders/tool_loop.py's `_get_function` (itself
    mirroring capp/classes/search/tool_loop.py) so all three tool loops
    accept the exact same real-world shapes (plain dicts in tests/some BYOK
    providers, OpenAI-SDK objects from CortecsClient in production).
    """
    return getattr(call, "function", None) or (call.get("function") if isinstance(call, dict) else None)


def _get_function_name(fn):
    return getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)


def _get_function_args_raw(fn):
    return getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else None)


def run_profile_tool_calls(tool_calls, *, user, source_message=None) -> Tuple[str, int]:
    """
    Execute at most one profile tool call (currently just update_timezone)
    from the model's tool_calls.

    Returns (condensed_block, calls_used) -- same shape as
    run_search_tool_calls/run_reminder_tool_calls, so capp/tasks.py can fold
    the result into the same block it already builds for those tools.
    """
    for call in tool_calls or []:
        fn = _get_function(call)
        if not fn:
            continue
        name = _get_function_name(fn)
        if not isinstance(name, str) or name not in PROFILE_TOOL_NAMES:
            continue

        try:
            args = json.loads(_get_function_args_raw(fn) or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.info("Profile tool call had unparseable arguments for user=%s", user.user_id)
            return (f"[{name} failed: could not parse arguments]", 1)

        if not isinstance(args, dict):
            logger.info("Profile tool call arguments were not a JSON object for user=%s", user.user_id)
            return (f"[{name} failed: arguments were not a JSON object]", 1)

        def execute(locked_user):
            try:
                return _handle_update_timezone(args, user=locked_user)
            except ReminderToolError as exc:
                return (f"[{name} failed: {exc}]", 1)

        return run_durable_mutation(
            user=user, source_message=source_message, family="profile", name=name, execute=execute
        )

    return ("", 0)


def _handle_update_timezone(args, *, user) -> Tuple[str, int]:
    new_tz = _string_arg(args, "timezone", required=True)

    if new_tz not in _VALID_IANA_TIMEZONES:
        logger.info("update_timezone rejected invalid tz=%s for user=%s", new_tz, user.user_id)
        return (
            f"[update_timezone failed: '{new_tz}' is not a recognized IANA timezone name -- "
            "use a real IANA zone like 'Europe/Zurich' or 'America/New_York'; ask the user "
            "which city/region if you are not sure which one applies.]",
            1,
        )

    if user.timezone == new_tz:
        return (
            f"[update_timezone result: timezone was already set to {new_tz} -- no change needed. "
            "Let the user know it's already set correctly.]",
            1,
        )

    old_tz = user.timezone
    user.timezone = new_tz
    user.save(update_fields=["timezone", "updated_at"])
    logger.info("Timezone updated for user=%s: %s -> %s", user.user_id, old_tz, new_tz)

    return (
        f"[update_timezone result: timezone updated successfully, now set to {new_tz}. "
        "Confirm this back to the user in their own language.]",
        1,
    )
