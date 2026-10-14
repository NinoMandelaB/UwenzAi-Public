# capp/tests_character_approval.py
"""
Tests for the character approval flow and gallery-image publication.

Background
----------
A non-admin user who made a public character and saved it saw it stay a draft
forever, so admins never had anything to review. Two things were wrong:

* The edit page only asked for a review when the character was already approved
  (re-approval), never for a draft or a rejected character, and the backend
  charged the first submission of a draft as if it were a re-review. The
  privacy transitions (public <-> private) were also dead code: they compared
  the already-updated value with the payload, which is always equal.
* Gallery images generated through the API were always stored private, so even
  an approved public character showed an empty gallery to everyone else.

Images the owner generates for the character's own gallery (setup and edit
pages) are now stored private with ``publish_on_approval`` and published when an
admin approves the character. Images made in chat or on the detail page never
carry the flag and stay private.
"""

import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from capp.classes.characters.character_gallery_service import CharacterGalleryService
from capp.classes.characters.character_service import CharacterService
from capp.models import BillingReservation, Character, CharacterGalleryImage, SparkPool
from capp.tests import RECAPTCHA_OK, _bearer_token, _bypass_generated_image_screen, _make_character, _make_user

PUBLIC = CharacterGalleryImage.VisibilityChoices.PUBLIC
PRIVATE = CharacterGalleryImage.VisibilityChoices.PRIVATE


def _spark_balance(user):
    return SparkPool.objects.get(user=user).sparks


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class _ApprovalFixture(TestCase):
    """Shared users, a public character and request helpers."""

    def setUp(self):
        _bypass_generated_image_screen(self)
        cache.clear()
        self.admin = _make_user(username="apradmin", email="apradmin@example.com", verified=True, is_staff=True)
        self.owner = _make_user(username="aprowner", email="aprowner@example.com", verified=True)
        self.other = _make_user(username="aprother", email="aprother@example.com", verified=True)
        for user in (self.owner, self.other):
            SparkPool.objects.update_or_create(
                user=user,
                defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
            )
        self.character = self._character(status=Character.StatusChoices.DRAFT, is_public=True, with_avatar=True)

    # ── builders ────────────────────────────────────────────────────────────

    def _character(self, status, is_public=True, with_avatar=True, is_active=None, slug=None):
        character = _make_character(
            name="Neema",
            slug=slug or f"neema-{Character.objects.count()}",
            is_public=is_public,
            created_by=self.owner,
        )
        # The shared fixture's "casual" tone is not allowed on update.
        character.tone_style = "warm"
        character.status = status
        character.is_active = status == Character.StatusChoices.APPROVED if is_active is None else is_active
        character.save(update_fields=["tone_style", "status", "is_active"])
        if with_avatar:
            character.avatar.save("avatar.webp", ContentFile(b"avatar-bytes"), save=True)
        return character

    def _image(self, character=None, created_by=None, visibility=PRIVATE, publish_on_approval=False):
        image = CharacterGalleryImage(
            character=character or self.character,
            caption="x",
            created_by=created_by or self.owner,
            visibility=visibility,
            publish_on_approval=publish_on_approval,
        )
        image.image.save("g.webp", ContentFile(b"gallery-bytes"), save=False)
        image.save()
        return image

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _auth(self, username):
        return {"HTTP_AUTHORIZATION": f"Bearer {_bearer_token(self.client, username)}"}

    def _update(self, character, username, data):
        return self.client.patch(
            f"/api/v1/characters/{character.character_id}/update/",
            data=data,
            content_type="application/json",
            **self._auth(username),
        )

    def _approve(self, character, username="apradmin"):
        return self.client.post(
            f"/api/v1/characters/{character.character_id}/approve/",
            data={},
            content_type="application/json",
            **self._auth(username),
        )

    def _complete_setup(self, character, username):
        return self.client.post(
            f"/api/v1/characters/{character.character_id}/complete-setup/",
            data={},
            content_type="application/json",
            **self._auth(username),
        )

    def _gallery(self, character, username=None):
        headers = self._auth(username) if username else {}
        resp = self.client.get(f"/api/v1/characters/{character.character_id}/gallery/", **headers)
        self.assertEqual(resp.status_code, 200, resp.content)
        return {img["id"]: img for img in resp.json()["data"]["images"]}

    def _generate(self, character, username, **extra):
        body = {"prompt": "portrait, high quality, detailed", **extra}
        with mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"generated-image"):
            return self.client.post(
                f"/api/v1/characters/{character.character_id}/gallery/generate/",
                data=body,
                content_type="application/json",
                **self._auth(username),
            )


