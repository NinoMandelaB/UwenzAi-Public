# play_billing_app/models.py
import uuid
from hashlib import sha256

from django.db import models
from django.db.models import Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from capp.models import TimeStampedModel, CustomUser


def hash_purchase_token(purchase_token: str) -> str:
    """
    Stable, indexable digest of a Play purchase token.

    The raw token is what the Play Developer API expects, so it is retained on
    the row for acknowledgment retries — but every lookup goes through this
    hash instead, because tokens are long opaque strings and the hash doubles
    as the public, log-safe handle for a purchase. Ledger references use the
    same hash (see ``PlayBillingService``) so SparkTransaction rows never carry
    raw provider tokens.
    """
    return sha256(purchase_token.encode("utf-8")).hexdigest()


class PlayPurchase(TimeStampedModel):
    """
    The authoritative record of a Google Play in-app purchase of Sparks.

    Play purchases have no initiation step on our side — the customer pays
    inside the Play sheet and the client only hands us the resulting purchase
    token afterwards. The idempotency guarantee therefore lives entirely on
    this row: ``purchase_token_hash`` is unique, the row is created in the same
    atomic block that credits the Spark pool, and a resubmitted token finds
    the row and is never credited twice. This mirrors the database-level
    guarantee ``PaystackFulfillment`` gives the Paystack flow (see
    ``paystack_app/models.py``) using the constraint available here: a
    purchase that is credited *is* the row.

    States:

    * ``credited`` — verified against the Play Developer API, Sparks granted
      through the shared ChargeService path, acknowledged (or awaiting a
      best-effort acknowledge retry).
    * ``manual_review`` — Google confirmed the purchase, but the mapped Spark
      package was inactive, so nothing could be granted. Deliberately *not*
      acknowledged: Play auto-refunds unacknowledged purchases after three
      days, which is the correct outcome for money we could not turn into
      Sparks. The row keeps the evidence for the compensation queue.
    * ``reversed`` — refunded/voided by Google; granted Sparks were clawed
      back through the same row-locked ledger primitives (partial clawback is
      recorded when the balance was already spent).
    """

    class Status(models.TextChoices):
        CREDITED = "credited", "Credited"
        MANUAL_REVIEW = "manual_review", "Awaiting manual review"
        REVERSED = "reversed", "Reversed"

    play_purchase_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The idempotency key. A token is credited once, no matter which route it
    # arrives by or how many times it is resubmitted.
    purchase_token_hash = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    # Retained so acknowledgment and reconciliation can still reach the Play
    # API. Never returned by any API response or written to a ledger row.
    purchase_token = models.TextField(editable=False)

    product_id = models.CharField(max_length=64, editable=False)
    package_slug = models.CharField(max_length=120, editable=False)
    order_id = models.CharField(max_length=64, blank=True, null=True)

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="play_purchases",
    )
    # Explicit erasure marker, matching PaystackTransaction: the financial
    # evidence outlives the account while the ownership link does not.
    owner_deleted_at = models.DateTimeField(blank=True, null=True, editable=False)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREDITED)
    sparks_granted = models.BigIntegerField(default=0)

    acknowledged = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(blank=True, null=True)
    reversed_at = models.DateTimeField(blank=True, null=True)

    # Minimized provider evidence — see privacy.py. No customer PII.
    play_data = models.JSONField(default=dict, blank=True)
    # Reconciliation facts (e.g. sparks_reversed / shortfall on refunds).
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "play_purchases"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            # An erased purchase must not keep a live owner. Live purchases
            # are created with a user; only erasure detaches them.
            models.CheckConstraint(
                condition=Q(owner_deleted_at__isnull=True) | Q(user__isnull=True),
                name="play_purchase_erased_has_no_owner",
            ),
        ]

    def __str__(self):
        if self.owner_deleted_at:
            return f"Deleted account – {self.product_id} ({self.status})"
        if self.user:
            return f"{self.user.get_display_name()} – {self.product_id} ({self.status})"
        return f"{self.product_id} ({self.status})"

    @property
    def owner_description(self) -> str:
        """A log-safe identifier for the owner. Never a credential."""
        if self.owner_deleted_at:
            return "owner:deleted"
        if self.user_id:
            return f"user:{self.user_id}"
        return "owner:unknown"


class PlayVoidedPurchase(TimeStampedModel):
    """
    A refund/chargeback seen through the Play ``voidedpurchases`` API.

    One row per voided purchase token: the unique hash makes reconciliation
    idempotent (a re-delivered or re-polled void event is a no-op) and the
    stored ``voided_time_millis`` doubles as the polling checkpoint for the
    next scheduled run.
    """

    play_voided_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_token_hash = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    order_id = models.CharField(max_length=64, blank=True, null=True)
    voided_time_millis = models.BigIntegerField(editable=False)
    # Play reports these as small integers (reason/source enums); kept as
    # strings so a future enum value can never break the row.
    voided_reason = models.CharField(max_length=16, blank=True, null=True)
    voided_source = models.CharField(max_length=16, blank=True, null=True)
    # True once the matching PlayPurchase (if any) has been dealt with.
    processed = models.BooleanField(default=False)

    class Meta:
        db_table = "play_voided_purchases"
        ordering = ["voided_time_millis"]

    def __str__(self):
        return f"Voided {self.order_id or self.purchase_token_hash[:12]}"


@receiver(pre_delete, sender=CustomUser)
def retain_play_purchases_on_account_deletion(sender, instance, using, **kwargs):
    """
    Keep the purchase evidence when an account is deleted.

    Financial records must outlive the customer row for accounting (see the
    Paystack equivalent in paystack_app/models.py). Sparks granted and refund
    facts stay; the ownership link is severed and stamped so no later
    reconciliation can debit a deleted account. ``play_data`` already contains
    only provider purchase facts, never customer details, so it is retained.
    """
    PlayPurchase.objects.using(using).select_for_update().filter(user_id=instance.pk).update(
        user=None,
        owner_deleted_at=timezone.now(),
    )
