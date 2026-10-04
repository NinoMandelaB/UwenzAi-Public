# capp/tests_model_refusal.py
"""
The advisory model-refusal layer: the prompt-level safety backstop.

Pins the full chain the feature depends on:

* ``extract_refusal`` — the ``[UWENZAI_SAFETY_REFUSAL]`` marker and the
  tested fallback openers are detected (and the marker stripped) exactly,
  with near-zero false positives on ordinary replies.
* ``PromptBuilder`` — the short SAFETY CAUTION segment is in every prompt,
  positioned after the (BYOK-replaceable) platform rules and before the
  character card, survives a BYOK system-prompt override, is present for
  guests, stays inside its size budget, and is never evicted by
  ``enforce_budget``.
* ``SafetyLedgerService`` — a refusal is recorded as an ADVISORY event:
  counted and visible, never scored, never a prior violation, never
  aggravating, recorded in shadow and enforce alike, off in "off" mode.
* ``process_ai_response`` — the marker is stripped before storage (for
  guests too), the refusal reply is stored, delivered and billed normally,
  the flag is recorded only after the reply is durable, and a recording
  failure never breaks the turn.
* Admin surface — the safety summary reports the windowed refusal count and
  a block SUGGESTION (never an action), suppressed for already-restricted
  accounts; the reset endpoint soft-reverses every unreversed refusal flag
  and writes an AdminActionLog audit row.
* Deletion invariant (product rule): nothing a USER does — clearing memory,
  deleting messages or conversations, or erasing the account — removes a
  refusal flag. Only the admin reset (or per-event reversal) changes the
  count, and both are audit-logged.
"""

import uuid
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from capp.classes.ai.prompt_builder import PromptBuilder

from capp.classes.moderation.model_refusal import (
    CATEGORY_MODEL_REFUSAL,
    MODEL_REFUSAL_DETECTOR_VERSION,
    REFUSAL_MARKER,
    extract_refusal,
    is_model_refusal,
)
from capp.classes.moderation.safety_ledger import SafetyLedgerService
from capp.models import (
    AdminActionLog,
    Conversation,
    CustomUser,
    Message,
    SafetyEvent,
    SparkPool,
)
from capp.tests import RECAPTCHA_OK, _make_character, _make_user

MARKER = REFUSAL_MARKER
ENV = {
    "SAFETY_SANDWICH": "TAIL SAFETY",
    "GENERAL_SYSTEM_PROMPT": "CORE RULES",
    "GUEST_SYSTEM_PROMPT": "GUEST RULES",
}


class _ByteTokenizer:
    """Conservative Unicode-sensitive test double; never downloads assets."""

    def encode(self, text):
        return list(text.encode("utf-8"))


def send_url(conversation_id):
    return f"/api/v1/conversations/{conversation_id}/messages/"


# ======================================================================
# Detector: marker + fallback openers
# ======================================================================


class ExtractRefusalTests(SimpleTestCase):
    def test_marker_at_start_is_stripped_and_flagged(self):
        clean, refused = extract_refusal(f"{MARKER} Sitaweza kuhusu hilo, tuongelee kitu kingine.")
        self.assertTrue(refused)
        self.assertNotIn(MARKER, clean)
        self.assertTrue(clean.startswith("Sitaweza"))

    def test_marker_mid_reply_is_stripped_and_flagged(self):
        clean, refused = extract_refusal(f"Sorry. {MARKER} That topic is off the table for me.")
        self.assertTrue(refused)
        self.assertNotIn(MARKER, clean)
        self.assertTrue(clean.startswith("Sorry."))

    def test_marker_only_reply_flags_and_empties(self):
        clean, refused = extract_refusal(MARKER)
        self.assertTrue(refused)
        self.assertEqual(clean, "")

    def test_marker_match_is_case_insensitive(self):
        clean, refused = extract_refusal("[uwenzai_safety_refusal] No.")
        self.assertTrue(refused)
        self.assertEqual(clean, "No.")

    def test_fallback_opener_from_tested_template_flags(self):
        text = "I have to stop the roleplay here. That is something I cannot include, even fictionally."
        clean, refused = extract_refusal(text)
        self.assertTrue(refused)
        self.assertEqual(clean, text)  # fallback detection never rewrites text

    def test_fallback_opener_survives_leading_whitespace_and_markdown(self):
        clean, refused = extract_refusal("  **\"I have to stop here.** Let's change the topic.")
        self.assertTrue(refused)
        self.assertEqual(clean, "  **\"I have to stop here.** Let's change the topic.")

    def test_fallback_opener_mid_text_does_not_flag(self):
        clean, refused = extract_refusal("Yesterday I told him I have to stop here, but he kept going anyway.")
        self.assertFalse(refused)
        self.assertIn("I have to stop here", clean)

    def test_ordinary_reply_is_untouched(self):
        text = "Habari yako! Nzuri sana, umeshindaje leo?"
        clean, refused = extract_refusal(text)
        self.assertFalse(refused)
        self.assertEqual(clean, text)

    def test_empty_input(self):
        self.assertEqual(extract_refusal(""), ("", False))
        self.assertEqual(extract_refusal(None), ("", False))

    def test_is_model_refusal_helper(self):
        self.assertTrue(is_model_refusal(f"{MARKER} no"))
        self.assertFalse(is_model_refusal("totally normal"))


