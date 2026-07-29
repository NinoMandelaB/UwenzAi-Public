# capp/tests_guest_identity.py
"""
Coverage for the server-minted anonymous guest identity, its transfer to a
verified account, the one-time legacy migration and the WebSocket ticket.

The vulnerability this suite exists to keep closed: guest ownership used to be
proved by a client-generated UUID that the browser kept in localStorage and sent
in request bodies, query strings and WebSocket URLs. Anyone who learned one owned
that guest's conversations, sparks and purchase history, and signup accepted an
arbitrary ``session_id`` so the takeover needed no more than a POST.
"""

import uuid
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_identity_service import (
    GuestIdentityService,
    hash_credential,
)
from capp.classes.guest.ws_ticket_service import (
    GuestWebSocketTicketService,
    hash_ticket,
)
from capp.models import (
    Conversation,
    CustomUser,
    GuestIdentity,
    GuestTokenQuota,
    GuestTransferGrant,
    GuestWebSocketTicket,
    Message,
    SparkPool,
    SparkTransaction,
    UsageCharge,
)
from capp.tests import RECAPTCHA_OK, _make_character, _make_user
from paystack_app.models import PaystackTransaction

COOKIE = settings.GUEST_CREDENTIAL_COOKIE_NAME

ENSURE_URL = "/api/v1/guest/session/"
MIGRATE_URL = "/api/v1/guest/session/migrate/"
TICKET_URL = "/api/v1/guest/ws-ticket/"
CONVERSATIONS_URL = "/api/v1/conversations/"

# The migration endpoint is fail-closed, so every test that exercises it has to
# open the window explicitly.
MIGRATION_OPEN = {
    "GUEST_LEGACY_MIGRATION_ENABLED": True,
    "GUEST_LEGACY_MIGRATION_CUTOFF": "2099-01-01",
}

_UNSET = object()


class GuestTestMixin:
    def setUp(self):
        # Throttles share the process cache; reset so tests do not starve each other.
        cache.clear()

    def ensure_session(self, client=None):
        """Bootstrap a guest on ``client`` and return the raw credential."""
        client = client or self.client
        resp = client.post(ENSURE_URL, data={}, content_type="application/json")
        assert resp.status_code in (200, 201), resp.status_code
        return client.cookies[COOKIE].value

    def grant(self, user, identity):
        """
        Stand in for the grant ``api_signup_user`` writes.

        Tests that build their account with ``_make_user`` rather than by posting
        to the signup endpoint have no grant, and without one nothing is
        transferable. Calling this asserts "this account came from this guest
        session" so the test can go on to exercise the transfer itself.
        """
        return GuestTransferGrant.objects.create(
            user=user,
            guest_identity=identity,
            status=GuestTransferGrant.Status.PENDING,
            origin_guest_session_id=identity.owner_key,
        )

    def signin(self, username, password="pw123456", client=None):
        client = client or self.client
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            return client.post(
                "/api/v1/auth/signin/",
                data={"username": username, "password": password},
                content_type="application/json",
            )


class GuestCredentialCookieTests(GuestTestMixin, TestCase):
    """The cookie is the only thing that proves guest ownership."""

    def test_bootstrap_mints_identity_and_sets_httponly_cookie(self):
        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["data"], {"guest": True, "created": True})

        morsel = resp.cookies[COOKIE]
        self.assertTrue(morsel["httponly"])
        self.assertEqual(morsel["path"], settings.GUEST_CREDENTIAL_COOKIE_PATH)
        self.assertEqual(morsel["samesite"], settings.GUEST_CREDENTIAL_COOKIE_SAMESITE)
        self.assertEqual(morsel["max-age"], settings.GUEST_CREDENTIAL_COOKIE_AGE)
        self.assertEqual(bool(morsel["secure"]), settings.GUEST_CREDENTIAL_COOKIE_SECURE)

    @override_settings(GUEST_CREDENTIAL_COOKIE_SECURE=True)
    def test_cookie_is_secure_when_configured(self):
        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")
        self.assertTrue(resp.cookies[COOKIE]["secure"])

    def test_credential_is_high_entropy_and_only_stored_as_a_hash(self):
        raw = self.ensure_session()

        self.assertGreaterEqual(len(raw), 40)
        identity = GuestIdentity.objects.get()
        self.assertEqual(identity.credential_hash, hash_credential(raw))
        # The raw credential must exist nowhere in the row.
        self.assertNotIn(raw, str(identity.__dict__))

    def test_response_never_reveals_the_stable_ownership_id(self):
        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")
        identity = GuestIdentity.objects.get()
        self.assertNotIn(str(identity.guest_id), resp.content.decode())

    def test_reload_reuses_the_same_identity(self):
        raw = self.ensure_session()

        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {"guest": True, "created": False})
        # No new cookie is issued, so the browser keeps the one it already stored.
        self.assertNotIn(COOKIE, resp.cookies)
        self.assertEqual(self.client.cookies[COOKIE].value, raw)
        self.assertEqual(GuestIdentity.objects.count(), 1)

    def test_identity_survives_a_browser_restart(self):
        """A restart drops nothing but session cookies; ours has a max-age."""
        raw = self.ensure_session()
        identity_id = GuestIdentity.objects.get().guest_id

        fresh_browser = Client()
        fresh_browser.cookies[COOKIE] = raw
        resp = fresh_browser.post(ENSURE_URL, data={}, content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["data"]["created"])
        self.assertEqual(GuestIdentity.objects.get().guest_id, identity_id)

    def test_last_seen_is_refreshed_and_expiry_extended(self):
        """A guest who keeps using the site keeps a rolling expiry."""
        raw = self.ensure_session()
        stale = timezone.now() - timedelta(days=30)
        nearly_expired = timezone.now() + timedelta(days=1)
        GuestIdentity.objects.update(last_seen_at=stale, expires_at=nearly_expired)

        self.client.post(ENSURE_URL, data={}, content_type="application/json")

        identity = GuestIdentity.objects.get()
        self.assertGreater(identity.last_seen_at, stale)
        self.assertGreater(identity.expires_at, nearly_expired)
        self.assertEqual(identity.credential_hash, hash_credential(raw))

    def test_rotation_keeps_ownership_and_remembers_the_previous_hash(self):
        raw = self.ensure_session()
        identity = GuestIdentity.objects.get()
        owner_key = identity.owner_key

        rotated = GuestIdentityService.rotate_credential(identity)

        self.assertNotEqual(rotated, raw)
        identity.refresh_from_db()
        self.assertEqual(identity.owner_key, owner_key)
        self.assertEqual(identity.credential_hash, hash_credential(rotated))
        self.assertEqual(identity.previous_credential_hash, hash_credential(raw))
        self.assertEqual(identity.rotation_count, 1)
        self.assertIsNotNone(identity.rotated_at)

    def test_superseded_credential_no_longer_authenticates(self):
        raw = self.ensure_session()
        GuestIdentityService.rotate_credential(GuestIdentity.objects.get())

        stale_browser = Client()
        stale_browser.cookies[COOKIE] = raw
        resp = stale_browser.get(CONVERSATIONS_URL)

        self.assertEqual(resp.status_code, 401)

    def test_authenticated_caller_gets_no_guest_identity(self):
        _make_user(username="signed", email="signed@example.com", verified=True)
        token = self.signin("signed").json()["data"]["access_token"]

        resp = Client().post(
            ENSURE_URL,
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {"guest": False})
        self.assertFalse(GuestIdentity.objects.exists())


