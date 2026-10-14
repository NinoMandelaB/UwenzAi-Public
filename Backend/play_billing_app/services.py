# play_billing_app/services.py
"""
Google Play Billing verification, fulfillment and refund reconciliation.

The rule this module enforces mirrors paystack_app.services: Sparks are
credited on the strength of what the provider's API says — verified
server-side with our own service-account credentials — never on what the
client claims, and a purchase token credits exactly once. Unlike Paystack
there is no pre-recorded purchase intent (the customer pays inside Google's
own sheet), so the provider call *is* the intent: the product id must be one
we explicitly mapped, the purchase must be in PURCHASED state, and the credit
happens through the same ChargeService path the Paystack flow uses.

Idempotency is a database guarantee, not an application one: the
``PlayPurchase.purchase_token_hash`` unique constraint plus the credit inside
one ``transaction.atomic()`` block mean a resubmitted or concurrently
double-submitted token produces one net credit.

Acknowledgment happens *after* the credit commits, deliberately: Google
auto-refunds purchases that are not acknowledged within three days, so
refusing to acknowledge is the correct state for a purchase we could not
turn into Sparks, and a failed acknowledge call is retried on the next
verification attempt rather than blocking the credit.
"""

import json
import logging
from typing import Dict, Any, Optional, Tuple, List

import requests
from django.conf import settings
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone

from capp.classes.billing.balances import (
    InsufficientSparks,
    debit_locked,
    lock_spark_pool,
    read_user_sparks,
)
from capp.models import CustomUser, SparkPackage, SparkTransaction
from .models import PlayPurchase, PlayVoidedPurchase, hash_purchase_token
from .privacy import play_purchase_audit_payload

logger = logging.getLogger(__name__)

PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
PUBLISHER_BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3"

# purchases.products.get "purchaseState" enum.
PURCHASE_STATE_PURCHASED = 0
PURCHASE_STATE_CANCELED = 1
PURCHASE_STATE_PENDING = 2

# Play keeps voided purchases for 30 days, and the voidedpurchases.list
# ``startTime`` parameter "cannot be older than 30 days". A checkpoint older
# than that (a month without refunds, or a paused poller) is clamped to just
# inside the window instead of being sent and rejected on every run. The day
# of margin absorbs clock differences; de-duplication by token makes the
# overlap harmless.
VOIDED_LOOKBACK_MS = 29 * 24 * 60 * 60 * 1000

# Voids whose clawback failed on an earlier run, retried per run at most.
VOIDED_RETRY_BATCH = 200


# Ledger reference for Play purchases. Short, stable, never a raw token.
def play_ledger_reference(purchase_token: str) -> str:
    return f"play_{hash_purchase_token(purchase_token)[:32]}"


class PlayApiError(Exception):
    """The Play Developer API could not be reached or answered unusably."""


class FulfillmentRefused(Exception):
    """A business rule refused this purchase; nothing was credited."""

    def __init__(self, reason: str, status_code: int = 400):
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


