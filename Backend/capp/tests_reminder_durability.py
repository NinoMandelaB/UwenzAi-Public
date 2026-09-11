"""Audited reminder/tool regressions. Providers and delivery hints are doubles;
claims, locks, reservations, refunds, outcomes and Messages use the real DB.
Concurrency cases require PostgreSQL (not SQLite's select_for_update no-op).
"""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from unittest import mock, skipUnless
from zoneinfo import ZoneInfo

from celery.exceptions import Retry
from django.db import connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.billing.idempotency import result_key
from capp.classes.billing.paid_features import PaidFeature, ReservationError
from capp.classes.billing.reservation_service import BillingReservationService
from capp.classes.profile.tool_loop import run_profile_tool_calls
from capp.classes.reminders.reminder_service import ReminderService
from capp.classes.reminders.tool_loop import (
    build_reminder_tool_specs,
    ReminderDisabled,
    cancel_reminder,
    create_reminder,
    run_reminder_tool_calls,
    update_reminder,
)
from capp.constants import BYOK_MESSAGE_PAIR_COST, MESSAGE_PAIR_COST, REMINDER_EXPIRY_GRACE_HOURS
from capp.models import (
    BillingReservation,
    CustomUser,
    Message,
    Reminder,
    ReminderDeliveryAttempt,
    ReminderToolExecution,
    SparkPool,
    UserBYOKConfig,
)
from capp.tests import _make_character, _make_user
from capp.tests_reminders import _fund_user, _make_conversation, _make_reminder
from capp.views.reminder_views import api_create_reminder, api_delete_reminder, api_update_reminder


def tool(name, args, call_id=None):
    return {
        "id": call_id or str(uuid.uuid4()),
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class WorkerDied(BaseException):
    """Simulate termination after the reserve transaction committed."""


class ReminderRegressionBase:
    def setUp(self):
        super().setUp()
        self.user = _make_user(username="reminder-regression", email="reminder-regression@example.test")
        self.character = _make_character(name="Regression", slug="reminder-regression")
        self.conversation = _make_conversation(self.user, self.character)
        _fund_user(self.user, 10000)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(BillingReservationService, "_push_balance_after_commit"))
        self.stack.enter_context(mock.patch("capp.classes.push.push_service.PushService.send_new_message_push"))
        self.stack.enter_context(mock.patch("capp.classes.chat.unread_service.broadcast_unread"))
        self.broadcast = self.stack.enter_context(mock.patch.object(ReminderService, "_send_websocket_notification"))
        self.generate = self.stack.enter_context(
            mock.patch.object(ReminderService, "_generate_delivery_text", return_value="Time to call Paul.")
        )

    def reminder(self, **kwargs):
        return _make_reminder(self.user, self.character, self.conversation, **kwargs)

    def source(self):
        return Message.objects.create(conversation=self.conversation, sender_type="user", content="Remind me.")

    def args(self, **overrides):
        local = (timezone.now() + timedelta(days=3)).astimezone(ZoneInfo(self.user.timezone))
        return {"reminder_text": "call Paul", "due_date": local.strftime("%Y-%m-%d"), "due_time": "10:00", **overrides}

    def execute(self, calls, source=None):
        return run_reminder_tool_calls(
            calls,
            user=CustomUser.objects.get(pk=self.user.pk),
            character=self.character,
            conversation=self.conversation,
            source_message=source,
        )

    def api(self, view, method, data=None, reminder=None):
        request = getattr(APIRequestFactory(), method)("/", data=data, format="json")
        force_authenticate(request, user=CustomUser.objects.get(pk=self.user.pk))
        return view(request, **({"reminder_id": reminder.pk} if reminder is not None else {}))

    def balance(self):
        return SparkPool.objects.get(user=self.user).sparks

    def paid_messages(self):
        return Message.objects.filter(conversation=self.conversation, metadata__reminder=True)

    def held(self, reminder, *, legacy=False):
        with transaction.atomic():
            attempt = (
                None
                if legacy
                else ReminderDeliveryAttempt.objects.create(
                    reminder_key=reminder.pk,
                    user=self.user,
                    revision=reminder.revision,
                    lease_until=timezone.now() + timedelta(minutes=10),
                )
            )
            reservation = PaidFeature.reserve(
                feature=BillingReservation.Feature.MESSAGE_PAIR,
                operation_id=(
                    str(attempt.pk)
                    if attempt
                    else str(uuid.uuid5(uuid.UUID("6c1a8f2e-9b3d-4c7a-8e2f-1a9d5b6c7e40"), str(reminder.pk)))
                ),
                operation_reference=(
                    f"reminder:{reminder.pk}:attempt:{attempt.pk}" if attempt else f"reminder:{reminder.pk}"
                ),
                user=self.user,
            )
            if attempt:
                attempt.reservation = reservation
                attempt.save(update_fields=["reservation"])
        return attempt, reservation

    def byok(self):
        return UserBYOKConfig.objects.create(
            user=self.user,
            api_url="https://provider.invalid/v1",
            api_key_encrypted="offline-only",
            model_name="offline",
            is_active=True,
        )

    def dispatch(self, calls, names, source=None):
        from capp.tasks import _run_offered_tool_calls

        return _run_offered_tool_calls(
            calls,
            self.conversation.pk,
            offered_tool_names=frozenset(names),
            conversation=self.conversation,
            user=self.user,
            character=self.character,
            source_message=source,
        )