class GuestApiAuthorizationTests(GuestTestMixin, TestCase):
    """Guest API paths must derive ownership from the cookie and nothing else."""

    def setUp(self):
        super().setUp()
        self.character = _make_character(name="Amina", slug="amina")

    def _guest_conversation(self, client):
        raw = self.ensure_session(client)
        identity = GuestIdentity.objects.get(credential_hash=hash_credential(raw))
        conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=identity.owner_key,
        )
        Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.USER,
            content="habari",
            status=Message.StatusChoices.SENT,
        )
        return identity, conversation

    def test_no_cookie_is_unauthorized(self):
        resp = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["data"], "guest_session_required")

    def test_unknown_credential_is_unauthorized(self):
        self.client.cookies[COOKIE] = "not-a-real-credential"
        self.assertEqual(self.client.get(CONVERSATIONS_URL).status_code, 401)

    def test_guest_sees_only_its_own_conversations(self):
        alice = Client()
        bob = Client()
        _, alice_conv = self._guest_conversation(alice)
        _, bob_conv = self._guest_conversation(bob)

        listed = alice.get(CONVERSATIONS_URL).json()["data"]["conversations"]
        ids = {item["conversation_id"] for item in listed}

        self.assertEqual(ids, {str(alice_conv.conversation_id)})
        self.assertNotIn(str(bob_conv.conversation_id), ids)

    def test_guest_cannot_reach_another_guest_by_naming_its_ownership_id(self):
        """The old attack: knowing the ID used to be enough to own the data."""
        victim = Client()
        victim_identity, victim_conv = self._guest_conversation(victim)

        attacker = Client()
        self.ensure_session(attacker)

        resp = attacker.delete(
            f"/api/v1/conversations/{victim_conv.conversation_id}/reset/",
            data={"session_id": victim_identity.owner_key, "guest_session_id": victim_identity.owner_key},
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 403)
        victim_conv.refresh_from_db()
        self.assertEqual(victim_conv.guest_session_id, victim_identity.owner_key)
        self.assertTrue(Message.objects.filter(conversation=victim_conv).exists())

    def test_expired_credential_is_rejected(self):
        self.ensure_session()
        GuestIdentity.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(CONVERSATIONS_URL).status_code, 401)

    def test_revoked_credential_is_rejected(self):
        self.ensure_session()
        GuestIdentityService.revoke(GuestIdentity.objects.get())
        self.assertEqual(self.client.get(CONVERSATIONS_URL).status_code, 401)

    def test_claimed_credential_is_rejected(self):
        self.ensure_session()
        user = _make_user(username="owner", email="owner@example.com", verified=True)
        GuestIdentity.objects.update(
            status=GuestIdentity.Status.CLAIMED,
            claimed_at=timezone.now(),
            claimed_by=user,
        )
        self.assertEqual(self.client.get(CONVERSATIONS_URL).status_code, 401)

    def test_bootstrap_replaces_a_claimed_credential_with_a_fresh_guest(self):
        self.ensure_session()
        user = _make_user(username="owner2", email="owner2@example.com", verified=True)
        claimed_id = GuestIdentity.objects.get().guest_id
        GuestIdentity.objects.update(
            status=GuestIdentity.Status.CLAIMED,
            claimed_at=timezone.now(),
            claimed_by=user,
        )

        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()["data"]["created"])
        fresh = GuestIdentity.objects.exclude(guest_id=claimed_id).get()
        self.assertEqual(fresh.status, GuestIdentity.Status.ACTIVE)


