# paystack_app/services.py
"""
Paystack initiation, verification and fulfillment.

The rule this module enforces is that a payment is credited on the strength of
what we recorded *before* sending the customer to Paystack, never on what comes
back. The provider payload and its ``metadata`` are treated as a cross-check:
they must agree with the stored intent, and disagreement is a rejection. Nothing
in a payload ever selects a package, a price, an owner or a redirect.

Fulfillment is idempotent at the database level rather than the application
level — see :class:`paystack_app.models.PaystackFulfillment`. Validation, the
status transition and the Spark/subscription grant all happen inside one
``transaction.atomic()`` while holding a row lock on the transaction, so two
concurrent deliveries of the same payment produce one net credit.
"""

import logging
import hmac
import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional

import requests
from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from .callbacks import build_callback_url
from .models import PaystackFulfillment, PaystackTransaction, PaystackWebhookEvent
from .privacy import payment_audit_payload
from capp.models import CustomUser

logger = logging.getLogger(__name__)

# A subscription may only be replaced once the sparks it granted are nearly
# spent, so a customer cannot stack plans. Mirrors ChargeService's threshold.
SUBSCRIPTION_UPGRADE_THRESHOLD = 500


class FulfillmentFailed(Exception):
    """
    Raised to unwind an atomic fulfillment whose grant step failed unexpectedly.

    Unwinding leaves the transaction PENDING rather than SUCCESS, so nothing was
    captured in our records and the delivery can be retried safely. Business
    conflicts that we *expect* are detected before the grant is attempted (see
    ``_resolve_grant``) so that they land in manual review instead.
    """


