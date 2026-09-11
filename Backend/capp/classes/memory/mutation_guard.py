"""Shared database fencing for memory writers, never held over provider I/O."""

from contextlib import contextmanager

from django.db import transaction
from django.db.models import F, Max

from capp.models import Conversation, CustomUser, Message


@contextmanager
def memory_write_lock(user):
    # Account-wide because graph entities and explicitly shared facts span
    # characters. This serializes with editors without modifying auth fields.
    with transaction.atomic():
        CustomUser.objects.select_for_update().only("user_id").get(pk=user.pk)
        yield


def generation(conversation, field="memory_generation"):
    return (
        Conversation.objects.filter(pk=conversation.pk, user_id=conversation.user_id)
        .values_list(field, flat=True)
        .first()
    )


def advance_memory(user):
    Conversation.objects.filter(user=user).update(memory_generation=F("memory_generation") + 1)


def advance_summary(user, character, *, manual=True):
    values = {"summary_generation": F("summary_generation") + 1}
    if manual:
        # Do not let a queued job rebuild deleted/corrected material from the
        # same old messages. Future turns may still generate a fresh summary.
        values["summary_source_cutoff"] = Message.objects.filter(
            conversation__user=user,
            conversation__character=character,
        ).aggregate(latest=Max("created_at"))["latest"]
    Conversation.objects.filter(user=user, character=character).update(**values)
