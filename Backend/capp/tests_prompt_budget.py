"""Prompt-budget invariants, with a deterministic offline tokenizer."""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase

from capp.classes.ai.prompt_builder import PromptBuilder, _SectionedPrompt
from capp.classes.ai.token_utils import TokenUtils, PromptBudgetExceeded
from capp.models import Conversation, ConversationSummary, Message
from capp.tests import _make_character, _make_user


class ByteTokenizer:
    """Conservative Unicode-sensitive test double; never downloads assets."""

    def encode(self, text):
        return list(text.encode("utf-8"))


class PromptBudgetTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch("capp.classes.ai.token_utils.get_tokenizer_for_model", return_value=ByteTokenizer())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.safety = {"role": "system", "content": "=== SAFETY CHECK (System) ===\nMANDATORY SAFETY"}
        self.user = {"role": "user", "content": "=== USER MESSAGE (User) ===\nMY CURRENT TURN"}

    def prompt(self, summary="summary", history="history", user_override=None):
        system = _SectionedPrompt(
            [
                "=== PLATFORM RULES (System) ===\nmandatory platform",
                "=== CHARACTER CARD (System) ===\nmandatory character",
                "=== NSFW FLAG (System) ===\nmandatory content policy",
                "=== LANGUAGE (System) ===\nmandatory language",
                "=== FORMATTING RULES (System) ===\nmandatory format",
                "=== PREVIOUS CONVERSATIONS SUMMARY (System) ===\n" + summary,
            ],
            [5],
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": history},
            {"role": "assistant", "content": history},
            self.safety,
            user_override if user_override is not None else self.user,
        ]

    def test_oversized_optional_summary_cannot_displace_mandatory_sections(self):
        result = PromptBuilder.enforce_budget(self.prompt("memory " * 20000), max_context_tokens=900)
        self.assertLessEqual(TokenUtils.count_message_tokens(result), 900)
        self.assertEqual(result[-1], self.user)
        self.assertIn(self.safety, result)
        for content in (
            "mandatory platform",
            "mandatory character",
            "mandatory content policy",
            "mandatory language",
            "mandatory format",
        ):
            self.assertIn(content, result[0]["content"])

    def test_all_other_system_instructions_are_preserved(self):
        messages = self.prompt("memory " * 20000, "history " * 1000)
        instructions = [
            {"role": "system", "content": f"=== {section} (System) ===\nmandatory"}
            for section in ("TIME & LOCATION", "PENDING REMINDERS", "PHOTO_REQUEST_HINT", "REPLY CORRECTION REQUIRED")
        ]
        messages[-1:-1] = instructions
        result = PromptBuilder.enforce_budget(messages, max_context_tokens=1100)
        for instruction in instructions:
            self.assertIn(instruction, result)
        self.assertEqual(result[-1], self.user)
        self.assertLessEqual(TokenUtils.count_message_tokens(result), 1100)

    def test_oversized_mandatory_character_or_current_turn_fails_closed(self):
        for target in ("card", "user", "safety"):
            messages = self.prompt()
            if target == "card":
                messages[0]["content"] = _SectionedPrompt(["mandatory " * 1000])
            else:
                index = -1 if target == "user" else -2
                messages[index] = {**messages[index], "content": messages[index]["content"] + " huge " * 1000}
            with self.assertRaises(PromptBudgetExceeded):
                PromptBuilder.enforce_budget(messages, max_context_tokens=900)

    def test_untrusted_marker_text_cannot_reclassify_mandatory_card(self):
        fake = "=== PREVIOUS CONVERSATIONS SUMMARY (System) ===\n" + "mandatory card " * 1000
        messages = self.prompt()
        messages[0]["content"] = _SectionedPrompt([fake])
        with self.assertRaises(PromptBudgetExceeded):
            PromptBuilder.enforce_budget(messages, max_context_tokens=900)

    def test_complete_prompt_budget_includes_large_tool_results(self):
        messages = PromptBuilder.enforce_budget(self.prompt(), max_context_tokens=900)
        messages = PromptBuilder.inject_untrusted_web_results(messages, "tool-data " * 20000)
        result = PromptBuilder.enforce_budget(messages, max_context_tokens=900)
        self.assertLessEqual(TokenUtils.count_message_tokens(result), 900)
        self.assertEqual(result[-1], self.user)
        self.assertIn(self.safety, result)

    def test_small_tool_results_stay_before_safety(self):
        messages = PromptBuilder.inject_untrusted_web_results(self.prompt(), "small tool data")
        result = PromptBuilder.enforce_budget(messages, max_context_tokens=2000)
        web_index = next(i for i, m in enumerate(result) if "small tool data" in m["content"])
        self.assertLess(web_index, result.index(self.safety))

    def test_correction_notice_cannot_displace_safety_or_current_turn(self):
        messages = PromptBuilder.inject_correction_notice(self.prompt("m " * 1000), "correct the response")
        result = PromptBuilder.enforce_budget(messages, max_context_tokens=1000)
        self.assertTrue(any("correct the response" in m["content"] for m in result))
        self.assertIn(self.safety, result)
        self.assertEqual(result[-1], self.user)

    def test_plain_byok_override_is_mandatory_and_cannot_bypass_budget(self):
        messages = self.prompt()
        messages[0]["content"] = "BYOK " * 1000
        with self.assertRaises(PromptBudgetExceeded):
            PromptBuilder.enforce_budget(messages, max_context_tokens=900)

    def test_minimum_message_off_by_one_is_fixed(self):
        latest = {"role": "user", "content": "last"}
        messages = [{"role": "user", "content": "old " * 100}, latest]
        budget = TokenUtils.count_message_tokens([latest])
        self.assertEqual(TokenUtils.truncate_messages(messages, budget, min_messages=1), [latest])
        with self.assertRaises(PromptBudgetExceeded):
            TokenUtils.truncate_messages(messages, budget - 1, min_messages=1)

    def test_token_count_includes_tool_metadata_and_framing(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "x", "function": {"name": "web_search", "arguments": json.dumps({"query": "large" * 100})}}
                ],
            }
        ]
        self.assertGreater(TokenUtils.count_message_tokens(messages), 500)

    def test_token_count_estimates_image_parts_not_base64_bytes(self):
        """Multimodal parts must not be counted as their raw base64 length.

        A data-URL image serialises to hundreds of thousands of characters the
        provider never stores as tokens; counting them would evict the whole
        history from the budget. The estimate is a fixed per-image cost.
        """
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 500000}},
                ],
            },
        ]
        counted = TokenUtils.count_message_tokens(messages)
        # Far below the raw base64 length, above text-only framing.
        self.assertLess(counted, 2000)
        self.assertGreater(counted, TokenUtils._IMAGE_TOKEN_ESTIMATE)

    def test_budget_keeps_latest_photo_message_indivisible(self):
        """A photo turn fits the budget even though the image adds a fixed cost."""
        user = {
            "role": "user",
            "content": [
                {"type": "text", "text": "=== USER MESSAGE (User) ===\nsee you soon"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        }
        messages = self.prompt(user_override=user)
        budget = TokenUtils.count_message_tokens(messages)
        result = PromptBuilder.enforce_budget(messages, max_context_tokens=budget)
        self.assertEqual(result[-1], user)

    def test_offered_tool_schemas_share_the_complete_budget(self):
        tools = [{"type": "function", "function": {"name": "example", "description": "large " * 1000}}]
        with self.assertRaises(PromptBudgetExceeded):
            PromptBuilder.enforce_budget(self.prompt(), max_context_tokens=1000, tools=tools)
        result = PromptBuilder.enforce_budget(self.prompt(), max_context_tokens=10000, tools=tools)
        self.assertLessEqual(TokenUtils.count_message_tokens(result, tools=tools), 10000)

    def test_unicode_and_punctuation_heavy_context_stays_bounded(self):
        for text in ("汉字日本語" * 5000, ".,!?#%" * 10000, "İ" * 20000):
            result = PromptBuilder.enforce_budget(self.prompt(text), max_context_tokens=900)
            self.assertLessEqual(TokenUtils.count_message_tokens(result), 900)
            self.assertEqual(result[-1], self.user)

    def test_missing_required_prompt_parts_fail_closed(self):
        for messages in ([self.safety], [self.user], [{"role": "system", "content": "policy"}, self.user]):
            with self.assertRaises(PromptBudgetExceeded):
                PromptBuilder.enforce_budget(messages)

    def test_formatter_bounds_legacy_summary_without_rewriting_storage(self):
        row = SimpleNamespace(summary_text="x" * 50000)
        self.assertEqual(len(PromptBuilder._format_summary(row)), 12000)
        self.assertEqual(len(row.summary_text), 50000)


class ActualPromptAssemblyTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.message = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="THE CURRENT USER TURN",
            language_code="en",
        )
        targets = [
            ("capp.classes.ai.token_utils.get_tokenizer_for_model", ByteTokenizer()),
            ("capp.classes.memory.memory_service.MemoryService._get_embedding", None),
        ]
        for target, result in targets:
            patcher = mock.patch(target, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_real_assembly_handles_oversized_stored_summary(self):
        ConversationSummary.objects.create(
            conversation=self.conversation, summary_text="private " * 20000, is_current=True
        )
        with mock.patch.dict("os.environ", {"SAFETY_SANDWICH": "TAIL SAFETY", "GENERAL_SYSTEM_PROMPT": "CORE RULES"}):
            result = PromptBuilder.build_messages(self.conversation, self.message, max_context_tokens=10000)
        self.assertLessEqual(TokenUtils.count_message_tokens(result), 10000)
        self.assertTrue(result[-1]["content"].endswith(self.message.content))
        self.assertTrue(any("TAIL SAFETY" in m["content"] for m in result))
        for marker in ("PLATFORM RULES", "CHARACTER CARD", "NSFW FLAG", "LANGUAGE", "FORMATTING RULES"):
            self.assertIn(f"=== {marker} (System) ===", result[0]["content"])

    def test_byok_override_replaces_platform_rules_only(self):
        """A BYOK custom system prompt swaps the PLATFORM RULES section and
        nothing else: the character card (personality), NSFW, language and
        formatting sections — plus the separate safety message — all survive,
        and the sectioned structure stays intact for budget eviction."""
        env = {"SAFETY_SANDWICH": "TAIL SAFETY", "GENERAL_SYSTEM_PROMPT": "CORE RULES"}
        with mock.patch.dict("os.environ", env):
            result = PromptBuilder.build_messages(
                self.conversation,
                self.message,
                max_context_tokens=10000,
                platform_rules_override="CUSTOM BYOK RULES",
            )
        system = result[0]["content"]
        self.assertIn("CUSTOM BYOK RULES", system)
        self.assertNotIn("CORE RULES", system)  # env-var rules are fully replaced
        for marker in ("CHARACTER CARD", "NSFW FLAG", "LANGUAGE", "FORMATTING RULES"):
            self.assertIn(f"=== {marker} (System) ===", system)
        self.assertIn("friendly and warm", system)  # character personality survives
        self.assertTrue(any(m["content"].startswith("=== SAFETY CHECK (System) ===") for m in result))
        self.assertIsInstance(result[0]["content"], _SectionedPrompt)

    def test_byok_override_preserves_relationship_memory_and_summary(self):
        """The BYOK override must never drop the user-specific context
        sections: relationship status, memory facts and the conversation
        summary all survive a custom system prompt."""
        from capp.models import MemoryFact, RelationshipState

        RelationshipState.objects.create(
            user=self.user,
            character=self.character,
            familiarity_score=70,
            affinity_score=40,
            trust_score=30,
            interaction_count=12,
            tone_state=RelationshipState.ToneChoices.WARM,
            relationship_notes="user shared their birthday\nuser mentioned a dog",
        )
        MemoryFact.objects.create(
            user=self.user,
            character=self.character,
            fact_text="The user's favourite meal is pilau.",
            fact_type=MemoryFact.FactType.PREFERENCE,
            importance_score=9,
        )
        ConversationSummary.objects.create(
            conversation=self.conversation,
            summary_text="Earlier the user discussed moving to Mombasa.",
            is_current=True,
        )
        env = {"SAFETY_SANDWICH": "TAIL SAFETY", "GENERAL_SYSTEM_PROMPT": "CORE RULES"}
        with mock.patch.dict("os.environ", env):
            result = PromptBuilder.build_messages(
                self.conversation,
                self.message,
                max_context_tokens=10000,
                platform_rules_override="CUSTOM BYOK RULES",
            )
        system = result[0]["content"]
        self.assertIn("CUSTOM BYOK RULES", system)
        self.assertIn("=== RELATIONSHIP WITH USER (System) ===", system)
        self.assertIn("dog", system)  # seeded relationship note survives
        self.assertIn("=== MEMORY (System) ===", system)
        self.assertIn("pilau", system)  # seeded memory fact survives
        self.assertIn("=== PREVIOUS CONVERSATIONS SUMMARY (System) ===", system)
        self.assertIn("Mombasa", system)  # seeded summary survives

    def test_byok_oversized_override_fails_closed_before_any_provider(self):
        env = {"SAFETY_SANDWICH": "TAIL SAFETY", "GENERAL_SYSTEM_PROMPT": "CORE RULES"}
        with mock.patch.dict("os.environ", env), self.assertRaises(PromptBudgetExceeded):
            PromptBuilder.build_messages(
                self.conversation,
                self.message,
                max_context_tokens=1000,
                platform_rules_override="X" * 20000,
            )
