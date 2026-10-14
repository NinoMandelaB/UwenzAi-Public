# play_billing_app/tests.py
"""
Server-side verification, idempotent crediting and refund reconciliation for
Google Play purchases of Sparks.

The property under test throughout: a purchase token verified against the
Play Developer API credits exactly once, through the same ChargeService path
the Paystack flow uses, and a refunded purchase reverses its credit exactly
once. The Play API client is always a mock — these tests assert our ledger
behaviour, not Google's.
"""

import json
from decimal import Decimal
from unittest import mock

from django.db import OperationalError
from django.test import TestCase, override_settings
from django.utils import timezone

from capp.classes.billing.balances import lock_spark_pool
from capp.models import CustomUser, SparkPackage, SparkPool, SparkTransaction
from capp.utils.encryption import EncryptionService
from play_billing_app.models import PlayPurchase, PlayVoidedPurchase, hash_purchase_token
from play_billing_app.services import (
    VOIDED_LOOKBACK_MS,
    PlayApiError,
    PlayBillingService,
    play_ledger_reference,
)

VERIFY_URL = "/api/v1/billing/play/verify/"

SERVICE_ACCOUNT_JSON = json.dumps({"client_email": "play@test-project.iam.gserviceaccount.com"})

PLAY_SETTINGS = {
    "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON": SERVICE_ACCOUNT_JSON,
    "GOOGLE_PLAY_PACKAGE_NAME": "com.uwenzai.android",
    "GOOGLE_PLAY_PRODUCT_MAP": {"sparks_small": "starter", "sparks_medium": "medium"},
}


def make_user(username="buyer", email="buyer@example.com", password="pw123456"):
    email_fields = EncryptionService.prepare_email_fields(email)
    return CustomUser.objects.create_user(
        username=username,
        password=password,
        email_encrypted=email_fields["email_encrypted"],
        email_hash=email_fields["email_hash"],
        is_verified=True,
        account_status=CustomUser.AccountStatus.ACTIVE,
    )


def make_package(slug="starter", sparks=1000, price=Decimal("100.00"), active=True):
    return SparkPackage.objects.create(
        slug=slug,
        name=f"{slug.title()} Pack",
        base_spark_amount=sparks,
        base_price_kes=price,
        discount_percent=Decimal("0.00"),
        vat_percent=Decimal("0.00"),
        final_price_kes=price,
        is_active=active,
    )


class FakePlayClient:
    """Deterministic stand-in for PlayPublisherClient (no network)."""

    def __init__(self, purchase=None, acknowledge_result=True, voided_pages=None):
        self.purchase = purchase
        self.acknowledge_result = acknowledge_result
        self.voided_pages = voided_pages or []
        self.acknowledge_calls = []
        self.list_calls = 0

    def get_purchase(self, product_id, purchase_token):
        if self.purchase is None:
            return None
        return {**self.purchase, "productId": product_id}

    def acknowledge(self, product_id, purchase_token):
        self.acknowledge_calls.append((product_id, purchase_token))
        return self.acknowledge_result

    def list_voided_purchases(self, start_time_millis=None, page_token=None, max_results=200):
        self.list_calls += 1
        index = 0 if page_token is None else int(page_token)
        if index >= len(self.voided_pages):
            return [], None
        entries, _ = self.voided_pages[index]
        return entries, (str(index + 1) if index + 1 < len(self.voided_pages) else None)


def purchased_payload(purchase_state=0, order_id="GPA.3304-1234-5678-12345"):
    return {
        "kind": "androidpublisher#productPurchase",
        "purchaseState": purchase_state,
        "consumptionState": 0,
        "acknowledged": False,
        "orderId": order_id,
        "purchaseTimeMillis": "1760000000000",
        "obfuscatedExternalAccountId": "should-not-be-retained",
    }


