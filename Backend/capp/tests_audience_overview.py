# capp/tests_audience_overview.py
"""
Per-person audience analytics: the admin view across accounts AND guests,
and the visit-recording endpoint that feeds it.

These tests pin what the audience list counts, what it leaves out, and the
honesty invariants of the visit columns: recorded visits are only ever
written by the visit endpoint, first_visit_at is never rewritten, and
people whose history predates the endpoint keep unknown (null) visit
fields rather than invented ones.
"""

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.models import (
    Conversation,
    CustomUser,
    GuestIdentity,
    GuestTransferGrant,
    Message,
)
from capp.tests import _make_character, _make_user

AUDIENCE_URL = "/api/v1/admin/audience/"
VISIT_URL = "/api/v1/analytics/visit/"
COOKIE = settings.GUEST_CREDENTIAL_COOKIE_NAME


def _make_conversation(*, user=None, guest_session_id=None, character, started_at=None):
    convo = Conversation.objects.create(
        user=user,
        guest_session_id=guest_session_id,
        character=character,
        started_at=started_at or timezone.now(),
    )
    return convo


def _message(conversation, sender_type, status=Message.StatusChoices.SENT, created_at=None):
    msg = Message.objects.create(
        conversation=conversation,
        sender_type=sender_type,
        content="x",
        status=status,
    )
    if created_at is not None:
        Message.objects.filter(pk=msg.pk).update(created_at=created_at)
    return msg


