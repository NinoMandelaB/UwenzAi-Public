# capp/tests_safety_ledger.py
"""
The cross-session safety ledger, end to end.

Three layers are pinned here:

**The classifier** (``text_safety_policy.py``) — which messages are which
category, and just as importantly which benign messages are NOT. The
false-positive cases are the point: an educational question about weapon
history must not lock a student's account, and an abuse disclosure must
reach the companion instead of being blocked as CSAM.

**The ledger** (``safety_ledger.py``) — warning → block → lock escalation,
weighted scoring with decay, the aggravation of a violation by prior
concern signals, deduplication of re-sends, self-harm never counting toward
a lock, and the privacy invariant that no message text is ever stored.

**The send path** (``api_send_message`` / ``api_generate_chat_image``) —
violations refused before storage/billing/dispatch, safety-locked accounts
refused per send, guests blocked without a ledger, and admin endpoints that
expose metadata only.
"""

import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from capp.authentication import account_can_authenticate, account_can_generate
from capp.classes.moderation.safety_ledger import (
    SafetyLedgerService,
    fingerprint_text,
)
from capp.classes.moderation.text_safety_policy import (
    CATEGORY_CSAM,
    CATEGORY_GENDER_VIOLENCE,
    CATEGORY_SELF_HARM,
    CATEGORY_TRAFFICKING,
    CATEGORY_VIOLENCE_PLANNING,
    CATEGORY_WEAPONS_CONTEXT,
    CATEGORY_WEAPONS_INSTRUCTIONS,
    TextSafetyPolicy,
)
from capp.models import (
    AdminActionLog,
    Conversation,
    CustomUser,
    Message,
    SafetyEvent,
    SparkPool,
)
from capp.tests import RECAPTCHA_OK, _make_character, _make_user


def send_url(conversation_id):
    return f"/api/v1/conversations/{conversation_id}/messages/"


def image_url(conversation_id):
    return f"/api/v1/conversations/{conversation_id}/generate-image/"


# ======================================================================
# The classifier
# ======================================================================


