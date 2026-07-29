# capp/tests_billing_integration.py
"""
Per-feature coverage of reserve → settle / refund.

Every paid operation is asked the same eight questions:

* the happy path settles exactly one reservation and produces exactly one
  artifact;
* an unaffordable balance is refused before the provider is touched;
* a provider failure gives the Sparks back;
* a persistence failure gives the Sparks back;
* replaying the same key returns the first attempt's artifact and charges once;
* reusing a key for different terms is refused rather than silently charged;
* a Celery redelivery resumes rather than regenerating;
* and a broadcast failure after a durable write stays settled.

The invariant each test is really pinning down is *net Sparks moved*. A
reservation that settles has moved the price exactly once; one that refunds has
moved nothing. Anything else is the bug this branch exists to remove.
"""

import contextlib
import uuid
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from capp.classes.billing.idempotency import result_key
from capp.classes.billing.paid_features import PaidFeature
from capp.classes.characters.character_service import CharacterService
from capp.constants import (
    CHARACTER_GENERATION_COST,
    IMAGE_GENERATION_COST,
    MESSAGE_PAIR_COST,
)
from capp.models import (
    BillingReservation,
    Character,
    CharacterGalleryImage,
    Conversation,
    Message,
    SparkPool,
    SparkTransaction,
)
from capp.tests import _make_character, _make_user


@contextlib.contextmanager
def stub_image_providers(image):
    """
    Stand in for both image backends at once.

    Seedream is tried first and Stability is the fallback, so patching only one
    of them leaves a live path to the network — and, worse, lets a test that
    means to simulate a failure quietly succeed. ``image`` is either the bytes
    both should return or an exception both should raise.
    """
    side_effect = image if isinstance(image, Exception) else None
    returned = None if side_effect else image

    with mock.patch.object(
        CharacterService,
        "_generate_fal_seedream_image",
        return_value=returned,
        side_effect=side_effect,
    ) as seedream, mock.patch.object(
        CharacterService,
        "_generate_ai_image",
        return_value=returned,
        side_effect=side_effect,
    ) as stability:
        yield (seedream, stability)


def net_sparks_moved(user):
    """Debits minus credits on the ledger, as a positive number of Sparks."""
    total = sum(int(t.spark_amount) for t in SparkTransaction.objects.filter(user=user))
    return -total


class BillingFeatureTestCase(TestCase):
    """Shared fixtures: one owner with a funded pool and one character."""

    def setUp(self):
        self.user = _make_user(username="billed", email="billed@example.com", verified=True)
        self.pool = SparkPool.objects.create(user=self.user, sparks=10000, token_balance=0, is_active=True)
        self.character = _make_character(name="Zola", slug="zola", created_by=self.user)

    def fund(self, sparks):
        SparkPool.objects.filter(user=self.user).update(sparks=sparks)

    def balance(self):
        return int(SparkPool.objects.get(user=self.user).sparks)

    def assertSettledOnce(self, cost):
        reservation = BillingReservation.objects.get(user=self.user)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)
        self.assertEqual(int(reservation.amount_sparks), cost)
        self.assertEqual(net_sparks_moved(self.user), cost)
        return reservation

    def assertRefunded(self, reason=None):
        reservation = BillingReservation.objects.get(user=self.user)
        self.assertEqual(reservation.status, BillingReservation.Status.REFUNDED)
        if reason is not None:
            self.assertEqual(reservation.refund_reason, reason)
        self.assertEqual(net_sparks_moved(self.user), 0)
        return reservation

    def assertProvidersNotCalled(self, providers):
        for provider in providers:
            provider.assert_not_called()


# ======================================================================
# Character generation
# ======================================================================


PERSONA = {
    "short_description": "a warm friend",
    "personality_description": "friendly and warm",
    "greeting_message": "Hi there!",
    "system_instructions": "be kind",
    "prompt_template": "{{input}}",
}


def character_payload(name="Neema"):
    return {
        "name": name,
        "short_description": "desc",
        "mood": "cheerful",
        "intro": "intro",
        "personality_description": "friendly",
        "prompt_template": "{{input}}",
        "greeting_message": "Hello!",
        "tone_style": "warm",
        "is_public": False,
    }


