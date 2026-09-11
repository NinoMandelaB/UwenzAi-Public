# capp/tests_guest_transfer_binding.py
"""
Who is allowed to receive a guest session's Sparks, conversations and purchases.

The bug this suite exists to keep closed: the transfer used to be authorised by
"a guest credential cookie arrived alongside a successful verified login". That
is equally true of a guest who signs up and of a guest who walks up to the login
form and signs in to an account they already had, so the second case quietly
absorbed the guest's state — money included — into a pre-existing account.

Authority is now an explicit server-created record, ``GuestTransferGrant``,
written during signup inside the account's own transaction from the identity the
server resolved from the HttpOnly cookie. Every test below is a statement about
that record: who gets one, who does not, and that it can be spent exactly once.

The record belongs to the guest session rather than to the first account that
asked for it, because an account is created before its email is verified and an
abandoned signup must not keep a guest's Sparks. ``AbandonedSignupRetryTests``
covers that handover and — more importantly — everything it must not do.

``GuestTransferBindingConcurrencyTests`` is a ``TransactionTestCase`` because the
races it describes are invisible under ``TestCase``: that wraps each test in a
transaction that never commits, so a second thread cannot see the first thread's
writes and every race passes vacuously.
"""

import threading
import uuid
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import get_resolver

from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.classes.guest.guest_transfer_grant_service import GuestTransferGrantService
from capp.constants import SIGNUP_SPARKS
from capp.models import (
    Conversation,
    CustomUser,
    GuestIdentity,
    GuestTokenQuota,
    GuestTransferGrant,
    SparkPool,
    SparkTransaction,
    UsageCharge,
)
from capp.tests import RECAPTCHA_OK, _make_character, _make_user
from paystack_app.models import PaystackTransaction

COOKIE = settings.GUEST_CREDENTIAL_COOKIE_NAME

ENSURE_URL = "/api/v1/guest/session/"
MIGRATE_URL = "/api/v1/guest/session/migrate/"
SIGNUP_URL = "/api/v1/auth/signup/"
SIGNIN_URL = "/api/v1/auth/signin/"
SIGNOUT_URL = "/api/v1/auth/signout/"
REFRESH_URL = "/api/v1/auth/refresh/"
VERIFY_URL = "/api/v1/auth/verify-email/"
GOOGLE_URL = "/api/v1/auth/google/"

MIGRATION_OPEN = {
    "GUEST_LEGACY_MIGRATION_ENABLED": True,
    "GUEST_LEGACY_MIGRATION_CUTOFF": "2099-01-01",
}

KNOWN_CODE = "424242"


def signup_payload(username="newbie", **extra):
    payload = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "pw123456",
        "confirm_password": "pw123456",
        "age_confirmed": True,
        "terms_accepted": True,
        "privacy_accepted": True,
    }
    payload.update(extra)
    return payload


class TransferFlowMixin:
    """Drives the real endpoints, because the binding is a property of the flow."""

    def setUp(self):
        # Throttles share the process cache; reset so tests do not starve each other.
        cache.clear()
        self.character = _make_character(name="Amina", slug="amina")
        super().setUp()

    def ensure_session(self, client=None):
        client = client or self.client
        resp = client.post(ENSURE_URL, data={}, content_type="application/json")
        self.assertIn(resp.status_code, (200, 201))
        return client.cookies[COOKIE].value

    def seed_guest_state(self, identity, sparks=250, reference=None):
        """Give a guest something worth stealing."""
        owner_key = identity.owner_key
        conversation = Conversation.objects.create(character=self.character, guest_session_id=owner_key)
        GuestTokenQuota.objects.create(
            session_id=owner_key,
            sparks=sparks,
            lifetime_tokens_used=4321,
            daily_tokens_used=42,
        )
        charge = UsageCharge.objects.create(
            guest_session_id=owner_key,
            charge_source=UsageCharge.ChargeSource.SPARK_POOL,
            sparks_charged=7,
        )
        purchase = PaystackTransaction.objects.create(
            guest_session_id=owner_key,
            reference=reference or f"ref-{uuid.uuid4().hex[:12]}",
            amount="100.00",
            status=PaystackTransaction.StatusChoices.SUCCESS,
        )
        return conversation, charge, purchase

    def signup(self, client=None, username="newbie", **extra):
        client = client or self.client
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ), mock.patch(
            "capp.classes.auth.email_verification_service.EmailVerificationService._generate_code",
            return_value=KNOWN_CODE,
        ):
            return client.post(
                SIGNUP_URL,
                data=signup_payload(username, **extra),
                content_type="application/json",
            )

    def verify(self, user_id, client=None, code=KNOWN_CODE):
        client = client or self.client
        return client.post(
            VERIFY_URL,
            data={"user_id": str(user_id), "code": code},
            content_type="application/json",
        )

    def signin(self, username, password="pw123456", client=None):
        client = client or self.client
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            return client.post(
                SIGNIN_URL,
                data={"username": username, "password": password},
                content_type="application/json",
            )

    def assertGuestUntouched(self, identity, conversation, charge, purchase, sparks=250):
        """The guest still owns everything and can still come back for it."""
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.ACTIVE)
        self.assertIsNone(identity.claimed_by_id)
        self.assertIsNone(identity.claimed_at)

        conversation.refresh_from_db()
        self.assertIsNone(conversation.user_id)
        self.assertEqual(conversation.guest_session_id, identity.owner_key)
        self.assertIsNone(conversation.claimed_from_guest_at)

        charge.refresh_from_db()
        self.assertIsNone(charge.user_id)
        self.assertEqual(charge.guest_session_id, identity.owner_key)

        purchase.refresh_from_db()
        self.assertIsNone(purchase.user_id)
        self.assertEqual(purchase.guest_session_id, identity.owner_key)

        quota = GuestTokenQuota.objects.get(session_id=identity.owner_key)
        self.assertEqual(quota.sparks, sparks)
        self.assertIsNone(quota.claimed_by_id)