# ── Saving a public character moves it into the review queue ────────────────


@mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
class SubmitForReviewTests(_ApprovalFixture):
    def test_saving_public_draft_with_submit_flag_enters_review_for_free(self, _r):
        before = _spark_balance(self.owner)

        resp = self._update(self.character, "aprowner", {"submit_for_review": True})

        self.assertEqual(resp.status_code, 200, resp.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.PENDING)
        self.assertFalse(self.character.is_active)
        self.assertEqual(resp.json()["data"]["character"]["review_status"], "pending")
        # Creating the character was already paid for; submitting it is free.
        self.assertEqual(_spark_balance(self.owner), before)
        self.assertFalse(BillingReservation.objects.filter(user=self.owner).exists())

    def test_submit_flag_is_listed_for_admins_in_the_pending_queue(self, _r):
        self._update(self.character, "aprowner", {"submit_for_review": True})

        resp = self.client.get("/api/v1/characters/pending/", **self._auth("apradmin"))

        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {c["character_id"] for c in resp.json()["data"]["characters"]}
        self.assertIn(str(self.character.character_id), ids)

    def test_saving_without_the_flag_keeps_the_draft(self, _r):
        resp = self._update(self.character, "aprowner", {"short_description": "Updated text"})

        self.assertEqual(resp.status_code, 200, resp.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.DRAFT)

    def test_string_false_does_not_submit(self, _r):
        resp = self._update(self.character, "aprowner", {"submit_for_review": "false"})

        self.assertEqual(resp.status_code, 200, resp.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.DRAFT)

    def test_draft_without_avatar_cannot_be_submitted(self, _r):
        draft = self._character(status=Character.StatusChoices.DRAFT, with_avatar=False)

        resp = self._update(draft, "aprowner", {"submit_for_review": True, "short_description": "changed"})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "avatar_required")
        draft.refresh_from_db()
        self.assertEqual(draft.status, Character.StatusChoices.DRAFT)
        # Refused before anything was written.
        self.assertEqual(draft.short_description, "desc")

    def test_private_draft_is_not_sent_to_review(self, _r):
        draft = self._character(status=Character.StatusChoices.DRAFT, is_public=False)

        resp = self._update(draft, "aprowner", {"submit_for_review": True, "is_public": False})

        self.assertEqual(resp.status_code, 200, resp.content)
        draft.refresh_from_db()
        self.assertNotEqual(draft.status, Character.StatusChoices.PENDING)

    def test_rejected_character_can_be_resubmitted_and_pays_the_review_fee(self, _r):
        rejected = self._character(status=Character.StatusChoices.REJECTED)
        rejected.rejection_reason = "blurry"
        rejected.save(update_fields=["rejection_reason"])
        before = _spark_balance(self.owner)

        resp = self._update(rejected, "aprowner", {"submit_for_review": True})

        self.assertEqual(resp.status_code, 200, resp.content)
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Character.StatusChoices.PENDING)
        self.assertIsNone(rejected.rejection_reason)
        self.assertLess(_spark_balance(self.owner), before)

    def test_rejected_resubmission_needs_sparks(self, _r):
        rejected = self._character(status=Character.StatusChoices.REJECTED)
        SparkPool.objects.filter(user=self.owner).update(sparks=0)

        resp = self._update(rejected, "aprowner", {"submit_for_review": True})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "insufficient_sparks")
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Character.StatusChoices.REJECTED)

    def test_editing_an_approved_public_character_still_requires_re_review(self, _r):
        approved = self._character(status=Character.StatusChoices.APPROVED)

        resp = self._update(approved, "aprowner", {"short_description": "New tagline"})

        self.assertEqual(resp.status_code, 200, resp.content)
        approved.refresh_from_db()
        self.assertEqual(approved.status, Character.StatusChoices.PENDING)

    def test_pending_character_stays_pending_and_is_not_billed_again(self, _r):
        pending = self._character(status=Character.StatusChoices.PENDING)
        before = _spark_balance(self.owner)

        resp = self._update(pending, "aprowner", {"short_description": "Tweaked while waiting"})

        self.assertEqual(resp.status_code, 200, resp.content)
        pending.refresh_from_db()
        self.assertEqual(pending.status, Character.StatusChoices.PENDING)
        self.assertEqual(_spark_balance(self.owner), before)

    def test_stranger_cannot_submit_someone_elses_draft(self, _r):
        resp = self._update(self.character, "aprother", {"submit_for_review": True})

        self.assertEqual(resp.status_code, 403)
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.DRAFT)

    def test_complete_setup_still_submits_a_draft(self, _r):
        resp = self._complete_setup(self.character, "aprowner")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.PENDING)