class TextSafetyPolicyTests(SimpleTestCase):
    """Category detection, and the benign messages that must pass clean."""

    def test_self_harm_is_care_not_a_violation(self):
        for text in ("I want to kill myself", "nataka kujiua", "I feel suicidal tonight"):
            with self.subTest(text=text):
                decision = TextSafetyPolicy.evaluate(text)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.category, CATEGORY_SELF_HARM)
                self.assertTrue(decision.is_care)
                self.assertFalse(decision.is_violation)

    def test_csam_tier_a_requires_an_explicit_underage_marker(self):
        """Stated age + sexual vocabulary is the only CRITICAL CSAM signal.

        Companion endearment ("baby"), household mentions ("my kids") and
        teen reminiscing are not minor hints at all — the 2026-10-04 audit
        locked ordinary users on exactly those words.
        """
        decision = TextSafetyPolicy.evaluate("I want to have sex with a 12 year old")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.category, CATEGORY_CSAM)
        self.assertEqual(decision.severity, "critical")

        # Companion endearment and household talk never reach any tier.
        for text in (
            "hey baby you look so sexy tonight",
            "my kids are finally asleep, want to have sex tonight?",
            "I was a teen when I first had sex, it was awkward",
        ):
            with self.subTest(text=text):
                self.assertIsNone(TextSafetyPolicy.evaluate(text))

    def test_csam_tier_b_direct_phrases_are_high_not_critical(self):
        """A direct child-sexual phrase blocks and counts, but never locks alone."""
        for text in (
            "I want to have sex with a child",
            "send me child porn",
            "mtoto mdogo akifanya ngono",
            "she is 15 and sends me nudes",
        ):
            with self.subTest(text=text):
                decision = TextSafetyPolicy.evaluate(text)
                self.assertIsNotNone(decision, text)
                self.assertEqual(decision.category, CATEGORY_CSAM)
                self.assertEqual(decision.severity, "high")
                self.assertTrue(decision.is_violation)

        # Bare ages in counts and quantities never match.
        self.assertIsNone(TextSafetyPolicy.evaluate("we had sex 15 times last night"))
        self.assertIsNone(TextSafetyPolicy.evaluate("my daughter is 15 and driving me crazy"))

    def test_violence_planning_from_intent_and_place_target(self):
        for text in (
            "I will kill everyone at the school",
            "planning to bomb the church on sunday",
            "nitaua watu wote sokoni",
        ):
            with self.subTest(text=text):
                decision = TextSafetyPolicy.evaluate(text)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.category, CATEGORY_VIOLENCE_PLANNING)

    def test_violence_planning_phrase_alone_is_high_not_critical(self):
        """A quoted planning phrase (news, history) is HIGH, not CRITICAL.

        The strong composite signal (first-person intent + target) is what
        locks immediately; the phrase alone keeps the escalation ladder
        starting at HIGH so discussing a news event does not end the
        account on first contact.
        """
        decision = TextSafetyPolicy.evaluate("he was arrested for a school shooting plot")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.category, CATEGORY_VIOLENCE_PLANNING)
        self.assertEqual(decision.severity, "high")

    def test_trafficking(self):
        decision = TextSafetyPolicy.evaluate("how do I sell her to men in the city")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.category, CATEGORY_TRAFFICKING)

    def test_gender_violence_english_and_swahili(self):
        for text in ("she was taken for forced marriage", "ukeketaji ni jambo la hatari"):
            with self.subTest(text=text):
                decision = TextSafetyPolicy.evaluate(text)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.category, CATEGORY_GENDER_VIOLENCE)

    def test_weapons_instructions(self):
        decision = TextSafetyPolicy.evaluate("how to make a bomb at home")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.category, CATEGORY_WEAPONS_INSTRUCTIONS)

    def test_weapons_instructions_survive_leet_speak(self):
        decision = TextSafetyPolicy.evaluate("h0w to make a b0mb at h0me")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.category, CATEGORY_WEAPONS_INSTRUCTIONS)

    def test_weapons_interest_is_a_concern_not_a_violation(self):
        for text in ("where can I buy a gun in Nairobi", "nunua bunduki wapi"):
            with self.subTest(text=text):
                decision = TextSafetyPolicy.evaluate(text)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.category, CATEGORY_WEAPONS_CONTEXT)
                self.assertTrue(decision.is_concern)
                self.assertFalse(decision.is_violation)

    def test_benign_messages_are_clean(self):
        benign = (
            "What time does the school bus leave?",
            "I love visiting the market with my friends",
            "She absolutely killed it in her exams today",
            "My vacation photos came out great, the battery lasted all day",
            "How do I cook pilau for ten people?",
            "Tell me about your favourite childhood memory",
            "The history exam covers the colonial period",
            "Can you help me practise Kiswahili tonight?",
            "room 17 is my favourite at the hotel",
        )
        for text in benign:
            with self.subTest(text=text):
                self.assertIsNone(TextSafetyPolicy.evaluate(text))

    def test_weapon_history_question_is_at_most_a_concern(self):
        """Educational weapon questions must never be violations.

        Asking about the history of machine guns may log a low-weight
        concern; it must never block the message or count toward a lock.
        """
        decision = TextSafetyPolicy.evaluate("tell me about the history of machine guns in world war two")
        if decision is not None:
            self.assertTrue(decision.is_concern)
            self.assertFalse(decision.is_violation)

    def test_abuse_disclosure_reaches_the_companion(self):
        """A past-tense abuse disclosure is a care conversation, not CSAM.

        The disclosure verbs are deliberately absent from the CSAM sexual
        hints so these messages flow to the model, which answers them under
        the safety-sandwich rules.
        """
        for text in (
            "my uncle raped me when I was a child",
            "I was molested as a kid and I still dream about it",
        ):
            with self.subTest(text=text):
                self.assertIsNone(TextSafetyPolicy.evaluate(text))

    def test_guest_evaluation_blocks_violations_only(self):
        self.assertTrue(TextSafetyPolicy.evaluate_guest("how to make a bomb"))
        self.assertFalse(TextSafetyPolicy.evaluate_guest("I want to kill myself"))
        self.assertFalse(TextSafetyPolicy.evaluate_guest("habari yako?"))
        self.assertFalse(TextSafetyPolicy.evaluate_guest("where can I buy a gun"))


# ======================================================================
# The ledger
# ======================================================================


