# capp/classes/reminders/tool_loop.py
"""
Server-side tools for turning "remind me to X at Y" (and its follow-ups --
"actually make that 10am instead", "never mind, cancel it") into persisted
Reminder rows, using the same offered-tool / condensed-result / one-shot
follow-up pattern as capp/classes/search/tool_loop.py.

Design:
- The model already receives the user's current local date/time in the
  system prompt (see capp.classes.ai.prompt_builder.get_time_context) and
  the list of the user's pending reminders (see
  capp.classes.ai.prompt_builder.get_reminder_context), so it is only asked
  to resolve relative phrases ("tomorrow evening") into a LOCAL
  due_date/due_time -- it is never asked to do UTC conversion itself. The
  backend is the only thing that converts local -> UTC (via zoneinfo, using
  the user's stored IANA timezone), so a model arithmetic mistake can never
  silently create a wrong-timezone reminder.
- Exactly one reminder-tool call per user message, across all three tools
  combined (create/update/cancel) -- same spirit as open_url's
  one-call-per-message cap. A user asking to both reschedule and add a new
  reminder in the same message gets the first one handled and can just ask
  again for the second.
- update_reminder/cancel_reminder require the model to pass back the exact
  reminder_id it saw in the pending-reminders context; if the model can't
  find a matching id (e.g. the user references something that already fired
  or was never created), the tool call fails gracefully with a message the
  model can relay instead of guessing at some other id.
- Reminder tools are only offered to authenticated users with a durable
  account (gated in capp/tasks.py) -- a guest session has no reliable way to
  be reachable "tomorrow".
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone as django_timezone

from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.billing.paid_features import PaidFeature

logger = logging.getLogger(__name__)

CREATE_REMINDER_TOOL_NAME = "create_reminder"
UPDATE_REMINDER_TOOL_NAME = "update_reminder"
CANCEL_REMINDER_TOOL_NAME = "cancel_reminder"

# Backward-compatible alias: existing tests/imports reference this name for
# the create tool specifically.
REMINDER_TOOL_NAME = CREATE_REMINDER_TOOL_NAME

REMINDER_TOOL_NAMES = {CREATE_REMINDER_TOOL_NAME, UPDATE_REMINDER_TOOL_NAME, CANCEL_REMINDER_TOOL_NAME}

# A reminder further out than this is almost certainly a misparse (the rare
# user who genuinely means a year from now can just ask again closer to the
# date). MIN_REMINDER_LEAD guards against "remind me right now" collapsing
# into a due_at that's already in the past by the time this runs.
MAX_REMINDER_LEAD = timedelta(days=365)
MIN_REMINDER_LEAD = timedelta(minutes=1)

DEFAULT_TIMEZONE = "Africa/Nairobi"


def build_reminder_tool_specs(*, allow_create=True) -> list:
    """Build the create_reminder/update_reminder/cancel_reminder tool specs.

    No live values are interpolated into the descriptions (the user's
    current local time and pending reminders are already present elsewhere
    in the system prompt) so these stay static, cacheable prompt-prefix
    blocks like SEARCH_TOOL_SPEC/OPEN_URL_TOOL_SPEC.
    """
    return ([_CREATE_REMINDER_SPEC] if allow_create else []) + [_UPDATE_REMINDER_SPEC, _CANCEL_REMINDER_SPEC]


_CREATE_REMINDER_SPEC = {
    "type": "function",
    "function": {
        "name": CREATE_REMINDER_TOOL_NAME,
        "description": (
            "Call this when the user asks to be reminded about something in the "
            "future (e.g. 'remind me to call Paul tomorrow evening', or the same "
            "request in Kiswahili or any other language). Resolve relative phrases "
            "like 'tomorrow evening' or 'in 2 hours' against the user's current "
            "local date/time already given to you above in this prompt. Provide "
            "the target date/time in the user's OWN local timezone -- never "
            "convert to UTC yourself, the backend does that. Only call one "
            "reminder tool (create/update/cancel) per message. After it returns, "
            "confirm the reminder back to the user in whatever language they "
            "used, including the exact local time and the Spark balance note "
            "the tool result gives you."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_text": {
                    "type": "string",
                    "description": (
                        "Short description of what to remind the user about, in the "
                        "user's own words/language, e.g. 'call Paul'."
                    ),
                },
                "due_date": {
                    "type": "string",
                    "description": "Target date in the user's local timezone, as YYYY-MM-DD.",
                },
                "due_time": {
                    "type": "string",
                    "description": (
                        "Target time in the user's local timezone, 24-hour HH:MM. For a "
                        "vague time of day, use a sensible default: morning=09:00, "
                        "afternoon=15:00, evening=19:00, night=21:00."
                    ),
                },
            },
            "required": ["reminder_text", "due_date", "due_time"],
        },
    },
}

_UPDATE_REMINDER_SPEC = {
    "type": "function",
    "function": {
        "name": UPDATE_REMINDER_TOOL_NAME,
        "description": (
            "Call this when the user asks to change an existing reminder you already "
            "set for them -- including indirect or casual phrasings like 'don't "
            "remind me at 9, make it 10am instead', 'can you send it already at 3pm', "
            "'actually make it tomorrow morning', 'move it up an hour', or 'change "
            "that reminder to also mention the report'. Any time the user names or "
            "implies a different day/time for a reminder that already exists, you "
            "MUST call this tool with due_date and/or due_time set to the new value "
            "-- never reply claiming you rescheduled or changed something without "
            "actually calling this tool with the changed field(s). Saying 'done' or "
            "stating a new time in your reply is only allowed after this tool call "
            "returns a result, and you must always restate the exact time from that "
            "result (never the time the user asked for, if it differs). Look up the "
            "reminder_id from the pending-reminders list given to you above in this "
            "prompt -- match it by the reminder's quoted text and/or due time the "
            "user is referring to. Only pass the fields that actually change; leave "
            "the others out. Only call one reminder tool (create/update/cancel) per "
            "message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "The exact reminder_id from the pending-reminders list above.",
                },
                "reminder_text": {
                    "type": "string",
                    "description": "New description of what to remind the user about, if it changed.",
                },
                "due_date": {
                    "type": "string",
                    "description": "New target date in the user's local timezone, as YYYY-MM-DD, if it changed.",
                },
                "due_time": {
                    "type": "string",
                    "description": ("New target time in the user's local timezone, 24-hour HH:MM, if it changed."),
                },
            },
            "required": ["reminder_id"],
        },
    },
}

_CANCEL_REMINDER_SPEC = {
    "type": "function",
    "function": {
        "name": CANCEL_REMINDER_TOOL_NAME,
        "description": (
            "Call this when the user asks to cancel or delete a reminder you already "
            "set for them (e.g. 'never mind, don't remind me about that', 'cancel my "
            "9am reminder'). Look up the reminder_id from the pending-reminders list "
            "given to you above in this prompt -- match it by the reminder's quoted "
            "text and/or due time the user is referring to. Only call one reminder "
            "tool (create/update/cancel) per message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "The exact reminder_id from the pending-reminders list above.",
                },
            },
            "required": ["reminder_id"],
        },
    },
}


class ReminderToolError(Exception):
    """Raised for a malformed or out-of-range reminder request."""


class ReminderDisabled(ReminderToolError):
    pass


class ReminderLimitReached(ReminderToolError):
    pass


class ReminderNotFound(ReminderToolError):
    pass


def _string_arg(args, key, *, required=False):
    if not isinstance(args, dict):
        raise ReminderToolError("arguments must be a JSON object")
    if key not in args:
        if required:
            raise ReminderToolError(f"{key} is required")
        return ""
    value = args[key]
    if not isinstance(value, str):
        raise ReminderToolError(f"{key} must be a string")
    value = value.strip()
    if not value:
        raise ReminderToolError(f"{key} was empty")
    return value


def _reminder_uuid(value):
    try:
        return uuid.UUID(str(value)) if isinstance(value, (str, uuid.UUID)) else None
    except (ValueError, AttributeError):
        return None


def run_durable_mutation(*, user, source_message, family, name, execute):
    """Serialize logical tool slots, not unstable provider call IDs/arguments.

    There is one reminder and one profile mutation slot per source turn.
    The outcome and mutation commit together. No provider work belongs here.
    """
    from capp.models import CustomUser, ReminderToolExecution

    with transaction.atomic():
        locked_user = CustomUser.objects.select_for_update().get(pk=user.pk)
        source_key = source_message.pk if source_message is not None else None
        if source_key is not None:
            previous = ReminderToolExecution.objects.filter(
                user=locked_user, source_message_key=source_key, family=family
            ).first()
            if previous:
                return (
                    "[Previously executed for this message; do not apply again or claim it was just changed. "
                    "The schedule/account may since have changed. Original outcome follows.]\n" + previous.result,
                    1,
                )
        result = execute(locked_user)
        if source_key is not None:
            ReminderToolExecution.objects.create(
                user=locked_user, source_message_key=source_key, family=family, tool_name=name, result=result[0]
            )
        # Preserve the same-turn profile -> reminder dependency for callers.
        user.timezone = locked_user.timezone
        user.feature_preferences = locked_user.feature_preferences
        return result


def _resolve_due_at(user_timezone: str, due_date: str, due_time: str):
    """Turn the model's local date/time strings into an aware UTC datetime."""
    try:
        tz = ZoneInfo(user_timezone or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo(DEFAULT_TIMEZONE)

    try:
        naive = datetime.strptime(f"{due_date} {due_time}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ReminderToolError(f"could not parse due_date/due_time ({exc})") from None

    local_dt = naive.replace(tzinfo=tz)
    due_at = local_dt.astimezone(ZoneInfo("UTC"))

    now = django_timezone.now()
    if due_at < now + MIN_REMINDER_LEAD:
        raise ReminderToolError("requested time is in the past or too close to now")
    if due_at > now + MAX_REMINDER_LEAD:
        raise ReminderToolError("requested time is too far in the future")

    return due_at


def _local_str(due_at, tz_name: str) -> str:
    try:
        return due_at.astimezone(ZoneInfo(tz_name)).strftime("%A, %B %d at %I:%M %p")
    except ZoneInfoNotFoundError:
        return due_at.isoformat()


def _get_function(call):
    """Normalize a tool call to its `.function`/["function"] payload.

    ``tool_calls`` can be either plain dicts (unit tests, some BYOK
    providers) or OpenAI-SDK objects such as
    ``ChatCompletionMessageFunctionToolCall`` (what CortecsClient/OpenAI's
    own SDK actually hands back in production -- see
    capp/classes/ai/cortecs_client.py, which forwards the SDK objects
    as-is). Mirrors capp/classes/search/tool_loop.py's ``_get_function`` so
    both tool loops accept the exact same real-world shapes.
    """
    return getattr(call, "function", None) or (call.get("function") if isinstance(call, dict) else None)


def _get_function_name(fn):
    return getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)


def _get_function_args_raw(fn):
    return getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else None)