class GuestCookieCsrfTests(GuestTestMixin, TestCase):
    """
    Cookie-authenticated mutations need CSRF protection, and getting it right is
    not automatic: DRF marks ``api_view`` results ``csrf_exempt``, so a bare
    ``@csrf_protect`` above it is a silent no-op.
    """

    def setUp(self):
        super().setUp()
        self.strict = Client(enforce_csrf_checks=True)

    def _csrf_token(self):
        resp = self.strict.get("/api/v1/auth/csrf/")
        return resp.json()["data"]["csrfToken"]

    def test_guest_bootstrap_without_a_csrf_token_is_forbidden(self):
        resp = self.strict.post(ENSURE_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(GuestIdentity.objects.exists())

    def test_guest_bootstrap_with_a_csrf_token_succeeds(self):
        token = self._csrf_token()
        resp = self.strict.post(
            ENSURE_URL,
            data={},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resp.status_code, 201)

    def test_bearer_authenticated_callers_need_no_csrf_token(self):
        """Mobile/API clients hold no CSRF cookie, and a header is not ambient."""
        _make_user(username="bearer", email="bearer@example.com", verified=True)
        token = self.signin("bearer").json()["data"]["access_token"]

        resp = Client(enforce_csrf_checks=True).post(
            "/api/v1/auth/signout/",
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertNotEqual(resp.status_code, 403)


class SignupOwnershipClaimTests(GuestTestMixin, TestCase):
    """Signup must not accept an ownership claim from anybody."""

    SIGNUP_PAYLOAD = {
        "username": "newbie",
        "email": "newbie@example.com",
        "password": "pw123456",
        "confirm_password": "pw123456",
        "age_confirmed": True,
        "terms_accepted": True,
        "privacy_accepted": True,
    }

    @mock.patch(
        "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
        return_value=RECAPTCHA_OK,
    )
    def test_signup_ignores_an_arbitrary_session_id_in_the_body(self, _recaptcha):
        victim = Client()
        self.ensure_session(victim)
        victim_identity = GuestIdentity.objects.get()
        character = _make_character(name="Amina", slug="amina")
        conversation = Conversation.objects.create(
            character=character,
            guest_session_id=victim_identity.owner_key,
        )
        GuestTokenQuota.objects.create(session_id=victim_identity.owner_key, sparks=500)

        payload = dict(self.SIGNUP_PAYLOAD, session_id=victim_identity.owner_key)
        resp = Client().post("/api/v1/auth/signup/", data=payload, content_type="application/json")

        self.assertIn(resp.status_code, (200, 201))
        attacker = CustomUser.objects.get(username="newbie")
        conversation.refresh_from_db()
        self.assertIsNone(conversation.user_id)
        self.assertEqual(conversation.guest_session_id, victim_identity.owner_key)
        victim_identity.refresh_from_db()
        self.assertEqual(victim_identity.status, GuestIdentity.Status.ACTIVE)
        self.assertEqual(GuestTokenQuota.objects.get().sparks, 500)
        self.assertFalse(
            SparkTransaction.objects.filter(
                user=attacker,
                source=SparkTransaction.SourceChoices.ADJUSTMENT,
            ).exists()
        )

    @mock.patch(
        "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
        return_value=RECAPTCHA_OK,
    )
    def test_signup_does_not_claim_the_callers_own_guest_session(self, _recaptcha):
        """Signup cannot prove control of the email yet, so it transfers nothing."""
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        character = _make_character(name="Amina", slug="amina")
        Conversation.objects.create(character=character, guest_session_id=identity.owner_key)

        resp = self.client.post(
            "/api/v1/auth/signup/",
            data=self.SIGNUP_PAYLOAD,
            content_type="application/json",
        )

        self.assertIn(resp.status_code, (200, 201))
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.ACTIVE)
        self.assertEqual(
            Conversation.objects.filter(guest_session_id=identity.owner_key, user__isnull=True).count(),
            1,
        )


class GuestClaimOnLoginTests(GuestTestMixin, TestCase):
    """
    The transfer itself: all-or-nothing, once, on the first verified login of the
    account the guest session was signed up into.

    Every test here is about an account that *is* entitled, so ``_seed_guest``
    writes the grant signup would have written. The tests that an *unentitled*
    account transfers nothing live in ``capp/tests_guest_transfer_binding.py``.
    """

    def setUp(self):
        super().setUp()
        self.character = _make_character(name="Amina", slug="amina")
        self.user = _make_user(username="claimer", email="claimer@example.com", verified=True)

    def _seed_guest(self, client=None, sparks=250, entitled=True):
        client = client or self.client
        self.ensure_session(client)
        identity = GuestIdentity.objects.get()
        owner_key = identity.owner_key

        if entitled:
            self.grant(self.user, identity)

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
            reference="ref-guest-1",
            amount="100.00",
            status=PaystackTransaction.StatusChoices.SUCCESS,
        )
        return identity, conversation, charge, purchase

    def test_first_verified_login_transfers_everything_atomically(self):
        identity, conversation, charge, purchase = self._seed_guest()
        owner_key = identity.owner_key

        resp = self.signin("claimer")
        self.assertEqual(resp.status_code, 200)

        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, self.user.pk)
        self.assertIsNone(conversation.guest_session_id)

        charge.refresh_from_db()
        self.assertEqual(charge.user_id, self.user.pk)

        purchase.refresh_from_db()
        self.assertEqual(purchase.user_id, self.user.pk)

        pool = SparkPool.objects.get(user=self.user)
        self.assertEqual(pool.sparks, 250)

        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)
        self.assertEqual(identity.claimed_by_id, self.user.pk)
        self.assertIsNotNone(identity.claimed_at)
        self.assertEqual(owner_key, identity.owner_key)

    def test_provenance_is_retained_and_nothing_is_deleted(self):
        identity, conversation, charge, purchase = self._seed_guest()
        owner_key = identity.owner_key

        self.signin("claimer")

        for row in (conversation, charge, purchase):
            row.refresh_from_db()
            self.assertEqual(row.origin_guest_session_id, owner_key)
            self.assertIsNotNone(row.claimed_from_guest_at)

        # The guest quota row survives with its usage counters intact; only the
        # spendable balance moves, and it moves through the ledger.
        quota = GuestTokenQuota.objects.get(session_id=owner_key)
        self.assertEqual(quota.sparks, 0)
        self.assertEqual(quota.lifetime_tokens_used, 4321)
        self.assertEqual(quota.claimed_by_id, self.user.pk)
        self.assertIsNotNone(quota.claimed_at)

        entry = SparkTransaction.objects.get(user=self.user, source=SparkTransaction.SourceChoices.ADJUSTMENT)
        self.assertEqual(entry.spark_amount, 250)
        self.assertEqual(entry.sparks_after, 250)
        self.assertEqual(entry.metadata["origin_guest_session_id"], owner_key)

    def test_sparks_are_merged_into_an_existing_balance(self):
        SparkPool.objects.create(user=self.user, sparks=40, token_balance=0, is_active=True)
        self._seed_guest(sparks=60)

        self.signin("claimer")

        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 100)

    def test_cookie_is_cleared_only_after_the_transfer_commits(self):
        self._seed_guest()

        resp = self.signin("claimer")

        self.assertIn(COOKIE, resp.cookies)
        self.assertEqual(resp.cookies[COOKIE].value, "")
        self.assertEqual(self.client.cookies[COOKIE].value, "")

    def test_failed_transfer_rolls_back_and_keeps_the_guest_intact(self):
        identity, conversation, charge, purchase = self._seed_guest()

        with mock.patch.object(
            GuestClaimService,
            "_transfer_purchases",
            side_effect=RuntimeError("boom"),
        ):
            resp = self.signin("claimer")

        # Login still succeeds, so the guest can retry on the next attempt.
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(COOKIE, resp.cookies)

        # The grant is consumed inside the same transaction as the transfer, so
        # the rollback returns it to PENDING and the entitlement is not burnt.
        self.assertEqual(
            GuestTransferGrant.objects.get(user=self.user).status,
            GuestTransferGrant.Status.PENDING,
        )

        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.ACTIVE)
        conversation.refresh_from_db()
        self.assertIsNone(conversation.user_id)
        self.assertEqual(conversation.guest_session_id, identity.owner_key)
        charge.refresh_from_db()
        self.assertIsNone(charge.user_id)
        purchase.refresh_from_db()
        self.assertIsNone(purchase.user_id)
        self.assertEqual(GuestTokenQuota.objects.get().sparks, 250)
        self.assertFalse(SparkPool.objects.filter(user=self.user, sparks__gt=0).exists())

    def test_retry_after_a_failed_transfer_completes_the_claim(self):
        identity, conversation, _charge, _purchase = self._seed_guest()

        with mock.patch.object(
            GuestClaimService,
            "_transfer_purchases",
            side_effect=RuntimeError("boom"),
        ):
            self.signin("claimer")

        cache.clear()
        self.signin("claimer")

        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)
        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, self.user.pk)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)

    def test_replaying_a_spent_credential_on_login_transfers_nothing_twice(self):
        identity, _conversation, _charge, _purchase = self._seed_guest()
        self.signin("claimer")

        # The raw credential is unrecoverable from the hash, so a browser still
        # holding a working cookie for the claimed identity is simulated by
        # rotating and presenting the new value.
        replay = Client()
        replay.cookies[COOKIE] = GuestIdentityService.rotate_credential(identity)
        cache.clear()
        resp = self.signin("claimer", client=replay)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)
        self.assertEqual(
            SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT).count(),
            1,
        )

    def test_replaying_the_claim_transfers_nothing_a_second_time(self):
        identity, _conversation, _charge, _purchase = self._seed_guest()

        first = GuestClaimService.claim(self.user, identity)
        identity.refresh_from_db()
        second = GuestClaimService.claim(self.user, identity)

        self.assertTrue(first["claimed"])
        self.assertEqual(first["conversations_claimed"], 1)
        # The entitlement was spent by the first call, so the replay is not a
        # second success — it is a no-op, which is what stops the double credit.
        self.assertFalse(second["claimed"])
        self.assertEqual(second["reason"], "no_transfer_grant")
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 250)
        self.assertEqual(
            SparkTransaction.objects.filter(source=SparkTransaction.SourceChoices.ADJUSTMENT).count(),
            1,
        )

    def test_another_account_cannot_claim_an_already_claimed_guest(self):
        identity, conversation, _charge, _purchase = self._seed_guest()
        GuestClaimService.claim(self.user, identity)
        identity.refresh_from_db()

        intruder = _make_user(username="intruder", email="intruder@example.com", verified=True)
        result = GuestClaimService.claim(intruder, identity)

        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "no_transfer_grant")
        conversation.refresh_from_db()
        self.assertEqual(conversation.user_id, self.user.pk)

    def test_unverified_login_never_claims(self):
        identity, conversation, _charge, _purchase = self._seed_guest(entitled=False)
        pending = _make_user(username="pending", email="pending@example.com", verified=False)
        self.grant(pending, identity)

        resp = self.signin("pending")

        self.assertEqual(resp.status_code, 400)
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.ACTIVE)
        conversation.refresh_from_db()
        self.assertIsNone(conversation.user_id)

    def test_service_refuses_an_unverified_account(self):
        identity, _conversation, _charge, _purchase = self._seed_guest(entitled=False)
        pending = _make_user(username="pending2", email="pending2@example.com", verified=False)
        self.grant(pending, identity)

        result = GuestClaimService.claim(pending, identity)

        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "user_not_verified")
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.ACTIVE)

    def test_revoked_identity_is_not_claimable(self):
        identity, _conversation, _charge, _purchase = self._seed_guest()
        GuestIdentityService.revoke(identity)
        identity.refresh_from_db()

        result = GuestClaimService.claim(self.user, identity)

        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "guest_identity_revoked")

    def test_claim_spends_outstanding_websocket_tickets(self):
        identity, conversation, _charge, _purchase = self._seed_guest()
        issued = GuestWebSocketTicketService.issue(
            identity=identity,
            channel=GuestWebSocketTicket.Channel.CHAT,
            conversation_id=conversation.conversation_id,
        )
        raw_ticket = issued["data"]["ticket"]

        GuestClaimService.claim(self.user, identity)

        self.assertIsNotNone(GuestWebSocketTicket.objects.get().consumed_at)
        self.assertIsNone(
            GuestWebSocketTicketService.consume(
                raw_ticket,
                GuestWebSocketTicket.Channel.CHAT,
                conversation.conversation_id,
            )
        )

    def test_logout_does_not_resurrect_a_claimed_guest(self):
        identity, _conversation, _charge, _purchase = self._seed_guest()
        signin = self.signin("claimer")
        access_token = signin.json()["data"]["access_token"]
        # The claim at login is what retires the credential.
        self.assertEqual(signin.cookies[COOKIE].value, "")

        signout = self.client.post(
            "/api/v1/auth/signout/",
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(signout.status_code, 200)
        # Signout no longer touches the guest cookie — it must not, or a visitor
        # who logged into an unrelated account would lose their guest session on
        # the way out. It does not need to here: the identity is already CLAIMED,
        # so the credential resolves to nothing regardless.
        self.assertNotIn(COOKIE, signout.cookies)

        # Later anonymous use gets a brand-new guest, not the claimed one.
        resp = self.client.post(ENSURE_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        fresh = GuestIdentity.objects.exclude(guest_id=identity.guest_id).get()
        self.assertEqual(fresh.status, GuestIdentity.Status.ACTIVE)
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.CLAIMED)

    def test_login_with_a_dead_credential_clears_the_cookie_without_claiming(self):
        self.ensure_session()
        identity = GuestIdentity.objects.get()
        GuestIdentityService.revoke(identity)

        resp = self.signin("claimer")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.cookies[COOKIE].value, "")
        identity.refresh_from_db()
        self.assertEqual(identity.status, GuestIdentity.Status.REVOKED)

    def test_login_without_a_guest_cookie_leaves_the_account_untouched(self):
        resp = self.signin("claimer")

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(COOKIE, resp.cookies)
        self.assertFalse(GuestIdentity.objects.exists())