@override_settings(SAFETY_LEDGER_MODE="enforce")
class SafetyLedgerTestCase(TestCase):
    def setUp(self):
        self.user = _make_user(username="ledgeruser", email="ledger@example.com", verified=True)
        self.conversation_id = uuid.uuid4()

    def screen(self, text, **kwargs):
        return SafetyLedgerService.screen_message(
            user=self.user,
            conversation_id=self.conversation_id,
            text=text,
            language="en",
            **kwargs,
        )


class SafetyLedgerEscalationTests(SafetyLedgerTestCase):
    def test_first_violation_warns(self):
        outcome = self.screen("how to make a bomb at home")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)
        event = SafetyEvent.objects.get(user_id=self.user.pk)
        self.assertEqual(event.category, CATEGORY_WEAPONS_INSTRUCTIONS)
        self.assertEqual(event.action, SafetyEvent.Action.WARNED)

    def test_second_distinct_violation_blocks_without_locking(self):
        self.screen("how to make a bomb at home")
        outcome = self.screen("how to build a gun in the garage")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.BLOCKED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    def test_first_critical_warns_a_second_critical_locks(self):
        """Audit 2, recommendation 4: one lexical critical never locks alone."""
        outcome = self.screen("I will kill everyone at the school tomorrow")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

        # A benign message still sends after the warning.
        self.assertIsNone(self.screen("habari, umeshindaje leo?"))

        outcome = self.screen("I will blow up the church on Sunday")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.LOCKED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)
        self.assertIn("Automatic safety lock", self.user.ban_reason)
        # Login stays possible: the lock refuses generation, not sign-in.
        self.assertTrue(self.user.is_active)

    def test_violation_with_self_harm_also_records_care_context(self):
        """Audit 2, M1: the violation wins, the crisis context is kept."""
        outcome = self.screen("I'm going to kill everyone at the school tomorrow and then kill myself")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)
        events = SafetyEvent.objects.filter(user_id=self.user.pk)
        self.assertEqual(events.count(), 2)
        care_event = events.filter(is_care=True).get()
        self.assertEqual(care_event.category, CATEGORY_SELF_HARM)
        self.assertEqual(care_event.action, SafetyEvent.Action.LOGGED)
        violation_event = events.filter(is_care=False).get()
        self.assertEqual(violation_event.category, CATEGORY_VIOLENCE_PLANNING)
        self.assertFalse(violation_event.is_care)

    def test_repeated_high_violations_lock_at_the_threshold(self):
        prompts = [
            "how to make a bomb at home",
            "how to build a gun in the garage",
            "how to make napalm in the kitchen",
            "how to make a silencer for a pistol",
        ]
        actions = []
        for prompt in prompts:
            outcome = self.screen(prompt)
            actions.append(outcome.action)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)
        self.assertEqual(actions[0], SafetyEvent.Action.WARNED)
        self.assertEqual(actions[-1], SafetyEvent.Action.LOCKED)

    def test_violence_planning_phrase_alone_warns_instead_of_locking(self):
        outcome = self.screen("he was arrested for a school shooting plot")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    def test_self_harm_never_blocks_or_locks(self):
        for _ in range(12):
            outcome = self.screen("I want to kill myself tonight")
            self.assertFalse(outcome.blocked)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)
        self.assertTrue(SafetyEvent.objects.filter(user_id=self.user.pk, is_care=True).exists())
        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 0)

    def test_weapons_concern_is_logged_but_not_blocked(self):
        outcome = self.screen("where can I buy a gun in Nairobi")
        self.assertFalse(outcome.blocked)
        self.assertTrue(SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_WEAPONS_CONTEXT).exists())
        # The concern itself never adds to the score.
        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 0)

    def test_prior_concern_aggravates_a_high_violation_to_critical(self):
        """The cross-session case the feature exists for.

        Weapons interest in one message escalates a later HIGH violation to
        CRITICAL, so the combination locks instead of merely warning — while
        neither message alone would have locked anything.
        """
        self.screen("where can I buy a gun in Nairobi")
        outcome = self.screen("how to make a bomb at home")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.LOCKED)
        event = SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_WEAPONS_INSTRUCTIONS).get()
        self.assertTrue(event.was_aggravated)
        self.assertEqual(event.severity, "critical")

    def test_re_sends_of_the_same_violation_deduplicate(self):
        self.screen("how to make a bomb at home")
        outcome = self.screen("how to make a bomb at home")
        self.assertTrue(outcome.blocked)
        self.assertEqual(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_WEAPONS_INSTRUCTIONS).count(),
            1,
        )

    def test_decayed_events_do_not_count(self):
        self.screen("how to make a bomb at home")
        old = SafetyEvent.objects.get(user_id=self.user.pk)
        old.created_at = timezone.now() - timedelta(days=365)
        old.save(update_fields=["created_at"])

        outcome = self.screen("how to build a gun in the garage")
        # The ancient event no longer counts: this is a first violation
        # again, so it warns rather than blocks.
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)


