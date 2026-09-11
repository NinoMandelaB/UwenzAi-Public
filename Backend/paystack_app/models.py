# paystack_app/models.py
import uuid
from django.db import models
from django.db.models import Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from capp.models import TimeStampedModel, CustomUser
from .privacy import payment_audit_payload


class PaystackTransaction(TimeStampedModel):
    """
    The authoritative record of what a customer intended to buy.

    Everything needed to decide whether a payment may be credited — which
    package, at what price, in what currency, for whom, returning where — is
    written here *before* the customer is sent to Paystack. Provider payloads
    and webhook metadata are only ever cross-checked against these columns;
    they never supply them. See ``Backend/docs/PAYSTACK_TRANSACTION_INTEGRITY.md``.

    The intent columns are nullable because rows written before this model
    existed cannot have them backfilled without guessing a price. Constraints
    that would be unsafe against those rows are therefore conditional on
    ``package_slug`` being set, which marks a row as written by the current
    code path.
    """

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    class PackageType(models.TextChoices):
        SPARK = "spark", "Spark package"
        SUBSCRIPTION = "subscription", "Subscription plan"

    class FulfillmentState(models.TextChoices):
        # Nothing has been captured yet, so nothing is owed.
        UNFULFILLED = "unfulfilled", "Unfulfilled"
        # Money captured and value delivered.
        FULFILLED = "fulfilled", "Fulfilled"
        # Money captured but value could not be delivered (the package went
        # inactive, or a subscription conflict appeared after capture). Held for
        # the manual compensation queue rather than silently kept.
        MANUAL_REVIEW = "manual_review", "Awaiting manual review"

    # A payment may only move between these states. Anything else is a bug or an
    # attack, and is refused rather than applied.
    ALLOWED_STATUS_TRANSITIONS = {
        StatusChoices.PENDING: {StatusChoices.SUCCESS, StatusChoices.FAILED},
        StatusChoices.SUCCESS: {StatusChoices.REVERSED},
        StatusChoices.FAILED: set(),
        StatusChoices.REVERSED: set(),
    }

    transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paystack_transactions",
    )
    # For guest purchases — null for authenticated users, and released once an
    # account claims the guest.
    guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    # Immutable provenance: the guest that made this purchase. Never cleared, so
    # a claimed purchase stays visible in account history as guest-originated.
    origin_guest_session_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    claimed_from_guest_at = models.DateTimeField(blank=True, null=True)
    # Explicit erasure provenance, not a new owner or a transferable guest ID.
    # The financial record and fulfillment proof outlive the account.
    owner_deleted_at = models.DateTimeField(blank=True, null=True, editable=False)

    reference = models.CharField(max_length=100, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING)

    # ----- Purchase intent, written at initiation and never from a payload -----
    package_slug = models.CharField(max_length=120, blank=True, null=True, db_index=True)
    package_type = models.CharField(max_length=20, choices=PackageType.choices, blank=True, null=True)
    # Paystack works in the currency's minor unit. Storing the exact integer we
    # asked for removes any float/rounding disagreement at verification time.
    expected_amount_minor = models.BigIntegerField(blank=True, null=True)
    # An allowlisted route on a configured origin, not a client-supplied URL.
    callback_path = models.CharField(max_length=200, blank=True, null=True)

    fulfillment_state = models.CharField(
        max_length=20,
        choices=FulfillmentState.choices,
        default=FulfillmentState.UNFULFILLED,
    )

    paystack_access_code = models.CharField(max_length=100, blank=True, null=True)
    paystack_authorization_url = models.URLField(blank=True, null=True)

    paystack_reference = models.CharField(max_length=100, blank=True, null=True)
    paystack_transaction_id = models.BigIntegerField(blank=True, null=True)
    paystack_message = models.TextField(blank=True, null=True)
    paystack_data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "paystack_transactions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["guest_session_id", "status"]),
            models.Index(fields=["origin_guest_session_id"]),
            models.Index(fields=["status", "fulfillment_state"]),
        ]
        constraints = [
            # A payment belongs to an account or to a guest, never to both. A
            # claimed guest purchase moves to the account and releases
            # guest_session_id, so this holds across a claim too.
            models.CheckConstraint(
                condition=Q(user__isnull=True) | Q(guest_session_id__isnull=True),
                name="paystack_txn_single_owner",
            ),
            # New intents need an owner. Retained records may instead carry an
            # explicit erasure marker; legacy rows remain exempt.
            models.CheckConstraint(
                condition=(
                    Q(package_slug__isnull=True)
                    | Q(user__isnull=False)
                    | Q(guest_session_id__isnull=False)
                    | Q(origin_guest_session_id__isnull=False)
                    | Q(owner_deleted_at__isnull=False)
                ),
                name="paystack_txn_intent_has_owner",
            ),
            models.CheckConstraint(
                condition=Q(owner_deleted_at__isnull=True) | (Q(user__isnull=True) & Q(guest_session_id__isnull=True)),
                name="paystack_txn_erased_has_no_owner",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="paystack_txn_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(expected_amount_minor__isnull=True) | Q(expected_amount_minor__gt=0),
                name="paystack_txn_expected_amount_positive",
            ),
            # An intent is only usable if it is complete: knowing the slug but
            # not the price, type or currency would leave nothing to verify
            # against.
            models.CheckConstraint(
                condition=(
                    Q(package_slug__isnull=True)
                    | (Q(package_type__isnull=False) & Q(expected_amount_minor__isnull=False) & ~Q(currency=""))
                ),
                name="paystack_txn_intent_is_complete",
            ),
        ]

    def __str__(self):
        if self.owner_deleted_at:
            return f"Deleted account – {self.amount} {self.currency} ({self.status})"
        if self.user:
            return f"{self.user.get_display_name()} – {self.amount} {self.currency} ({self.status})"
        guest_hint = self.guest_session_id[:8] if self.guest_session_id else "?"
        return f"Guest({guest_hint}) – {self.amount} {self.currency} ({self.status})"

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_STATUS_TRANSITIONS.get(self.status, set())

    @property
    def owner_description(self) -> str:
        """A log-safe identifier for the owner. Never a credential."""
        if self.owner_deleted_at:
            return "owner:deleted"
        if self.user_id:
            return f"user:{self.user_id}"
        if self.guest_session_id:
            return f"guest:{self.guest_session_id}"
        if self.origin_guest_session_id:
            return f"claimed-guest:{self.origin_guest_session_id}"
        return "owner:unknown"


