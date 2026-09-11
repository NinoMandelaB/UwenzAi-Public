"""Account erasure: retain shared content/accounting, not private payloads."""

import secrets
import uuid

from django.db.models import Q
from django.utils import timezone

from capp.models import (
    BillingReservation,
    Character,
    CharacterFlag,
    CharacterGalleryImage,
    Conversation,
    Feedback,
    GuestIdentity,
    GuestTokenQuota,
    GuestWebSocketTicket,
    MessageFlag,
    ModerationFlag,
    PaymentTransaction,
    SparkTransaction,
    UsageCharge,
    delete_file_after_commit,
)


def erase_private_content(user):
    """Run inside the account-deletion transaction, before collecting the user.

    Snapshot history before deleting the owner's conversations. Public personas
    remain available; previously chatted/flagged private personas become inert
    shells so other people's history is not cascaded away.
    """
    from capp.classes.auth.erasure_audit import scrub_audit_snapshots

    scrub_audit_snapshots(user)
    for character in Character.objects.select_for_update().filter(created_by=user):
        if character.is_public:
            # Shared fictional content is functionally needed, creator-only
            # operational metadata is not. SET_NULL detaches attribution later.
            Character.objects.filter(pk=character.pk).update(client_operation_id=None, rejection_reason=None)
            continue
        if not character.conversations.exists() and not character.character_flags.exists():
            character.delete()
            continue

        delete_file_after_commit(character.avatar)
        Character.objects.filter(pk=character.pk).update(
            status=Character.StatusChoices.ARCHIVED,
            is_active=False,
            is_featured=False,
            name="Archived character",
            slug=f"archived-{character.pk.hex}",
            avatar="",
            short_description="",
            mood="",
            intro="",
            personality_description="",
            prompt_template="",
            system_instructions=None,
            greeting_message="",
            tone_style="",
            country="",
            town="",
            timezone=None,
            occupation=None,
            rejection_reason=None,
            client_operation_id=None,
            updated_at=timezone.now(),
        )
        character.personalities.clear()
        character.hobbies.clear()
        character.conversations.exclude(status=Conversation.StatusChoices.CLOSED).update(
            status=Conversation.StatusChoices.CLOSED, updated_at=timezone.now()
        )
        # A gallery on a private persona is private even if its own visibility
        # was accidentally/defaulted to public. Do not retain those files.
        character.gallery_images.all().delete()

    Conversation.objects.filter(user=user).delete()
    CharacterGalleryImage.objects.filter(
        created_by=user, visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE
    ).delete()
    Feedback.objects.filter(user=user).delete()


def scrub_retained_personal_data(user, using):
    """Erase free text/credentials, keeping typed ledger and anti-replay facts.

    No retention period or legal sufficiency is inferred here. Opaque guest
    provenance remains necessary for preventing a second claim/refund.
    """
    ledgers = SparkTransaction.objects.using(using).filter(Q(user=user) | Q(metadata__admin_user_id=str(user.pk)))
    for ledger in ledgers:
        ledgers.filter(pk=ledger.pk).update(description=None, metadata=accounting_metadata(ledger.metadata))
    PaymentTransaction.objects.using(using).filter(user=user).update(
        provider_payload={}, metadata={}, failure_reason=None
    )
    UsageCharge.objects.using(using).filter(user=user).update(notes=None)
    BillingReservation.objects.using(using).filter(claimed_by=user).update(settlement_reason=None, refund_reason=None)
    identities = GuestIdentity.objects.using(using).filter(claimed_by=user)
    guest_keys = [str(key) for key in identities.values_list("pk", flat=True)]
    guest_keys.extend(GuestTokenQuota.objects.using(using).filter(claimed_by=user).values_list("session_id", flat=True))
    reporter = Q(reported_by=user) | Q(guest_session_id__in=guest_keys)
    CharacterFlag.objects.using(using).filter(reporter).update(reason="Reporter account deleted", guest_session_id=None)
    MessageFlag.objects.using(using).filter(reporter).update(
        reason="Reporter account deleted", resolution_note=None, guest_session_id=None
    )
    MessageFlag.objects.using(using).filter(reviewed_by=user).update(resolution_note=None)
    ModerationFlag.objects.using(using).filter(reviewed_by=user).update(resolution_note=None)
    GuestWebSocketTicket.objects.using(using).filter(guest_identity__in=identities).delete()
    # Keep the non-usable claim tombstone and stable owner ID, not bearer hashes.
    for identity in identities:
        identities.filter(pk=identity.pk).update(
            credential_hash=secrets.token_hex(32),
            previous_credential_hash=None,
            migrated_from_legacy_session_id=None,
        )
    GuestTokenQuota.objects.using(using).filter(Q(claimed_by=user) | Q(session_id__in=guest_keys)).update(
        ip_address=None, user_agent=None
    )


def accounting_metadata(value):
    """Retain only typed financial facts and opaque internal provenance."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("reservation_id", "origin_guest_session_id", "subscription_id"):
        try:
            result[key] = str(uuid.UUID(str(value[key])))
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
    if value.get("feature") in BillingReservation.Feature.values:
        result["feature"] = value["feature"]
    spent = value.get("subscription_sparks_spent")
    if isinstance(spent, int) and not isinstance(spent, bool) and spent >= 0:
        result["subscription_sparks_spent"] = spent
    if isinstance(value.get("admin_adjustment"), bool):
        result["admin_adjustment"] = value["admin_adjustment"]
    return result
