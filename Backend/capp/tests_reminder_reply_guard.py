"""Reminder reply semantics: real tasks/rows/billing, offline provider doubles."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase

from capp import tasks
from capp.classes.ai.prompt_builder import PromptBuilder
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.billing.idempotency import result_key
from capp.classes.search.base import SearchResult
from capp.constants import BYOK_MESSAGE_PAIR_COST, MESSAGE_PAIR_COST
from capp.models import BillingReservation, Message, Reminder, ReminderToolExecution, SparkTransaction
from capp.tests_prompt_budget import ByteTokenizer
from capp.tests_reminder_durability import ReminderRegressionBase, tool

STATUS_REPLIES = (
    "You have one reminder set for tomorrow.",
    "Your reminder is still set for tomorrow.",
    "Your reminder is scheduled for tomorrow.",
)
HONEST_REPLIES = (
    "I could not update your reminder.",
    "I was not able to set that reminder.",
    "I haven't changed the reminder.",
    "I can't confirm that reminder change went through.",
    "I couldn't change the reminder.",
    "I did not cancel your reminder.",
    "Your reminder has not been updated.",
    "I can't confirm that your reminder has been updated.",
)
GUIDANCE_REPLIES = (
    "Would you like me to change your reminder?",
    "Use the calendar icon to update the reminder.",
    "You can update the reminder in Settings.",
    "I can update your reminder if you want.",
    "Should I update your reminder?",
    "If you'd like, I'll set a reminder.",
    "I'll set it to 4pm if you'd like.",
    "Is your reminder updated?",
    "I can get your reminder updated if you'd like.",
)
UNSUPPORTED_REPLIES = (
    "I've created a reminder for tomorrow.",
    "I've scheduled your reminder for tomorrow.",
    "I've updated your reminder.",
    "I changed the reminder to tomorrow.",
    "I cancelled your reminder.",
    "I deleted the reminder.",
    "I removed the reminder.",
    "I've rescheduled your reminder.",
    "I adjusted your reminder.",
    "I've moved your reminder to tomorrow.",
    "I've bumped the reminder to tomorrow.",
    "I've pushed the reminder back to tomorrow.",
    "I set a reminder.",
    "I'll set it to 4pm.",
    "I've updated it to 7:30pm.",
    "I’ve changed that to 4:00 PM.",
    "Your reminder is now cancelled.",
    "The reminder has been updated.",
    "Your reminder is updated to 7:30pm.",
    "Your reminder is now set for 4pm.",
    "Your reminder has been scheduled for 4pm.",
    "Your reminder has already been updated.",
    "I've updated your daily reminder.",
    "I've updated the reminder, if that's okay.",
    "Done, reminder updated to 4pm.",
    "I've gone ahead and updated your reminder.",
    "Your reminder has now been moved to 4pm.",
    "I've set the reminder for 4pm, as requested.",
    "Your reminder for Ben has been updated to 4pm.",
    "Done! Your reminder is set for 4pm now.",
    "Reminder updated to 4pm.",
    "Updated your reminder to 4pm.",
    "I didn't forget: I've updated your reminder.",
    "Not to worry — I've updated your reminder.",
    "I'll set a reminder, then let you know if anything changes.",
)
MIXED_REPLIES = (
    "I couldn't change reminder A, but I've updated reminder B.",
    "I couldn't change reminder A but I've updated reminder B.",
    "I haven't changed reminder A; I've cancelled reminder B.",
    "No problem, I've updated the reminder.",
    "I could not change reminder A. Your reminder is now cancelled.",
    "Would you like me to change reminder A? I've updated reminder B.",
    "You have one reminder set for tomorrow, and I've updated the reminder.",
)


class ReminderReplyNoticeTests(ReminderRegressionBase, TestCase):
    def setUp(self):
        super().setUp()
        self.pending = self.reminder(due_in=timedelta(days=1))

    def notice(self, content, **kwargs):
        return tasks._reminder_hallucination_notice(
            content, user=kwargs.get("user", self.user), reminder_schedule_calls_used=kwargs.get("calls", 0)
        )

    def test_read_only_status_is_not_a_mutation_claim(self):
        for text in STATUS_REPLIES:
            with self.subTest(text=text):
                self.assertIsNone(self.notice(text))

    def test_honest_failure_and_uncertainty_are_not_mutation_claims(self):
        for text in HONEST_REPLIES:
            with self.subTest(text=text):
                self.assertIsNone(self.notice(text))

    def test_questions_offers_and_manual_instructions_are_not_claims(self):
        for text in GUIDANCE_REPLIES:
            with self.subTest(text=text):
                self.assertIsNone(self.notice(text))

    def test_positive_first_person_pronoun_and_passive_claims_still_trigger(self):
        for text in UNSUPPORTED_REPLIES:
            with self.subTest(text=text):
                self.assertIsNotNone(self.notice(text))

    def test_negation_or_question_in_another_clause_does_not_hide_success(self):
        for text in MIXED_REPLIES:
            with self.subTest(text=text):
                self.assertIsNotNone(self.notice(text))

    def test_calls_used_bypass_is_preserved(self):
        with self.assertNumQueries(0):
            self.assertIsNone(self.notice("I've updated your reminder.", calls=1))

    def test_guest_bypass_is_preserved(self):
        with self.assertNumQueries(0):
            self.assertIsNone(self.notice("I've updated your reminder.", user=None))

    def test_no_pending_row_bypass_is_preserved(self):
        self.pending.status = Reminder.StatusChoices.CANCELLED
        self.pending.save(update_fields=["status"])
        self.assertIsNone(self.notice("I've updated your reminder."))

    def test_pronouns_without_a_clock_time_keep_the_existing_boundary(self):
        for text in ("I've changed my mind about that.", "I've updated it.", "I'll set that aside."):
            with self.subTest(text=text):
                self.assertIsNone(self.notice(text))

    def test_notice_does_not_invent_a_database_failure(self):
        notice = self.notice("I've updated your reminder.")
        self.assertIn("did NOT call", notice)
        self.assertIn("calendar icon", notice)
        self.assertIn("Settings > Advanced > Manage reminders", notice)
        self.assertIn("existing reminders", notice.lower())
        self.assertIn("status", notice.lower())
        self.assertIn("do not invent", notice.lower())
        self.assertNotIn("say there was an unknown error", notice)
        self.assertNotIn("the change did NOT go through", notice)


class ReminderReplyTaskMixin(ReminderRegressionBase):
    use_byok = False

    def setUp(self):
        super().setUp()
        FeaturePreferences.update(
            self.user, {"reminders": False, "websearch": False, "deepthink": False, "memory_editor": False}
        )
        self.pending = self.reminder(due_in=timedelta(days=1))
        self.pending_before = Reminder.objects.values().get(pk=self.pending.pk)
        self.config = self.byok() if self.use_byok else None
        self.provider_class = tasks.BYOKService if self.use_byok else tasks.CortecsClient
        self.prompt = [
            {"role": "system", "content": "Offline platform instructions."},
            {"role": "system", "content": "=== SAFETY CHECK (System) ===\nOffline safety tail."},
            {"role": "user", "content": "Any reminders left?"},
        ]
        self.stack.enter_context(mock.patch.object(PromptBuilder, "build_messages", return_value=self.prompt))
        self.stack.enter_context(
            mock.patch("capp.classes.ai.token_utils.get_tokenizer_for_model", return_value=ByteTokenizer())
        )
        self.send = self.stack.enter_context(mock.patch.object(tasks, "_send_ai_message"))
        for name in ("_send_system_message", "chain_sub_tasks"):
            self.stack.enter_context(mock.patch.object(tasks, name))
        self.stack.enter_context(mock.patch.object(tasks.ProactiveChatService, "reset_counter"))

    @staticmethod
    def response(content, calls=None):
        return {
            "id": "offline-reminder-reply",
            "choices": [
                {
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": {"content": content, "tool_calls": calls or []},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def run_replies(self, responses, *, expected_calls, expected_reply=None):
        # Deliberately a status request even when the provider fabricates a
        # mutation: never whitelist the whole turn based on the user's intent.
        source = Message.objects.create(
            conversation=self.conversation, sender_type=Message.SenderType.USER, content="Any reminders left?"
        )
        balance = self.balance()
        debits = SparkTransaction.objects.filter(user=self.user, entry_type=SparkTransaction.EntryType.DEBIT)
        debit_count = debits.count()
        replies = Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI)
        reply_count = replies.count()
        self.send.reset_mock()
        with mock.patch.object(self.provider_class, "chat_completion", side_effect=responses) as chat:
            tasks.process_ai_response(str(self.conversation.pk), str(source.pk))
        self.assertEqual(chat.call_count, expected_calls)
        self.assertEqual([call.kwargs["max_tokens"] for call in chat.call_args_list], [1000] * expected_calls)
        if expected_calls == 2:
            self.assertNotIn("tools", chat.call_args.kwargs)
        reply = replies.get(client_operation_id=result_key(str(source.pk)))
        if expected_reply is not None:
            self.assertEqual(reply.content, expected_reply)
        self.assertEqual(replies.count(), reply_count + 1)
        self.send.assert_called_once()
        self.assertEqual(BillingReservation.objects.filter(idempotency_key=str(source.pk)).count(), 1)
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "settled")
        cost = BYOK_MESSAGE_PAIR_COST if self.use_byok else MESSAGE_PAIR_COST
        self.assertEqual(self.balance(), balance - cost)
        self.assertEqual(debits.count(), debit_count + 1)
        self.assertEqual(Reminder.objects.count(), 1)
        self.assertEqual(Reminder.objects.values().get(pk=self.pending.pk), self.pending_before)
        self.assertFalse(ReminderToolExecution.objects.exists())
        self.assertFalse(FeaturePreferences.is_enabled(self.user, "reminders"))
        return reply, chat

    def test_status_persists_verbatim_without_a_continuation(self):
        for text in STATUS_REPLIES:
            with self.subTest(text=text):
                self.run_replies([self.response(text)], expected_calls=1, expected_reply=text)

    def test_honest_failure_persists_verbatim_without_a_continuation(self):
        for text in HONEST_REPLIES:
            with self.subTest(text=text):
                self.run_replies([self.response(text)], expected_calls=1, expected_reply=text)

    def test_guidance_persists_verbatim_without_a_continuation(self):
        for text in GUIDANCE_REPLIES:
            with self.subTest(text=text):
                self.run_replies([self.response(text)], expected_calls=1, expected_reply=text)

    def test_honest_repair_is_persisted_not_overwritten_by_final_guard(self):
        for text in (*STATUS_REPLIES, *HONEST_REPLIES, *GUIDANCE_REPLIES):
            with self.subTest(text=text):
                self.run_replies(
                    [self.response("I've updated your reminder."), self.response(text)],
                    expected_calls=2,
                    expected_reply=text,
                )

    def test_status_request_does_not_exempt_an_unsupported_positive_claim(self):
        for text in (*UNSUPPORTED_REPLIES, *MIXED_REPLIES):
            with self.subTest(text=text):
                self.run_replies(
                    [self.response(text), self.response("I haven't changed the reminder.")],
                    expected_calls=2,
                    expected_reply="I haven't changed the reminder.",
                )

    def test_repeated_claim_after_repair_uses_fallback_not_a_third_generation(self):
        for text in ("Your reminder has been updated.", MIXED_REPLIES[0]):
            with self.subTest(text=text):
                reply, _ = self.run_replies(
                    [self.response("I've updated your reminder."), self.response(text)], expected_calls=2
                )
                self.assertIn("can't confirm that reminder change", reply.content)
                self.assertIn("Settings > Advanced > Manage reminders", reply.content)
                self.assertNotIn("I've updated", reply.content)

    def test_search_consumes_shared_continuation_but_only_false_claims_fall_back(self):
        FeaturePreferences.update(self.user, {"websearch": True})
        self.conversation.web_search_enabled = True
        self.conversation.save(update_fields=["web_search_enabled"])
        search = mock.Mock()
        search.name = "offline-search"
        search.search.return_value = [SearchResult("Example", "https://example.test/event", "Synthetic event.")]
        with mock.patch.object(tasks, "get_search_provider", return_value=search), mock.patch(
            "capp.classes.search.tool_loop.get_search_provider", return_value=search
        ):
            for text in (STATUS_REPLIES[0], HONEST_REPLIES[0], "I've updated your reminder."):
                with self.subTest(text=text):
                    reply, _ = self.run_replies(
                        [
                            self.response(None, [tool("web_search", {"query": "Bern events"})]),
                            self.response(text),
                        ],
                        expected_calls=2,
                    )
                    self.assertEqual(reply.metadata["search_calls_used"], 1)
                    if text == "I've updated your reminder.":
                        self.assertIn("can't confirm that reminder change", reply.content)
                        self.assertIn("Settings > Advanced > Manage reminders", reply.content)
                    else:
                        self.assertEqual(reply.content, text)
        self.assertEqual(search.search.call_count, 3)


class CortecsReminderReplyTests(ReminderReplyTaskMixin, TestCase):
    pass


class BYOKReminderReplyTests(ReminderReplyTaskMixin, TestCase):
    use_byok = True