def bearer_token(client, username, password="pw123456"):
    from django.core.cache import cache

    # Throttles use the shared process cache; reset so tests are independent.
    cache.clear()
    with mock.patch(
        "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
        return_value={"success": True},
    ):
        resp = client.post(
            "/api/v1/auth/signin/",
            data={"username": username, "password": password},
            content_type="application/json",
        )
    body = resp.json()
    assert resp.status_code == 200, f"sign-in failed: {body}"
    return body["data"]["access_token"]


def _auth(client, username, password="pw123456"):
    token = bearer_token(client, username, password)
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _patch_client(fake):
    return mock.patch("play_billing_app.services.PlayPublisherClient.from_settings", return_value=fake)


class PlayVerifyEndpointTests(TestCase):
    """The client-facing verify endpoint: auth, validation, and outcomes."""

    @override_settings(**PLAY_SETTINGS)
    def setUp(self):
        self.user = make_user()
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)
        self.package = make_package()

    def _post(self, body, auth=True, username="buyer"):
        headers = _auth(self.client, username) if auth else {}
        return self.client.post(
            VERIFY_URL,
            data=body,
            content_type="application/json",
            **headers,
        )

    # ------------------------------------------------------------------
    # Access control and input validation
    # ------------------------------------------------------------------
    def test_requires_authentication(self):
        resp = self.client.post(VERIFY_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    @override_settings(**PLAY_SETTINGS)
    def test_missing_fields_rejected(self):
        resp = self._post({"product_id": "sparks_small"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "product_id_and_purchase_token_required")

    @override_settings(**PLAY_SETTINGS)
    def test_invalid_product_id_rejected(self):
        resp = self._post({"product_id": "../etc/passwd", "purchase_token": "tok"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_product_id")

    @override_settings(**PLAY_SETTINGS)
    def test_invalid_purchase_token_rejected(self):
        resp = self._post({"product_id": "sparks_small", "purchase_token": "bad token!"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_purchase_token")

    @override_settings(**PLAY_SETTINGS)
    def test_invalid_order_id_rejected(self):
        resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123", "order_id": "bad order id"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_order_id")

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @override_settings(GOOGLE_PLAY_SERVICE_ACCOUNT_JSON="", GOOGLE_PLAY_PRODUCT_MAP={"sparks_small": "starter"})
    def test_unconfigured_returns_503(self):
        resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["data"], "play_billing_not_configured")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)

    @override_settings(GOOGLE_PLAY_SERVICE_ACCOUNT_JSON=SERVICE_ACCOUNT_JSON, GOOGLE_PLAY_PRODUCT_MAP={})
    def test_unmapped_product_refused(self):
        resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "product_not_offered")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)

    # ------------------------------------------------------------------
    # Provider outcomes
    # ------------------------------------------------------------------
    @override_settings(**PLAY_SETTINGS)
    def test_purchase_not_found(self):
        with _patch_client(FakePlayClient(purchase=None)):
            resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "purchase_not_found")

    @override_settings(**PLAY_SETTINGS)
    def test_pending_purchase_credits_nothing(self):
        with _patch_client(FakePlayClient(purchase=purchased_payload(purchase_state=2))):
            resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["result"], "pending")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        self.assertFalse(PlayPurchase.objects.exists())

    @override_settings(**PLAY_SETTINGS)
    def test_cancelled_purchase_credits_nothing(self):
        with _patch_client(FakePlayClient(purchase=purchased_payload(purchase_state=1))):
            resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["result"], "cancelled")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        self.assertFalse(PlayPurchase.objects.exists())

    @override_settings(**PLAY_SETTINGS)
    def test_unrecognized_purchase_state_refused(self):
        with _patch_client(FakePlayClient(purchase=purchased_payload(purchase_state=9))):
            resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "purchase_state_unrecognized")

    @override_settings(**PLAY_SETTINGS)
    def test_play_api_error_maps_to_502(self):
        fake = FakePlayClient(purchase=purchased_payload())
        fake.get_purchase = mock.Mock(side_effect=Exception("network down"))
        with _patch_client(fake):
            resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["data"], "play_api_unavailable")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        self.assertFalse(PlayPurchase.objects.exists())

    # ------------------------------------------------------------------
    # The credit itself
    # ------------------------------------------------------------------
    @override_settings(**PLAY_SETTINGS)
    def test_verified_purchase_credits_once_and_acknowledges(self):
        fake = FakePlayClient(purchase=purchased_payload())
        with _patch_client(fake):
            resp = self._post(
                {
                    "product_id": "sparks_small",
                    "purchase_token": "tok123",
                    "order_id": "GPA.3304-1234-5678-12345",
                }
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["data"]["result"], "credited")
        self.assertEqual(body["data"]["sparks_granted"], 1000)
        self.assertEqual(body["data"]["spark_pool_balance"], 1000)
        self.assertTrue(body["data"]["acknowledged"])
        self.assertEqual(fake.acknowledge_calls, [("sparks_small", "tok123")])

        # Ledger: one credit row, keyed on the hashed token, never the raw one.
        ledger_reference = play_ledger_reference("tok123")
        tx = SparkTransaction.objects.get(reference=ledger_reference)
        self.assertEqual(tx.entry_type, SparkTransaction.EntryType.CREDIT)
        self.assertEqual(tx.spark_amount, 1000)
        self.assertNotIn("tok123", tx.reference)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 1000)

        purchase = PlayPurchase.objects.get()
        self.assertEqual(purchase.status, PlayPurchase.Status.CREDITED)
        self.assertEqual(purchase.sparks_granted, 1000)
        self.assertTrue(purchase.acknowledged)
        self.assertEqual(purchase.user, self.user)
        self.assertEqual(purchase.package_slug, "starter")
        # Provider payload is minimized: reconciliation facts only.
        self.assertNotIn("obfuscatedExternalAccountId", purchase.play_data)
        self.assertEqual(purchase.play_data["purchaseState"], 0)
        # The raw token never reaches the ledger rows.
        self.assertNotIn("tok123", json.dumps(purchase.play_data))

    @override_settings(**PLAY_SETTINGS)
    def test_resubmitted_token_is_idempotent(self):
        fake = FakePlayClient(purchase=purchased_payload())
        with _patch_client(fake):
            first = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})
            second = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})

        self.assertEqual(first.json()["data"]["result"], "credited")
        self.assertEqual(second.json()["data"]["result"], "already_credited")
        # Exactly one credit, one row, and acknowledge ran exactly once.
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 1000)
        self.assertEqual(PlayPurchase.objects.count(), 1)
        self.assertEqual(
            SparkTransaction.objects.filter(
                entry_type=SparkTransaction.EntryType.CREDIT, reference=play_ledger_reference("tok123")
            ).count(),
            1,
        )
        self.assertEqual(len(fake.acknowledge_calls), 1)

    @override_settings(**PLAY_SETTINGS)
    def test_acknowledge_failure_still_credits_and_retries_later(self):
        fake = FakePlayClient(purchase=purchased_payload(), acknowledge_result=False)
        with _patch_client(fake):
            first = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})

            # The credit is committed regardless; the client sees acknowledged=false.
            self.assertEqual(first.json()["data"]["result"], "credited")
            self.assertFalse(first.json()["data"]["acknowledged"])
            self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 1000)
            purchase = PlayPurchase.objects.get()
            self.assertFalse(purchase.acknowledged)

            # A later resubmit retries the acknowledge only.
            fake.acknowledge_result = True
            second = self._post({"product_id": "sparks_small", "purchase_token": "tok123"})

        self.assertEqual(second.json()["data"]["result"], "already_credited")
        self.assertTrue(second.json()["data"]["acknowledged"])
        self.assertEqual(len(fake.acknowledge_calls), 2)
        purchase.refresh_from_db()
        self.assertTrue(purchase.acknowledged)
        # Still exactly one credit.
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 1000)

    @override_settings(**PLAY_SETTINGS)
    def test_other_users_token_refused_generically(self):
        other = make_user(username="other", email="other@example.com")
        SparkPool.objects.create(user=other, sparks=0, token_balance=0, is_active=True)
        with _patch_client(FakePlayClient(purchase=purchased_payload())):
            self._post({"product_id": "sparks_small", "purchase_token": "tok123"})

            # The non-owner must get the same answer as a token that never existed.
            resp = self._post({"product_id": "sparks_small", "purchase_token": "tok123"}, username="other")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "purchase_not_found")
        self.assertEqual(SparkPool.objects.get(user=other).sparks, 0)

    # ------------------------------------------------------------------
    # Unfulfillable purchases
    # ------------------------------------------------------------------
    @override_settings(**PLAY_SETTINGS)
    def test_inactive_package_holds_purchase_without_credit_or_acknowledge(self):
        make_package(slug="medium", sparks=5000, price=Decimal("400.00"), active=False)
        fake = FakePlayClient(purchase=purchased_payload(order_id="GPA.99"))
        with _patch_client(fake):
            resp = self._post({"product_id": "sparks_medium", "purchase_token": "tok-medium"})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "spark_package_unavailable")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        purchase = PlayPurchase.objects.get()
        self.assertEqual(purchase.status, PlayPurchase.Status.MANUAL_REVIEW)
        self.assertEqual(purchase.sparks_granted, 0)
        # Deliberately unacknowledged: Play auto-refunds after three days,
        # which is the correct outcome for money we could not turn into Sparks.
        self.assertEqual(fake.acknowledge_calls, [])

    @override_settings(**PLAY_SETTINGS)
    def test_reactivated_package_fulfills_held_purchase(self):
        make_package(slug="medium", sparks=5000, price=Decimal("400.00"), active=False)
        fake = FakePlayClient(purchase=purchased_payload())
        with _patch_client(fake):
            self._post({"product_id": "sparks_medium", "purchase_token": "tok-medium"})
            # Reactivate the package, then the client retries.
            SparkPackage.objects.filter(slug="medium").update(is_active=True)
            resp = self._post({"product_id": "sparks_medium", "purchase_token": "tok-medium"})

        self.assertEqual(resp.json()["data"]["result"], "credited")
        self.assertEqual(resp.json()["data"]["sparks_granted"], 5000)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 5000)
        purchase = PlayPurchase.objects.get()
        self.assertEqual(purchase.status, PlayPurchase.Status.CREDITED)
        self.assertTrue(purchase.acknowledged)


