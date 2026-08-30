# capp/tests_default_outfit.py
"""
Tests for the per-character default-outfit injection.

Background
----------
Image prompts are assembled from a character sheet plus a free-text user
instruction. When the assembled prompt says nothing about clothing, image
models fill the gap from gendered training-data priors — male characters
ended up rendered in female lingerie for prompts like "at the beach".

There is NO new field: the character's existing ``clothing_style``
(``Character.clothing_style``, chosen in wizard step 7 / the edit form) is
the default outfit. The prompt builder injects it ONLY when the user's
instruction is ambiguous about attire. Explicit clothing ("red dress") and
explicit nudity ("topless", "uchi") are both user intent and always win —
nothing is injected for them.
"""

from unittest import mock

from django.test import SimpleTestCase, TestCase

from capp.classes.characters.attire_detection import (
    CLOTHING_PHRASES,
    CLOTHING_TOKENS,
    CLOTHING_TOKENS_EN,
    CLOTHING_TOKENS_SW,
    NUDITY_PHRASES,
    NUDITY_TOKENS,
    NUDITY_TOKENS_EN,
    NUDITY_TOKENS_SW,
    mentions_clothing,
    mentions_clothing_or_nudity,
    mentions_nudity,
)
from capp.classes.characters.character_service import CharacterService
from capp.models import Character
from capp.tests import RECAPTCHA_OK, _bearer_token, _make_character, _make_user

_BASE_PROMPT = (
    "full-body photo, natural standing pose, believable clothing folds, "
    "realistic proportions, natural shadows, candid fashion photography"
)

# What each built-in clothing_style choice normalises to inside the prompt.
_CLOTHING_LINE = {
    "casual": "simple casual clothing",
    "formal": "clean formal clothing",
    "sporty": "sporty everyday clothing",
    "streetwear": "modern streetwear",
    "gothic": "sexy dark gothic clothing",
}


class _PromptCharacter:
    """
    Attribute stub mirroring every field ``_build_character_prompt`` reads.
    Rendering a prompt must not require a database row — the injection rule
    is pure prompt assembly and is fastest covered with SimpleTestCase.
    """

    def __init__(self, **overrides):
        self.gender = "male"
        self.visual_style = "realistic"
        self.skin_color = "dark"
        self.body_type = "athletic"
        self.eye_color = "brown"
        self.hair_length = "short"
        self.hairstyle = "short"
        self.hair_color = "black"
        self.clothing_style = "casual"
        self.country = "Kenya"
        self.age = 25
        self.nsfw_enabled = False
        for key, value in overrides.items():
            setattr(self, key, value)


def _build_prompt(character, instruction="", frame_type="gallery", base_prompt=_BASE_PROMPT):
    return CharacterService._build_character_prompt(
        character,
        base_prompt,
        frame_type=frame_type,
        primary_instruction=instruction,
    )


