"""
Tests for RelationshipService: idempotency, trust decoupling from affinity,
rolling notes, and the Cortecs-first / Mistral-fallback analysis router.
"""

import json
from unittest import mock

from django.test import TestCase

from capp.classes.relationship.relationship_service import (
    RELATIONSHIP_NOTES_MAX,
    RelationshipService,
)
from capp.models import Conversation, Message, RelationshipState
from capp.tests import _make_character, _make_message, _make_user


def _fake_analysis(affinity_change=0, tone_state="neutral", note="ok"):
    """Serialize the payload the way the LLM would return it."""
    return json.dumps(
        {
            "affinity_change": affinity_change,
            "tone_state": tone_state,
            "note": note,
        }
    )


class RelationshipUpdateBehaviourTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.user_message = _make_message(self.conversation, Message.SenderType.USER, "You are wonderful today!")
        self.ai_message = _make_message(self.conversation, Message.SenderType.AI, "Aw, thanks — that made my day.")

    def _run_update(self, affinity_change, tone_state="warm", note="user was warm and complimentary"):
        with mock.patch.object(
            RelationshipService,
            "_analysis_chat_completion",
            return_value=_fake_analysis(affinity_change, tone_state, note),
        ):
            RelationshipService.update_relationship_state(self.conversation, self.user_message, self.ai_message)

    # ---------------- Idempotency ----------------

    def test_repeat_call_with_same_ai_message_is_skipped(self):
        self._run_update(affinity_change=4)
        state = RelationshipState.objects.get(user=self.user, character=self.character)
        self.assertEqual(state.interaction_count, 1)
        self.assertEqual(state.affinity_score, 4)
        first_trust = state.trust_score

        # Simulate a Celery retry of the exact same task.
        self._run_update(affinity_change=4)
        state.refresh_from_db()
        # No double-counting.
        self.assertEqual(state.interaction_count, 1)
        self.assertEqual(state.affinity_score, 4)
        self.assertEqual(state.trust_score, first_trust)

    def test_new_ai_message_processes_normally_after_earlier_one(self):
        self._run_update(affinity_change=3)
        # Second AI message → new idempotency key.
        new_ai = _make_message(self.conversation, Message.SenderType.AI, "second turn")
        with mock.patch.object(
            RelationshipService,
            "_analysis_chat_completion",
            return_value=_fake_analysis(2, "warm", "still positive"),
        ):
            RelationshipService.update_relationship_state(self.conversation, self.user_message, new_ai)
        state = RelationshipState.objects.get(user=self.user, character=self.character)
        self.assertEqual(state.interaction_count, 2)
        self.assertEqual(state.affinity_score, 5)

    # ---------------- Trust decoupling ----------------

    def test_small_positive_affinity_does_not_move_trust(self):
        self._run_update(affinity_change=2)  # below TRUST_MIN_ABS_AFFINITY=3
        state = RelationshipState.objects.get(user=self.user, character=self.character)
        self.assertEqual(state.affinity_score, 2)
        self.assertEqual(state.trust_score, 0)  # trust untouched

    def test_meaningful_positive_affinity_nudges_trust_up(self):
        self._run_update(affinity_change=3)
        state = RelationshipState.objects.get(user=self.user, character=self.character)
        self.assertEqual(state.trust_score, 1)

    def test_negative_affinity_decays_trust_immediately(self):
        # Seed some trust first.
        self._run_update(affinity_change=4)
        state = RelationshipState.objects.get(user=self.user, character=self.character)
        self.assertEqual(state.trust_score, 1)

        # Fresh AI message so we're not blocked by idempotency.
        new_ai = _make_message(self.conversation, Message.SenderType.AI, "next")
        with mock.patch.object(
            RelationshipService,
            "_analysis_chat_completion",
            return_value=_fake_analysis(-1, "distant", "user cooled off"),
        ):
            RelationshipService.update_relationship_state(self.conversation, self.user_message, new_ai)
        state.refresh_from_db()
        self.assertEqual(state.trust_score, 0)

    # ---------------- Runaway-value clamp ----------------

    def test_affinity_change_is_clamped_to_pm5(self):
        # LLM returns something absurd → we still clamp.
        self._run_update(affinity_change=999, tone_state="warm", note="")
        state = RelationshipState.objects.get(user=self.user, character=self.character)
        self.assertEqual(state.affinity_score, 5)

    # ---------------- Notes rolling window ----------------

    def test_notes_are_appended_newest_first_and_capped(self):
        # Feed RELATIONSHIP_NOTES_MAX + 2 turns.
        for i in range(RELATIONSHIP_NOTES_MAX + 2):
            new_ai = _make_message(self.conversation, Message.SenderType.AI, f"turn {i}")
            with mock.patch.object(
                RelationshipService,
                "_analysis_chat_completion",
                return_value=_fake_analysis(1, "warm", f"note {i}"),
            ):
                RelationshipService.update_relationship_state(self.conversation, self.user_message, new_ai)

        state = RelationshipState.objects.get(user=self.user, character=self.character)
        lines = state.relationship_notes.splitlines()
        # Newest first, capped at RELATIONSHIP_NOTES_MAX.
        self.assertEqual(len(lines), RELATIONSHIP_NOTES_MAX)
        expected_newest = f"note {RELATIONSHIP_NOTES_MAX + 1}"
        self.assertEqual(lines[0], expected_newest)


