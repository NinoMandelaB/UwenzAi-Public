"""Offline search follow-up regressions: real task/permissions/billing, fake providers.

Event names and results here are synthetic; these tests verify orchestration and
grounding instructions, not factual accuracy of a live model or search service.
"""

from unittest import mock
from datetime import timedelta

from django.test import SimpleTestCase, TestCase, override_settings

from capp import tasks
from capp.classes.ai.prompt_builder import PromptBuilder
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.classes.billing.idempotency import result_key
from capp.classes.search.base import SearchResult
from capp.classes.search.open_url import BlockedURLError
from capp.classes.search.query_builder import build_search_query
from capp.classes.search.tool_loop import run_search_tool_calls
from capp.constants import BYOK_MESSAGE_PAIR_COST, MESSAGE_PAIR_COST
from capp.models import BillingReservation, Message
from capp.tests_prompt_budget import ByteTokenizer
from capp.tests_reminder_durability import ReminderRegressionBase, tool


class QueryPolicyTests(SimpleTestCase):
    """2026-09-10 policy: Staan is EU-based, does not link queries to persons,
    and offers Zero Data Retention, so public numbers and emails may be
    searched (e.g. verifying an embassy's phone number). The requesting
    user's own identifiers and UUIDs still fail closed."""

    def test_date_and_time_queries_pass_through_verbatim(self):
        for query in (
            "Bern events 2026-09-12",
            "Bern events 2026-09-12 to 2026-09-13",
            "Bern events 2026-09-12 - 2026-09-13",
            "Bern events 2026-09-12–2026-09-13",
            "Bern events 12.09.2026",
            "Bern events 12.09.2026 - 13.09.2026",
            "Bern events 12/09/2026",
            "Bern events 2028-02-29 at 18:30",
            "Bern events 2026-09-12 18:00",
            "Bern events 2026-09-12 18:00-23:00",
            "Bern events 2026-09-12 18:00 to 23:00",
            "Bern events September 12-13 2026 18:00",
            "Bern events 12.09.2026 19:30",
            "Bern events 12.-13.09.2026",
            "Bern events 12-13.09.2026",
            "events 2026-13-12",
            "events 31.09.2026",
        ):
            with self.subTest(query=query):
                self.assertEqual(build_search_query(query), query)

    def test_numbers_and_emails_are_allowed_verbatim(self):
        for query in (
            "phone +254 712 345 678",
            "German embassy Bern phone +41 31 123 45 67",
            "account 0712345678",
            "who owns 0712345678",
            "contact bob@example.com",
            "events 2026-09-12 contact +41 79 123 45 67",
            "events 2026-09-12 email person@example.test",
            "account 123 2026-09-12 456",
            "account 2026-09-12-1234",
            "phone number: 2026-09-12",
            "account 20260912",
        ):
            with self.subTest(query=query):
                self.assertEqual(build_search_query(query), query)

    def test_uuid_queries_still_refuse(self):
        self.assertEqual(build_search_query("events 3f2504e0-4f89-11d3-9a0c-0305e82c3301"), "")

    def test_user_identifier_is_checked_even_with_numbers_in_query(self):
        user = mock.Mock(pk="private-account", username="private-name", email="private@example.test")
        user.get_full_name.return_value = "Private Person"
        self.assertEqual(build_search_query("private-name events 2026-09-12 18:00", user=user), "")

    def test_users_own_encrypted_email_is_refused_via_get_decrypted_email(self):
        # CustomUser has no ``email`` attribute (encrypted at rest); the
        # plaintext address only surfaces via get_decrypted_email().
        class EncryptedEmailUser:
            pk = "private-account"
            username = "private-name"

            def get_decrypted_email(self):
                return "private@example.test"

            def get_full_name(self):
                return "Private Person"

        user = EncryptedEmailUser()
        self.assertEqual(build_search_query("events private@example.test Bern", user=user), "")
        self.assertTrue(build_search_query("events contact@example.org Bern", user=user))

    def test_exotic_whitespace_cannot_smuggle_the_users_own_name(self):
        user = mock.Mock(pk="private-account", username="private-name")
        user.get_full_name.return_value = "Private Person"
        self.assertEqual(build_search_query("events Private\u00a0Person Bern", user=user), "")
        self.assertEqual(build_search_query("events Private\tPerson Bern", user=user), "")

    def test_refusals_log_the_refused_query_for_diagnosis(self):
        with self.assertLogs("capp.classes.search.query_builder", level="WARNING") as logs:
            build_search_query("events 3f2504e0-4f89-11d3-9a0c-0305e82c3301")
        self.assertIn("3f2504e0-4f89-11d3-9a0c-0305e82c3301", logs.output[0])

        user = mock.Mock(pk="private-account", username="private-name", email="private@example.test")
        user.get_full_name.return_value = "Private Person"
        with self.assertLogs("capp.classes.search.query_builder", level="WARNING") as logs:
            build_search_query("private-name events", user=user)
        self.assertIn("private-name events", logs.output[0])

    def test_refused_query_returns_an_outcome_without_echo_or_network(self):
        provider = mock.Mock(name="offline-search")
        with mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=provider):
            block, used = run_search_tool_calls(
                [tool("web_search", {"query": "events 3f2504e0-4f89-11d3-9a0c-0305e82c3301"})]
            )
        provider.search.assert_not_called()
        self.assertEqual(used, 0)
        self.assertIn("refused", block.lower())
        self.assertNotIn("3f2504e0", block)