class LegacyGuestMigrationTests(GuestTestMixin, TestCase):
    """
    The one temporary compatibility risk in this change: legacy localStorage IDs
    were bearer credentials, so exactly one caller may ever exchange one.
    """

    def setUp(self):
        super().setUp()
        self.legacy_id = str(uuid.uuid4())

    def _migrate(self, client=None, legacy_id=_UNSET):
        client = client or self.client
        return client.post(
            MIGRATE_URL,
            data={"legacy_session_id": self.legacy_id if legacy_id is _UNSET else legacy_id},
            content_type="application/json",
        )

    @override_settings(**MIGRATION_OPEN)
    def test_migration_adopts_legacy_guest_data_and_rotates_immediately(self):
        character = _make_character(name="Amina", slug="amina")
        conversation = Conversation.objects.create(character=character, guest_session_id=self.legacy_id)
        Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.USER,
            content="habari",
            status=Message.StatusChoices.SENT,
        )
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=90)

        resp = self._migrate()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], {"migrated": True})

        identity = GuestIdentity.objects.get()
        self.assertEqual(identity.owner_key, self.legacy_id)
        self.assertEqual(identity.migrated_from_legacy_session_id, self.legacy_id)
        self.assertIsNotNone(identity.migrated_at)
        # Rotated on adoption, so the legacy ID is no longer a usable credential.
        self.assertEqual(identity.rotation_count, 1)
        self.assertEqual(identity.credential_hash, hash_credential(resp.cookies[COOKIE].value))
        self.assertNotEqual(resp.cookies[COOKIE].value, self.legacy_id)

        # The pre-existing guest data is reachable through the new credential.
        listed = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(listed.status_code, 200)
        self.assertNotEqual(listed.json()["data"]["conversations"], [])
        self.assertEqual(conversation.guest_session_id, self.legacy_id)

    @override_settings(**MIGRATION_OPEN)
    def test_the_legacy_id_itself_is_not_accepted_as_a_credential(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        self._migrate()

        impostor = Client()
        impostor.cookies[COOKIE] = self.legacy_id
        self.assertEqual(impostor.get(CONVERSATIONS_URL).status_code, 401)

    @override_settings(**MIGRATION_OPEN)
    def test_a_legacy_id_can_only_be_migrated_once(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        self.assertEqual(self._migrate().status_code, 200)

        second_browser = Client()
        resp = self._migrate(client=second_browser)

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["data"], "legacy_session_already_migrated")
        self.assertNotIn(COOKIE, resp.cookies)
        self.assertEqual(GuestIdentity.objects.count(), 1)

    @override_settings(**MIGRATION_OPEN)
    def test_caller_with_a_live_credential_cannot_migrate(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        self.ensure_session()

        resp = self._migrate()

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["data"], "guest_session_already_active")
        self.assertEqual(GuestIdentity.objects.count(), 1)

    @override_settings(**MIGRATION_OPEN)
    def test_unknown_legacy_id_is_not_adopted(self):
        resp = self._migrate()

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["data"], "legacy_session_not_found")
        self.assertFalse(GuestIdentity.objects.exists())

    @override_settings(**MIGRATION_OPEN)
    def test_already_claimed_legacy_data_is_not_adopted(self):
        user = _make_user(username="already", email="already@example.com", verified=True)
        GuestTokenQuota.objects.create(
            session_id=self.legacy_id,
            sparks=0,
            claimed_at=timezone.now(),
            claimed_by=user,
        )

        resp = self._migrate()

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["data"], "legacy_session_already_claimed")
        self.assertFalse(GuestIdentity.objects.exists())

    @override_settings(**MIGRATION_OPEN)
    def test_non_uuid_values_are_refused(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        hex_form = self.legacy_id.replace("-", "")

        rejected = (
            "",
            "   ",
            None,
            "not-a-uuid",
            hex_form,
            f"{{{self.legacy_id}}}",
            f"urn:uuid:{self.legacy_id}",
            "1 OR 1=1",
        )

        for bad in rejected:
            with self.subTest(value=bad):
                cache.clear()  # keep the tight migration throttle out of the way
                resp = self._migrate(client=Client(), legacy_id=bad)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["data"], "invalid_legacy_session_id")

        self.assertFalse(GuestIdentity.objects.exists())

    @override_settings(**MIGRATION_OPEN)
    def test_case_variants_of_one_legacy_id_cannot_yield_two_identities(self):
        """Normalisation matters: two spellings must not mean two owners."""
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)

        self.assertEqual(self._migrate().status_code, 200)
        resp = self._migrate(client=Client(), legacy_id=self.legacy_id.upper())

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(GuestIdentity.objects.count(), 1)

    @override_settings(**MIGRATION_OPEN)
    def test_authenticated_callers_cannot_migrate(self):
        _make_user(username="signedin", email="signedin@example.com", verified=True)
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        token = self.signin("signedin").json()["data"]["access_token"]

        resp = Client().post(
            MIGRATE_URL,
            data={"legacy_session_id": self.legacy_id},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "already_authenticated")
        self.assertFalse(GuestIdentity.objects.exists())

    @override_settings(GUEST_LEGACY_MIGRATION_ENABLED=False, GUEST_LEGACY_MIGRATION_CUTOFF="2099-01-01")
    def test_disabled_flag_closes_the_endpoint(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        resp = self._migrate()
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["data"], "legacy_migration_disabled")

    @override_settings(GUEST_LEGACY_MIGRATION_ENABLED=True, GUEST_LEGACY_MIGRATION_CUTOFF="")
    def test_missing_cutoff_closes_the_endpoint(self):
        """Fail closed: an unbounded window is exactly what must not happen."""
        resp = self._migrate()
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["data"], "legacy_migration_cutoff_not_configured")

    @override_settings(GUEST_LEGACY_MIGRATION_ENABLED=True, GUEST_LEGACY_MIGRATION_CUTOFF="not-a-date")
    def test_unparseable_cutoff_closes_the_endpoint(self):
        resp = self._migrate()
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["data"], "legacy_migration_cutoff_invalid")

    @override_settings(GUEST_LEGACY_MIGRATION_ENABLED=True, GUEST_LEGACY_MIGRATION_CUTOFF="2020-01-01")
    def test_past_cutoff_closes_the_endpoint(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        resp = self._migrate()
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["data"], "legacy_migration_expired")
        self.assertFalse(GuestIdentity.objects.exists())

    @override_settings(**MIGRATION_OPEN)
    def test_migration_is_rate_limited(self):
        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["guest_legacy_migration"]
        allowed = int(rate.split("/")[0])

        statuses = []
        for _ in range(allowed + 1):
            statuses.append(self._migrate(client=Client(), legacy_id=str(uuid.uuid4())).status_code)

        self.assertEqual(statuses[-1], 429)
        self.assertEqual(statuses.count(429), 1)

    @override_settings(**MIGRATION_OPEN)
    def test_legacy_id_is_not_echoed_back_to_the_caller(self):
        GuestTokenQuota.objects.create(session_id=self.legacy_id, sparks=5)
        resp = self._migrate()
        self.assertNotIn(self.legacy_id, resp.content.decode())