def run_reminder_tool_calls(tool_calls, *, conversation, user, character, source_message) -> Tuple[str, int]:
    """
    Execute at most one reminder tool call (the first in returned order) from the
    model's tool_calls.

    Returns (condensed_block, calls_used) -- same shape as
    run_search_tool_calls/run_open_url_tool_calls, so capp/tasks.py can fold
    the result into the same block it already builds for search/open_url.
    """
    for call in tool_calls or []:
        fn = _get_function(call)
        if not fn:
            continue
        name = _get_function_name(fn)
        if not isinstance(name, str) or name not in REMINDER_TOOL_NAMES:
            continue

        try:
            args = json.loads(_get_function_args_raw(fn) or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.info("Reminder tool call had unparseable arguments for user=%s", user.user_id)
            return (f"[{name} failed: could not parse arguments]", 1)

        if not isinstance(args, dict):
            logger.info("Reminder tool call arguments were not a JSON object for user=%s", user.user_id)
            return (f"[{name} failed: arguments were not a JSON object]", 1)

        def execute(locked_user):
            try:
                if name == CREATE_REMINDER_TOOL_NAME:
                    return _handle_create(
                        args,
                        conversation=conversation,
                        user=locked_user,
                        character=character,
                        source_message=source_message,
                    )
                if name == UPDATE_REMINDER_TOOL_NAME:
                    return _handle_update(args, user=locked_user)
                return _handle_cancel(args, user=locked_user)
            except ReminderToolError as exc:
                return (f"[{name} failed: {exc}]", 1)

        return run_durable_mutation(
            user=user, source_message=source_message, family="reminder", name=name, execute=execute
        )

    return ("", 0)


def check_reminder_limit(user, character) -> None:
    """Raise ReminderToolError if `user` already has the max PENDING reminders
    allowed for `character`. Shared by both creation paths (the create_reminder
    chat tool and the REST create endpoint in capp/views/reminder_views.py) so
    the cap can never be bypassed by going through the other path.

    The caller must hold the owner row lock through both count and insert;
    create_reminder is the shared atomic entry point that enforces this.
    """
    from capp.constants import MAX_PENDING_REMINDERS_PER_USER_CHARACTER
    from capp.models import Reminder  # local import: avoid app-loading cycles

    pending_count = Reminder.objects.filter(
        user=user, character=character, status=Reminder.StatusChoices.PENDING
    ).count()
    if pending_count >= MAX_PENDING_REMINDERS_PER_USER_CHARACTER:
        raise ReminderLimitReached(
            f"you already have {pending_count} pending reminders with this character, "
            f"which is the maximum of {MAX_PENDING_REMINDERS_PER_USER_CHARACTER} -- cancel one before adding another"
        )


def create_reminder(args, *, conversation, user, character, source_message=None):
    """Shared REST/tool count-and-insert, locked against feature PATCH too."""
    from capp.models import CustomUser, Reminder

    reminder_text = _string_arg(args, "reminder_text", required=True)[:500]
    due_date = _string_arg(args, "due_date", required=True)
    due_time = _string_arg(args, "due_time", required=True)
    with transaction.atomic():
        user = CustomUser.objects.select_for_update().get(pk=user.pk)
        if not FeaturePreferences.is_enabled(user, "reminders"):
            raise ReminderDisabled("reminders_disabled")
        if conversation.user_id != user.pk or conversation.character_id != character.pk:
            raise ReminderToolError("conversation does not belong to this user/character")
        check_reminder_limit(user, character)
        due_at = _resolve_due_at(user.timezone, due_date, due_time)
        return Reminder.objects.create(
            user=user,
            character=character,
            conversation=conversation,
            source_message=source_message,
            reminder_text=reminder_text,
            due_at=due_at,
            timezone_snapshot=user.timezone or DEFAULT_TIMEZONE,
        )


def _handle_create(args, *, conversation, user, character, source_message) -> Tuple[str, int]:
    from capp.constants import BYOK_MESSAGE_PAIR_COST
    from capp.models import BillingReservation, UserBYOKConfig

    reminder = create_reminder(
        args, conversation=conversation, user=user, character=character, source_message=source_message
    )
    reminder_text, due_at, user_tz_name = reminder.reminder_text, reminder.due_at, reminder.timezone_snapshot

    local_str = _local_str(due_at, user_tz_name)
    byok_active = UserBYOKConfig.objects.filter(user=user, is_active=True).exists()
    spark_cost = PaidFeature.cost(
        BillingReservation.Feature.MESSAGE_PAIR,
        cost_override=BYOK_MESSAGE_PAIR_COST if byok_active else None,
    )

    logger.info(
        "Reminder %s created for user=%s due_at=%s (local=%s %s)",
        reminder.reminder_id,
        user.user_id,
        due_at.isoformat(),
        local_str,
        user_tz_name,
    )

    return (
        "[create_reminder result: reminder saved successfully for "
        f"'{reminder_text}' at {local_str} ({user_tz_name}). Confirm this back to "
        "the user in their own language, stating the exact local time and "
        f"timezone, and mention they need at least {spark_cost} Sparks available "
        "at that time or the reminder cannot be sent.]",
        1,
    )


def _locked_reminder(reminder_id, user):
    from capp.models import Reminder

    key = _reminder_uuid(reminder_id)
    reminder = (
        None
        if key is None
        else Reminder.objects.select_for_update()
        .filter(pk=key, user=user, status=Reminder.StatusChoices.PENDING)
        .first()
    )
    if reminder is None:
        raise ReminderNotFound("no matching pending reminder found for that id")
    return reminder


def update_reminder(args, *, user, reminder_id, use_snapshot=False):
    from capp.models import CustomUser
    from capp.classes.reminders.reminder_service import ReminderService

    new_text = _string_arg(args, "reminder_text")[:500]
    due_date = _string_arg(args, "due_date")
    due_time = _string_arg(args, "due_time")
    if not (new_text or due_date or due_time):
        raise ReminderToolError("no changes were provided")
    with transaction.atomic():
        user = CustomUser.objects.select_for_update().get(pk=user.pk)
        reminder = _locked_reminder(reminder_id, user)
        schedule_changed = bool(due_date or due_time)
        if schedule_changed:
            # Tools speak current-local time, matching the prompt. REST edits
            # speak the snapshot-local time explicitly displayed by that editor.
            tz_name = (reminder.timezone_snapshot if use_snapshot else user.timezone) or DEFAULT_TIMEZONE
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                tz_name, tz = DEFAULT_TIMEZONE, ZoneInfo(DEFAULT_TIMEZONE)
            existing_local = reminder.due_at.astimezone(tz)
            reminder.due_at = _resolve_due_at(
                tz_name,
                due_date or existing_local.strftime("%Y-%m-%d"),
                due_time or existing_local.strftime("%H:%M"),
            )
            reminder.timezone_snapshot = tz_name
            reminder.notice_sent = False
        if new_text:
            reminder.reminder_text = new_text
        ReminderService.invalidate_locked(reminder)
        reminder.revision += 1
        reminder.failure_reason = ""
        reminder.save(
            update_fields=[
                "reminder_text",
                "due_at",
                "timezone_snapshot",
                "notice_sent",
                "revision",
                "failure_reason",
                "updated_at",
            ]
        )
        return reminder, schedule_changed


def _handle_update(args, *, user) -> Tuple[str, int]:
    reminder_id = _string_arg(args, "reminder_id", required=True)
    reminder, schedule_changed = update_reminder(args, user=user, reminder_id=reminder_id)

    local_str = _local_str(reminder.due_at, reminder.timezone_snapshot)
    logger.info("Reminder %s updated for user=%s new due_at=%s", reminder.reminder_id, user.user_id, local_str)

    if schedule_changed:
        confirmation = (
            "[update_reminder result: reminder updated successfully. The reminder is "
            f"now set for '{reminder.reminder_text}' at EXACTLY {local_str} ({reminder.timezone_snapshot}). "
            f"IMPORTANT: you must state this exact time -- {local_str} -- back to the user in their own "
            "language. Do not state the time the user originally asked for if it differs from this one; "
            "this is the true, saved time.]"
        )
    else:
        confirmation = (
            "[update_reminder result: reminder updated successfully, but no schedule change was made "
            f"(no due_date/due_time was provided) -- it is still set for {local_str} ({reminder.timezone_snapshot}). "
            "Only the other field(s) you passed were changed. If the user asked for a different time and you "
            "did not include due_date/due_time in this call, explain that the time was not changed and "
            "ask for a follow-up message to reschedule, or point to Manage reminders. No further tools "
            "are available in this reply; do not tell the user the time changed.]"
        )

    return (confirmation, 1)


def cancel_reminder(*, user, reminder_id):
    from capp.models import CustomUser
    from capp.classes.reminders.reminder_service import ReminderService

    with transaction.atomic():
        user = CustomUser.objects.select_for_update().get(pk=user.pk)
        reminder = _locked_reminder(reminder_id, user)
        reminder_text = reminder.reminder_text
        ReminderService.invalidate_locked(reminder)
        reminder.delete()
        return reminder_text


def _handle_cancel(args, *, user) -> Tuple[str, int]:
    reminder_id = _string_arg(args, "reminder_id", required=True)
    reminder_text = cancel_reminder(user=user, reminder_id=reminder_id)
    logger.info("Reminder %s cancelled by user=%s", reminder_id, user.user_id)

    return (
        f"[cancel_reminder result: reminder for '{reminder_text}' cancelled successfully. "
        "Confirm this back to the user in their own language.]",
        1,
    )