@receiver(pre_delete, sender=CustomUser)
def retain_payments_on_account_deletion(sender, instance, using, **kwargs):
    """Detach retained payments inside the deletion collector's transaction.

    Stamp and detach in the same UPDATE so neither ownership constraint is
    temporarily violated. This covers instance, queryset and admin deletion,
    not just the HTTP endpoint. Financial facts and guest provenance survive;
    unnecessary customer details and checkout credentials do not. Row locks
    serialize this scrub with capture (not the entire guest-claim protocol).
    """
    rows = PaystackTransaction.objects.using(using).select_for_update().filter(user_id=instance.pk)
    for payment in rows:
        event_match = Q(data__reference=payment.reference)
        if payment.paystack_transaction_id is not None:
            event_match |= Q(paystack_event_id=str(payment.paystack_transaction_id))
        events = PaystackWebhookEvent.objects.using(using).filter(event_match)
        for event in events:
            events.filter(pk=event.pk).update(data=payment_audit_payload(event.data), error_message=None)
        rows.filter(pk=payment.pk).update(
            user=None,
            guest_session_id=None,
            owner_deleted_at=timezone.now(),
            paystack_data=payment_audit_payload(payment.paystack_data),
            paystack_message=None,
            paystack_access_code=None,
            paystack_authorization_url=None,
        )


class PaystackFulfillment(TimeStampedModel):
    """
    Proof that a reference has already been credited.

    This row is the idempotency guarantee, and it is a database guarantee rather
    than an application one: ``transaction`` is one-to-one and ``reference`` is
    unique, so a second attempt to credit the same payment raises
    ``IntegrityError`` no matter which route it arrived by — a replayed webhook
    with a fresh event ID, a manual verification racing a webhook, or a retry
    after a timeout.
    """

    class Source(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"
        MANUAL_VERIFICATION = "manual_verification", "Manual verification"

    fulfillment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.OneToOneField(
        PaystackTransaction,
        on_delete=models.CASCADE,
        related_name="fulfillment",
    )
    # Denormalised from the transaction so the uniqueness guarantee survives
    # even if the transaction row is ever re-keyed.
    reference = models.CharField(max_length=100, unique=True)
    source = models.CharField(max_length=32, choices=Source.choices)
    package_slug = models.CharField(max_length=120)
    package_type = models.CharField(max_length=20, choices=PaystackTransaction.PackageType.choices)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    sparks_granted = models.BigIntegerField(default=0)

    class Meta:
        db_table = "paystack_fulfillments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Fulfillment {self.reference} ({self.package_slug})"


class PaystackWebhookEvent(TimeStampedModel):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=100)
    paystack_event_id = models.CharField(max_length=100, unique=True)
    data = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "paystack_webhook_events"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} – {self.paystack_event_id}"
