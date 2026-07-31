# paystack_app/tests_ownership.py
"""
Who may look at a payment, and what a refusal is allowed to reveal.

A Paystack reference travels in a redirect URL, so it is not a secret and it is
not proof of ownership. The properties under test are that ownership is always
re-derived server-side, and that refusing a caller never tells them whether the
reference they guessed exists.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework_simplejwt.tokens import RefreshToken

from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.models import (
    GuestIdentity,
    GuestTokenQuota,
    GuestTransferGrant,
    SparkPackage,
    SparkPool,
)
from paystack_app.models import PaystackTransaction
from paystack_app.tests import make_user

COOKIE = settings.GUEST_CREDENTIAL_COOKIE_NAME


def auth_header(user):
    """The API authenticates accounts by JWT only; there is no session login."""
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class ThrottleFreeMixin:
    """Anonymous throttles share the process cache; reset so tests do not starve."""

    def setUp(self):
        cache.clear()
        super().setUp()


def make_spark_package(slug="starter", sparks=1000, price="100.00", active=True):
    return SparkPackage.objects.create(
        slug=slug,
        name=slug.title(),
        base_spark_amount=sparks,
        base_price_kes=Decimal(price),
        discount_percent=Decimal("0.00"),
        final_price_kes=Decimal(price),
        is_active=active,
    )


def make_txn(reference, *, user=None, guest_session_id=None, status=None, **extra):
    return PaystackTransaction.objects.create(
        user=user,
        guest_session_id=guest_session_id,
        reference=reference,
        amount=Decimal("100.00"),
        currency="KES",
        status=status or PaystackTransaction.StatusChoices.SUCCESS,
        package_slug="starter",
        package_type=PaystackTransaction.PackageType.SPARK,
        expected_amount_minor=10000,
        **extra,
    )


class VerifyOwnershipTests(ThrottleFreeMixin, TestCase):
    """Authenticated callers see only their own payments."""

    def setUp(self):
        self.url = reverse("paystack_verify")
        self.owner = make_user("owner", "owner@example.com")
        self.stranger = make_user("stranger", "stranger@example.com")
        SparkPool.objects.create(user=self.owner, sparks=1000, token_balance=0, is_active=True)
        SparkPool.objects.create(user=self.stranger, sparks=0, token_balance=0, is_active=True)
        self.txn = make_txn("ref_owned_by_owner", user=self.owner)

    def _get(self, reference, user=None):
        headers = auth_header(user) if user is not None else {}
        return self.client.get(self.url, {"reference": reference}, **headers)

    def test_owner_can_verify_own_payment(self):
        resp = self._get("ref_owned_by_owner", self.owner)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["reference"], "ref_owned_by_owner")
        self.assertEqual(resp.json()["data"]["spark_pool_balance"], 1000)

    def test_another_user_cannot_verify_it(self):
        resp = self._get("ref_owned_by_owner", self.stranger)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["data"], "transaction_not_found")

    def test_refusal_is_indistinguishable_from_a_reference_that_never_existed(self):
        """
        This is the whole point of the consistent 404: an attacker enumerating
        references must not be able to tell a real payment from a made-up one.
        """
        unowned = self._get("ref_owned_by_owner", self.stranger)
        nonexistent = self._get("ref_never_issued", self.stranger)
        self.assertEqual(unowned.status_code, nonexistent.status_code)
        self.assertEqual(unowned.json(), nonexistent.json())

    def test_reference_is_required(self):
        resp = self.client.get(self.url, **auth_header(self.owner))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "reference_required")

    def test_a_guest_cookie_cannot_reach_a_users_payment(self):
        identity, credential = GuestIdentityService.mint()
        self.client.cookies[COOKIE] = credential
        resp = self._get("ref_owned_by_owner")
        self.assertEqual(resp.status_code, 404)

    def test_anonymous_caller_without_a_cookie_is_told_nothing_about_the_payment(self):
        resp = self._get("ref_owned_by_owner")
        self.assertEqual(resp.status_code, 401)
        # Deliberately not 404: this says only that no guest credential was sent.
        self.assertEqual(resp.json()["data"], "guest_session_required")


class GuestVerifyOwnershipTests(ThrottleFreeMixin, TestCase):
    """Guest ownership comes from the HttpOnly cookie, never from the URL."""

    def setUp(self):
        self.url = reverse("paystack_verify")
        self.identity, self.credential = GuestIdentityService.mint()
        self.other_identity, self.other_credential = GuestIdentityService.mint()
        GuestTokenQuota.objects.create(session_id=self.identity.owner_key, sparks=1000)
        self.txn = make_txn("ref_owned_by_guest", guest_session_id=self.identity.owner_key)

    def _get(self, reference, credential=None):
        if credential is not None:
            self.client.cookies[COOKIE] = credential
        return self.client.get(self.url, {"reference": reference})

    def test_guest_returning_from_paystack_sees_its_purchase(self):
        resp = self._get("ref_owned_by_guest", self.credential)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["reference"], "ref_owned_by_guest")
        self.assertEqual(data["spark_pool_balance"], 1000)

    def test_response_never_echoes_the_guest_identity(self):
        """Nothing in the payload lets a guest ID leak into the browser or a log."""
        resp = self._get("ref_owned_by_guest", self.credential)
        body = resp.content.decode()
        self.assertNotIn(str(self.identity.owner_key), body)
        self.assertNotIn(self.credential, body)

    def test_another_guest_cannot_verify_it(self):
        resp = self._get("ref_owned_by_guest", self.other_credential)
        self.assertEqual(resp.status_code, 404)

    def test_cross_guest_refusal_matches_an_unknown_reference(self):
        unowned = self._get("ref_owned_by_guest", self.other_credential)
        nonexistent = self._get("ref_never_issued")
        self.assertEqual(unowned.status_code, nonexistent.status_code)
        self.assertEqual(unowned.json(), nonexistent.json())

    def test_missing_cookie_is_refused(self):
        resp = self.client.get(self.url, {"reference": "ref_owned_by_guest"})
        self.assertEqual(resp.status_code, 401)

    def test_unknown_credential_is_refused(self):
        resp = self._get("ref_owned_by_guest", "not-a-real-credential")
        self.assertEqual(resp.status_code, 401)

    def test_expired_identity_is_refused(self):
        GuestIdentity.objects.filter(pk=self.identity.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        resp = self._get("ref_owned_by_guest", self.credential)
        self.assertEqual(resp.status_code, 401)

    def test_revoked_identity_is_refused(self):
        GuestIdentityService.revoke(self.identity)
        resp = self._get("ref_owned_by_guest", self.credential)
        self.assertEqual(resp.status_code, 401)

    def test_rotated_credential_still_owns_the_purchase(self):
        """Ownership keys on the identity, not the credential, so rotation is safe."""
        fresh = GuestIdentityService.rotate_credential(self.identity)
        resp = self._get("ref_owned_by_guest", fresh)
        self.assertEqual(resp.status_code, 200)

    def test_guest_id_is_never_accepted_from_the_query_string(self):
        """A caller naming a guest it holds no credential for gets nowhere."""
        resp = self.client.get(
            self.url,
            {"reference": "ref_owned_by_guest", "session_id": str(self.identity.owner_key)},
        )
        self.assertEqual(resp.status_code, 401)


class ClaimedPurchaseTests(ThrottleFreeMixin, TestCase):
    """Claiming moves the purchase to the account and closes the guest door."""

    def setUp(self):
        self.url = reverse("paystack_verify")
        self.identity, self.credential = GuestIdentityService.mint()
        GuestTokenQuota.objects.create(session_id=self.identity.owner_key, sparks=500)
        self.txn = make_txn("ref_to_be_claimed", guest_session_id=self.identity.owner_key)
        self.user = make_user("claimer", "claimer@example.com")
        SparkPool.objects.create(user=self.user, sparks=0, token_balance=0, is_active=True)

    def _claim(self):
        # Stands in for signup, which is the only thing that writes a grant.
        # Without one the account is not entitled and nothing moves.
        GuestTransferGrant.objects.get_or_create(
            user=self.user,
            guest_identity=self.identity,
            defaults={
                "status": GuestTransferGrant.Status.PENDING,
                "origin_guest_session_id": self.identity.owner_key,
            },
        )
        return GuestClaimService.claim(self.user, self.identity)

    def test_history_survives_the_claim_with_immutable_provenance(self):
        self._claim()
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.user_id, self.user.user_id)
        # Released, so a stale cookie cannot reach it any more.
        self.assertIsNone(self.txn.guest_session_id)
        # Kept forever, so the purchase can still be traced to where it began.
        self.assertEqual(self.txn.origin_guest_session_id, self.identity.owner_key)
        self.assertIsNotNone(self.txn.claimed_from_guest_at)

    def test_claiming_user_can_verify_the_claimed_purchase(self):
        self._claim()
        resp = self.client.get(self.url, {"reference": "ref_to_be_claimed"}, **auth_header(self.user))
        self.assertEqual(resp.status_code, 200)

    def test_stale_guest_cookie_cannot_reach_the_claimed_purchase(self):
        """
        Provenance is not authorization. After the claim the identity is CLAIMED,
        so the cookie no longer resolves at all.
        """
        self._claim()
        self.client.cookies[COOKIE] = self.credential
        resp = self.client.get(self.url, {"reference": "ref_to_be_claimed"})
        self.assertEqual(resp.status_code, 401)

    def test_a_different_user_still_cannot_see_the_claimed_purchase(self):
        self._claim()
        other = make_user("other", "other@example.com")
        resp = self.client.get(self.url, {"reference": "ref_to_be_claimed"}, **auth_header(other))
        self.assertEqual(resp.status_code, 404)


@override_settings(
    PAYMENT_CALLBACK_ALLOWED_ORIGINS=["https://app.uwenzai.com"],
    PAYMENT_CALLBACK_ALLOWED_PATHS=["/payment/verify"],
    PAYMENT_CALLBACK_DEFAULT_PATH="/payment/verify",
)
class InitiatePaymentCallbackTests(ThrottleFreeMixin, TestCase):
    """The initiation endpoint refuses a destination it did not offer."""

    def setUp(self):
        self.url = reverse("paystack_initiate")
        self.user = make_user()
        make_spark_package()

    def _post(self, authenticated=True, **extra):
        payload = {"package_slug": "starter", "package_type": "spark"}
        payload.update(extra)
        headers = auth_header(self.user) if authenticated else {}
        return self.client.post(self.url, data=payload, content_type="application/json", **headers)

    def test_hostile_callback_is_refused_outright(self):
        """
        Refused, not silently replaced with the default: an open-redirect attempt
        should be a visible 400 rather than a request that appears to succeed.
        """
        for hostile in (
            "https://evil.example/payment/verify",
            "//evil.example",
            "javascript:alert(1)",
            "/payment/verify?next=https://evil.example",
            "/admin/",
        ):
            with self.subTest(hostile=hostile):
                resp = self._post(callback_url=hostile)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["data"], "invalid_callback_destination")
                self.assertFalse(PaystackTransaction.objects.exists())

    def test_package_is_required(self):
        self.assertEqual(self._post(package_slug="").status_code, 400)
        self.assertEqual(self._post(package_type="").status_code, 400)

    def test_guest_without_a_cookie_cannot_initiate(self):
        resp = self._post(authenticated=False)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["data"], "guest_session_required")

    def test_guest_cannot_buy_a_subscription(self):
        resp = self._post(authenticated=False, package_type="subscription")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "guests_can_only_buy_sparks")
