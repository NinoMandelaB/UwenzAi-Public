"""Keyset pagination for conversation message history.

History is read newest-first so the first page is the tail of the conversation,
and each page is handed back in chronological order for display. The cursor is a
composite of ``(created_at, message_id)``: ``created_at`` alone is not unique —
a user message and its AI reply can land in the same microsecond — and an
offset would skip or repeat rows as new messages arrive between page requests.
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

from django.utils.dateparse import parse_datetime

from capp.models import Message

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

_CURSOR_SEPARATOR = "|"


class InvalidCursor(Exception):
    """The ``before`` value is not a cursor this server issued."""

    code = "invalid_cursor"


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    message_id: uuid.UUID


def encode_cursor(message) -> str:
    """Opaque cursor pointing at ``message``; rows strictly older follow it."""
    raw = f"{message.created_at.isoformat()}{_CURSOR_SEPARATOR}{message.message_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(raw: str, conversation=None) -> Cursor:
    """Parse a cursor, or raise :class:`InvalidCursor`.

    Also accepts a bare message UUID, the form the endpoint took before cursors
    existed, so a client mid-scroll across a deploy keeps working. That form
    needs the conversation to resolve the message's timestamp.
    """
    value = (raw or "").strip()
    if not value:
        raise InvalidCursor("empty cursor")

    try:
        legacy_id = uuid.UUID(value)
    except ValueError:
        pass
    else:
        if conversation is None:
            raise InvalidCursor("message id cursor needs a conversation")
        message = Message.objects.filter(conversation=conversation, message_id=legacy_id).only("created_at").first()
        if message is None:
            raise InvalidCursor("unknown message id")
        return Cursor(created_at=message.created_at, message_id=legacy_id)

    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise InvalidCursor("cursor is not valid base64")

    parts = decoded.split(_CURSOR_SEPARATOR)
    if len(parts) != 2:
        raise InvalidCursor("cursor has the wrong shape")

    timestamp = parse_datetime(parts[0])
    if timestamp is None or timestamp.utcoffset() is None:
        raise InvalidCursor("cursor timestamp is not an aware datetime")

    try:
        message_id = uuid.UUID(parts[1])
    except ValueError:
        raise InvalidCursor("cursor message id is not a UUID")

    return Cursor(created_at=timestamp, message_id=message_id)


def resolve_page_size(raw) -> int:
    """Bounded page size. A missing or unusable value falls back to the default."""
    try:
        requested = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if requested < 1:
        return DEFAULT_PAGE_SIZE
    return min(requested, MAX_PAGE_SIZE)


def fetch_page(conversation, cursor: Cursor = None, page_size: int = DEFAULT_PAGE_SIZE):
    """Return ``(messages, has_more, next_before)`` for one page of history.

    ``messages`` is chronological (oldest first) even though the query runs
    newest-first, and ``next_before`` is the cursor that continues into older
    history. One extra row is read to learn whether more history exists without
    a second COUNT query.
    """
    queryset = Message.objects.filter(conversation=conversation).order_by("-created_at", "-message_id")

    if cursor is not None:
        # ``created_at <= cursor`` is written as its own condition so Postgres can
        # use it as an index bound on (conversation_id, created_at) and start the
        # backward scan at the cursor. Expressing the keyset as a single OR
        # instead makes the planner scan and discard every newer row, which turns
        # deep pages into offset-style work. The exclude then drops the rows that
        # share the cursor's timestamp but sort after it.
        queryset = queryset.filter(created_at__lte=cursor.created_at).exclude(
            created_at=cursor.created_at, message_id__gte=cursor.message_id
        )

    rows = list(queryset[: page_size + 1])
    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_before = encode_cursor(page[-1]) if has_more and page else None

    return list(reversed(page)), has_more, next_before