class GuestWebSocketTicketTests(GuestTestMixin, TestCase):
    """
    A WebSocket URL is recorded by every proxy it crosses, so the guest
    credential can never appear in one. Tickets are the substitute: seconds long,
    single use, and bound to one channel and conversation.
    """

    def setUp(self):
        super().setUp()
        self.character = _make_character(name="Amina", slug="amina")
        self.ensure_session()
        self.identity = GuestIdentity.objects.get()
        self.conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=self.identity.owner_key,
        )

    def _issue(self, channel="spark_pool", conversation_id=None, client=None):
        body = {"channel": channel}
        if conversation_id is not None:
            body["conversation_id"] = str(conversation_id)
        return (client or self.client).post(TICKET_URL, data=body, content_type="application/json")

    def test_ticket_requires_a_guest_cookie(self):
        resp = self._issue(client=Client())
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["data"], "guest_session_required")
        self.assertFalse(GuestWebSocketTicket.objects.exists())

    def test_ticket_is_issued_to_a_cookie_authenticated_guest(self):
        resp = self._issue()

        self.assertEqual(resp.status_code, 201)
        raw = resp.json()["data"]["ticket"]
        self.assertEqual(resp.json()["data"]["expires_in"], settings.GUEST_WS_TICKET_TTL_SECONDS)

        ticket = GuestWebSocketTicket.objects.get()
        self.assertEqual(ticket.guest_identity_id, self.identity.pk)
        self.assertEqual(ticket.channel, GuestWebSocketTicket.Channel.SPARK_POOL)
        self.assertIsNone(ticket.conversation_id)
        self.assertEqual(ticket.ticket_hash, hash_ticket(raw))
        self.assertNotIn(raw, str(ticket.__dict__))

    def test_ticket_ttl_is_short(self):
        self.assertLessEqual(settings.GUEST_WS_TICKET_TTL_SECONDS, 120)

    def test_response_never_contains_the_credential_or_ownership_id(self):
        body = self._issue().content.decode()
        self.assertNotIn(self.client.cookies[COOKIE].value, body)
        self.assertNotIn(str(self.identity.guest_id), body)

    def test_authenticated_clients_are_told_to_use_their_access_token(self):
        _make_user(username="wsuser", email="wsuser@example.com", verified=True)
        token = self.signin("wsuser", client=Client()).json()["data"]["access_token"]

        resp = Client().post(
            TICKET_URL,
            data={"channel": "spark_pool"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "authenticated_clients_use_access_token")

    def test_unknown_channel_is_refused(self):
        resp = self._issue(channel="admin_firehose")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_channel")

    def test_chat_channel_requires_a_conversation(self):
        resp = self._issue(channel="chat")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "conversation_id_required")

    def test_chat_ticket_is_bound_to_a_conversation_the_guest_owns(self):
        resp = self._issue(channel="chat", conversation_id=self.conversation.conversation_id)

        self.assertEqual(resp.status_code, 201)
        ticket = GuestWebSocketTicket.objects.get()
        self.assertEqual(ticket.conversation_id, self.conversation.conversation_id)

    def test_guest_cannot_get_a_ticket_for_another_guests_conversation(self):
        other = Client()
        self.ensure_session(other)
        other_identity = GuestIdentity.objects.exclude(pk=self.identity.pk).get()
        their_conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=other_identity.owner_key,
        )

        resp = self._issue(channel="chat", conversation_id=their_conversation.conversation_id)

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["data"], "conversation_not_found")
        self.assertFalse(GuestWebSocketTicket.objects.exists())

    def test_ticket_for_a_nonexistent_conversation_is_refused(self):
        resp = self._issue(channel="chat", conversation_id=uuid.uuid4())
        self.assertEqual(resp.status_code, 404)

    def test_consume_returns_the_identity_exactly_once(self):
        raw = self._issue().json()["data"]["ticket"]

        first = GuestWebSocketTicketService.consume(raw, GuestWebSocketTicket.Channel.SPARK_POOL)
        second = GuestWebSocketTicketService.consume(raw, GuestWebSocketTicket.Channel.SPARK_POOL)

        self.assertIsNotNone(first)
        self.assertEqual(first.pk, self.identity.pk)
        self.assertIsNone(second)
        self.assertIsNotNone(GuestWebSocketTicket.objects.get().consumed_at)

    def test_a_racing_connection_loses_on_the_conditional_update(self):
        """
        What makes a race safe is the conditional UPDATE, not the preceding read.
        Two connections can both read an unspent row; only one UPDATE can match
        it, and the loser must be turned away rather than trusted for having read
        a valid ticket a moment earlier.
        """
        raw = self._issue().json()["data"]["ticket"]
        ticket = GuestWebSocketTicket.objects.get()
        self.assertIsNotNone(GuestWebSocketTicketService.consume(raw, GuestWebSocketTicket.Channel.SPARK_POOL))

        loser_update = GuestWebSocketTicket.objects.filter(
            pk=ticket.pk,
            consumed_at__isnull=True,
        ).update(consumed_at=timezone.now())

        self.assertEqual(loser_update, 0)

    def test_ticket_bound_to_one_channel_cannot_be_used_on_another(self):
        raw = self._issue(channel="chat", conversation_id=self.conversation.conversation_id).json()["data"]["ticket"]

        self.assertIsNone(
            GuestWebSocketTicketService.consume(raw, GuestWebSocketTicket.Channel.SPARK_POOL),
        )
        self.assertIsNone(GuestWebSocketTicket.objects.get().consumed_at)

    def test_chat_ticket_cannot_be_used_on_a_different_conversation(self):
        raw = self._issue(channel="chat", conversation_id=self.conversation.conversation_id).json()["data"]["ticket"]
        elsewhere = Conversation.objects.create(
            character=self.character,
            guest_session_id=self.identity.owner_key,
        )

        self.assertIsNone(
            GuestWebSocketTicketService.consume(
                raw,
                GuestWebSocketTicket.Channel.CHAT,
                elsewhere.conversation_id,
            )
        )
        self.assertIsNotNone(
            GuestWebSocketTicketService.consume(
                raw,
                GuestWebSocketTicket.Channel.CHAT,
                self.conversation.conversation_id,
            )
        )

    def test_expired_ticket_is_refused(self):
        raw = self._issue().json()["data"]["ticket"]
        GuestWebSocketTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        self.assertIsNone(GuestWebSocketTicketService.consume(raw, GuestWebSocketTicket.Channel.SPARK_POOL))

    def test_forged_ticket_is_refused(self):
        self.assertIsNone(GuestWebSocketTicketService.consume("forged-ticket", GuestWebSocketTicket.Channel.SPARK_POOL))
        self.assertIsNone(GuestWebSocketTicketService.consume("", GuestWebSocketTicket.Channel.SPARK_POOL))

    def test_ticket_of_a_revoked_guest_is_refused(self):
        raw = self._issue().json()["data"]["ticket"]
        GuestIdentityService.revoke(self.identity)

        self.assertIsNone(GuestWebSocketTicketService.consume(raw, GuestWebSocketTicket.Channel.SPARK_POOL))

    def test_neither_the_ticket_nor_the_credential_reaches_the_logs(self):
        with self.assertLogs("capp.classes.guest.ws_ticket_service", level="INFO") as logs:
            raw = self._issue().json()["data"]["ticket"]
        logged = "\n".join(logs.output)

        self.assertNotIn(raw, logged)
        self.assertNotIn(self.client.cookies[COOKIE].value, logged)

    def test_purge_removes_only_long_expired_tickets(self):
        self._issue()
        fresh = GuestWebSocketTicket.objects.get()
        GuestWebSocketTicket.objects.create(
            guest_identity=self.identity,
            ticket_hash="a" * 64,
            channel=GuestWebSocketTicket.Channel.SPARK_POOL,
            expires_at=timezone.now() - timedelta(days=1),
        )

        removed = GuestWebSocketTicketService.purge_expired()

        self.assertEqual(removed, 1)
        self.assertEqual(GuestWebSocketTicket.objects.get().pk, fresh.pk)