class CharacterGenerationBillingTests(BillingFeatureTestCase):
    def _create(self, operation_id, name="Neema", persona=PERSONA):
        request = mock.Mock(user=self.user, FILES={})
        with mock.patch.object(CharacterService, "_generate_persona_fields", return_value=persona) as persona_call:
            result = CharacterService.create_character(
                character_payload(name), request=request, operation_id=operation_id
            )
        return result, persona_call

    def test_success_settles_once_and_creates_one_character(self):
        before = self.balance()
        result, _ = self._create(str(uuid.uuid4()))

        self.assertEqual(result["status"], "success")
        self.assertEqual(Character.objects.filter(created_by=self.user).count(), 2)  # fixture + new
        self.assertEqual(self.balance(), before - CHARACTER_GENERATION_COST)
        self.assertSettledOnce(CHARACTER_GENERATION_COST)

    def test_insufficient_balance_refuses_before_the_persona_call(self):
        """
        The regression this ordering exists for: the persona is written by a
        paid Mistral call, so an unaffordable request must not reach it.
        """
        self.fund(CHARACTER_GENERATION_COST - 1)
        result, persona_call = self._create(str(uuid.uuid4()))

        self.assertEqual(result["data"], "insufficient_sparks")
        persona_call.assert_not_called()
        self.assertEqual(self.balance(), CHARACTER_GENERATION_COST - 1)
        self.assertFalse(BillingReservation.objects.exists())

    def test_persona_failure_refunds(self):
        before = self.balance()
        request = mock.Mock(user=self.user, FILES={})
        with mock.patch.object(CharacterService, "_generate_persona_fields", side_effect=RuntimeError("mistral down")):
            result = CharacterService.create_character(
                character_payload(), request=request, operation_id=str(uuid.uuid4())
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(self.balance(), before)
        self.assertRefunded("unhandled_error")
        self.assertFalse(Character.objects.filter(name="Neema").exists())

    def test_persistence_failure_refunds_and_leaves_no_character(self):
        before = self.balance()
        request = mock.Mock(user=self.user, FILES={})
        with mock.patch.object(CharacterService, "_generate_persona_fields", return_value=PERSONA):
            with mock.patch.object(Character.objects, "create", side_effect=RuntimeError("db gone")):
                result = CharacterService.create_character(
                    character_payload(), request=request, operation_id=str(uuid.uuid4())
                )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(self.balance(), before)
        self.assertRefunded("unhandled_error")

    def test_replaying_the_same_key_returns_the_first_character_and_charges_once(self):
        operation_id = str(uuid.uuid4())
        before = self.balance()

        first, _ = self._create(operation_id)
        second, persona_call = self._create(operation_id)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(
            first["data"]["character"]["character_id"],
            second["data"]["character"]["character_id"],
            "a replay must return the character the first attempt created",
        )
        # The replay short-circuits before the provider and before billing.
        persona_call.assert_not_called()
        self.assertEqual(Character.objects.filter(name="Neema").count(), 1)
        self.assertEqual(self.balance(), before - CHARACTER_GENERATION_COST)
        self.assertSettledOnce(CHARACTER_GENERATION_COST)

    def test_reusing_a_key_for_a_different_feature_is_refused(self):
        operation_id = str(uuid.uuid4())
        PaidFeature.reserve(
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            operation_id=operation_id,
            user=self.user,
        )
        before = self.balance()

        result, persona_call = self._create(operation_id)

        self.assertEqual(result["data"], "billing_error")
        persona_call.assert_not_called()
        self.assertEqual(self.balance(), before)
        self.assertEqual(BillingReservation.objects.count(), 1)


# ======================================================================
# Avatar generation
# ======================================================================


class AvatarBillingTests(BillingFeatureTestCase):
    def setUp(self):
        super().setUp()
        # The first avatar for a character is free, so give this one an avatar
        # already: every generation under test is a paid re-roll.
        self.character.avatar = "avatars/existing.webp"
        self.character.avatar_source = Character.AvatarSource.AI_GENERATED
        self.character.save(update_fields=["avatar", "avatar_source"])

    def _generate(self, operation_id, image=b"avatar-bytes"):
        with stub_image_providers(image) as providers:
            result = CharacterService.generate_avatar(
                character=self.character,
                prompt="a portrait",
                actor=self.user,
                operation_id=operation_id,
            )
        return result, providers

    def test_success_settles_once(self):
        before = self.balance()
        result, _ = self._generate(str(uuid.uuid4()))

        self.assertEqual(result["status"], "success")
        self.assertEqual(self.balance(), before - IMAGE_GENERATION_COST)
        self.assertSettledOnce(IMAGE_GENERATION_COST)

    def test_insufficient_balance_refuses_before_the_provider(self):
        self.fund(IMAGE_GENERATION_COST - 1)
        result, providers = self._generate(str(uuid.uuid4()))

        self.assertEqual(result["data"], "insufficient_sparks")
        self.assertProvidersNotCalled(providers)
        self.assertFalse(BillingReservation.objects.exists())

    def test_provider_returning_nothing_refunds(self):
        before = self.balance()
        result, _ = self._generate(str(uuid.uuid4()), image=None)

        self.assertEqual(result["status"], "fail")
        self.assertEqual(self.balance(), before)
        self.assertRefunded("provider_failed")

    def test_provider_raising_refunds(self):
        before = self.balance()
        result, _ = self._generate(str(uuid.uuid4()), image=RuntimeError("fal down"))

        self.assertEqual(result["status"], "fail")
        self.assertEqual(self.balance(), before)
        self.assertRefunded("unhandled_error")


# ======================================================================
# Gallery / in-chat image generation
# ======================================================================


class GalleryImageBillingTests(BillingFeatureTestCase):
    def _generate(self, operation_id, image=b"gallery-bytes", **kwargs):
        with stub_image_providers(image) as providers:
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="a portrait",
                actor=self.user,
                is_public=False,
                operation_id=operation_id,
                **kwargs,
            )
        return result, providers

    def test_success_settles_once_and_stores_one_image(self):
        before = self.balance()
        result, _ = self._generate(str(uuid.uuid4()))

        self.assertEqual(result["status"], "success")
        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 1)
        self.assertEqual(self.balance(), before - IMAGE_GENERATION_COST)
        self.assertSettledOnce(IMAGE_GENERATION_COST)

    def test_insufficient_balance_refuses_before_the_provider(self):
        self.fund(IMAGE_GENERATION_COST - 1)
        result, providers = self._generate(str(uuid.uuid4()))

        self.assertEqual(result["data"], "insufficient_sparks")
        self.assertProvidersNotCalled(providers)
        self.assertFalse(CharacterGalleryImage.objects.exists())
        self.assertFalse(BillingReservation.objects.exists())

    def test_provider_failure_refunds(self):
        before = self.balance()
        result, _ = self._generate(str(uuid.uuid4()), image=None)

        self.assertEqual(result["status"], "fail")
        self.assertEqual(self.balance(), before)
        self.assertRefunded("provider_failed")
        self.assertFalse(CharacterGalleryImage.objects.exists())

    def test_replay_returns_the_stored_image_without_regenerating(self):
        operation_id = str(uuid.uuid4())
        before = self.balance()

        first, _ = self._generate(operation_id)
        second, providers = self._generate(operation_id)

        self.assertProvidersNotCalled(providers)
        self.assertEqual(first["data"]["image"]["id"], second["data"]["image"]["id"])
        self.assertEqual(CharacterGalleryImage.objects.count(), 1)
        self.assertEqual(self.balance(), before - IMAGE_GENERATION_COST)
        self.assertSettledOnce(IMAGE_GENERATION_COST)

    def test_deferred_settlement_leaves_the_hold_open_for_the_caller(self):
        """
        The in-chat path stores the picture, then writes the Message that links
        to it. Settling at the first step would call the operation finished
        before the user can see anything, so the task settles instead.
        """
        result, _ = self._generate(str(uuid.uuid4()), defer_settlement=True)

        self.assertEqual(result["status"], "success")
        reservation = BillingReservation.objects.get(user=self.user)
        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)
        self.assertEqual(net_sparks_moved(self.user), IMAGE_GENERATION_COST)


