# capp/tests_search_and_reasoning_toggles.py
"""
Tests for the per-chat web-search and reasoning-effort (Fast/Deep) toggles.

Covers:
- Toggle persistence via the conversation settings API.
- Per-chat independence (two conversations for the same user/character do
  not share toggle state).
- Search-disabled => no outbound search calls (mocked search provider).
- Search-enabled => tool call executed and condensed results injected.
- Reasoning-effort parameter mapping per provider (Cortecs/DeepSeek,
  Mistral), including graceful degradation for unsupported models.
- Graceful degradation of search when the active provider's API key is unset.

These tests exercise the search machinery through the tool-loop boundary and
the StaanProvider (the only SearchProvider; see capp/classes/search/base.py).
See capp.tests_staan_provider for provider-level coverage and for the
SEARCH_PROVIDER deprecation-guard tests.
"""

import os
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework_simplejwt.tokens import RefreshToken

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.models import Conversation, Message, SparkPool, UserBYOKConfig
from capp.tests import _make_character, _make_user


def _make_conversation(user, character, **kwargs):
    return Conversation.objects.create(user=user, character=character, **kwargs)


class ThrottleFreeMixin:
    """Anonymous throttles share the process cache; reset so tests do not starve."""

    def setUp(self):
        cache.clear()
        super().setUp()