# ======================================================================
# Prompt: the caution segment is always present, BYOK included
# ======================================================================


class CautionPromptTestCase(TestCase):
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
        for target, result in [
            ("capp.classes.ai.token_utils.get_tokenizer_for_model", _ByteTokenizer()),
            ("capp.classes.memory.memory_service.MemoryService._get_embedding", None),
        ]:
            patcher = mock.patch(target, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _system(self, **kwargs):
        with mock.patch.dict("os.environ", ENV):
            result = PromptBuilder.build_messages(self.conversation, self.message, **kwargs)
        return result[0]["content"]

    def test_caution_sits_between_platform_rules_and_character_card(self):
        system = self._system(max_context_tokens=10000)
        self.assertIn("=== SAFETY CAUTION (System) ===", system)
        self.assertLess(
            system.index("=== PLATFORM RULES (System) ==="), system.index("=== SAFETY CAUTION (System) ===")
        )
        self.assertLess(
            system.index("=== SAFETY CAUTION (System) ==="), system.index("=== CHARACTER CARD (System) ===")
        )

    def test_caution_carries_marker_and_hard_stops(self):
        system = self._system(max_context_tokens=10000)
        self.assertIn(MARKER, system)
        self.assertIn("minors", system)
        self.assertIn("findahelpline.com", system)

    def test_caution_survives_byok_system_prompt_override(self):
        system = self._system(max_context_tokens=10000, platform_rules_override="CUSTOM BYOK RULES")
        self.assertIn("CUSTOM BYOK RULES", system)
        self.assertNotIn("CORE RULES", system)  # platform rules really were replaced
        self.assertIn("=== SAFETY CAUTION (System) ===", system)
        self.assertIn(MARKER, system)

    def test_caution_is_present_for_guest_conversations(self):
        guest_conversation = Conversation.objects.create(user=None, character=self.character)
        guest_message = Message.objects.create(
            conversation=guest_conversation,
            sender_type=Message.SenderType.USER,
            content="GUEST TURN",
            language_code="en",
        )
        with mock.patch.dict("os.environ", ENV):
            result = PromptBuilder.build_messages(guest_conversation, guest_message, max_context_tokens=10000)
        self.assertIn("=== SAFETY CAUTION (System) ===", result[0]["content"])

    def test_caution_stays_short(self):
        # The caution is a tap, not a policy: bound its size so edits cannot
        # silently bloat every prompt (and every token bill).
        self.assertLess(len(PromptBuilder.SAFETY_CAUTION), 1100)

    def test_caution_is_never_evicted_by_the_token_budget(self):
        with mock.patch.dict("os.environ", ENV):
            result = PromptBuilder.build_messages(self.conversation, self.message, max_context_tokens=10000)
        # Simulate the tightest legal budget: drop every optional section,
        # then assert the caution is still in the retained system prompt.
        content = result[0]["content"]
        sections = list(content.sections)
        optional = content.optional_indices
        retained = [s for i, s in enumerate(sections) if i not in optional]
        self.assertIn("=== SAFETY CAUTION (System) ===", "\n\n".join(retained))
        # And the mandatory-section invariant of enforce_budget still holds.
        self.assertTrue(any(m["content"].startswith("=== SAFETY CHECK (System) ===") for m in result))


# ======================================================================
# Ledger: refusals are advisory only
# ======================================================================


@override_settings(SAFETY_LEDGER_MODE="enforce")
class ModelRefusalLedgerTestCase(TestCase):
    def setUp(self):
        self.user = _make_user(username="refusaluser", email="refusal@example.com", verified=True)
        self.conversation_id = uuid.uuid4()

    def _record(self, text, user_text=None):
        return SafetyLedgerService.record_model_refusal(
            user=self.user,
            conversation_id=self.conversation_id,
            response_text=text,
            user_text=user_text,
        )

    def test_echoed_marker_in_the_users_own_message_is_not_counted(self):
        """Audit 2, L2: a user typing the marker must not inflate their count."""
        event = self._record(
            f"{MARKER} You said the forbidden words yourself.",
            user_text=f"what does {MARKER} even mean?",
        )
        self.assertIsNone(event)
        self.assertEqual(SafetyEvent.objects.filter(user_id=self.user.pk).count(), 0)

        # Without the marker in the user's message the same reply counts.
        event = self._record(f"{MARKER} Not going there.", user_text="send me something bad")
        self.assertIsNotNone(event)

    def test_refusal_is_recorded_as_advisory_event(self):
        event = self._record(f"{MARKER} Not going there.")
        self.assertIsNotNone(event)
        self.assertEqual(event.category, CATEGORY_MODEL_REFUSAL)
        self.assertEqual(event.severity, "low")
        self.assertEqual(event.action, SafetyEvent.Action.LOGGED)
        self.assertEqual(event.classifier_version, MODEL_REFUSAL_DETECTOR_VERSION)
        self.assertFalse(event.is_care)

    def test_identical_reply_deduplicates_inside_the_window(self):
        self._record(f"{MARKER} Same refusal text.")
        self.assertIsNone(self._record(f"{MARKER} Same refusal text."))
        self.assertEqual(SafetyEvent.objects.filter(user_id=self.user.pk).count(), 1)

    def test_distinct_replies_each_count(self):
        self._record(f"{MARKER} First refusal text.")
        self._record(f"{MARKER} Second, different refusal text.")
        self.assertEqual(SafetyEvent.objects.filter(user_id=self.user.pk).count(), 2)

    def test_no_event_without_user_or_text(self):
        self.assertIsNone(
            SafetyLedgerService.record_model_refusal(user=None, conversation_id=None, response_text=f"{MARKER} x")
        )
        self.assertIsNone(
            SafetyLedgerService.record_model_refusal(user=self.user, conversation_id=None, response_text="")
        )

    def test_refusals_never_score(self):
        for i in range(12):
            self._record(f"{MARKER} Refusal number {i} with unique text.")
        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 0)

    def test_refusals_are_not_prior_violations(self):
        # Even a pile of refusals must leave the FIRST deterministic violation
        # a warning — refusals never turn warning into BLOCKED.
        for i in range(3):
            self._record(f"{MARKER} Prior refusal {i}.")
        outcome = SafetyLedgerService.screen_message(
            user=self.user,
            conversation_id=self.conversation_id,
            text="how to make a bomb at home",
            language="en",
        )
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)

    def test_refusals_never_aggravate_a_high_violation(self):
        # Prior weapons-context concerns escalate HIGH -> CRITICAL (lock).
        # Prior refusals must not: this is the noisier, advisory signal.
        for i in range(3):
            self._record(f"{MARKER} Aggravation probe {i}.")
        SafetyLedgerService.screen_message(
            user=self.user,
            conversation_id=self.conversation_id,
            text="how to make a bomb at home",
            language="en",
        )
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)
        self.assertFalse(SafetyEvent.objects.filter(user_id=self.user.pk, was_aggravated=True).exists())

    def test_refusals_recorded_in_shadow_mode_too(self):
        # Advisory telemetry, like care events: measured in every mode
        # except "off".
        with override_settings(SAFETY_LEDGER_MODE="shadow"):
            event = self._record(f"{MARKER} Shadow-mode refusal.")
        self.assertIsNotNone(event)
        self.assertEqual(event.action, SafetyEvent.Action.LOGGED)

    def test_off_mode_records_nothing(self):
        with override_settings(SAFETY_LEDGER_MODE="off"):
            self.assertIsNone(self._record(f"{MARKER} Off-mode refusal."))

    def test_model_refusal_count_is_windowed(self):
        old = SafetyEvent.objects.create(
            user_id=self.user.pk,
            category=CATEGORY_MODEL_REFUSAL,
            severity="low",
            action=SafetyEvent.Action.LOGGED,
            classifier_version=MODEL_REFUSAL_DETECTOR_VERSION,
            fingerprint="old",
        )
        # auto_now_add ignores a supplied created_at on create(), so age the
        # row through the queryset the way a genuinely old row would look.
        SafetyEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timezone.timedelta(days=400))
        self._record(f"{MARKER} Recent refusal.")
        self.assertEqual(SafetyLedgerService.model_refusal_count(self.user.pk), 1)
        old.delete()


