# capp/tests_generated_image_screening.py
"""
Output-side child-safety screening for AI-generated images.

Before this change, a generated image was only protected by prompt-side
`ImagePromptPolicy`; the *bytes fal or Stability returned* were never run
through the child-safety moderation that user-uploaded photos go through.
These tests pin the new contract:

* every generated image (avatar and gallery, both providers) is screened
  against the same fail-closed moderation used for uploads, before the
  bytes are persisted or shown;
* a blocked image is a hard failure: refund the reservation, persist
  nothing, return one of the `generated_image_moderation_*` reasons;
* a moderation outage is also a hard failure (fail closed) — it must not
  let an unreviewed image through;
* a blocked image writes a `generated_image_child_safety` Safety Ledger
  event so a repeat requester is visible to admin review, with the same
  narrow posture as uploads: one block never locks an account;
* the ledger write is best-effort — a ledger outage must not change the
  refusal itself.
"""

from unittest import mock

from django.test import TestCase, override_settings

from capp.classes.characters.character_service import CharacterService
from capp.classes.moderation import photo_moderation
from capp.classes.moderation.photo_moderation import (
    GENERATED_IMAGE_BLOCKED_REASON,
    GENERATED_IMAGE_UNAVAILABLE_REASON,
)
from capp.classes.moderation.safety_ledger import (
    CATEGORY_GENERATED_IMAGE_CHILD_SAFETY,
    CATEGORY_GENERATED_IMAGE_DEATH,
    CATEGORY_GENERATED_IMAGE_GORE,
    SafetyLedgerService,
)
from capp.models import CustomUser, SafetyEvent
from capp.tests import _make_character, _make_user


