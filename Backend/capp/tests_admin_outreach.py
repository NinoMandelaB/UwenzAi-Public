# capp/tests_admin_outreach.py
"""
Admin-initiated outreach: a support ticket opened on the user's behalf.

High-priority safety situations (care events, critical/high severity) need the
admin to reach the user directly instead of waiting for them to open a ticket.
These tests pin the endpoint contract, the ticket shape (created_by the target
user, first message from the admin, HIGH priority, IN_PROGRESS), the user-side
experience (their Support inbox shows the ticket as unread and they can reply),
the safety-summary gate flag, and the permission boundaries.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from capp.classes.support.support_service import SupportService
from capp.models import SupportTicket, TicketMessage, TicketView
from capp.tests import _make_user
from capp.tests_admin_users_safety_flag import _event

ADMIN_URL = "/api/v1/admin/users/{user_id}/message/"
SAFETY_URL = "/api/v1/admin/users/{user_id}/safety/"


class OutreachBaseTestCase(TestCase):
    """Shared fixtures: one admin, one regular user, authenticated clients."""

    def setUp(self):
        self.admin = _make_user(username="outreach_admin", email="outreach_admin@example.com", is_staff=True)
        self.alice = _make_user(username="alice_out", email="alice_out@example.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.user_client = APIClient()
        self.user_client.force_authenticate(user=self.alice)


class AdminOutreachEndpointTests(OutreachBaseTestCase):
    def test_creates_ticket_on_behalf_of_user(self):
        response = self.client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "We noticed you might be going through a hard time. Would you like to talk?"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        ticket = SupportTicket.objects.get(ticket_id=response.json()["data"]["ticket"]["ticket_id"])
        # The ticket belongs to the user: it appears in THEIR Support inbox.
        self.assertEqual(ticket.created_by, self.alice)
        # First message is from the admin, not the user.
        first = ticket.messages.first()
        self.assertEqual(first.sender, self.admin)
        self.assertIn("hard time", first.content)
        # Outreach is high priority, in progress, and owned by the sender.
        self.assertEqual(ticket.priority, SupportTicket.Priority.HIGH)
        self.assertEqual(ticket.status, SupportTicket.Status.IN_PROGRESS)
        self.assertEqual(ticket.assigned_to, self.admin)
        self.assertTrue(ticket.metadata.get("initiated_by_admin"))
        self.assertEqual(ticket.metadata.get("initiated_by_admin_id"), str(self.admin.user_id))

    def test_default_title_and_custom_title(self):
        response = self.client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SupportTicket.objects.first().title, SupportService.DEFAULT_OUTREACH_TITLE)

        response = self.client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "hello again", "title": "Checking in"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SupportTicket.objects.filter(title="Checking in").count(), 1)

    def test_empty_message_is_refused(self):
        response = self.client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "   "},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "message_required")
        self.assertEqual(SupportTicket.objects.count(), 0)

    def test_missing_user_is_404(self):
        response = self.client.post(
            ADMIN_URL.format(user_id="00000000-0000-0000-0000-000000000000"),
            {"message": "hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["data"], "user_not_found")

    def test_non_admin_is_forbidden(self):
        response = self.user_client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_own_outreach_is_not_unread_for_the_admin(self):
        response = self.client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "hello"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        ticket_id = response.json()["data"]["ticket"]["ticket_id"]
        # The sending admin immediately has a read-marker for the ticket...
        self.assertTrue(TicketView.objects.filter(user=self.admin, ticket__ticket_id=ticket_id).exists())
        # ...so their global unread count stays zero.
        unread = SupportService.get_unread_count(self.admin)
        self.assertEqual(unread["data"]["count"], 0)


class OutreachUserExperienceTests(OutreachBaseTestCase):
    def _outreach(self):
        response = self.client.post(
            ADMIN_URL.format(user_id=self.alice.user_id),
            {"message": "Would you like to talk? We are here for you."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]["ticket"]

    def test_user_sees_ticket_in_their_inbox_as_unread(self):
        self._outreach()
        response = self.user_client.get("/api/v1/support/tickets/")
        self.assertEqual(response.status_code, 200)
        tickets = response.json()["data"]["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertGreaterEqual(tickets[0]["unread_count"], 1)
        # The user's global unread badge picks it up too.
        unread = self.user_client.get("/api/v1/support/unread-count/")
        self.assertEqual(unread.json()["data"]["count"], 1)

    def test_user_can_read_and_reply(self):
        ticket = self._outreach()
        detail = self.user_client.get(f"/api/v1/support/tickets/{ticket['ticket_id']}/")
        self.assertEqual(detail.status_code, 200)
        messages = detail.json()["data"]["ticket"]["messages"]
        self.assertEqual(len(messages), 1)
        # The admin's message is marked as coming from the support team.
        self.assertTrue(messages[0]["sender"]["is_admin"])
        self.assertEqual(messages[0]["sender"]["user_id"], str(self.admin.user_id))

        reply = self.user_client.post(
            f"/api/v1/support/tickets/{ticket['ticket_id']}/reply/",
            {"content": "Thank you, I appreciate it."},
            format="json",
        )
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(
            TicketMessage.objects.filter(sender=self.alice, content="Thank you, I appreciate it.").count(), 1
        )


class OutreachGateFlagTests(OutreachBaseTestCase):
    def safety_summary(self, user):
        response = self.client.get(SAFETY_URL.format(user_id=user.user_id))
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["summary"]

    def test_summary_includes_flag_false_without_events(self):
        self.assertFalse(self.safety_summary(self.alice)["has_priority_events"])

    def test_flag_true_for_care_or_critical_or_high(self):
        _event(self.alice, category="self_harm", severity="low", is_care=True)
        self.assertTrue(self.safety_summary(self.alice)["has_priority_events"])

    def test_flag_true_for_high_severity(self):
        _event(self.alice, category="weapons_instructions", severity="high")
        self.assertTrue(self.safety_summary(self.alice)["has_priority_events"])

    def test_flag_false_for_low_medium_or_advisory_only(self):
        _event(self.alice, severity="low")
        _event(self.alice, category="violence_planning", severity="medium")
        self.assertFalse(self.safety_summary(self.alice)["has_priority_events"])

    def test_flag_false_for_reversed_events(self):
        event = _event(self.alice, category="self_harm", severity="high")
        event.is_reversed = True
        event.save(update_fields=["is_reversed"])
        self.assertFalse(self.safety_summary(self.alice)["has_priority_events"])

    def test_matches_attention_level_priority(self):
        """The detail-page gate must agree with the Users-list flag."""
        _event(self.alice, category="csam", severity="critical")
        self.assertTrue(self.safety_summary(self.alice)["has_priority_events"])
        # Same definition as the list flag: a care event alone also qualifies.
        _event(self.alice, category="self_harm", severity="low", is_care=True)
        self.assertTrue(self.safety_summary(self.alice)["has_priority_events"])


class OutreachPushTests(OutreachBaseTestCase):
    """The target user gets a content-free web push for the outreach message."""

    def test_outreach_schedules_push_for_the_target_user_on_commit(self):
        from unittest import mock

        from capp.tasks import send_support_message_push as push_task

        with mock.patch.object(push_task, "delay") as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    ADMIN_URL.format(user_id=self.alice.user_id),
                    {"message": "hello"},
                    format="json",
                )
        self.assertEqual(response.status_code, 201)
        # Scheduled exactly once, for the TARGET user (not the admin).
        mock_delay.assert_called_once_with(str(self.alice.user_id))

    def test_push_scheduling_failure_never_breaks_ticket_creation(self):
        from unittest import mock

        from capp.tasks import send_support_message_push as push_task

        with mock.patch.object(push_task, "delay", side_effect=Exception("broker down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    ADMIN_URL.format(user_id=self.alice.user_id),
                    {"message": "hello"},
                    format="json",
                )
        # The ticket exists even though the push could not be scheduled.
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SupportTicket.objects.count(), 1)

    def test_push_payload_is_content_free(self):
        from unittest import mock

        from capp.classes.push.push_service import PushService

        with mock.patch.object(PushService, "send_to_user", return_value=1) as mock_send:
            PushService.send_support_message_push(self.alice)
        mock_send.assert_called_once_with(
            self.alice,
            title="UwenzAi",
            body="You have a new support message.",
            url="/support",
        )
        # No unread_count kwarg: that field drives the chat badge.
        self.assertNotIn("unread_count", mock_send.call_args.kwargs)

    def test_push_body_follows_preferred_language(self):
        from unittest import mock

        from capp.classes.push.push_service import PushService

        self.alice.preferred_language = "sw"
        self.alice.save(update_fields=["preferred_language"])
        with mock.patch.object(PushService, "send_to_user", return_value=1) as mock_send:
            PushService.send_support_message_push(self.alice)
        self.assertEqual(mock_send.call_args.kwargs["body"], "Una ujumbe mpya wa usaidizi.")

    def test_push_task_survives_delivery_failure_and_missing_user(self):
        from capp.tasks import send_support_message_push as push_task

        # Missing user: logged and skipped, not a crash.
        self.assertEqual(push_task("00000000-0000-0000-0000-000000000000"), 0)
        # Delivery failure: logged and swallowed — the ticket is already safe.
        from unittest import mock

        with mock.patch(
            "capp.classes.push.push_service.PushService.send_support_message_push",
            side_effect=Exception("endpoint unreachable"),
        ):
            self.assertEqual(push_task(str(self.alice.user_id)), 0)