class RelationshipRetrievalTests(TestCase):
    def test_get_relationship_returns_defaults_for_guest_without_creating_row(self):
        character = _make_character()
        result = RelationshipService.get_relationship(user=None, character=character)
        self.assertEqual(result["affinity_score"], 0)
        self.assertEqual(result["tone_state"], RelationshipState.ToneChoices.NEUTRAL)
        self.assertEqual(RelationshipState.objects.count(), 0)

    def test_get_relationship_includes_notes_for_registered_user(self):
        user = _make_user()
        character = _make_character()
        RelationshipState.objects.create(
            user=user,
            character=character,
            affinity_score=15,
            trust_score=20,
            familiarity_score=30,
            relationship_notes="user shared their birthday\nuser mentioned dog",
        )
        result = RelationshipService.get_relationship(user=user, character=character)
        self.assertEqual(result["affinity_score"], 15)
        self.assertIn("dog", result["relationship_notes"])


class RelationshipAnalysisRouterTests(TestCase):
    """Cortecs-first / Mistral-fallback for the sentiment-analysis call."""

    def _fake_cortecs_response(self, content: str):
        choice = mock.Mock()
        choice.message = mock.Mock()
        choice.message.content = content
        response = mock.Mock()
        response.choices = [choice]
        return response

    def test_cortecs_success_returns_content_and_skips_mistral(self):
        fake_client_cls = mock.MagicMock()
        fake_client = mock.MagicMock()
        fake_client_cls.return_value = fake_client
        fake_client.chat.completions.create.return_value = self._fake_cortecs_response('{"affinity_change": 2}')

        with mock.patch("openai.OpenAI", fake_client_cls), mock.patch(
            "capp.classes.relationship.relationship_service.settings"
        ) as fake_settings, mock.patch(
            "capp.classes.relationship.relationship_service.MistralClient.chat_completion"
        ) as mistral_mock:
            fake_settings.CORTECS_API_KEY = "sk-cortecs"
            fake_settings.CORTECS_BASE_URL = "https://api.cortecs.ai/v1"
            fake_settings.CORTECS_MEM_MODEL = "deepseek-v4-flash-0731"

            result = RelationshipService._analysis_chat_completion(
                messages=[{"role": "user", "content": "analyse"}],
                max_tokens=150,
                temperature=0.2,
            )

        self.assertEqual(result, '{"affinity_change": 2}')
        # Verify we routed through the memory-tier model.
        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "deepseek-v4-flash-0731")
        mistral_mock.assert_not_called()

    def test_falls_back_to_mistral_when_cortecs_raises(self):
        fake_client_cls = mock.MagicMock()
        fake_client = mock.MagicMock()
        fake_client_cls.return_value = fake_client
        fake_client.chat.completions.create.side_effect = RuntimeError("cortecs down")

        with mock.patch("openai.OpenAI", fake_client_cls), mock.patch(
            "capp.classes.relationship.relationship_service.settings"
        ) as fake_settings, mock.patch(
            "capp.classes.relationship.relationship_service.MistralClient.chat_completion",
            return_value={"choices": [{"message": {"content": '{"from": "mistral"}'}}]},
        ) as mistral_mock:
            fake_settings.CORTECS_API_KEY = "sk-cortecs"
            fake_settings.CORTECS_BASE_URL = "https://api.cortecs.ai/v1"
            fake_settings.CORTECS_MEM_MODEL = "deepseek-v4-flash-0731"

            result = RelationshipService._analysis_chat_completion(
                messages=[{"role": "user", "content": "analyse"}],
                max_tokens=150,
                temperature=0.2,
            )

        self.assertEqual(result, '{"from": "mistral"}')
        mistral_mock.assert_called_once()

    def test_skips_cortecs_when_no_api_key(self):
        with mock.patch("capp.classes.relationship.relationship_service.settings") as fake_settings, mock.patch(
            "capp.classes.relationship.relationship_service.MistralClient.chat_completion",
            return_value={"choices": [{"message": {"content": '{"only": "mistral"}'}}]},
        ) as mistral_mock:
            fake_settings.CORTECS_API_KEY = None

            result = RelationshipService._analysis_chat_completion(
                messages=[{"role": "user", "content": "analyse"}],
                max_tokens=150,
                temperature=0.2,
            )

        self.assertEqual(result, '{"only": "mistral"}')
        mistral_mock.assert_called_once()

    def test_total_failure_returns_none_and_never_raises(self):
        with mock.patch("capp.classes.relationship.relationship_service.settings") as fake_settings, mock.patch(
            "capp.classes.relationship.relationship_service.MistralClient.chat_completion",
            side_effect=RuntimeError("mistral down"),
        ):
            fake_settings.CORTECS_API_KEY = None
            result = RelationshipService._analysis_chat_completion(
                messages=[{"role": "user", "content": "analyse"}],
                max_tokens=150,
                temperature=0.2,
            )
        self.assertIsNone(result)