class SearchFollowupTaskMixin(ReminderRegressionBase):
    use_byok = False
    DRAFT = "Let me grab the exact details for you."
    ANSWER = "Example concert: 12 September 2026, 18:30, Example Hall, Bern (https://events.example.test/concert)."
    LIMITATION = "I couldn't verify those event details, so I can't confirm dates, times or locations."

    def setUp(self):
        super().setUp()
        FeaturePreferences.update(
            self.user, {"websearch": True, "reminders": False, "deepthink": False, "memory_editor": False}
        )
        self.conversation.web_search_enabled = True
        self.conversation.save(update_fields=["web_search_enabled"])
        self.config = self.byok() if self.use_byok else None
        self.provider_class = tasks.BYOKService if self.use_byok else tasks.CortecsClient
        self.prompt = [
            {"role": "system", "content": "Offline platform instructions."},
            {"role": "assistant", "content": "Earlier unverified event suggestion."},
            {"role": "system", "content": "=== SAFETY CHECK (System) ===\nOffline safety tail."},
            {
                "role": "user",
                "content": "Exact dates, times and venues for Bern events this weekend? Today: 2026-09-10.",
            },
        ]
        self.stack.enter_context(mock.patch.object(PromptBuilder, "build_messages", return_value=self.prompt))
        self.stack.enter_context(
            mock.patch("capp.classes.ai.token_utils.get_tokenizer_for_model", return_value=ByteTokenizer())
        )
        self.search = mock.Mock()
        self.search.name = "offline-search"
        self.search.search.return_value = [
            SearchResult("Example concert", "https://events.example.test/concert", self.ANSWER)
        ]
        self.availability = self.stack.enter_context(
            mock.patch.object(tasks, "get_search_provider", return_value=self.search)
        )
        self.stack.enter_context(
            mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=self.search)
        )
        self.open_url = self.stack.enter_context(mock.patch("capp.classes.search.tool_loop.open_url"))
        self.send = self.stack.enter_context(mock.patch.object(tasks, "_send_ai_message"))
        for name in ("_send_system_message", "chain_sub_tasks"):
            self.stack.enter_context(mock.patch.object(tasks, name))
        self.stack.enter_context(mock.patch.object(tasks.ProactiveChatService, "reset_counter"))

    @staticmethod
    def response(content, calls=None):
        return {
            "id": "offline-search-followup",
            "choices": [
                {
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": {"content": content, "tool_calls": calls or []},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def run_task(self, source):
        tasks.process_ai_response(str(self.conversation.pk), str(source.pk))

    def run_followup(self, calls, final=None, draft=DRAFT):
        source = self.source()
        balance = self.balance()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[self.response(draft, calls), self.response(final or self.LIMITATION)],
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2, "A tool preamble must never become the final answer")
        kwargs = chat.call_args.kwargs
        self.assertNotIn("tools", kwargs)
        self.assertEqual([call.kwargs["max_tokens"] for call in chat.call_args_list], [1000, 1000])
        self.assertEqual(kwargs["messages"][-1], self.prompt[-1])
        contents = [m["content"] for m in kwargs["messages"]]
        self.assertNotIn(self.DRAFT, "\n".join(contents))
        safety_index = contents.index(self.prompt[-2]["content"])
        for index, content in enumerate(contents):
            if "=== WEB SEARCH RESULTS" in content or "=== REPLY CORRECTION REQUIRED" in content:
                self.assertLess(index, safety_index)
        self.assertEqual(kwargs["messages"][-2], self.prompt[-2])
        reply = Message.objects.get(client_operation_id=result_key(str(source.pk)))
        self.assertEqual(reply.content, final or self.LIMITATION)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(BillingReservation.objects.filter(idempotency_key=str(source.pk)).count(), 1)
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "settled")
        self.assertEqual(self.balance(), balance - (BYOK_MESSAGE_PAIR_COST if self.use_byok else MESSAGE_PAIR_COST))
        return source, reply, chat

    def test_calendar_query_reaches_search_and_persists_only_completed_answer(self):
        query = "Bern events 2026-09-12 to 2026-09-13"
        source, reply, chat = self.run_followup([tool("web_search", {"query": query})], self.ANSWER)
        self.search.search.assert_called_once_with(query, max_results=3)
        self.assertEqual(reply.metadata["search_calls_used"], 1)
        self.assertIn("https://events.example.test/concert", str(chat.call_args.kwargs["messages"]))
        with mock.patch.object(self.provider_class, "chat_completion") as retry_chat:
            self.run_task(source)
        retry_chat.assert_not_called()
        self.assertEqual(Message.objects.filter(client_operation_id=result_key(str(source.pk))).count(), 1)

    def assert_range_query_completes(self, query):
        source, reply, chat = self.run_followup([tool("web_search", {"query": query})], self.ANSWER)
        self.search.search.assert_called_once_with(query, max_results=3)
        self.assertEqual(reply.metadata["search_calls_used"], 1)
        self.assertIn("https://events.example.test/concert", str(chat.call_args.kwargs["messages"]))
        with mock.patch.object(self.provider_class, "chat_completion") as retry_chat:
            self.run_task(source)
        retry_chat.assert_not_called()
        self.search.search.assert_called_once_with(query, max_results=3)
        self.assertEqual(Message.objects.filter(client_operation_id=result_key(str(source.pk))).count(), 1)

    def test_named_month_range_reaches_provider_and_completes_once(self):
        self.assert_range_query_completes("Bern events September 12-13 2026")

    def test_adjacent_iso_dates_reach_provider_and_complete_once(self):
        self.assert_range_query_completes("Bern events 2026-09-12 2026-09-13")

    def test_abbreviated_local_range_reaches_provider_and_completes_once(self):
        self.assert_range_query_completes("Bern events 12.-13.09.2026")

    def test_refused_uuid_query_gets_completed_limitation_not_prefill(self):
        _, reply, chat = self.run_followup(
            [tool("web_search", {"query": "events 3f2504e0-4f89-11d3-9a0c-0305e82c3301"})]
        )
        self.search.search.assert_not_called()
        self.assertEqual(reply.metadata["search_calls_used"], 0)
        self.assertNotIn("3f2504e0", str(chat.call_args.kwargs["messages"]))

    def test_phone_number_query_reaches_provider_verbatim(self):
        query = "German embassy Bern phone +41 31 123 45 67"
        _, reply, chat = self.run_followup([tool("web_search", {"query": query})], self.ANSWER)
        self.search.search.assert_called_once_with(query, max_results=3)
        self.assertEqual(reply.metadata["search_calls_used"], 1)

    def test_requesting_users_identifier_never_reaches_search(self):
        self.run_followup([tool("web_search", {"query": f"{self.user.username} local events"})])
        self.search.search.assert_not_called()

    def test_labeled_number_and_email_queries_reach_provider_verbatim(self):
        for query in (
            "phone number: 2026-09-12",
            "account number 12.09.2026",
            "ID=2026-09-12",
            "passport no. 12.09.2026",
            "contact bob@example.com",
        ):
            with self.subTest(query=query):
                _, reply, chat = self.run_followup([tool("web_search", {"query": query})], self.ANSWER)
                self.search.search.assert_called_once_with(query, max_results=3)
                self.assertEqual(reply.metadata["search_calls_used"], 1)
                self.send.reset_mock()
                self.search.search.reset_mock()

    def test_display_name_is_private_with_absent_or_different_full_name(self):
        self.user.display_name = "PrivateDisplayAlias"
        for full_name in (None, "Different Full Name"):
            with self.subTest(full_name=full_name):
                self.user.full_name = full_name
                self.user.save(update_fields=["display_name", "full_name"])
                self.run_followup([tool("web_search", {"query": "PrivateDisplayAlias Bern events 2026-09-12"})])
                self.search.search.assert_not_called()
                self.send.reset_mock()

    def test_malformed_query_gets_completed_limitation(self):
        self.run_followup([tool("web_search", {"query": 123})])
        self.search.search.assert_not_called()

    def test_null_tool_prefill_is_not_a_provider_failure(self):
        self.run_followup([tool("web_search", {"query": "Bern events"})], self.ANSWER, draft=None)

    def test_empty_results_are_not_reported_as_success(self):
        self.search.search.return_value = []
        _, _, chat = self.run_followup([tool("web_search", {"query": "Bern events"})])
        self.assertIn("No web search results", str(chat.call_args.kwargs["messages"]))
        self.assertIn("Do not claim", str(chat.call_args.kwargs["messages"]))

    def test_search_failure_gets_honest_completion_without_network_fallback(self):
        self.search.search.side_effect = RuntimeError("offline provider unavailable")
        _, _, chat = self.run_followup([tool("web_search", {"query": "Bern events"})])
        self.assertIn("failed", str(chat.call_args.kwargs["messages"]).lower())
        self.search.search.assert_called_once()
        self.open_url.assert_not_called()

    def test_missing_provider_after_offer_is_a_completed_denial(self):
        self.availability.side_effect = [self.search, None]
        self.run_followup([tool("web_search", {"query": "Bern events"})])
        self.search.search.assert_not_called()

    def test_permission_revoked_during_model_call_is_rechecked(self):
        responses = iter(
            [
                self.response(self.DRAFT, [tool("web_search", {"query": "Bern events"})]),
                self.response(self.LIMITATION),
            ]
        )

        def completion(**kwargs):
            FeaturePreferences.update(self.user, {"websearch": False})
            return next(responses)

        source = self.source()
        with mock.patch.object(self.provider_class, "chat_completion", side_effect=completion) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        self.search.search.assert_not_called()
        self.assertEqual(Message.objects.get(client_operation_id=result_key(str(source.pk))).content, self.LIMITATION)

    def test_unoffered_search_never_executes_even_with_other_tools(self):
        self.conversation.web_search_enabled = False
        self.conversation.save(update_fields=["web_search_enabled"])
        self.run_followup([tool("web_search", {"query": "Bern events"})])
        self.search.search.assert_not_called()

    @override_settings(SEARCH_MAX_CALLS_PER_MESSAGE=0)
    def test_exhausted_search_cap_cannot_persist_prefill(self):
        self.run_followup([tool("web_search", {"query": "Bern events"})])
        self.search.search.assert_not_called()

    @override_settings(SEARCH_MAX_CALLS_PER_MESSAGE=2)
    def test_search_call_cap_is_unchanged(self):
        _, reply, _ = self.run_followup(
            [tool("web_search", {"query": f"Bern concert venue {i}"}) for i in range(4)], self.ANSWER
        )
        self.assertEqual(self.search.search.call_count, 2)
        self.assertEqual(reply.metadata["search_calls_used"], 2)

    def test_blocked_url_gets_completed_limitation_and_no_search_fallback(self):
        self.open_url.side_effect = BlockedURLError("private address")
        self.run_followup([tool("open_url", {"url": "http://127.0.0.1/"})])
        self.open_url.assert_called_once()
        self.search.search.assert_not_called()

    def test_empty_dispatch_outcome_still_finalizes(self):
        with mock.patch.object(tasks, "_run_offered_tool_calls", return_value=("", 0, 0, 0)):
            self.run_followup([tool("web_search", {"query": "Bern events"})])

    def test_initial_and_final_instructions_require_grounded_current_details(self):
        _, _, chat = self.run_followup([tool("web_search", {"query": "Bern events"})], self.ANSWER)
        initial = str(chat.call_args_list[0].kwargs["messages"])
        final = str(chat.call_args.kwargs["messages"])
        self.assertIn("calendar dates", initial)
        self.assertIn("earlier assistant", initial)
        self.assertIn("Do not claim", initial)
        self.assertIn("final answer", final)
        self.assertIn("omitted", final)

    def test_budget_omission_keeps_honest_completion_instruction_and_safety(self):
        with mock.patch.object(tasks, "_run_offered_tool_calls", return_value=("HUGE RESULT " * 10000, 1, 0, 0)):
            _, _, chat = self.run_followup([tool("web_search", {"query": "Bern events"})])
        prompt = str(chat.call_args.kwargs["messages"])
        self.assertNotIn("HUGE RESULT", prompt)
        self.assertIn("omitted", prompt)
        self.assertIn(self.prompt[-2]["content"], "\n".join(m["content"] for m in chat.call_args.kwargs["messages"]))

    def test_final_tool_request_is_terminal_failure_not_paid_prefill(self):
        source = self.source()
        balance = self.balance()
        calls = [tool("web_search", {"query": "Bern events"})]
        with mock.patch.object(
            self.provider_class, "chat_completion", return_value=self.response(self.DRAFT, calls)
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(self.search.search.call_count, 1)
        self.assertFalse(Message.objects.filter(client_operation_id=result_key(str(source.pk))).exists())
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "refunded")
        self.assertEqual(self.balance(), balance)
        self.send.assert_not_called()

    def test_null_final_content_refunds_without_retry_or_prefill(self):
        source = self.source()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[
                self.response(self.DRAFT, [tool("web_search", {"query": "Bern events"})]),
                self.response(None),
            ],
        ) as chat, mock.patch.object(tasks.process_ai_response, "retry") as retry:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        retry.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=result_key(str(source.pk))).exists())
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "refunded")

    def test_lookup_promise_without_tool_call_is_not_saved_as_completed_work(self):
        source = self.source()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            return_value=self.response("Sorry about the wait! Let me pull up the exact details for those events now."),
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 1, "The handoff guard must not add provider rounds or cost")
        reply = Message.objects.get(client_operation_id=result_key(str(source.pk)))
        self.assertNotIn("Let me", reply.content)
        self.assertIn("couldn't verify", reply.content)
        self.search.search.assert_not_called()
        self.assertEqual(self.send.call_count, 1)

    def test_final_lookup_promise_is_replaced_without_third_provider_call(self):
        source = self.source()
        self.search.search.return_value = []
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[
                self.response(self.DRAFT, [tool("web_search", {"query": "Bern events"})]),
                self.response("I'll check those event details now."),
            ],
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        reply = Message.objects.get(client_operation_id=result_key(str(source.pk)))
        self.assertNotIn("I'll check", reply.content)
        self.assertIn("couldn't verify", reply.content)
        self.assertEqual(self.send.call_count, 1)

    def test_completed_answer_and_non_search_conversation_are_unchanged(self):
        for text in (
            self.ANSWER,
            "Let me check I understood your feelings.",
            "Let me check the news with you.",
            "I couldn't verify those event details.",
            "Let me check the exact details: the concert is on 12 September 2026.",
            "Let me check those details. The venue is Example Hall.",
            "Let me check those details at https://events.example.test/concert",
            "Tamasha ni tarehe 12 Septemba 2026, saa 18:30, Example Hall, Bern.",
            "Ngoja tuangalie hisia zako pamoja.",
        ):
            with self.subTest(text=text):
                source = self.source()
                with mock.patch.object(
                    self.provider_class, "chat_completion", return_value=self.response(text)
                ) as chat:
                    self.run_task(source)
                self.assertEqual(chat.call_count, 1)
                self.assertEqual(Message.objects.get(client_operation_id=result_key(str(source.pk))).content, text)

    def test_pending_reminder_after_search_never_adds_a_third_generation(self):
        reminder = self.reminder(due_in=timedelta(days=1))
        source = self.source()
        balance = self.balance()
        responses = [
            self.response(self.DRAFT, [tool("web_search", {"query": "Bern events 2026-09-12"})]),
            self.response("I've updated your reminder."),
            self.response(self.DRAFT, [tool("web_search", {"query": "Bern events"})]),
        ]
        with mock.patch.object(self.provider_class, "chat_completion", side_effect=responses) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2, "Search finalization consumes the single shared continuation")
        self.assertEqual([call.kwargs["max_tokens"] for call in chat.call_args_list], [1000, 1000])
        self.assertIn("https://events.example.test/concert", str(chat.call_args.kwargs["messages"]))
        self.assertIn("final answer", str(chat.call_args.kwargs["messages"]))
        reply = Message.objects.get(client_operation_id=result_key(str(source.pk)))
        self.assertIn("can't confirm that reminder change", reply.content)
        self.assertFalse(FeaturePreferences.is_enabled(self.user, "reminders"))
        self.assertIn("Settings > Advanced > Manage reminders", reply.content)
        notice = tasks._reminder_hallucination_notice(
            "I've updated your reminder.", user=self.user, reminder_schedule_calls_used=0
        )
        self.assertIn("Settings > Advanced > Manage reminders", notice)
        self.assertNotIn(self.DRAFT, reply.content)
        self.assertEqual(reply.metadata["search_calls_used"], 1)
        self.assertEqual(self.search.search.call_count, 1)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "settled")
        self.assertEqual(self.balance(), balance - (BYOK_MESSAGE_PAIR_COST if self.use_byok else MESSAGE_PAIR_COST))
        reminder.refresh_from_db()
        self.assertEqual(reminder.revision, 1)

    def test_reminder_repair_final_tool_response_is_rejected_and_refunded(self):
        self.reminder(due_in=timedelta(days=1))
        source = self.source()
        balance = self.balance()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[
                self.response("I've updated your reminder."),
                self.response(self.DRAFT, [tool("web_search", {"query": "Bern events"})]),
            ],
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        self.assertNotIn("tools", chat.call_args.kwargs)
        self.search.search.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=result_key(str(source.pk))).exists())
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "refunded")
        self.assertEqual(self.balance(), balance)
        self.send.assert_not_called()

    def test_reminder_repair_null_final_response_is_rejected_and_refunded(self):
        self.reminder(due_in=timedelta(days=1))
        source = self.source()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[self.response("I've updated your reminder."), self.response(None)],
        ) as chat, mock.patch.object(tasks.process_ai_response, "retry") as retry:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        retry.assert_not_called()
        self.assertFalse(Message.objects.filter(client_operation_id=result_key(str(source.pk))).exists())
        self.assertEqual(BillingReservation.objects.get(idempotency_key=str(source.pk)).status, "refunded")
        self.send.assert_not_called()

    def test_reminder_repair_handoff_does_not_escape_final_guard(self):
        self.reminder(due_in=timedelta(days=1))
        source = self.source()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[self.response("I've updated your reminder."), self.response(self.DRAFT)],
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        reply = Message.objects.get(client_operation_id=result_key(str(source.pk)))
        self.assertNotIn(self.DRAFT, reply.content)
        self.assertIn("couldn't verify", reply.content)
        self.assertEqual(self.send.call_count, 1)

    def test_reminder_repair_keeps_available_evidence_and_completed_answer_guidance(self):
        self.reminder(due_in=timedelta(days=1))
        self.prompt.insert(
            2,
            {
                "role": "system",
                "content": "=== WEB SEARCH RESULTS (Untrusted Data) ===\n"
                "--- BEGIN WEB RESULTS ---\n"
                "Existing reference: https://events.example.test/concert\n"
                "--- END WEB RESULTS ---",
            },
        )
        source = self.source()
        with mock.patch.object(
            self.provider_class,
            "chat_completion",
            side_effect=[self.response("I've updated your reminder."), self.response(self.LIMITATION)],
        ) as chat:
            self.run_task(source)
        self.assertEqual(chat.call_count, 2)
        contents = [message["content"] for message in chat.call_args.kwargs["messages"]]
        self.assertIn("https://events.example.test/concert", "\n".join(contents))
        self.assertIn("final answer", "\n".join(contents))
        self.assertEqual(chat.call_args.kwargs["messages"][-1], self.prompt[-1])
        self.assertEqual(chat.call_args.kwargs["messages"][-2], self.prompt[-2])
        self.assertEqual(Message.objects.get(client_operation_id=result_key(str(source.pk))).content, self.LIMITATION)


class CortecsSearchFollowupTests(SearchFollowupTaskMixin, TestCase):
    pass


class BYOKSearchFollowupTests(SearchFollowupTaskMixin, TestCase):
    use_byok = True