class AttireDetectionTests(SimpleTestCase):
    """Unit coverage for the word lists and boundary matching."""

    # ---------- Clothing detection ----------

    def test_common_english_garments_detected(self):
        for text in (
            "wearing a red dress",
            "in blue jeans and a hoodie",
            "a tailored suit",
            "white t-shirt and sneakers",
            "standing in a school uniform",
        ):
            self.assertTrue(mentions_clothing(text), text)
            self.assertTrue(mentions_clothing_or_nudity(text), text)

    def test_attire_verbs_detected(self):
        self.assertTrue(mentions_clothing("she is wearing something nice"))
        self.assertTrue(mentions_clothing("dressed for dinner"))
        self.assertTrue(mentions_clothing("fully clothed on the sofa"))

    def test_revealing_garments_count_as_clothing(self):
        # Bikini and lingerie are garments the user asked for, so the
        # character's preset outfit must not override them.
        for text in ("in a bikini by the pool", "modelling black lingerie", "underwear only"):
            self.assertTrue(mentions_clothing_or_nudity(text), text)

    def test_swahili_clothing_detected(self):
        for text in ("amevaa gauni nyekundu", "nguo za kifahari", "mavazi ya kawaida", "kuvaa suruali"):
            self.assertTrue(mentions_clothing(text), text)

    def test_case_insensitive(self):
        self.assertTrue(mentions_clothing("WEARING A RED DRESS"))
        self.assertTrue(mentions_clothing_or_nudity("Completely NAKED"))

    # ---------- Nudity detection ----------

    def test_english_nudity_detected(self):
        for text in (
            "topless on the balcony",
            "completely naked",
            "a tasteful nude portrait",
            "undressed by the window",
            "bare chest glistening",
        ):
            self.assertTrue(mentions_nudity(text), text)
            self.assertTrue(mentions_clothing_or_nudity(text), text)

    def test_swahili_nudity_detected(self):
        self.assertTrue(mentions_nudity("yu uchi kabisa"))
        self.assertTrue(mentions_clothing_or_nudity("bila nguo kitandani"))

    def test_no_clothes_phrase_detected(self):
        for text in ("with no clothes on", "no clothes, no shame", "without clothes at home"):
            self.assertTrue(mentions_clothing_or_nudity(text), text)

    # ---------- Word-boundary correctness ----------

    def test_shirtless_is_nudity_not_clothing(self):
        # The bare token "shirt" must NOT match inside "shirtless"; the
        # compound is its own nudity token (absence of the garment).
        self.assertFalse(mentions_clothing("shirtless in the sun"))
        self.assertTrue(mentions_nudity("shirtless in the sun"))
        self.assertTrue(mentions_clothing_or_nudity("shirtless in the sun"))

    def test_tshirt_counts_as_clothing(self):
        self.assertTrue(mentions_clothing("wearing a white t-shirt"))

    def test_no_false_positive_from_embedded_words(self):
        # Benign substrings must not trip the detector.
        for text in (
            "a tiramisu dessert",  # not "suit"/"bra"
            "a captain of industry",  # contains "cap" — plain text, no attire intent
            "sitting on the dresser",  # contains "dress"
            "a terrific graphic poster",
            "a well-worn hiking path",
        ):
            self.assertFalse(mentions_clothing_or_nudity(text), text)

    def test_attire_ambiguous_text_detects_nothing(self):
        for text in (
            "at the beach",
            "smiling in a sunny market",
            "looking out over Nairobi at dusk",
            "holding a cup of coffee",
            "",
            None,
        ):
            self.assertFalse(mentions_clothing_or_nudity(text), repr(text))

    # ---------- List hygiene ----------

    def test_word_lists_are_documented_and_disjoint(self):
        # Phrase members stay out of the token sets (they are matched as
        # phrases, not tokens) and clothing/nudity tokens never overlap.
        self.assertFalse(CLOTHING_TOKENS & NUDITY_TOKENS)
        self.assertIn("nguo", CLOTHING_TOKENS_SW)
        self.assertIn("uchi", NUDITY_TOKENS_SW)
        self.assertIn("bila nguo", NUDITY_PHRASES)
        self.assertIn("no clothes", NUDITY_PHRASES)
        self.assertTrue(CLOTHING_TOKENS_EN)
        self.assertTrue(NUDITY_TOKENS_EN)
        self.assertTrue(CLOTHING_PHRASES)


class DefaultOutfitInjectionTests(SimpleTestCase):
    """The prompt-builder rule: inject the character's outfit only when the
    user's instruction is ambiguous about attire."""

    # ---------- Ambiguous prompt + outfit set → injected ----------

    def test_ambiguous_instruction_injects_default_outfit(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="casual"), "at the beach")
        self.assertIn(_CLOTHING_LINE["casual"], prompt)

    def test_ambiguous_instruction_injects_each_builtin_style(self):
        for style, line in _CLOTHING_LINE.items():
            prompt = _build_prompt(_PromptCharacter(clothing_style=style), "standing in a sunny market")
            self.assertIn(line, prompt, style)

    def test_no_instruction_still_injects_outfit(self):
        # Unchanged pre-existing behaviour: no instruction → preset line.
        prompt = _build_prompt(_PromptCharacter(clothing_style="formal"), "")
        self.assertIn(_CLOTHING_LINE["formal"], prompt)

    def test_injection_is_gender_neutral(self):
        # The regression this fixes: a MALE character must keep his own
        # clothing line instead of the model guessing from training priors.
        male_prompt = _build_prompt(_PromptCharacter(gender="male", clothing_style="sporty"), "in the gym")
        self.assertIn(_CLOTHING_LINE["sporty"], male_prompt)
        self.assertNotIn("lingerie", male_prompt)

    # ---------- Explicit nudity → NOT injected ----------

    def test_topless_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="casual"), "topless on the balcony")
        self.assertNotIn(_CLOTHING_LINE["casual"], prompt)

    def test_completely_naked_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="formal"), "completely naked in the bedroom")
        self.assertNotIn(_CLOTHING_LINE["formal"], prompt)

    def test_swahili_uchi_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="sporty"), "yu uchi kabisa")
        self.assertNotIn(_CLOTHING_LINE["sporty"], prompt)

    def test_swahili_bila_nguo_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="gothic"), "bila nguo kitandani")
        self.assertNotIn(_CLOTHING_LINE["gothic"], prompt)

    # ---------- Explicit clothing → NOT injected ----------

    def test_red_dress_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="casual"), "wearing a red dress")
        self.assertNotIn(_CLOTHING_LINE["casual"], prompt)
        self.assertIn("red dress", prompt)  # the user's own words are kept

    def test_bikini_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="formal"), "in a bikini by the pool")
        self.assertNotIn(_CLOTHING_LINE["formal"], prompt)

    def test_lingerie_request_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="casual"), "modelling black lingerie")
        self.assertNotIn(_CLOTHING_LINE["casual"], prompt)

    # ---------- Word boundaries through the prompt builder ----------

    def test_shirtless_suppresses_injection(self):
        # "shirtless" is nudity-ish (no garment), not a shirt: the preset
        # outfit must not fight it.
        prompt = _build_prompt(_PromptCharacter(clothing_style="streetwear"), "shirtless in the sun")
        self.assertNotIn(_CLOTHING_LINE["streetwear"], prompt)

    def test_tshirt_mention_suppresses_injection(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="elegant"), "wearing a white t-shirt")
        self.assertNotIn("elegant clothing", prompt)

    # ---------- Empty outfit → nothing injected ----------

    def test_blank_clothing_style_injects_nothing(self):
        # Neutral base prompt so any "wear"/"clothing" in the output can only
        # have come from the (suppressed) injection path.
        prompt = _build_prompt(
            _PromptCharacter(clothing_style=""),
            "at the beach",
            base_prompt="full-body photo, natural standing pose",
        )
        self.assertNotIn("clothing", prompt.lower())
        self.assertNotIn("wear", prompt.lower())

    def test_whitespace_instruction_is_treated_as_absent(self):
        prompt = _build_prompt(_PromptCharacter(clothing_style="casual"), "   ")
        self.assertIn(_CLOTHING_LINE["casual"], prompt)

    def test_body_type_line_survives_regardless(self):
        # Dropping the outfit must not drop the physique description.
        prompt = _build_prompt(_PromptCharacter(clothing_style="casual"), "completely naked")
        self.assertIn("athletic build", prompt)