# ── Visibility changes made by the owner ────────────────────────────────────


@mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
class VisibilityTransitionTests(_ApprovalFixture):
    def test_public_draft_made_private_is_approved_instead_of_stuck(self, _r):
        before = _spark_balance(self.owner)

        resp = self._update(self.character, "aprowner", {"is_public": False})

        self.assertEqual(resp.status_code, 200, resp.content)
        self.character.refresh_from_db()
        self.assertFalse(self.character.is_public)
        self.assertEqual(self.character.status, Character.StatusChoices.APPROVED)
        self.assertTrue(self.character.is_active)
        self.assertEqual(_spark_balance(self.owner), before)

    def test_pending_character_made_private_is_approved(self, _r):
        pending = self._character(status=Character.StatusChoices.PENDING)

        resp = self._update(pending, "aprowner", {"is_public": False})

        self.assertEqual(resp.status_code, 200, resp.content)
        pending.refresh_from_db()
        self.assertEqual(pending.status, Character.StatusChoices.APPROVED)
        self.assertTrue(pending.is_active)

    def test_approved_character_made_private_keeps_its_state(self, _r):
        approved = self._character(status=Character.StatusChoices.APPROVED, is_active=False)

        resp = self._update(approved, "aprowner", {"is_public": False})

        self.assertEqual(resp.status_code, 200, resp.content)
        approved.refresh_from_db()
        self.assertEqual(approved.status, Character.StatusChoices.APPROVED)
        # An admin's deactivation is not undone by flipping the switch.
        self.assertFalse(approved.is_active)

    def test_rejected_character_made_private_stays_rejected(self, _r):
        rejected = self._character(status=Character.StatusChoices.REJECTED)

        resp = self._update(rejected, "aprowner", {"is_public": False})

        self.assertEqual(resp.status_code, 200, resp.content)
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Character.StatusChoices.REJECTED)

    def test_private_character_made_public_goes_to_review_and_is_billed(self, _r):
        private = self._character(status=Character.StatusChoices.APPROVED, is_public=False)
        before = _spark_balance(self.owner)

        resp = self._update(private, "aprowner", {"is_public": True})

        self.assertEqual(resp.status_code, 200, resp.content)
        private.refresh_from_db()
        self.assertTrue(private.is_public)
        self.assertEqual(private.status, Character.StatusChoices.PENDING)
        self.assertFalse(private.is_active)
        self.assertLess(_spark_balance(self.owner), before)

    def test_going_private_withdraws_pending_gallery_publication(self, _r):
        waiting = self._image(publish_on_approval=True)

        self._update(self.character, "aprowner", {"is_public": False})

        waiting.refresh_from_db()
        self.assertFalse(waiting.publish_on_approval)
        self.assertEqual(waiting.visibility, PRIVATE)


# ── Gallery images: who sees what, and when ─────────────────────────────────


@mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
class GalleryGenerationVisibilityTests(_ApprovalFixture):
    def _only_image(self):
        return CharacterGalleryImage.objects.get()

    def test_owner_setup_image_waits_for_approval(self, _r):
        resp = self._generate(self.character, "aprowner", publish_on_approval=True)

        self.assertEqual(resp.status_code, 200, resp.content)
        image = self._only_image()
        self.assertEqual(image.visibility, PRIVATE)
        self.assertTrue(image.publish_on_approval)
        self.assertEqual(resp.json()["data"]["image"]["publish_on_approval"], True)

    def test_image_without_the_flag_is_plain_private(self, _r):
        """Chat and detail-page callers never send it."""
        resp = self._generate(self.character, "aprowner")

        self.assertEqual(resp.status_code, 200, resp.content)
        image = self._only_image()
        self.assertEqual(image.visibility, PRIVATE)
        self.assertFalse(image.publish_on_approval)

    def test_string_false_flag_is_not_a_request_to_publish(self, _r):
        resp = self._generate(self.character, "aprowner", publish_on_approval="false")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(self._only_image().publish_on_approval)

    def test_other_users_flag_is_ignored(self, _r):
        approved = self._character(status=Character.StatusChoices.APPROVED)

        resp = self._generate(approved, "aprother", publish_on_approval=True)

        self.assertEqual(resp.status_code, 200, resp.content)
        image = CharacterGalleryImage.objects.get(character=approved)
        self.assertEqual(image.visibility, PRIVATE)
        self.assertFalse(image.publish_on_approval)

    def test_private_character_images_do_not_queue_for_publication(self, _r):
        private = self._character(status=Character.StatusChoices.APPROVED, is_public=False)

        resp = self._generate(private, "aprowner", publish_on_approval=True)

        self.assertEqual(resp.status_code, 200, resp.content)
        image = CharacterGalleryImage.objects.get(character=private)
        self.assertEqual(image.visibility, PRIVATE)
        self.assertFalse(image.publish_on_approval)

    def test_admin_image_for_public_character_is_public_immediately(self, _r):
        resp = self._generate(self.character, "apradmin", publish_on_approval=True)

        self.assertEqual(resp.status_code, 200, resp.content)
        image = self._only_image()
        self.assertEqual(image.visibility, PUBLIC)
        self.assertFalse(image.publish_on_approval)

    def test_waiting_image_is_visible_to_owner_and_admin_but_nobody_else(self, _r):
        self._generate(self.character, "aprowner", publish_on_approval=True)
        image_id = str(self._only_image().id)

        self.assertIn(image_id, self._gallery(self.character, "aprowner"))
        self.assertIn(image_id, self._gallery(self.character, "apradmin"))
        self.assertNotIn(image_id, self._gallery(self.character, "aprother"))
        self.assertNotIn(image_id, self._gallery(self.character))

    def test_admin_does_not_see_unflagged_private_images(self, _r):
        """The moderator exception covers images submitted for release only."""
        chat_image = self._image(visibility=PRIVATE, publish_on_approval=False)
        waiting = self._image(visibility=PRIVATE, publish_on_approval=True)

        listing = self._gallery(self.character, "apradmin")

        self.assertIn(str(waiting.id), listing)
        self.assertNotIn(str(chat_image.id), listing)


@mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
class PublishOnApprovalTests(_ApprovalFixture):
    def setUp(self):
        super().setUp()
        self.character.status = Character.StatusChoices.PENDING
        self.character.save(update_fields=["status"])
        self.edit_image = self._image(publish_on_approval=True)
        self.second_edit_image = self._image(publish_on_approval=True)
        self.chat_image = self._image(publish_on_approval=False)

    def _assert_published_only_edit_images(self):
        for image in (self.edit_image, self.second_edit_image, self.chat_image):
            image.refresh_from_db()
        self.assertEqual(self.edit_image.visibility, PUBLIC)
        self.assertEqual(self.second_edit_image.visibility, PUBLIC)
        self.assertFalse(self.edit_image.publish_on_approval)
        # Chat / detail-page pictures stay private to the person who made them.
        self.assertEqual(self.chat_image.visibility, PRIVATE)

    def test_approving_publishes_edit_images_but_not_chat_images(self, _r):
        resp = self._approve(self.character)

        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_published_only_edit_images()

    def test_everyone_sees_the_published_images_after_approval(self, _r):
        before = self._gallery(self.character)
        self.assertEqual(before, {})

        self._approve(self.character)

        for who in (None, "aprother"):
            listing = self._gallery(self.character, who)
            self.assertEqual({str(self.edit_image.id), str(self.second_edit_image.id)}, set(listing))
            self.assertNotIn(str(self.chat_image.id), listing)

    def test_admin_completing_setup_publishes(self, _r):
        draft = self._character(status=Character.StatusChoices.DRAFT, with_avatar=True)
        waiting = self._image(character=draft, publish_on_approval=True)
        # The creator is the owner, but an admin finishes the setup.
        resp = self._complete_setup(draft, "apradmin")

        self.assertEqual(resp.status_code, 200, resp.content)
        waiting.refresh_from_db()
        self.assertEqual(waiting.visibility, PUBLIC)

    def test_admin_setting_status_approved_in_the_edit_form_publishes(self, _r):
        resp = self._update(self.character, "apradmin", {"status": "approved"})

        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_published_only_edit_images()

    def test_admin_saving_an_already_approved_character_is_not_an_approval(self, _r):
        self._approve(self.character)
        late = self._image(publish_on_approval=True)

        resp = self._update(self.character, "apradmin", {"short_description": "Admin tweak", "status": "approved"})

        self.assertEqual(resp.status_code, 200, resp.content)
        late.refresh_from_db()
        self.assertEqual(late.visibility, PRIVATE)
        self.assertTrue(late.publish_on_approval)

    def test_rejection_publishes_nothing_and_a_later_approval_does(self, _r):
        resp = self.client.post(
            f"/api/v1/characters/{self.character.character_id}/reject/",
            data={"rejection_reason": "needs a clearer photo"},
            content_type="application/json",
            **self._auth("apradmin"),
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.edit_image.refresh_from_db()
        self.assertEqual(self.edit_image.visibility, PRIVATE)
        self.assertTrue(self.edit_image.publish_on_approval)

        self._approve(self.character)

        self._assert_published_only_edit_images()

    def test_owner_resubmission_cycle_publishes_new_images_on_next_approval(self, _r):
        self._approve(self.character)
        waiting = self._image(publish_on_approval=True)
        self._update(self.character, "aprowner", {"short_description": "Edited after approval"})
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.PENDING)
        waiting.refresh_from_db()
        self.assertEqual(waiting.visibility, PRIVATE)

        self._approve(self.character)

        waiting.refresh_from_db()
        self.assertEqual(waiting.visibility, PUBLIC)
        self.assertFalse(waiting.publish_on_approval)

    def test_private_character_never_publishes(self, _r):
        private = self._character(status=Character.StatusChoices.PENDING, is_public=False)
        waiting = self._image(character=private, publish_on_approval=True)

        self._approve(private)

        waiting.refresh_from_db()
        self.assertEqual(waiting.visibility, PRIVATE)

    def test_image_from_an_unrelated_account_is_never_published(self, _r):
        """Defence in depth: a flag on somebody else's image is not honoured."""
        stranger = self._image(created_by=self.other, publish_on_approval=True)

        self._approve(self.character)

        stranger.refresh_from_db()
        self.assertEqual(stranger.visibility, PRIVATE)

    def test_publishing_is_a_no_op_before_approval(self, _r):
        published = CharacterGalleryService.publish_images_awaiting_approval(self.character)

        self.assertEqual(published, 0)
        self.edit_image.refresh_from_db()
        self.assertEqual(self.edit_image.visibility, PRIVATE)

    def test_a_publishing_failure_does_not_undo_the_approval(self, _r):
        with mock.patch.object(
            CharacterGalleryService, "publish_images_awaiting_approval", side_effect=RuntimeError("storage down")
        ):
            resp = self._approve(self.character)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.character.refresh_from_db()
        self.assertEqual(self.character.status, Character.StatusChoices.APPROVED)
        self.edit_image.refresh_from_db()
        self.assertEqual(self.edit_image.visibility, PRIVATE)

        # Approving again republishes what was left behind.
        self._approve(self.character)
        self._assert_published_only_edit_images()

    def test_publishing_twice_is_harmless(self, _r):
        self._approve(self.character)

        again = CharacterGalleryService.publish_images_awaiting_approval(self.character)

        self.assertEqual(again, 0)
        self._assert_published_only_edit_images()