class SignupToVerifiedLoginTests(TransferFlowMixin, TestCase):
    """The one legitimate route: signup from a guest session, then verified login."""

    def test_signup_then_verified_login_transfers_everything_exactly_once(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        owner_key = identity.owner_key
        conversation, charge, purchase = self.seed_guest_state(identity)

        signup = self.signup(username="newbie")
        self.assertIn(signup.status_code, (200, 201))
        user = CustomUser.objects.get(username="newbie")

        # Signup records eligibility and moves nothing: the account cannot prove
        # control of the email address yet.
        grant = GuestTransferGrant.objects.get(user=user)
        self.assertEqual(grant.status, GuestTransferGrant.Status.PENDING)
        self.assertEqual(grant.guest_identity_id, identity.pk)
        self.assertEqual(grant.origin_guest_session_id, owner_key)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

        self.assertEqual(self.verify(user.user_id).status_code, 200)
        resp = self.signin("newbie")
        self.assertEqual(resp.status_code, 200)

        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, user.pk)
        self.assertIsNone(conversation.guest_session_id)
        charge.refresh_from_db()
        self.assertEqual(charge.user_id, user.pk)
        purchase.refresh_from_db()
        self.assertEqual(purchase.user_id, user.pk)
        # The guest's 250 land on top of the signup grant, through the ledger.
        self.assertEqual(SparkPool.objects.get(user=user).sparks, SIGNUP_SPARKS + 250)
        entry = SparkTransaction.objects.get(user=user, source=SparkTransaction.SourceChoices.ADJUSTMENT)
        self.assertEqual(entry.spark_amount, 250)
        self.assertEqual(entry.metadata["origin_guest_session_id"], owner_key)

        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)
        self.assertEqual(identity.claimed_by_id, user.pk)

        grant.refresh_from_db()
        self.assertEqual(grant.status, GuestTransferGrant.Status.CONSUMED)
        self.assertIsNotNone(grant.consumed_at)

        # The credential is spent, so the browser is told to drop it.
        self.assertEqual(resp.cookies[COOKIE].value, "")

    def test_a_second_login_of_the_same_account_credits_nothing_further(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        self.seed_guest_state(identity)

        self.signup(username="newbie")
        user = CustomUser.objects.get(username="newbie")
        self.verify(user.user_id)
        self.signin("newbie")

        cache.clear()
        # A browser that still holds a working credential for the spent identity.
        replay = Client()
        replay.cookies[COOKIE] = GuestIdentityService.rotate_credential(identity)
        resp = self.signin("newbie", client=replay)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SparkPool.objects.get(user=user).sparks, SIGNUP_SPARKS + 250)
        self.assertEqual(
            SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT).count(),
            1,
        )

    def test_purchase_provenance_survives_the_transfer(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        owner_key = identity.owner_key
        _conversation, _charge, purchase = self.seed_guest_state(identity, reference="ref-provenance")

        self.signup(username="newbie")
        user = CustomUser.objects.get(username="newbie")
        self.verify(user.user_id)
        self.signin("newbie")

        purchase.refresh_from_db()
        self.assertEqual(purchase.user_id, user.pk)
        # Released, so a stale cookie cannot reach it; kept, so the purchase can
        # still be traced to the anonymous session that paid for it.
        self.assertIsNone(purchase.guest_session_id)
        self.assertEqual(purchase.origin_guest_session_id, owner_key)
        self.assertIsNotNone(purchase.claimed_from_guest_at)
        self.assertEqual(PaystackTransaction.objects.filter(reference="ref-provenance").count(), 1)

    def test_signup_without_a_guest_session_records_no_eligibility(self):
        signup = self.signup(username="newbie")

        self.assertIn(signup.status_code, (200, 201))
        self.assertFalse(GuestTransferGrant.objects.exists())
        self.assertFalse(GuestIdentity.objects.exists())

    def test_signup_from_a_revoked_guest_session_records_no_eligibility(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        GuestIdentityService.revoke(identity)

        self.signup(username="newbie")

        self.assertFalse(GuestTransferGrant.objects.exists())


class AbandonedSignupRetryTests(TransferFlowMixin, TestCase):
    """
    The follow-up bug: an account created and never verified used to keep a
    guest's Sparks forever.

    Signup does not finish at signup. The grant is written when the account row
    is, before the email is proved, so a visitor who starts signing up, walks
    away, and later signs up again from the same guest session had their transfer
    held by the first, abandoned account — which could never spend it, because the
    claim path requires a verified user. Nothing reached the account they
    actually used.

    The entitlement therefore belongs to the guest session: a ``PENDING`` grant
    whose holder is unverified moves to the next signup from that same
    server-resolved identity. Everything below is a statement about the edges of
    that rule — that it fires for the abandoned case and for nothing else.
    """

    def _abandoned_then_retried(self):
        """The reported sequence, up to the point just before the retry logs in."""
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        rows = self.seed_guest_state(identity)

        self.assertIn(self.signup(username="abandoned").status_code, (200, 201))
        abandoned = CustomUser.objects.get(username="abandoned")
        # Step 3 of the report: the visitor goes back to guest mode and their
        # Sparks are still there. Nothing has moved, because nothing may move
        # before an email is verified.
        self.assertGuestUntouched(identity, *rows)

        self.assertIn(self.signup(username="retried").status_code, (200, 201))
        retried = CustomUser.objects.get(username="retried")

        return identity, abandoned, retried, rows

    def test_the_retry_after_an_abandoned_signup_receives_the_transfer(self):
        identity, abandoned, retried, (conversation, charge, purchase) = self._abandoned_then_retried()
        owner_key = identity.owner_key

        # The entitlement has moved, and it is recorded as having moved.
        grants = GuestTransferGrant.objects.filter(guest_identity=identity)
        self.assertEqual(grants.count(), 1)
        grant = grants.get()
        self.assertEqual(grant.user_id, retried.pk)
        self.assertEqual(grant.previous_user_id, abandoned.pk)
        self.assertIsNotNone(grant.reassigned_at)
        self.assertEqual(grant.status, GuestTransferGrant.Status.PENDING)
        # Still the same guest session; only the account waiting on it changed.
        self.assertEqual(grant.origin_guest_session_id, owner_key)
        self.assertFalse(GuestTransferGrant.objects.filter(user=abandoned).exists())
        # And still nothing has been transferred: the retry is not verified yet.
        self.assertGuestUntouched(identity, conversation, charge, purchase)

        self.assertEqual(self.verify(retried.user_id).status_code, 200)
        resp = self.signin("retried")
        self.assertEqual(resp.status_code, 200)

        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, retried.pk)
        charge.refresh_from_db()
        self.assertEqual(charge.user_id, retried.pk)
        purchase.refresh_from_db()
        self.assertEqual(purchase.user_id, retried.pk)
        self.assertEqual(purchase.origin_guest_session_id, owner_key)

        self.assertEqual(SparkPool.objects.get(user=retried).sparks, SIGNUP_SPARKS + 250)
        adjustments = SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT)
        self.assertEqual(adjustments.count(), 1)
        self.assertEqual(adjustments.get().user_id, retried.pk)

        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)
        self.assertEqual(identity.claimed_by_id, retried.pk)
        grant.refresh_from_db()
        self.assertEqual(grant.status, GuestTransferGrant.Status.CONSUMED)
        self.assertEqual(resp.cookies[COOKIE].value, "")

        # The abandoned account got its signup bonus and not one Spark more.
        self.assertEqual(SparkPool.objects.get(user=abandoned).sparks, SIGNUP_SPARKS)

    def test_the_displaced_account_transfers_nothing_if_it_verifies_and_logs_in_first(self):
        """
        The abandoned account is not merely last in the queue — it is out of it.
        It verifies and logs in *before* the retry does, and still moves nothing,
        which is also what proves the retry's entitlement is not just a duplicate.
        """
        identity, abandoned, retried, (conversation, charge, purchase) = self._abandoned_then_retried()

        self.assertEqual(self.verify(abandoned.user_id).status_code, 200)
        resp = self.signin("abandoned")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)
        self.assertEqual(SparkPool.objects.get(user=abandoned).sparks, SIGNUP_SPARKS)
        self.assertFalse(SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT).exists())
        # Nothing was claimed, so the guest credential is still live for the retry.
        self.assertNotIn(COOKIE, resp.cookies)

        self.verify(retried.user_id)
        self.assertEqual(self.signin("retried").status_code, 200)

        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, retried.pk)
        self.assertEqual(SparkPool.objects.get(user=retried).sparks, SIGNUP_SPARKS + 250)
        self.assertEqual(
            SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT).count(),
            1,
        )

    def test_the_grant_follows_the_last_of_a_chain_of_abandoned_signups(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, _charge, _purchase = self.seed_guest_state(identity)

        for username in ("first", "second", "third"):
            self.assertIn(self.signup(username=username).status_code, (200, 201))

        third = CustomUser.objects.get(username="third")
        grant = GuestTransferGrant.objects.get(guest_identity=identity)
        self.assertEqual(grant.user_id, third.pk)
        self.assertEqual(grant.previous_user_id, CustomUser.objects.get(username="second").pk)
        self.assertEqual(GuestTransferGrant.objects.count(), 1)

        self.verify(third.user_id)
        self.assertEqual(self.signin("third").status_code, 200)

        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, third.pk)
        self.assertEqual(SparkPool.objects.get(user=third).sparks, SIGNUP_SPARKS + 250)

    def test_an_unverified_holder_cannot_spend_its_own_grant(self):
        """
        The user's own requirement: no transfer before the email is verified. The
        grant is written at signup, so this guard is the only thing standing
        between an unverified account and the guest's Sparks.
        """
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)
        self.signup(username="abandoned")
        abandoned = CustomUser.objects.get(username="abandoned")

        result = GuestClaimService.claim(abandoned, identity)

        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "user_not_verified")
        self.assertGuestUntouched(identity, conversation, charge, purchase)
        self.assertEqual(
            GuestTransferGrant.objects.get(user=abandoned).status,
            GuestTransferGrant.Status.PENDING,
        )

    def test_a_consumed_grant_is_never_reassigned(self):
        """
        A spent entitlement cannot be handed to a later signup, or the guest's
        state would be transferred twice — the second time out of the account
        that legitimately holds it.
        """
        identity, _credential = GuestIdentityService.mint()
        owner = _make_user(username="owner", email="owner@example.com", verified=True)
        grant = GuestTransferGrantService.grant_at_signup(owner, identity)
        self.assertTrue(GuestTransferGrantService.consume(grant))
        latecomer = _make_user(username="latecomer", email="latecomer@example.com", verified=False)

        self.assertIsNone(GuestTransferGrantService.grant_at_signup(latecomer, identity))

        grant.refresh_from_db()
        self.assertEqual(grant.user_id, owner.pk)
        self.assertEqual(grant.status, GuestTransferGrant.Status.CONSUMED)
        self.assertIsNone(grant.previous_user_id)
        self.assertFalse(GuestTransferGrant.objects.filter(user=latecomer).exists())

    def test_a_verified_but_unspent_grant_is_never_reassigned(self):
        """
        The dangerous middle state: the holder has verified but has not logged in
        yet, so the grant is still ``PENDING``. It is about to be spent and must
        not be stealable by a signup from the same guest session.
        """
        identity, _credential = GuestIdentityService.mint()
        owner = _make_user(username="owner", email="owner@example.com", verified=True)
        GuestTransferGrantService.grant_at_signup(owner, identity)
        newcomer = _make_user(username="newcomer", email="newcomer@example.com", verified=False)

        self.assertIsNone(GuestTransferGrantService.grant_at_signup(newcomer, identity))

        grant = GuestTransferGrant.objects.get(guest_identity=identity)
        self.assertEqual(grant.user_id, owner.pk)
        self.assertEqual(grant.status, GuestTransferGrant.Status.PENDING)
        self.assertIsNone(grant.previous_user_id)

    def test_a_signup_from_a_different_guest_session_does_not_touch_the_grant(self):
        """Reassignment is scoped to one identity: the retry has to be the same guest."""
        abandoning = Client()
        self.ensure_session(abandoning)
        abandoned_identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(abandoned_identity)
        self.signup(client=abandoning, username="abandoned")
        abandoned = CustomUser.objects.get(username="abandoned")

        stranger = Client()
        self.ensure_session(stranger)
        stranger_identity = GuestIdentity.objects.exclude(pk=abandoned_identity.pk).get()
        self.signup(client=stranger, username="stranger")
        stranger_user = CustomUser.objects.get(username="stranger")

        # Each guest session backs exactly its own signup.
        self.assertEqual(
            GuestTransferGrant.objects.get(guest_identity=abandoned_identity).user_id,
            abandoned.pk,
        )
        self.assertEqual(
            GuestTransferGrant.objects.get(guest_identity=stranger_identity).user_id,
            stranger_user.pk,
        )
        self.assertIsNone(GuestTransferGrant.objects.get(guest_identity=abandoned_identity).previous_user_id)

        self.verify(stranger_user.user_id, client=stranger)
        self.assertEqual(self.signin("stranger", client=stranger).status_code, 200)

        self.assertGuestUntouched(abandoned_identity, conversation, charge, purchase)

    def test_a_reassigned_grant_dies_with_the_account_that_holds_it(self):
        """
        ``previous_user`` must not resurrect an entitlement. If the retry account
        is deleted the grant goes with it (CASCADE), rather than falling back to
        the abandoned account it was taken from.
        """
        identity, _abandoned, retried, _rows = self._abandoned_then_retried()

        retried.delete()

        self.assertFalse(GuestTransferGrant.objects.filter(guest_identity=identity).exists())