# ======================================================================
# Chat message pairs, including the Celery task's retry behaviour
# ======================================================================


class ChatMessagePairBillingTests(BillingFeatureTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)

    def _user_message(self, operation_id, content="hello"):
        return Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content=content,
            status=Message.StatusChoices.SENT,
            client_operation_id=operation_id,
        )

    def _run(self, message, reply="Hi there!", side_effect=None):
        from capp.classes.ai.cortecs_client import CortecsClient
        from capp.classes.ai.prompt_builder import PromptBuilder
        from capp.tasks import process_ai_response

        completion = {
            "choices": [{"message": {"content": reply}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "test-model",
        }
        # PromptBuilder reads system prompts from the environment, which the
        # test settings do not provide; the prompt's contents are not what these
        # tests are about.
        with mock.patch.object(
            PromptBuilder,
            "build_messages",
            return_value=[{"role": "system", "content": "sys"}, {"role": "user", "content": message.content}],
        ), mock.patch.object(
            CortecsClient,
            "chat_completion",
            return_value=completion,
            side_effect=side_effect,
        ) as provider:
            process_ai_response(str(self.conversation.conversation_id), str(message.message_id))
        return provider

    def test_success_settles_once_and_stores_one_reply(self):
        before = self.balance()
        operation_id = str(uuid.uuid4())
        self._run(self._user_message(operation_id))

        replies = Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI)
        self.assertEqual(replies.count(), 1)
        self.assertEqual(replies.first().client_operation_id, result_key(operation_id))
        self.assertEqual(self.balance(), before - MESSAGE_PAIR_COST)
        self.assertSettledOnce(MESSAGE_PAIR_COST)

    def test_insufficient_balance_blocks_before_the_provider(self):
        self.fund(MESSAGE_PAIR_COST - 1)
        provider = self._run(self._user_message(str(uuid.uuid4())))

        provider.assert_not_called()
        self.assertFalse(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI).exists()
        )
        self.assertFalse(BillingReservation.objects.exists())

    def test_empty_provider_response_refunds(self):
        before = self.balance()
        self._run(self._user_message(str(uuid.uuid4())), reply="   ")

        self.assertEqual(self.balance(), before)
        self.assertRefunded("provider_returned_empty")

    def test_redelivery_replays_the_stored_reply_without_charging_again(self):
        """
        Celery redelivers on worker loss. The second run must find the reply the
        first one persisted and re-broadcast it, not buy a second one.
        """
        before = self.balance()
        operation_id = str(uuid.uuid4())
        message = self._user_message(operation_id)

        self._run(message)
        provider = self._run(message)

        provider.assert_not_called()
        self.assertEqual(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI).count(),
            1,
        )
        self.assertEqual(self.balance(), before - MESSAGE_PAIR_COST)
        self.assertSettledOnce(MESSAGE_PAIR_COST)

    def test_broadcast_failure_after_persistence_stays_settled(self):
        """
        A WebSocket that will not deliver is not a billing event: the reply is
        already durable, and a history reload recovers it. Refunding here would
        hand back Sparks for work the user can still read.
        """
        before = self.balance()
        with mock.patch("capp.tasks._send_ai_message", side_effect=RuntimeError("channel layer down")):
            self._run(self._user_message(str(uuid.uuid4())))

        self.assertEqual(self.balance(), before - MESSAGE_PAIR_COST)
        self.assertSettledOnce(MESSAGE_PAIR_COST)

        # What the client would get on reload.
        reply = Message.objects.get(conversation=self.conversation, sender_type=Message.SenderType.AI)
        self.assertEqual(reply.content, "Hi there!")

    def test_a_refunded_operation_is_not_resurrected_by_a_late_retry(self):
        """
        Once an operation has failed and been paid back, replaying its key must
        not start the work again on Sparks that are no longer held.
        """
        operation_id = str(uuid.uuid4())
        message = self._user_message(operation_id)
        self._run(message, reply="")
        self.assertRefunded("provider_returned_empty")
        before = self.balance()

        provider = self._run(message)

        provider.assert_not_called()
        self.assertEqual(self.balance(), before)
        self.assertEqual(BillingReservation.objects.get(user=self.user).status, BillingReservation.Status.REFUNDED)


