"""Targeted erasure of credentials and copied audit/support snapshots."""

import uuid
from datetime import timedelta

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from capp.classes.auth.auth import Auth
from capp.classes.auth.erasure_audit import scrub_audit_snapshots
from capp.models import (
    AdminActionLog,
    CharacterFlag,
    CharacterGalleryImage,
    Conversation,
    CustomUser,
    Message,
    PaymentTransaction,
    SparkTransaction,
    SupportTicket,
    TicketMessage,
    UsageCharge,
    UserBYOKConfig,
)
from capp.tests import _make_character, _make_user
from capp.views.admin_views import admin_audit_log
from paystack_app.models import PaystackTransaction


class ErasureAuditTests(TestCase):
    def setUp(self):
        self.user = _make_user("erasing", "erasing@example.com", is_staff=True)
        self.other = _make_user("remaining", "remaining@example.com", is_staff=True)

    def _character(self, name, *, public, owner=None):
        return _make_character(
            name=name,
            slug=f"erasure-{uuid.uuid4().hex}",
            is_public=public,
            created_by=owner or self.user,
        )

    def _entry(self, obj, *, content_type=None, object_id=None):
        return LogEntry.objects.create(
            user=self.other,
            content_type=content_type or ContentType.objects.get_for_model(obj, for_concrete_model=False),
            object_id=str(obj.pk) if object_id is None else str(object_id),
            object_repr="Sensitive object snapshot",
            change_message="Sensitive change snapshot",
            action_flag=CHANGE,
        )

    def _session(self, key, payload):
        return Session.objects.create(
            session_key=key,
            session_data=Session.objects.encode(payload),
            expire_date=timezone.now() + timedelta(days=1),
        )

    def test_audit_api_retains_event_with_null_erased_administrator(self):
        self.other.is_superuser = True
        self.other.save(update_fields=["is_superuser"])
        event = AdminActionLog.objects.create(
            admin_user=self.user,
            target_user=self.other,
            action_type=AdminActionLog.ActionType.SPARK_ADJUSTMENT,
            reason="Private administrator narrative",
            metadata={"spark_delta": 10},
        )
        control = AdminActionLog.objects.create(
            admin_user=self.other,
            action_type=AdminActionLog.ActionType.USER_STATUS_CHANGE,
        )

        self.assertEqual(Auth.hard_delete_user(self.user)["status"], "success")
        request = APIRequestFactory().get("/api/v1/admin/audit-log/", {"source": "admin_action"})
        force_authenticate(request, user=self.other)
        response = admin_audit_log(request)

        self.assertEqual(response.status_code, 200)
        events = {item["log_id"]: item for item in response.data["data"]["events"]}
        self.assertIsNone(events[str(event.pk)]["admin_user"])
        self.assertIsNone(events[str(event.pk)]["reason"])
        self.assertEqual(events[str(event.pk)]["target_user"]["user_id"], str(self.other.pk))
        self.assertEqual(events[str(control.pk)]["admin_user"]["user_id"], str(self.other.pk))

    def _report(self, character, *, reporter=None):
        reporter = reporter or self.other
        flag = CharacterFlag.objects.create(character=character, reported_by=reporter, reason="Reporter explanation")
        description = (
            f"Flagged Character:\n{character.name}\n\n"
            f"Reason: {flag.reason}\n\n"
            f"Flagged by: {reporter.get_display_name()} ({reporter.username})\n"
            f"Character ID: {character.pk}\n"
            f"Character Slug: {character.slug}\n"
            f"Created by: {character.created_by.get_display_name()}"
        )
        ticket = SupportTicket.objects.create(
            created_by=reporter,
            title=f"Reported character: {character.name}",
            description=description,
            metadata={"flag_id": str(flag.pk), "workflow_tag": "preserve"},
            status=SupportTicket.Status.IN_PROGRESS,
        )
        initial = TicketMessage.objects.create(ticket=ticket, sender=reporter, content=description)
        return flag, ticket, initial

    def test_clear_raw_jwts_preserving_blacklist_expiry_and_unrelated_token(self):
        refresh = RefreshToken.for_user(self.user)
        old_refresh = str(refresh)
        token = OutstandingToken.objects.get(jti=refresh["jti"])
        blacklisted_refresh = RefreshToken.for_user(self.user)
        blacklisted_refresh.blacklist()
        blacklisted = OutstandingToken.objects.get(jti=blacklisted_refresh["jti"])
        blacklist = BlacklistedToken.objects.get(token=blacklisted)
        other_refresh = RefreshToken.for_user(self.other)
        other_token = OutstandingToken.objects.get(jti=other_refresh["jti"])
        original_facts = (token.jti, token.created_at, token.expires_at)

        with transaction.atomic():
            scrub_audit_snapshots(self.user)
            self.user.delete()

        token.refresh_from_db()
        blacklisted.refresh_from_db()
        other_token.refresh_from_db()
        self.assertEqual(token.token, "")
        self.assertEqual(blacklisted.token, "")
        self.assertIsNone(token.user_id)
        self.assertEqual((token.jti, token.created_at, token.expires_at), original_facts)
        self.assertTrue(BlacklistedToken.objects.filter(pk=blacklist.pk, token=blacklisted).exists())
        self.assertEqual(other_token.token, str(other_refresh))
        self.assertEqual(other_token.user_id, self.other.pk)
        result = Auth.refresh_access_token(old_refresh)
        self.assertEqual(result, {"status": "fail", "data": "user_not_found"})

    def test_delete_only_sessions_with_exact_authenticated_user_id(self):
        own = self._session("own-admin-session", {"_auth_user_id": str(self.user.pk), "secret": "private"})
        other = self._session("other-admin-session", {"_auth_user_id": str(self.other.pk)})
        anonymous = self._session("anonymous-session", {"note": str(self.user.pk)})
        malformed = Session.objects.create(
            session_key="unreadable-session",
            session_data="not-valid-session-data",
            expire_date=timezone.now() + timedelta(days=1),
        )
        # Matching must be the auth field, not any occurrence in encoded data.
        scrub_audit_snapshots(self.user)
        self.assertFalse(Session.objects.filter(pk=own.pk).exists())
        for session in (other, anonymous, malformed):
            self.assertTrue(Session.objects.filter(pk=session.pk).exists())

    def test_session_cleanup_streams_more_than_one_chunk(self):
        rows = [
            Session(
                session_key=f"owned-session-{index}",
                session_data=Session.objects.encode({"_auth_user_id": str(self.user.pk)}),
                expire_date=timezone.now() + timedelta(days=1),
            )
            for index in range(205)
        ]
        Session.objects.bulk_create(rows)
        other = self._session("retain-other-session", {"_auth_user_id": str(self.other.pk)})
        scrub_audit_snapshots(self.user)
        self.assertEqual(list(Session.objects.values_list("pk", flat=True)), [other.pk])

    def test_admin_snapshots_require_both_exact_content_type_and_owned_object(self):
        config = UserBYOKConfig.objects.create(
            user=self.user, api_url="https://provider.invalid/v1", api_key_encrypted="private", model_name="model"
        )
        private = self._character("Private name", public=False)
        public = self._character("Public fiction", public=True)
        other_private = self._character("Other private name", public=False, owner=self.other)
        other_public = self._character("Other public fiction", public=True, owner=self.other)
        conversation = Conversation.objects.create(user=self.user, character=public)
        message = Message.objects.create(
            conversation=conversation, sender_type=Message.SenderType.USER, content="Private chat"
        )
        other_conversation = Conversation.objects.create(user=self.other, character=private)
        private_gallery = CharacterGalleryImage.objects.create(
            character=public, created_by=self.user, visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE
        )
        private_character_gallery = CharacterGalleryImage.objects.create(
            character=private, created_by=self.other, visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC
        )
        public_gallery = CharacterGalleryImage.objects.create(character=public, created_by=self.user)
        other_public_gallery = CharacterGalleryImage.objects.create(character=public, created_by=self.other)
        spark = SparkTransaction.objects.create(
            user=self.user, entry_type=SparkTransaction.EntryType.CREDIT, source=SparkTransaction.SourceChoices.PAYMENT
        )
        payment = PaymentTransaction.objects.create(
            user=self.user, amount=100, payment_method="card", purpose="spark_purchase"
        )
        usage = UsageCharge.objects.create(user=self.user, charge_source="spark_pool", sparks_charged=25)
        paystack = PaystackTransaction.objects.create(user=self.user, reference="audit-financial", amount=100)
        targeted = [
            self._entry(obj)
            for obj in (
                self.user,
                config,
                private,
                public,
                conversation,
                message,
                private_gallery,
                private_character_gallery,
                public_gallery,
                spark,
                payment,
                usage,
                paystack,
            )
        ]
        unrelated = [
            self._entry(obj)
            for obj in (self.other, other_private, other_public, other_conversation, other_public_gallery)
        ]
        # An identical object_id under a different content type is not a match.
        unrelated.append(
            self._entry(
                self.user,
                content_type=ContentType.objects.get_for_model(CharacterFlag),
                object_id=self.user.pk,
            )
        )
        unrelated.append(self._entry(self.user, object_id=uuid.uuid4()))
        before = [(entry.pk, entry.action_flag, entry.action_time, entry.object_id) for entry in targeted]

        scrub_audit_snapshots(self.user)

        for entry in targeted:
            entry.refresh_from_db()
            self.assertEqual(entry.object_repr, "Erased account content")
            self.assertEqual(entry.change_message, "")
            self.assertIn((entry.pk, entry.action_flag, entry.action_time, entry.object_id), before)
        for entry in unrelated:
            entry.refresh_from_db()
            self.assertEqual(entry.object_repr, "Sensitive object snapshot")
            self.assertEqual(entry.change_message, "Sensitive change snapshot")
        self.assertTrue(Conversation.objects.filter(pk=other_conversation.pk).exists())
        public.refresh_from_db()
        public_gallery.refresh_from_db()
        payment.refresh_from_db()
        usage.refresh_from_db()
        paystack.refresh_from_db()
        self.assertEqual(public.name, "Public fiction")
        self.assertEqual(public.created_by_id, self.user.pk)
        self.assertEqual(public_gallery.created_by_id, self.user.pk)
        self.assertEqual(payment.amount, 100)
        self.assertEqual(usage.sparks_charged, 25)
        self.assertEqual(paystack.reference, "audit-financial")

    def test_public_creator_snapshot_redacted_but_report_and_later_updates_preserved(self):
        character = self._character("Public persona", public=True)
        flag, ticket, initial = self._report(character)
        original_description = ticket.description
        reply = TicketMessage.objects.create(
            ticket=ticket, sender=self.other, parent=initial, content="A later reporter update"
        )
        repeated_root = TicketMessage.objects.create(ticket=ticket, sender=self.other, content=original_description)
        unrelated_character = self._character("Someone else's character", public=True, owner=self.other)
        _, unrelated, unrelated_initial = self._report(unrelated_character)
        unrelated_description = unrelated.description

        scrub_audit_snapshots(self.user)

        ticket.refresh_from_db()
        initial.refresh_from_db()
        reply.refresh_from_db()
        repeated_root.refresh_from_db()
        unrelated.refresh_from_db()
        unrelated_initial.refresh_from_db()
        self.assertNotIn(f"Created by: {self.user.get_display_name()}", ticket.description)
        self.assertTrue(ticket.description.endswith("Created by: Deleted account"))
        self.assertEqual(initial.content, ticket.description)
        self.assertIn(f"Reason: {flag.reason}", ticket.description)
        self.assertEqual(ticket.title, f"Reported character: {character.name}")
        self.assertEqual(ticket.created_by_id, self.other.pk)
        self.assertEqual(ticket.status, SupportTicket.Status.IN_PROGRESS)
        self.assertEqual(ticket.metadata, {"flag_id": str(flag.pk), "workflow_tag": "preserve"})
        self.assertEqual(reply.content, "A later reporter update")
        self.assertEqual(repeated_root.content, original_description)
        self.assertEqual(unrelated.description, unrelated_description)
        self.assertEqual(unrelated_initial.content, unrelated_description)

    def test_private_report_copies_erased_without_overwriting_updated_ticket(self):
        character = self._character("Private identifying persona", public=False)
        _, ticket, initial = self._report(character)
        original_title = ticket.title
        ticket.description = "A manually updated case description"
        ticket.save(update_fields=["description"])
        reply = TicketMessage.objects.create(ticket=ticket, sender=self.other, content="Preserve this update")

        scrub_audit_snapshots(self.user)

        ticket.refresh_from_db()
        initial.refresh_from_db()
        reply.refresh_from_db()
        self.assertNotEqual(ticket.title, original_title)
        self.assertEqual(ticket.title, "Reported private character (archived)")
        self.assertEqual(ticket.description, "A manually updated case description")
        self.assertNotIn(character.name, initial.content)
        self.assertNotIn(character.slug, initial.content)
        self.assertNotIn(self.user.get_display_name(), initial.content)
        self.assertIn(str(character.pk), initial.content)
        self.assertEqual(reply.content, "Preserve this update")

    def test_multiline_creator_snapshot_is_removed_in_full(self):
        self.user.display_name = "Private creator\nCreated by: another private name"
        self.user.save(update_fields=["display_name"])
        character = self._character("Public fiction", public=True)
        _, ticket, initial = self._report(character)
        scrub_audit_snapshots(self.user)
        ticket.refresh_from_db()
        initial.refresh_from_db()
        for text in (ticket.description, initial.content):
            self.assertNotIn("Private creator", text)
            self.assertNotIn("another private name", text)
            self.assertEqual(text.count("Created by:"), 1)
            self.assertTrue(text.endswith("Created by: Deleted account"))

    def test_private_report_default_description_and_initial_message_are_both_scrubbed(self):
        character = self._character("Private person", public=False)
        _, ticket, initial = self._report(character)
        scrub_audit_snapshots(self.user)
        ticket.refresh_from_db()
        initial.refresh_from_db()
        self.assertEqual(ticket.description, initial.content)
        self.assertNotIn(character.name, ticket.description)
        self.assertEqual(ticket.title, "Reported private character (archived)")
        # Repeated calls do not broaden redaction or discard the case.
        original = ticket.description
        scrub_audit_snapshots(self.user)
        ticket.refresh_from_db()
        self.assertEqual(ticket.description, original)

    def test_metadata_link_alone_does_not_overwrite_unrelated_ticket_text(self):
        character = self._character("Private person", public=False)
        flag = CharacterFlag.objects.create(character=character, reported_by=self.other, reason="Reason")
        ticket = SupportTicket.objects.create(
            created_by=self.other,
            title="Manually maintained case",
            description="Unrelated workflow content",
            metadata={"flag_id": str(flag.pk)},
        )
        initial = TicketMessage.objects.create(ticket=ticket, sender=self.other, content="Original manual message")
        scrub_audit_snapshots(self.user)
        ticket.refresh_from_db()
        initial.refresh_from_db()
        self.assertEqual(ticket.description, "Unrelated workflow content")
        self.assertEqual(ticket.title, "Manually maintained case")
        self.assertEqual(initial.content, "Original manual message")

    def test_erased_admin_events_keep_typed_financial_facts_and_survive_user_delete(self):
        ledger_id = uuid.uuid4()
        facts = {
            "spark_delta": -25,
            "sparks_before": 100,
            "sparks_after": 75,
            "spark_transaction_id": str(ledger_id),
        }
        action = AdminActionLog.objects.create(
            admin_user=self.user,
            target_user=self.other,
            action_type=AdminActionLog.ActionType.SPARK_ADJUSTMENT,
            reason="Private author note",
            metadata={**facts, "admin_username": self.user.username, "description": "Private description"},
        )
        unrelated = AdminActionLog.objects.create(
            admin_user=self.other,
            target_user=self.other,
            action_type=AdminActionLog.ActionType.USER_STATUS_CHANGE,
            reason="Unrelated reason",
            metadata={"old_status": "active", "new_status": "suspended"},
        )
        original_time = action.created_at
        with transaction.atomic():
            scrub_audit_snapshots(self.user)
            self.user.delete()

        action.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertIsNone(action.admin_user_id)
        self.assertIsNone(action.reason)
        self.assertEqual(action.target_user_id, self.other.pk)
        self.assertEqual(action.metadata, facts)
        self.assertEqual(action.created_at, original_time)
        self.assertIn("Deleted account", str(action))
        self.assertEqual(unrelated.reason, "Unrelated reason")
        self.assertEqual(unrelated.metadata, {"old_status": "active", "new_status": "suspended"})

    def test_private_target_action_snapshot_is_scrubbed_without_deleting_event(self):
        character = self._character("Private snapshot", public=False)
        action = AdminActionLog.objects.create(
            admin_user=self.other,
            action_type=AdminActionLog.ActionType.CHARACTER_HARD_DELETE,
            target_character_id=character.pk,
            target_character_name=character.name,
            reason="Private narrative",
            metadata={"cascaded": {"gallery_images": 3, "name": "Private snapshot"}},
        )
        scrub_audit_snapshots(self.user)
        action.refresh_from_db()
        self.assertIsNone(action.target_character_name)
        self.assertIsNone(action.reason)
        self.assertEqual(action.metadata, {"cascaded": {"gallery_images": 3}})
        self.assertEqual(action.admin_user_id, self.other.pk)
        self.assertEqual(action.target_character_id, character.pk)

    def test_target_account_erasure_preserves_typed_admin_financial_metadata(self):
        facts = {"spark_delta": 10, "sparks_before": 50, "sparks_after": 60}
        action = AdminActionLog.objects.create(
            admin_user=self.other,
            target_user=self.user,
            action_type=AdminActionLog.ActionType.SPARK_ADJUSTMENT,
            reason="Private target details",
            metadata={**facts, "description": "Private target details"},
        )
        scrub_audit_snapshots(self.user)
        action.refresh_from_db()
        self.assertEqual(action.metadata, facts)
        self.assertIsNone(action.reason)
        self.assertEqual(action.admin_user_id, self.other.pk)

    def test_snapshot_and_session_cleanup_roll_back_with_account_transaction(self):
        refresh = RefreshToken.for_user(self.user)
        token = OutstandingToken.objects.get(jti=refresh["jti"])
        session = self._session("rollback-admin-session", {"_auth_user_id": str(self.user.pk)})
        entry = self._entry(self.user)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                scrub_audit_snapshots(self.user)
                raise RuntimeError("Abort deletion")
        token.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(token.token, str(refresh))
        self.assertTrue(Session.objects.filter(pk=session.pk).exists())
        self.assertEqual(entry.object_repr, "Sensitive object snapshot")
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())