class DefaultOutfitApiTests(TestCase):
    """API round-trip of the existing ``clothing_style`` field — the value
    the prompt builder reads is the value the wizard and edit form write."""

    CREATE_URL = "/api/v1/characters/create/"

    # The create endpoint validates the full persona payload before the
    # auto-generation step overwrites it, so tests must send complete fields.
    CREATE_PAYLOAD = {
        "short_description": "A calm, grounded companion",
        "mood": "cheerful",
        "category": "supportive",
        "intro": "Karibu! Great to meet you.",
        "personality_description": "Steady and warm, loves morning coffee.",
        "prompt_template": "You are {name}, a {gender} companion.",
        "greeting_message": "Habari! How is your day going?",
        "tone_style": "warm",
    }

    def setUp(self):
        self.owner = _make_user(username="outfitowner", email="outfitowner@example.com", verified=True)
        self.admin = _make_user(username="outfitadmin", email="outfitadmin@example.com", verified=True, is_staff=True)

    def _post_create(self, token, extra):
        with mock.patch(
            "capp.classes.characters.character_service.MistralClient.chat_completion",
            side_effect=RuntimeError("no LLM in tests"),
        ):
            return self.client.post(
                self.CREATE_URL,
                data={**self.CREATE_PAYLOAD, **extra},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_create_round_trips_clothing_style(self, _recaptcha):
        token = _bearer_token(self.client, "outfitadmin")
        resp = self._post_create(token, {"name": "Jabali", "clothing_style": "formal", "gender": "male"})

        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()["data"]["character"]
        self.assertEqual(data["clothing_style"], "formal")

        character = Character.objects.get(name="Jabali")
        self.assertEqual(character.clothing_style, "formal")

        # And the stored value is exactly what the prompt builder injects.
        prompt = _build_prompt(character, "at the park")
        self.assertIn(_CLOTHING_LINE["formal"], prompt)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_create_defaults_to_casual_when_omitted(self, _recaptcha):
        token = _bearer_token(self.client, "outfitadmin")
        resp = self._post_create(token, {"name": "Zawadi"})

        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()["data"]["character"]["clothing_style"], "casual")

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_update_round_trips_clothing_style(self, _recaptcha):
        character = _make_character(name="Bahati", slug="bahati", created_by=self.admin)
        # The shared fixture's "casual" tone is not in ALLOWED_TONE_STYLES, so
        # an update would fail validation before touching clothing_style.
        character.tone_style = "warm"
        character.clothing_style = "casual"
        character.save(update_fields=["tone_style", "clothing_style"])

        token = _bearer_token(self.client, "outfitadmin")
        resp = self.client.patch(
            f"/api/v1/characters/{character.character_id}/update/",
            data={"clothing_style": "gothic"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        character.refresh_from_db()
        self.assertEqual(character.clothing_style, "gothic")
        self.assertEqual(resp.json()["data"]["character"]["clothing_style"], "gothic")

        # The updated value flows into prompt assembly immediately.
        prompt = _build_prompt(character, "dancing at a concert")
        self.assertIn(_CLOTHING_LINE["gothic"], prompt)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_update_rejects_unknown_clothing_style(self, _recaptcha):
        character = _make_character(name="Kito", slug="kito", created_by=self.admin)
        character.tone_style = "warm"
        character.save(update_fields=["tone_style"])
        token = _bearer_token(self.client, "outfitadmin")
        resp = self.client.patch(
            f"/api/v1/characters/{character.character_id}/update/",
            data={"clothing_style": "chainmail-bikini-of-doom"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_clothing_style")

        character.refresh_from_db()
        self.assertEqual(character.clothing_style, "casual")