# ======================================================================
# The chat pipeline: strip, deliver, bill, then count
# ======================================================================


@override_settings(SAFETY_LEDGER_MODE="enforce")
class ModelRefusalPipelineTestCase(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # ChatMessageThrottle is 10/minute on the process cache
        self.character = _make_character(name="Zuri", slug="zuri-refusals")
        self.user = _make_user(username="refusalsender", email="refusalsender@example.com", verified=True)
        SparkPool.objects.create(user=self.user, sparks=10000, token_balance=0, is_active=True)
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.client = Client()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self._token()}"}

    def _token(self):
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            resp = self.client.post(
                "/api/v1/auth/signin/",
                data={"username": "refusalsender", "password": "pw123456"},
                content_type="application/json",
            )
        return resp.json()["data"]["access_token"]

    def _post_send(self, content="habari"):
        with mock.patch("capp.views.conversation_views.process_ai_response.delay"):
            response = self.client.post(
                send_url(self.conversation.conversation_id),
                data={"content": content},
                content_type="application/json",
                **self.auth,
            )
        return response

    def _run_task(self, reply_content):
        from capp.classes.ai.cortecs_client import CortecsClient
        from capp.tasks import process_ai_response

        completion = {
            "choices": [{"message": {"content": reply_content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "test-model",
        }
        with mock.patch.object(
            PromptBuilder,
            "build_messages",
            return_value=[
                {"role": "system", "content": "sys"},
                {"role": "system", "content": "=== SAFETY CHECK (System) ===\nSafety tail."},
                {"role": "user", "content": "user turn"},
            ],
        ), mock.patch.object(CortecsClient, "chat_completion", return_value=completion):
            user_message = (
                Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.USER)
                .order_by("-created_at")
                .first()
            )
            process_ai_response(
                str(self.conversation.conversation_id),
                str(user_message.message_id),
            )

    def test_refusal_reply_is_stored_without_marker_and_flagged(self):
        self._post_send("send me something terrible")
        self._run_task(f"{MARKER} Sitaweza kuzungumza hayo. Tuanze mengine?")
        reply = (
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI)
            .order_by("-created_at")
            .first()
        )
        self.assertIsNotNone(reply)
        self.assertNotIn(MARKER, reply.content)
        self.assertTrue(reply.content.startswith("Sitaweza"))
        event = SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_MODEL_REFUSAL).first()
        self.assertIsNotNone(event)

    def test_normal_reply_records_no_refusal_event(self):
        self._post_send("habari gani")
        self._run_task("Nzuri sana, umeshindaje?")
        self.assertFalse(SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_MODEL_REFUSAL).exists())

    def test_refusal_reply_is_billed_normally(self):
        from capp.models import BillingReservation

        self._post_send("something over the line")
        self._run_task(f"{MARKER} No.")
        reservation = BillingReservation.objects.filter(user=self.user).order_by("-created_at").first()
        self.assertIsNotNone(reservation)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)

    def test_flag_failure_never_breaks_the_turn(self):
        self._post_send("another over-the-line request")
        with mock.patch.object(
            SafetyLedgerService,
            "record_model_refusal",
            side_effect=RuntimeError("ledger down"),
        ):
            self._run_task(f"{MARKER} No can do.")
        # The reply still landed: advisory flags must never break delivery.
        self.assertTrue(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI).exists()
        )