class PlayPublisherClient:
    """
    Minimal requests wrapper over the Google Play Developer API.

    Authenticated with the Google Cloud service account (already a backend
    dependency via google-auth) against the androidpublisher REST endpoints.
    Kept deliberately small and behind a class so tests substitute the whole
    client instead of patching HTTP.
    """

    def __init__(self, credentials_info: dict, package_name: str, timeout: int = 20):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession

        if not isinstance(credentials_info, dict) or "client_email" not in credentials_info:
            raise ValueError("invalid service account JSON")
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=[PUBLISHER_SCOPE],
        )
        self._session = AuthorizedSession(credentials)
        self.package_name = package_name
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> "PlayPublisherClient":
        raw = getattr(settings, "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "") or ""
        if not raw.strip():
            raise PlayApiError("play_billing_not_configured")
        try:
            credentials_info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlayApiError("play_billing_not_configured") from exc
        return cls(credentials_info, settings.GOOGLE_PLAY_PACKAGE_NAME)

    # ------------------------------------------------------------------
    # purchases.products
    # ------------------------------------------------------------------
    def get_purchase(self, product_id: str, purchase_token: str) -> Optional[dict]:
        """
        Return the authoritative purchase resource, or None if it does not exist.

        A missing purchase (bad token, or a token from another application) is
        a normal refusal, not an outage; every other HTTP failure raises so
        the caller can distinguish "Play says no" from "Play unreachable".
        """
        url = (
            f"{PUBLISHER_BASE}/applications/{self.package_name}"
            f"/purchases/products/{requests.utils.quote(product_id, safe='')}"
            f"/tokens/{requests.utils.quote(purchase_token, safe='')}"
        )
        try:
            response = self._session.get(url, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise PlayApiError("play_api_unavailable") from exc

        if response.status_code == 404:
            return None
        if not response.ok:
            logger.warning(
                "Play purchases.products.get answered HTTP %s for product=%s",
                response.status_code,
                product_id,
            )
            raise PlayApiError("play_api_unavailable")
        try:
            return response.json()
        except ValueError as exc:
            raise PlayApiError("play_api_unavailable") from exc

    def acknowledge(self, product_id: str, purchase_token: str) -> bool:
        """
        Acknowledge a purchase server-side (DGAPI 2.0 has no client acknowledge).

        Best-effort by design: the credit is already committed, and Google's
        own already-acknowledged errors are a success. Failures return False
        and are retried on the next verification of the same token.
        """
        url = (
            f"{PUBLISHER_BASE}/applications/{self.package_name}"
            f"/purchases/products/{requests.utils.quote(product_id, safe='')}"
            f"/tokens/{requests.utils.quote(purchase_token, safe='')}:acknowledge"
        )
        try:
            response = self._session.post(url, json={}, timeout=self.timeout)
        except requests.exceptions.RequestException:
            logger.exception("Play acknowledge request failed for product=%s", product_id)
            return False

        if response.ok:
            return True
        # 400 with "already acknowledged" (or the equivalent reason in the
        # body) means the goal state is already true.
        if response.status_code == 400 and "already acknowledged" in (response.text or "").lower():
            return True
        logger.warning("Play acknowledge answered HTTP %s for product=%s", response.status_code, product_id)
        return False

    # ------------------------------------------------------------------
    # purchases.voidedpurchases
    # ------------------------------------------------------------------
    def list_voided_purchases(
        self,
        start_time_millis: Optional[int] = None,
        page_token: Optional[str] = None,
        max_results: int = 200,
    ) -> Tuple[List[dict], Optional[str]]:
        """
        Return one page of voided purchases and the next page token, if any.

        ``start_time_millis`` is exclusive of the previous checkpoint so a run
        never re-reads the window it already processed; the caller still
        de-duplicates by token, so an inclusive boundary would be harmless.
        """
        params = {"maxResults": max_results}
        if start_time_millis is not None:
            params["startTime"] = int(start_time_millis)
        if page_token:
            params["token"] = page_token

        url = f"{PUBLISHER_BASE}/applications/{self.package_name}/purchases/voidedpurchases"
        try:
            response = self._session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise PlayApiError("play_api_unavailable") from exc

        if not response.ok:
            logger.warning("Play voidedpurchases.list answered HTTP %s", response.status_code)
            raise PlayApiError("play_api_unavailable")

        try:
            body = response.json()
        except ValueError as exc:
            raise PlayApiError("play_api_unavailable") from exc

        entries = body.get("voidedPurchases") or []
        next_token = ((body.get("tokenPagination") or {}).get("nextPageToken")) or None
        return entries, next_token


class PlayBillingService:
    """Verification, idempotent crediting, acknowledgment and reversals."""

    # ------------------------------------------------------------------
    # Configuration / catalog
    # ------------------------------------------------------------------
    @staticmethod
    def is_configured() -> bool:
        return bool((getattr(settings, "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "") or "").strip())

    @staticmethod
    def resolve_product_slug(product_id: str) -> Optional[str]:
        """Map a Play product id to our Spark package slug, or None."""
        product_map = getattr(settings, "GOOGLE_PLAY_PRODUCT_MAP", {}) or {}
        return product_map.get(product_id)

    @staticmethod
    def product_id_for_slug(slug: str) -> Optional[str]:
        """Reverse mapping, used to annotate the public package catalog."""
        product_map = getattr(settings, "GOOGLE_PLAY_PRODUCT_MAP", {}) or {}
        for product_id, package_slug in product_map.items():
            if package_slug == slug:
                return product_id
        return None

    # ------------------------------------------------------------------
    # Verification + fulfillment (client-facing path)
    # ------------------------------------------------------------------
    @classmethod
    def verify_and_fulfill(
        cls,
        user: CustomUser,
        product_id: str,
        purchase_token: str,
        order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Verify a purchase against the Play Developer API and credit it once.

        Returns a dict with ``status`` (success/fail), ``data.result`` where
        relevant (credited / already_credited / pending / cancelled), and the
        post-credit balance. Never raises for business outcomes.
        """
        try:
            if not cls.is_configured():
                logger.error("Play verify refused: service account is not configured")
                return {"status": "fail", "data": "play_billing_not_configured"}

            # The product must be one we offer through Play. A token for an
            # unmapped product id is treated as a generic refusal.
            package_slug = cls.resolve_product_slug(product_id)
            if package_slug is None:
                logger.warning("Play verify refused: unmapped product_id=%s", product_id)
                return {"status": "fail", "data": "product_not_offered"}

            try:
                client = PlayPublisherClient.from_settings()
            except (PlayApiError, ValueError):
                logger.exception("Play verify refused: unusable service account configuration")
                return {"status": "fail", "data": "play_billing_not_configured"}

            try:
                purchase = client.get_purchase(product_id, purchase_token)
            except PlayApiError:
                raise
            except Exception as exc:
                # An unexpected client failure is a transient "ask Google
                # again" — never a refusal the client might treat as final.
                raise PlayApiError("play_api_unavailable") from exc
            if purchase is None:
                return {"status": "fail", "data": "purchase_not_found"}

            # The provider payload never selects the package: the product id
            # did, and the slug came from our own configuration.
            purchase_state = purchase.get("purchaseState")
            if purchase_state == PURCHASE_STATE_PENDING:
                return {"status": "success", "data": {"result": "pending", "sparks_granted": 0}}
            if purchase_state == PURCHASE_STATE_CANCELED:
                return {"status": "success", "data": {"result": "cancelled", "sparks_granted": 0}}
            if purchase_state != PURCHASE_STATE_PURCHASED:
                logger.error(
                    "Play verify refused: unknown purchaseState=%s for product=%s",
                    purchase_state,
                    product_id,
                )
                return {"status": "fail", "data": "purchase_state_unrecognized"}

            token_hash = hash_purchase_token(purchase_token)
            existing = PlayPurchase.objects.filter(purchase_token_hash=token_hash).first()

            if existing is not None:
                return cls._handle_existing(user, client, existing, product_id)

            package = SparkPackage.objects.filter(slug=package_slug, is_active=True).first()
            if package is None:
                # Google captured the money but we cannot deliver value. Record
                # the purchase for the compensation queue and deliberately do
                # NOT acknowledge: Play auto-refunds after three days, which is
                # the correct outcome for a purchase we could not fulfill.
                cls._record_unfulfillable(
                    token_hash=token_hash,
                    purchase_token=purchase_token,
                    product_id=product_id,
                    package_slug=package_slug,
                    user=user,
                    order_id=order_id,
                    purchase=purchase,
                )
                logger.error(
                    "PLAY BILLING: purchase verified but package unavailable (product=%s, %s) — held for review",
                    product_id,
                    f"user:{user.pk}",
                )
                return {"status": "fail", "data": "spark_package_unavailable"}

            outcome = cls._credit_new_purchase(
                user=user,
                client=client,
                token_hash=token_hash,
                purchase_token=purchase_token,
                product_id=product_id,
                package_slug=package_slug,
                order_id=order_id,
                purchase=purchase,
            )
            return outcome

        except PlayApiError:
            logger.exception("Play Developer API failure during verification")
            return {"status": "fail", "data": "play_api_unavailable"}
        except Exception:
            logger.exception("Unexpected error verifying a Play purchase")
            return {"status": "fail", "data": "server_error"}

    # ------------------------------------------------------------------
    # Idempotent credit
    # ------------------------------------------------------------------
    @classmethod
    def _credit_new_purchase(
        cls,
        user,
        client,
        token_hash,
        purchase_token,
        product_id,
        package_slug,
        order_id,
        purchase,
    ) -> Dict[str, Any]:
        from capp.classes.billing.charge_service import ChargeService

        ledger_reference = play_ledger_reference(purchase_token)

        try:
            with db_transaction.atomic():
                row = None
                try:
                    row = PlayPurchase.objects.create(
                        purchase_token_hash=token_hash,
                        purchase_token=purchase_token,
                        product_id=product_id,
                        package_slug=package_slug,
                        order_id=order_id,
                        user=user,
                        status=PlayPurchase.Status.CREDITED,
                        play_data=play_purchase_audit_payload(purchase),
                    )
                except IntegrityError:
                    # A concurrent verification of the same token won the
                    # unique race. Its credit is the only one; fall through
                    # to report already_credited once this empty
                    # transaction has been released.
                    pass

                if row is not None:
                    result = ChargeService.process_successful_payment(
                        user=user,
                        package_slug=package_slug,
                        package_type="spark",
                        paystack_reference=ledger_reference,
                    )
                    if result.get("status") != "success":
                        # Unwind the row with the credit: nothing was
                        # delivered, the purchase stays verifiable, and the
                        # client may retry.
                        raise FulfillmentRefused(str(result.get("data", "fulfillment_failed")))

                    row.sparks_granted = int(result.get("sparks_granted", 0) or 0)
                    row.save(update_fields=["sparks_granted", "updated_at"])
        except FulfillmentRefused as exc:
            logger.error("Play fulfillment refused after verification: %s", exc)
            return {"status": "fail", "data": str(exc)}

        if row is None:
            return cls._already_credited_response(user, client, product_id, token_hash)

        # Credit committed. Acknowledgment is best-effort and outside the
        # database transaction: a failure is retried on the next verify of
        # the same token rather than holding locks on an HTTP call.
        acknowledged = cls._acknowledge(client, row, product_id)

        return {
            "status": "success",
            "data": {
                "result": "credited",
                "sparks_granted": row.sparks_granted,
                "acknowledged": acknowledged,
                "spark_pool_balance": read_user_sparks(user),
            },
        }

    @classmethod
    def _handle_existing(cls, user: CustomUser, client, row: PlayPurchase, product_id: str) -> Dict[str, Any]:
        """
        A token we have already seen. Only the recorded owner may learn that.

        Any other account submitting this token gets the same generic refusal
        as a token that never existed, so probing other users' tokens reveals
        nothing.
        """
        if row.user_id != user.pk:
            logger.warning(
                "SECURITY: Play purchase token submitted by a non-owner (purchase owner=%s)",
                row.owner_description,
            )
            return {"status": "fail", "data": "purchase_not_found"}

        if row.status == PlayPurchase.Status.REVERSED:
            return {"status": "fail", "data": "purchase_reversed"}

        if row.status == PlayPurchase.Status.MANUAL_REVIEW:
            # The package may have been reactivated since; retry the grant.
            return cls._retry_manual_review(user, client, row)

        return cls._already_credited_response(user, client, product_id, row.purchase_token_hash)

    @classmethod
    def _already_credited_response(cls, user: CustomUser, client, product_id: str, token_hash: str) -> Dict[str, Any]:
        row = PlayPurchase.objects.filter(purchase_token_hash=token_hash).first()
        if row is None or row.user_id != user.pk:
            # The row vanished mid-flight (erasure race) — refuse generically.
            return {"status": "fail", "data": "purchase_not_found"}
        if row.status == PlayPurchase.Status.REVERSED:
            return {"status": "fail", "data": "purchase_reversed"}
        if row.status == PlayPurchase.Status.MANUAL_REVIEW:
            return cls._retry_manual_review(user, client, row)

        acknowledged = row.acknowledged
        if not acknowledged:
            acknowledged = cls._acknowledge(client, row, product_id)

        return {
            "status": "success",
            "data": {
                "result": "already_credited",
                "sparks_granted": row.sparks_granted,
                "acknowledged": acknowledged,
                "spark_pool_balance": read_user_sparks(user),
            },
        }

    @classmethod
    def _retry_manual_review(cls, user: CustomUser, client, row: PlayPurchase) -> Dict[str, Any]:
        """
        Re-attempt fulfillment of a held purchase whose package is active again.

        The status flip is guarded inside the row lock, so two retries racing
        each other produce one credit.
        """
        from capp.classes.billing.charge_service import ChargeService

        package = SparkPackage.objects.filter(slug=row.package_slug, is_active=True).first()
        if package is None:
            return {"status": "fail", "data": "spark_package_unavailable"}

        try:
            with db_transaction.atomic():
                locked = PlayPurchase.objects.select_for_update().get(pk=row.pk)
                if locked.status != PlayPurchase.Status.MANUAL_REVIEW:
                    # Another retry won the race; report its outcome.
                    if locked.status == PlayPurchase.Status.REVERSED:
                        return {"status": "fail", "data": "purchase_reversed"}
                    return cls._already_credited_from_row(user, client, locked)

                result = ChargeService.process_successful_payment(
                    user=user,
                    package_slug=locked.package_slug,
                    package_type="spark",
                    paystack_reference=play_ledger_reference(locked.purchase_token),
                )
                if result.get("status") != "success":
                    return {"status": "fail", "data": str(result.get("data", "fulfillment_failed"))}

                locked.status = PlayPurchase.Status.CREDITED
                locked.sparks_granted = int(result.get("sparks_granted", 0) or 0)
                locked.save(update_fields=["status", "sparks_granted", "updated_at"])
        except Exception:
            logger.exception("Unexpected error retrying a held Play purchase")
            return {"status": "fail", "data": "server_error"}

        acknowledged = cls._acknowledge(client, locked, locked.product_id)
        return {
            "status": "success",
            "data": {
                "result": "credited",
                "sparks_granted": locked.sparks_granted,
                "acknowledged": acknowledged,
                "spark_pool_balance": read_user_sparks(user),
            },
        }

    @classmethod
    def _already_credited_from_row(cls, user, client, row: PlayPurchase) -> Dict[str, Any]:
        acknowledged = row.acknowledged
        if not acknowledged:
            acknowledged = cls._acknowledge(client, row, row.product_id)
        return {
            "status": "success",
            "data": {
                "result": "already_credited",
                "sparks_granted": row.sparks_granted,
                "acknowledged": acknowledged,
                "spark_pool_balance": read_user_sparks(user),
            },
        }

    @staticmethod
    def _acknowledge(client, row: PlayPurchase, product_id: str) -> bool:
        if row.acknowledged:
            return True
        try:
            acknowledged = client.acknowledge(product_id, row.purchase_token)
        except Exception:
            # Post-commit best-effort: the credit stands, the retry happens on
            # the next verification of the same token.
            logger.exception("Play acknowledge raised for product=%s", product_id)
            return False
        if acknowledged:
            PlayPurchase.objects.filter(pk=row.pk, acknowledged=False).update(
                acknowledged=True,
                acknowledged_at=timezone.now(),
                updated_at=timezone.now(),
            )
        return acknowledged

    @staticmethod
    def _record_unfulfillable(
        token_hash,
        purchase_token,
        product_id,
        package_slug,
        user,
        order_id,
        purchase,
    ) -> PlayPurchase:
        """
        Record a verified purchase we could not fulfill, without crediting.

        ``get_or_create`` (not ``create``) because two concurrent verifications
        of an unfulfillable purchase must not lose the evidence in an
        IntegrityError. An existing CREDITED row is never touched.
        """
        try:
            with db_transaction.atomic():
                row, _created = PlayPurchase.objects.get_or_create(
                    purchase_token_hash=token_hash,
                    defaults={
                        "purchase_token": purchase_token,
                        "product_id": product_id,
                        "package_slug": package_slug,
                        "order_id": order_id,
                        "user": user,
                        "status": PlayPurchase.Status.MANUAL_REVIEW,
                        "play_data": play_purchase_audit_payload(purchase),
                    },
                )
            return row
        except IntegrityError:
            return PlayPurchase.objects.get(purchase_token_hash=token_hash)

    # ------------------------------------------------------------------
    # Refund reconciliation (scheduled path)
    # ------------------------------------------------------------------
    @classmethod
    def reconcile_voided_purchases(cls, client: Optional[PlayPublisherClient] = None) -> Dict[str, Any]:
        """
        Poll the Play voided-purchases API and reverse credited Sparks once.

        Each run first retries voids recorded earlier whose clawback failed
        (``processed=False``), from our own records. It then resumes the Play
        listing after the newest voided timestamp already recorded (clamped to
        Play's 30-day window), de-duplicates tokens through the unique
        ``PlayVoidedPurchase`` row, and flips each credited purchase to
        REVERSED exactly once under a row lock. A Play API failure is reported,
        not raised: the next run resumes from the same checkpoint.
        """
        if not cls.is_configured():
            return {"status": "fail", "data": "play_billing_not_configured"}
        if client is None:
            try:
                client = PlayPublisherClient.from_settings()
            except (PlayApiError, ValueError):
                return {"status": "fail", "data": "play_billing_not_configured"}

        reversed_count = cls._retry_unprocessed_voids()

        checkpoint = (
            PlayVoidedPurchase.objects.order_by("-voided_time_millis")
            .values_list("voided_time_millis", flat=True)
            .first()
        )
        start_time = cls._voided_start_time(checkpoint)

        seen_count = 0
        page_token = None
        pages = 0
        try:
            while True:
                entries, page_token = client.list_voided_purchases(start_time_millis=start_time, page_token=page_token)
                for entry in entries:
                    if cls._process_voided(entry):
                        reversed_count += 1
                    seen_count += 1
                pages += 1
                if not page_token or pages > 50:
                    # The page cap is a belt-and-braces guard against a broken
                    # pagination token looping forever; 50 pages is 10k entries.
                    break
        except PlayApiError as exc:
            # Entries handled before the failure are recorded; the next run
            # picks up from the checkpoint they produced.
            logger.warning(
                "Play voided-purchase listing failed after %d page(s), %d entries: %s",
                pages,
                seen_count,
                exc,
            )
            return {"status": "fail", "data": "play_api_unavailable"}

        return {
            "status": "success",
            "data": {"voided_seen": seen_count, "reversed": reversed_count},
        }

    @staticmethod
    def _voided_start_time(checkpoint: Optional[int]) -> Optional[int]:
        """The listing's ``startTime``: the checkpoint, kept inside Play's window."""
        if checkpoint is None:
            # First run: Play's default is the full 30 days.
            return None
        floor = int(timezone.now().timestamp() * 1000) - VOIDED_LOOKBACK_MS
        return max(int(checkpoint), floor)

    @classmethod
    def _retry_unprocessed_voids(cls) -> int:
        """
        Retry clawbacks that failed on an earlier run, from our own records.

        Such a void is recorded with ``processed=False``. Play does not list
        it again: newer voids moved the checkpoint past it, and Play forgets
        voids after 30 days anyway. The stored token hash finds the purchase,
        and the status flip inside ``_reverse_purchase`` keeps the reversal
        once-only. Returns the number of purchases reversed now.
        """
        reversed_count = 0
        pending = list(
            PlayVoidedPurchase.objects.filter(processed=False).order_by("voided_time_millis")[:VOIDED_RETRY_BATCH]
        )
        for voided in pending:
            purchase = PlayPurchase.objects.filter(purchase_token_hash=voided.purchase_token_hash).first()
            if purchase is not None:
                try:
                    if cls._reverse_purchase(purchase, {"voidedReason": voided.voided_reason}):
                        reversed_count += 1
                except Exception:
                    logger.exception(
                        "Retrying the clawback for voided Play purchase %s failed; will retry",
                        voided.purchase_token_hash[:12],
                    )
                    continue
            PlayVoidedPurchase.objects.filter(pk=voided.pk).update(processed=True, updated_at=timezone.now())

        still_pending = PlayVoidedPurchase.objects.filter(processed=False).count()
        if still_pending:
            logger.warning("%d voided Play purchase(s) still await their clawback", still_pending)
        return reversed_count

    @classmethod
    def _process_voided(cls, entry: dict) -> bool:
        """
        Reverse one voided purchase and then record that it was handled.

        Ordering matters: the reversal runs *before* the ``PlayVoidedPurchase``
        checkpoint row is created. If the run dies in between, the next run
        re-fetches the entry, finds the purchase already REVERSED, and
        completes the record — no reversal is ever lost to a crash. The status
        flip inside ``_reverse_purchase`` is what makes the reversal itself
        once-only, so re-deliveries are harmless.

        If the reversal fails, the row is still written, with
        ``processed=False``. Newer voids move the checkpoint past this one, so
        the retry comes from our own records (``_retry_unprocessed_voids``),
        not from Play.
        """
        purchase_token = entry.get("purchaseToken")
        if not isinstance(purchase_token, str) or not purchase_token:
            return False
        token_hash = hash_purchase_token(purchase_token)

        purchase = PlayPurchase.objects.filter(purchase_token_hash=token_hash).first()
        reversed_now = False
        processed = True
        if purchase is None:
            # A purchase we never credited (e.g. from before this integration
            # existed, or an unfulfillable one Google refunded as intended).
            logger.info("Voided Play purchase with no local record: order=%s", entry.get("orderId"))
        else:
            try:
                reversed_now = cls._reverse_purchase(purchase, entry)
            except Exception:
                logger.exception("Error reversing voided Play purchase %s; will retry", token_hash[:12])
                processed = False

        try:
            with db_transaction.atomic():
                record, created = PlayVoidedPurchase.objects.get_or_create(
                    purchase_token_hash=token_hash,
                    defaults={
                        "order_id": entry.get("orderId"),
                        "voided_time_millis": int(entry.get("voidedTimeMillis") or 0),
                        "voided_reason": (
                            str(entry.get("voidedReason")) if entry.get("voidedReason") is not None else None
                        ),
                        "voided_source": (
                            str(entry.get("voidedSource")) if entry.get("voidedSource") is not None else None
                        ),
                        "processed": processed,
                    },
                )
                if not created and record.processed != processed:
                    # The same void listed again: its latest outcome counts.
                    record.processed = processed
                    record.save(update_fields=["processed", "updated_at"])
        except IntegrityError:
            # A concurrent run is recording the same void event; its result
            # stands and the reversal guard makes ours a no-op anyway.
            pass

        return reversed_now

    @classmethod
    def _reverse_purchase(cls, purchase: PlayPurchase, entry: dict) -> bool:
        """
        Claw back granted Sparks for a refunded purchase, exactly once.

        The status flip inside the row lock is the once-only guarantee. If the
        balance was already spent, the clawback is partial and the shortfall
        is recorded on the row for the manual compensation queue — a refund
        must never silently mint Sparks, and it also must not fail loudly
        over a normal, spent balance.
        """
        voided_reason = str(entry.get("voidedReason")) if entry.get("voidedReason") is not None else None
        with db_transaction.atomic():
            row = PlayPurchase.objects.select_for_update().get(pk=purchase.pk)

            if row.status == PlayPurchase.Status.REVERSED:
                return False

            if row.status == PlayPurchase.Status.MANUAL_REVIEW:
                # Google refunded a purchase we never credited. Nothing to
                # claw back; record the closure and move on.
                row.status = PlayPurchase.Status.REVERSED
                row.reversed_at = timezone.now()
                row.metadata = {
                    **(row.metadata or {}),
                    "voided_reason": voided_reason,
                    "never_credited": True,
                }
                row.save(update_fields=["status", "reversed_at", "metadata", "updated_at"])
                return True

            # row.status == CREDITED
            if row.user_id is None or row.owner_deleted_at:
                # The account was erased after the purchase. Sparks cannot
                # be clawed back from a deleted pool; Google has already
                # refunded the money, and the evidence stays for accounting.
                row.status = PlayPurchase.Status.REVERSED
                row.reversed_at = timezone.now()
                row.metadata = {
                    **(row.metadata or {}),
                    "voided_reason": voided_reason,
                    "owner_erased": True,
                    "sparks_reversed": 0,
                }
                row.save(update_fields=["status", "reversed_at", "metadata", "updated_at"])
                logger.info(
                    "Voided Play purchase %s belonged to an erased account; no clawback possible",
                    row.purchase_token_hash[:12],
                )
                return True

            pool = lock_spark_pool(row.user)
            granted = int(row.sparks_granted)
            balance_before = int(pool.sparks)
            shortfall = 0

            try:
                debit_locked(pool, granted)
                debited = granted
            except InsufficientSparks as exc:
                debited = exc.balance
                shortfall = granted - debited
                if debited > 0:
                    debit_locked(pool, debited)

            SparkTransaction.objects.create(
                spark_pool=pool,
                user=row.user,
                entry_type=SparkTransaction.EntryType.DEBIT,
                source=SparkTransaction.SourceChoices.REFUND,
                spark_amount=-debited,
                sparks_before=balance_before,
                sparks_after=pool.sparks,
                description=f"Google Play refund – {row.package_slug}",
                reference=play_ledger_reference(row.purchase_token),
                metadata={
                    "play_refund": True,
                    "product_id": row.product_id,
                    "voided_reason": voided_reason,
                    "sparks_reversed": debited,
                    **({"shortfall": shortfall} if shortfall else {}),
                },
            )

            row.status = PlayPurchase.Status.REVERSED
            row.reversed_at = timezone.now()
            row.metadata = {
                **(row.metadata or {}),
                "voided_reason": voided_reason,
                "sparks_reversed": debited,
                **({"shortfall": shortfall} if shortfall else {}),
            }
            row.save(update_fields=["status", "reversed_at", "metadata", "updated_at"])
            logger.info(
                "Reversed %d sparks for refunded Play purchase %s (shortfall=%d)",
                debited,
                row.purchase_token_hash[:12],
                shortfall,
            )
            return True