class LegacyWebSocketSessionIdGateTests(TestCase):
    """
    The transition escape hatch for browsers already connecting with a raw
    session ID in the URL. It must be off by default and must not outlive the
    migration window even if the flag is left on.
    """

    def setUp(self):
        cache.clear()
        from capp import consumers

        self.allowed = consumers._legacy_ws_session_id_allowed

    def test_disabled_by_default(self):
        self.assertFalse(settings.GUEST_LEGACY_WS_SESSION_ID_ENABLED)

    @override_settings(GUEST_LEGACY_WS_SESSION_ID_ENABLED=False, **MIGRATION_OPEN)
    def test_flag_off_refuses_legacy_query_authentication(self):
        self.assertFalse(self.allowed())

    @override_settings(GUEST_LEGACY_WS_SESSION_ID_ENABLED=True, **MIGRATION_OPEN)
    def test_flag_on_inside_the_window_is_allowed(self):
        self.assertTrue(self.allowed())

    @override_settings(
        GUEST_LEGACY_WS_SESSION_ID_ENABLED=True,
        GUEST_LEGACY_MIGRATION_ENABLED=True,
        GUEST_LEGACY_MIGRATION_CUTOFF="2020-01-01",
    )
    def test_flag_on_past_the_cutoff_is_refused(self):
        """The flag cannot keep the legacy path alive on its own."""
        self.assertFalse(self.allowed())

    @override_settings(
        GUEST_LEGACY_WS_SESSION_ID_ENABLED=True,
        GUEST_LEGACY_MIGRATION_ENABLED=False,
        GUEST_LEGACY_MIGRATION_CUTOFF="2099-01-01",
    )
    def test_flag_on_with_migration_disabled_is_refused(self):
        self.assertFalse(self.allowed())
