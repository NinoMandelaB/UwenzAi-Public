"""Deterministic erasure races at the chat provider/API persistence boundaries.

These tests use real erasure, message and billing services; only provider work
and delivery are stubbed. They do not model PostgreSQL thread scheduling.
"""

import uuid
from contextlib import ExitStack
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from capp import tasks
from capp.classes.auth.auth import Auth
from capp.classes.billing.idempotency import result_key
from capp.classes.guest.guest_claim_service import GuestClaimService
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.constants import MESSAGE_PAIR_COST
from capp.models import (
    BillingReservation,
    Character,
    Conversation,
    CustomUser,
    GuestIdentity,
    GuestTokenQuota,
    GuestTransferGrant,
    Message,
    SparkPool,
    SparkTransaction,
    TokenUsageLog,
)
from capp.tests import _make_character, _make_user


class ErasureChatFenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user("remaining-chatter", "remaining-chatter@example.test")
        self.creator = _make_user("erasing-creator", "erasing-creator@example.test")
        self.user.feature_preferences = {}
        self.user.save(update_fields=["feature_preferences"])
        self.character = _make_character(
            name="Private fiction",
            slug="erasure-chat-character",
            is_public=False,
            created_by=self.creator,
        )
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.pool = SparkPool.objects.create(user=self.user, sparks=1000, is_active=True)
        self.operation_id = str(uuid.uuid4())
        self.source = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="Hello.",
            language_code="en",
            status=Message.StatusChoices.SENT,
            client_operation_id=self.operation_id,
        )
        self.access_token = str(AccessToken.for_user(self.user))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            mock.patch.object(
                tasks.PromptBuilder,
                "build_messages",
                return_value=[
                    {"role": "system", "content": "Offline system prompt."},
                    {"role": "user", "content": "Hello."},
                ],
            )
        )
        self.stack.enter_context(
            mock.patch.object(tasks.PromptBuilder, "enforce_budget", side_effect=lambda messages, **kw: messages)
        )
        self.stack.enter_context(mock.patch.object(tasks.ProactiveChatService, "reset_counter"))
        self.stack.enter_context(mock.patch.object(tasks, "chain_sub_tasks"))
        self.unread = self.stack.enter_context(mock.patch.object(tasks.unread_service, "broadcast_unread"))
        self.push = self.stack.enter_context(mock.patch.object(tasks.PushService, "send_new_message_push"))
        self.send_ai = self.stack.enter_context(mock.patch.object(tasks, "_send_ai_message"))
        self.send_system = self.stack.enter_context(mock.patch.object(tasks, "_send_system_message"))
        self.send_offer = self.stack.enter_context(mock.patch.object(tasks, "_send_photo_offer_message"))
        self.provider = self.stack.enter_context(
            mock.patch.object(tasks.CortecsClient, "chat_completion", return_value=self._completion())
        )

    def _completion(self, content="A completed answer."):
        return {
            "id": "offline-erasure-chat",
            "choices": [{"finish_reason": "stop", "message": {"content": content, "tool_calls": []}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def _run(self, **kwargs):
        tasks.process_ai_response(str(self.conversation.pk), str(self.source.pk), **kwargs)

    def _send(self, *, detect_effect=None):
        operation_id = str(uuid.uuid4())
        with mock.patch(
            "capp.views.conversation_views.LanguageDetector.detect",
            return_value="en",
            side_effect=detect_effect,
        ), mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = self.client.post(
                f"/api/v1/conversations/{self.conversation.pk}/messages/",
                data={"content": "New request."},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.access_token}",
                HTTP_IDEMPOTENCY_KEY=operation_id,
            )
        return response, dispatch, operation_id

    def _archive_character(self):
        Character.objects.filter(pk=self.character.pk).update(status=Character.StatusChoices.ARCHIVED, is_active=False)

    def _erase_creator(self):
        result = Auth.hard_delete_user(self.creator)
        self.assertEqual(result["status"], "success", result)

    def _reserve(self):
        return tasks._reserve_message_pair(
            conversation=self.conversation,
            operation_id=self.operation_id,
            user=self.user,
            guest_session_id=None,
            cost_override=None,
        )

    def _assert_no_reply(self):
        self.assertFalse(
            Message.objects.filter(conversation_id=self.conversation.pk, sender_type=Message.SenderType.AI).exists()
        )
        self.assertFalse(TokenUsageLog.objects.filter(conversation_id=self.conversation.pk).exists())
        self.send_ai.assert_not_called()
        self.send_offer.assert_not_called()
        self.push.assert_not_called()

    def _assert_refunded(self):
        reservation = BillingReservation.objects.get(idempotency_key=self.operation_id)
        self.assertEqual(reservation.status, BillingReservation.Status.REFUNDED)
        self.assertEqual(reservation.refund_reason, "chat_no_longer_writable")
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.sparks, 1000)
        self.assertEqual(
            SparkTransaction.objects.filter(user=self.user, source=SparkTransaction.SourceChoices.REFUND).count(),
            1,
        )
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, Message.StatusChoices.FAILED)
        self._assert_no_reply()

    def test_initial_archived_character_stops_before_provider_or_reservation(self):
        # Status alone is sufficient, even if an old row still says active.
        Character.objects.filter(pk=self.character.pk).update(status=Character.StatusChoices.ARCHIVED)
        self._run()
        self.provider.assert_not_called()
        self.assertFalse(BillingReservation.objects.exists())
        self._assert_no_reply()
        self.source.refresh_from_db()
        self.assertEqual(self.source.error_message, "character_unavailable")

    def test_initial_inactive_character_stops_before_provider_or_reservation(self):
        Character.objects.filter(pk=self.character.pk).update(is_active=False)
        self._run()
        self.provider.assert_not_called()
        self.assertFalse(BillingReservation.objects.exists())
        self._assert_no_reply()

    def test_initial_closed_conversation_refunds_existing_hold_once(self):
        self._reserve()
        Conversation.objects.filter(pk=self.conversation.pk).update(status=Conversation.StatusChoices.CLOSED)
        self._run()
        self._run()
        self.provider.assert_not_called()
        self._assert_refunded()

    def test_creator_erasure_closes_user_archived_history_and_blocks_api_and_queued_reply(self):
        Conversation.objects.filter(pk=self.conversation.pk).update(status=Conversation.StatusChoices.ARCHIVED)
        self._reserve()
        self._erase_creator()
        self.conversation.refresh_from_db()
        self.character.refresh_from_db()
        self.assertEqual(self.conversation.status, Conversation.StatusChoices.CLOSED)
        self.assertEqual(self.conversation.user_id, self.user.pk)
        self.assertEqual(self.character.status, Character.StatusChoices.ARCHIVED)
        self.assertFalse(self.character.is_active)
        self.assertIsNone(self.character.created_by_id)
        response, dispatch, operation_id = self._send()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "conversation_closed")
        dispatch.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=operation_id).exists())
        self._run()
        self.provider.assert_not_called()
        self._assert_refunded()
        self.assertEqual(self.source.content, "Hello.")

    def test_erasure_during_provider_discards_reply_and_refunds_surviving_chatter(self):
        def erase_then_complete(**kwargs):
            self._erase_creator()
            return self._completion()

        self.provider.side_effect = erase_then_complete
        self._run()
        self.provider.assert_called_once()
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Conversation.StatusChoices.CLOSED)
        self.assertTrue(CustomUser.objects.filter(pk=self.user.pk).exists())
        self._assert_refunded()

    def test_character_archived_during_provider_is_rechecked_even_if_conversation_stays_active(self):
        def archive_then_complete(**kwargs):
            self._archive_character()
            return self._completion()

        self.provider.side_effect = archive_then_complete
        self._run()
        self.provider.assert_called_once()
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Conversation.StatusChoices.ACTIVE)
        self._assert_refunded()

    def test_conversation_closed_during_provider_is_rechecked_even_with_active_character(self):
        def close_then_complete(**kwargs):
            Conversation.objects.filter(pk=self.conversation.pk).update(status=Conversation.StatusChoices.CLOSED)
            return self._completion()

        self.provider.side_effect = close_then_complete
        self._run()
        self.provider.assert_called_once()
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_active)
        self._assert_refunded()

    def test_own_account_erased_during_provider_does_not_recreate_public_chat_or_pool(self):
        Character.objects.filter(pk=self.character.pk).update(is_public=True, created_by=self.user)

        def erase_then_complete(**kwargs):
            result = Auth.hard_delete_user(self.user)
            self.assertEqual(result["status"], "success", result)
            return self._completion()

        self.provider.side_effect = erase_then_complete
        self._run()
        self.provider.assert_called_once()
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Conversation.objects.filter(pk=self.conversation.pk).exists())
        self.assertFalse(Message.objects.filter(pk=self.source.pk).exists())
        self.assertFalse(SparkPool.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(BillingReservation.objects.filter(idempotency_key=self.operation_id).exists())
        self.character.refresh_from_db()
        self.assertTrue(self.character.is_public)
        self.assertTrue(self.character.is_active)
        self.assertIsNone(self.character.created_by_id)
        self._assert_no_reply()

    def test_independently_archived_conversation_with_active_character_remains_writable(self):
        Conversation.objects.filter(pk=self.conversation.pk).update(status=Conversation.StatusChoices.ARCHIVED)
        response, dispatch, operation_id = self._send()
        self.assertEqual(response.status_code, 201)
        dispatch.assert_called_once()
        self.assertTrue(Message.objects.filter(client_operation_id=operation_id).exists())
        self._run()
        self.provider.assert_called_once()
        reply = Message.objects.get(conversation=self.conversation, client_operation_id=result_key(self.operation_id))
        self.assertEqual(reply.content, "A completed answer.")
        reservation = BillingReservation.objects.get(idempotency_key=self.operation_id)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.sparks, 1000 - MESSAGE_PAIR_COST)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Conversation.StatusChoices.ARCHIVED)

    def test_api_rejects_initial_retired_character_and_closed_conversation(self):
        cases = [
            (Character.StatusChoices.ARCHIVED, True, Conversation.StatusChoices.ACTIVE, "character_unavailable"),
            (Character.StatusChoices.APPROVED, False, Conversation.StatusChoices.ACTIVE, "character_unavailable"),
            (Character.StatusChoices.APPROVED, True, Conversation.StatusChoices.CLOSED, "conversation_closed"),
        ]
        for char_status, active, chat_status, expected in cases:
            with self.subTest(char_status=char_status, active=active, chat_status=chat_status):
                Character.objects.filter(pk=self.character.pk).update(status=char_status, is_active=active)
                Conversation.objects.filter(pk=self.conversation.pk).update(status=chat_status)
                response, dispatch, operation_id = self._send()
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["data"], expected)
                dispatch.assert_not_called()
                self.assertFalse(Message.objects.filter(client_operation_id=operation_id).exists())

    def test_api_rechecks_creator_erasure_after_language_detection_before_message_write(self):
        def erase_then_detect(content):
            self._erase_creator()
            return "en"

        response, dispatch, operation_id = self._send(detect_effect=erase_then_detect)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "conversation_closed")
        dispatch.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=operation_id).exists())
        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), 1)

    def test_api_rechecks_character_state_without_relying_on_conversation_closure(self):
        def archive_then_detect(content):
            self._archive_character()
            return "en"

        response, dispatch, operation_id = self._send(detect_effect=archive_then_detect)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "character_unavailable")
        dispatch.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=operation_id).exists())

    def test_api_rechecks_account_erasure_on_surviving_public_character(self):
        Character.objects.filter(pk=self.character.pk).update(is_public=True, created_by=self.user)

        def erase_then_detect(content):
            result = Auth.hard_delete_user(self.user)
            self.assertEqual(result["status"], "success", result)
            return "en"

        response, dispatch, operation_id = self._send(detect_effect=erase_then_detect)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["data"], "conversation_not_found")
        dispatch.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=operation_id).exists())
        self.assertFalse(Conversation.objects.filter(pk=self.conversation.pk).exists())
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())

    def test_late_redelivery_of_settled_reply_after_closure_does_not_refund_or_mark_failed(self):
        self._run()
        self._erase_creator()
        self.provider.reset_mock()
        self.send_ai.reset_mock()
        self._run()
        self.provider.assert_not_called()
        self.send_ai.assert_not_called()
        reservation = BillingReservation.objects.get(idempotency_key=self.operation_id)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, Message.StatusChoices.SENT)
        self.assertEqual(Message.objects.filter(conversation=self.conversation, sender_type="ai").count(), 1)
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.sparks, 1000 - MESSAGE_PAIR_COST)

    def test_optional_photo_offer_cannot_be_persisted_after_reply_then_erasure(self):
        self.provider.return_value = self._completion("A completed answer. [PHOTO_OFFER:A portrait in a garden.]")
        self.send_ai.side_effect = lambda *args: self._erase_creator()
        self._run()
        self.send_ai.assert_called_once()
        self.send_offer.assert_not_called()
        self.push.assert_not_called()
        replies = Message.objects.filter(conversation=self.conversation, sender_type="ai")
        self.assertEqual(replies.count(), 1)
        self.assertEqual(replies.get().content, "A completed answer.")
        self.assertFalse(replies.filter(metadata__type="photo_offer").exists())
        reservation = BillingReservation.objects.get(idempotency_key=self.operation_id)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.sparks, 1000 - MESSAGE_PAIR_COST)

    def test_source_message_must_belong_to_queued_conversation(self):
        other = Conversation.objects.create(user=self.creator, character=self.character)
        Message.objects.filter(pk=self.source.pk).update(conversation=other)
        self._run()
        self.provider.assert_not_called()
        self.assertFalse(BillingReservation.objects.exists())
        self._assert_no_reply()

    def test_terminal_guest_cancellation_leaves_erased_claim_hold_for_review_without_quota(self):
        identity, _ = GuestIdentityService.mint()
        GuestIdentity.objects.filter(pk=identity.pk).update(
            status=GuestIdentity.Status.CLAIMED, claimed_at=timezone.now()
        )
        Conversation.objects.filter(pk=self.conversation.pk).update(
            user=None, guest_session_id=identity.owner_key, status=Conversation.StatusChoices.CLOSED
        )
        reservation = BillingReservation.objects.create(
            idempotency_key=self.operation_id,
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            amount_sparks=MESSAGE_PAIR_COST,
            guest_identity=identity,
            origin_guest_session_id=identity.owner_key,
            claimed_from_guest_at=timezone.now(),
        )
        GuestTokenQuota.objects.filter(session_id=identity.owner_key).delete()
        with self.assertLogs("capp.tasks", level="WARNING") as captured:
            self._run(is_guest=True, guest_session_id=identity.owner_key)
        self.assertTrue(any("reason=refund_owner_deleted" in line for line in captured.output))
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)
        self.assertIsNone(reservation.refunded_at)
        self.assertFalse(GuestTokenQuota.objects.filter(session_id=identity.owner_key).exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.error_message, "conversation_closed")
        self.provider.assert_not_called()
        self._assert_no_reply()

    def test_guest_claim_during_provider_refunds_current_account_not_guest_quota(self):
        identity, _ = GuestIdentityService.mint()
        GuestTokenQuota.objects.update_or_create(session_id=identity.owner_key, defaults={"sparks": 1000})
        Conversation.objects.filter(pk=self.conversation.pk).update(user=None, guest_session_id=identity.owner_key)
        GuestTransferGrant.objects.create(
            user=self.user,
            guest_identity=identity,
            origin_guest_session_id=identity.owner_key,
        )

        def claim_then_complete(**kwargs):
            with mock.patch.object(GuestClaimService, "_queue_retrospective_memory"):
                result = GuestClaimService.claim(self.user, identity)
            self.assertTrue(result["claimed"], result)
            return self._completion()

        self.provider.side_effect = claim_then_complete
        self._run(is_guest=True, guest_session_id=identity.owner_key)
        self.provider.assert_called_once()
        reservation = BillingReservation.objects.get(idempotency_key=self.operation_id)
        self.assertEqual(reservation.status, BillingReservation.Status.REFUNDED)
        self.assertEqual(reservation.claimed_by_id, self.user.pk)
        self.assertEqual(GuestTokenQuota.objects.get(session_id=identity.owner_key).sparks, 0)
        self.pool.refresh_from_db()
        self.assertEqual(self.pool.sparks, 2000)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.user_id, self.user.pk)
        self.assertIsNone(self.conversation.guest_session_id)
        self._assert_no_reply()