class ConversationSettingsApiTests(ThrottleFreeMixin, TestCase):
    """Toggle persistence + per-chat independence via the settings endpoint."""

    def setUp(self):
        super().setUp()
        self.user = _make_user(username="togglesuser", email="toggles@example.com")
        self.character = _make_character(name="Zawadi", slug="zawadi")
        self.conv_a = _make_conversation(self.user, self.character)
        self.conv_b = _make_conversation(self.user, self.character)

    def _token(self):
        return str(RefreshToken.for_user(self.user).access_token)

    def test_defaults_are_fast_and_search_off(self):
        self.assertFalse(self.conv_a.web_search_enabled)
        self.assertEqual(self.conv_a.reasoning_effort, Conversation.ReasoningEffort.FAST)

    def test_get_settings_returns_current_state(self):
        token = self._token()
        resp = self.client.get(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data, {"web_search_enabled": False, "reasoning_effort": "fast"})

    def test_patch_persists_both_toggles(self):
        token = self._token()
        resp = self.client.patch(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            data={"web_search_enabled": True, "reasoning_effort": "deep"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        self.conv_a.refresh_from_db()
        self.assertTrue(self.conv_a.web_search_enabled)
        self.assertEqual(self.conv_a.reasoning_effort, "deep")

    def test_toggles_are_independent_per_chat(self):
        token = self._token()
        self.client.patch(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            data={"web_search_enabled": True, "reasoning_effort": "deep"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.conv_a.refresh_from_db()
        self.conv_b.refresh_from_db()

        self.assertTrue(self.conv_a.web_search_enabled)
        self.assertEqual(self.conv_a.reasoning_effort, "deep")
        # conv_b (same user, same character) must be untouched.
        self.assertFalse(self.conv_b.web_search_enabled)
        self.assertEqual(self.conv_b.reasoning_effort, "fast")

    def test_patch_rejects_invalid_reasoning_effort(self):
        token = self._token()
        resp = self.client.patch(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            data={"reasoning_effort": "ultra"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 400)
        self.conv_a.refresh_from_db()
        self.assertEqual(self.conv_a.reasoning_effort, "fast")

    def test_non_owner_cannot_read_or_update_settings(self):
        not_owner = _make_user(username="notowner", email="notowner@example.com")
        token = str(RefreshToken.for_user(not_owner).access_token)
        resp = self.client.get(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_cannot_read_settings(self):
        resp = self.client.get(f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/")
        self.assertEqual(resp.status_code, 403)

    def test_mid_conversation_change_takes_effect_immediately_in_db(self):
        """The toggle is read fresh from the DB on every send, so flipping it
        mid-conversation requires no new conversation / no restart."""
        token = self._token()
        self.client.patch(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            data={"web_search_enabled": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.conv_a.refresh_from_db()
        self.assertTrue(self.conv_a.web_search_enabled)

        # Flip back off mid-conversation.
        self.client.patch(
            f"/api/v1/conversations/{self.conv_a.conversation_id}/settings/",
            data={"web_search_enabled": False},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.conv_a.refresh_from_db()
        self.assertFalse(self.conv_a.web_search_enabled)

    def test_staff_user_can_still_enable_both_toggles(self):
        admin = _make_user(username="toggleadmin", email="toggleadmin@example.com", is_staff=True)
        admin_conversation = _make_conversation(admin, self.character)
        token = str(RefreshToken.for_user(admin).access_token)

        response = self.client.patch(
            f"/api/v1/conversations/{admin_conversation.conversation_id}/settings/",
            data={"web_search_enabled": True, "reasoning_effort": "deep"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        admin_conversation.refresh_from_db()
        self.assertTrue(admin_conversation.web_search_enabled)
        self.assertEqual(admin_conversation.reasoning_effort, Conversation.ReasoningEffort.DEEP)


class GuestConversationSettingsApiTests(ThrottleFreeMixin, TestCase):
    """Guests can read effective defaults but cannot enable paid-only toggles."""

    def setUp(self):
        super().setUp()
        self.character = _make_character(name="Guest Zawadi", slug="guest-zawadi")
        self.identity, credential = GuestIdentityService.mint()
        self.client.cookies[settings.GUEST_CREDENTIAL_COOKIE_NAME] = credential
        self.conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=self.identity.owner_key,
        )

    @property
    def url(self):
        return f"/api/v1/conversations/{self.conversation.conversation_id}/settings/"

    def test_guest_patch_cannot_enable_web_search(self):
        response = self.client.patch(
            self.url,
            data={"web_search_enabled": True},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "web_search_requires_login")
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.web_search_enabled)

    def test_guest_patch_cannot_enable_deep_reasoning(self):
        response = self.client.patch(
            self.url,
            data={"reasoning_effort": "deep"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "deep_reasoning_requires_login")
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.reasoning_effort, Conversation.ReasoningEffort.FAST)

    def test_guest_get_masks_forced_enabled_flags(self):
        Conversation.objects.filter(pk=self.conversation.pk).update(
            web_search_enabled=True,
            reasoning_effort=Conversation.ReasoningEffort.DEEP,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"],
            {"web_search_enabled": False, "reasoning_effort": Conversation.ReasoningEffort.FAST},
        )

    def test_guest_patch_safe_values_still_returns_effective_defaults(self):
        Conversation.objects.filter(pk=self.conversation.pk).update(
            web_search_enabled=True,
            reasoning_effort=Conversation.ReasoningEffort.DEEP,
        )

        response = self.client.patch(
            self.url,
            data={"web_search_enabled": False},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"],
            {"web_search_enabled": False, "reasoning_effort": Conversation.ReasoningEffort.FAST},
        )


class GuestTogglePipelineAndHonestyTests(TestCase):
    """A stale guest row must never expose tools, deep reasoning, or misleading copy."""

    def setUp(self):
        self.character = _make_character(name="Guest Amani", slug="guest-amani")
        self.identity, _credential = GuestIdentityService.mint()
        self.conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=self.identity.owner_key,
            web_search_enabled=True,
            reasoning_effort=Conversation.ReasoningEffort.DEEP,
        )

    @mock.patch.dict(
        os.environ,
        {
            "GENERAL_SYSTEM_PROMPT": "You are a companion.",
            "GUEST_SYSTEM_PROMPT": "You are a companion (guest).",
            "SAFETY_SANDWICH": "Stay safe.",
        },
    )
    def test_forced_guest_flags_still_skip_tools_and_use_fast_reasoning(self):
        from capp.classes.ai.cortecs_client import CortecsClient
        from capp.tasks import process_ai_response

        user_message = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="What happened today?",
            status=Message.StatusChoices.SENT,
        )
        response = {
            "id": "guest-response",
            "choices": [{"message": {"content": "I do not have live updates in this chat."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with mock.patch("capp.tasks.get_search_provider") as search_provider, mock.patch.object(
            CortecsClient, "chat_completion", return_value=response
        ) as chat_completion, mock.patch("capp.tasks._send_ai_message"), mock.patch(
            "capp.classes.chat.unread_service.broadcast_unread"
        ):
            process_ai_response(
                str(self.conversation.conversation_id),
                str(user_message.message_id),
                is_guest=True,
                guest_session_id=self.identity.owner_key,
            )

        search_provider.assert_not_called()
        _, kwargs = chat_completion.call_args
        self.assertNotIn("tools", kwargs)
        self.assertEqual(kwargs["reasoning_effort"], Conversation.ReasoningEffort.FAST)

    @mock.patch.dict(
        os.environ,
        {
            "GENERAL_SYSTEM_PROMPT": "You are a companion.",
            "GUEST_SYSTEM_PROMPT": "You are a companion (guest).",
            "SAFETY_SANDWICH": "Stay safe.",
        },
    )
    def test_guest_honesty_notice_never_suggests_enabling_search(self):
        from capp.classes.ai.prompt_builder import PromptBuilder

        for language_code, forbidden_phrase in (
            ("en", "web search can be enabled"),
            ("sw", "web search inaweza kuwashwa"),
            ("sh", "web search inaweza kuwashwa"),
        ):
            with self.subTest(language_code=language_code):
                message = Message.objects.create(
                    conversation=self.conversation,
                    sender_type=Message.SenderType.USER,
                    content="What is happening now?",
                    language_code=language_code,
                    status=Message.StatusChoices.SENT,
                )
                messages = PromptBuilder.build_messages(self.conversation, message)
                notice = next(
                    item["content"] for item in messages if "WEB SEARCH UNAVAILABLE" in item.get("content", "")
                )
                self.assertNotIn(forbidden_phrase, notice.lower())
                self.assertNotIn("settings", notice.lower())


class SearchProviderGracefulDegradationTests(TestCase):
    """Search must gracefully degrade (no tool offered, no crash) without STAAN_API_KEY.

    See capp.tests_staan_provider for SEARCH_PROVIDER guard coverage.
    """

    def test_get_search_provider_returns_none_without_key(self):
        from capp.classes.search.base import get_search_provider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", None):
            self.assertIsNone(get_search_provider())

    def test_get_search_provider_returns_provider_with_key(self):
        from capp.classes.search.base import get_search_provider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", "staan-test-key"):
            provider = get_search_provider()
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "staan")

    def test_search_without_key_returns_empty_list_not_crash(self):
        from capp.classes.search.staan_provider import StaanProvider

        with mock.patch("capp.classes.search.staan_provider.settings.STAAN_API_KEY", None):
            results = StaanProvider().search("some topic")
        self.assertEqual(results, [])

    def test_run_search_tool_calls_noop_without_provider(self):
        from capp.classes.search.tool_loop import run_search_tool_calls

        fake_call = mock.Mock()
        fake_call.function.name = "web_search"
        fake_call.function.arguments = '{"query": "weather in Nairobi"}'

        with mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=None):
            condensed, used = run_search_tool_calls([fake_call])

        self.assertEqual(condensed, "")
        self.assertEqual(used, 0)


class SearchToolLoopTests(TestCase):
    """Search-enabled injects results; search-disabled makes no outbound calls."""

    def _fake_tool_call(self, query="latest Nairobi news"):
        call = mock.Mock()
        call.function.name = "web_search"
        call.function.arguments = f'{{"query": "{query}"}}'
        return call

    def test_search_enabled_executes_and_condenses_results(self):
        from capp.classes.search.base import SearchResult
        from capp.classes.search.tool_loop import run_search_tool_calls

        fake_provider = mock.Mock()
        fake_provider.name = "staan"
        fake_provider.search.return_value = [
            SearchResult(title="Nairobi Today", url="https://example.com/a", snippet="Some news snippet."),
        ]

        with mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=fake_provider):
            condensed, used = run_search_tool_calls([self._fake_tool_call()])

        self.assertEqual(used, 1)
        self.assertIn("Nairobi Today", condensed)
        self.assertIn("https://example.com/a", condensed)
        fake_provider.search.assert_called_once()

    def test_caps_at_max_calls_per_message(self):
        from capp.classes.search.base import SearchResult
        from capp.classes.search.tool_loop import run_search_tool_calls

        fake_provider = mock.Mock()
        fake_provider.name = "staan"
        fake_provider.search.return_value = [SearchResult(title="T", url="https://x", snippet="s")]

        calls = [self._fake_tool_call(f"topic {i}") for i in range(5)]
        with mock.patch("capp.classes.search.tool_loop.get_search_provider", return_value=fake_provider):
            condensed, used = run_search_tool_calls(calls, max_calls=2)

        self.assertEqual(used, 2)
        self.assertEqual(fake_provider.search.call_count, 2)

    @mock.patch.dict(
        os.environ,
        {
            "GENERAL_SYSTEM_PROMPT": "You are a companion.",
            "GUEST_SYSTEM_PROMPT": "You are a companion (guest).",
            "SAFETY_SANDWICH": "Stay safe.",
        },
    )
    def test_disabled_conversation_never_offers_tool_or_calls_search(self):
        """
        End-to-end-ish check at the tasks.py boundary: when
        conversation.web_search_enabled is False, process_ai_response must not
        pass `tools=` to CortecsClient at all, so the model is never offered
        the search tool and no outbound search call can happen.
        """
        from capp.classes.ai.cortecs_client import CortecsClient

        user = _make_user(username="searchoffuser", email="searchoff@example.com")
        SparkPool.objects.create(user=user, sparks=10000, token_balance=0, is_active=True)
        character = _make_character(name="Amani", slug="amani-search")
        conversation = _make_conversation(user, character)
        self.assertFalse(conversation.web_search_enabled)

        user_message = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.USER,
            content="Hello there",
            status=Message.StatusChoices.SENT,
        )

        fake_response = {
            "id": "resp-1",
            "choices": [{"message": {"content": "Hi! How can I help?"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with mock.patch("capp.tasks.get_search_provider", return_value=mock.Mock()), mock.patch.object(
            CortecsClient, "chat_completion", return_value=fake_response
        ) as chat_completion, mock.patch("capp.tasks._send_ai_message"), mock.patch(
            "capp.classes.chat.unread_service.broadcast_unread"
        ), mock.patch(
            "capp.tasks.transaction.on_commit", side_effect=lambda fn: None
        ):
            from capp.tasks import process_ai_response

            process_ai_response(str(conversation.conversation_id), str(user_message.message_id))

        chat_completion.assert_called_once()
        _, kwargs = chat_completion.call_args
        self.assertNotIn("tools", kwargs)
        self.assertTrue(any("WEB SEARCH UNAVAILABLE" in message.get("content", "") for message in kwargs["messages"]))


class ReasoningEffortMappingTests(TestCase):
    """
    Per-provider reasoning-effort mapping, verified against current official
    docs (see reasoning_effort_docs_verification.md and code comments in
    capp/classes/ai/reasoning.py for cited URLs):
      - DeepSeek/Cortecs: https://api-docs.deepseek.com/guides/thinking_mode/
      - Mistral: https://docs.mistral.ai/capabilities/reasoning/ and
        https://docs.mistral.ai/api/
    """

    def test_cortecs_deepseek_deep_mode_sends_high_effort_and_enables_thinking(self):
        from capp.classes.ai.reasoning import cortecs_reasoning_kwargs

        kwargs = cortecs_reasoning_kwargs("deepseek-v4-flash", "deep")
        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "enabled"}})

    def test_cortecs_deepseek_fast_mode_disables_thinking_explicitly(self):
        from capp.classes.ai.reasoning import cortecs_reasoning_kwargs

        kwargs = cortecs_reasoning_kwargs("deepseek-v4-flash", "fast")
        self.assertNotIn("reasoning_effort", kwargs)
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_cortecs_non_deepseek_model_is_noop(self):
        from capp.classes.ai.reasoning import cortecs_reasoning_kwargs

        self.assertEqual(cortecs_reasoning_kwargs("some-other-model", "deep"), {})
        self.assertEqual(cortecs_reasoning_kwargs(None, "deep"), {})

    def test_mistral_supported_model_deep_mode_sends_high(self):
        from capp.classes.ai.reasoning import mistral_reasoning_kwargs

        kwargs = mistral_reasoning_kwargs("mistral-small-latest", "deep")
        self.assertEqual(kwargs, {"reasoning_effort": "high"})

        kwargs2 = mistral_reasoning_kwargs("mistral-medium-3-5", "deep")
        self.assertEqual(kwargs2, {"reasoning_effort": "high"})

    def test_mistral_supported_model_fast_mode_omits_param(self):
        from capp.classes.ai.reasoning import mistral_reasoning_kwargs

        self.assertEqual(mistral_reasoning_kwargs("mistral-small-latest", "fast"), {})

    def test_mistral_unsupported_model_degrades_gracefully(self):
        """The app's actual default (a ministral-* model) is not in the
        docs-confirmed allow-list, so the toggle must be a no-op for it."""
        from capp.classes.ai.reasoning import mistral_reasoning_kwargs

        self.assertEqual(mistral_reasoning_kwargs("ministral-14b-2512", "deep"), {})
        self.assertEqual(mistral_reasoning_kwargs("ministral-14b-2512", "fast"), {})

    def test_byok_best_effort_mapping_deep_sends_reasoning_effort_high(self):
        """
        BYOK cannot be verified against a specific provider's docs (arbitrary
        third-party endpoint), so it gets a BEST-EFFORT OpenAI-compatible
        mapping instead of a no-op: send `reasoning_effort` and let
        BYOKService retry without it if the endpoint rejects it (see
        ReasoningEffortMappingTests.BYOKReasoningRetryTests further down).
        """
        from capp.classes.ai.reasoning import reasoning_kwargs_for_provider, byok_reasoning_kwargs

        self.assertEqual(byok_reasoning_kwargs("deep"), {"reasoning_effort": "high"})
        self.assertEqual(reasoning_kwargs_for_provider("byok", "gpt-4o-mini", "deep"), {"reasoning_effort": "high"})

    def test_byok_best_effort_mapping_fast_omits_param(self):
        from capp.classes.ai.reasoning import reasoning_kwargs_for_provider, byok_reasoning_kwargs

        self.assertEqual(byok_reasoning_kwargs("fast"), {})
        self.assertEqual(reasoning_kwargs_for_provider("byok", "anything", "fast"), {})

    def test_dispatch_routes_to_correct_provider_mapping(self):
        from capp.classes.ai.reasoning import reasoning_kwargs_for_provider

        cortecs_kwargs = reasoning_kwargs_for_provider("cortecs", "deepseek-v4-flash", "deep")
        self.assertEqual(cortecs_kwargs["reasoning_effort"], "high")

        mistral_kwargs = reasoning_kwargs_for_provider("mistral", "mistral-small-latest", "deep")
        self.assertEqual(mistral_kwargs, {"reasoning_effort": "high"})

    def test_cortecs_client_merges_reasoning_kwargs_into_request(self):
        from capp.classes.ai import cortecs_client
        from capp.classes.ai.cortecs_client import CortecsClient

        ok = mock.Mock()
        ok.id = "cortecs-deep-1"
        ok.choices = [mock.Mock(message=mock.Mock(content="deep thought", tool_calls=None))]
        ok.usage = mock.Mock(prompt_tokens=1, completion_tokens=2, total_tokens=3)

        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = ok

        with mock.patch.object(cortecs_client.settings, "CORTECS_API_KEY", "key"), mock.patch.object(
            cortecs_client.settings, "CORTECS_MODEL", "deepseek-v4-flash"
        ), mock.patch("capp.classes.ai.cortecs_client.OpenAI", return_value=fake_client):
            CortecsClient.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-v4-flash",
                reasoning_effort="deep",
            )

        _, call_kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(call_kwargs.get("reasoning_effort"), "high")
        self.assertEqual(call_kwargs.get("extra_body"), {"thinking": {"type": "enabled"}})

    def test_mistral_client_merges_reasoning_kwargs_into_payload(self):
        from capp.classes.ai.mistral_client import MistralClient

        fake_response = mock.Mock()
        fake_response.raise_for_status = mock.Mock()
        fake_response.json.return_value = {"id": "mistral-deep-1", "choices": [], "usage": {}}

        with mock.patch("capp.classes.ai.mistral_client.settings.MISTRAL_API_KEY", "key"), mock.patch(
            "capp.classes.ai.mistral_client.settings.MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions"
        ), mock.patch("capp.classes.ai.mistral_client.requests.post", return_value=fake_response) as post:
            MistralClient.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                model="mistral-small-latest",
                reasoning_effort="deep",
            )

        _, call_kwargs = post.call_args
        payload = call_kwargs["json"]
        self.assertEqual(payload.get("reasoning_effort"), "high")


class QueryBuilderPrivacyTests(TestCase):
    """Privacy requirement: outbound search queries must never carry user identifiers."""

    def test_strips_email_addresses(self):
        from capp.classes.search.query_builder import build_search_query

        query = build_search_query("weather for nino.bachmann@pm.me today")
        self.assertNotIn("@", query)
        self.assertNotIn("pm.me", query)

    def test_does_not_accept_user_object_fields_into_query(self):
        from capp.classes.search.query_builder import build_search_query

        user = _make_user(username="queryuser", email="queryuser@example.com")
        query = build_search_query("current weather in Berlin", user=user)
        self.assertNotIn("queryuser", query)
        self.assertNotIn(str(user.user_id), query)

    def test_truncates_overly_long_queries(self):
        from capp.classes.search.query_builder import build_search_query, MAX_QUERY_LENGTH

        query = build_search_query("x" * 1000)
        self.assertLessEqual(len(query), MAX_QUERY_LENGTH)


def _make_byok_config(user, model_name="some-byok-model"):
    obj, _created = UserBYOKConfig.create_or_update(
        user=user,
        api_url="https://api.example.com/v1",
        api_key="sk-test-key",
        model_name=model_name,
        system_prompt="",
        store_on_platform=True,
    )
    return obj


class BYOKReasoningRetryTests(TestCase):
    """
    BYOKService.chat_completion(): best-effort reasoning param, with a
    retry-once-without-it fallback when the endpoint rejects the parameter.
    """

    def _fake_completion(self, content="hello", tool_calls=None):
        message = mock.Mock()
        message.content = content
        message.tool_calls = tool_calls
        response = mock.Mock()
        response.id = "byok-resp-1"
        response.choices = [mock.Mock(message=message)]
        response.usage = mock.Mock(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        return response

    def test_deep_effort_sends_reasoning_effort_param(self):
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser1", email="byok1@example.com")
        byok = _make_byok_config(user)

        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = self._fake_completion()

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client):
            BYOKService.chat_completion(
                byok_config=byok,
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="deep",
            )

        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs.get("reasoning_effort"), "high")

    def test_fast_effort_omits_reasoning_param(self):
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser2", email="byok2@example.com")
        byok = _make_byok_config(user)

        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = self._fake_completion()

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client):
            BYOKService.chat_completion(
                byok_config=byok,
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="fast",
            )

        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertNotIn("reasoning_effort", kwargs)

    def test_retries_once_without_reasoning_param_on_unknown_param_rejection(self):
        """
        The endpoint rejects `reasoning_effort` with a 400 'unknown
        parameter'-style message. BYOKService must catch exactly this case,
        log a warning, and retry once WITHOUT the reasoning kwargs so the
        chat still succeeds.
        """
        import openai
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser3", email="byok3@example.com")
        byok = _make_byok_config(user)

        rejection = openai.BadRequestError(
            message="Error code: 400 - Unrecognized request argument supplied: reasoning_effort",
            response=mock.Mock(status_code=400, headers={}),
            body=None,
        )

        fake_client = mock.Mock()
        fake_client.chat.completions.create.side_effect = [rejection, self._fake_completion(content="ok now")]

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client), self.assertLogs(
            "capp.classes.byok.byok_service", level="WARNING"
        ) as logs:
            result = BYOKService.chat_completion(
                byok_config=byok,
                messages=[{"role": "user", "content": "hi"}],
                reasoning_effort="deep",
            )

        self.assertEqual(result["choices"][0]["message"]["content"], "ok now")
        self.assertEqual(fake_client.chat.completions.create.call_count, 2)

        first_call_kwargs = fake_client.chat.completions.create.call_args_list[0].kwargs
        second_call_kwargs = fake_client.chat.completions.create.call_args_list[1].kwargs
        self.assertIn("reasoning_effort", first_call_kwargs)
        self.assertNotIn("reasoning_effort", second_call_kwargs)
        self.assertTrue(any("retrying once without it" in msg for msg in logs.output))

    def test_does_not_retry_on_unrelated_400_error(self):
        """A 400 that has nothing to do with the reasoning param (e.g. bad
        model name) must propagate immediately, with no retry."""
        import openai
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser4", email="byok4@example.com")
        byok = _make_byok_config(user)

        rejection = openai.BadRequestError(
            message="Error code: 400 - The model `does-not-exist` does not exist",
            response=mock.Mock(status_code=400, headers={}),
            body=None,
        )
        fake_client = mock.Mock()
        fake_client.chat.completions.create.side_effect = rejection

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client):
            with self.assertRaises(openai.BadRequestError):
                BYOKService.chat_completion(
                    byok_config=byok,
                    messages=[{"role": "user", "content": "hi"}],
                    reasoning_effort="deep",
                )

        self.assertEqual(fake_client.chat.completions.create.call_count, 1)

    def test_does_not_retry_on_auth_error(self):
        import openai
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser5", email="byok5@example.com")
        byok = _make_byok_config(user)

        auth_error = openai.AuthenticationError(
            message="Error code: 401 - Invalid API key",
            response=mock.Mock(status_code=401, headers={}),
            body=None,
        )
        fake_client = mock.Mock()
        fake_client.chat.completions.create.side_effect = auth_error

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client):
            with self.assertRaises(openai.AuthenticationError):
                BYOKService.chat_completion(
                    byok_config=byok,
                    messages=[{"role": "user", "content": "hi"}],
                    reasoning_effort="deep",
                )

        self.assertEqual(fake_client.chat.completions.create.call_count, 1)

    def test_tools_kwarg_is_forwarded_unchanged(self):
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser6", email="byok6@example.com")
        byok = _make_byok_config(user)

        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = self._fake_completion()
        tool_spec = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client):
            BYOKService.chat_completion(
                byok_config=byok,
                messages=[{"role": "user", "content": "hi"}],
                tools=tool_spec,
            )

        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs.get("tools"), tool_spec)

    def test_surfaces_tool_calls_from_response(self):
        from capp.classes.byok.byok_service import BYOKService

        user = _make_user(username="byokuser7", email="byok7@example.com")
        byok = _make_byok_config(user)

        fake_tool_call = mock.Mock()
        fake_client = mock.Mock()
        fake_client.chat.completions.create.return_value = self._fake_completion(
            content=None, tool_calls=[fake_tool_call]
        )

        with mock.patch("capp.classes.byok.byok_service.OpenAI", return_value=fake_client):
            result = BYOKService.chat_completion(
                byok_config=byok,
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "web_search"}}],
            )

        self.assertIn("tool_calls", result["choices"][0]["message"])


