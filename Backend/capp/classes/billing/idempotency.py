"""
Operation IDs for paid actions.

Every paid action carries one stable ID from the moment the user triggers it
until the operation reaches a terminal state. That single value does three jobs:

* it is the ``BillingReservation.idempotency_key``, so the Sparks are debited
  once no matter how many times the request or the Celery task is replayed;
* it is written onto the durable artifact the operation produces (the
  ``Message``, ``Character`` or ``CharacterGalleryImage``), so a replay can find
  the previous result instead of creating a second one;
* it lets a retry decide what to do, because the reservation's status says
  whether the operation is still in flight, already paid for, or already
  refunded.

Who mints it
------------
The browser mints it, once per user action, and reuses it across automatic
network retries and double clicks — that is the only place with enough context
to know that two requests are the *same* action rather than two deliberate ones.
Clients that do not send one (a cached bundle from before this change) get a
server-minted key, which is returned in the ``Idempotency-Key`` response header.
Such a request is still billed exactly once and still refunds correctly; what it
loses is cross-request duplicate detection, because two POSTs from the same old
client are genuinely indistinguishable to the server. Setting
``BILLING_REQUIRE_IDEMPOTENCY_KEY=true`` closes that gap once the frontend has
finished rolling out; see ``docs/BILLING_RESERVATIONS.md``.

Scope
-----
Keys are scoped to the principal by a database constraint: one reservation per
``(user, key)`` / ``(guest_identity, key)``. They are deliberately *not* scoped
to the feature. Presenting the same key for a different feature or a different
amount is a client bug, not a second operation, and
``BillingReservationService`` rejects it with ``IdempotencyKeyConflict`` rather
than charging the old terms.

Only the canonical lowercase hyphenated UUID form is accepted from clients, for
the same reason ``GuestIdentityService.parse_legacy_session_id`` is strict: one
logical key must have exactly one spelling, or the uniqueness constraint that
prevents double charging can be sidestepped by re-encoding it.
"""

import uuid

from django.conf import settings

HEADER = "Idempotency-Key"
META_KEY = "HTTP_IDEMPOTENCY_KEY"

# Marks the second, server-produced artifact of an operation whose request
# artifact already carries the bare key — an AI reply, or a generated picture
# message. Both rows live in the same conversation, which is where the
# uniqueness constraint applies, so they need distinct values.
RESULT_SUFFIX = "#result"


class InvalidIdempotencyKey(Exception):
    """The client sent an operation ID that is not a canonical UUID."""

    code = "invalid_idempotency_key"


def result_key(operation_id: str) -> str:
    return f"{operation_id}{RESULT_SUFFIX}"


def parse(value) -> str:
    """Return the canonical key, or raise :class:`InvalidIdempotencyKey`."""
    if not isinstance(value, str):
        raise InvalidIdempotencyKey()
    value = value.strip()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise InvalidIdempotencyKey() from None
    if str(parsed) != value.lower():
        raise InvalidIdempotencyKey()
    return str(parsed)


def resolve(request) -> tuple:
    """
    Return ``(operation_id, client_supplied)`` for a paid request.

    Raises :class:`InvalidIdempotencyKey` for a malformed header, and also for a
    missing one when ``BILLING_REQUIRE_IDEMPOTENCY_KEY`` is on.
    """
    raw = request.META.get(META_KEY)
    if raw:
        return parse(raw), True

    if getattr(settings, "BILLING_REQUIRE_IDEMPOTENCY_KEY", False):
        raise InvalidIdempotencyKey()

    return str(uuid.uuid4()), False


def echo(response, operation_id: str):
    """Return the operation ID to the caller so a client retry can reuse it."""
    response[HEADER] = operation_id
    return response