class PreExistingAccountTests(TransferFlowMixin, TestCase):
    """
    The reported bug, and the shape of every variant of it.

    None of these accounts was created from the guest session in play, so none of
    them may receive a single Spark from it.
    """

    def test_login_to_a_pre_existing_account_transfers_nothing(self):
        veteran = _make_user(username="veteran", email="veteran@example.com", verified=True)
        SparkPool.objects.create(user=veteran, sparks=40, token_balance=0, is_active=True)

        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        resp = self.signin("veteran")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SparkPool.objects.get(user=veteran).sparks, 40)
        self.assertFalse(
            SparkTransaction.objects.filter(
                user=veteran,
                source=SparkTransaction.SourceChoices.ADJUSTMENT,
            ).exists()
        )
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    def test_the_guest_session_survives_login_and_logout_and_is_recoverable(self):
        _make_user(username="veteran", email="veteran@example.com", verified=True)
        credential = self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        signin = self.signin("veteran")
        access_token = signin.json()["data"]["access_token"]
        # Nothing was claimed, so the credential must not be retired.
        self.assertNotIn(COOKIE, signin.cookies)

        signout = self.client.post(
            SIGNOUT_URL,
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(signout.status_code, 200)
        self.assertNotIn(COOKIE, signout.cookies)

        self.assertGuestUntouched(identity, conversation, charge, purchase)

        # The visitor comes back anonymously with the same credential and finds
        # the same guest session, not a new one.
        returning = Client()
        returning.cookies[COOKIE] = credential
        resp = returning.post(ENSURE_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {"guest": True, "created": False})
        self.assertEqual(GuestIdentity.objects.count(), 1)

    def test_staff_login_transfers_nothing(self):
        _make_user(username="staffer", email="staffer@example.com", verified=True, is_staff=True)
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        resp = self.signin("staffer")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    def test_superuser_login_transfers_nothing(self):
        admin = _make_user(username="root", email="root@example.com", verified=True, is_staff=True)
        admin.is_superuser = True
        admin.save(update_fields=["is_superuser"])

        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        resp = self.signin("root")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    @override_settings(GOOGLE_WEB_CLIENT_ID="test-client-id")
    def test_google_login_transfers_nothing(self):
        """
        Google auth never creates an account — it refuses with
        ``google_signup_not_allowed`` — so every success on that path is a login
        to a pre-existing account and must transfer nothing.
        """
        user = _make_user(username="veteran", email="veteran@example.com", verified=True)
        # Existing Google binding: silent email-match linking is now forbidden.
        user.google_sub = "google-sub-1"
        user.save(update_fields=["google_sub"])
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        with mock.patch(
            "capp.classes.auth.auth.id_token.verify_oauth2_token",
            return_value={"sub": "google-sub-1", "email": "veteran@example.com", "email_verified": True},
        ):
            resp = self.client.post(
                GOOGLE_URL,
                data={"credential": "stub-google-credential"},
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    @override_settings(GOOGLE_WEB_CLIENT_ID="test-client-id")
    def test_google_login_cannot_be_used_to_sign_up_and_so_earns_no_grant(self):
        self.ensure_session()

        with mock.patch(
            "capp.classes.auth.auth.id_token.verify_oauth2_token",
            return_value={"sub": "google-sub-2", "email": "stranger@example.com", "email_verified": True},
        ):
            resp = self.client.post(
                GOOGLE_URL,
                data={"credential": "stub-google-credential"},
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "google_signup_not_allowed")
        self.assertFalse(GuestTransferGrant.objects.exists())


class WrongPairingTests(TransferFlowMixin, TestCase):
    """A grant names one user and one identity, and both halves are checked."""

    def test_a_different_account_cannot_spend_someone_elses_grant(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        self.signup(username="newbie")
        owner = CustomUser.objects.get(username="newbie")
        self.verify(owner.user_id)
        intruder = _make_user(username="intruder", email="intruder@example.com", verified=True)

        result = GuestClaimService.claim(intruder, identity)

        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "no_transfer_grant")
        self.assertGuestUntouched(identity, conversation, charge, purchase)
        # The rightful owner's entitlement is untouched and still spendable.
        self.assertEqual(
            GuestTransferGrant.objects.get(user=owner).status,
            GuestTransferGrant.Status.PENDING,
        )

    def test_a_stolen_cookie_presented_with_another_account_transfers_nothing(self):
        """
        The changed-browser attack. The attacker has the victim's guest
        credential — the only thing the old code required — and an account of
        their own, and still gets nothing.
        """
        victim = Client()
        victim_credential = self.ensure_session(victim)
        victim_identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(victim_identity)

        _make_user(username="attacker", email="attacker@example.com", verified=True)
        attacker = Client()
        attacker.cookies[COOKIE] = victim_credential

        resp = self.signin("attacker", client=attacker)

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(victim_identity, conversation, charge, purchase)
        self.assertFalse(SparkPool.objects.filter(sparks__gt=0).exists())

    def test_an_account_that_signed_up_from_its_own_guest_cannot_claim_another(self):
        """
        The attacker does everything right for their own guest session and then
        presents the victim's cookie, so a grant *does* exist for them — just
        not for this identity.
        """
        victim = Client()
        victim_credential = self.ensure_session(victim)
        victim_identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(victim_identity)

        attacker = Client()
        self.ensure_session(attacker)
        attacker_identity = GuestIdentity.objects.exclude(pk=victim_identity.pk).get()
        self.signup(client=attacker, username="attacker")
        attacker_user = CustomUser.objects.get(username="attacker")
        self.verify(attacker_user.user_id, client=attacker)

        self.assertEqual(
            GuestTransferGrant.objects.get(user=attacker_user).guest_identity_id,
            attacker_identity.pk,
        )

        attacker.cookies[COOKIE] = victim_credential
        resp = self.signin("attacker", client=attacker)

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(victim_identity, conversation, charge, purchase)
        # Only the signup grant; not a Spark of the victim's came across.
        self.assertEqual(SparkPool.objects.get(user=attacker_user).sparks, SIGNUP_SPARKS)
        self.assertFalse(
            SparkTransaction.objects.filter(
                user=attacker_user,
                source=SparkTransaction.SourceChoices.ADJUSTMENT,
            ).exists()
        )

    def test_a_second_signup_cannot_take_a_grant_from_a_verified_account(self):
        """
        An abandoned signup's grant is reassignable — see
        ``AbandonedSignupRetryTests`` — but only while it is abandoned. Once the
        holder has proved control of its email address the entitlement is theirs,
        whether or not they have spent it yet.
        """
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        self.seed_guest_state(identity)

        first = self.signup(username="firstborn")
        firstborn = CustomUser.objects.get(username="firstborn")
        self.assertEqual(self.verify(firstborn.user_id).status_code, 200)

        second = self.signup(username="secondborn")

        self.assertIn(first.status_code, (200, 201))
        # The refusal is not an error: the second account is created, it simply
        # has no claim on the guest session.
        self.assertIn(second.status_code, (200, 201))

        grants = GuestTransferGrant.objects.filter(guest_identity=identity)
        self.assertEqual(grants.count(), 1)
        self.assertEqual(grants.get().user.username, "firstborn")
        self.assertIsNone(grants.get().previous_user_id)
        self.assertFalse(GuestTransferGrant.objects.filter(user__username="secondborn").exists())

    def test_the_second_account_transfers_nothing_when_the_first_is_verified(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)

        self.signup(username="firstborn")
        firstborn = CustomUser.objects.get(username="firstborn")
        self.verify(firstborn.user_id)

        self.signup(username="secondborn")
        second = CustomUser.objects.get(username="secondborn")
        self.verify(second.user_id)

        resp = self.signin("secondborn")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    def test_one_account_can_never_accumulate_entitlements(self):
        user = _make_user(username="greedy", email="greedy@example.com", verified=True)
        first, _ = GuestIdentityService.mint()
        second, _ = GuestIdentityService.mint()

        self.assertIsNotNone(GuestTransferGrantService.grant_at_signup(user, first))
        self.assertIsNone(GuestTransferGrantService.grant_at_signup(user, second))

        self.assertEqual(GuestTransferGrant.objects.filter(user=user).count(), 1)

    def test_a_refused_grant_does_not_poison_the_signup_transaction(self):
        """
        ``grant_at_signup`` runs inside the account's own transaction, so the
        ``IntegrityError`` from a losing race has to be absorbed by a savepoint
        or the account itself would be rolled back.
        """
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        GuestTransferGrantService.grant_at_signup(
            _make_user(username="firstborn", email="firstborn@example.com", verified=True),
            identity,
        )

        resp = self.signup(username="secondborn")

        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(CustomUser.objects.filter(username="secondborn").exists())


class NonSignupPathTests(TransferFlowMixin, TestCase):
    """A pending grant is not a trigger. Only a verified login spends one."""

    def _pending_guest(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        rows = self.seed_guest_state(identity)
        self.signup(username="newbie")
        user = CustomUser.objects.get(username="newbie")
        self.verify(user.user_id)
        return identity, user, rows

    def test_refreshing_an_access_token_never_transfers(self):
        veteran = _make_user(username="veteran", email="veteran@example.com", verified=True)
        self.signin("veteran")
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)
        GuestTransferGrantService.grant_at_signup(veteran, identity)

        resp = self.client.post(REFRESH_URL, data={}, content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)
        self.assertEqual(
            GuestTransferGrant.objects.get(user=veteran).status,
            GuestTransferGrant.Status.PENDING,
        )

    def test_bootstrapping_a_guest_session_never_transfers(self):
        identity, _user, (conversation, charge, purchase) = self._pending_guest()

        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    def test_verifying_the_email_does_not_itself_transfer(self):
        """
        Verification proves the address; it does not prove possession of the
        browser holding the guest credential, which arrives with the login.
        """
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        conversation, charge, purchase = self.seed_guest_state(identity)
        self.signup(username="newbie")
        user = CustomUser.objects.get(username="newbie")

        self.assertEqual(self.verify(user.user_id).status_code, 200)

        self.assertGuestUntouched(identity, conversation, charge, purchase)

    def test_no_endpoint_performs_a_transfer_on_demand(self):
        """
        There is deliberately no callable transfer endpoint, so there is nothing
        to abuse by replaying or by supplying someone else's identifier. This
        fails loudly if one is ever added without revisiting the threat model.
        """

        def routes(patterns, prefix=""):
            for entry in patterns:
                route = prefix + str(entry.pattern)
                if hasattr(entry, "url_patterns"):
                    yield from routes(entry.url_patterns, route)
                else:
                    yield route

        offenders = [
            route
            for route in routes(get_resolver().url_patterns)
            if "claim" in route.lower() or "transfer" in route.lower()
        ]
        self.assertEqual(offenders, [])


class LegacyMigrationBindingTests(TransferFlowMixin, TestCase):
    """
    Exchanging a legacy localStorage ID mints an identity. It must not also
    confer eligibility, or the migration endpoint would be a way to hand any
    account a guest session it never signed up from.
    """

    def _legacy_guest(self, legacy_id=None):
        legacy_id = legacy_id or str(uuid.uuid4())
        conversation = Conversation.objects.create(character=self.character, guest_session_id=legacy_id)
        GuestTokenQuota.objects.create(session_id=legacy_id, sparks=250)
        charge = UsageCharge.objects.create(
            guest_session_id=legacy_id,
            charge_source=UsageCharge.ChargeSource.SPARK_POOL,
            sparks_charged=7,
        )
        purchase = PaystackTransaction.objects.create(
            guest_session_id=legacy_id,
            reference=f"ref-legacy-{uuid.uuid4().hex[:8]}",
            amount="100.00",
            status=PaystackTransaction.StatusChoices.SUCCESS,
        )
        return legacy_id, (conversation, charge, purchase)

    def _migrate(self, legacy_id, client=None):
        client = client or self.client
        return client.post(
            MIGRATE_URL,
            data={"legacy_session_id": legacy_id},
            content_type="application/json",
        )

    @override_settings(**MIGRATION_OPEN)
    def test_migrating_a_legacy_session_confers_no_eligibility(self):
        legacy_id, _rows = self._legacy_guest()

        resp = self._migrate(legacy_id)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(GuestIdentity.objects.filter(guest_id=legacy_id).exists())
        self.assertFalse(GuestTransferGrant.objects.exists())

    @override_settings(**MIGRATION_OPEN)
    def test_a_migrated_legacy_session_is_not_claimable_by_a_pre_existing_account(self):
        _make_user(username="veteran", email="veteran@example.com", verified=True)
        legacy_id, (conversation, charge, purchase) = self._legacy_guest()
        self._migrate(legacy_id)
        identity = GuestIdentity.objects.get(guest_id=legacy_id)

        resp = self.signin("veteran")

        self.assertEqual(resp.status_code, 200)
        self.assertGuestUntouched(identity, conversation, charge, purchase)

    @override_settings(**MIGRATION_OPEN)
    def test_a_migrated_legacy_session_still_converts_through_signup(self):
        """Migration must not break the legitimate route, only the shortcut."""
        legacy_id, (conversation, _charge, _purchase) = self._legacy_guest()
        self._migrate(legacy_id)
        identity = GuestIdentity.objects.get(guest_id=legacy_id)

        self.signup(username="newbie")
        user = CustomUser.objects.get(username="newbie")
        self.assertEqual(GuestTransferGrant.objects.get(user=user).guest_identity_id, identity.pk)

        self.verify(user.user_id)
        self.signin("newbie")

        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, user.pk)
        self.assertEqual(SparkPool.objects.get(user=user).sparks, SIGNUP_SPARKS + 250)
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)