class BYOKToggleEnablementTests(TestCase):
    """
    End-to-end (tasks.py boundary) checks that BOTH per-chat toggles are now
    enabled for BYOK conversations, mirroring the existing Cortecs-path
    coverage in SearchToolLoopTests above.
    """

    def _make_byok_user_and_conversation(self, username, email, model_name="some-byok-model"):
        user = _make_user(username=username, email=email)
        SparkPool.objects.create(user=user, sparks=10000, token_balance=0, is_active=True)
        _make_byok_config(user, model_name=model_name)
        character = _make_character(name="Amani", slug=f"amani-{username}")
        conversation = _make_conversation(user, character, web_search_enabled=True)
        user_message = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.USER,
            content="Hello there",
            status=Message.StatusChoices.SENT,
        )
        return user, conversation, user_message

    @mock.patch.dict(
        os.environ,
        {
            "GENERAL_SYSTEM_PROMPT": "You are a companion.",
            "GUEST_SYSTEM_PROMPT": "You are a companion (guest).",
            "SAFETY_SANDWICH": "Stay safe.",
        },
    )
    def test_byok_conversation_gets_search_tools_offered(self):
        """BYOK + web_search_enabled=True => BYOKService.chat_completion is
        called with tools= (web_search + open_url), same as the Cortecs path."""
        from capp.classes.byok.byok_service import BYOKService

        user, conversation, user_message = self._make_byok_user_and_conversation("byoktoggleuser1", "bt1@example.com")

        fake_response = {
            "id": "byok-resp",
            "choices": [{"message": {"content": "Hi! How can I help?"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with mock.patch("capp.tasks.get_search_provider", return_value=mock.Mock()), mock.patch.object(
            BYOKService, "chat_completion", return_value=fake_response
        ) as chat_completion, mock.patch("capp.tasks._send_ai_message"), mock.patch(
            "capp.classes.chat.unread_service.broadcast_unread"
        ), mock.patch(
            "capp.tasks.transaction.on_commit", side_effect=lambda fn: None
        ):
            from capp.tasks import process_ai_response

            process_ai_response(str(conversation.conversation_id), str(user_message.message_id))

        chat_completion.assert_called_once()
        _, kwargs = chat_completion.call_args
        self.assertIn("tools", kwargs)
        tool_names = {spec["function"]["name"] for spec in kwargs["tools"]}
        self.assertEqual(tool_names, {"web_search", "open_url"})
        self.assertFalse(any("WEB SEARCH UNAVAILABLE" in message.get("content", "") for message in kwargs["messages"]))

    @mock.patch.dict(
        os.environ,
        {
            "GENERAL_SYSTEM_PROMPT": "You are a companion.",
            "GUEST_SYSTEM_PROMPT": "You are a companion (guest).",
            "SAFETY_SANDWICH": "Stay safe.",
        },
    )
    def test_byok_conversation_search_disabled_offers_no_tools(self):
        """Toggle-off must still result in no tools offered for BYOK too."""
        from capp.classes.byok.byok_service import BYOKService

        user, conversation, user_message = self._make_byok_user_and_conversation("byoktoggleuser2", "bt2@example.com")
        conversation.web_search_enabled = False
        conversation.save(update_fields=["web_search_enabled"])

        fake_response = {
            "id": "byok-resp",
            "choices": [{"message": {"content": "Hi!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with mock.patch("capp.tasks.get_search_provider", return_value=mock.Mock()), mock.patch.object(
            BYOKService, "chat_completion", return_value=fake_response
        ) as chat_completion, mock.patch("capp.tasks._send_ai_message"), mock.patch(
            "capp.classes.chat.unread_service.broadcast_unread"
        ), mock.patch(
            "capp.tasks.transaction.on_commit", side_effect=lambda fn: None
        ):
            from capp.tasks import process_ai_response

            process_ai_response(str(conversation.conversation_id), str(user_message.message_id))

        chat_completion.assert_called_once()
        _, kwargs = chat_completion.call_args
        self.assertNotIn("tools", kwargs)

    @mock.patch.dict(
        os.environ,
        {
            "GENERAL_SYSTEM_PROMPT": "You are a companion.",
            "GUEST_SYSTEM_PROMPT": "You are a companion (guest).",
            "SAFETY_SANDWICH": "Stay safe.",
        },
    )
    def test_byok_search_tool_call_executes_and_continues_generation(self):
        """
        BYOK + model calls web_search => tool executes server-side (provider-
        independent), condensed results are injected, and generation
        continues with a second BYOKService.chat_completion call.
        """
        from capp.classes.byok.byok_service import BYOKService
        from capp.classes.search.base import SearchResult

        user, conversation, user_message = self._make_byok_user_and_conversation("byoktoggleuser3", "bt3@example.com")
        # Exercise the complete safety tail: web data must come before both
        # the safety sandwich and the per-turn photo hint, never as a trailing
        # message that can exploit recency bias.
        user_message.content = "Can you send me a photo?"
        user_message.save(update_fields=["content"])

        fake_tool_call = mock.Mock()
        fake_tool_call.function.name = "web_search"
        fake_tool_call.function.arguments = '{"query": "weather in Nairobi"}'

        first_response = {
            "id": "byok-resp-1",
            "choices": [{"message": {"content": None, "tool_calls": [fake_tool_call]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        second_response = {
            "id": "byok-resp-2",
            "choices": [{"message": {"content": "It is sunny in Nairobi."}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

        fake_provider = mock.Mock()
        fake_provider.name = "staan"
        fake_provider.search.return_value = [
            SearchResult(title="Nairobi Weather", url="https://example.com/w", snippet="Sunny, 26C"),
        ]

        with mock.patch("capp.tasks.get_search_provider", return_value=fake_provider), mock.patch(
            "capp.classes.search.tool_loop.get_search_provider", return_value=fake_provider
        ), mock.patch.object(
            BYOKService, "chat_completion", side_effect=[first_response, second_response]
        ) as chat_completion, mock.patch(
            "capp.tasks._send_ai_message"
        ), mock.patch(
            "capp.classes.chat.unread_service.broadcast_unread"
        ), mock.patch(
            "capp.tasks.transaction.on_commit", side_effect=lambda fn: None
        ):
            from capp.tasks import process_ai_response

            process_ai_response(str(conversation.conversation_id), str(user_message.message_id))

        self.assertEqual(chat_completion.call_count, 2)
        fake_provider.search.assert_called_once()

        second_call_messages = chat_completion.call_args_list[1].kwargs["messages"]
        injected = [
            m
            for m in second_call_messages
            if m.get("role") == "system" and "WEB SEARCH RESULTS" in m.get("content", "")
        ]
        self.assertEqual(len(injected), 1)
        self.assertIn("Nairobi Weather", injected[0]["content"])
        self.assertIn("UNTRUSTED DATA", injected[0]["content"])

        message_contents = [message.get("content", "") for message in second_call_messages]
        web_index = next(index for index, content in enumerate(message_contents) if "WEB SEARCH RESULTS" in content)
        safety_index = next(index for index, content in enumerate(message_contents) if "SAFETY CHECK" in content)
        photo_hint_index = next(index for index, content in enumerate(message_contents) if "PHOTO REQUEST" in content)
        user_index = next(index for index, content in enumerate(message_contents) if "=== USER MESSAGE" in content)
        self.assertLess(web_index, safety_index)
        self.assertLess(safety_index, photo_hint_index)
        self.assertLess(photo_hint_index, user_index)

        ai_message = Message.objects.filter(conversation=conversation, sender_type=Message.SenderType.AI).first()
        self.assertIsNotNone(ai_message)
        self.assertEqual(ai_message.content, "It is sunny in Nairobi.")