class ReminderDurabilityTests(ReminderRegressionBase, TransactionTestCase):
    def test_provider_failure_refunds_then_retries_with_new_key_and_reference(self):
        for failure in (RuntimeError("provider failed"), ""):
            with self.subTest(failure=str(failure)):
                start = self.balance()
                reminder = self.reminder()
                self.generate.side_effect = [failure, "Delivered."]
                self.assertFalse(ReminderService._deliver(reminder))
                self.assertEqual(self.balance(), start)
                self.assertTrue(ReminderService._deliver(reminder))
                attempts = list(ReminderDeliveryAttempt.objects.filter(reminder_key=reminder.pk))
                self.assertEqual(len(attempts), 2)
                self.assertEqual({a.reservation.status for a in attempts}, {"refunded", "settled"})
                self.assertEqual(len({a.reservation.operation_reference for a in attempts}), 2)
                self.assertEqual(self.balance(), start - MESSAGE_PAIR_COST)
                self.assertFalse(Reminder.objects.filter(pk=reminder.pk).exists())
                self.assertEqual(self.paid_messages().filter(metadata__reminder_id=str(reminder.pk)).count(), 1)

    def test_deadline_is_checked_before_reserve_or_provider(self):
        now = timezone.now()
        for hours in (REMINDER_EXPIRY_GRACE_HOURS, 48):
            reminder = self.reminder()
            Reminder.objects.filter(pk=reminder.pk).update(due_at=now - timedelta(hours=hours))
            with mock.patch("capp.classes.reminders.reminder_service.django_timezone.now", return_value=now):
                self.assertFalse(ReminderService._deliver(reminder))
        self.assertEqual(BillingReservation.objects.count(), 0)
        self.generate.assert_not_called()
        self.assertEqual(Message.objects.filter(metadata__code="reminder_gave_up").count(), 2)
        self.assertEqual(self.balance(), 10000)

    def test_generation_crossing_deadline_refunds_and_persists_give_up(self):
        reminder = self.reminder()
        now = timezone.now()
        with mock.patch("capp.classes.reminders.reminder_service.django_timezone.now", return_value=now) as clock:

            def generate(*args):
                clock.return_value = reminder.due_at + timedelta(hours=REMINDER_EXPIRY_GRACE_HOURS)
                return "Too late."

            self.generate.side_effect = generate
            self.assertFalse(ReminderService._deliver(reminder))
        self.assertEqual(self.balance(), 10000)
        self.assertEqual(self.paid_messages().count(), 0)
        self.assertEqual(Message.objects.filter(metadata__code="reminder_gave_up").count(), 1)
        self.assertEqual(BillingReservation.objects.get().status, "refunded")

    def test_stale_selection_after_cancel_or_update_never_reserves(self):
        reminder = self.reminder()
        self.assertEqual(self.api(api_delete_reminder, "delete", reminder=reminder).status_code, 200)
        self.assertFalse(ReminderService._deliver(reminder))
        other = self.reminder()
        self.assertEqual(self.api(api_update_reminder, "patch", self.args(), reminder=other).status_code, 200)
        self.assertFalse(ReminderService._deliver(other))
        self.generate.assert_not_called()
        self.assertEqual(BillingReservation.objects.count(), 0)
        self.assertTrue(Reminder.objects.filter(pk=other.pk).exists())

    def test_acknowledged_cancel_during_provider_fences_commit_rest_and_tool(self):
        for use_rest in (True, False):
            reminder = self.reminder()

            def generate(*args):
                self.assertFalse(connection.in_atomic_block)
                if use_rest:
                    self.assertEqual(self.api(api_delete_reminder, "delete", reminder=reminder).status_code, 200)
                else:
                    result, _ = self.execute(
                        [tool("cancel_reminder", {"reminder_id": str(reminder.pk)})], self.source()
                    )
                    self.assertIn("cancelled successfully", result)
                return "Must not commit."

            self.generate.side_effect = generate
            self.assertFalse(ReminderService._deliver(reminder))
            self.assertFalse(Reminder.objects.filter(pk=reminder.pk).exists())
        self.assertEqual(self.paid_messages().count(), 0)
        self.assertEqual(self.balance(), 10000)
        self.assertFalse(BillingReservation.objects.filter(status="reserved").exists())

    def test_acknowledged_reschedule_during_provider_preserves_new_schedule(self):
        for use_rest in (True, False):
            reminder = self.reminder()

            def generate(*args):
                if use_rest:
                    self.assertEqual(
                        self.api(api_update_reminder, "patch", self.args(), reminder=reminder).status_code, 200
                    )
                else:
                    result, _ = self.execute(
                        [tool("update_reminder", {**self.args(), "reminder_id": str(reminder.pk)})], self.source()
                    )
                    self.assertIn("updated successfully", result)
                return "Must not commit."

            self.generate.side_effect = generate
            self.assertFalse(ReminderService._deliver(reminder))
            reminder.refresh_from_db()
            self.assertEqual(reminder.revision, 2)
            self.assertGreater(reminder.due_at, timezone.now())
        self.assertEqual(self.paid_messages().count(), 0)
        self.assertEqual(self.balance(), 10000)

    def test_process_death_lease_recovery_uses_current_byok_terms(self):
        reminder = self.reminder()
        self.generate.side_effect = WorkerDied()
        with self.assertRaises(WorkerDied):
            ReminderService._deliver(reminder)
        attempt = ReminderDeliveryAttempt.objects.get()
        self.assertEqual(attempt.reservation.status, "reserved")
        self.byok()
        self.generate.side_effect = None
        ReminderDeliveryAttempt.objects.filter(pk=attempt.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(ReminderService.check_and_send_due(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.reservation.status, "refunded")
        self.assertEqual(self.balance(), 10000 - BYOK_MESSAGE_PAIR_COST)
        self.assertEqual(self.paid_messages().count(), 1)

    def test_cancel_retains_refund_pending_tombstone_if_refund_fails(self):
        for use_rest in (True, False):
            reminder = self.reminder()
            attempt, reservation = self.held(reminder)
            with mock.patch.object(BillingReservationService, "refund", side_effect=ReservationError("offline")):
                if use_rest:
                    self.assertEqual(self.api(api_delete_reminder, "delete", reminder=reminder).status_code, 200)
                else:
                    cancel_reminder(user=self.user, reminder_id=reminder.pk)
            self.assertFalse(Reminder.objects.filter(pk=reminder.pk).exists())
            attempt.refresh_from_db()
            self.assertEqual(attempt.status, "refund_pending")
            self.assertEqual(ReminderService.check_and_send_due(), 0)
            reservation.refresh_from_db()
            self.assertEqual(reservation.status, "refunded")
        self.generate.assert_not_called()
        self.assertEqual(self.balance(), 10000)
        self.assertEqual(BillingReservation.objects.filter(refund_spark_transaction__isnull=False).count(), 2)

    def test_expiry_remains_durable_even_if_existing_hold_cannot_yet_refund(self):
        reminder = self.reminder()
        attempt, reservation = self.held(reminder)
        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now() - timedelta(hours=48))
        with mock.patch.object(BillingReservationService, "refund", side_effect=ReservationError("offline")):
            self.assertEqual(ReminderService.check_and_send_due(), 0)
        self.assertFalse(Reminder.objects.filter(pk=reminder.pk).exists())
        self.assertEqual(Message.objects.filter(metadata__code="reminder_gave_up").count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "refund_pending")
        ReminderService.check_and_send_due()
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "refunded")
        self.assertEqual(self.balance(), 10000)
        self.generate.assert_not_called()

    def test_recovery_finds_holds_after_raw_cascade_delete_and_inactive_owner(self):
        reminder = self.reminder()
        _, reservation = self.held(reminder)
        reminder.delete()
        other = self.reminder()
        _, other_reservation = self.held(other)
        CustomUser.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(ReminderService.check_and_send_due(), 0)
        for hold in (reservation, other_reservation):
            hold.refresh_from_db()
            self.assertEqual(hold.status, "refunded")
        self.generate.assert_not_called()
        self.assertEqual(self.balance(), 10000)

    def test_legacy_refunded_and_byok_conflicting_holds_do_not_block_retry(self):
        for refunded in (False, True):
            reminder = self.reminder()
            _, reservation = self.held(reminder, legacy=True)
            if refunded:
                BillingReservationService.refund(reservation, reason="old_provider_failure")
            if not UserBYOKConfig.objects.filter(user=self.user).exists():
                self.byok()
            start = self.balance() + (0 if refunded else MESSAGE_PAIR_COST)
            self.assertEqual(ReminderService.check_and_send_due(), 1)
            self.assertEqual(self.balance(), start - BYOK_MESSAGE_PAIR_COST)
            reservation.refresh_from_db()
            self.assertEqual(reservation.status, "refunded")

    def test_legacy_orphan_hold_is_automatically_recovered_without_schedule(self):
        reminder = self.reminder()
        _, reservation = self.held(reminder, legacy=True)
        reminder.delete()
        ReminderService.check_and_send_due()
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "refunded")
        self.assertEqual(self.balance(), 10000)

    def test_offline_notices_survive_channel_failure_and_are_not_charged(self):
        reminder = self.reminder()
        _fund_user(self.user, 0)
        self.broadcast.side_effect = RuntimeError("channel unavailable")
        self.assertEqual(ReminderService.check_and_send_due(), 0)
        self.assertEqual(ReminderService.check_and_send_due(), 0)
        reminder.refresh_from_db()
        self.assertTrue(reminder.notice_sent)
        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now() - timedelta(hours=48))
        self.assertEqual(ReminderService.check_and_send_due(), 0)
        notices = Message.objects.filter(conversation=self.conversation, sender_type="system")
        self.assertEqual(notices.count(), 2)
        self.assertEqual({m.metadata["code"] for m in notices}, {"reminder_insufficient_sparks", "reminder_gave_up"})
        self.assertEqual(BillingReservation.objects.count(), 0)
        self.assertEqual(self.balance(), 0)

    def test_one_broken_refund_does_not_block_other_recovery(self):
        first = self.reminder()
        _, bad_hold = self.held(first)
        second = self.reminder()
        _, good_hold = self.held(second)
        Reminder.objects.filter(pk__in=[first.pk, second.pk]).delete()
        strict_refund = BillingReservationService.refund

        def refund(reservation, **kwargs):
            if getattr(reservation, "pk", reservation) == bad_hold.pk:
                raise ReservationError("one hold needs later recovery")
            return strict_refund(reservation, **kwargs)

        with mock.patch.object(BillingReservationService, "refund", side_effect=refund):
            ReminderService.check_and_send_due()
        good_hold.refresh_from_db()
        self.assertEqual(good_hold.status, "refunded")
        self.assertTrue(ReminderDeliveryAttempt.objects.filter(reservation=bad_hold, status="active").exists())
        ReminderService.check_and_send_due()
        self.assertEqual(self.balance(), 10000)


