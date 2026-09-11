"""Remove account snapshots without deleting another person's case or ledger.

Call before private-content removal, inside the account-erasure transaction.
Only existing, explicitly owned objects identify Django admin-log targets; an
object ID alone is never enough. Historical already-deleted targets cannot be
reliably attributed by this schema.

Django's session table has no user index: session cleanup is a linear, streamed
scan (normally the small admin-session population), with exact-key deletes.
This module does not erase exported logs, backups or external-provider copies.
No payload is logged, and no retention duration is inferred.
"""

import uuid

from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.db import DEFAULT_DB_ALIAS
from django.db.models import Q
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from capp import models
from paystack_app.models import PaystackTransaction

_CHUNK_SIZE = 200
_ERASED_OBJECT = "Erased account content"
_ERASED_CREATOR = "Deleted account"


def scrub_audit_snapshots(user):
    """Scrub local snapshots before deleting ``user`` or its private objects.

    The caller owns the surrounding transaction. JTI/expiry/blacklist records,
    typed financial audit facts and other people's case updates survive.
    """
    using = user._state.db or DEFAULT_DB_ALIAS
    private_characters = models.Character.objects.using(using).filter(created_by=user, is_public=False)
    private_character_ids = list(private_characters.values_list("pk", flat=True))

    OutstandingToken.objects.using(using).filter(user=user).update(token="")
    _delete_account_sessions(user, using)
    _scrub_django_admin_snapshots(user, private_character_ids, using)
    _scrub_character_report_snapshots(user, using)
    _scrub_admin_actions(user, private_character_ids, using)


def _delete_account_sessions(user, using):
    account_id = str(user.pk)
    matching_keys = []
    sessions = Session.objects.using(using).only("session_key", "session_data")
    for session in sessions.iterator(chunk_size=_CHUNK_SIZE):
        if session.get_decoded().get("_auth_user_id") != account_id:
            continue
        matching_keys.append(session.pk)
        if len(matching_keys) == _CHUNK_SIZE:
            Session.objects.using(using).filter(pk__in=matching_keys).delete()
            matching_keys.clear()
    if matching_keys:
        Session.objects.using(using).filter(pk__in=matching_keys).delete()


def _scrub_django_admin_snapshots(user, private_character_ids, using):
    # Explicit ownership, not a text search for usernames. In particular, another
    # person's chats with an owned character are NOT this user's private chats.
    filters = {
        models.CustomUser: Q(pk=user.pk),
        models.UserBYOKConfig: Q(user=user),
        # Public content itself stays available, but __str__ also embeds the
        # erased creator's display name in its admin snapshot.
        models.Character: Q(created_by=user),
        models.CharacterGalleryImage: (Q(character_id__in=private_character_ids) | Q(created_by=user)),
        models.Conversation: Q(user=user),
        models.Message: Q(conversation__user=user),
        models.ConversationSummary: Q(conversation__user=user),
        models.MemoryFact: Q(user=user),
        models.MemoryEntity: Q(user=user),
        models.MemoryRelation: Q(user=user),
        models.RelationshipState: Q(user=user),
        models.Feedback: Q(user=user),
        models.SupportTicket: Q(created_by=user),
        models.TicketMessage: Q(sender=user) | Q(ticket__created_by=user),
        models.Reminder: Q(user=user),
        models.TokenUsageLog: Q(user=user),
        models.ModerationFlag: Q(user=user),
        models.ComplianceConfirmationLog: Q(user=user),
        models.PasswordRecovery: Q(user=user),
        models.EmailChangeRequest: Q(user=user),
        models.EmailVerification: Q(user=user),
        models.PushSubscription: Q(user=user),
        models.SparkPool: Q(user=user),
        models.UserSubscription: Q(user=user),
        models.SparkTransaction: Q(user=user),
        models.PaymentTransaction: Q(user=user),
        models.UsageCharge: Q(user=user),
        PaystackTransaction: Q(user=user),
    }
    content_types = {
        (app_label, model_name): pk
        for app_label, model_name, pk in ContentType.objects.using(using)
        .filter(
            Q(
                app_label="capp",
                model__in=[model._meta.model_name for model in filters if model is not PaystackTransaction],
            )
            | Q(app_label="paystack_app", model=PaystackTransaction._meta.model_name)
        )
        .values_list("app_label", "model", "pk")
    }
    for model, owner_filter in filters.items():
        content_type_id = content_types.get((model._meta.app_label, model._meta.model_name))
        if content_type_id is None:
            continue
        entries = LogEntry.objects.using(using).filter(content_type_id=content_type_id)
        if not entries.exists():
            continue
        # Python string conversion preserves Django's UUID representation on
        # both PostgreSQL and SQLite, unlike casting SQLite's UUID storage.
        object_ids = []
        owned_ids = model.objects.using(using).filter(owner_filter).values_list("pk", flat=True)
        for object_id in owned_ids.iterator(chunk_size=_CHUNK_SIZE):
            object_ids.append(str(object_id))
            if len(object_ids) == _CHUNK_SIZE:
                entries.filter(object_id__in=object_ids).update(object_repr=_ERASED_OBJECT, change_message="")
                object_ids.clear()
        if object_ids:
            entries.filter(object_id__in=object_ids).update(object_repr=_ERASED_OBJECT, change_message="")