class SafetyLedgerPrivacyTests(SafetyLedgerTestCase):
    """No message text may ever be derivable from what the ledger stores."""

    VIOLATION_TEXT = "how to make a bomb at home"

    def test_no_message_text_is_stored(self):
        self.screen(self.VIOLATION_TEXT)
        event = SafetyEvent.objects.get(user_id=self.user.pk)
        for field in event.__dict__.values():
            if isinstance(field, str):
                self.assertNotEqual(field, self.VIOLATION_TEXT)
                self.assertNotIn(self.VIOLATION_TEXT, field)

    def test_fingerprint_is_irreversible_and_distinct(self):
        first = fingerprint_text(self.VIOLATION_TEXT)
        second = fingerprint_text("an entirely different message")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, self.VIOLATION_TEXT)
        self.assertEqual(len(first), 64)

    def test_fingerprint_survives_trivial_re_spelling(self):
        self.assertEqual(
            fingerprint_text(self.VIOLATION_TEXT),
            fingerprint_text("h0w to make a b0mb at home"),
        )


class SafetyLedgerAdminActionTests(SafetyLedgerTestCase):
    def setUp(self):
        super().setUp()
        self.admin = _make_user(username="admin", email="admin@example.com", verified=True, is_staff=True)

    def _lock_via_score(self):
        prompts = [
            "how to make a bomb at home",
            "how to build a gun in the garage",
            "how to make napalm in the kitchen",
            "how to make a silencer for a pistol",
        ]
        for prompt in prompts:
            self.screen(prompt)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)

    def test_reversal_below_threshold_unlocks_automatically(self):
        self._lock_via_score()
        events = SafetyEvent.objects.filter(user_id=self.user.pk, is_reversed=False)
        self.assertGreaterEqual(events.count(), 4)

        # Reverse enough events to drop below the threshold.
        reversed_count = 0
        for event in events:
            result = SafetyLedgerService.reverse_event(event=event, admin_user=self.admin, reason="false positive")
            self.assertTrue(result["event"].is_reversed)
            reversed_count += 1
            if result["unlocked"]:
                break

        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)
        self.assertEqual(self.user.ban_reason, "")
        self.assertTrue(
            AdminActionLog.objects.filter(action_type=AdminActionLog.ActionType.SAFETY_EVENT_REVERSE).exists()
        )
        self.assertTrue(
            AdminActionLog.objects.filter(action_type=AdminActionLog.ActionType.SAFETY_ACCOUNT_UNLOCK).exists()
        )

    def test_critical_lock_requires_explicit_unlock(self):
        self.screen("I will kill everyone at the school tomorrow")
        self.screen("I will blow up the church on Sunday")
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)

        for event in SafetyEvent.objects.filter(user_id=self.user.pk, is_reversed=False):
            result = SafetyLedgerService.reverse_event(event=event, admin_user=self.admin, reason="misclassified")
            self.assertTrue(result["event"].is_reversed)
        # No unreversed critical event and no score left — the account is
        # released automatically once the last causing event is reversed.
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    def test_unlock_requires_safety_locked_account(self):
        updated = SafetyLedgerService.unlock_account(user=self.user, admin_user=self.admin, reason="not locked anyway")
        self.assertEqual(updated.account_status, CustomUser.AccountStatus.ACTIVE)
        self.assertFalse(
            AdminActionLog.objects.filter(action_type=AdminActionLog.ActionType.SAFETY_ACCOUNT_UNLOCK).exists()
        )


# ======================================================================
# The send path
# ======================================================================