class PlayVerifyThrottleTests(TestCase):
    """Each verification is a Play API call; one account cannot spend the shared quota."""

    @override_settings(**PLAY_SETTINGS)
    def test_verify_is_limited_per_account(self):
        user = make_user()
        SparkPool.objects.create(user=user, sparks=0, token_balance=0, is_active=True)
        make_package()
        headers = _auth(self.client, "buyer")
        # A cancelled purchase is the cheapest full round trip: verified,
        # nothing credited.
        fake = FakePlayClient(purchase=purchased_payload(purchase_state=1))

        with _patch_client(fake):
            statuses = [
                self.client.post(
                    VERIFY_URL,
                    data={"product_id": "sparks_small", "purchase_token": f"tok-throttle-{i}"},
                    content_type="application/json",
                    **headers,
                ).status_code
                for i in range(11)
            ]

        self.assertEqual(statuses[:10], [200] * 10)
        self.assertEqual(statuses[10], 429)


class PlayReversalTests(TestCase):
    """Refund reconciliation through the voided-purchases poller."""

    def setUp(self):
        self.user = make_user()
        self.package = make_package()

    def _credited_purchase(
        self, sparks_granted=1000, pool_sparks=1000, owner=None, status=PlayPurchase.Status.CREDITED
    ):
        SparkPool.objects.create(user=owner or self.user, sparks=pool_sparks, token_balance=0, is_active=True)
        return PlayPurchase.objects.create(
            purchase_token_hash=hash_purchase_token("tok123"),
            purchase_token="tok123",
            product_id="sparks_small",
            package_slug="starter",
            order_id="GPA.3304-1234-5678-12345",
            user=owner or self.user,
            status=status,
            sparks_granted=sparks_granted,
            play_data={"purchaseState": 0},
        )

    @staticmethod
    def _voided_entry(token="tok123", voided_time=1760000100000, order_id="GPA.3304-1234-5678-12345"):
        return {
            "kind": "androidpublisher#voidedPurchase",
            "purchaseToken": token,
            "orderId": order_id,
            "purchaseTimeMillis": "1760000000000",
            "voidedTimeMillis": str(voided_time),
            "voidedReason": 1,
            "voidedSource": 2,
        }

    @override_settings(**PLAY_SETTINGS)
    def test_voided_purchase_reverses_credit_once(self):
        purchase = self._credited_purchase()
        fake = FakePlayClient(voided_pages=[([self._voided_entry()], None)])
        with _patch_client(fake):
            first = PlayBillingService.reconcile_voided_purchases()
            # A re-delivery of the same void event must not double-reverse.
            fake2 = FakePlayClient(voided_pages=[([self._voided_entry()], None)])
            with _patch_client(fake2):
                second = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(first["data"], {"voided_seen": 1, "reversed": 1})
        self.assertEqual(second["data"], {"voided_seen": 1, "reversed": 0})
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, PlayPurchase.Status.REVERSED)
        self.assertIsNotNone(purchase.reversed_at)

        reversal = SparkTransaction.objects.get(
            entry_type=SparkTransaction.EntryType.DEBIT, reference=play_ledger_reference("tok123")
        )
        self.assertEqual(reversal.source, SparkTransaction.SourceChoices.REFUND)
        self.assertEqual(reversal.spark_amount, -1000)
        # The void record is the processed checkpoint.
        self.assertTrue(PlayVoidedPurchase.objects.filter(purchase_token_hash=hash_purchase_token("tok123")).exists())

    @override_settings(**PLAY_SETTINGS)
    def test_voided_purchase_with_spent_balance_reverses_partially(self):
        # The user already spent most of the sparks.
        self._credited_purchase(pool_sparks=200)
        fake = FakePlayClient(voided_pages=[([self._voided_entry()], None)])
        with _patch_client(fake):
            PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        purchase = PlayPurchase.objects.get()
        self.assertEqual(purchase.metadata["sparks_reversed"], 200)
        self.assertEqual(purchase.metadata["shortfall"], 800)

    @override_settings(**PLAY_SETTINGS)
    def test_voided_purchase_of_erased_owner_records_without_debit(self):
        purchase = self._credited_purchase()
        # Simulate account erasure: the retention signal already detached the
        # purchase, and the pool is gone with the account.
        purchase.user = None
        purchase.owner_deleted_at = purchase.created_at
        purchase.save(update_fields=["user", "owner_deleted_at"])

        fake = FakePlayClient(voided_pages=[([self._voided_entry()], None)])
        with _patch_client(fake):
            result = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(result["data"]["reversed"], 1)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, PlayPurchase.Status.REVERSED)
        self.assertTrue(purchase.metadata["owner_erased"])
        self.assertEqual(purchase.metadata["sparks_reversed"], 0)
        self.assertFalse(
            SparkTransaction.objects.filter(
                reference=play_ledger_reference("tok123"), entry_type=SparkTransaction.EntryType.DEBIT
            ).exists()
        )

    @override_settings(**PLAY_SETTINGS)
    def test_voided_unfulfilled_purchase_closes_without_debit(self):
        self._credited_purchase(status=PlayPurchase.Status.MANUAL_REVIEW, sparks_granted=0, pool_sparks=0)
        fake = FakePlayClient(voided_pages=[([self._voided_entry()], None)])
        with _patch_client(fake):
            result = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(result["data"]["reversed"], 1)
        purchase = PlayPurchase.objects.get()
        self.assertEqual(purchase.status, PlayPurchase.Status.REVERSED)
        self.assertTrue(purchase.metadata["never_credited"])
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)

    @override_settings(**PLAY_SETTINGS)
    def test_unknown_voided_token_is_recorded_and_ignored(self):
        fake = FakePlayClient(voided_pages=[([self._voided_entry(token="stranger", order_id="GPA.OTHER")], None)])
        with _patch_client(fake):
            result = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(result["data"], {"voided_seen": 1, "reversed": 0})
        self.assertTrue(PlayVoidedPurchase.objects.filter(order_id="GPA.OTHER").exists())

    @override_settings(**PLAY_SETTINGS)
    def test_pagination_processes_all_pages(self):
        self._credited_purchase()
        pages = [
            ([self._voided_entry("tokA", 1760000100000, "GPA.A")], "1"),
            ([self._voided_entry("tokB", 1760000200000, "GPA.B")], None),
        ]
        fake = FakePlayClient(voided_pages=pages)
        with _patch_client(fake):
            result = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(result["data"]["voided_seen"], 2)
        self.assertEqual(PlayVoidedPurchase.objects.count(), 2)

    @override_settings(GOOGLE_PLAY_SERVICE_ACCOUNT_JSON="", GOOGLE_PLAY_PRODUCT_MAP={"sparks_small": "starter"})
    def test_reconciliation_skips_when_unconfigured(self):
        result = PlayBillingService.reconcile_voided_purchases()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], "play_billing_not_configured")

    @override_settings(**PLAY_SETTINGS)
    def test_checkpoint_advances_so_older_voided_events_are_not_refetched(self):
        self._credited_purchase(pool_sparks=1000)
        first_run_entries = [
            self._voided_entry("tokA", 1760000100000, "GPA.A"),
            self._voided_entry("tokB", 1760000200000, "GPA.B"),
        ]
        seen_start_times = []
        fake = FakePlayClient(voided_pages=[(first_run_entries, None)])
        original_list = fake.list_voided_purchases

        def recording_list(start_time_millis=None, page_token=None, max_results=200):
            seen_start_times.append(start_time_millis)
            return original_list(start_time_millis, page_token, max_results)

        fake.list_voided_purchases = recording_list
        with _patch_client(fake):
            PlayBillingService.reconcile_voided_purchases()

        # Second run resumes after the newest processed voided timestamp.
        fake2 = FakePlayClient(voided_pages=[([], None)])
        fake2.list_voided_purchases = mock.Mock(wraps=fake2.list_voided_purchases)
        with _patch_client(fake2):
            PlayBillingService.reconcile_voided_purchases()

        checkpoint = PlayVoidedPurchase.objects.order_by("-voided_time_millis").first().voided_time_millis
        self.assertEqual(checkpoint, 1760000200000)

    # ------------------------------------------------------------------
    # Failed clawbacks, Play outages and the 30-day listing window
    # ------------------------------------------------------------------
    def _another_credited_purchase(self, token, order_id, sparks_granted=1000):
        return PlayPurchase.objects.create(
            purchase_token_hash=hash_purchase_token(token),
            purchase_token=token,
            product_id="sparks_small",
            package_slug="starter",
            order_id=order_id,
            user=self.user,
            status=PlayPurchase.Status.CREDITED,
            sparks_granted=sparks_granted,
            play_data={"purchaseState": 0},
        )

    @staticmethod
    def _lock_failing_first_time():
        """lock_spark_pool that fails once (a lock timeout), then works."""
        calls = {"count": 0}

        def flaky_lock(user):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OperationalError("canceling statement due to lock timeout")
            return lock_spark_pool(user)

        return mock.patch("play_billing_app.services.lock_spark_pool", side_effect=flaky_lock)

    @override_settings(**PLAY_SETTINGS)
    def test_failed_clawback_is_retried_after_a_newer_void_moves_the_checkpoint(self):
        older = self._credited_purchase(pool_sparks=2000)
        newer = self._another_credited_purchase("tok-newer", "GPA.NEWER")
        entries = [
            self._voided_entry("tok123", 1760000100000),
            self._voided_entry("tok-newer", 1760000200000, "GPA.NEWER"),
        ]

        with _patch_client(FakePlayClient(voided_pages=[(entries, None)])), self._lock_failing_first_time():
            first = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(first["data"], {"voided_seen": 2, "reversed": 1})
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, PlayPurchase.Status.CREDITED)
        self.assertEqual(newer.status, PlayPurchase.Status.REVERSED)
        older_void = PlayVoidedPurchase.objects.get(purchase_token_hash=hash_purchase_token("tok123"))
        self.assertFalse(older_void.processed)
        # The checkpoint is already past the failed void, so Play will not
        # list it again: the retry has to come from our own records.
        checkpoint = PlayVoidedPurchase.objects.order_by("-voided_time_millis").first().voided_time_millis
        self.assertEqual(checkpoint, 1760000200000)

        with _patch_client(FakePlayClient(voided_pages=[([], None)])):
            second = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(second["data"], {"voided_seen": 0, "reversed": 1})
        older.refresh_from_db()
        self.assertEqual(older.status, PlayPurchase.Status.REVERSED)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        self.assertFalse(PlayVoidedPurchase.objects.filter(processed=False).exists())
        refunds = SparkTransaction.objects.filter(
            entry_type=SparkTransaction.EntryType.DEBIT,
            source=SparkTransaction.SourceChoices.REFUND,
        )
        self.assertEqual(refunds.count(), 2)

        # A third run has nothing left to do and debits nothing twice.
        with _patch_client(FakePlayClient(voided_pages=[([], None)])):
            third = PlayBillingService.reconcile_voided_purchases()
        self.assertEqual(third["data"], {"voided_seen": 0, "reversed": 0})
        self.assertEqual(refunds.count(), 2)

    @override_settings(**PLAY_SETTINGS)
    def test_failed_clawback_is_retried_even_when_play_is_unreachable(self):
        purchase = self._credited_purchase()
        PlayVoidedPurchase.objects.create(
            purchase_token_hash=hash_purchase_token("tok123"),
            order_id="GPA.3304-1234-5678-12345",
            voided_time_millis=1760000100000,
            voided_reason="1",
            processed=False,
        )
        fake = FakePlayClient()
        fake.list_voided_purchases = mock.Mock(side_effect=PlayApiError("play_api_unavailable"))

        with _patch_client(fake):
            result = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(result, {"status": "fail", "data": "play_api_unavailable"})
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, PlayPurchase.Status.REVERSED)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 0)
        self.assertTrue(PlayVoidedPurchase.objects.get().processed)

    @override_settings(**PLAY_SETTINGS)
    def test_play_api_error_while_listing_is_reported_not_raised(self):
        purchase = self._credited_purchase()
        fake = FakePlayClient()
        fake.list_voided_purchases = mock.Mock(side_effect=PlayApiError("play_api_unavailable"))

        with _patch_client(fake):
            result = PlayBillingService.reconcile_voided_purchases()

        self.assertEqual(result, {"status": "fail", "data": "play_api_unavailable"})
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, PlayPurchase.Status.CREDITED)
        self.assertFalse(PlayVoidedPurchase.objects.exists())

    @override_settings(**PLAY_SETTINGS)
    def test_reconcile_task_returns_the_play_outage_instead_of_raising(self):
        from play_billing_app.tasks import reconcile_play_voided_purchases

        fake = FakePlayClient()
        fake.list_voided_purchases = mock.Mock(side_effect=PlayApiError("play_api_unavailable"))
        with _patch_client(fake):
            result = reconcile_play_voided_purchases()

        self.assertEqual(result, {"status": "fail", "data": "play_api_unavailable"})

    def _start_time_sent_for_checkpoint(self, checkpoint_ms):
        PlayVoidedPurchase.objects.create(
            purchase_token_hash=hash_purchase_token("checkpoint"),
            voided_time_millis=checkpoint_ms,
            processed=True,
        )
        fake = FakePlayClient(voided_pages=[([], None)])
        fake.list_voided_purchases = mock.Mock(wraps=fake.list_voided_purchases)
        with _patch_client(fake):
            PlayBillingService.reconcile_voided_purchases()
        return fake.list_voided_purchases.call_args.kwargs["start_time_millis"]

    @override_settings(**PLAY_SETTINGS)
    def test_checkpoint_older_than_the_play_window_is_clamped(self):
        day_ms = 24 * 60 * 60 * 1000
        before_ms = int(timezone.now().timestamp() * 1000)
        start_time = self._start_time_sent_for_checkpoint(before_ms - 45 * day_ms)
        after_ms = int(timezone.now().timestamp() * 1000)

        # Play rejects a startTime older than 30 days; the poller stays inside.
        self.assertGreaterEqual(start_time, before_ms - VOIDED_LOOKBACK_MS)
        self.assertLessEqual(start_time, after_ms - VOIDED_LOOKBACK_MS)
        self.assertGreater(start_time, after_ms - 30 * day_ms)

    @override_settings(**PLAY_SETTINGS)
    def test_recent_checkpoint_is_sent_unchanged(self):
        recent_ms = int(timezone.now().timestamp() * 1000) - 2 * 24 * 60 * 60 * 1000
        self.assertEqual(self._start_time_sent_for_checkpoint(recent_ms), recent_ms)