# ======================================================================
# Cross-cutting invariants
# ======================================================================


class BillingInvariantTests(TestCase):
    """
    Static checks. These are cheap, and they fail loudly when a new paid call
    site is added the old way — which is exactly when a reviewer is least
    likely to notice.
    """

    SOURCE_DIRS = ["capp/classes", "capp/views", "capp/tasks.py", "capp/consumers"]

    def _sources(self):
        import pathlib

        base = pathlib.Path(__file__).resolve().parent.parent
        for entry in self.SOURCE_DIRS:
            path = base / entry
            if path.is_file():
                yield path, path.read_text()
                continue
            for file in path.rglob("*.py"):
                yield file, file.read_text()

    def test_no_module_reintroduces_the_charge_after_provider_pair(self):
        offenders = [str(p) for p, text in self._sources() if "charge_usage(" in text or "validate_usage(" in text]
        self.assertEqual(offenders, [], "charge-after-provider billing is gone; use PaidFeature instead")

    def test_balances_are_only_mutated_through_the_locking_primitives(self):
        """
        ``sparks`` may be assigned in balances.py (which holds the row lock) and
        nowhere else. Everything else must go through debit_locked /
        credit_locked so the non-negative constraint is enforced under a lock.
        """
        import pathlib
        import re

        base = pathlib.Path(__file__).resolve().parent
        allowed = {base / "classes" / "billing" / "balances.py"}
        pattern = re.compile(r"\.sparks\s*(=|\+=|-=)[^=]")

        offenders = []
        for path, text in self._sources():
            if pathlib.Path(path) in allowed:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line) and "sparks_" not in line:
                    offenders.append(f"{path}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "write balances through capp.classes.billing.balances")

    def test_prices_are_unchanged(self):
        """
        This branch moves *when* Sparks are taken, never how many. Pinning the
        four prices makes an accidental reprice a test failure rather than a
        billing incident.
        """
        from capp.classes.billing.paid_features import FEATURE_COST

        self.assertEqual(
            {feature: int(cost) for feature, cost in FEATURE_COST.items()},
            {
                BillingReservation.Feature.MESSAGE_PAIR: 85,
                BillingReservation.Feature.CHARACTER_GENERATION: 500,
                BillingReservation.Feature.CHARACTER_EDIT: 50,
                BillingReservation.Feature.IMAGE_GENERATION: 450,
            },
        )

    def test_every_paid_feature_has_a_price(self):
        from capp.classes.billing.paid_features import FEATURE_COST

        self.assertEqual(
            sorted(FEATURE_COST),
            sorted(choice for choice, _ in BillingReservation.Feature.choices),
        )


class ReservationAuditTrailTests(BillingFeatureTestCase):
    """A reservation must be resolvable back to what it paid for."""

    def test_a_reservation_records_its_operation_reference_and_ledger_rows(self):
        operation_id = str(uuid.uuid4())
        reservation = PaidFeature.reserve(
            feature=BillingReservation.Feature.IMAGE_GENERATION,
            operation_id=operation_id,
            user=self.user,
            operation_reference=f"gallery_image:{self.character.character_id}:{operation_id}",
        )

        self.assertEqual(reservation.idempotency_key, operation_id)
        self.assertIn(str(self.character.character_id), reservation.operation_reference)
        self.assertIsNotNone(reservation.debit_spark_transaction)
        self.assertIsNotNone(reservation.usage_charge)
        self.assertEqual(
            reservation.usage_charge.amount_charged,
            Decimal(IMAGE_GENERATION_COST) * Decimal("0.1"),
        )
        self.assertIsNotNone(reservation.reserved_at)
        self.assertLessEqual(reservation.reserved_at, timezone.now())