class PaystackService:
    BASE_URL = settings.PAYSTACK_BASE_URL
    SECRET_KEY = settings.PAYSTACK_SECRET_KEY
    PUBLIC_KEY = settings.PAYSTACK_PUBLIC_KEY
    WEBHOOK_SECRET = getattr(settings, "PAYSTACK_WEBHOOK_SECRET", None)

    # ------------------------------------------------------------------
    # Initialize a payment
    # ------------------------------------------------------------------
    @classmethod
    def initialize_payment(
        cls,
        user: Optional[CustomUser],
        amount,
        email: str,
        package_slug: str,
        package_type: str,
        currency: str = "KES",
        metadata: dict = None,
        callback_path: str = None,
        guest_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record the purchase intent, then ask Paystack to initialize against it.

        ``amount`` is the major-unit price already resolved from the DB by the
        caller. The minor-unit integer we send to Paystack is computed once here
        and stored, so verification later compares integers we chose rather than
        re-deriving a price that may have changed in the meantime.
        """
        currency = (currency or "").strip().upper()
        try:
            expected_amount_minor = int((Decimal(str(amount)) * 100).to_integral_value())
        except (InvalidOperation, TypeError, ValueError):
            logger.error("Refusing to initialize payment: unparseable amount for slug=%s", package_slug)
            return {"status": "fail", "data": "invalid_amount"}

        if expected_amount_minor <= 0:
            logger.error("Refusing to initialize payment: non-positive amount for slug=%s", package_slug)
            return {"status": "fail", "data": "invalid_amount"}

        try:
            reference = f"txn_{uuid.uuid4().hex[:16]}"

            paystack_txn = PaystackTransaction.objects.create(
                user=user,  # None for guests
                guest_session_id=guest_session_id,  # set for guests
                reference=reference,
                amount=Decimal(str(amount)),
                currency=currency,
                status=PaystackTransaction.StatusChoices.PENDING,
                package_slug=package_slug,
                package_type=package_type,
                expected_amount_minor=expected_amount_minor,
                callback_path=callback_path or None,
            )

            headers = {
                "Authorization": f"Bearer {cls.SECRET_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "email": email,
                "amount": expected_amount_minor,
                "reference": reference,
                "currency": currency,
                "metadata": metadata or {},
            }
            # Built from server configuration, never from the request. Absent
            # config means no callback_url is sent and the Paystack dashboard
            # default applies.
            absolute_callback = build_callback_url(callback_path) if callback_path else ""
            if absolute_callback:
                payload["callback_url"] = absolute_callback

            response = requests.post(
                f"{cls.BASE_URL}/transaction/initialize",
                json=payload,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("status"):
                logger.error("Paystack initialize failed: %s", data.get("message"))
                cls._mark_failed(paystack_txn, str(data.get("message", ""))[:500])
                return {"status": "fail", "data": "paystack_error"}

            paystack_data = data["data"]
            # A provider response can arrive after erasure. Do not restore
            # checkout credentials through a stale in-memory model instance.
            updated = PaystackTransaction.objects.filter(
                pk=paystack_txn.pk,
                owner_deleted_at__isnull=True,
                user_id=getattr(user, "pk", None),
                guest_session_id=guest_session_id,
            ).update(
                paystack_access_code=paystack_data.get("access_code"),
                paystack_authorization_url=paystack_data.get("authorization_url"),
                updated_at=timezone.now(),
            )
            if not updated:
                return {"status": "fail", "data": "payment_owner_changed"}

            return {
                "status": "success",
                "data": {
                    "reference": reference,
                    "authorization_url": paystack_data.get("authorization_url"),
                    "access_code": paystack_data.get("access_code"),
                },
            }

        except requests.exceptions.RequestException:
            # The intent row stays PENDING rather than FAILED: we do not know
            # whether Paystack registered the reference, and a PENDING intent
            # can still be verified later. It can never be credited without a
            # matching provider success.
            logger.exception("Paystack API error during initialize")
            return {"status": "fail", "data": "paystack_unavailable"}
        except Exception:
            logger.exception("Unexpected error in initialize_payment")
            return {"status": "fail", "data": "server_error"}

    @staticmethod
    def _mark_failed(paystack_txn: PaystackTransaction, message: str) -> None:
        if not paystack_txn.can_transition_to(PaystackTransaction.StatusChoices.FAILED):
            return
        PaystackTransaction.objects.filter(
            pk=paystack_txn.pk,
            status=PaystackTransaction.StatusChoices.PENDING,
            owner_deleted_at__isnull=True,
        ).update(
            status=PaystackTransaction.StatusChoices.FAILED,
            paystack_message="provider_initialization_failed",
            updated_at=timezone.now(),
        )

    # ------------------------------------------------------------------
    # Process incoming webhook
    # ------------------------------------------------------------------
    @classmethod
    def process_webhook(
        cls,
        payload: dict,
        signature: str = None,
        raw_body: bytes = None,
    ) -> Dict[str, Any]:
        try:
            event = payload.get("event")
            data = payload.get("data", {})

            if not event or not data:
                return {"status": "fail", "data": "invalid_payload"}

            # ---- Fail closed: never process a webhook we cannot verify ----
            if not cls.WEBHOOK_SECRET:
                logger.warning("SECURITY: Paystack webhook rejected — webhook secret is not configured")
                return {"status": "fail", "data": "webhook_secret_not_configured"}

            if not signature or not raw_body:
                logger.warning("SECURITY: Paystack webhook rejected — missing signature or raw body")
                return {"status": "fail", "data": "missing_signature"}

            if not cls._verify_signature(raw_body, signature):
                logger.warning("SECURITY: Paystack webhook rejected — invalid signature")
                return {"status": "fail", "data": "invalid_signature"}
            # ---------------------------------------------------------------

            paystack_event_id = str(data.get("id", ""))
            if paystack_event_id:
                if PaystackWebhookEvent.objects.filter(paystack_event_id=paystack_event_id).exists():
                    logger.info("Duplicate webhook event %s ignored", paystack_event_id)
                    return {"status": "success", "data": "duplicate_ignored"}

            try:
                webhook_log = PaystackWebhookEvent.objects.create(
                    event_type=event,
                    paystack_event_id=paystack_event_id or "",
                    # Keep reconciliation facts, not customer/card/IP details.
                    # Always minimize new events, including late replays after
                    # erasure and events racing an account deletion.
                    data=payment_audit_payload(data),
                    processed=False,
                )
            except IntegrityError:
                # Two deliveries of the same event id raced the check above. The
                # event-id column is unique, so the loser simply stops here.
                logger.info("Duplicate webhook event %s ignored (raced)", paystack_event_id)
                return {"status": "success", "data": "duplicate_ignored"}

            if event == "charge.success":
                cls._handle_charge_success(data, webhook_log)
            else:
                webhook_log.processed = True
                webhook_log.save(update_fields=["processed"])

            return {"status": "success", "data": "webhook_processed"}

        except Exception:
            logger.exception("Error processing webhook")
            return {"status": "fail", "data": "server_error"}

    # ------------------------------------------------------------------
    # Charge success handler
    # ------------------------------------------------------------------
    @classmethod
    def _handle_charge_success(cls, data: dict, webhook_log: PaystackWebhookEvent):
        reference = data.get("reference")
        if not reference:
            webhook_log.error_message = "No reference in charge.success"
            webhook_log.processed = True
            webhook_log.save(update_fields=["error_message", "processed"])
            return

        result = cls.fulfill_reference(
            reference=reference,
            provider_data=data,
            source=PaystackFulfillment.Source.WEBHOOK,
        )

        webhook_log.processed = True
        if result.get("status") != "success":
            webhook_log.error_message = str(result.get("data"))[:500]
            webhook_log.save(update_fields=["error_message", "processed"])
        else:
            webhook_log.save(update_fields=["processed"])

    # ------------------------------------------------------------------
    # Verification against the provider (manual / callback-return path)
    # ------------------------------------------------------------------
    @classmethod
    def verify_and_fulfill(cls, reference: str) -> Dict[str, Any]:
        """
        Ask Paystack for the authoritative state of a reference, then fulfill.

        Used when the customer returns from the redirect before the webhook has
        landed. A provider error leaves everything untouched so the call is safe
        to retry; only a provider-confirmed success can credit anything.
        """
        if not cls.SECRET_KEY:
            logger.error("Cannot verify payment: Paystack secret key is not configured")
            return {"status": "fail", "data": "paystack_not_configured"}

        try:
            response = requests.get(
                f"{cls.BASE_URL}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {cls.SECRET_KEY}"},
                timeout=20,
            )
            response.raise_for_status()
            body = response.json()
        except requests.exceptions.RequestException:
            logger.exception("Paystack API error during verify of a payment reference")
            return {"status": "fail", "data": "paystack_unavailable"}
        except ValueError:
            logger.exception("Paystack verify returned a non-JSON body")
            return {"status": "fail", "data": "paystack_unavailable"}

        if not body.get("status"):
            return {"status": "fail", "data": "provider_verification_failed"}

        provider_data = body.get("data") or {}
        if str(provider_data.get("status", "")).lower() != "success":
            return {"status": "fail", "data": "payment_not_successful"}

        return cls.fulfill_reference(
            reference=reference,
            provider_data=provider_data,
            source=PaystackFulfillment.Source.MANUAL_VERIFICATION,
        )

    # ------------------------------------------------------------------
    # Validate + transition + fulfill, atomically
    # ------------------------------------------------------------------
    @classmethod
    def fulfill_reference(cls, reference: str, provider_data: dict, source: str) -> Dict[str, Any]:
        """
        Credit a confirmed payment exactly once, or refuse and explain why.

        Everything happens under a row lock on the transaction so that a webhook
        and a manual verification arriving together serialise; the loser sees the
        fulfillment already recorded and returns ``already_fulfilled``.
        """
        try:
            with db_transaction.atomic():
                try:
                    # ``user`` is nullable, so select_related would add an outer
                    # join and PostgreSQL refuses to lock through one. Lock the
                    # transaction row only; that is the row idempotency turns on.
                    txn = PaystackTransaction.objects.select_for_update().get(reference=reference)
                except PaystackTransaction.DoesNotExist:
                    logger.error("Paystack reference not found in local DB")
                    return {"status": "fail", "data": "transaction_not_found"}

                if cls._already_fulfilled(txn):
                    logger.info("Reference already fulfilled for %s; ignoring replay", txn.owner_description)
                    return {"status": "success", "data": "already_fulfilled"}

                validation = cls._validate_against_intent(txn, provider_data)
                if validation is not None:
                    # A failed validation must never look like a success and must
                    # never credit anything. It is still flagged for review,
                    # because a provider-confirmed charge we refused to honour may
                    # need refunding.
                    cls._reject_capture(txn, validation)
                    return {"status": "fail", "data": validation}

                grant = cls._resolve_grant(txn)
                if grant.get("status") != "success":
                    # The money is captured but we cannot deliver value. Record
                    # the capture honestly and queue it for compensation rather
                    # than keeping the payment silently.
                    cls._record_capture(txn, provider_data, PaystackTransaction.FulfillmentState.MANUAL_REVIEW)
                    logger.error(
                        "SECURITY/BILLING: captured payment cannot be fulfilled (%s) for %s — queued for manual review",
                        grant.get("data"),
                        txn.owner_description,
                    )
                    return {"status": "fail", "data": grant.get("data")}

                fulfillment_result = cls._apply_fulfillment(txn, source, grant["data"])
                if fulfillment_result.get("status") != "success":
                    return fulfillment_result

                cls._record_capture(txn, provider_data, PaystackTransaction.FulfillmentState.FULFILLED)
                return {"status": "success", "data": "fulfilled"}

        except IntegrityError:
            # An FK/CHECK failure (e.g. concurrent erasure) is not proof that
            # value was delivered. Only committed fulfillment evidence is.
            if PaystackFulfillment.objects.filter(reference=reference).exists():
                return {"status": "success", "data": "already_fulfilled"}
            logger.exception("Payment fulfillment integrity failure; no committed fulfillment")
            return {"status": "fail", "data": "fulfillment_integrity_error"}
        except FulfillmentFailed as exc:
            # Rolled back: still PENDING, nothing credited, safe to retry.
            logger.error("Fulfillment rolled back and left retryable: %s", exc)
            return {"status": "fail", "data": str(exc)}
        except Exception:
            logger.exception("Unexpected error fulfilling a Paystack reference")
            return {"status": "fail", "data": "server_error"}

    @staticmethod
    def _already_fulfilled(txn: PaystackTransaction) -> bool:
        if txn.fulfillment_state != PaystackTransaction.FulfillmentState.UNFULFILLED:
            return True
        return PaystackFulfillment.objects.filter(reference=txn.reference).exists()

    # ------------------------------------------------------------------
    # Provider payload vs stored intent
    # ------------------------------------------------------------------
    @classmethod
    def _validate_against_intent(cls, txn: PaystackTransaction, provider_data: dict) -> Optional[str]:
        """
        Return None when the provider result matches the stored intent, else a
        short machine-readable reason.

        Reasons are deliberately terse and the log lines carry no card, customer
        or credential data — only the reference-scoped facts needed to triage.
        """
        # No recorded intent means there is nothing to validate against, so there
        # is no safe way to decide what this payment bought. Legacy rows land
        # here and are reconciled by hand.
        if not txn.package_slug or not txn.package_type or txn.expected_amount_minor is None:
            logger.error("SECURITY: refusing to fulfill a payment with no recorded purchase intent")
            return "no_recorded_intent"

        provider_status = str(provider_data.get("status", "")).lower()
        if provider_status and provider_status != "success":
            return f"provider_status_not_success:{provider_status[:32]}"

        provider_reference = provider_data.get("reference")
        if provider_reference is not None and str(provider_reference) != txn.reference:
            logger.warning("SECURITY: Paystack payload reference does not match the transaction it was matched to")
            return "reference_mismatch"

        # ---- Amount: exact integer comparison in minor units ----
        raw_amount = provider_data.get("amount")
        try:
            paid_minor = int(raw_amount)
        except (TypeError, ValueError):
            logger.warning("SECURITY: Paystack payload carried an unusable amount")
            return "amount_unreadable"

        if paid_minor != int(txn.expected_amount_minor):
            logger.warning(
                "SECURITY: Paystack amount mismatch for %s — expected=%s paid=%s; not crediting",
                txn.owner_description,
                txn.expected_amount_minor,
                paid_minor,
            )
            return f"amount_mismatch expected={txn.expected_amount_minor} paid={paid_minor}"

        # ---- Currency: exact match against the intent ----
        provider_currency = str(provider_data.get("currency", "") or "").strip().upper()
        expected_currency = (txn.currency or "").strip().upper()
        if not provider_currency:
            logger.warning("SECURITY: Paystack payload carried no currency")
            return "currency_missing"
        if provider_currency != expected_currency:
            logger.warning(
                "SECURITY: Paystack currency mismatch for %s — expected=%s paid=%s; not crediting",
                txn.owner_description,
                expected_currency,
                provider_currency,
            )
            return f"currency_mismatch expected={expected_currency} paid={provider_currency}"

        # ---- Metadata: cross-check only, never a source of truth ----
        metadata = provider_data.get("metadata") or {}
        if isinstance(metadata, dict):
            mismatch = cls._metadata_disagreement(txn, metadata)
            if mismatch:
                logger.warning(
                    "SECURITY: Paystack metadata disagrees with the recorded intent for %s (%s); not crediting",
                    txn.owner_description,
                    mismatch,
                )
                return f"metadata_mismatch:{mismatch}"

        return None

    @staticmethod
    def _metadata_disagreement(txn: PaystackTransaction, metadata: dict) -> Optional[str]:
        """
        Name the first metadata field that contradicts the intent.

        Absent fields are fine — Paystack does not guarantee echoing metadata —
        but a *present* field that disagrees means either tampering or a bug, and
        either way the payment must not be credited on a guess.
        """
        claimed_slug = metadata.get("package_slug")
        if claimed_slug is not None and str(claimed_slug) != txn.package_slug:
            return "package_slug"

        claimed_type = metadata.get("package_type")
        if claimed_type is not None and str(claimed_type) != txn.package_type:
            return "package_type"

        claimed_guest = metadata.get("guest_session_id")
        if claimed_guest is not None:
            # Ownership comes from the row. A payload claiming a different guest
            # is an attempt to redirect value.
            expected_guest = txn.guest_session_id or txn.origin_guest_session_id
            if str(claimed_guest) != str(expected_guest):
                return "guest_session_id"

        return None

    # ------------------------------------------------------------------
    # Eligibility at fulfillment time
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_grant(cls, txn: PaystackTransaction) -> Dict[str, Any]:
        """
        Re-check the fulfillment-critical parts of eligibility after capture.

        The price is *not* re-checked here: the customer paid what we quoted, and
        a price change since then is our problem, not theirs. What must still
        hold is that the thing they bought is deliverable at all.
        """
        from capp.models import SparkPackage, SubscriptionPlan

        if txn.owner_deleted_at:
            # A late capture must enter compensation review, never fall back
            # to origin_guest_session_id and resurrect a claimed guest balance.
            return {"status": "fail", "data": "payment_owner_deleted"}
        if not txn.user_id and not txn.guest_session_id:
            # Also covers historical origin-only rows and a claim/deletion
            # race that detaches ownership without the erasure timestamp.
            return {"status": "fail", "data": "payment_owner_missing"}

        if txn.package_type == PaystackTransaction.PackageType.SPARK:
            pkg = SparkPackage.objects.filter(slug=txn.package_slug, is_active=True).first()
            if pkg is None:
                return {"status": "fail", "data": "spark_package_unavailable"}
            return {"status": "success", "data": {"sparks": int(pkg.base_spark_amount)}}

        if txn.package_type == PaystackTransaction.PackageType.SUBSCRIPTION:
            if txn.user is None:
                return {"status": "fail", "data": "subscription_without_account"}
            plan = SubscriptionPlan.objects.filter(slug=txn.package_slug, is_active=True).first()
            if plan is None:
                return {"status": "fail", "data": "subscription_plan_unavailable"}

            # Eligibility is checked before initiation too, but the customer may
            # have acquired sparks while on Paystack's page. Detecting the
            # conflict *here* rather than inside the grant is what lets the
            # capture be recorded for compensation instead of silently rolled
            # back with the money taken.
            from capp.models import UserSubscription

            existing = UserSubscription.objects.filter(
                user=txn.user,
                status=UserSubscription.StatusChoices.ACTIVE,
            ).first()
            if existing is not None and existing.sparks_remaining >= SUBSCRIPTION_UPGRADE_THRESHOLD:
                return {"status": "fail", "data": "too_many_sparks_remaining"}

            return {"status": "success", "data": {"sparks": int(plan.included_sparks)}}

        return {"status": "fail", "data": "invalid_package_type"}

    # ------------------------------------------------------------------
    # Apply the grant
    # ------------------------------------------------------------------
    @classmethod
    def _apply_fulfillment(cls, txn: PaystackTransaction, source: str, grant: dict) -> Dict[str, Any]:
        from capp.classes.billing.charge_service import ChargeService

        if txn.user_id:
            result = ChargeService.process_successful_payment(
                user=txn.user,
                package_slug=txn.package_slug,
                package_type=txn.package_type,
                paystack_reference=txn.reference,
            )
        else:
            owner_key = txn.guest_session_id
            if not owner_key:
                return {"status": "fail", "data": "guest_owner_missing"}
            if txn.package_type != PaystackTransaction.PackageType.SPARK:
                return {"status": "fail", "data": "guest_can_only_buy_sparks"}
            result = ChargeService.process_guest_spark_payment(
                guest_session_id=owner_key,
                package_slug=txn.package_slug,
                paystack_reference=txn.reference,
            )

        if result.get("status") != "success":
            # Raise so the whole atomic block unwinds — a partial grant with a
            # SUCCESS status recorded would be worse than no grant at all.
            raise FulfillmentFailed(str(result.get("data", "fulfillment_failed")))

        granted = int(result.get("sparks_granted", grant.get("sparks", 0)) or 0)

        PaystackFulfillment.objects.create(
            transaction=txn,
            reference=txn.reference,
            source=source,
            package_slug=txn.package_slug,
            package_type=txn.package_type,
            amount_minor=int(txn.expected_amount_minor),
            currency=txn.currency,
            sparks_granted=granted,
        )
        return {"status": "success", "data": "granted"}

    @staticmethod
    def _reject_capture(txn: PaystackTransaction, reason: str) -> None:
        """
        Refuse a payment: never SUCCESS, never credited, always visible.

        The provider payload is deliberately *not* stored here — a payload that
        failed validation is untrusted input, and it may carry customer detail we
        have no reason to retain. The terse reason is enough to triage from.
        """
        fields = ["paystack_message", "fulfillment_state", "updated_at"]
        txn.paystack_message = reason[:500]
        # Reachable from PENDING only; a payment already SUCCESS is not downgraded
        # by a later bad payload.
        if txn.can_transition_to(PaystackTransaction.StatusChoices.FAILED):
            txn.status = PaystackTransaction.StatusChoices.FAILED
            fields.append("status")
        # FAILED + manual_review means "we declined to honour a charge the
        # provider confirmed" — the refund side of the compensation queue.
        txn.fulfillment_state = PaystackTransaction.FulfillmentState.MANUAL_REVIEW
        txn.save(update_fields=fields)

    @staticmethod
    def _record_capture(txn: PaystackTransaction, provider_data: dict, fulfillment_state: str) -> None:
        """Move a validated capture to SUCCESS and record what the provider said."""
        fields = ["paystack_reference", "paystack_transaction_id", "paystack_data", "paystack_message"]

        txn.paystack_reference = txn.reference
        provider_txn_id = provider_data.get("id")
        txn.paystack_transaction_id = provider_txn_id if isinstance(provider_txn_id, int) else None
        txn.paystack_data = payment_audit_payload(provider_data)
        txn.paystack_message = ""

        if txn.can_transition_to(PaystackTransaction.StatusChoices.SUCCESS):
            txn.status = PaystackTransaction.StatusChoices.SUCCESS
            fields.append("status")

        txn.fulfillment_state = fulfillment_state
        fields.append("fulfillment_state")

        fields.append("updated_at")
        txn.save(update_fields=fields)
        logger.info(
            "Payment captured for %s (%s %s) state=%s at %s",
            txn.owner_description,
            txn.amount,
            txn.currency,
            fulfillment_state,
            timezone.now().isoformat(),
        )

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------
    @classmethod
    def _verify_signature(cls, raw_body: bytes, signature: str) -> bool:
        # Fail closed: without a secret, a signature, or a body there is nothing
        # we can trust, so verification must fail rather than pass.
        if not cls.WEBHOOK_SECRET or not signature or not raw_body:
            return False
        computed = hmac.new(
            cls.WEBHOOK_SECRET.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)