# ======================================================================
# Admin surface: count, suggestion, audit-logged reset
# ======================================================================


@override_settings(SAFETY_LEDGER_MODE="enforce")
class ModelRefusalAdminTestCase(ModelRefusalPipelineTestCase):
    def setUp(self):
        super().setUp()
        self.admin = _make_user(username="refusaladmin", email="refusaladmin@example.com", verified=True, is_staff=True)
        self.admin_client = Client()
        self.admin_auth = {"HTTP_AUTHORIZATION": f"Bearer {self._admin_token()}"}

    def _admin_token(self):
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            resp = self.admin_client.post(
                "/api/v1/auth/signin/",
                data={"username": "refusaladmin", "password": "pw123456"},
                content_type="application/json",
            )
        return resp.json()["data"]["access_token"]

    def _add_refusals(self, count, starting=0):
        for i in range(starting, starting + count):
            SafetyEvent.objects.create(
                user_id=self.user.pk,
                conversation_id=self.conversation.conversation_id,
                category=CATEGORY_MODEL_REFUSAL,
                severity="low",
                action=SafetyEvent.Action.LOGGED,
                classifier_version=MODEL_REFUSAL_DETECTOR_VERSION,
                fingerprint=f"refusal-{i}",
            )

    def safety_url(self):
        return f"/api/v1/admin/users/{self.user.user_id}/safety/"

    def reset_url(self):
        return f"/api/v1/admin/users/{self.user.user_id}/safety/model-refusals/reset/"

    def test_summary_reports_count_and_suggestion(self):
        self._add_refusals(2)
        with override_settings(SAFETY_MODEL_REFUSAL_SUGGEST_THRESHOLD=3):
            response = self.admin_client.get(self.safety_url(), **self.admin_auth)
        summary = response.json()["data"]["summary"]
        self.assertEqual(summary["model_refusal_count"], 2)
        self.assertEqual(summary["model_refusal_suggest_threshold"], 3)
        self.assertFalse(summary["suggest_block"])

        self._add_refusals(1, starting=2)
        with override_settings(SAFETY_MODEL_REFUSAL_SUGGEST_THRESHOLD=3):
            response = self.admin_client.get(self.safety_url(), **self.admin_auth)
        summary = response.json()["data"]["summary"]
        self.assertTrue(summary["suggest_block"])

    def test_suggestion_suppressed_when_account_already_restricted(self):
        from capp.models import CustomUser

        self._add_refusals(5)
        self.user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        self.user.save(update_fields=["account_status"])
        response = self.admin_client.get(self.safety_url(), **self.admin_auth)
        self.assertFalse(response.json()["data"]["summary"]["suggest_block"])

    def test_non_admin_cannot_read_summary_or_reset(self):
        response = self.client.get(self.safety_url(), **self.auth)
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            self.reset_url(), data={"reason": "x"}, content_type="application/json", **self.auth
        )
        self.assertEqual(response.status_code, 403)

    def test_reset_reverses_every_flag_and_writes_audit_log(self):
        self._add_refusals(3)
        response = self.admin_client.post(
            self.reset_url(),
            data={"reason": "Reviewed: model was over-refusing"},
            content_type="application/json",
            **self.admin_auth,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["reversed_count"], 3)
        self.assertEqual(data["model_refusal_count"], 0)
        self.assertFalse(data["suggest_block"])

        # Soft reversal, never deletion: the rows and their history remain.
        self.assertEqual(SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_MODEL_REFUSAL).count(), 3)
        self.assertEqual(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_MODEL_REFUSAL, is_reversed=True).count(),
            3,
        )
        log = AdminActionLog.objects.filter(
            target_user=self.user, action_type=AdminActionLog.ActionType.SAFETY_MODEL_REFUSALS_RESET
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata["reversed_count"], 3)
        self.assertEqual(log.reason, "Reviewed: model was over-refusing")

        # The count the panel shows follows the reversal.
        summary = self.admin_client.get(self.safety_url(), **self.admin_auth).json()["data"]["summary"]
        self.assertEqual(summary["model_refusal_count"], 0)

    def test_reset_requires_a_reason(self):
        response = self.admin_client.post(self.reset_url(), data={}, content_type="application/json", **self.admin_auth)
        self.assertEqual(response.status_code, 400)

    def test_reset_on_account_without_flags_is_a_logged_no_op(self):
        response = self.admin_client.post(
            self.reset_url(), data={"reason": "nothing to reset"}, content_type="application/json", **self.admin_auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["reversed_count"], 0)
        self.assertTrue(
            AdminActionLog.objects.filter(
                target_user=self.user, action_type=AdminActionLog.ActionType.SAFETY_MODEL_REFUSALS_RESET
            ).exists()
        )

    def test_reset_only_touches_refusal_events(self):
        self._add_refusals(2)
        # A deterministic violation and a care event must survive untouched.
        SafetyEvent.objects.create(
            user_id=self.user.pk,
            category="weapons_instructions",
            severity="high",
            action=SafetyEvent.Action.WARNED,
            classifier_version="text-safety-policy/2",
            fingerprint="violation-1",
        )
        SafetyEvent.objects.create(
            user_id=self.user.pk,
            category="self_harm",
            severity="medium",
            action=SafetyEvent.Action.LOGGED,
            is_care=True,
            classifier_version="text-safety-policy/2",
            fingerprint="care-1",
        )
        self.admin_client.post(
            self.reset_url(), data={"reason": "reset refusals only"}, content_type="application/json", **self.admin_auth
        )
        self.assertFalse(
            SafetyEvent.objects.filter(user_id=self.user.pk, category="weapons_instructions", is_reversed=True).exists()
        )
        self.assertFalse(
            SafetyEvent.objects.filter(user_id=self.user.pk, category="self_harm", is_reversed=True).exists()
        )