def _png_bytes():
    """Real tiny PNG so `_prepare_screening_copy` can decode it."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


class GeneratedImageScreeningUnitTests(TestCase):
    """Unit-level tests for `_screen_generated_image` and its ledger write."""

    def setUp(self):
        self.user = _make_user()
        self.character = _make_character(self.user)

    def test_allowed_image_returns_none(self):
        with mock.patch.object(
            photo_moderation,
            "moderate_photo",
            return_value=photo_moderation.ModerationDecision(action="allow", provider="mistral_vlm"),
        ):
            self.assertIsNone(
                CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="fal_seedream")
            )

    def test_blocked_image_returns_blocked_reason(self):
        with mock.patch.object(
            photo_moderation,
            "moderate_photo",
            return_value=photo_moderation.ModerationDecision(
                action="block", provider="mistral_vlm", categories=["minor_nudity"]
            ),
        ):
            self.assertEqual(
                CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="fal_seedream"),
                GENERATED_IMAGE_BLOCKED_REASON,
            )

    def test_unavailable_moderation_fails_closed(self):
        with mock.patch.object(
            photo_moderation,
            "moderate_photo",
            side_effect=photo_moderation.PhotoModerationUnavailable("outage"),
        ):
            self.assertEqual(
                CharacterService._screen_generated_image(b"any", actor=self.user, source_provider="fal_seedream"),
                GENERATED_IMAGE_UNAVAILABLE_REASON,
            )

    def test_unexpected_error_also_fails_closed(self):
        with mock.patch.object(
            photo_moderation,
            "moderate_photo",
            side_effect=RuntimeError("unexpected"),
        ):
            self.assertEqual(
                CharacterService._screen_generated_image(b"any", actor=self.user, source_provider="fal_seedream"),
                GENERATED_IMAGE_UNAVAILABLE_REASON,
            )

    def test_blocked_image_records_generated_image_safety_event(self):
        with override_settings(SAFETY_LEDGER_MODE="enforce"):
            with mock.patch.object(
                photo_moderation,
                "moderate_photo",
                return_value=photo_moderation.ModerationDecision(
                    action="block", provider="mistral_vlm", categories=["minor_nudity"]
                ),
            ):
                CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="fal_seedream")

            event = SafetyEvent.objects.filter(
                user_id=self.user.pk,
                category=CATEGORY_GENERATED_IMAGE_CHILD_SAFETY,
            ).first()
            self.assertIsNotNone(event)
            self.assertEqual(event.action, SafetyEvent.Action.BLOCKED)
            self.assertIn("fal_seedream", event.classifier_version)

    def test_shadow_mode_records_shadowed_not_blocked(self):
        with override_settings(SAFETY_LEDGER_MODE="shadow"):
            with mock.patch.object(
                photo_moderation,
                "moderate_photo",
                return_value=photo_moderation.ModerationDecision(
                    action="block", provider="mistral_vlm", categories=["minor_nudity"]
                ),
            ):
                CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="stability")

            event = SafetyEvent.objects.filter(
                user_id=self.user.pk,
                category=CATEGORY_GENERATED_IMAGE_CHILD_SAFETY,
            ).first()
            self.assertIsNotNone(event)
            self.assertEqual(event.action, SafetyEvent.Action.SHADOWED)

    def test_ledger_failure_does_not_change_the_refusal(self):
        with mock.patch.object(
            photo_moderation,
            "moderate_photo",
            return_value=photo_moderation.ModerationDecision(
                action="block", provider="mistral_vlm", categories=["minor_nudity"]
            ),
        ):
            with mock.patch.object(
                SafetyLedgerService,
                "record_generated_image_block",
                side_effect=RuntimeError("ledger outage"),
            ):
                # The refusal itself must still be returned.
                self.assertEqual(
                    CharacterService._screen_generated_image(
                        _png_bytes(), actor=self.user, source_provider="fal_seedream"
                    ),
                    GENERATED_IMAGE_BLOCKED_REASON,
                )

    def test_guest_actor_records_no_safety_event(self):
        # A guest has no CustomUser, so there is nothing to ledger against —
        # the refusal still happens, just without an account-level event.
        with override_settings(SAFETY_LEDGER_MODE="enforce"):
            with mock.patch.object(
                photo_moderation,
                "moderate_photo",
                return_value=photo_moderation.ModerationDecision(
                    action="block", provider="mistral_vlm", categories=["minor_nudity"]
                ),
            ):
                self.assertEqual(
                    CharacterService._screen_generated_image(_png_bytes(), actor=None, source_provider="fal_seedream"),
                    GENERATED_IMAGE_BLOCKED_REASON,
                )
            self.assertFalse(SafetyEvent.objects.filter(category=CATEGORY_GENERATED_IMAGE_CHILD_SAFETY).exists())

    def test_one_generated_image_block_never_locks_the_account(self):
        with override_settings(SAFETY_LEDGER_MODE="enforce"):
            with mock.patch.object(
                photo_moderation,
                "moderate_photo",
                return_value=photo_moderation.ModerationDecision(
                    action="block", provider="mistral_vlm", categories=["minor_nudity"]
                ),
            ):
                CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="fal_seedream")

            self.user.refresh_from_db()
            self.assertNotEqual(
                self.user.account_status,
                CustomUser.AccountStatus.SAFETY_LOCKED,
            )


class ModerationProviderPassthroughTests(TestCase):
    """`moderate_generated_image` reuses the same provider and fail-closed
    behaviour as `moderate_photo` — a regression here would silently weaken
    one path or the other."""

    def test_same_provider_is_used(self):
        decision = photo_moderation.ModerationDecision(action="allow", provider="mistral_vlm")
        with mock.patch.object(photo_moderation, "moderate_photo", return_value=decision) as inner:
            out = photo_moderation.moderate_generated_image(b"x", "image/webp")
            self.assertEqual(out.action, "allow")
            inner.assert_called_once_with(b"x", "image/webp")

    def test_debug_without_provider_allows_like_upload_path(self):
        # In DEBUG with no provider configured, both entry points allow —
        # this mirrors the documented dev-only carve-out on `moderate_photo`.
        with override_settings(DEBUG=True):
            with mock.patch.object(photo_moderation, "moderation_provider_name", return_value=None):
                out = photo_moderation.moderate_generated_image(b"x", "image/webp")
                self.assertEqual(out.action, "allow")
                self.assertEqual(out.provider, "debug")


class GeneratedImageRealContractTests(TestCase):
    """The bundled providers RETURN a block decision; they never raise on block.

    Pins the real end-to-end contract against a mocked Mistral reply, so a
    regression that goes back to ignoring the returned decision (audit B1)
    fails these tests.
    """

    _BLOCK = {
        "contains_minor": True,
        "minor_nudity": True,
        "minor_sexual_content": False,
        "bestiality": False,
        "gore": False,
        "death": False,
        "uncertain": False,
    }

    def setUp(self):
        self.user = _make_user()

    def _mistral_reply(self, verdict):
        import json

        return {"choices": [{"message": {"content": json.dumps(verdict)}}]}

    def test_returned_block_decision_is_refused_and_recorded(self):
        decision = photo_moderation.ModerationDecision(
            action="block", provider="mistral_vlm", categories=["minor_nudity"]
        )
        with override_settings(SAFETY_LEDGER_MODE="enforce"):
            with mock.patch.object(photo_moderation, "moderate_photo", return_value=decision):
                reason = CharacterService._screen_generated_image(
                    _png_bytes(), actor=self.user, source_provider="fal_seedream"
                )
        self.assertEqual(reason, GENERATED_IMAGE_BLOCKED_REASON)
        self.assertTrue(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_GENERATED_IMAGE_CHILD_SAFETY).exists()
        )

    @override_settings(DEBUG=False, IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="test-key")
    def test_mistral_block_verdict_end_to_end(self):
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._mistral_reply(self._BLOCK),
        ):
            reason = CharacterService._screen_generated_image(_png_bytes(), actor=None, source_provider="fal_seedream")
        self.assertEqual(reason, GENERATED_IMAGE_BLOCKED_REASON)

    @override_settings(DEBUG=False, IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="test-key")
    def test_mistral_allow_verdict_end_to_end(self):
        clean = {field: False for field in self._BLOCK}
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._mistral_reply(clean),
        ):
            reason = CharacterService._screen_generated_image(_png_bytes(), actor=None, source_provider="fal_seedream")
        self.assertIsNone(reason)

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_gore_block_records_gore_not_child_safety(self):
        """A gore block must not be recorded as a child-safety offence (H6)."""
        decision = photo_moderation.ModerationDecision(action="block", provider="mistral_vlm", categories=["gore"])
        with mock.patch.object(photo_moderation, "moderate_photo", return_value=decision):
            CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="fal_seedream")
        self.assertTrue(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_GENERATED_IMAGE_GORE).exists()
        )
        self.assertFalse(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_GENERATED_IMAGE_CHILD_SAFETY).exists()
        )

    @override_settings(SAFETY_LEDGER_MODE="enforce")
    def test_death_block_records_death_not_child_safety(self):
        """A death (funeral) block must not be recorded as a child-safety offence."""
        decision = photo_moderation.ModerationDecision(action="block", provider="mistral_vlm", categories=["death"])
        with mock.patch.object(photo_moderation, "moderate_photo", return_value=decision):
            CharacterService._screen_generated_image(_png_bytes(), actor=self.user, source_provider="fal_seedream")
        self.assertTrue(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_GENERATED_IMAGE_DEATH).exists()
        )
        self.assertFalse(
            SafetyEvent.objects.filter(user_id=self.user.pk, category=CATEGORY_GENERATED_IMAGE_CHILD_SAFETY).exists()
        )

    @override_settings(DEBUG=False, IMAGE_MODERATION_PROVIDER="mistral_vlm", MISTRAL_API_KEY="test-key")
    def test_max_tokens_has_headroom_for_the_7_field_verdict(self):
        """H5: the 7-field verdict must not be cut off by the token cap."""
        clean = {field: False for field in self._BLOCK}
        with mock.patch(
            "capp.classes.ai.mistral_client.MistralClient.chat_completion",
            return_value=self._mistral_reply(clean),
        ) as completion:
            CharacterService._screen_generated_image(_png_bytes(), actor=None, source_provider="fal_seedream")
        self.assertGreaterEqual(completion.call_args.kwargs["max_tokens"], 200)

    def test_dead_animals_hunting_and_food_are_allowed_but_animal_cruelty_is_gore(self):
        """Product decision 2026-10-14: dead animals, hunting, fishing and food
        are allowed; bestiality and cruelty to animals stay blocked."""
        template = photo_moderation.MistralVLMProvider._QUERY_TEMPLATE
        death = template.split("  death:", 1)[1].split("\n", 1)[0]
        gore = template.split("  gore:", 1)[1].split("\n", 1)[0]
        bestiality = template.split("  bestiality:", 1)[1].split("\n", 1)[0]
        self.assertIn("a person appears dead", death)
        self.assertNotIn("animal appears dead", death)
        for allowed in ("Dead animals", "hunting", "fishing", "food"):
            self.assertIn(allowed, death)
        self.assertIn("cruelty to an animal", gore)
        self.assertNotIn("harm to a person or animal", gore)
        for allowed in ("dead animals", "hunting", "fishing", "slaughter or butchering for food", "food"):
            self.assertIn(allowed, gore)
        self.assertIn("between a human and an animal", bestiality)