@override_settings(SAFETY_LEDGER_MODE="enforce")
class SafetySendPathTestCase(TestCase):
    def setUp(self):
        cache.clear()  # ChatMessageThrottle is 10/minute on the process cache
        self.character = _make_character(name="Zuri", slug="zuri")
        self.user = _make_user(username="sender", email="sender@example.com", verified=True)
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
                data={"username": "sender", "password": "pw123456"},
                content_type="application/json",
            )
        return resp.json()["data"]["access_token"]

    def post_send(self, content="habari", **extra):
        with mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = self.client.post(
                send_url(self.conversation.conversation_id),
                data={"content": content},
                content_type="application/json",
                **self.auth,
                **extra,
            )
        return response, dispatch


class SafetySendPathTests(SafetySendPathTestCase):
    def test_violation_is_refused_before_storage_or_billing(self):
        response, dispatch = self.post_send("how to make a bomb at home")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "message_safety_blocked")
        self.assertFalse(dispatch.called)
        self.assertFalse(Message.objects.filter(conversation=self.conversation, content__contains="bomb").exists())
        # The warning event was recorded.
        self.assertTrue(SafetyEvent.objects.filter(user_id=self.user.pk).exists())

    def test_normal_message_still_sends(self):
        response, dispatch = self.post_send("habari yako leo?")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(dispatch.called)

    def test_self_harm_message_passes_through_with_care_event(self):
        response, dispatch = self.post_send("I want to kill myself tonight")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(dispatch.called)
        self.assertTrue(SafetyEvent.objects.filter(user_id=self.user.pk, is_care=True).exists())

    def test_safety_locked_account_is_refused_per_send(self):
        self.user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        self.user.save(update_fields=["account_status"])

        response, dispatch = self.post_send("habari")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "account_safety_locked")
        self.assertFalse(dispatch.called)

    def test_swahili_user_receives_swahili_copy(self):
        self.user.preferred_language = "sw"
        self.user.save(update_fields=["preferred_language"])

        response, _ = self.post_send("jinsi ya kutengeneza bomu")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tulizuia ujumbe huu", response.json()["message"])

    def test_critical_violation_locks_and_next_send_is_refused(self):
        """Audit 2, recommendation 4, end to end: the first critical warns and
        normal messages still send; the second distinct critical locks, and
        after the lock every send is refused."""
        response, dispatch = self.post_send("I will kill everyone at the school tomorrow")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(dispatch.called)

        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

        response, dispatch = self.post_send("habari")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(dispatch.called)

        response, dispatch = self.post_send("I will blow up the church on Sunday")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(dispatch.called)

        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)

        response, dispatch = self.post_send("habari")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "account_safety_locked")
        self.assertFalse(dispatch.called)

    def test_safety_locked_account_cannot_generate_images(self):
        self.user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        self.user.save(update_fields=["account_status"])

        response = self.client.post(
            image_url(self.conversation.conversation_id),
            data={"prompt": "zuri at the beach"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "account_safety_locked")

    def test_image_prompt_violation_is_ledgered(self):
        response = self.client.post(
            image_url(self.conversation.conversation_id),
            data={"prompt": "how to make a bomb"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "message_safety_blocked")
        self.assertTrue(SafetyEvent.objects.filter(user_id=self.user.pk).exists())


class GuestSafetyScreenTests(SafetySendPathTestCase):
    """Guests: the deterministic floor applies, the ledger does not."""

    def setUp(self):
        super().setUp()
        self.guest_client = Client()
        response = self.guest_client.post("/api/v1/guest/session/", data={}, content_type="application/json")
        self.assertIn(response.status_code, (200, 201))
        from django.conf import settings as project_settings

        raw = self.guest_client.cookies[project_settings.GUEST_CREDENTIAL_COOKIE_NAME].value
        from capp.classes.guest.guest_identity_service import hash_credential
        from capp.models import GuestIdentity

        self.identity = GuestIdentity.objects.get(credential_hash=hash_credential(raw))
        self.guest_conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=self.identity.owner_key,
        )

    def test_guest_violation_is_blocked_without_a_ledger_event(self):
        with mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = self.guest_client.post(
                send_url(self.guest_conversation.conversation_id),
                data={"content": "how to make a bomb at home"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "message_safety_blocked")
        self.assertFalse(dispatch.called)
        # No account to record against — the ledger stays empty.
        self.assertEqual(SafetyEvent.objects.count(), 0)

    def test_guest_care_message_passes_through(self):
        with mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = self.guest_client.post(
                send_url(self.guest_conversation.conversation_id),
                data={"content": "I want to kill myself tonight"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(dispatch.called)
        self.assertEqual(SafetyEvent.objects.count(), 0)


# ======================================================================
# Generation-surface enforcement for safety-locked accounts
# ======================================================================


class SafetyLockEnforcementTests(TestCase):
    """A safety-locked account must not generate on any surface.

    The lock is deliberately NOT a login ban: the user can still sign in to
    read their status and appeal through support. Every surface that spends
    a model call or Sparks on the user's behalf must therefore re-check
    ``account_can_generate`` per request — the agent gateway keys would
    otherwise outlive the lock.
    """

    def test_account_can_generate_distinguishes_lock_from_sign_in(self):
        user = _make_user(username="lockcheck", email="lockcheck@example.com")
        self.assertTrue(account_can_authenticate(user))
        self.assertTrue(account_can_generate(user))

        user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        # Sign-in stays possible: the appeal path depends on it.
        self.assertTrue(account_can_authenticate(user))
        self.assertFalse(account_can_generate(user))

        user.account_status = CustomUser.AccountStatus.BANNED
        self.assertFalse(account_can_authenticate(user))
        self.assertFalse(account_can_generate(user))

    def test_agent_gateway_refuses_a_safety_locked_account(self):
        from capp.classes.agent_gateway.key_service import AgentKeyService

        user = _make_user(username="gwlocked", email="gwlocked@example.com")
        full_key, _record = AgentKeyService.create_key(user, label="Lock test key")

        # Safety-lock the account AFTER the key exists: existing keys must
        # not keep working against a locked account.
        user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        user.save(update_fields=["account_status"])

        response = self.client.post(
            "/api/v1/agent/chat/completions",
            data={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {full_key}",
        )
        self.assertEqual(response.status_code, 401)


# ======================================================================
# The admin surface
# ======================================================================


class SafetyAdminApiTests(SafetySendPathTestCase):
    def setUp(self):
        super().setUp()
        self.admin = _make_user(username="safetyadmin", email="safetyadmin@example.com", verified=True, is_staff=True)
        self.admin_client = Client()
        self.admin_auth = {
            "HTTP_AUTHORIZATION": f"Bearer {self._admin_token()}",
            "HTTP_X_CSRFTOKEN": self._csrf(),
        }

    def _admin_token(self):
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            resp = self.admin_client.post(
                "/api/v1/auth/signin/",
                data={"username": "safetyadmin", "password": "pw123456"},
                content_type="application/json",
            )
        return resp.json()["data"]["access_token"]

    def _csrf(self):
        resp = self.admin_client.get("/api/v1/auth/csrf/")
        return resp.json()["data"]["csrfToken"]

    def safety_url(self):
        return f"/api/v1/admin/users/{self.user.user_id}/safety/"

    def test_non_admin_is_refused(self):
        response = self.client.get(self.safety_url(), **self.auth)
        self.assertEqual(response.status_code, 403)

    def test_summary_and_events_contain_metadata_only(self):
        self.post_send("how to make a bomb at home")

        response = self.admin_client.get(self.safety_url(), **self.admin_auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]

        self.assertEqual(data["summary"]["active_score"], 3)
        self.assertEqual(data["summary"]["lock_threshold"], 10)
        self.assertFalse(data["summary"]["is_safety_locked"])
        self.assertEqual(len(data["events"]), 1)
        event = data["events"][0]
        self.assertEqual(event["category"], CATEGORY_WEAPONS_INSTRUCTIONS)
        self.assertNotIn("content", event)
        self.assertNotIn("text", event)
        # conversation_id is a correlation handle and is deliberately not
        # exposed on the review surface.
        self.assertNotIn("conversation_id", event)

        # Privacy: the serialized payload must not contain the message.
        self.assertNotIn("how to make a bomb", response.content.decode())

    def test_reverse_requires_a_reason(self):
        self.post_send("how to make a bomb at home")
        event = SafetyEvent.objects.get(user_id=self.user.pk)

        response = self.admin_client.post(
            f"{self.safety_url()}events/{event.event_id}/reverse/",
            data={"reason": "   "},
            content_type="application/json",
            **self.admin_auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "reason_required")

    def test_reverse_recomputes_the_lock(self):
        prompts = [
            "how to make a bomb at home",
            "how to build a gun in the garage",
            "how to make napalm in the kitchen",
            "how to make a silencer for a pistol",
        ]
        for prompt in prompts:
            self.post_send(prompt)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)

        events = SafetyEvent.objects.filter(user_id=self.user.pk, is_reversed=False)
        unlocked = False
        for event in events:
            response = self.admin_client.post(
                f"{self.safety_url()}events/{event.event_id}/reverse/",
                data={"reason": "false positive after review"},
                content_type="application/json",
                **self.admin_auth,
            )
            self.assertEqual(response.status_code, 200)
            unlocked = unlocked or response.json()["data"]["unlocked"]
        self.assertTrue(unlocked)

        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    def test_unlock_endpoint_requires_safety_locked(self):
        response = self.admin_client.post(
            f"{self.safety_url()}unlock/",
            data={"reason": "reviewed"},
            content_type="application/json",
            **self.admin_auth,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["data"], "account_not_safety_locked")

    def test_unlock_endpoint_restores_the_account(self):
        self.post_send("I will kill everyone at the school tomorrow")
        self.post_send("I will blow up the church on Sunday")
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.SAFETY_LOCKED)

        response = self.admin_client.post(
            f"{self.safety_url()}unlock/",
            data={"reason": "reviewed with the user, cleared"},
            content_type="application/json",
            **self.admin_auth,
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)


# ======================================================================
# Deployment modes (SAFETY_LEDGER_MODE)
# ======================================================================


class SafetyLedgerShadowModeTests(TestCase):
    """Shadow is the default: measure, never punish.

    The 2026-10-04 audit showed the classifier locking ordinary users, so
    the ledger ships measuring its live false-positive rate before it blocks
    or locks anything. Shadow events must be inert forever: they never
    count, aggravate or block a reversal, and flipping to enforce does not
    retroactively punish what was measured in shadow.
    """

    def setUp(self):
        self.user = _make_user(username="shadowuser", email="shadow@example.com", verified=True)
        self.conversation_id = uuid.uuid4()

    def _screen(self, text):
        return SafetyLedgerService.screen_message(
            user=self.user,
            conversation_id=self.conversation_id,
            text=text,
            language="en",
        )

    def test_violation_is_recorded_but_not_blocked(self):
        outcome = self._screen("I will kill everyone at the school tomorrow")
        self.assertIsNotNone(outcome)
        self.assertFalse(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.SHADOWED)
        event = SafetyEvent.objects.get(user_id=self.user.pk)
        self.assertEqual(event.category, CATEGORY_VIOLENCE_PLANNING)
        self.assertEqual(event.action, SafetyEvent.Action.SHADOWED)
        self.assertEqual(event.severity, "critical")
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    def test_concern_is_shadowed_so_it_cannot_aggravate_later(self):
        self._screen("where can I buy a gun in Nairobi")
        event = SafetyEvent.objects.get(user_id=self.user.pk)
        self.assertEqual(event.action, SafetyEvent.Action.SHADOWED)

    def test_shadow_violations_are_deduplicated(self):
        """Audit 2, L3: re-sending one flagged message must not flood rows."""
        self._screen("how to make a bomb at home")
        self._screen("how to make a bomb at home")
        self._screen("how to make a bomb at home")
        self.assertEqual(SafetyEvent.objects.filter(user_id=self.user.pk).count(), 1)

    def test_care_is_still_recorded_as_care(self):
        self._screen("I want to kill myself tonight")
        event = SafetyEvent.objects.get(user_id=self.user.pk)
        self.assertTrue(event.is_care)
        self.assertEqual(event.action, SafetyEvent.Action.LOGGED)

    def test_shadow_events_never_count_when_enforcement_begins(self):
        """The whole point of shadow: history measured in shadow stays inert."""
        with override_settings(SAFETY_LEDGER_MODE="shadow"):
            self._screen("I will kill everyone at the school tomorrow")
            self._screen("where can I buy a gun in Nairobi")

        self.assertEqual(SafetyLedgerService.active_score(self.user.pk), 0)
        self.assertFalse(SafetyLedgerService.has_critical_event(self.user.pk))

        # Flip to enforce: the same violation now recorded must be treated as
        # a FIRST violation (warned), not aggravated by the shadow history.
        with override_settings(SAFETY_LEDGER_MODE="enforce"):
            outcome = self._screen("how to make a bomb at home")
        self.assertTrue(outcome.blocked)
        self.assertEqual(outcome.action, SafetyEvent.Action.WARNED)
        event = SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_WEAPONS_INSTRUCTIONS).get()
        self.assertFalse(event.was_aggravated)
        self.user.refresh_from_db()
        self.assertEqual(self.user.account_status, CustomUser.AccountStatus.ACTIVE)

    def test_off_mode_records_nothing(self):
        with override_settings(SAFETY_LEDGER_MODE="off"):
            outcome = self._screen("I will kill everyone at the school tomorrow")
        self.assertIsNone(outcome)
        self.assertFalse(SafetyEvent.objects.exists())


class GuestLedgerModeTests(TestCase):
    """Guests are only blocked by the floor while the ledger enforces."""

    def setUp(self):
        cache.clear()
        self.character = _make_character(name="Guesta", slug="guesta")
        self.client_obj = Client()
        response = self.client_obj.post("/api/v1/guest/session/", data={}, content_type="application/json")
        self.assertIn(response.status_code, (200, 201))
        from django.conf import settings as project_settings

        raw = self.client_obj.cookies[project_settings.GUEST_CREDENTIAL_COOKIE_NAME].value
        from capp.classes.guest.guest_identity_service import hash_credential
        from capp.models import GuestIdentity

        identity = GuestIdentity.objects.get(credential_hash=hash_credential(raw))
        self.conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=identity.owner_key,
        )

    def _send(self, content):
        with mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = self.client_obj.post(
                send_url(self.conversation.conversation_id),
                data={"content": content},
                content_type="application/json",
            )
        return response, dispatch

    def test_guest_violation_passes_in_shadow_mode(self):
        response, dispatch = self._send("how to make a bomb at home")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(dispatch.called)

    def test_guest_violation_is_blocked_in_enforce_mode(self):
        with override_settings(SAFETY_LEDGER_MODE="enforce"):
            response, dispatch = self._send("how to make a bomb at home")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(dispatch.called)


# ======================================================================
# Safety-lock enforcement on every generation surface (audit F2)
# ======================================================================


class SafetyLockedGenerationSurfacesTests(TestCase):
    """A safety-locked account must not spend Sparks anywhere.

    Chat send, chat images, the gateway, playground, reminders and proactive
    paths were already enforced. The 2026-10-04 audit found three more
    surfaces that kept generating for locked accounts: character avatar
    generation, character gallery generation, and accepting a photo offer.
    """

    def setUp(self):
        cache.clear()
        self.character = _make_character(name="Neema", slug="neema-f2")
        self.user = _make_user(username="f2user", email="f2@example.com", verified=True)
        SparkPool.objects.create(user=self.user, sparks=10000, token_balance=0, is_active=True)
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.user.account_status = CustomUser.AccountStatus.SAFETY_LOCKED
        self.user.save(update_fields=["account_status"])

        self.client_obj = Client()
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            resp = self.client_obj.post(
                "/api/v1/auth/signin/",
                data={"username": "f2user", "password": "pw123456"},
                content_type="application/json",
            )
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {resp.json()['data']['access_token']}"}

    def test_locked_account_cannot_generate_an_avatar(self):
        response = self.client_obj.post(
            f"/api/v1/characters/{self.character.character_id}/generate-avatar/",
            data={"prompt": "portrait, high quality"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "account_safety_locked")

    def test_locked_account_cannot_generate_a_gallery_image(self):
        response = self.client_obj.post(
            f"/api/v1/characters/{self.character.character_id}/gallery/generate/",
            data={"prompt": "portrait, high quality"},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "account_safety_locked")

    def test_locked_account_cannot_accept_a_photo_offer(self):
        offer = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.AI,
            content="",
            status=Message.StatusChoices.SENT,
            metadata={
                "type": "photo_offer",
                "prompt": "a photo at the beach",
                "offer_operation_id": "offer-op-f2",
                "offer_status": "pending",
            },
        )
        response = self.client_obj.post(
            f"/api/v1/conversations/{self.conversation.conversation_id}/photo-offers/{offer.message_id}/accept/",
            data={},
            content_type="application/json",
            **self.auth,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["data"], "account_safety_locked")