class ReminderToolRegressionTests(ReminderRegressionBase, TransactionTestCase):
    def test_logical_create_dedupe_ignores_changed_call_id_arguments_and_deleted_schedule(self):
        source = self.source()
        first, _ = self.execute([tool("create_reminder", self.args())], source)
        self.assertIn("saved successfully", first)
        reminder = Reminder.objects.get()
        cancel_reminder(user=self.user, reminder_id=reminder.pk)
        # A late retry's different arguments must not mint a second logical slot.
        replay, _ = self.execute([tool("create_reminder", self.args(reminder_text="changed model wording"))], source)
        self.assertIn("Previously executed", replay)
        self.assertFalse(Reminder.objects.exists())
        self.assertEqual(ReminderToolExecution.objects.filter(family="reminder").count(), 1)

    def test_update_and_cancel_outcomes_are_replayed_without_reapplying(self):
        reminder = self.reminder(due_in=timedelta(days=3))
        source = self.source()
        self.execute([tool("update_reminder", {"reminder_id": str(reminder.pk), "reminder_text": "first"})], source)
        update_reminder({"reminder_text": "subsequent edit"}, user=self.user, reminder_id=reminder.pk)
        self.execute(
            [tool("update_reminder", {"reminder_id": str(reminder.pk), "reminder_text": "late retry"})], source
        )
        reminder.refresh_from_db()
        self.assertEqual(reminder.reminder_text, "subsequent edit")
        cancel_source = self.source()
        self.execute([tool("cancel_reminder", {"reminder_id": str(reminder.pk)})], cancel_source)
        replay, _ = self.execute([tool("create_reminder", self.args())], cancel_source)
        self.assertIn("cancelled successfully", replay)
        self.assertFalse(Reminder.objects.exists())

    def test_disabling_creation_keeps_management_and_delivery_and_uses_fresh_preference(self):
        self.reminder()
        stale_user = CustomUser.objects.get(pk=self.user.pk)
        FeaturePreferences.update(self.user, {"reminders": False})
        with self.assertRaisesRegex(ReminderDisabled, "reminders_disabled"):
            create_reminder(self.args(), user=stale_user, character=self.character, conversation=self.conversation)
        response = self.api(
            api_create_reminder,
            "post",
            {
                **self.args(),
                "conversation_id": str(self.conversation.pk),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ReminderService.check_and_send_due(), 1)
        self.assertEqual(self.balance(), 10000 - MESSAGE_PAIR_COST)
        other = self.reminder(due_in=timedelta(days=3))
        self.assertEqual(
            self.api(api_update_reminder, "patch", {"reminder_text": "edit"}, reminder=other).status_code, 200
        )
        self.assertEqual(self.api(api_delete_reminder, "delete", reminder=other).status_code, 200)
        self.assertEqual(
            {s["function"]["name"] for s in build_reminder_tool_specs(allow_create=False)},
            {"update_reminder", "cancel_reminder"},
        )

    def test_partial_preference_update_uses_latest_owner_not_stale_request(self):
        stale = CustomUser.objects.get(pk=self.user.pk)
        FeaturePreferences.update(self.user, {"reminders": False})
        FeaturePreferences.update(stale, {"websearch": True})
        self.user.refresh_from_db()
        self.assertFalse(FeaturePreferences.is_enabled(self.user, "reminders"))
        self.assertTrue(FeaturePreferences.is_enabled(self.user, "websearch"))

    def test_current_local_tool_updates_snapshot_for_all_partial_date_time_shapes(self):
        for zone in ("Europe/Zurich", "America/New_York"):
            for mode in ("time", "date", "both"):
                reminder = self.reminder(due_in=timedelta(days=4))
                CustomUser.objects.filter(pk=self.user.pk).update(timezone=zone)
                local_before = reminder.due_at.astimezone(ZoneInfo(zone))
                changes = {"reminder_id": str(reminder.pk)}
                if mode in ("time", "both"):
                    changes["due_time"] = "10:00"
                if mode in ("date", "both"):
                    changes["due_date"] = (timezone.now() + timedelta(days=5)).strftime("%Y-%m-%d")
                block, _ = self.execute([tool("update_reminder", changes)])
                self.assertIn("updated successfully", block)
                reminder.refresh_from_db()
                local_after = reminder.due_at.astimezone(ZoneInfo(zone))
                self.assertEqual(reminder.timezone_snapshot, zone)
                self.assertEqual(local_after.strftime("%H:%M"), changes.get("due_time", local_before.strftime("%H:%M")))
                self.assertEqual(
                    local_after.strftime("%Y-%m-%d"), changes.get("due_date", local_before.strftime("%Y-%m-%d"))
                )
                CustomUser.objects.filter(pk=self.user.pk).update(timezone="Africa/Nairobi")

    def test_timezone_change_alone_preserves_absolute_schedules_and_rest_snapshot_contract(self):
        reminder = self.reminder(due_in=timedelta(days=4))
        original = reminder.due_at
        run_profile_tool_calls([tool("update_timezone", {"timezone": "America/New_York"})], user=self.user)
        reminder.refresh_from_db()
        self.assertEqual(reminder.due_at, original)
        response = self.api(api_update_reminder, "patch", {"due_time": "10:00"}, reminder=reminder)
        self.assertEqual(response.status_code, 200)
        reminder.refresh_from_db()
        self.assertEqual(reminder.timezone_snapshot, "Africa/Nairobi")
        self.assertEqual(reminder.due_at.astimezone(ZoneInfo("Africa/Nairobi")).hour, 10)

    def test_dependent_profile_and_reminder_execute_in_returned_order(self):
        for reverse in (False, True):
            CustomUser.objects.filter(pk=self.user.pk).update(timezone="Africa/Nairobi")
            calls = [tool("update_timezone", {"timezone": "America/New_York"}), tool("create_reminder", self.args())]
            if reverse:
                calls.reverse()
            result = self.dispatch(calls, {"update_timezone", "create_reminder"}, self.source())
            self.assertEqual(result[3], 1)
            reminder = Reminder.objects.latest("created_at")
            zone = "Africa/Nairobi" if reverse else "America/New_York"
            self.assertEqual(reminder.timezone_snapshot, zone)
            self.assertEqual(reminder.due_at.astimezone(ZoneInfo(zone)).hour, 10)

    def test_failed_timezone_does_not_silently_schedule_in_old_timezone(self):
        result = self.dispatch(
            [tool("update_timezone", {"timezone": "not-a-zone"}), tool("create_reminder", self.args())],
            {"update_timezone", "create_reminder"},
            self.source(),
        )
        self.assertIn("preceding timezone update failed", result[0])
        self.assertFalse(Reminder.objects.exists())

    def test_invalid_string_fields_and_uuid_shapes_return_errors_not_exceptions(self):
        reminder = self.reminder(due_in=timedelta(days=4))
        bad_values = [None, 10, False, [], {}, ""]
        cases = [
            ("create_reminder", self.args(), ("reminder_text", "due_date", "due_time")),
            (
                "update_reminder",
                {"reminder_id": str(reminder.pk), "due_time": "10:00"},
                ("reminder_id", "reminder_text", "due_date", "due_time"),
            ),
            ("cancel_reminder", {"reminder_id": str(reminder.pk)}, ("reminder_id",)),
        ]
        for name, args, fields in cases:
            for field in fields:
                for value in bad_values:
                    with self.subTest(name=name, field=field, value=value):
                        block, count = self.execute([tool(name, {**args, field: value})])
                        self.assertIn("failed:", block)
                        self.assertEqual(count, 1)
        for value in bad_values + ["bad-uuid", str(uuid.uuid4())]:
            for name in ("update_reminder", "cancel_reminder"):
                block, _ = self.execute([tool(name, {"reminder_id": value, "reminder_text": "new"})])
                self.assertIn("failed:", block)
        for value in bad_values:
            block, _ = run_profile_tool_calls([tool("update_timezone", {"timezone": value})], user=self.user)
            self.assertIn("failed:", block)
        self.assertEqual(Reminder.objects.count(), 1)
        reminder.refresh_from_db()
        self.assertEqual(reminder.revision, 1)

    def test_rest_malformed_payloads_and_uuid_are_safe_4xx(self):
        reminder = self.reminder(due_in=timedelta(days=4))
        for value in [[], 1, False, "not-json"]:
            self.assertEqual(self.api(api_create_reminder, "post", value).status_code, 400)
            self.assertEqual(self.api(api_update_reminder, "patch", value, reminder=reminder).status_code, 400)
        for field in ("reminder_text", "due_date", "due_time", "conversation_id"):
            self.assertEqual(
                self.api(
                    api_create_reminder,
                    "post",
                    {
                        **self.args(),
                        "conversation_id": str(self.conversation.pk),
                        field: [],
                    },
                ).status_code,
                400,
            )
        self.assertEqual(
            self.api(
                api_create_reminder,
                "post",
                {
                    **self.args(),
                    "conversation_id": "bad-uuid",
                },
            ).status_code,
            400,
        )

    def test_exact_allowlist_rejects_unoffered_tools_even_when_other_tools_are_offered(self):
        calls = [
            tool("web_search", {"query": "weather"}),
            tool("open_url", {"url": "https://invalid.test"}),
            tool("create_reminder", self.args()),
            tool("update_timezone", {"timezone": "Europe/Zurich"}),
            {"function": {"name": [], "arguments": "{}"}},
        ]
        with mock.patch("capp.tasks.run_search_tool_calls") as search, mock.patch(
            "capp.tasks.run_open_url_tool_calls"
        ) as fetch:
            result = self.dispatch(calls, {"update_timezone"}, self.source())
        search.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(result[1:], (0, 0, 0))
        self.assertFalse(Reminder.objects.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Europe/Zurich")

    def test_current_search_permissions_and_conversation_toggle_are_rechecked(self):
        calls = [tool("web_search", {"query": "weather"}), tool("open_url", {"url": "https://invalid.test"})]
        for feature, conv_flag in ((False, True), (True, False)):
            FeaturePreferences.update(self.user, {"websearch": feature})
            self.conversation.web_search_enabled = conv_flag
            self.conversation.save(update_fields=["web_search_enabled"])
            with mock.patch("capp.tasks.run_search_tool_calls") as search, mock.patch(
                "capp.tasks.run_open_url_tool_calls"
            ) as fetch:
                result = self.dispatch(calls, {"web_search", "open_url"}, self.source())
            search.assert_not_called()
            fetch.assert_not_called()
            self.assertEqual(result[1:], (0, 0, 0))

    def test_empty_allowlist_and_guests_cannot_execute_tools(self):
        calls = [tool("create_reminder", self.args())]
        self.assertEqual(self.dispatch(calls, set())[1:], (0, 0, 0))
        from capp.tasks import _run_offered_tool_calls

        result = _run_offered_tool_calls(
            calls, uuid.uuid4(), offered_tool_names={"create_reminder"}, conversation=None, user=None
        )
        self.assertEqual(result, ("", 0, 0, 0))
        self.assertFalse(Reminder.objects.exists())


class ReminderChatTaskRegressionTests(ReminderRegressionBase, TransactionTestCase):
    def setUp(self):
        super().setUp()
        from capp import tasks

        self.tasks = tasks
        self.stack.enter_context(
            mock.patch.object(
                tasks.PromptBuilder,
                "build_messages",
                return_value=[
                    {"role": "system", "content": "Platform rules."},
                    {"role": "system", "content": "=== SAFETY CHECK (System) ===\nSafety tail."},
                    {"role": "user", "content": "Remind me."},
                ],
            )
        )
        self.stack.enter_context(
            mock.patch.object(
                tasks.PromptBuilder, "inject_untrusted_web_results", side_effect=lambda messages, block: messages
            )
        )
        self.stack.enter_context(mock.patch.object(tasks, "_send_ai_message"))
        self.stack.enter_context(mock.patch.object(tasks, "_send_system_message"))
        self.stack.enter_context(mock.patch.object(tasks, "chain_sub_tasks"))
        self.stack.enter_context(mock.patch.object(tasks.ProactiveChatService, "reset_counter"))
        self.stack.enter_context(mock.patch.object(tasks, "get_search_provider", return_value=object()))

    def response(self, calls=None):
        return {
            "id": "offline-response",
            "choices": [{"message": {"content": "Saved." if not calls else "", "tool_calls": calls or []}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def run_task(self, source):
        self.tasks.process_ai_response.run(str(self.conversation.pk), str(source.pk))

    def test_failed_confirmation_retry_has_one_schedule_outcome_and_delivery_charge(self):
        source = self.source()
        first = self.response([tool("create_reminder", self.args())])
        retry = self.response([tool("create_reminder", self.args(reminder_text="different retry wording"))])
        with mock.patch.object(
            self.tasks.CortecsClient,
            "chat_completion",
            side_effect=[
                first,
                RuntimeError("confirmation failed"),
                retry,
                self.response(),
            ],
        ), mock.patch.object(self.tasks.process_ai_response, "retry", side_effect=Retry("offline retry")):
            with self.assertRaises(Retry):
                self.run_task(source)
            self.assertEqual(Reminder.objects.filter(source_message=source).count(), 1)
            self.run_task(source)
        self.assertEqual(ReminderToolExecution.objects.filter(source_message_key=source.pk).count(), 1)
        self.assertEqual(Reminder.objects.filter(source_message=source).count(), 1)
        reminder = Reminder.objects.get(source_message=source)
        Reminder.objects.filter(pk=reminder.pk).update(due_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(ReminderService.check_and_send_due(), 1)
        self.assertEqual(ReminderService.check_and_send_due(), 0)
        self.assertEqual(self.paid_messages().count(), 1)
        self.assertEqual(self.balance(), 10000 - 2 * MESSAGE_PAIR_COST)  # chat + delivery

    def test_failed_confirmation_delayed_retry_after_cancel_cannot_recreate(self):
        source = self.source()
        offered = self.response([tool("create_reminder", self.args())])
        with mock.patch.object(
            self.tasks.CortecsClient,
            "chat_completion",
            side_effect=[
                offered,
                RuntimeError("confirmation failed"),
                offered,
                self.response(),
            ],
        ), mock.patch.object(self.tasks.process_ai_response, "retry", side_effect=Retry("offline retry")):
            with self.assertRaises(Retry):
                self.run_task(source)
            reminder = Reminder.objects.get(source_message=source)
            cancel_reminder(user=self.user, reminder_id=reminder.pk)
            self.run_task(source)
        self.assertFalse(Reminder.objects.exists())
        self.assertEqual(ReminderToolExecution.objects.filter(source_message_key=source.pk).count(), 1)
        self.assertEqual(self.balance(), 10000 - MESSAGE_PAIR_COST)

    def test_both_provider_branches_reject_unoffered_search_and_create(self):
        FeaturePreferences.update(self.user, {"reminders": False, "websearch": False})
        for use_byok in (False, True):
            with self.subTest(byok=use_byok):
                if use_byok:
                    self.byok()
                source = self.source()
                calls = [
                    tool("web_search", {"query": "weather"}),
                    tool("open_url", {"url": "https://invalid.test"}),
                    tool("create_reminder", self.args()),
                    tool("update_timezone", {"timezone": "Europe/Zurich"}),
                ]
                provider = self.tasks.BYOKService if use_byok else self.tasks.CortecsClient
                with mock.patch.object(
                    provider,
                    "chat_completion",
                    side_effect=[
                        self.response(calls),
                        self.response(),
                    ],
                ) as chat, mock.patch.object(self.tasks, "run_search_tool_calls") as search, mock.patch.object(
                    self.tasks, "run_open_url_tool_calls"
                ) as fetch:
                    self.run_task(source)
                offered = {spec["function"]["name"] for spec in chat.call_args_list[0].kwargs["tools"]}
                self.assertEqual(offered, {"update_timezone", "update_reminder", "cancel_reminder"})
                search.assert_not_called()
                fetch.assert_not_called()
                self.assertFalse(Reminder.objects.exists())
                self.assertTrue(
                    Message.objects.filter(client_operation_id=result_key(str(source.pk)), sender_type="ai").exists()
                )

    def test_both_provider_branches_recheck_permissions_after_model_latency(self):
        for use_byok in (False, True):
            with self.subTest(byok=use_byok):
                FeaturePreferences.update(self.user, {"reminders": True, "websearch": True})
                self.conversation.web_search_enabled = True
                self.conversation.save(update_fields=["web_search_enabled"])
                if use_byok:
                    self.byok()
                source = self.source()
                provider = self.tasks.BYOKService if use_byok else self.tasks.CortecsClient

                def completion(**kwargs):
                    if "tools" not in kwargs:
                        return self.response()
                    offered = {spec["function"]["name"] for spec in kwargs["tools"]}
                    self.assertTrue({"create_reminder", "web_search", "open_url"}.issubset(offered))
                    FeaturePreferences.update(
                        CustomUser.objects.get(pk=self.user.pk),
                        {
                            "reminders": False,
                            "websearch": False,
                        },
                    )
                    return self.response(
                        [
                            tool("create_reminder", self.args()),
                            tool("web_search", {"query": "weather"}),
                            tool("open_url", {"url": "https://invalid.test"}),
                        ]
                    )

                with mock.patch.object(provider, "chat_completion", side_effect=completion), mock.patch.object(
                    self.tasks, "run_search_tool_calls"
                ) as search, mock.patch.object(self.tasks, "run_open_url_tool_calls") as fetch:
                    self.run_task(source)
                search.assert_not_called()
                fetch.assert_not_called()
                self.assertFalse(Reminder.objects.exists())
                self.assertTrue(
                    Message.objects.filter(client_operation_id=result_key(str(source.pk)), sender_type="ai").exists()
                )

    def test_unoffered_only_response_with_null_content_is_a_relayable_denial(self):
        FeaturePreferences.update(self.user, {"reminders": False})
        for use_byok in (False, True):
            if use_byok:
                self.byok()
            source = self.source()
            response = self.response([tool("create_reminder", self.args())])
            response["choices"][0]["message"]["content"] = None
            provider = self.tasks.BYOKService if use_byok else self.tasks.CortecsClient
            with mock.patch.object(provider, "chat_completion", side_effect=[response, self.response()]) as chat:
                self.run_task(source)
            self.assertEqual(chat.call_count, 2)
            self.assertFalse(Reminder.objects.exists())
            self.assertTrue(
                Message.objects.filter(client_operation_id=result_key(str(source.pk)), sender_type="ai").exists()
            )

    def test_allowed_search_and_url_caps_are_preserved_by_ordered_dispatch(self):
        self.conversation.web_search_enabled = True
        self.conversation.save(update_fields=["web_search_enabled"])
        calls = [tool("web_search", {"query": "weather"}), tool("open_url", {"url": "https://invalid.test"})] * 5
        with self.settings(SEARCH_MAX_CALLS_PER_MESSAGE=2, OPEN_URL_MAX_CALLS_PER_MESSAGE=1), mock.patch.object(
            self.tasks, "run_search_tool_calls", return_value=("search result", 1)
        ) as search, mock.patch.object(self.tasks, "run_open_url_tool_calls", return_value=("page result", 1)) as fetch:
            result = self.dispatch(calls, {"web_search", "open_url"}, self.source())
        self.assertEqual(result[1:], (2, 1, 0))
        self.assertEqual(search.call_count, 2)
        self.assertEqual(fetch.call_count, 1)


class ReminderMigrationRegressionTests(ReminderRegressionBase, TransactionTestCase):
    def test_0086_backfills_logical_create_guard_without_deleting_existing_schedules(self):
        from django.db.migrations.executor import MigrationExecutor

        before = [("capp", "0085_user_feature_preferences")]
        source = self.source()
        executor = MigrationExecutor(connection)
        after = executor.loader.graph.leaf_nodes()
        executor.migrate(before)
        try:
            old_apps = executor.loader.project_state(before).apps
            OldReminder = old_apps.get_model("capp", "Reminder")
            keys = []
            for _ in range(2):
                old = OldReminder.objects.create(
                    user_id=self.user.pk,
                    character_id=self.character.pk,
                    conversation_id=self.conversation.pk,
                    source_message_id=source.pk,
                    reminder_text="legacy reminder",
                    due_at=timezone.now() + timedelta(days=2),
                    timezone_snapshot="Africa/Nairobi",
                )
                keys.append(old.pk)
        finally:
            # Restore even after an assertion/setup failure so the rest of the
            # suite always sees the declared current models and migration leaf.
            MigrationExecutor(connection).migrate(after)
        self.assertEqual(Reminder.objects.filter(pk__in=keys, revision=1).count(), 2)
        self.assertEqual(ReminderToolExecution.objects.filter(source_message_key=source.pk).count(), 1)
        replay, _ = self.execute([tool("create_reminder", self.args())], source)
        self.assertIn("Previously executed", replay)
        self.assertEqual(Reminder.objects.count(), 2)


@skipUnless(connection.vendor == "postgresql", "Row-lock regressions require PostgreSQL")
class ReminderPostgresConcurrencyTests(ReminderRegressionBase, TransactionTestCase):
    def worker(self, fn):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
            return fn()
        finally:
            connections.close_all()

    def test_owner_lock_enforces_cap_across_rest_and_tool(self):
        for _ in range(14):
            self.reminder(due_in=timedelta(days=2))
        start = threading.Barrier(2)
        source = self.source()

        def rest():
            start.wait(5)
            return (
                self.api(
                    api_create_reminder,
                    "post",
                    {
                        **self.args(),
                        "conversation_id": str(self.conversation.pk),
                    },
                ).status_code
                == 201
            )

        def chat():
            start.wait(5)
            return "saved successfully" in self.execute([tool("create_reminder", self.args())], source)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result(10) for f in [pool.submit(self.worker, rest), pool.submit(self.worker, chat)]]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(Reminder.objects.count(), 15)

    def test_concurrent_identical_source_creates_one_durable_effect(self):
        source = self.source()
        start = threading.Barrier(2)

        def create():
            start.wait(5)
            return self.execute([tool("create_reminder", self.args())], source)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result(10) for f in [pool.submit(self.worker, create), pool.submit(self.worker, create)]]
        self.assertTrue(all(r[1] == 1 for r in results))
        self.assertEqual(Reminder.objects.count(), 1)
        self.assertEqual(ReminderToolExecution.objects.count(), 1)

    def test_independent_concurrent_preference_patches_merge(self):
        start = threading.Barrier(2)

        def change(payload):
            user = CustomUser.objects.get(pk=self.user.pk)
            start.wait(5)
            return FeaturePreferences.update(user, payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.worker, lambda: change({"reminders": False})),
                pool.submit(self.worker, lambda: change({"websearch": False})),
            ]
            for future in futures:
                self.assertEqual(future.result(10)["status"], "success")
        self.user.refresh_from_db()
        self.assertFalse(FeaturePreferences.is_enabled(self.user, "reminders"))
        self.assertFalse(FeaturePreferences.is_enabled(self.user, "websearch"))

    def test_live_claim_prevents_overlapping_success_and_failure_provider_work(self):
        reminder = self.reminder()
        entered, release = threading.Event(), threading.Event()

        def generate(*args):
            self.assertFalse(connection.in_atomic_block)
            entered.set()
            if not release.wait(8):
                raise AssertionError("test did not release provider")
            raise RuntimeError("first worker failed")

        self.generate.side_effect = generate
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, lambda: ReminderService._deliver(reminder))
            try:
                self.assertTrue(entered.wait(8))
                second = pool.submit(self.worker, lambda: ReminderService._deliver(reminder))
                self.assertFalse(second.result(8))
            finally:
                release.set()
            self.assertFalse(first.result(8))
        self.assertEqual(self.generate.call_count, 1)
        self.generate.side_effect = None
        self.assertTrue(ReminderService._deliver(reminder))
        self.assertEqual(self.paid_messages().count(), 1)
        self.assertEqual(self.balance(), 10000 - MESSAGE_PAIR_COST)

    def test_expired_lease_worker_cannot_commit_or_refund_successor(self):
        reminder = self.reminder()
        entered, release = threading.Event(), threading.Event()

        def generate(*args):
            if not entered.is_set():
                entered.set()
                if not release.wait(8):
                    raise AssertionError("test did not release old provider")
                return "Stale response."
            return "Current response."

        self.generate.side_effect = generate
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, lambda: ReminderService._deliver(reminder))
            try:
                self.assertTrue(entered.wait(8))
                ReminderDeliveryAttempt.objects.filter(reminder_key=reminder.pk).update(
                    lease_until=timezone.now() - timedelta(seconds=1)
                )
                second = pool.submit(self.worker, lambda: ReminderService._deliver(reminder))
                self.assertTrue(second.result(8))
            finally:
                release.set()
            self.assertFalse(first.result(8))
        self.assertEqual(self.paid_messages().get().content, "Current response.")
        self.assertEqual(self.balance(), 10000 - MESSAGE_PAIR_COST)
        self.assertEqual(BillingReservation.objects.filter(status="settled").count(), 1)
        self.assertEqual(BillingReservation.objects.filter(status="refunded").count(), 1)

    def test_cancel_other_connection_acknowledges_while_provider_is_running(self):
        reminder = self.reminder()
        entered, release = threading.Event(), threading.Event()

        def generate(*args):
            entered.set()
            if not release.wait(8):
                raise AssertionError("test did not release provider")
            return "Stale response."

        self.generate.side_effect = generate
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, lambda: ReminderService._deliver(reminder))
            try:
                self.assertTrue(entered.wait(8))
                cancel = pool.submit(self.worker, lambda: self.api(api_delete_reminder, "delete", reminder=reminder))
                self.assertEqual(cancel.result(8).status_code, 200)
            finally:
                release.set()
            self.assertFalse(first.result(8))
        self.assertEqual(self.balance(), 10000)
        self.assertEqual(self.paid_messages().count(), 0)
