# capp/tests_admin_users_safety_flag.py
"""
At-a-glance safety flag on the admin Users list.

The administrator should see which accounts need a look without opening every
profile. These tests pin what raises the flag (a care event or a critical/high
event, or ten or more events in total), what never does (reversed events,
advisory model refusals) and the privacy invariant that the list carries a
level and counts only.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from capp.classes.moderation.safety_ledger import ATTENTION_EVENT_THRESHOLD, SafetyLedgerService
from capp.models import SafetyEvent
from capp.tests import _make_user

URL = "/api/v1/admin/users/"


def _event(user, *, category="weapons_context", severity="low", is_care=False, **extra):
    """Create one safety event; defaults to a low-severity concern signal."""
    return SafetyEvent.objects.create(
        user_id=user.pk,
        category=category,
        severity=severity,
        action=extra.pop("action", SafetyEvent.Action.SHADOWED),
        is_care=is_care,
        classifier_version="test-v3",
        fingerprint=f"fp-{SafetyEvent.objects.count()}-{category}-{severity}",
        **extra,
    )


class UsersSafetyFlagTestCase(TestCase):
    def setUp(self):
        self.admin = _make_user(username="flagadmin", email="flagadmin@example.com", verified=True, is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.alice = _make_user(username="alice_flag", email="alice_flag@example.com")
        self.bob = _make_user(username="bob_flag", email="bob_flag@example.com")

    def safety(self, user):
        response = self.client.get(URL, {"search": user.username})
        self.assertEqual(response.status_code, 200)
        rows = {row["username"]: row for row in response.json()["data"]["users"]}
        return rows[user.username]["safety"]


class UsersSafetyFlagLevelTests(UsersSafetyFlagTestCase):
    def test_user_without_events_is_none(self):
        self.assertEqual(self.safety(self.alice), {"event_count": 0, "priority_count": 0, "level": "none"})

    def test_high_severity_event_is_priority(self):
        _event(self.alice, category="weapons_instructions", severity="high")
        self.assertEqual(self.safety(self.alice), {"event_count": 1, "priority_count": 1, "level": "priority"})

    def test_critical_event_is_priority(self):
        _event(self.alice, category="csam", severity="critical")
        self.assertEqual(self.safety(self.alice)["level"], "priority")

    def test_care_event_is_priority_even_at_low_severity(self):
        _event(self.alice, category="self_harm", severity="low", is_care=True)
        self.assertEqual(self.safety(self.alice), {"event_count": 1, "priority_count": 1, "level": "priority"})

    def test_a_few_low_or_medium_events_are_listed_but_not_flagged(self):
        _event(self.alice, severity="low")
        _event(self.alice, category="violence_planning", severity="medium")
        self.assertEqual(self.safety(self.alice), {"event_count": 2, "priority_count": 0, "level": "events"})

    def test_ten_low_or_medium_events_need_review(self):
        for _ in range(ATTENTION_EVENT_THRESHOLD - 1):
            _event(self.alice, severity="low")
        self.assertEqual(self.safety(self.alice)["level"], "events")
        _event(self.alice, category="violence_planning", severity="medium")
        self.assertEqual(
            self.safety(self.alice),
            {"event_count": ATTENTION_EVENT_THRESHOLD, "priority_count": 0, "level": "review"},
        )

    def test_priority_wins_over_review(self):
        for _ in range(ATTENTION_EVENT_THRESHOLD):
            _event(self.alice, severity="low")
        _event(self.alice, category="threats_targeted", severity="high")
        flag = self.safety(self.alice)
        self.assertEqual(flag["level"], "priority")
        self.assertEqual(flag["event_count"], ATTENTION_EVENT_THRESHOLD + 1)
        self.assertEqual(flag["priority_count"], 1)

    def test_threshold_is_reported_with_the_list(self):
        data = self.client.get(URL).json()["data"]
        self.assertEqual(data["safety_attention_threshold"], ATTENTION_EVENT_THRESHOLD)


class UsersSafetyFlagExclusionTests(UsersSafetyFlagTestCase):
    def test_reversed_events_do_not_count(self):
        _event(self.alice, category="csam", severity="critical", is_reversed=True, reversed_at=timezone.now())
        self.assertEqual(self.safety(self.alice), {"event_count": 0, "priority_count": 0, "level": "none"})

    def test_advisory_model_refusals_do_not_count(self):
        for _ in range(ATTENTION_EVENT_THRESHOLD + 2):
            _event(self.alice, category="model_refusal", severity="high")
        self.assertEqual(self.safety(self.alice), {"event_count": 0, "priority_count": 0, "level": "none"})

    def test_events_are_attributed_per_user(self):
        _event(self.alice, category="weapons_instructions", severity="high")
        self.assertEqual(self.safety(self.alice)["level"], "priority")
        self.assertEqual(self.safety(self.bob)["level"], "none")

    def test_enforced_and_shadowed_events_both_count(self):
        _event(self.alice, category="violence_planning", severity="medium", action=SafetyEvent.Action.BLOCKED)
        _event(self.alice, category="violence_planning", severity="medium", action=SafetyEvent.Action.SHADOWED)
        self.assertEqual(self.safety(self.alice)["event_count"], 2)


class UsersSafetyFlagPrivacyAndPerformanceTests(UsersSafetyFlagTestCase):
    def test_flag_carries_only_level_and_counts(self):
        _event(self.alice, category="csam", severity="critical")
        flag = self.safety(self.alice)
        self.assertEqual(set(flag), {"event_count", "priority_count", "level"})
        body = self.client.get(URL).content.decode()
        self.assertNotIn("csam", body)
        self.assertNotIn("fingerprint", body)
        self.assertNotIn("conversation_id", body)

    def test_one_grouped_query_regardless_of_page_size(self):
        for index in range(6):
            user = _make_user(username=f"many_{index}", email=f"many_{index}@example.com")
            _event(user, severity="high", category="weapons_instructions")
        with CaptureQueriesContext(connection) as small:
            self.client.get(URL, {"page_size": 2})
        with CaptureQueriesContext(connection) as large:
            self.client.get(URL, {"page_size": 20})

        def safety_queries(captured):
            return [q for q in captured if "safety_events" in q["sql"]]

        self.assertEqual(len(safety_queries(small)), 1)
        self.assertEqual(len(safety_queries(large)), 1)

    def test_service_returns_an_entry_for_every_requested_id(self):
        _event(self.alice, severity="high", category="weapons_instructions")
        result = SafetyLedgerService.attention_summaries([self.alice.pk, self.bob.pk])
        self.assertEqual(set(result), {self.alice.pk, self.bob.pk})
        self.assertEqual(SafetyLedgerService.attention_summaries([]), {})

    def test_non_admin_is_still_refused(self):
        client = APIClient()
        client.force_authenticate(user=self.alice)
        self.assertEqual(client.get(URL).status_code, 403)