class AudienceTestCase(TestCase):
    def setUp(self):
        self.admin = _make_user(username="audadmin", email="audadmin@example.com", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.amina = _make_character(name="Amina", slug="amina")
        self.zuri = _make_character(name="Zuri", slug="zuri")

    def get(self, **params):
        response = self.client.get(AUDIENCE_URL, params)
        return response, response.json().get("data")

    def rows(self, **params):
        response, data = self.get(**params)
        self.assertEqual(response.status_code, 200, data)
        return {row["person_key"]: row for row in data["people"]}, data

    def mint_guest(self, **fields):
        identity, _credential = GuestIdentityService.mint()
        if fields:
            GuestIdentity.objects.filter(pk=identity.pk).update(**fields)
            identity.refresh_from_db()
        return identity


class AudienceAccessTests(AudienceTestCase):
    def test_non_admin_is_refused(self):
        user = _make_user(username="plain", email="plain@example.com")
        client = APIClient()
        client.force_authenticate(user=user)
        self.assertEqual(client.get(AUDIENCE_URL).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertIn(APIClient().get(AUDIENCE_URL).status_code, (401, 403))

    def test_invalid_params_are_rejected(self):
        for params in (
            {"visitor_type": "robots"},
            {"ordering": "username"},
            {"character_id": "not-a-uuid"},
            {"first_visit_after": "31-02-2026"},
            {"first_visit_after": "2026-01-05", "first_visit_before": "2026-01-01"},
        ):
            response, _ = self.get(**params)
            self.assertEqual(response.status_code, 400, params)


class AudienceContentTests(AudienceTestCase):
    def test_accounts_and_guests_listed_with_visit_columns(self):
        user = _make_user(username="wanjiku", email="wanjiku@example.com")
        CustomUser.objects.filter(pk=user.pk).update(
            first_visit_at=timezone.now() - timedelta(days=2),
            last_visit_at=timezone.now() - timedelta(hours=1),
            visit_country_code="KE",
        )
        guest = self.mint_guest(
            first_visit_at=timezone.now() - timedelta(days=1),
            visit_country_code="TZ",
        )

        rows, data = self.rows()

        self.assertEqual(data["total"], 3)  # admin + wanjiku + the guest
        account_row = rows[f"user:{user.user_id}"]
        self.assertEqual(account_row["visitor_type"], "account")
        self.assertEqual(account_row["country_code"], "KE")
        self.assertEqual(account_row["first_seen_source"], "recorded_visit")
        self.assertEqual(account_row["last_seen_source"], "recorded_visit")
        self.assertEqual(account_row["account_created_at"], user.created_at.isoformat())

        guest_row = rows[f"guest:{guest.guest_id}"]
        self.assertEqual(guest_row["visitor_type"], "guest")
        self.assertEqual(guest_row["country_code"], "TZ")
        self.assertIsNone(guest_row["account_created_at"])
        self.assertEqual(guest_row["label"], str(guest.guest_id))

        self.assertEqual(data["summary"]["accounts"], 2)
        self.assertEqual(data["summary"]["guests"], 1)
        self.assertEqual(data["summary"]["with_recorded_visit"], 2)

    def test_unvisited_people_show_evidence_not_invented_visits(self):
        # An account and a guest with no recorded visit: first/last seen fall
        # back to earliest known evidence with an explicit source label, never
        # to a fabricated "recorded visit".
        user = _make_user(username="njeri", email="njeri@example.com")
        past = timezone.now() - timedelta(days=10)
        convo = _make_conversation(user=user, character=self.amina, started_at=past)
        _message(convo, Message.SenderType.USER, created_at=past)

        rows, _ = self.rows(visitor_type="accounts")
        row = rows[f"user:{user.user_id}"]
        self.assertEqual(row["first_seen_source"], "conversation")
        self.assertEqual(row["last_seen_source"], "conversation")
        self.assertIsNotNone(row["first_seen_at"])

    def test_claimed_guest_is_merged_into_the_account(self):
        guest = self.mint_guest(first_visit_at=timezone.now() - timedelta(days=5))
        convo = _make_conversation(guest_session_id=guest.owner_key, character=self.amina)
        _message(convo, Message.SenderType.USER)
        _message(convo, Message.SenderType.AI)

        user = _make_user(username="claimed", email="claimed@example.com", verified=True)
        GuestTransferGrant.objects.create(
            user=user,
            guest_identity=guest,
            status=GuestTransferGrant.Status.PENDING,
            origin_guest_session_id=guest.owner_key,
        )
        result = GuestClaimService.claim(user, guest)
        self.assertTrue(result["claimed"])

        rows, data = self.rows(visitor_type="all")

        # No separate guest row: the person appears once, as the account.
        self.assertNotIn(f"guest:{guest.guest_id}", rows)
        self.assertIn(f"user:{user.user_id}", rows)
        account_row = rows[f"user:{user.user_id}"]
        # The claim carries the guest's first recorded visit over to the
        # account row, so the person's first seen IS that recorded visit —
        # "recorded_visit", not merely a claimed-guest fallback.
        self.assertEqual(account_row["first_seen_source"], "recorded_visit")
        # The claimed conversation's messages belong to the account now.
        self.assertEqual(account_row["total_messages"], 2)
        self.assertEqual(account_row["character_count"], 1)
        self.assertEqual(account_row["characters"][0]["name"], "Amina")

    def test_claimed_identity_tombstone_does_not_reappear(self):
        # Account erasure keeps the claimed identity as a tombstone (claimed_by
        # is SET_NULL on user delete, but status stays "claimed"). It must not
        # resurface as a guest row.
        guest = self.mint_guest()
        user = _make_user(username="erased", email="erased@example.com", verified=True)
        GuestTransferGrant.objects.create(
            user=user,
            guest_identity=guest,
            status=GuestTransferGrant.Status.PENDING,
            origin_guest_session_id=guest.owner_key,
        )
        self.assertTrue(GuestClaimService.claim(user, guest)["claimed"])
        user.delete()

        rows, data = self.rows(visitor_type="guests")
        self.assertEqual(data["total"], 0)
        self.assertEqual(rows, {})

    def test_soft_deleted_accounts_are_excluded(self):
        user = _make_user(username="gone", email="gone@example.com")
        CustomUser.objects.filter(pk=user.pk).update(is_deleted=True)

        rows, data = self.rows()
        self.assertNotIn(f"user:{user.user_id}", rows)

    def test_legacy_orphan_guest_sessions_are_included(self):
        # A conversation keyed on a guest_session_id with no GuestIdentity row
        # (pre-migration legacy UUID). It must appear with unknown visits.
        legacy_id = "5b9f2c1d-8a4e-4c1f-9c3d-7d2e6f8a1b2c"
        convo = _make_conversation(guest_session_id=legacy_id, character=self.zuri)
        _message(convo, Message.SenderType.USER)
        _message(convo, Message.SenderType.AI)

        rows, data = self.rows(visitor_type="guests")
        row = rows[f"guest:{legacy_id}"]
        self.assertEqual(row["label"], legacy_id)
        self.assertIsNone(row["country_code"])
        self.assertEqual(row["total_messages"], 2)
        self.assertEqual(row["characters"][0]["name"], "Zuri")

    def test_only_sent_user_and_ai_messages_are_counted(self):
        user = _make_user(username="counter", email="counter@example.com")
        convo_a = _make_conversation(user=user, character=self.amina)
        convo_z = _make_conversation(user=user, character=self.zuri)

        # Amina: 2 sent user + 1 sent AI = 3 exchanged; plus noise that must
        # not count (failed, blocked, pending, streaming, system).
        _message(convo_a, Message.SenderType.USER)
        _message(convo_a, Message.SenderType.USER)
        _message(convo_a, Message.SenderType.AI)
        _message(convo_a, Message.SenderType.USER, status=Message.StatusChoices.FAILED)
        _message(convo_a, Message.SenderType.USER, status=Message.StatusChoices.BLOCKED)
        _message(convo_a, Message.SenderType.USER, status=Message.StatusChoices.PENDING)
        _message(convo_a, Message.SenderType.USER, status=Message.StatusChoices.STREAMING)
        _message(convo_a, Message.SenderType.SYSTEM)

        # Zuri: 1 sent AI only.
        _message(convo_z, Message.SenderType.AI)

        rows, _ = self.rows(visitor_type="accounts")
        row = rows[f"user:{user.user_id}"]
        self.assertEqual(row["total_messages"], 4)
        self.assertEqual(row["character_count"], 2)
        by_name = {c["name"]: c for c in row["characters"]}
        self.assertEqual(by_name["Amina"]["user_messages"], 2)
        self.assertEqual(by_name["Amina"]["ai_messages"], 1)
        self.assertEqual(by_name["Amina"]["total_messages"], 3)
        self.assertEqual(by_name["Zuri"]["ai_messages"], 1)


class AudienceFilterTests(AudienceTestCase):
    def setUp(self):
        super().setUp()
        self.alice = _make_user(username="aliceke", email="aliceke@example.com")
        CustomUser.objects.filter(pk=self.alice.pk).update(visit_country_code="KE")
        self.nairobi_guest = self.mint_guest(visit_country_code="KE")
        self.zurich_guest = self.mint_guest(visit_country_code="CH")

    def test_visitor_type_filter(self):
        rows_accounts, data_accounts = self.rows(visitor_type="accounts")
        self.assertEqual(data_accounts["summary"]["guests"], 0)
        self.assertTrue(all(r["visitor_type"] == "account" for r in rows_accounts.values()))

        rows_guests, data_guests = self.rows(visitor_type="guests")
        self.assertEqual(data_guests["summary"]["accounts"], 0)
        self.assertTrue(all(r["visitor_type"] == "guest" for r in rows_guests.values()))

    def test_country_filter_and_unknown(self):
        rows, data = self.rows(country="KE")
        self.assertTrue(all(r["country_code"] == "KE" for r in rows.values()))
        # The admin has no recorded country → excluded by a specific country.
        self.assertNotIn(f"user:{self.admin.user_id}", rows)

        rows_unknown, _ = self.rows(country="unknown")
        self.assertIn(f"user:{self.admin.user_id}", rows_unknown)
        self.assertNotIn(f"guest:{self.nairobi_guest.guest_id}", rows_unknown)

    def test_country_facets_exclude_the_country_filter_itself(self):
        rows, data = self.rows(country="KE")
        facet_countries = {c["country_code"]: c["count"] for c in data["countries"]}
        # Facet counts are computed without the country filter: KE shows the
        # count a fresh "All countries" view would offer.
        self.assertEqual(facet_countries["KE"], 2)  # aliceke + nairobi guest
        self.assertEqual(facet_countries["CH"], 1)  # zurich guest
        self.assertEqual(facet_countries[None], 1)  # the admin, no country

    def test_character_filter_selects_people_who_exchanged_messages(self):
        user = _make_user(username="aminafan", email="aminafan@example.com")
        convo = _make_conversation(user=user, character=self.amina)
        _message(convo, Message.SenderType.USER)
        _message(convo, Message.SenderType.AI)
        # An empty conversation with Zuri must NOT match the Zuri filter.
        _make_conversation(user=user, character=self.zuri)

        rows, _ = self.rows(character_id=str(self.amina.character_id))
        self.assertIn(f"user:{user.user_id}", rows)

        rows_zuri, _ = self.rows(character_id=str(self.zuri.character_id))
        self.assertNotIn(f"user:{user.user_id}", rows_zuri)

    def test_first_seen_date_range_filter(self):
        user = _make_user(username="oldfriend", email="oldfriend@example.com")
        CustomUser.objects.filter(pk=user.pk).update(first_visit_at=timezone.now() - timedelta(days=30))

        rows, data = self.rows(first_visit_after="2020-01-01")
        self.assertIn(f"user:{user.user_id}", rows)
        rows_recent, _ = self.rows(first_visit_after=timezone.now().strftime("%Y-%m-%d"))
        self.assertNotIn(f"user:{user.user_id}", rows_recent)

    def test_created_filter_excludes_guests(self):
        user = _make_user(username="newbie", email="newbie@example.com")
        today = timezone.now().strftime("%Y-%m-%d")
        rows, data = self.rows(created_after=today)
        self.assertEqual(data["summary"]["guests"], 0)
        self.assertIn(f"user:{user.user_id}", rows)

    def test_search_matches_usernames_and_guest_ids(self):
        rows, _ = self.rows(search="aliceke")
        self.assertIn(f"user:{self.alice.user_id}", rows)

        guest_id_prefix = str(self.zurich_guest.guest_id)[:8]
        rows_guest, _ = self.rows(search=guest_id_prefix, visitor_type="guests")
        self.assertIn(f"guest:{self.zurich_guest.guest_id}", rows_guest)
        self.assertNotIn(f"guest:{self.nairobi_guest.guest_id}", rows_guest)

    def test_ordering_by_messages(self):
        chatty = _make_user(username="chatty", email="chatty@example.com")
        quiet = _make_user(username="quiet", email="quiet@example.com")
        for _i in range(5):
            _message(
                _make_conversation(user=chatty, character=self.amina),
                Message.SenderType.USER,
            )
        _message(_make_conversation(user=quiet, character=self.amina), Message.SenderType.USER)

        response, data = self.get(ordering="-messages", visitor_type="accounts")
        keys = [r["person_key"] for r in data["people"]]
        self.assertLess(keys.index(f"user:{chatty.user_id}"), keys.index(f"user:{quiet.user_id}"))

    def test_pagination_and_deterministic_ties(self):
        for i in range(25):
            self.mint_guest()
        # Equalise the sort key so the person_key tie-breaker is what orders.
        tie = timezone.now()
        GuestIdentity.objects.update(last_seen_at=tie, first_visit_at=tie)
        response, data = self.get(visitor_type="guests", page_size=10)
        # 25 minted + the two guests created in setUp.
        self.assertEqual(data["total"], 27)
        self.assertEqual(len(data["people"]), 10)
        self.assertEqual(data["total_pages"], 3)
        page_keys = [r["person_key"] for r in data["people"]]
        self.assertEqual(page_keys, sorted(page_keys))  # person_key tie-breaker

        response2, data2 = self.get(visitor_type="guests", page_size=10, page=3)
        self.assertEqual(len(data2["people"]), 7)
        self.assertFalse(set(r["person_key"] for r in data2["people"]) & set(page_keys))


class VisitRecordingTests(TestCase):
    """The /api/v1/analytics/visit/ endpoint and its write rules."""

    def setUp(self):
        self.client = APIClient()

    def _ensure_guest(self):
        resp = self.client.post("/api/v1/guest/session/", data={}, content_type="application/json")
        assert resp.status_code in (200, 201)
        identity = GuestIdentity.objects.latest("created_at")
        return identity

    def test_guest_visit_records_first_last_and_country(self):
        identity = self._ensure_guest()

        with mock.patch(
            "django.utils.timezone.now",
            return_value=timezone.now() - timedelta(hours=2),
        ):
            self.client.post(
                VISIT_URL,
                data={},
                content_type="application/json",
                HTTP_CF_IPCOUNTRY="KE",
            )

        identity.refresh_from_db()
        self.assertIsNotNone(identity.first_visit_at)
        self.assertIsNotNone(identity.last_visit_at)
        self.assertEqual(identity.visit_country_code, "KE")

    def test_rapid_pings_coalesce_last_visit(self):
        identity = self._ensure_guest()
        self.client.post(VISIT_URL, data={}, content_type="application/json")
        identity.refresh_from_db()
        first_ping = identity.last_visit_at

        self.client.post(VISIT_URL, data={}, content_type="application/json")
        identity.refresh_from_db()
        self.assertEqual(identity.last_visit_at, first_ping)  # not rewritten
        self.assertIsNotNone(identity.first_visit_at)

    def test_stale_ping_updates_last_visit_but_never_first(self):
        identity = self._ensure_guest()
        self.client.post(VISIT_URL, data={}, content_type="application/json")
        identity.refresh_from_db()
        original_first = identity.first_visit_at

        GuestIdentity.objects.filter(pk=identity.pk).update(last_visit_at=timezone.now() - timedelta(hours=6))
        self.client.post(VISIT_URL, data={}, content_type="application/json")
        identity.refresh_from_db()
        self.assertGreater(identity.last_visit_at, timezone.now() - timedelta(minutes=5))
        self.assertEqual(identity.first_visit_at, original_first)

    def test_authenticated_visit_recorded_on_account(self):
        user = _make_user(username="visitor", email="visitor@example.com")
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.post(
            VISIT_URL,
            data={},
            content_type="application/json",
            HTTP_CF_IPCOUNTRY="DE",
        )
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertIsNotNone(user.first_visit_at)
        self.assertEqual(user.visit_country_code, "DE")

    def test_unattributable_visit_is_a_silent_noop(self):
        resp = self.client.post(VISIT_URL, data={}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["data"]["recorded"])

    def test_invalid_country_values_are_not_stored(self):
        identity = self._ensure_guest()
        # Note: "ke" is NOT here on purpose — values are normalised to
        # uppercase, so a lowercase two-letter code is accepted as valid.
        for bad in ("XX", "T1", "Kenya", "K", "KEN", ""):
            self.client.post(
                VISIT_URL,
                data={},
                content_type="application/json",
                HTTP_CF_IPCOUNTRY=bad,
            )
        identity.refresh_from_db()
        self.assertIsNone(identity.visit_country_code)

    @override_settings(VISIT_COUNTRY_HEADER=None)
    def test_country_capture_can_be_disabled(self):
        identity = self._ensure_guest()
        self.client.post(
            VISIT_URL,
            data={},
            content_type="application/json",
            HTTP_CF_IPCOUNTRY="KE",
        )
        identity.refresh_from_db()
        self.assertIsNotNone(identity.first_visit_at)
        self.assertIsNone(identity.visit_country_code)