def _redact_report_description(description, character):
    """Recognize the generated character-report snapshot, not arbitrary text."""
    if not isinstance(description, str) or not description.startswith("Flagged Character:\n"):
        return None
    identity_marker = f"\nCharacter ID: {character.pk}\nCharacter Slug: "
    body, has_identity, target_tail = description.partition(identity_marker)
    slug, has_creator, _creator_snapshot = target_tail.partition("\nCreated by: ")
    if not has_identity or not has_creator:
        return None
    if character.is_public:
        # Drop the entire creator suffix, including embedded newlines/another
        # "Created by:" inside a user-supplied display name.
        return f"{body}{identity_marker}{slug}\nCreated by: {_ERASED_CREATOR}"
    # Private persona details can also be quoted in the generated reason block.
    # Keep the case and opaque target, not that copied private narrative.
    return (
        "Reported private character (archived).\n"
        f"Character ID: {character.pk}\n"
        "Creator account deleted; private character details erased."
    )


def _generated_report_title(description):
    if not isinstance(description, str) or not description.startswith("Flagged Character:\n"):
        return None
    name, separator, _rest = description.removeprefix("Flagged Character:\n").partition("\n\nReason: ")
    return f"Reported character: {name}" if separator else None


def _scrub_character_report_snapshots(user, using):
    flags = models.CharacterFlag.objects.using(using).filter(character__created_by=user).select_related("character")
    for flag in flags.iterator(chunk_size=_CHUNK_SIZE):
        tickets = (
            models.SupportTicket.objects.using(using).filter(metadata__flag_id=str(flag.pk)).exclude(created_by=user)
        )
        for ticket in tickets.iterator(chunk_size=_CHUNK_SIZE):
            # create_ticket writes exactly one initial root message. Never
            # rewrite later replies/notes, even if they repeat the same text.
            initial = (
                models.TicketMessage.objects.using(using)
                .filter(ticket=ticket, sender_id=ticket.created_by_id, parent__isnull=True, is_internal=False)
                .order_by("created_at", "pk")
                .first()
            )
            changes = {}
            generated_titles = set()
            redacted = _redact_report_description(ticket.description, flag.character)
            if redacted is not None:
                generated_titles.add(_generated_report_title(ticket.description))
                changes["description"] = redacted
            if initial is not None:
                redacted_initial = _redact_report_description(initial.content, flag.character)
                if redacted_initial is not None:
                    generated_titles.add(_generated_report_title(initial.content))
                    models.TicketMessage.objects.using(using).filter(pk=initial.pk).update(content=redacted_initial)
            if not flag.character.is_public and ticket.title in generated_titles:
                changes["title"] = "Reported private character (archived)"
            if changes:
                models.SupportTicket.objects.using(using).filter(pk=ticket.pk).update(**changes)


def _audit_facts(metadata):
    """Keep known typed event facts, not free-form author names or explanations."""
    if not isinstance(metadata, dict):
        return {}
    facts = {}
    for key in ("spark_delta", "sparks_before", "sparks_after"):
        value = metadata.get(key)
        if type(value) is int:
            facts[key] = value
    for key in ("old_status", "new_status"):
        value = metadata.get(key)
        if isinstance(value, str) and value in models.CustomUser.AccountStatus.values:
            facts[key] = value
    value = metadata.get("spark_transaction_id")
    if isinstance(value, str):
        try:
            facts["spark_transaction_id"] = str(uuid.UUID(value))
        except ValueError:
            pass
    cascaded = metadata.get("cascaded")
    if isinstance(cascaded, dict):
        counts = {
            key: cascaded[key]
            for key in ("gallery_images", "proactive_chat_preferences", "relationship_states")
            if type(cascaded.get(key)) is int and cascaded[key] >= 0
        }
        if counts:
            facts["cascaded"] = counts
    return facts


def _scrub_admin_actions(user, private_character_ids, using):
    private_ids = set(private_character_ids)
    actions = models.AdminActionLog.objects.using(using).filter(
        Q(admin_user=user) | Q(target_user=user) | Q(target_character_id__in=private_ids)
    )
    for action in actions.iterator(chunk_size=_CHUNK_SIZE):
        changes = {"reason": None, "metadata": _audit_facts(action.metadata)}
        if action.target_character_id in private_ids:
            changes["target_character_name"] = None
        models.AdminActionLog.objects.using(using).filter(pk=action.pk).update(**changes)