def run_in_threads(target, count):
    """
    Run ``target(index)`` in ``count`` threads released simultaneously.

    A barrier is used rather than bare thread starts so the threads actually
    overlap inside the critical section; without it the first can finish before
    the second begins and the race never happens. Every thread closes its
    connection in a ``finally``; a leaked connection holding a row lock
    deadlocks the whole suite rather than failing one test.
    """
    barrier = threading.Barrier(count)
    results = [None] * count

    def wrapped(index):
        try:
            barrier.wait(timeout=30)
            results[index] = ("ok", target(index))
        except Exception as exc:  # noqa: BLE001 - recorded and asserted on
            results[index] = ("error", exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for thread in threads:
        assert not thread.is_alive(), "thread did not finish; likely a deadlock"

    return results


class GuestTransferBindingConcurrencyTests(TransactionTestCase):
    """
    The races, against a real PostgreSQL server and real separate connections.

    What is being pinned down is that the grant is spent by exactly one caller
    however many arrive at once — the guarantee that keeps a retried or
    double-clicked login from crediting the Sparks twice.
    """

    def setUp(self):
        cache.clear()
        self.character = _make_character(name="Amina", slug="amina")
        self.user = _make_user(username="claimer", email="claimer@example.com", verified=True)
        self.identity, self.credential = GuestIdentityService.mint()
        Conversation.objects.create(character=self.character, guest_session_id=self.identity.owner_key)
        GuestTokenQuota.objects.create(session_id=self.identity.owner_key, sparks=250)

    def _adjustments(self):
        return SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT)

    def test_concurrent_claims_of_one_grant_credit_the_sparks_once(self):
        GuestTransferGrantService.grant_at_signup(self.user, self.identity)

        results = run_in_threads(lambda _i: GuestClaimService.claim(self.user, self.identity), 6)

        statuses = [status for status, _ in results]
        self.assertEqual(statuses, ["ok"] * 6, [payload for _s, payload in results])

        claimed = [payload for _s, payload in results if payload["claimed"]]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["conversations_claimed"], 1)
        self.assertEqual(claimed[0]["sparks_transferred"], 250)

        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)
        self.assertEqual(self._adjustments().count(), 1)
        self.assertEqual(GuestTokenQuota.objects.get(session_id=self.identity.owner_key).sparks, 0)
        self.assertEqual(
            GuestTransferGrant.objects.get(user=self.user).status,
            GuestTransferGrant.Status.CONSUMED,
        )

    def test_concurrent_logins_of_the_entitled_account_credit_the_sparks_once(self):
        """The same race as it actually arrives: several logins at once."""
        GuestTransferGrantService.grant_at_signup(self.user, self.identity)

        def login(_index):
            client = Client()
            client.cookies[COOKIE] = self.credential
            with mock.patch(
                "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
                return_value=RECAPTCHA_OK,
            ):
                resp = client.post(
                    SIGNIN_URL,
                    data={"username": "claimer", "password": "pw123456"},
                    content_type="application/json",
                )
            return resp.status_code

        results = run_in_threads(login, 4)

        self.assertEqual([status for status, _ in results], ["ok"] * 4)
        # Throttling may reject some of them; what matters is that no login
        # errored and the credit happened at most once.
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)
        self.assertEqual(self._adjustments().count(), 1)

    def test_only_the_entitled_account_wins_a_race_with_an_unentitled_one(self):
        GuestTransferGrantService.grant_at_signup(self.user, self.identity)
        intruder = _make_user(username="intruder", email="intruder@example.com", verified=True)
        contenders = [self.user, intruder]

        results = run_in_threads(
            lambda i: GuestClaimService.claim(contenders[i % 2], self.identity),
            6,
        )

        self.assertEqual([status for status, _ in results], ["ok"] * 6)
        claimed = [payload for _s, payload in results if payload["claimed"]]
        self.assertEqual(len(claimed), 1)

        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)
        self.assertFalse(SparkPool.objects.filter(user=intruder, sparks__gt=0).exists())
        self.assertEqual(self.identity.__class__.objects.get(pk=self.identity.pk).claimed_by_id, self.user.pk)

    def test_concurrent_signups_from_one_guest_session_yield_one_grant(self):
        users = [_make_user(username=f"twin{i}", email=f"twin{i}@example.com", verified=True) for i in range(6)]

        results = run_in_threads(lambda i: GuestTransferGrantService.grant_at_signup(users[i], self.identity), 6)

        self.assertEqual([status for status, _ in results], ["ok"] * 6)
        granted = [payload for _s, payload in results if payload is not None]
        self.assertEqual(len(granted), 1)
        self.assertEqual(GuestTransferGrant.objects.filter(guest_identity=self.identity).count(), 1)

    def test_concurrent_reassignments_of_an_abandoned_grant_resolve_to_one_owner(self):
        """
        Several retries from one guest session at once. The identity row lock
        serialises them, so however many run the guest session ends up owed to
        exactly one account and every loser holds nothing.
        """
        abandoned = _make_user(username="abandoned", email="abandoned@example.com", verified=False)
        GuestTransferGrantService.grant_at_signup(abandoned, self.identity)
        retries = [_make_user(username=f"retry{i}", email=f"retry{i}@example.com", verified=False) for i in range(6)]

        results = run_in_threads(lambda i: GuestTransferGrantService.grant_at_signup(retries[i], self.identity), 6)

        self.assertEqual([status for status, _ in results], ["ok"] * 6, [payload for _s, payload in results])
        self.assertEqual(GuestTransferGrant.objects.count(), 1)
        grant = GuestTransferGrant.objects.get()
        self.assertEqual(grant.guest_identity_id, self.identity.pk)
        self.assertIn(grant.user_id, [retry.pk for retry in retries])
        self.assertEqual(grant.status, GuestTransferGrant.Status.PENDING)
        self.assertIsNotNone(grant.reassigned_at)
        self.assertFalse(GuestTransferGrant.objects.filter(user=abandoned).exists())

    def test_a_verified_holders_grant_survives_concurrent_signups(self):
        GuestTransferGrantService.grant_at_signup(self.user, self.identity)
        newcomers = [
            _make_user(username=f"newcomer{i}", email=f"newcomer{i}@example.com", verified=False) for i in range(6)
        ]

        results = run_in_threads(lambda i: GuestTransferGrantService.grant_at_signup(newcomers[i], self.identity), 6)

        self.assertEqual([status for status, _ in results], ["ok"] * 6)
        self.assertEqual([payload for _s, payload in results], [None] * 6)
        grant = GuestTransferGrant.objects.get()
        self.assertEqual(grant.user_id, self.user.pk)
        self.assertIsNone(grant.previous_user_id)

    def test_a_claim_and_a_concurrent_signup_from_the_same_guest_do_not_deadlock(self):
        """
        Both paths take the identity lock and then the grant lock. If either one
        reversed that order these two threads would deadlock instead of queueing,
        and PostgreSQL would abort one of them.
        """
        GuestTransferGrantService.grant_at_signup(self.user, self.identity)
        newcomer = _make_user(username="newcomer", email="newcomer@example.com", verified=False)

        def contend(index):
            if index == 0:
                return GuestClaimService.claim(self.user, self.identity)
            return GuestTransferGrantService.grant_at_signup(newcomer, self.identity)

        results = run_in_threads(contend, 2)

        self.assertEqual([status for status, _ in results], ["ok"] * 2, [payload for _s, payload in results])
        # The holder is verified, so the outcome is not merely deadlock-free but
        # decided: the claim wins whichever thread reaches the lock first.
        self.assertTrue(results[0][1]["claimed"])
        self.assertIsNone(results[1][1])
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)
        self.assertEqual(self._adjustments().count(), 1)

    def test_concurrent_signup_requests_from_one_guest_session_yield_one_grant(self):
        """The same race through the endpoint, so the savepoint is exercised too."""

        def register(index):
            client = Client()
            client.cookies[COOKIE] = self.credential
            with mock.patch(
                "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
                return_value=RECAPTCHA_OK,
            ):
                resp = client.post(
                    SIGNUP_URL,
                    data=signup_payload(f"racer{index}"),
                    content_type="application/json",
                )
            return resp.status_code

        results = run_in_threads(register, 4)

        self.assertEqual([status for status, _ in results], ["ok"] * 4)
        created = [payload for _s, payload in results if payload in (200, 201)]
        # Every account that was created exists — a refused grant must never
        # take the account down with it.
        self.assertEqual(CustomUser.objects.filter(username__startswith="racer").count(), len(created))
        self.assertLessEqual(GuestTransferGrant.objects.filter(guest_identity=self.identity).count(), 1)
