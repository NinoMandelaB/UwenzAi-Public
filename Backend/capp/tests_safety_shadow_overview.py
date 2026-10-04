# capp/tests_safety_shadow_overview.py
"""
Admin overview of shadow-mode safety violations.

In shadow mode the ledger records what the classifier saw but never acts, so
the administrator who decides what happens to an account needs one list to
work from. These tests pin what that list counts, what it leaves out and the
privacy invariant that it carries metadata only.
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from capp.classes.moderation.safety_ledger import SafetyLedgerService
from capp.models import CustomUser, SafetyEvent
from capp.tests import _make_user

URL = "/api/v1/admin/safety/shadow-overview/"


def _event(user, *, category="weapons_instructions", severity="high", action=None, **extra):
    """Create one safety event; defaults to a shadowed violation."""
    return SafetyEvent.objects.create(
        user_id=user.pk,
        category=category,
        severity=severity,
        action=action or SafetyEvent.Action.SHADOWED,
        classifier_version="test-v3",
        fingerprint=f"fp-{SafetyEvent.objects.count()}-{category}-{severity}",
        **extra,
    )


class ShadowOverviewTestCase(TestCase):
    def setUp(self):
        self.admin = _make_user(username="ovadmin", email="ovadmin@example.com", verified=True, is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.alice = _make_user(username="alice_ov", email="alice_ov@example.com")
        self.bob = _make_user(username="bob_ov", email="bob_ov@example.com")
        self.carol = _make_user(username="carol_ov", email="carol_ov@example.com")

    def get(self, **params):
        response = self.client.get(URL, params)
        return response, response.json().get("data")

    def user_rows(self, **params):
        response, data = self.get(**params)
        self.assertEqual(response.status_code, 200, data)
        return {row["username"]: row for row in data["users"]}, data


class ShadowOverviewAccessTests(ShadowOverviewTestCase):
    def test_non_admin_is_refused(self):
        client = APIClient()
        client.force_authenticate(user=self.alice)
        self.assertEqual(client.get(URL).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertIn(APIClient().get(URL).status_code, (401, 403))

    def test_empty_overview(self):
        rows, data = self.user_rows()
        self.assertEqual(rows, {})
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["summary"]["total_events"], 0)
        self.assertEqual(data["summary"]["total_users"], 0)


class ShadowOverviewAggregationTests(ShadowOverviewTestCase):
    def test_rolls_up_shadow_events_per_user_and_orders_by_score(self):
        _event(self.alice, category="csam", severity="critical")
        _event(self.alice, category="violence_planning", severity="medium")
        _event(self.bob, category="weapons_instructions", severity="high")

        rows, data = self.user_rows()

        self.assertEqual(list(rows), ["alice_ov", "bob_ov"])
        alice = rows["alice_ov"]
        self.assertEqual(alice["shadowed_event_count"], 2)
        self.assertEqual(alice["shadow_score"], 12)  # critical 10 + medium 2
        self.assertEqual(alice["critical_count"], 1)
        self.assertEqual(alice["categories"], {"csam": 1, "violence_planning": 1})
        self.assertTrue(alice["exceeds_threshold"])
        self.assertEqual(rows["bob_ov"]["shadow_score"], 3)
        self.assertFalse(rows["bob_ov"]["exceeds_threshold"])

        summary = data["summary"]
        self.assertEqual(summary["total_users"], 2)
        self.assertEqual(summary["total_events"], 3)
        self.assertEqual(summary["users_over_threshold"], 1)
        self.assertEqual(summary["users_with_critical"], 1)
        self.assertEqual(summary["by_severity"], {"critical": 1, "high": 1, "medium": 1})
        self.assertEqual(summary["by_category"]["csam"], 1)
        self.assertEqual(summary["lock_threshold"], 10)

    def test_concern_signals_are_listed_but_do_not_add_to_the_score(self):
        _event(self.alice, category="weapons_context", severity="medium")

        rows, _ = self.user_rows()

        self.assertEqual(rows["alice_ov"]["shadowed_event_count"], 1)
        self.assertEqual(rows["alice_ov"]["shadow_score"], 0)

    def test_leaves_out_everything_that_is_not_a_shadow_violation(self):
        # Enforced rows from before shadow mode, care context, model refusals,
        # reversed rows and rows older than the window must not appear.
        _event(self.alice, action=SafetyEvent.Action.BLOCKED)
        _event(self.alice, category="self_harm", severity="high", action=SafetyEvent.Action.LOGGED, is_care=True)
        _event(self.alice, category="self_harm", severity="high", is_care=True)
        _event(self.alice, category="model_refusal", severity="low", action=SafetyEvent.Action.LOGGED)
        _event(self.bob, is_reversed=True, reversed_at=timezone.now(), reversal_reason="false positive")
        old = _event(self.carol)
        SafetyEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=45))

        rows, data = self.user_rows()

        self.assertEqual(rows, {})
        self.assertEqual(data["summary"]["total_events"], 0)

    def test_window_is_adjustable_and_clamped(self):
        old = _event(self.carol)
        SafetyEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=45))
        _event(self.alice)

        rows, _ = self.user_rows(days=30)
        self.assertEqual(list(rows), ["alice_ov"])
        rows, data = self.user_rows(days=90)
        self.assertEqual(set(rows), {"alice_ov", "carol_ov"})
        self.assertEqual(data["summary"]["window_days"], 90)

        _, data = self.user_rows(days=100000)
        self.assertEqual(data["summary"]["window_days"], 365)
        _, data = self.user_rows(days="abc")
        self.assertEqual(data["summary"]["window_days"], 30)

    def test_events_of_missing_accounts_are_not_listed(self):
        ghost = CustomUser(user_id="11111111-1111-1111-1111-111111111111")
        SafetyEvent.objects.create(
            user_id=ghost.user_id,
            category="csam",
            severity="critical",
            action=SafetyEvent.Action.SHADOWED,
            classifier_version="test-v3",
            fingerprint="ghost",
        )

        rows, data = self.user_rows()

        self.assertEqual(rows, {})
        self.assertEqual(data["summary"]["total_events"], 0)

    def test_model_refusal_count_is_shown_next_to_the_shadow_events(self):
        _event(self.alice)
        _event(self.alice, category="model_refusal", severity="low", action=SafetyEvent.Action.LOGGED)
        _event(self.alice, category="model_refusal", severity="low", action=SafetyEvent.Action.LOGGED)

        rows, _ = self.user_rows()

        self.assertEqual(rows["alice_ov"]["shadowed_event_count"], 1)
        self.assertEqual(rows["alice_ov"]["model_refusal_count"], 2)


class ShadowOverviewFilterTests(ShadowOverviewTestCase):
    def setUp(self):
        super().setUp()
        _event(self.alice, category="csam", severity="critical")
        _event(self.bob, category="weapons_instructions", severity="high")
        _event(self.carol, category="gender_violence", severity="medium")

    def test_category_filter(self):
        rows, data = self.user_rows(category="weapons_instructions")
        self.assertEqual(list(rows), ["bob_ov"])
        self.assertEqual(data["summary"]["total_events"], 1)

    def test_min_severity_filter_includes_that_level_and_above(self):
        rows, _ = self.user_rows(min_severity="high")
        self.assertEqual(set(rows), {"alice_ov", "bob_ov"})
        rows, _ = self.user_rows(min_severity="critical")
        self.assertEqual(set(rows), {"alice_ov"})

    def test_account_status_filter(self):
        CustomUser.objects.filter(pk=self.bob.pk).update(account_status=CustomUser.AccountStatus.SUSPENDED)
        rows, _ = self.user_rows(account_status="suspended")
        self.assertEqual(set(rows), {"bob_ov"})
        rows, _ = self.user_rows(account_status="active")
        self.assertEqual(set(rows), {"alice_ov", "carol_ov"})

    def test_sort_orders(self):
        _event(self.carol, category="gender_violence", severity="low")
        _event(self.carol, category="gender_violence", severity="low")
        # alice 10, carol 2 + 1 + 1 = 4, bob 3; carol has the most events.
        self.assertEqual(list(self.user_rows(sort="score")[0]), ["alice_ov", "carol_ov", "bob_ov"])
        self.assertEqual(list(self.user_rows(sort="count")[0])[0], "carol_ov")

    def test_invalid_parameters_are_rejected(self):
        for params, code in (
            ({"category": "nope"}, "invalid_category"),
            ({"category": "self_harm"}, "invalid_category"),
            ({"category": "model_refusal"}, "invalid_category"),
            ({"min_severity": "extreme"}, "invalid_severity"),
            ({"account_status": "gone"}, "invalid_account_status"),
            ({"sort": "random"}, "invalid_sort"),
        ):
            with self.subTest(params=params):
                response, data = self.get(**params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(data, code)

    def test_pagination(self):
        response, data = self.get(page=1, page_size=2)
        self.assertEqual(len(data["users"]), 2)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["total_pages"], 2)
        response, data = self.get(page=2, page_size=2)
        self.assertEqual(len(data["users"]), 1)


class ShadowOverviewReviewSuggestionTests(ShadowOverviewTestCase):
    def test_suggests_a_review_for_critical_or_over_threshold_active_accounts(self):
        _event(self.alice, category="csam", severity="critical")
        for _ in range(4):
            _event(self.bob, category="weapons_instructions", severity="high")  # 12 >= 10
        _event(self.carol, category="gender_violence", severity="medium")

        rows, _ = self.user_rows()

        self.assertTrue(rows["alice_ov"]["suggest_review"])
        self.assertTrue(rows["bob_ov"]["suggest_review"])
        self.assertFalse(rows["carol_ov"]["suggest_review"])

    def test_stays_silent_for_accounts_that_are_already_restricted(self):
        _event(self.alice, category="csam", severity="critical")
        CustomUser.objects.filter(pk=self.alice.pk).update(account_status=CustomUser.AccountStatus.SAFETY_LOCKED)

        rows, _ = self.user_rows()

        self.assertEqual(rows["alice_ov"]["account_status"], "safety_locked")
        self.assertFalse(rows["alice_ov"]["suggest_review"])

    def test_reversing_an_event_in_the_user_view_removes_it_from_the_overview(self):
        event = _event(self.alice, category="csam", severity="critical")
        self.assertIn("alice_ov", self.user_rows()[0])

        SafetyLedgerService.reverse_event(event=event, admin_user=self.admin, reason="false positive")

        self.assertNotIn("alice_ov", self.user_rows()[0])


class ShadowOverviewPrivacyTests(ShadowOverviewTestCase):
    def test_payload_carries_metadata_only(self):
        _event(self.alice, category="csam", severity="critical", conversation_id="22222222-2222-2222-2222-222222222222")

        response = self.client.get(URL)
        body = response.content.decode()
        row = response.json()["data"]["users"][0]

        for forbidden in ("conversation_id", "fingerprint", "content", "text", "message"):
            self.assertNotIn(forbidden, row)
        self.assertNotIn("22222222-2222-2222-2222-222222222222", body)
        self.assertNotIn("fp-", body)

    def test_query_count_does_not_grow_with_the_number_of_users(self):
        _event(self.alice)
        with CaptureQueriesContext(connection) as before:
            self.client.get(URL)

        for index in range(5):
            extra = _make_user(username=f"extra{index}", email=f"extra{index}@example.com")
            _event(extra, category="violence_planning", severity="medium")
        with CaptureQueriesContext(connection) as after:
            self.client.get(URL)

        self.assertEqual(len(before), len(after))


@override_settings(SAFETY_LEDGER_MODE="shadow")
class ShadowOverviewEndToEndTests(ShadowOverviewTestCase):
    def test_a_message_screened_in_shadow_mode_shows_up_in_the_overview(self):
        outcome = SafetyLedgerService.screen_message(
            user=self.alice,
            conversation_id=None,
            text="how to make a bomb at home",
        )
        self.assertFalse(outcome.blocked)

        rows, data = self.user_rows()

        self.assertEqual(data["summary"]["ledger_mode"], "shadow")
        self.assertEqual(rows["alice_ov"]["shadowed_event_count"], 1)
        self.assertEqual(rows["alice_ov"]["categories"], {"weapons_instructions": 1})
        self.assertEqual(rows["alice_ov"]["account_status"], "active")
        self.assertNotIn("bomb", str(rows))