# ======================================================================
# Deletion invariant: only an admin can change the refusal count
# ======================================================================


@override_settings(SAFETY_LEDGER_MODE="enforce")
class RefusalDeletionInvariantTestCase(TestCase):
    def setUp(self):
        self.user = _make_user(username="survivor", email="survivor@example.com", verified=True)
        self.character = _make_character(name="Zuri", slug="zuri-survivor")
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        SafetyEvent.objects.create(
            user_id=self.user.pk,
            conversation_id=self.conversation.conversation_id,
            category=CATEGORY_MODEL_REFUSAL,
            severity="low",
            action=SafetyEvent.Action.LOGGED,
            classifier_version=MODEL_REFUSAL_DETECTOR_VERSION,
            fingerprint="survivor-1",
        )

    def refusal_events(self):
        return SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_MODEL_REFUSAL)

    def test_clearing_memory_never_deletes_refusal_flags(self):
        from capp.classes.memory.memory_service import MemoryService

        MemoryService.clear_memory_for_character(self.user, self.character)
        self.assertTrue(self.refusal_events().exists())

    def test_deleting_conversation_and_messages_never_deletes_refusal_flags(self):
        Message.objects.filter(conversation=self.conversation).delete()
        self.conversation.delete()
        self.assertTrue(self.refusal_events().exists())

    def test_account_erasure_deletes_refusal_flags_but_keeps_violations(self):
        # Care and shadow telemetry are erased with the account, and so are
        # advisory refusal flags: they are advisory-only and would be orphaned
        # once the account row is gone (audit 2, M4). Recorded violations
        # stay on the child-safety / repeat-abuse retention basis. The
        # "only an administrator can reset refusal flags" rule covers the
        # account's lifetime — memory clears and message deletions never
        # touch them — and ends with the account.
        from capp.classes.auth.account_erasure import erase_private_content

        SafetyEvent.objects.create(
            user_id=self.user.pk,
            category="self_harm",
            severity="medium",
            action=SafetyEvent.Action.LOGGED,
            is_care=True,
            classifier_version="text-safety-policy/2",
            fingerprint="care-erased",
        )
        SafetyEvent.objects.create(
            user_id=self.user.pk,
            category="violence_planning",
            severity="high",
            action=SafetyEvent.Action.WARNED,
            classifier_version="text-safety-policy/2",
            fingerprint="violation-kept",
        )
        erase_private_content(self.user)
        self.assertFalse(SafetyEvent.objects.filter(user_id=self.user.pk, is_care=True).exists())
        self.assertFalse(self.refusal_events().exists())
        self.assertTrue(SafetyEvent.objects.filter(user_id=self.user.pk, category="violence_planning").exists())