class PlayErasureRetentionTests(TestCase):
    """Account deletion keeps the financial evidence and detaches the owner."""

    def test_user_deletion_retains_purchase_row(self):
        user = make_user()
        SparkPool.objects.create(user=user, sparks=0, token_balance=0, is_active=True)
        purchase = PlayPurchase.objects.create(
            purchase_token_hash=hash_purchase_token("tok123"),
            purchase_token="tok123",
            product_id="sparks_small",
            package_slug="starter",
            order_id=None,
            user=user,
            status=PlayPurchase.Status.CREDITED,
            sparks_granted=1000,
            play_data={"purchaseState": 0},
        )
        user.delete()
        purchase.refresh_from_db()
        self.assertIsNone(purchase.user)
        self.assertIsNotNone(purchase.owner_deleted_at)
        self.assertEqual(purchase.status, PlayPurchase.Status.CREDITED)
        self.assertEqual(purchase.sparks_granted, 1000)


class PlayCatalogAnnotationTests(TestCase):
    """The public package catalog exposes Play product ids for mapped slugs."""

    def test_mapped_package_gets_play_product_id(self):
        make_package()
        make_package(slug="medium", sparks=5000, price=Decimal("400.00"))
        from capp.classes.billing.charge_service import ChargeService

        with override_settings(GOOGLE_PLAY_PRODUCT_MAP={"sparks_small": "starter"}):
            result = ChargeService.get_available_packages()
        packages = {p["slug"]: p for p in result["data"]["packages"] if p["type"] == "spark"}
        self.assertEqual(packages["starter"]["play_product_id"], "sparks_small")
        self.assertNotIn("play_product_id", packages["medium"])

    def test_unmapped_catalog_is_unchanged(self):
        make_package()
        from capp.classes.billing.charge_service import ChargeService

        with override_settings(GOOGLE_PLAY_PRODUCT_MAP={}):
            result = ChargeService.get_available_packages()
        entry = next(p for p in result["data"]["packages"] if p["type"] == "spark")
        self.assertNotIn("play_product_id", entry)
