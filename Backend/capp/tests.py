# capp/tests.py
import io
import json
import pathlib
import re
import tempfile
import uuid
from datetime import timedelta
from unittest import mock

from PIL import Image

from packaging.utils import canonicalize_name
from packaging.version import Version
from celery.exceptions import SoftTimeLimitExceeded

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from django.utils import timezone

from capp.classes.billing.paid_features import InsufficientBalance, PaidFeature
from capp.classes.characters.character_service import CharacterService
from capp.classes.moderation.image_prompt_policy import (
    PROHIBITED_SUBJECT_ERROR_CODE,
    ImagePromptPolicy,
    ProhibitedImageSubjectError,
)
from capp.constants import IMAGE_GENERATION_COST, MESSAGE_PAIR_COST, SIGNUP_SPARKS
from capp.models import (
    BillingReservation,
    Character,
    CharacterFlag,
    CharacterGalleryImage,
    Conversation,
    CustomUser,
    GuestIdentity,
    GuestTokenQuota,
    Message,
    ProactiveChatPreference,
    SparkPool,
    SparkTransaction,
    UsageCharge,
    UserBYOKConfig,
)
from capp.classes.chat.proactive_chat_service import PROACTIVE_NAMESPACE, ProactiveChatService
from capp.tasks import check_and_send_proactive_messages, generate_chat_image
from capp.utils.encryption import EncryptionService

RECAPTCHA_OK = {"success": True}

# Sentinel so Seedream helper tests can request the default success payload
# (a valid images array) versus an explicit malformed one.
_UNSET = object()


def _make_user(username="alice", email="alice@example.com", verified=True, is_staff=False):
    fields = EncryptionService.prepare_email_fields(email)
    return CustomUser.objects.create_user(
        username=username,
        password="pw123456",
        email_encrypted=fields["email_encrypted"],
        email_hash=fields["email_hash"],
        is_verified=verified,
        is_staff=is_staff,
        account_status=(CustomUser.AccountStatus.ACTIVE if verified else CustomUser.AccountStatus.PENDING),
        # Test users get every optional feature enabled by default so existing
        # suites keep exercising the pre-feature-flag behaviour. Tests that
        # care about the disabled state override this explicitly
        # (see tests_feature_preferences.py).
        feature_preferences={
            "reminders": True,
            "deepthink": True,
            "websearch": True,
            "memory_editor": True,
        },
    )


class AuthApiTests(TestCase):
    """End-to-end auth flow through the DRF views (external calls mocked)."""

    def setUp(self):
        # Throttles use the shared process cache; reset so tests are independent.
        cache.clear()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_signup_creates_user_with_signup_bonus(self, _recaptcha):
        payload = {
            "username": "newbie",
            "email": "newbie@example.com",
            "password": "pw123456",
            "confirm_password": "pw123456",
            "full_name": "New Bie",
            "age_confirmed": True,
            "terms_accepted": True,
            "privacy_accepted": True,
        }
        resp = self.client.post("/api/v1/auth/signup/", data=payload, content_type="application/json")
        self.assertIn(resp.status_code, (200, 201))
        self.assertEqual(resp.json()["status"], "success")

        user = CustomUser.objects.get(username="newbie")
        pool = SparkPool.objects.get(user=user)
        self.assertEqual(pool.sparks, SIGNUP_SPARKS)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_signup_rejects_password_mismatch(self, _recaptcha):
        payload = {
            "username": "mismatch",
            "email": "mismatch@example.com",
            "password": "pw123456",
            "confirm_password": "different",
            "age_confirmed": True,
            "terms_accepted": True,
            "privacy_accepted": True,
        }
        resp = self.client.post("/api/v1/auth/signup/", data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "password_mismatch")
        self.assertFalse(CustomUser.objects.filter(username="mismatch").exists())

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_signin_success_returns_access_token_and_refresh_cookie(self, _recaptcha):
        _make_user(username="signer", email="signer@example.com", verified=True)
        resp = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "signer", "password": "pw123456"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("access_token", body["data"])
        self.assertIn("refresh_token", resp.cookies)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_signin_wrong_password_rejected(self, _recaptcha):
        _make_user(username="wrongpw", email="wrongpw@example.com", verified=True)
        resp = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "wrongpw", "password": "not-the-password"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_credentials")

    def test_signin_staff_account_requires_recaptcha(self):
        # Staff/superuser accounts are NOT exempt from reCAPTCHA — the admin
        # panel (uwenzAi-Admin) sends a reCAPTCHA token on login just like
        # the main app. Without a token, signin is rejected.
        _make_user(username="staffer", email="staffer@example.com", verified=True, is_staff=True)
        resp = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "staffer", "password": "pw123456"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "recaptcha_required")

    def test_signin_regular_account_requires_recaptcha(self):
        # Confirms reCAPTCHA is enforced for all accounts (staff and non-staff
        # alike) when no valid recaptcha_token is provided.
        _make_user(username="regular", email="regular@example.com", verified=True, is_staff=False)
        resp = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "regular", "password": "pw123456"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "recaptcha_required")

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_refresh_rotates_token_with_valid_cookie(self, _recaptcha):
        _make_user(username="refresher", email="refresher@example.com", verified=True)
        signin = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "refresher", "password": "pw123456"},
            content_type="application/json",
        )
        self.assertEqual(signin.status_code, 200)
        # The refresh-token cookie is carried by self.client automatically.
        resp = self.client.post("/api/v1/auth/refresh/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access_token", resp.json()["data"])

    def test_refresh_without_cookie_rejected(self):
        resp = self.client.post("/api/v1/auth/refresh/")
        self.assertEqual(resp.status_code, 401)

    def test_protected_endpoint_requires_auth(self):
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 401)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_protected_endpoint_accepts_bearer_token(self, _recaptcha):
        _make_user(username="meuser", email="meuser@example.com", verified=True)
        signin = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "meuser", "password": "pw123456"},
            content_type="application/json",
        )
        token = signin.json()["data"]["access_token"]
        resp = self.client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["user"]["username"], "meuser")


def _make_character(
    name="Amina", slug="amina", status=CustomUser.AccountStatus.ACTIVE, is_public=True, created_by=None
):
    return Character.objects.create(
        name=name,
        slug=slug,
        short_description="desc",
        mood="cheerful",
        intro="intro",
        personality_description="friendly and warm",
        greeting_message="Hi there!",
        tone_style="casual",
        status=Character.StatusChoices.APPROVED,
        is_active=True,
        is_public=is_public,
        created_by=created_by,
    )


def _bearer_token(client, username, password="pw123456"):
    resp = client.post(
        "/api/v1/auth/signin/",
        data={"username": username, "password": password},
        content_type="application/json",
    )
    try:
        body = resp.json()
    except ValueError:
        body = resp.content.decode("utf-8", errors="replace")

    if resp.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise AssertionError(f"Sign-in for {username!r} failed: status={resp.status_code}, body={body!r}")

    token = body["data"].get("access_token")
    if not token:
        raise AssertionError(
            f"Sign-in for {username!r} returned no access token: status={resp.status_code}, body={body!r}"
        )

    return token


class AdminApiTests(TestCase):
    """Admin-panel API surface consumed by the separate uwenzAi-Admin repo."""

    def setUp(self):
        cache.clear()
        self.admin = _make_user(username="admin1", email="admin1@example.com", verified=True, is_staff=True)
        self.plain_user = _make_user(username="regular1", email="regular1@example.com", verified=True)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_signin_updates_last_login(self, _recaptcha):
        self.assertIsNone(self.plain_user.last_login)
        _bearer_token(self.client, "regular1")
        self.plain_user.refresh_from_db()
        self.assertIsNotNone(self.plain_user.last_login)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_is_rejected(self, _recaptcha):
        token = _bearer_token(self.client, "regular1")
        resp = self.client.get("/api/v1/admin/users/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 403)

    def test_admin_endpoint_requires_auth(self):
        resp = self.client.get("/api/v1/admin/users/")
        self.assertEqual(resp.status_code, 401)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_list_users(self, _recaptcha):
        token = _bearer_token(self.client, "admin1")
        resp = self.client.get("/api/v1/admin/users/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 200)
        usernames = [u["username"] for u in resp.json()["data"]["users"]]
        self.assertIn("admin1", usernames)
        self.assertIn("regular1", usernames)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_deactivate_requires_reason(self, _recaptcha):
        token = _bearer_token(self.client, "admin1")
        resp = self.client.post(
            f"/api/v1/admin/users/{self.plain_user.user_id}/status/",
            data={"account_status": "deactivated"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "reason_required")

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_deactivate_with_reason_locks_user_out(self, _recaptcha):
        token = _bearer_token(self.client, "admin1")
        resp = self.client.post(
            f"/api/v1/admin/users/{self.plain_user.user_id}/status/",
            data={"account_status": "deactivated", "reason": "Suspicious activity reported by users."},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        self.plain_user.refresh_from_db()
        self.assertEqual(self.plain_user.account_status, CustomUser.AccountStatus.DEACTIVATED)
        self.assertFalse(self.plain_user.is_active)
        self.assertEqual(self.plain_user.ban_reason, "Suspicious activity reported by users.")

        # The now-deactivated user must no longer be able to sign in.
        signin = self.client.post(
            "/api/v1/auth/signin/",
            data={"username": "regular1", "password": "pw123456"},
            content_type="application/json",
        )
        self.assertEqual(signin.status_code, 400)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_view_last_chatted_character_and_byok(self, _recaptcha):
        character = _make_character()
        Conversation.objects.create(
            user=self.plain_user,
            character=character,
            last_message_at=timezone.now(),
        )
        UserBYOKConfig.objects.create(
            user=self.plain_user,
            api_url="https://api.example.com/v1",
            api_key_encrypted="irrelevant-for-this-test",
            model_name="some-model",
            system_prompt="Be concise.",
        )

        token = _bearer_token(self.client, "admin1")
        resp = self.client.get(
            f"/api/v1/admin/users/{self.plain_user.user_id}/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["last_character"]["name"], "Amina")
        self.assertEqual(data["byok"]["model_name"], "some-model")
        self.assertEqual(data["byok"]["system_prompt"], "Be concise.")

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_list_user_transactions(self, _recaptcha):
        pool = SparkPool.objects.create(user=self.plain_user, sparks=100, token_balance=0, is_active=True)
        SparkTransaction.objects.create(
            spark_pool=pool,
            user=self.plain_user,
            entry_type=SparkTransaction.EntryType.DEBIT,
            source=SparkTransaction.SourceChoices.USAGE,
            spark_amount=-10,
            sparks_before=100,
            sparks_after=90,
            description="message_pair",
        )

        token = _bearer_token(self.client, "admin1")
        resp = self.client.get(
            f"/api/v1/admin/users/{self.plain_user.user_id}/transactions/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        transactions = resp.json()["data"]["transactions"]
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["spark_amount"], -10)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_character_stats_counts_messages(self, _recaptcha):
        character = _make_character(name="Kofi", slug="kofi")
        conversation = Conversation.objects.create(user=self.plain_user, character=character)
        conversation.messages.create(sender_type="user", content="hello")
        conversation.messages.create(sender_type="ai", content="hi there")

        token = _bearer_token(self.client, "admin1")
        resp = self.client.get("/api/v1/admin/characters/stats/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 200)
        stats = {c["name"]: c for c in resp.json()["data"]["characters"]}
        self.assertEqual(stats["Kofi"]["message_count"], 2)
        self.assertEqual(stats["Kofi"]["conversation_count"], 1)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_list_all_characters_with_visibility_flag(self, _recaptcha):
        _make_character(name="Amina", slug="amina", is_public=True, created_by=self.plain_user)
        _make_character(name="Kofi", slug="kofi", is_public=False, created_by=self.plain_user)

        token = _bearer_token(self.client, "admin1")
        resp = self.client.get("/api/v1/admin/characters/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 200)
        characters = {c["name"]: c for c in resp.json()["data"]["characters"]}
        self.assertTrue(characters["Amina"]["is_public"])
        self.assertFalse(characters["Kofi"]["is_public"])
        self.assertEqual(resp.json()["data"]["total"], 2)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_filter_characters_by_visibility(self, _recaptcha):
        _make_character(name="Amina", slug="amina", is_public=True, created_by=self.plain_user)
        _make_character(name="Kofi", slug="kofi", is_public=False, created_by=self.plain_user)

        token = _bearer_token(self.client, "admin1")
        resp = self.client.get(
            "/api/v1/admin/characters/?visibility=private",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        names = [c["name"] for c in resp.json()["data"]["characters"]]
        self.assertEqual(names, ["Kofi"])

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_get_character_detail_with_flags(self, _recaptcha):
        character = _make_character(name="Amina", slug="amina", created_by=self.plain_user)
        CharacterFlag.objects.create(character=character, reported_by=self.plain_user, reason="Inappropriate content")

        token = _bearer_token(self.client, "admin1")
        resp = self.client.get(
            f"/api/v1/admin/characters/{character.character_id}/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]["character"]
        self.assertEqual(data["name"], "Amina")
        self.assertEqual(data["flag_count"], 1)
        self.assertEqual(len(data["recent_flags"]), 1)
        self.assertEqual(data["recent_flags"][0]["reason"], "Inappropriate content")

    def test_admin_character_endpoints_require_auth(self):
        resp = self.client.get("/api/v1/admin/characters/")
        self.assertEqual(resp.status_code, 401)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_cannot_list_admin_characters(self, _recaptcha):
        token = _bearer_token(self.client, "regular1")
        resp = self.client.get("/api/v1/admin/characters/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 403)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_archive_character_via_existing_delete_endpoint(self, _recaptcha):
        """
        Archiving from the admin panel reuses the existing soft-delete
        endpoint (capp/urls/character_urls.py) - it already accepts staff/
        superuser actors (see CharacterService._is_admin_user), so no new
        backend endpoint is needed for this action, just the admin-panel
        wiring. This test guards that admin access keeps working.
        """
        character = _make_character(name="Amina", slug="amina", created_by=self.plain_user)
        token = _bearer_token(self.client, "admin1")
        resp = self.client.post(
            f"/api/v1/characters/{character.character_id}/delete/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        character.refresh_from_db()
        self.assertEqual(character.status, Character.StatusChoices.ARCHIVED)
        self.assertFalse(character.is_active)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_hard_delete_character_without_history(self, _recaptcha):
        """
        Hard-delete is only allowed for compliance reasons when a
        character has zero related data (never chatted with, no gallery
        images, preferences, relationship state, or flags).
        """
        character = _make_character(name="Amina", slug="amina", created_by=self.plain_user)
        token = _bearer_token(self.client, "admin1")
        resp = self.client.post(
            f"/api/v1/admin/characters/{character.character_id}/hard-delete/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["deleted"], True)
        self.assertFalse(Character.objects.filter(character_id=character.character_id).exists())

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_cannot_hard_delete_character_with_conversation_history(self, _recaptcha):
        character = _make_character(name="Kofi", slug="kofi", created_by=self.plain_user)
        Conversation.objects.create(user=self.plain_user, character=character)

        token = _bearer_token(self.client, "admin1")
        resp = self.client.post(
            f"/api/v1/admin/characters/{character.character_id}/hard-delete/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("conversations", resp.json()["data"]["blocking_relations"])
        self.assertTrue(Character.objects.filter(character_id=character.character_id).exists())

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_cannot_hard_delete_character_with_flags(self, _recaptcha):
        character = _make_character(name="Zawadi", slug="zawadi", created_by=self.plain_user)
        CharacterFlag.objects.create(character=character, reported_by=self.plain_user, reason="Reported")

        token = _bearer_token(self.client, "admin1")
        resp = self.client.post(
            f"/api/v1/admin/characters/{character.character_id}/hard-delete/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("character_flags", resp.json()["data"]["blocking_relations"])
        self.assertTrue(Character.objects.filter(character_id=character.character_id).exists())

    def test_admin_hard_delete_requires_auth(self):
        character = _make_character(name="Amina", slug="amina2", created_by=self.plain_user)
        resp = self.client.post(f"/api/v1/admin/characters/{character.character_id}/hard-delete/")
        self.assertEqual(resp.status_code, 401)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_cannot_hard_delete_character(self, _recaptcha):
        character = _make_character(name="Amina", slug="amina3", created_by=self.plain_user)
        token = _bearer_token(self.client, "regular1")
        resp = self.client.post(
            f"/api/v1/admin/characters/{character.character_id}/hard-delete/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Character.objects.filter(character_id=character.character_id).exists())


class SparkDebitTests(TestCase):
    """The debit half of a reservation: what leaves the balance, and when."""

    def setUp(self):
        self.user = _make_user(username="payer", email="payer@example.com", verified=True)

    def _pool(self, sparks):
        pool, _ = SparkPool.objects.get_or_create(
            user=self.user,
            defaults={"sparks": 0, "token_balance": 0, "is_active": True},
        )
        pool.sparks = sparks
        pool.save(update_fields=["sparks"])
        return pool

    def test_reserve_deducts_sparks_atomically(self):
        self._pool(2000)
        reservation = PaidFeature.reserve(
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            operation_id=str(uuid.uuid4()),
            user=self.user,
        )
        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)

        pool = SparkPool.objects.get(user=self.user)
        self.assertEqual(pool.sparks, 2000 - MESSAGE_PAIR_COST)
        # A debit transaction and a usage-charge record are written together.
        txn = SparkTransaction.objects.get(user=self.user, entry_type=SparkTransaction.EntryType.DEBIT)
        self.assertEqual(txn.spark_amount, -MESSAGE_PAIR_COST)
        self.assertEqual(txn.sparks_after, 2000 - MESSAGE_PAIR_COST)
        self.assertTrue(UsageCharge.objects.filter(user=self.user, sparks_charged=MESSAGE_PAIR_COST).exists())

    def test_settle_does_not_charge_a_second_time(self):
        self._pool(2000)
        reservation = PaidFeature.reserve(
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            operation_id=str(uuid.uuid4()),
            user=self.user,
        )
        PaidFeature.settle(reservation, reason="test")

        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000 - MESSAGE_PAIR_COST)
        self.assertEqual(SparkTransaction.objects.filter(user=self.user).count(), 1)

    def test_insufficient_balance_does_not_deduct(self):
        self._pool(MESSAGE_PAIR_COST - 1)
        with self.assertRaises(InsufficientBalance):
            PaidFeature.reserve(
                feature=BillingReservation.Feature.MESSAGE_PAIR,
                operation_id=str(uuid.uuid4()),
                user=self.user,
            )

        pool = SparkPool.objects.get(user=self.user)
        self.assertEqual(pool.sparks, MESSAGE_PAIR_COST - 1)
        self.assertFalse(
            SparkTransaction.objects.filter(user=self.user, entry_type=SparkTransaction.EntryType.DEBIT).exists()
        )
        self.assertFalse(BillingReservation.objects.filter(user=self.user).exists())

    def test_guest_reservation_deducts_from_quota(self):
        guest = GuestIdentity.objects.create(expires_at=timezone.now() + timedelta(days=1))
        quota = GuestTokenQuota.get_or_create_quota(guest.owner_key)
        quota.sparks = 500
        quota.save(update_fields=["sparks"])

        PaidFeature.reserve(
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            operation_id=str(uuid.uuid4()),
            guest_identity=guest,
        )
        self.assertEqual(GuestTokenQuota.objects.get(session_id=guest.owner_key).sparks, 500 - MESSAGE_PAIR_COST)

    def test_refund_after_provider_failure_restores_the_balance(self):
        """
        The pattern every paid call site now follows: hold the Sparks, call the
        provider, and give them back when it fails. The user ends where they
        started, with both legs of the movement on the ledger.
        """
        self._pool(2000)
        from capp.classes.ai.cortecs_client import CortecsClient

        reservation = PaidFeature.reserve(
            feature=BillingReservation.Feature.MESSAGE_PAIR,
            operation_id=str(uuid.uuid4()),
            user=self.user,
        )
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000 - MESSAGE_PAIR_COST)

        with mock.patch.object(CortecsClient, "chat_completion", side_effect=RuntimeError("provider down")):
            try:
                CortecsClient.chat_completion(messages=[{"role": "user", "content": "hi"}])
            except RuntimeError:
                PaidFeature.refund(reservation, reason="provider_failed")

        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, BillingReservation.Status.REFUNDED)


class CortecsClientRetryTests(TestCase):
    """FIX 4: bounded retry/backoff on transient errors, Mistral fallback."""

    def test_falls_back_to_mistral_without_api_key(self):
        from capp.classes.ai import cortecs_client
        from capp.classes.ai.cortecs_client import CortecsClient

        sentinel = {"id": "mistral-1", "choices": [{"message": {"content": "hi"}}], "usage": {}}
        with mock.patch.object(cortecs_client.settings, "CORTECS_API_KEY", None), mock.patch.object(
            cortecs_client.MistralClient, "chat_completion", return_value=sentinel
        ) as mistral:
            out = CortecsClient.chat_completion(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(out, sentinel)
        mistral.assert_called_once()

    def test_retries_transient_error_then_succeeds(self):
        import openai
        from capp.classes.ai.cortecs_client import CortecsClient

        ok = mock.Mock()
        ok.id = "cortecs-1"
        ok.choices = [mock.Mock(message=mock.Mock(content="hi"))]
        ok.usage = mock.Mock(prompt_tokens=1, completion_tokens=2, total_tokens=3)

        fake_client = mock.Mock()
        transient = openai.APITimeoutError(request=mock.Mock())
        fake_client.chat.completions.create.side_effect = [transient, ok]

        with mock.patch("capp.classes.ai.cortecs_client.time.sleep"):
            result = CortecsClient._create_with_retry(fake_client, model="m", messages=[])

        self.assertIs(result, ok)
        self.assertEqual(fake_client.chat.completions.create.call_count, 2)

    def test_transient_error_falls_back_to_mistral_after_retries(self):
        import openai
        from capp.classes.ai import cortecs_client
        from capp.classes.ai.cortecs_client import CortecsClient

        sentinel = {"id": "mistral-2", "choices": [{"message": {"content": "fallback"}}], "usage": {}}
        transient = openai.APITimeoutError(request=mock.Mock())

        fake_client = mock.Mock()
        fake_client.chat.completions.create.side_effect = transient

        with mock.patch.object(cortecs_client.settings, "CORTECS_API_KEY", "key"), mock.patch(
            "capp.classes.ai.cortecs_client.OpenAI", return_value=fake_client
        ), mock.patch("capp.classes.ai.cortecs_client.time.sleep"), mock.patch.object(
            cortecs_client.MistralClient, "chat_completion", return_value=sentinel
        ) as mistral:
            out = CortecsClient.chat_completion(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(out, sentinel)
        mistral.assert_called_once()


class EmbeddingProviderRoutingTests(TestCase):
    """
    Embeddings are the second processing category the legal disclosure describes,
    and it named the wrong provider. Cortecs is primary here exactly as it is for
    chat replies; Mistral is only reached when Cortecs is absent or fails.
    """

    def _cortecs_response(self, vector):
        return mock.Mock(data=[mock.Mock(embedding=vector)])

    def test_cortecs_is_used_first_and_mistral_is_not_called(self):
        from capp.classes.memory import memory_service
        from capp.classes.memory.memory_service import MemoryService

        fake_client = mock.Mock()
        fake_client.embeddings.create.return_value = self._cortecs_response([0.1, 0.2])

        with mock.patch.object(memory_service.settings, "CORTECS_API_KEY", "key"), mock.patch.object(
            memory_service.settings, "CORTECS_EMBED_MODEL", "qwen3-embedding-8b"
        ), mock.patch("openai.OpenAI", return_value=fake_client), mock.patch.object(
            memory_service.requests, "post"
        ) as mistral_post:
            result = MemoryService._get_embedding("remember this")

        self.assertEqual(result, ([0.1, 0.2], "qwen3-embedding-8b"))
        fake_client.embeddings.create.assert_called_once()
        mistral_post.assert_not_called()

    def test_falls_back_to_mistral_when_cortecs_key_is_missing(self):
        from capp.classes.memory import memory_service
        from capp.classes.memory.memory_service import MemoryService

        resp = mock.Mock()
        resp.json.return_value = {"data": [{"embedding": [0.3, 0.4]}]}
        resp.raise_for_status.return_value = None

        with mock.patch.object(memory_service.settings, "CORTECS_API_KEY", None), mock.patch.object(
            memory_service.settings, "MISTRAL_EMBED_MODEL", "mistral-embed"
        ), mock.patch.object(memory_service.requests, "post", return_value=resp) as mistral_post:
            result = MemoryService._get_embedding("remember this")

        self.assertEqual(result, ([0.3, 0.4], "mistral-embed"))
        mistral_post.assert_called_once()

    def test_falls_back_to_mistral_when_cortecs_fails(self):
        from capp.classes.memory import memory_service
        from capp.classes.memory.memory_service import MemoryService

        fake_client = mock.Mock()
        fake_client.embeddings.create.side_effect = RuntimeError("cortecs down")

        resp = mock.Mock()
        resp.json.return_value = {"data": [{"embedding": [0.5]}]}
        resp.raise_for_status.return_value = None

        with mock.patch.object(memory_service.settings, "CORTECS_API_KEY", "key"), mock.patch.object(
            memory_service.settings, "MISTRAL_EMBED_MODEL", "mistral-embed"
        ), mock.patch("openai.OpenAI", return_value=fake_client), mock.patch.object(
            memory_service.requests, "post", return_value=resp
        ) as mistral_post:
            result = MemoryService._get_embedding("remember this")

        self.assertEqual(result, ([0.5], "mistral-embed"))
        mistral_post.assert_called_once()


class ChatImageGenerationTests(TestCase):
    """In-chat image generation: auth, safety, billing, privacy, async."""

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="imgowner", email="imgowner@example.com", verified=True)
        self.other = _make_user(username="imgother", email="imgother@example.com", verified=True)
        self.character = _make_character(name="Zola", slug="zola")
        self.conversation = Conversation.objects.create(user=self.owner, character=self.character)
        SparkPool.objects.update_or_create(
            user=self.owner,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )

    def _endpoint(self, conv_id=None):
        cid = conv_id or self.conversation.conversation_id
        return f"/api/v1/conversations/{cid}/generate-image/"

    def _post(self, prompt, token=None, conv_id=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(
            self._endpoint(conv_id),
            data={"prompt": prompt},
            content_type="application/json",
            **headers,
        )

    # ---------- Authorization ----------

    def test_requires_authentication(self):
        resp = self._post("at the market")
        self.assertEqual(resp.status_code, 401)

    def test_guest_session_cannot_generate(self):
        # A guest supplies a session_id in the body (the guest chat flow), but
        # image generation is authenticated-only: the request is rejected before
        # any task is dispatched, regardless of the guest payload.
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self.client.post(
                self._endpoint(),
                data={"prompt": "at the market", "session_id": "guest-abc-123"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 401)
        delay.assert_not_called()
        # No message may be persisted for an unauthenticated request.
        self.assertFalse(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.USER).exists()
        )

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_staff_user_can_generate_in_own_conversation(self, _recaptcha):
        staff = _make_user(username="imgstaff", email="imgstaff@example.com", verified=True, is_staff=True)
        SparkPool.objects.update_or_create(
            user=staff,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )
        staff_conversation = Conversation.objects.create(user=staff, character=self.character)
        token = _bearer_token(self.client, "imgstaff")
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self._post("a portrait in the studio", token=token, conv_id=staff_conversation.conversation_id)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "success")
        delay.assert_called_once()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_cannot_generate_in_another_users_conversation(self, _recaptcha):
        token = _bearer_token(self.client, "imgother")
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self._post("a portrait", token=token)
        # The conversation is owned by imgowner, so imgother must get a 404 and
        # no task may be dispatched.
        self.assertEqual(resp.status_code, 404)
        delay.assert_not_called()

    # ---------- Prompt validation / safety ----------

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_empty_prompt_rejected(self, _recaptcha):
        token = _bearer_token(self.client, "imgowner")
        resp = self._post("   ", token=token)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "image_prompt_required")

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_adult_explicit_prompt_is_allowed(self, _recaptcha):
        """Adult NSFW is deliberately permitted; only the three subjects are not."""
        token = _bearer_token(self.client, "imgowner")
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self._post("a nude explicit photo on the beach", token=token)
        self.assertEqual(resp.status_code, 202)
        delay.assert_called_once()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_prohibited_subject_prompt_rejected_before_dispatch_or_charge(self, _recaptcha):
        token = _bearer_token(self.client, "imgowner")
        for prompt in (
            "a child playing in the sand",
            "sex with animal",
            "standing beside a corpse",
        ):
            with self.subTest(prompt=prompt):
                with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
                    resp = self._post(prompt, token=token)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["data"], PROHIBITED_SUBJECT_ERROR_CODE)
                delay.assert_not_called()

        # Nothing was stored and no sparks were spent on any rejected attempt.
        self.assertFalse(Message.objects.filter(conversation=self.conversation).exists())
        self.assertEqual(SparkPool.objects.get(user=self.owner).sparks, 5000)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_prohibited_subject_in_character_metadata_rejected(self, _recaptcha):
        """A clean prompt cannot ride in on a character field that also reaches the provider."""
        self.character.visual_style = "photo of a toddler"
        self.character.save(update_fields=["visual_style"])

        token = _bearer_token(self.client, "imgowner")
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self._post("standing in a sunny market", token=token)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], PROHIBITED_SUBJECT_ERROR_CODE)
        delay.assert_not_called()

    def test_task_blocks_prohibited_prompt_without_charging(self):
        """A direct ``.delay()`` bypassing the view still cannot reach a provider."""
        with mock.patch.object(CharacterService, "generate_gallery_image") as gen, mock.patch(
            "capp.tasks._send_system_message"
        ) as notify:
            generate_chat_image(
                self.conversation.conversation_id,
                None,
                "a photo of a 12 year old",
            )

        gen.assert_not_called()
        notify.assert_called_once()
        self.assertEqual(SparkPool.objects.get(user=self.owner).sparks, 5000)

    # ---------- Billing ----------

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_insufficient_sparks_blocks_before_dispatch(self, _recaptcha):
        pool = SparkPool.objects.get(user=self.owner)
        pool.sparks = IMAGE_GENERATION_COST - 1
        pool.save(update_fields=["sparks"])

        token = _bearer_token(self.client, "imgowner")
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self._post("a portrait", token=token)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "insufficient_sparks")
        delay.assert_not_called()
        # No request message should have been recorded.
        self.assertFalse(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.USER).exists()
        )

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_success_dispatches_task_records_request_and_reserves_in_view(self, _recaptcha):
        token = _bearer_token(self.client, "imgowner")
        with mock.patch("capp.views.conversation_views.generate_chat_image.delay") as delay:
            resp = self._post("standing in a sunny market", token=token)

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "success")
        delay.assert_called_once()

        # The prompt is recorded as a user message tagged as an image request.
        request_msg = Message.objects.get(conversation=self.conversation, sender_type=Message.SenderType.USER)
        self.assertEqual(request_msg.metadata.get("type"), "image_request")

        # The Sparks are held before the task is queued, so an unaffordable
        # request is a 400 on this response rather than a system message
        # arriving over the socket some seconds later. The task settles the
        # hold once the picture and its message exist.
        self.assertEqual(SparkPool.objects.get(user=self.owner).sparks, 5000 - IMAGE_GENERATION_COST)
        reservation = BillingReservation.objects.get(user=self.owner)
        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)
        self.assertEqual(reservation.feature, BillingReservation.Feature.IMAGE_GENERATION)
        self.assertEqual(reservation.idempotency_key, resp.headers["Idempotency-Key"])

    # ---------- Async task behaviour ----------

    def test_task_links_generated_image_to_chat_as_private(self):
        from django.core.files.base import ContentFile

        request_msg = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="on a hilltop at sunset",
            status=Message.StatusChoices.SENT,
            metadata={"type": "image_request"},
        )

        # A real gallery image so the task can persist its DURABLE storage key
        # (image.name) rather than a short-lived signed URL.
        gallery_image = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.owner,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
        )
        gallery_image.image.save("generated/zola.webp", ContentFile(b"img-bytes"), save=True)
        durable_name = gallery_image.image.name

        fake_result = {
            "status": "success",
            "data": {
                "image": {
                    "id": str(gallery_image.id),
                    "image_url": gallery_image.image.url,
                },
                "prompt_used": "on a hilltop at sunset",
            },
        }

        from capp.tasks import generate_chat_image

        with mock.patch(
            "capp.classes.characters.character_service.CharacterService.generate_gallery_image",
            return_value=fake_result,
        ) as gen:
            generate_chat_image.apply(
                kwargs={
                    "conversation_id": str(self.conversation.conversation_id),
                    "user_message_id": str(request_msg.message_id),
                    "prompt": "on a hilltop at sunset",
                }
            )

        # The reused gallery generator must be asked for a PRIVATE image owned by
        # the requesting user, guaranteeing cross-user privacy.
        _, kwargs = gen.call_args
        self.assertEqual(kwargs.get("actor"), self.owner)
        self.assertEqual(kwargs.get("is_public"), False)

        ai_msg = Message.objects.get(conversation=self.conversation, sender_type=Message.SenderType.AI)
        self.assertEqual(ai_msg.metadata.get("type"), "generated_image")
        # We persist the durable storage key, NOT a signed URL that would expire.
        self.assertEqual(ai_msg.metadata.get("image_name"), durable_name)
        self.assertNotIn("image_url", ai_msg.metadata)
        self.assertEqual(ai_msg.metadata.get("gallery_image_id"), str(gallery_image.id))

    def test_photo_offer_hold_settles_at_the_discounted_price(self):
        """Regression: accepting a character photo offer always failed.

        The accept view places the discounted CHARACTER_PHOTO_OFFER_COST hold;
        the task then re-reserved through generate_gallery_image at the
        standard IMAGE_GENERATION_COST. The same idempotency key presented
        with different terms is rejected as IdempotencyKeyConflict, so every
        accepted offer failed and was refunded after the offer card had
        already flipped to accepted. The task must resume the hold at the
        price it was actually placed at.
        """
        from capp.classes.billing.paid_features import PaidFeature
        from capp.constants import CHARACTER_PHOTO_OFFER_COST

        offer_message = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.AI,
            content="",
            status=Message.StatusChoices.SENT,
            metadata={
                "type": "photo_offer",
                "prompt": "a photo at the beach",
                "offer_operation_id": "offer-op-1",
                "offer_cost": CHARACTER_PHOTO_OFFER_COST,
                "offer_status": "pending",
            },
        )
        user_message = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="a photo at the beach",
            status=Message.StatusChoices.SENT,
            metadata={"type": "image_request"},
        )

        # Exactly what api_accept_photo_offer does before queueing the task:
        # reserve the discounted price against the offer's own operation key.
        reservation = PaidFeature.reserve(
            feature=BillingReservation.Feature.IMAGE_GENERATION,
            operation_id="offer-op-1",
            user=self.owner,
            operation_reference=f"chat_photo_offer:{self.conversation.conversation_id}:offer-op-1",
            cost_override=CHARACTER_PHOTO_OFFER_COST,
        )
        self.assertEqual(reservation.status, BillingReservation.Status.RESERVED)

        # fal/Seedream is the production provider; pin the test to that
        # branch (the test env leaves FAL_API_KEY unset, which would take the
        # Stability fallback instead).
        with override_settings(FAL_API_KEY="dummy-key-for-branch-selection"):
            with mock.patch.object(
                CharacterService, "_generate_fal_seedream_image", return_value=b"offer-image-bytes"
            ) as seedream, mock.patch.object(CharacterService, "_generate_ai_image") as stability:
                generate_chat_image(
                    str(self.conversation.conversation_id),
                    str(user_message.message_id),
                    "a photo at the beach",
                    operation_id="offer-op-1",
                    offer_message_id=str(offer_message.message_id),
                )

        seedream.assert_called_once()
        stability.assert_not_called()
        # The hold the view placed is the one that settled, at the discounted
        # price — one charge, no conflict, no refund.
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)
        self.assertEqual(reservation.settlement_reason, "chat_image_delivered")
        self.assertEqual(int(reservation.amount_sparks), CHARACTER_PHOTO_OFFER_COST)
        self.assertEqual(
            int(SparkPool.objects.get(user=self.owner).sparks),
            5000 - CHARACTER_PHOTO_OFFER_COST,
        )

        # The offer card is fulfilled and the picture is linked into the chat.
        offer_message.refresh_from_db()
        self.assertEqual(offer_message.metadata.get("offer_status"), "accepted")
        self.assertTrue(
            Message.objects.filter(
                conversation=self.conversation,
                sender_type=Message.SenderType.AI,
                metadata__type="generated_image",
            ).exists()
        )

    def test_task_failure_posts_system_message_and_no_ai_image(self):
        request_msg = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="a portrait",
            status=Message.StatusChoices.SENT,
            metadata={"type": "image_request"},
        )

        from capp.tasks import generate_chat_image

        with mock.patch(
            "capp.classes.characters.character_service.CharacterService.generate_gallery_image",
            return_value={"status": "fail", "data": "ai_generation_failed"},
        ), mock.patch("capp.tasks._send_system_message") as send_system:
            generate_chat_image.apply(
                kwargs={
                    "conversation_id": str(self.conversation.conversation_id),
                    "user_message_id": str(request_msg.message_id),
                    "prompt": "a portrait",
                }
            )

        self.assertFalse(
            Message.objects.filter(
                conversation=self.conversation,
                sender_type=Message.SenderType.AI,
                metadata__type="generated_image",
            ).exists()
        )
        send_system.assert_called_once()

    def test_task_soft_limit_is_not_swallowed(self):
        request_msg = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="a portrait",
            status=Message.StatusChoices.SENT,
            metadata={"type": "image_request"},
        )

        with mock.patch.object(
            CharacterService,
            "generate_gallery_image",
            side_effect=SoftTimeLimitExceeded(),
        ), mock.patch("capp.tasks._send_system_message") as send_system, self.assertRaises(SoftTimeLimitExceeded):
            generate_chat_image(
                str(self.conversation.conversation_id),
                str(request_msg.message_id),
                "a portrait",
            )

        send_system.assert_called_once()

    # ---------- Gallery privacy + billing via the reused service ----------

    def test_reused_gallery_generator_stores_private_image_and_charges_once(self):
        pool = SparkPool.objects.get(user=self.owner)
        pool.sparks = 5000
        pool.save(update_fields=["sparks"])

        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"fake-image-bytes"):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="in a garden",
                extra_prompt="in a garden",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "success")

        image = CharacterGalleryImage.objects.get(character=self.character)
        self.assertEqual(image.visibility, CharacterGalleryImage.VisibilityChoices.PRIVATE)
        self.assertEqual(image.created_by, self.owner)

        # Billing is reused verbatim: exactly the normal picture cost, charged once.
        self.assertEqual(SparkPool.objects.get(user=self.owner).sparks, 5000 - IMAGE_GENERATION_COST)


class GalleryVisibilityListTests(TestCase):
    """
    Private gallery images (e.g. those generated inside a chat) must be listed
    for their creator via the gallery endpoint, and hidden from everyone else.

    "Everyone else" includes staff, superusers and the character's own creator:
    an elevated flag does not widen the ordinary gallery. A moderator who needs
    to look at a reported picture does so through separate, audited tooling, not
    by browsing the gallery every user sees.
    """

    GUEST_SESSION_URL = "/api/v1/guest/session/"

    def setUp(self):
        cache.clear()
        self.creator = _make_user(username="galcreator", email="galcreator@example.com", verified=True)
        self.other = _make_user(username="galother", email="galother@example.com", verified=True)
        self.admin = _make_user(username="galadmin", email="galadmin@example.com", verified=True, is_staff=True)
        self.staff = _make_user(username="galstaff", email="galstaff@example.com", verified=True, is_staff=True)
        self.superuser = _make_user(username="galsuper", email="galsuper@example.com", verified=True, is_staff=True)
        self.superuser.is_superuser = True
        self.superuser.save(update_fields=["is_superuser"])
        # Owns the character, which must not grant sight of what other users
        # generated from it.
        self.char_owner = _make_user(username="galowner", email="galowner@example.com", verified=True)
        self.character = _make_character(name="Nia", slug="nia", created_by=self.char_owner)

        # Public image – visible to everyone.
        self.public_img = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.admin,
            visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC,
            order=0,
        )
        # Private image owned by ``creator`` – simulates a chat-generated image.
        self.private_img = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.creator,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
            order=1,
        )
        # A second user's private image, so "sees only their own" is separable
        # from "sees every private image".
        self.other_private_img = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.other,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
            order=2,
        )
        # Sits after the private rows, so a caller who cannot see them would
        # read a gap in ``order`` if the stored index were exposed verbatim.
        self.public_img_late = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.admin,
            visibility=CharacterGalleryImage.VisibilityChoices.PUBLIC,
            order=3,
        )

    def _list(self, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(f"/api/v1/characters/{self.character.character_id}/gallery/", **headers)

    def _ids(self, resp):
        return {img["id"] for img in resp.json()["data"]["images"]}

    def _public_ids(self):
        return {str(self.public_img.id), str(self.public_img_late.id)}

    def _assert_sees_public_only(self, resp):
        """The full contract for a caller with no private images of their own."""
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["data"]
        self.assertEqual(self._ids(resp), self._public_ids())
        # Metadata must not betray the hidden rows either: the count is of the
        # rows actually returned, and ``order`` is contiguous rather than the
        # stored 0-and-3 that would imply two invisible images in between.
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["images"]), 2)
        self.assertEqual([img["order"] for img in payload["images"]], [0, 1])

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_creator_sees_own_private_image_but_not_another_users(self, _recaptcha):
        token = _bearer_token(self.client, "galcreator")
        resp = self._list(token)
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["data"]
        self.assertEqual(self._ids(resp), self._public_ids() | {str(self.private_img.id)})
        self.assertNotIn(str(self.other_private_img.id), self._ids(resp))
        self.assertEqual(payload["count"], 3)
        self.assertEqual([img["order"] for img in payload["images"]], [0, 1, 2])

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_other_user_cannot_see_private_image(self, _recaptcha):
        token = _bearer_token(self.client, "galother")
        resp = self._list(token)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(str(self.private_img.id), self._ids(resp))
        # Its own private image is still listed, so this is not a blanket hide.
        self.assertIn(str(self.other_private_img.id), self._ids(resp))

    def test_anonymous_cannot_see_private_image(self):
        self._assert_sees_public_only(self._list())

    def test_guest_cannot_see_private_image(self):
        resp = self.client.post(self.GUEST_SESSION_URL, data={}, content_type="application/json")
        self.assertIn(resp.status_code, (200, 201))
        self._assert_sees_public_only(self._list())

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_staff_cannot_see_other_users_private_images(self, _recaptcha):
        token = _bearer_token(self.client, "galstaff")
        self._assert_sees_public_only(self._list(token))

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_cannot_see_other_users_private_images(self, _recaptcha):
        token = _bearer_token(self.client, "galadmin")
        self._assert_sees_public_only(self._list(token))

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_superuser_cannot_see_other_users_private_images(self, _recaptcha):
        token = _bearer_token(self.client, "galsuper")
        self._assert_sees_public_only(self._list(token))

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_character_creator_cannot_see_other_users_private_images(self, _recaptcha):
        token = _bearer_token(self.client, "galowner")
        self._assert_sees_public_only(self._list(token))

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_unrelated_user_cannot_delete_another_users_private_image(self, _recaptcha):
        token = _bearer_token(self.client, "galother")
        resp = self.client.delete(
            f"/api/v1/characters/gallery/{self.private_img.id}/delete/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(CharacterGalleryImage.objects.filter(id=self.private_img.id).exists())


class GalleryPrivateImageIsolationTests(TestCase):
    """
    The gallery has no per-image read endpoint, so the only lookup that can
    return a row the caller does not own is the paid generator's idempotency
    replay. Its operation ID arrives in a client-supplied ``Idempotency-Key``
    header while the uniqueness constraint behind it is only per character, so
    the replay lookup has to be scoped to the caller.
    """

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="idemowner", email="idemowner@example.com", verified=True)
        self.attacker = _make_user(username="idemother", email="idemother@example.com", verified=True)
        self.character = _make_character(name="Zuri", slug="zuri", created_by=self.owner)

        self.operation_id = str(uuid.uuid4())
        self.private_img = CharacterGalleryImage.objects.create(
            character=self.character,
            created_by=self.owner,
            visibility=CharacterGalleryImage.VisibilityChoices.PRIVATE,
            client_operation_id=self.operation_id,
            order=0,
        )

        SparkPool.objects.update_or_create(
            user=self.attacker,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )

    def test_replayed_operation_id_does_not_return_another_users_image(self):
        with mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"fake-image-bytes"):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="a portrait",
                actor=self.attacker,
                is_public=False,
                operation_id=self.operation_id,
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], "idempotency_key_conflict")

        # The owner's row is untouched and was never handed over.
        self.private_img.refresh_from_db()
        self.assertEqual(self.private_img.created_by, self.owner)
        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 1)

        # A rejected operation is not a paid one.
        self.assertEqual(SparkPool.objects.get(user=self.attacker).sparks, 5000)

    def test_owner_replay_still_returns_its_own_image(self):
        with mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"fake-image-bytes"):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="a portrait",
                actor=self.owner,
                is_public=False,
                operation_id=self.operation_id,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["image"]["id"], str(self.private_img.id))
        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 1)


class ChatTranscriptOwnershipTests(TestCase):
    """
    Chat-generated pictures reach the user through the conversation transcript,
    so that endpoint is the other half of the privacy contract: it must stay
    owner-only, with no staff or superuser bypass.
    """

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="tsowner", email="tsowner@example.com", verified=True)
        self.superuser = _make_user(username="tsadmin", email="tsadmin@example.com", verified=True, is_staff=True)
        self.superuser.is_superuser = True
        self.superuser.save(update_fields=["is_superuser"])
        self.character = _make_character(name="Sifa", slug="sifa")
        self.conversation = Conversation.objects.create(user=self.owner, character=self.character)
        Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="a portrait",
            status=Message.StatusChoices.SENT,
            metadata={"type": "image_request"},
        )

    def _messages(self, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(f"/api/v1/conversations/{self.conversation.conversation_id}/messages-list/", **headers)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_superuser_cannot_read_another_users_transcript(self, _recaptcha):
        token = _bearer_token(self.client, "tsadmin")
        resp = self._messages(token)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["data"], "permission_denied")

    def test_anonymous_cannot_read_a_users_transcript(self):
        self.assertEqual(self._messages().status_code, 403)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_owner_can_read_own_transcript(self, _recaptcha):
        token = _bearer_token(self.client, "tsowner")
        resp = self._messages(token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["data"]["messages"]), 1)


class PricingEndpointTests(TestCase):
    """The pricing endpoint is the single source of truth the UI reads."""

    def setUp(self):
        cache.clear()
        _make_user(username="priceuser", email="priceuser@example.com", verified=True)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_pricing_exposes_current_image_generation_cost(self, _recaptcha):
        token = _bearer_token(self.client, "priceuser")
        resp = self.client.get("/api/v1/pricing/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        # Tracks the backend constant, and the current agreed price is 450.
        self.assertEqual(data["image_generation_cost"], IMAGE_GENERATION_COST)
        self.assertEqual(data["image_generation_cost"], 450)


class ChatImageReferenceStorageTests(TestCase):
    """
    Regression tests for the production bug where in-chat image generation ran
    in a Celery worker that did NOT share the web service's media volume, so the
    character avatar could not be read locally. The worker then silently fell
    back to Stability txt2img and failed with "Stability AI credentials not
    configured". Reference acquisition must be storage-agnostic and keep fal.ai
    as the primary generator whenever FAL credentials + a reference exist.
    """

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="refowner", email="refowner@example.com", verified=True)
        self.character = _make_character(name="Ref", slug="ref")
        SparkPool.objects.update_or_create(
            user=self.owner,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )

    def _set_avatar(self):
        from django.core.files.base import ContentFile

        self.character.avatar.save("ref_avatar.webp", ContentFile(b"local-avatar-bytes"), save=True)

    # ---------- Reference acquisition helper ----------

    def test_reference_reads_avatar_via_storage(self):
        """Direct storage read works from any process (web OR worker) because
        media lives in the SAME shared backend (bucket in prod, local in tests)."""
        from capp.classes.characters.character_service import CharacterService

        self._set_avatar()

        data = CharacterService._get_character_reference_bytes(self.character)
        self.assertEqual(data, b"local-avatar-bytes")

    def test_reference_none_when_storage_read_fails(self):
        """If the storage backend cannot read the avatar, we return None (and let
        the caller fall back to txt2img) rather than raising."""
        from capp.classes.characters.character_service import CharacterService

        self._set_avatar()

        with mock.patch.object(self.character.avatar, "open", side_effect=OSError("unreadable")):
            data = CharacterService._get_character_reference_bytes(self.character)

        self.assertIsNone(data)

    def test_reference_none_when_no_avatar(self):
        from capp.classes.characters.character_service import CharacterService

        self.assertIsNone(CharacterService._get_character_reference_bytes(self.character))

    # ---------- End-to-end: fal stays primary, single charge ----------

    def test_worker_reference_uses_fal_edit_not_stability_and_charges_once(self):
        """The core fix: with a reference obtained via shared storage and FAL
        configured, fal.ai Seedream edit is used, Stability is never touched, and
        the user is charged exactly once."""
        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(
            CharacterService, "_get_character_reference_bytes", return_value=b"avatar-bytes"
        ), mock.patch.object(
            CharacterService, "_upload_to_fal_storage", return_value="https://fal.cdn/ref.webp"
        ) as upload, mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"fal-generated-image"
        ) as fal, mock.patch.object(
            CharacterService, "_generate_ai_image"
        ) as stability, self.settings(
            FAL_API_KEY="fal-test-key"
        ):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="at the market",
                extra_prompt="at the market",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "success")
        upload.assert_called_once()
        fal.assert_called_once()
        # Reference generation MUST use the edit endpoint via image_urls.
        self.assertEqual(fal.call_args.kwargs["image_urls"], ["https://fal.cdn/ref.webp"])
        stability.assert_not_called()

        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 1)
        self.assertEqual(SparkPool.objects.get(user=self.owner).sparks, 5000 - IMAGE_GENERATION_COST)

    def test_fal_upload_failure_falls_back_to_seedream_text_to_image(self):
        """If a reference exists but the fal.ai upload fails, we prefer Seedream
        text-to-image over dropping to Stability (fal stays primary)."""
        from capp.classes.characters.character_service import CharacterService

        calls = []

        def _seedream(*args, **kwargs):
            calls.append(kwargs.get("image_urls"))
            return b"fal-text-to-image"

        with mock.patch.object(
            CharacterService, "_get_character_reference_bytes", return_value=b"avatar-bytes"
        ), mock.patch.object(CharacterService, "_upload_to_fal_storage", return_value=None), mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", side_effect=_seedream
        ), mock.patch.object(
            CharacterService, "_generate_ai_image"
        ) as stability, self.settings(
            FAL_API_KEY="fal-test-key"
        ):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="on a hill",
                extra_prompt="on a hill",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "success")
        # Upload failed → single Seedream text-to-image call (no image_urls).
        self.assertEqual(calls, [None])
        stability.assert_not_called()

    def test_fal_total_failure_falls_back_to_stability_style(self):
        """When every fal path fails but a reference exists, we reuse it through
        Stability's style endpoint."""
        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(
            CharacterService, "_get_character_reference_bytes", return_value=b"avatar-bytes"
        ), mock.patch.object(
            CharacterService, "_upload_to_fal_storage", return_value="https://fal.cdn/ref.webp"
        ), mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=None
        ) as fal, mock.patch.object(
            CharacterService, "_generate_ai_image", return_value=b"stability-image"
        ) as stability, self.settings(
            FAL_API_KEY="fal-test-key"
        ):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="on a hill",
                extra_prompt="on a hill",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "success")
        # Both fal modes (edit then text-to-image) attempted before Stability.
        self.assertEqual(fal.call_count, 2)
        stability.assert_called_once()
        self.assertEqual(stability.call_args.kwargs["reference_mode"], "style")

    def test_no_charge_when_all_generation_paths_fail(self):
        """Single-charge semantics: a failed generation charges nothing."""
        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(
            CharacterService, "_get_character_reference_bytes", return_value=b"avatar-bytes"
        ), mock.patch.object(
            CharacterService, "_upload_to_fal_storage", return_value="https://fal.cdn/ref.webp"
        ), mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=None
        ), mock.patch.object(
            CharacterService, "_generate_ai_image", return_value=None
        ), self.settings(
            FAL_API_KEY="fal-test-key"
        ):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="anywhere",
                extra_prompt="anywhere",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], "ai_generation_failed")
        self.assertEqual(CharacterGalleryImage.objects.filter(character=self.character).count(), 0)
        self.assertEqual(SparkPool.objects.get(user=self.owner).sparks, 5000)

    def test_missing_avatar_uses_seedream_text_to_image(self):
        """No usable reference: Seedream text-to-image is primary (fal), Stability
        is not touched."""
        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(
            CharacterService, "_get_character_reference_bytes", return_value=None
        ), mock.patch.object(CharacterService, "_upload_to_fal_storage") as upload, mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"fal-text-to-image"
        ) as fal, mock.patch.object(
            CharacterService, "_generate_ai_image"
        ) as stability, self.settings(
            FAL_API_KEY="fal-test-key"
        ):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="a scene",
                extra_prompt="a scene",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "success")
        upload.assert_not_called()
        fal.assert_called_once()
        # text-to-image: no image_urls passed.
        self.assertIsNone(fal.call_args.kwargs.get("image_urls"))
        stability.assert_not_called()

    def test_no_fal_key_falls_back_to_stability(self):
        """With FAL unconfigured, Stability is the provider (fallback role)."""
        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(
            CharacterService, "_get_character_reference_bytes", return_value=b"avatar-bytes"
        ), mock.patch.object(CharacterService, "_generate_fal_seedream_image") as fal, mock.patch.object(
            CharacterService, "_generate_ai_image", return_value=b"stability-image"
        ) as stability, self.settings(
            FAL_API_KEY=""
        ):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="a scene",
                extra_prompt="a scene",
                actor=self.owner,
                is_public=False,
            )

        self.assertEqual(result["status"], "success")
        fal.assert_not_called()
        stability.assert_called_once()


class SeedreamRequestHelperTests(TestCase):
    """
    Directly exercise the fal.ai Seedream v5 Lite request helper to prove it
    selects the correct endpoint/payload for each mode and validates responses.
    """

    def setUp(self):
        cache.clear()

    @staticmethod
    def _valid_png_bytes():
        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), (120, 90, 60)).save(buffer, format="PNG")
        return buffer.getvalue()

    def _fake_post(self, status=200, images=None, headers=None):
        resp = mock.Mock()
        resp.status_code = status
        resp.headers = headers or {}
        resp.text = "err"
        if images is _UNSET:
            payload = {"images": [{"url": "https://fal.cdn/out.webp"}]}
        else:
            payload = {"images": images}
        resp.iter_content.return_value = [json.dumps(payload).encode("utf-8")]
        return resp

    def test_text_to_image_endpoint_and_payload(self):
        from capp.classes.characters.character_service import CharacterService

        post = mock.Mock(return_value=self._fake_post(images=_UNSET))
        image_bytes = self._valid_png_bytes()
        get_response = mock.Mock(status_code=200, headers={"Content-Type": "image/png"})
        get_response.iter_content.return_value = [image_bytes]
        get = mock.Mock(return_value=get_response)

        with mock.patch("requests.post", post), mock.patch("requests.get", get), self.settings(
            FAL_API_KEY="k",
            FAL_API_BASE_URL="https://fal.run",
            FAL_TEXT_TO_IMAGE_MODEL="bytedance/seedream/v5/lite/text-to-image",
            FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS=("fal.cdn",),
        ):
            out = CharacterService._generate_fal_seedream_image(prompt="hi", image_size="square_hd")

        self.assertEqual(out, image_bytes)
        url = post.call_args.args[0]
        self.assertEqual(url, "https://fal.run/bytedance/seedream/v5/lite/text-to-image")
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("image_urls", payload)
        self.assertEqual(payload["num_images"], 1)
        self.assertEqual(payload["max_images"], 1)
        self.assertIs(payload["enable_safety_checker"], False)
        self.assertEqual(post.call_args.kwargs["headers"]["X-Fal-Store-IO"], "0")

    def test_edit_endpoint_used_when_image_urls_present(self):
        from capp.classes.characters.character_service import CharacterService

        post = mock.Mock(return_value=self._fake_post(images=_UNSET))
        image_bytes = self._valid_png_bytes()
        get_response = mock.Mock(status_code=200, headers={"Content-Type": "image/png"})
        get_response.iter_content.return_value = [image_bytes]
        get = mock.Mock(return_value=get_response)

        with mock.patch("requests.post", post), mock.patch("requests.get", get), self.settings(
            FAL_API_KEY="k",
            FAL_API_BASE_URL="https://fal.run",
            FAL_IMAGE_EDIT_MODEL="bytedance/seedream/v5/lite/edit",
            FAL_IMAGE_DOWNLOAD_ALLOWED_HOSTS=("fal.cdn",),
        ):
            out = CharacterService._generate_fal_seedream_image(
                prompt="hi",
                image_urls=["https://fal.cdn/ref.webp"],
                image_size="portrait_4_3",
            )

        self.assertEqual(out, image_bytes)
        self.assertEqual(post.call_args.args[0], "https://fal.run/bytedance/seedream/v5/lite/edit")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["image_urls"], ["https://fal.cdn/ref.webp"])
        self.assertIs(payload["enable_safety_checker"], False)

    def test_malformed_response_returns_none(self):
        from capp.classes.characters.character_service import CharacterService

        post = mock.Mock(return_value=self._fake_post(images=[]))
        with mock.patch("requests.post", post), self.settings(FAL_API_KEY="k"):
            out = CharacterService._generate_fal_seedream_image(prompt="hi")
        self.assertIsNone(out)

    def test_non_2xx_returns_none(self):
        from capp.classes.characters.character_service import CharacterService

        post = mock.Mock(return_value=self._fake_post(status=500, images=_UNSET))
        with mock.patch("requests.post", post), self.settings(FAL_API_KEY="k"):
            out = CharacterService._generate_fal_seedream_image(prompt="hi")
        self.assertIsNone(out)

    def test_no_api_key_returns_none(self):
        from capp.classes.characters.character_service import CharacterService

        with self.settings(FAL_API_KEY=""):
            self.assertIsNone(CharacterService._generate_fal_seedream_image(prompt="hi"))

    def test_aspect_ratio_maps_without_resolution_inflation(self):
        from capp.classes.characters.character_service import CharacterService

        self.assertEqual(CharacterService._aspect_ratio_to_fal_image_size("1:1"), "square_hd")
        self.assertEqual(CharacterService._aspect_ratio_to_fal_image_size("2:3"), "portrait_4_3")
        self.assertEqual(CharacterService._aspect_ratio_to_fal_image_size("16:9"), "landscape_16_9")
        # Unknown ratios fall back to the provided default, never auto_2K.
        self.assertEqual(CharacterService._aspect_ratio_to_fal_image_size("weird", default="square_hd"), "square_hd")


class AvatarSeedreamGenerationTests(TestCase):
    """generate_avatar uses Seedream (edit with reference, text-to-image without)
    as primary, with Stability as fallback."""

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="avowner", email="avowner@example.com", verified=True)
        self.character = _make_character(name="Av", slug="av", created_by=self.owner)
        SparkPool.objects.update_or_create(
            user=self.owner,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )

    def test_first_avatar_uses_seedream_text_to_image(self):
        from capp.classes.characters.character_service import CharacterService

        with mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"fal-img"
        ) as fal, mock.patch.object(CharacterService, "_generate_ai_image") as stability, self.settings(
            FAL_API_KEY="k"
        ):
            result = CharacterService.generate_avatar(character=self.character, prompt="hi", actor=self.owner)

        self.assertEqual(result["status"], "success")
        fal.assert_called_once()
        self.assertIsNone(fal.call_args.kwargs.get("image_urls"))
        stability.assert_not_called()

    def test_regenerate_avatar_with_reference_uses_seedream_edit(self):
        from django.core.files.base import ContentFile
        from capp.classes.characters.character_service import CharacterService

        self.character.avatar.save("a.webp", ContentFile(b"avatar-bytes"), save=True)

        with mock.patch.object(
            CharacterService, "_upload_to_fal_storage", return_value="https://fal.cdn/ref.webp"
        ), mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"fal-img"
        ) as fal, mock.patch.object(
            CharacterService, "_generate_ai_image"
        ) as stability, self.settings(
            FAL_API_KEY="k"
        ):
            result = CharacterService.generate_avatar(character=self.character, prompt="hi", actor=self.owner)

        self.assertEqual(result["status"], "success")
        fal.assert_called_once()
        self.assertEqual(fal.call_args.kwargs["image_urls"], ["https://fal.cdn/ref.webp"])
        stability.assert_not_called()


class StorageConfigTests(TestCase):
    """
    The STORAGES builder selects the private Railway Bucket when fully
    configured, falls back to local media only in development/tests, and fails
    closed in production whenever the bucket configuration is absent or partial.
    """

    BUCKET_ENV = {
        "AWS_STORAGE_BUCKET_NAME": "eaaic-media",
        "AWS_ACCESS_KEY_ID": "key",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_S3_ENDPOINT_URL": "https://bucket.railway.app",
    }

    def test_resolve_reports_complete_when_all_present(self):
        from eaaicproject.storage_config import resolve_bucket_config

        cfg = resolve_bucket_config(self.BUCKET_ENV)
        self.assertTrue(cfg["complete"])
        self.assertFalse(cfg["partial"])
        self.assertEqual(cfg["missing"], [])

    def test_resolve_reports_partial_when_some_missing(self):
        from eaaicproject.storage_config import resolve_bucket_config

        env = dict(self.BUCKET_ENV)
        del env["AWS_SECRET_ACCESS_KEY"]
        cfg = resolve_bucket_config(env)
        self.assertFalse(cfg["complete"])
        self.assertTrue(cfg["partial"])
        self.assertIn("AWS_SECRET_ACCESS_KEY", cfg["missing"])

    def test_complete_config_selects_private_s3_backend(self):
        from eaaicproject.storage_config import build_storages, S3_BACKEND

        storages, mode, warning = build_storages(self.BUCKET_ENV, is_production=True)
        self.assertEqual(mode, "s3")
        self.assertIsNone(warning)
        self.assertEqual(storages["default"]["BACKEND"], S3_BACKEND)
        opts = storages["default"]["OPTIONS"]
        # Objects must be PRIVATE and served via short-lived signed URLs.
        self.assertIsNone(opts["default_acl"])
        self.assertTrue(opts["querystring_auth"])
        self.assertEqual(opts["bucket_name"], "eaaic-media")
        self.assertFalse(opts["file_overwrite"])

    def test_resolve_reports_neither_complete_nor_partial_when_absent(self):
        from eaaicproject.storage_config import resolve_bucket_config

        cfg = resolve_bucket_config({})
        self.assertFalse(cfg["complete"])
        self.assertFalse(cfg["partial"])
        self.assertEqual(cfg["present"], [])
        self.assertEqual(sorted(cfg["missing"]), sorted(self.BUCKET_ENV))

    def test_no_config_uses_local_backend_in_dev(self):
        from eaaicproject.storage_config import build_storages, LOCAL_BACKEND

        storages, mode, warning = build_storages({}, is_production=False)
        self.assertEqual(mode, "local")
        self.assertIsNone(warning)
        self.assertEqual(storages["default"]["BACKEND"], LOCAL_BACKEND)

    def test_absent_config_raises_in_production(self):
        """Production must fail closed rather than fall back to local media."""
        from django.core.exceptions import ImproperlyConfigured
        from eaaicproject.storage_config import build_storages

        with self.assertRaises(ImproperlyConfigured) as ctx:
            build_storages({}, is_production=True)
        self.assertIn("AWS_STORAGE_BUCKET_NAME", str(ctx.exception))

    def test_blank_values_are_treated_as_absent_and_raise_in_production(self):
        from django.core.exceptions import ImproperlyConfigured
        from eaaicproject.storage_config import build_storages

        env = {key: "   " for key in self.BUCKET_ENV}
        with self.assertRaises(ImproperlyConfigured):
            build_storages(env, is_production=True)

    def test_complete_config_in_dev_still_uses_bucket(self):
        from eaaicproject.storage_config import build_storages, S3_BACKEND

        storages, mode, warning = build_storages(self.BUCKET_ENV, is_production=False)
        self.assertEqual(mode, "s3")
        self.assertIsNone(warning)
        self.assertEqual(storages["default"]["BACKEND"], S3_BACKEND)

    def test_partial_config_warns_in_dev(self):
        from eaaicproject.storage_config import build_storages, LOCAL_BACKEND

        env = dict(self.BUCKET_ENV)
        del env["AWS_ACCESS_KEY_ID"]
        storages, mode, warning = build_storages(env, is_production=False)
        self.assertEqual(mode, "local")
        self.assertIsNotNone(warning)
        self.assertEqual(storages["default"]["BACKEND"], LOCAL_BACKEND)

    def test_partial_config_raises_in_production(self):
        from django.core.exceptions import ImproperlyConfigured
        from eaaicproject.storage_config import build_storages

        env = dict(self.BUCKET_ENV)
        del env["AWS_ACCESS_KEY_ID"]
        with self.assertRaises(ImproperlyConfigured):
            build_storages(env, is_production=True)

    def test_bucket_name_alias_used_when_canonical_absent(self):
        from eaaicproject.storage_config import build_storages, S3_BACKEND

        env = dict(self.BUCKET_ENV)
        del env["AWS_STORAGE_BUCKET_NAME"]
        env["BUCKET_NAME"] = "eaaic-media"
        storages, mode, warning = build_storages(env, is_production=True)
        self.assertEqual(mode, "s3")
        self.assertIsNone(warning)
        self.assertEqual(storages["default"]["BACKEND"], S3_BACKEND)
        self.assertEqual(storages["default"]["OPTIONS"]["bucket_name"], "eaaic-media")

    def test_canonical_bucket_name_takes_precedence_over_alias(self):
        from eaaicproject.storage_config import build_storages

        env = dict(self.BUCKET_ENV)
        env["AWS_STORAGE_BUCKET_NAME"] = "canonical-bucket"
        env["BUCKET_NAME"] = "alias-bucket"
        storages, mode, _warning = build_storages(env, is_production=True)
        self.assertEqual(mode, "s3")
        self.assertEqual(storages["default"]["OPTIONS"]["bucket_name"], "canonical-bucket")


class MediaServingGateTests(SimpleTestCase):
    """
    Django only serves MEDIA_ROOT in local development. With DEBUG off there is
    no ``/media/`` route at all, so the media tree cannot be read anonymously;
    production media lives in the private bucket behind signed URLs.

    Django's test runner forces DEBUG=False, so the default URLconf exercised
    here is the production one.
    """

    def _media_patterns(self, urlconf):
        return [str(entry.pattern) for entry in urlconf.urlpatterns if "media" in str(entry.pattern)]

    def _reload_urlconf(self):
        import importlib

        from django.urls import clear_url_caches

        import eaaicproject.urls

        module = importlib.reload(eaaicproject.urls)
        clear_url_caches()
        return module

    def test_no_media_route_registered_when_debug_off(self):
        self.assertFalse(settings.DEBUG)
        self.assertEqual(self._media_patterns(self._reload_urlconf()), [])

    def test_media_request_is_not_served_when_debug_off(self):
        response = self.client.get("/media/avatars/secret.png")
        self.assertEqual(response.status_code, 404)

    def test_media_route_registered_when_debug_on(self):
        self.addCleanup(self._reload_urlconf)
        with self.settings(DEBUG=True):
            self.assertEqual(len(self._media_patterns(self._reload_urlconf())), 1)


class RequirementsLockTests(SimpleTestCase):
    """
    requirements.in is the source of truth and requirements.txt is its compiled
    lock. These checks fail if the two drift, so a recompile can never quietly
    resolve a dependency back below a version pinned for a security advisory.
    """

    BASE_DIR = pathlib.Path(settings.BASE_DIR)
    COMPILED_FOR_PYTHON = "3.12"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.declared = cls._parse_requirements_in()
        cls.locked = cls._parse_lock()

    @classmethod
    def _parse_requirements_in(cls):
        """Return {name: (operator, Version)} for every explicitly bounded entry."""
        pattern = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*(==|>=)\s*([A-Za-z0-9._+!-]+)\s*$")
        declared = {}
        for raw in (cls.BASE_DIR / "requirements.in").read_text().splitlines():
            match = pattern.match(raw.split("#")[0].strip())
            if match:
                declared[canonicalize_name(match.group(1))] = (match.group(2), Version(match.group(3)))
        return declared

    @classmethod
    def _parse_lock(cls):
        pattern = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([A-Za-z0-9._+!-]+)\s*$")
        locked = {}
        for raw in (cls.BASE_DIR / "requirements.txt").read_text().splitlines():
            match = pattern.match(raw.split("#")[0].strip())
            if match:
                locked[canonicalize_name(match.group(1))] = Version(match.group(2))
        return locked

    def test_every_declared_requirement_is_locked(self):
        missing = sorted(set(self.declared) - set(self.locked))
        self.assertEqual(missing, [], f"Declared in requirements.in but absent from the lock: {missing}. Recompile.")

    def test_locked_versions_satisfy_declared_bounds(self):
        violations = []
        for name, (operator, bound) in self.declared.items():
            actual = self.locked.get(name)
            if actual is None:
                continue
            if operator == "==" and actual != bound:
                violations.append(f"{name}: requirements.in pins =={bound}, lock has {actual}")
            elif operator == ">=" and actual < bound:
                violations.append(f"{name}: requirements.in requires >={bound}, lock has {actual}")
        self.assertEqual(violations, [], "requirements.txt has drifted from requirements.in: " + "; ".join(violations))

    def test_lock_was_compiled_for_the_supported_python(self):
        """A recompile on another interpreter can silently change resolution."""
        header = (self.BASE_DIR / "requirements.txt").read_text()[:400]
        self.assertIn(f"pip-compile with Python {self.COMPILED_FOR_PYTHON}", header)

    def test_no_stale_freeze_file_reappears(self):
        """freeze.txt was a drifted pip freeze dump; requirements.txt is the lock."""
        self.assertFalse(
            (self.BASE_DIR / "freeze.txt").exists(),
            "freeze.txt is not authoritative — add dependencies to requirements.in and recompile.",
        )


class ChatImageUrlResolutionTests(TestCase):
    """
    Chat image messages store a DURABLE storage key and resolve a FRESH URL at
    read time, so private-bucket presigned URLs never go stale and are never
    persisted in the database.
    """

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="urlowner", email="urlowner@example.com", verified=True)
        self.character = _make_character(name="Urlie", slug="urlie")
        self.conversation = Conversation.objects.create(user=self.owner, character=self.character)

    def _list_messages(self):
        with mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK):
            token = _bearer_token(self.client, "urlowner")
        return self.client.get(
            f"/api/v1/conversations/{self.conversation.conversation_id}/messages-list/",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_resolves_fresh_url_from_durable_key(self):
        Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.AI,
            content="",
            status=Message.StatusChoices.SENT,
            metadata={"type": "generated_image", "image_name": "generated/pic.webp"},
        )

        # Simulate a private bucket handing back a short-lived signed URL.
        signed = "https://bucket.railway.app/generated/pic.webp?X-Amz-Signature=abc123"
        with mock.patch("capp.views.conversation_views.default_storage.url", return_value=signed) as url_fn:
            resp = self._list_messages()

        self.assertEqual(resp.status_code, 200)
        url_fn.assert_called_with("generated/pic.webp")
        msg = resp.json()["data"]["messages"][0]
        self.assertEqual(msg["image_url"], signed)

    def test_legacy_image_url_metadata_still_renders(self):
        Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.AI,
            content="",
            status=Message.StatusChoices.SENT,
            metadata={"type": "generated_image", "image_url": "/media/generated/old.webp"},
        )

        resp = self._list_messages()
        self.assertEqual(resp.status_code, 200)
        msg = resp.json()["data"]["messages"][0]
        self.assertTrue(msg["image_url"].endswith("/media/generated/old.webp"))


class MigrateMediaToBucketCommandTests(TestCase):
    """
    The migrate_media_to_bucket command copies local files to the configured
    storage under their relative key, is idempotent (skips existing), supports
    a dry run, and never deletes the source.
    """

    def _run(self, source, **opts):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("migrate_media_to_bucket", source=source, stdout=out, stderr=out, **opts)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as src:
            (Path(src) / "sub").mkdir()
            (Path(src) / "sub" / "a.webp").write_bytes(b"data")

            with mock.patch("capp.management.commands.migrate_media_to_bucket.default_storage") as storage:
                storage.exists.return_value = False
                self._run(src, dry_run=True)
                storage.save.assert_not_called()

    def test_copies_files_under_relative_key(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as src:
            (Path(src) / "sub").mkdir()
            (Path(src) / "sub" / "a.webp").write_bytes(b"data")

            with mock.patch("capp.management.commands.migrate_media_to_bucket.default_storage") as storage:
                storage.exists.return_value = False
                storage.save.side_effect = lambda key, _f: key
                self._run(src)

            saved_keys = [c.args[0] for c in storage.save.call_args_list]
            self.assertEqual(saved_keys, ["sub/a.webp"])
            # Source is never removed.
            self.assertTrue((Path(src) / "sub" / "a.webp").exists())

    def test_skips_existing_by_default(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as src:
            (Path(src) / "a.webp").write_bytes(b"data")

            with mock.patch("capp.management.commands.migrate_media_to_bucket.default_storage") as storage:
                storage.exists.return_value = True
                self._run(src)
                storage.save.assert_not_called()


class CeleryQueueRoutingTests(SimpleTestCase):
    """
    Guards the general/images queue isolation. Image generation must never share
    a worker with fast interactive tasks (memory, relationship, summary,
    subscriptions, proactive chat), so routing is asserted by exact task name.
    """

    # Every non-image task, by exact registered name, must land on ``general``.
    GENERAL_TASKS = [
        "capp.tasks.process_ai_response",
        "capp.tasks.extract_memory_facts",
        "capp.tasks.update_relationship",
        "capp.tasks.maybe_summarize_conversation",
        "capp.tasks.retrospective_memory_for_new_user",
        "capp.tasks.check_and_send_proactive_messages",
        "capp.tasks.refund_billing_reservation",
        "capp.tasks.reconcile_stale_image_reservations",
        "expire_subscriptions",
    ]
    IMAGE_TASKS = ["capp.tasks.generate_chat_image"]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Import so shared_task definitions register with the app; the router
        # resolves by name regardless, but this lets us assert names exactly.
        import capp.tasks  # noqa: F401
        from eaaicproject.celery import app

        cls.app = app

    def _queue_for(self, task_name):
        options = self.app.amqp.router.route({}, task_name)
        queue = options.get("queue")
        return getattr(queue, "name", queue)

    def test_default_queue_is_general(self):
        self.assertEqual(self.app.conf.task_default_queue, "general")

    def test_declared_queues_exist(self):
        names = {q.name for q in self.app.conf.task_queues}
        self.assertEqual(names, {"general", "images"})

    def test_image_task_routes_to_images(self):
        for name in self.IMAGE_TASKS:
            with self.subTest(task=name):
                self.assertEqual(self._queue_for(name), "images")

    def test_general_tasks_route_to_general(self):
        for name in self.GENERAL_TASKS:
            with self.subTest(task=name):
                self.assertEqual(self._queue_for(name), "general")

    def test_task_names_are_registered(self):
        for name in self.IMAGE_TASKS + self.GENERAL_TASKS:
            with self.subTest(task=name):
                self.assertIn(name, self.app.tasks)

    def test_beat_schedule_tasks_route_to_general(self):
        from django.conf import settings

        schedule = settings.CELERY_BEAT_SCHEDULE
        self.assertTrue(schedule)
        for entry_name, entry in schedule.items():
            with self.subTest(beat_entry=entry_name):
                explicit_queue = entry.get("options", {}).get("queue")
                # Beat entries either pin ``general`` explicitly or fall through
                # to the default queue, which is also ``general``. Never images.
                resolved = explicit_queue or self._queue_for(entry["task"])
                self.assertEqual(resolved, "general")


def _make_message(conversation, sender_type, content, created_at=None, metadata=None):
    """Create a Message, optionally pinning ``created_at`` (which is auto_now_add).

    Pinning the timestamp lets us assert deterministic newest-activity ordering
    including exact ties.
    """
    msg = Message.objects.create(
        conversation=conversation,
        sender_type=sender_type,
        content=content,
        status=Message.StatusChoices.SENT,
        metadata=metadata or {},
    )
    if created_at is not None:
        Message.objects.filter(pk=msg.pk).update(created_at=created_at)
        msg.refresh_from_db()
    return msg


class ConversationListTests(TestCase):
    """Regression coverage for the Chats tab conversation-list endpoint.

    Root cause of the production bug: a conversation whose most-recent message
    carried empty text content (e.g. an AI-generated image, stored with
    ``content=""``) was silently dropped from the list. The endpoint must list
    every conversation that has at least one message, regardless of whether the
    newest message has text.
    """

    LIST_URL = "/api/v1/conversations/"

    def setUp(self):
        cache.clear()
        self.user = _make_user(username="lister", email="lister@example.com", verified=True)
        self.other = _make_user(username="other", email="other@example.com", verified=True)
        self.character = _make_character(name="Amina", slug="amina")

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def _auth_get(self, username, _recaptcha):
        token = _bearer_token(self.client, username)
        return self.client.get(self.LIST_URL, HTTP_AUTHORIZATION=f"Bearer {token}")

    def _conversations(self, resp):
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        return body["data"]["conversations"]

    def test_image_only_last_message_conversation_is_listed(self):
        """The exact production bug: an image-only newest message must not hide the chat."""
        conv = Conversation.objects.create(user=self.user, character=self.character)
        _make_message(conv, Message.SenderType.USER, "draw me a sunset")
        # AI image reply is stored with empty content + image metadata.
        _make_message(
            conv,
            Message.SenderType.AI,
            "",
            metadata={"type": "generated_image", "image_name": "characters/gallery/x.png"},
        )

        convs = self._conversations(self._auth_get("lister"))
        self.assertEqual(len(convs), 1)
        item = convs[0]
        self.assertEqual(item["conversation_id"], str(conv.conversation_id))
        # Empty raw content, but a non-empty human preview is provided.
        self.assertEqual(item["last_message"]["content"], "")
        self.assertTrue(item["last_message"]["preview"].strip())
        self.assertEqual(item["last_message"]["type"], "generated_image")

    def test_conversation_without_messages_is_omitted(self):
        """A character opened but never messaged is not a real chat and stays hidden."""
        Conversation.objects.create(user=self.user, character=self.character)
        convs = self._conversations(self._auth_get("lister"))
        self.assertEqual(convs, [])

    def test_all_conversations_listed_beyond_any_page_size(self):
        """No implicit page-size cap: every messaged conversation is returned."""
        total = 30
        for i in range(total):
            char = _make_character(name=f"Char{i}", slug=f"char-{i}")
            conv = Conversation.objects.create(user=self.user, character=char)
            _make_message(
                conv,
                Message.SenderType.USER,
                f"hello {i}",
                created_at=timezone.now() - timedelta(minutes=i),
            )

        convs = self._conversations(self._auth_get("lister"))
        self.assertEqual(len(convs), total)

    def test_ordering_is_newest_activity_first_and_deterministic_on_ties(self):
        now = timezone.now()
        older = _make_character(name="Older", slug="older")
        newer = _make_character(name="Newer", slug="newer")
        tie_a = _make_character(name="TieA", slug="tie-a")
        tie_b = _make_character(name="TieB", slug="tie-b")

        c_older = Conversation.objects.create(user=self.user, character=older)
        c_newer = Conversation.objects.create(user=self.user, character=newer)
        c_tie_a = Conversation.objects.create(user=self.user, character=tie_a)
        c_tie_b = Conversation.objects.create(user=self.user, character=tie_b)

        _make_message(c_older, Message.SenderType.USER, "old", created_at=now - timedelta(hours=2))
        _make_message(c_newer, Message.SenderType.USER, "new", created_at=now)
        tie_ts = now - timedelta(hours=1)
        _make_message(c_tie_a, Message.SenderType.USER, "tieA", created_at=tie_ts)
        _make_message(c_tie_b, Message.SenderType.USER, "tieB", created_at=tie_ts)

        first = [c["conversation_id"] for c in self._conversations(self._auth_get("lister"))]
        second = [c["conversation_id"] for c in self._conversations(self._auth_get("lister"))]

        # Newest first, oldest last; tied pair sits in the middle in a stable order.
        self.assertEqual(first[0], str(c_newer.conversation_id))
        self.assertEqual(first[-1], str(c_older.conversation_id))
        self.assertEqual(first, second)
        self.assertEqual(set(first[1:3]), {str(c_tie_a.conversation_id), str(c_tie_b.conversation_id)})

    def test_user_isolation_other_users_chats_never_appear(self):
        mine = Conversation.objects.create(user=self.user, character=self.character)
        _make_message(mine, Message.SenderType.USER, "mine")
        theirs = Conversation.objects.create(user=self.other, character=self.character)
        _make_message(theirs, Message.SenderType.USER, "theirs")

        convs = self._conversations(self._auth_get("lister"))
        ids = {c["conversation_id"] for c in convs}
        self.assertIn(str(mine.conversation_id), ids)
        self.assertNotIn(str(theirs.conversation_id), ids)

    def test_closed_conversation_with_messages_still_listed(self):
        conv = Conversation.objects.create(
            user=self.user, character=self.character, status=Conversation.StatusChoices.CLOSED
        )
        _make_message(conv, Message.SenderType.USER, "past chat")
        convs = self._conversations(self._auth_get("lister"))
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["status"], Conversation.StatusChoices.CLOSED)

    def test_text_last_message_content_is_decrypted(self):
        conv = Conversation.objects.create(user=self.user, character=self.character)
        _make_message(conv, Message.SenderType.AI, "habari yako rafiki")
        convs = self._conversations(self._auth_get("lister"))
        self.assertEqual(convs[0]["last_message"]["content"], "habari yako rafiki")
        self.assertEqual(convs[0]["last_message"]["preview"], "habari yako rafiki")


class ImagePromptPolicyTests(SimpleTestCase):
    """
    Unit coverage for the prohibited-subject policy itself.

    The policy is deliberately narrow: adult NSFW is allowed, and only minors,
    bestiality/zoophilia and deceased people are refused. Normal, non-sexual
    animal scenes (pets, riding, farming, wildlife) are allowed. These cases pin
    both halves of that contract, plus the substring false-positives the term
    lists must not trip on.
    """

    ALLOWED = (
        # Adult explicit content is a product requirement, not a violation.
        "a nude woman on the beach",
        "explicit sex scene between two adults",
        "naked, erotic, nsfw portrait",
        "hardcore porn, xxx, hentai style",
        "topless adult woman, 25 years old",
        # Benign prompts whose words merely contain a prohibited term.
        "on vacation in Zanzibar",
        "a coward turning away",
        "holding a battery powered lamp",
        "wearing a hat at a communication summit",
        "a scarf in a 17th century market",
        "sitting on a concrete bench",
        "dead leaves scattered on the path",
        "at a dead end street",
        "a deadpan expression",
        "a grave expression on her face",
        "brown pupils, wide eyes",
        "a small mole on her cheek",
        "hair in a ponytail",
        "an 18-year-old Kenyan woman",
        "a 30 year old man in Nairobi",
        # Normal, non-sexual animal scenes are now allowed.
        "posing with her pet dog",
        "a cat on the windowsill",
        "riding a horse",
        "holding a chicken",
        "an elephant in the savannah",
        "a lion resting",
        "a butterfly on her hand",
        "a snake around her neck",
        "beside a cow in the field",
        "mbwa na paka",
        "simba wa Serengeti",
        "nyuki kwenye ua",
    )

    BLOCKED = {
        "minors": (
            "a child playing in the sand",
            "two children at the market",
            "a baby in a blanket",
            "a toddler on the floor",
            "a teenage girl smiling",
            "an underage person",
            "a little girl in a dress",
            "girl child portrait",
            "she is 15 years old",
            "aged 12",
            "a 9yo subject",
            "grade 5 student",
            "only 13",
            "13 years of age",
            "a schoolgirl in uniform",
            "his daughter beside him",
            "mtoto mdogo sokoni",
            "msichana shuleni",
            "umri wa 12",
            "miaka 14",
            "ch1ld portrait",
            "c h i l d portrait",
            "c.h.i.l.d portrait",
            "сhild portrait",
        ),
        "bestiality": (
            "a bestiality scene",
            "zoophilia content",
            "sex with animal",
            "having sex with dog",
            "fucking horse",
            "a dog penetrating a woman",
            "oral sex on a dog",
            "an erotic scene between a woman and horse",
            "a woman mating with a horse",
            "kujamiiana na mnyama",
            "kufanya ngono na mnyama",
            "ngono na mnyama",
        ),
        "deceased": (
            "standing beside a corpse",
            "a cadaver on a table",
            "a dead body in the street",
            "the deceased man",
            "the woman is dead",
            "a dead actress on the floor",
            "the deceased model posing",
            "an open casket funeral",
            "in a graveyard at night",
            "a zombie reaching out",
            "an autopsy room",
            "maiti chumbani",
            "kaburi la marehemu",
        ),
    }

    def test_adult_and_benign_prompts_are_allowed(self):
        for prompt in self.ALLOWED:
            with self.subTest(prompt=prompt):
                self.assertIsNone(ImagePromptPolicy.find_violation(prompt))
                self.assertTrue(ImagePromptPolicy.is_allowed(prompt))

    def test_prohibited_prompts_are_blocked_with_the_right_category(self):
        for category, prompts in self.BLOCKED.items():
            for prompt in prompts:
                with self.subTest(category=category, prompt=prompt):
                    self.assertEqual(ImagePromptPolicy.find_violation(prompt), category)

    def test_violation_in_any_argument_is_detected(self):
        """Every field is screened, so a secondary field cannot smuggle a subject."""
        self.assertEqual(
            ImagePromptPolicy.find_violation("a portrait at sunset", None, "", "sex with animal"),
            "bestiality",
        )

    def test_blank_and_none_inputs_are_allowed(self):
        self.assertIsNone(ImagePromptPolicy.find_violation(None, "", "   "))

    def test_assert_allowed_raises_with_category_and_code(self):
        with self.assertRaises(ProhibitedImageSubjectError) as ctx:
            ImagePromptPolicy.assert_allowed("a child on the beach")
        self.assertEqual(ctx.exception.category, "minors")
        self.assertEqual(ctx.exception.code, PROHIBITED_SUBJECT_ERROR_CODE)

    def test_assert_allowed_passes_adult_explicit_text(self):
        ImagePromptPolicy.assert_allowed("a nude adult woman, explicit")

    def test_error_code_is_stable(self):
        self.assertEqual(ImagePromptPolicy.ERROR_CODE, "image_prompt_prohibited_subject")


class ImagePolicyProviderGateTests(SimpleTestCase):
    """
    The provider helpers re-assert the policy immediately before dispatch.

    They raise rather than return ``None`` so a refusal is never mistaken for a
    provider failure that should trigger the Seedream -> Stability fallback and
    retry the same prohibited prompt.
    """

    def test_seedream_helper_refuses_before_any_http_call(self):
        with mock.patch("requests.post") as post:
            with self.assertRaises(ProhibitedImageSubjectError):
                CharacterService._generate_fal_seedream_image("a photo of a toddler")
        post.assert_not_called()

    def test_stability_helper_refuses_before_any_http_call(self):
        with mock.patch("requests.post") as post:
            with self.assertRaises(ProhibitedImageSubjectError):
                CharacterService._generate_ai_image("a photo of a dead man")
        post.assert_not_called()


class ImagePolicyServiceEnforcementTests(TestCase):
    """Gallery and avatar generation refuse prohibited subjects before charging."""

    def setUp(self):
        cache.clear()
        self.user = _make_user(username="polowner", email="polowner@example.com", verified=True)
        self.character = _make_character(name="Neema", slug="neema", created_by=self.user)
        SparkPool.objects.update_or_create(
            user=self.user,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )

    def _assert_refused_without_side_effects(self, result, reserve, post):
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], PROHIBITED_SUBJECT_ERROR_CODE)
        reserve.assert_not_called()
        post.assert_not_called()
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 5000)
        self.assertFalse(BillingReservation.objects.exists())

    def test_gallery_generation_refused(self):
        with mock.patch.object(PaidFeature, "reserve") as reserve, mock.patch("requests.post") as post:
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="sex with animal",
                extra_prompt="sex with animal",
                actor=self.user,
                is_public=False,
            )
        self._assert_refused_without_side_effects(result, reserve, post)

    def test_gallery_negative_prompt_cannot_smuggle_prohibited_subject(self):
        with mock.patch.object(PaidFeature, "reserve") as reserve, mock.patch("requests.post") as post:
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="portrait",
                negative_prompt="sex with animal",
                actor=self.user,
                is_public=False,
            )
        self._assert_refused_without_side_effects(result, reserve, post)

    def test_avatar_generation_refused(self):
        with mock.patch.object(PaidFeature, "reserve") as reserve, mock.patch("requests.post") as post:
            result = CharacterService.generate_avatar(
                character=self.character,
                prompt="beside a corpse",
                extra_prompt="beside a corpse",
                actor=self.user,
            )
        self._assert_refused_without_side_effects(result, reserve, post)

    def test_avatar_negative_prompt_cannot_smuggle_prohibited_subject(self):
        with mock.patch.object(PaidFeature, "reserve") as reserve, mock.patch("requests.post") as post:
            result = CharacterService.generate_avatar(
                character=self.character,
                prompt="portrait",
                negative_prompt="a dead actress",
                actor=self.user,
            )
        self._assert_refused_without_side_effects(result, reserve, post)

    def test_gallery_generation_allows_adult_explicit_prompt(self):
        with mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"generated-image"):
            result = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="a nude explicit portrait",
                extra_prompt="a nude explicit portrait",
                actor=self.user,
                is_public=False,
            )
        self.assertEqual(result["status"], "success")

    def test_image_policy_texts_covers_character_fields_reaching_the_provider(self):
        self.character.visual_style = "sex with animal"
        self.character.save(update_fields=["visual_style"])
        texts = CharacterService.image_policy_texts(self.character)
        self.assertEqual(ImagePromptPolicy.find_violation(*texts), "bestiality")


class CharacterImageGenerationAuthTests(TestCase):
    """The gallery/avatar generation endpoints are authenticated-only.

    The character detail page is a public route, so hiding the button is a UX
    affordance; these tests pin the server-side guarantee that a guest or a
    direct unauthenticated call cannot reach the generator.
    """

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="galowner", email="galowner@example.com", verified=True)
        self.staff = _make_user(username="galstaff", email="galstaff@example.com", verified=True, is_staff=True)
        self.character = _make_character(name="Zawadi", slug="zawadi", created_by=self.owner)
        for user in (self.owner, self.staff):
            SparkPool.objects.update_or_create(
                user=user,
                defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
            )
        self.gallery_url = f"/api/v1/characters/{self.character.character_id}/gallery/generate/"
        self.avatar_url = f"/api/v1/characters/{self.character.character_id}/generate-avatar/"

    def _post(self, url, token=None, session_id=None):
        body = {"prompt": "portrait, high quality, detailed"}
        if session_id:
            body["session_id"] = session_id
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(url, data=body, content_type="application/json", **headers)

    def test_unauthenticated_gallery_generation_rejected(self):
        with mock.patch.object(CharacterService, "generate_gallery_image") as gen:
            resp = self._post(self.gallery_url)
        self.assertIn(resp.status_code, (401, 403))
        gen.assert_not_called()

    def test_unauthenticated_avatar_generation_rejected(self):
        with mock.patch.object(CharacterService, "generate_avatar") as gen:
            resp = self._post(self.avatar_url)
        self.assertIn(resp.status_code, (401, 403))
        gen.assert_not_called()

    def test_guest_session_id_does_not_unlock_generation(self):
        """A guest session id is accepted by the chat endpoints but must not work here."""
        with mock.patch.object(CharacterService, "generate_gallery_image") as gen:
            resp = self._post(self.gallery_url, session_id="guest-session-abc")
        self.assertIn(resp.status_code, (401, 403))
        gen.assert_not_called()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_authenticated_owner_can_generate_gallery_image(self, _recaptcha):
        token = _bearer_token(self.client, "galowner")
        with mock.patch.object(
            CharacterService,
            "generate_gallery_image",
            return_value={"status": "success", "data": {"id": 1}},
        ) as gen:
            resp = self._post(self.gallery_url, token=token)
        self.assertEqual(resp.status_code, 200)
        gen.assert_called_once()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_staff_can_generate_gallery_image(self, _recaptcha):
        token = _bearer_token(self.client, "galstaff")
        with mock.patch.object(
            CharacterService,
            "generate_gallery_image",
            return_value={"status": "success", "data": {"id": 1}},
        ) as gen:
            resp = self._post(self.gallery_url, token=token)
        self.assertEqual(resp.status_code, 200)
        gen.assert_called_once()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_staff_can_generate_avatar(self, _recaptcha):
        token = _bearer_token(self.client, "galstaff")
        with mock.patch.object(
            CharacterService,
            "generate_avatar",
            return_value={"status": "success", "data": {"avatar_url": "/x.png"}},
        ) as gen:
            resp = self._post(self.avatar_url, token=token)
        self.assertEqual(resp.status_code, 200)
        gen.assert_called_once()

    def test_service_layer_refuses_anonymous_actor(self):
        """Defence in depth: the service rejects an anonymous actor on its own."""
        anonymous = AnonymousUser()
        with mock.patch("requests.post") as post:
            gallery = CharacterService.generate_gallery_image(
                character=self.character,
                prompt="portrait",
                actor=anonymous,
            )
            avatar = CharacterService.generate_avatar(
                character=self.character,
                prompt="portrait",
                actor=anonymous,
            )
        self.assertEqual(gallery["data"], "permission_denied")
        self.assertEqual(avatar["data"], "permission_denied")
        post.assert_not_called()

    def test_post_lock_avatar_fast_path_still_checks_owner(self):
        """A stale no-avatar object cannot expose a newly-created private avatar."""
        stale_character = Character.objects.get(pk=self.character.pk)
        self.character.avatar.save("private.webp", ContentFile(b"private-avatar"), save=True)

        result = CharacterService.generate_avatar(
            character=stale_character,
            prompt="portrait",
            actor=_make_user(username="intruder", email="intruder@example.com"),
        )

        self.assertEqual(result, {"status": "fail", "data": "permission_denied"})

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_gallery_rejects_non_object_json_before_provider_call(self, _recaptcha):
        token = _bearer_token(self.client, "galowner")
        with mock.patch.object(CharacterService, "generate_gallery_image") as generate:
            response = self.client.post(
                self.gallery_url,
                data=["portrait"],
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_payload")
        generate.assert_not_called()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_gallery_rejects_invalid_order_before_provider_call(self, _recaptcha):
        token = _bearer_token(self.client, "galowner")
        for order in (-1, 2**31, 1.5, "not-an-order"):
            with self.subTest(order=order), mock.patch.object(CharacterService, "generate_gallery_image") as generate:
                response = self.client.post(
                    self.gallery_url,
                    data={"prompt": "portrait", "order": order},
                    content_type="application/json",
                    HTTP_AUTHORIZATION=f"Bearer {token}",
                )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["data"], "invalid_order")
            generate.assert_not_called()

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_avatar_rejects_float_seed_before_provider_call(self, _recaptcha):
        token = _bearer_token(self.client, "galowner")
        with mock.patch.object(CharacterService, "generate_avatar") as generate:
            response = self.client.post(
                self.avatar_url,
                data={"prompt": "portrait", "seed": 1.5},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_seed")
        generate.assert_not_called()


def _image_upload(name="picture.png", size=(512, 512), content_type="image/png"):
    """A real PNG that clears the 400x400 minimum both validators enforce."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 90, 60)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AdminOnlyImageUploadTests(TestCase):
    """
    Supplying a picture file is an admin-only action. Everyone else gets
    artwork through the AI generators, which are prompt-screened and billed;
    an uploaded file skips both gates, so owning the character is not enough.
    The character edit form is the case that matters here — it accepts an
    ``avatar`` part on a multipart update, which is a second upload route into
    the same ImageField as the gallery endpoint.
    """

    def setUp(self):
        cache.clear()
        self.admin = _make_user(username="imgadmin", email="imgadmin@example.com", verified=True, is_staff=True)
        self.owner = _make_user(username="imgowner", email="imgowner@example.com", verified=True)
        self.character = _make_character(name="Neema", slug="neema", created_by=self.owner)
        # The shared fixture's "casual" tone is not in ALLOWED_TONE_STYLES, so an
        # update would fail validation before reaching the permission check.
        self.character.tone_style = "warm"
        self.character.save(update_fields=["tone_style"])

        # Editing an approved public character costs the owner a re-review fee.
        SparkPool.objects.update_or_create(
            user=self.owner,
            defaults={"sparks": 5000, "token_balance": 0, "is_active": True},
        )

        self.update_url = f"/api/v1/characters/{self.character.character_id}/update/"
        self.gallery_upload_url = f"/api/v1/characters/{self.character.character_id}/gallery/upload/"
        self.avatar_generate_url = f"/api/v1/characters/{self.character.character_id}/generate-avatar/"
        self.gallery_generate_url = f"/api/v1/characters/{self.character.character_id}/gallery/generate/"

    def _patch_avatar(self, token):
        return self.client.patch(
            self.update_url,
            data=encode_multipart(BOUNDARY, {"avatar": _image_upload("avatar.png")}),
            content_type=MULTIPART_CONTENT,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def _post_gallery_image(self, token):
        return self.client.post(
            self.gallery_upload_url,
            data={"image": _image_upload("gallery.png")},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    # ── Character avatar upload ──────────────────────────────────────────────

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_owner_cannot_upload_character_avatar(self, _recaptcha):
        """Owning the character does not grant the right to upload a file for it."""
        token = _bearer_token(self.client, "imgowner")
        resp = self._patch_avatar(token)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["data"], "permission_denied")

        self.character.refresh_from_db()
        self.assertFalse(self.character.avatar)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_upload_character_avatar(self, _recaptcha):
        token = _bearer_token(self.client, "imgadmin")
        resp = self._patch_avatar(token)

        self.assertEqual(resp.status_code, 200)

        self.character.refresh_from_db()
        self.assertTrue(self.character.avatar)
        self.assertIn("characters/avatars/", self.character.avatar.name)

    def test_anonymous_cannot_upload_character_avatar(self):
        resp = self.client.patch(
            self.update_url,
            data=encode_multipart(BOUNDARY, {"avatar": _image_upload("avatar.png")}),
            content_type=MULTIPART_CONTENT,
        )

        self.assertEqual(resp.status_code, 401)

        self.character.refresh_from_db()
        self.assertFalse(self.character.avatar)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_cannot_upload_avatar_while_creating_a_character(self, _recaptcha):
        """The create endpoint takes the same multipart ``avatar`` part."""
        token = _bearer_token(self.client, "imgowner")
        resp = self.client.post(
            "/api/v1/characters/create/",
            data={"name": "Baraka", "avatar": _image_upload("avatar.png")},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["data"], "permission_denied")
        self.assertFalse(Character.objects.filter(name="Baraka").exists())

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_owner_can_still_edit_a_character_without_uploading(self, _recaptcha):
        """The fix gates the file, not the whole edit form."""
        token = _bearer_token(self.client, "imgowner")
        resp = self.client.patch(
            self.update_url,
            data={"mood": "playful"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 200)
        self.character.refresh_from_db()
        self.assertEqual(self.character.mood, "playful")

    # ── Gallery upload ───────────────────────────────────────────────────────

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_cannot_upload_gallery_image(self, _recaptcha):
        token = _bearer_token(self.client, "imgowner")
        resp = self._post_gallery_image(token)

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(CharacterGalleryImage.objects.filter(character=self.character).exists())

    def test_anonymous_cannot_upload_gallery_image(self):
        resp = self.client.post(self.gallery_upload_url, data={"image": _image_upload("gallery.png")})

        self.assertEqual(resp.status_code, 401)
        self.assertFalse(CharacterGalleryImage.objects.filter(character=self.character).exists())

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_admin_can_upload_gallery_image(self, _recaptcha):
        token = _bearer_token(self.client, "imgadmin")
        resp = self._post_gallery_image(token)

        self.assertEqual(resp.status_code, 201)

        image = CharacterGalleryImage.objects.get(character=self.character)
        self.assertEqual(image.created_by, self.admin)
        self.assertEqual(image.source, Character.AvatarSource.UPLOADED)

    # ── Generation stays open to any signed-in user ──────────────────────────
    #
    # Only the caller-supplied file is admin-only. The AI generators are the
    # sanctioned route to artwork for everyone else, so they must keep working
    # for a non-admin; a fix that quietly closed them would break the feature
    # rather than secure it.

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_can_still_generate_a_character_avatar(self, _recaptcha):
        token = _bearer_token(self.client, "imgowner")
        with mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"generated-avatar"
        ), mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"generated-avatar"):
            resp = self.client.post(
                self.avatar_generate_url,
                data={"prompt": "portrait, high quality, detailed"},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(resp.status_code, 200)
        self.character.refresh_from_db()
        self.assertTrue(self.character.avatar)

    @mock.patch("capp.classes.auth.recaptcha.RecaptchaService.verify_token", return_value=RECAPTCHA_OK)
    def test_non_admin_can_still_generate_a_gallery_image(self, _recaptcha):
        token = _bearer_token(self.client, "imgowner")
        with mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"generated-gallery"
        ), mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"generated-gallery"):
            resp = self.client.post(
                self.gallery_generate_url,
                data={"prompt": "portrait, high quality, detailed"},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(CharacterGalleryImage.objects.filter(character=self.character).exists())

    def test_generation_services_accept_a_non_admin_actor(self):
        """The service layer, not just the view, still lets a non-admin generate."""
        with mock.patch.object(
            CharacterService, "_generate_fal_seedream_image", return_value=b"generated"
        ), mock.patch.object(CharacterService, "_generate_ai_image", return_value=b"generated"):
            avatar = CharacterService.generate_avatar(character=self.character, prompt="portrait", actor=self.owner)
            gallery = CharacterService.generate_gallery_image(
                character=self.character, prompt="portrait", actor=self.owner
            )

        self.assertEqual(avatar["status"], "success")
        self.assertEqual(gallery["status"], "success")

    # ── Service layer, independent of the view decorators ────────────────────

    def test_service_layer_refuses_a_non_admin_avatar_upload(self):
        """Defence in depth: the check does not rely on the view decorator."""
        request = mock.Mock(user=self.owner, FILES={"avatar": _image_upload("avatar.png")})

        result = CharacterService.update_character(self.character, {}, request=request)

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["data"], "permission_denied")


class ProactiveChatServiceTests(TestCase):
    """Behavioural coverage for the proactive (AI-initiated) chat pipeline.

    Previously only queue routing was tested. The decision logic, the
    billing-adjacent send path (reserve -> settle/refund), the idempotency
    guard, and the beat task's eligibility filter had no coverage.
    """

    def setUp(self):
        cache.clear()
        # Background sends now load the same operator prompts as the chat path.
        env = mock.patch.dict(
            "os.environ",
            {"GENERAL_SYSTEM_PROMPT": "You are a companion.", "SAFETY_SANDWICH": "Stay safe."},
        )
        env.start()
        self.addCleanup(env.stop)
        self.user = _make_user(username="proactive", email="proactive@example.com", verified=True)
        self.character = _make_character(name="Zuri", slug="zuri")
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.pref = ProactiveChatPreference.objects.create(
            user=self.user, character=self.character, enabled=True, interval_hours=24
        )

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _fund(self, user, sparks):
        pool, _ = SparkPool.objects.get_or_create(
            user=user, defaults={"sparks": 0, "token_balance": 0, "is_active": True}
        )
        pool.sparks = sparks
        pool.save(update_fields=["sparks"])
        return pool

    def _stale_message(self, hours=25):
        """A user message old enough to satisfy the default 24 h interval."""
        return _make_message(
            self.conversation,
            Message.SenderType.USER,
            "hey",
            created_at=timezone.now() - timedelta(hours=hours),
        )

    @staticmethod
    def _llm_ok(content="Habari! Nimekukumbuka."):
        return {"choices": [{"message": {"content": content}}]}

    @staticmethod
    def _operation_id(pref, last_msg):
        return str(uuid.uuid5(PROACTIVE_NAMESPACE, f"{pref.pk}:{last_msg.pk}"))

    # ── _should_send_proactive ───────────────────────────────────────────────

    def test_should_send_requires_prior_conversation(self):
        stranger = _make_character(name="New", slug="new")
        pref = ProactiveChatPreference.objects.create(
            user=self.user, character=stranger, enabled=True, interval_hours=24
        )
        self.assertFalse(ProactiveChatService._should_send_proactive(pref, timezone.now()))

    def test_should_send_false_when_interval_not_elapsed(self):
        _make_message(
            self.conversation,
            Message.SenderType.USER,
            "recent",
            created_at=timezone.now() - timedelta(hours=1),
        )
        self.assertFalse(ProactiveChatService._should_send_proactive(self.pref, timezone.now()))

    def test_should_send_true_after_interval_elapses(self):
        self._stale_message()
        self.assertTrue(ProactiveChatService._should_send_proactive(self.pref, timezone.now()))

    def test_should_send_false_at_consecutive_cap(self):
        self._stale_message()
        self.pref.consecutive_proactive_without_reply = ProactiveChatService.MAX_PROACTIVE_WITHOUT_REPLY
        self.pref.save(update_fields=["consecutive_proactive_without_reply", "updated_at"])
        self.assertFalse(ProactiveChatService._should_send_proactive(self.pref, timezone.now()))

    # ── _send_proactive_message ─────────────────────────────────────────────

    @mock.patch.object(ProactiveChatService, "_send_websocket_notification")
    @mock.patch("capp.classes.chat.proactive_chat_service.unread_service.broadcast_unread")
    @mock.patch("capp.classes.chat.proactive_chat_service.CortecsClient.chat_completion")
    def test_send_persists_settles_and_bumps_counter(self, mock_llm, _unread, _ws):
        mock_llm.return_value = self._llm_ok()
        self._fund(self.user, 2000)
        last = self._stale_message()

        ProactiveChatService._send_proactive_message(self.pref)

        ai_msg = Message.objects.get(conversation=self.conversation, sender_type=Message.SenderType.AI)
        self.assertTrue(ai_msg.metadata["proactive"])
        self.assertEqual(ai_msg.client_operation_id, self._operation_id(self.pref, last))

        # The trusted envelope wraps the task prompt: platform rules first,
        # safety sandwich as the last system turn, task prompt as the final user turn.
        sent = mock_llm.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "system", "user"])
        self.assertIn("=== PLATFORM RULES (System) ===\nYou are a companion.", sent[0]["content"])
        self.assertIn("NSFW content is DISABLED", sent[0]["content"])
        self.assertEqual(sent[1]["content"], "=== SAFETY CHECK (System) ===\nStay safe.")

        reservation = BillingReservation.objects.get(user=self.user)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000 - MESSAGE_PAIR_COST)

        self.pref.refresh_from_db()
        self.assertEqual(self.pref.consecutive_proactive_without_reply, 1)
        self.assertIsNotNone(self.pref.last_proactive_sent_at)

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.last_message_at, ai_msg.created_at)

    def test_send_is_idempotent_per_operation(self):
        """A redelivered beat task in the same window must not pay or send twice."""
        self._fund(self.user, 2000)
        last = self._stale_message()
        Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.AI,
            content="already sent",
            status=Message.StatusChoices.SENT,
            client_operation_id=self._operation_id(self.pref, last),
            metadata={"proactive": True},
        )

        with mock.patch("capp.classes.chat.proactive_chat_service.CortecsClient.chat_completion") as mock_llm:
            ProactiveChatService._send_proactive_message(self.pref)

        mock_llm.assert_not_called()
        # Still just the original user message + the one proactive AI message.
        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), 2)
        self.assertFalse(BillingReservation.objects.filter(user=self.user).exists())
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000)

    @mock.patch.object(ProactiveChatService, "_send_websocket_notification")
    @mock.patch("capp.classes.chat.proactive_chat_service.unread_service.broadcast_unread")
    @mock.patch("capp.classes.chat.proactive_chat_service.CortecsClient.chat_completion")
    def test_redelivery_after_successful_send_does_not_resend(self, mock_llm, _unread, _ws):
        """A redelivered task after a completed send must derive the same operation ID.

        Regression: the operation ID was anchored to the newest message, which
        after a send is the proactive message itself — a redelivery derived a
        fresh ID and paid for a second uninvited message.
        """
        mock_llm.return_value = self._llm_ok()
        self._fund(self.user, 2000)
        self._stale_message()

        ProactiveChatService._send_proactive_message(self.pref)
        ProactiveChatService._send_proactive_message(self.pref)  # redelivery

        self.assertEqual(mock_llm.call_count, 1)
        self.assertEqual(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI).count(), 1
        )
        self.assertEqual(BillingReservation.objects.filter(user=self.user).count(), 1)
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000 - MESSAGE_PAIR_COST)

    @mock.patch.object(ProactiveChatService, "_send_websocket_notification")
    @mock.patch("capp.classes.chat.proactive_chat_service.unread_service.broadcast_unread")
    @mock.patch("capp.classes.chat.proactive_chat_service.CortecsClient.chat_completion")
    def test_send_refunds_when_provider_returns_nothing(self, mock_llm, _unread, _ws):
        mock_llm.return_value = {"choices": []}
        self._fund(self.user, 2000)
        self._stale_message()

        ProactiveChatService._send_proactive_message(self.pref)

        self.assertFalse(
            Message.objects.filter(conversation=self.conversation, sender_type=Message.SenderType.AI).exists()
        )
        reservation = BillingReservation.objects.get(user=self.user)
        self.assertEqual(reservation.status, BillingReservation.Status.REFUNDED)
        # Reserve debited, refund credited: the balance ends where it started.
        self.assertEqual(SparkPool.objects.get(user=self.user).sparks, 2000)

    @mock.patch("capp.classes.chat.proactive_chat_service.CortecsClient.chat_completion")
    def test_send_skips_on_insufficient_balance(self, mock_llm):
        self._fund(self.user, MESSAGE_PAIR_COST - 1)
        self._stale_message()

        ProactiveChatService._send_proactive_message(self.pref)

        mock_llm.assert_not_called()
        self.assertFalse(BillingReservation.objects.filter(user=self.user).exists())
        self.assertEqual(Message.objects.filter(conversation=self.conversation).count(), 1)

    # ── Beat task eligibility filter ─────────────────────────────────────────

    @mock.patch.object(ProactiveChatService, "_send_websocket_notification")
    @mock.patch("capp.classes.chat.proactive_chat_service.unread_service.broadcast_unread")
    @mock.patch("capp.classes.chat.proactive_chat_service.CortecsClient.chat_completion")
    def test_beat_task_skips_ineligible_preferences(self, mock_llm, _unread, _ws):
        mock_llm.return_value = self._llm_ok()
        self._fund(self.user, 2000)
        self._stale_message()  # eligible: active user + active character

        banned = _make_user(username="banned", email="banned@example.com", verified=True)
        banned.is_active = False
        banned.account_status = CustomUser.AccountStatus.BANNED
        banned.save(update_fields=["is_active", "account_status", "updated_at"])

        deleted = _make_user(username="deleted", email="deleted@example.com", verified=True)
        deleted.soft_delete()

        inactive_char = _make_character(name="Pending", slug="pending")
        inactive_char.is_active = False
        inactive_char.save(update_fields=["is_active", "updated_at"])

        for user, character in (
            (banned, self.character),
            (deleted, self.character),
            (self.user, inactive_char),
        ):
            conv = Conversation.objects.create(user=user, character=character)
            _make_message(
                conv,
                Message.SenderType.USER,
                "hey",
                created_at=timezone.now() - timedelta(hours=25),
            )
            ProactiveChatPreference.objects.create(user=user, character=character, enabled=True, interval_hours=24)
            self._fund(user, 2000)

        # Disabled preference on an otherwise eligible pair.
        disabled_char = _make_character(name="Off", slug="off")
        conv = Conversation.objects.create(user=self.user, character=disabled_char)
        _make_message(
            conv,
            Message.SenderType.USER,
            "hey",
            created_at=timezone.now() - timedelta(hours=25),
        )
        ProactiveChatPreference.objects.create(
            user=self.user, character=disabled_char, enabled=False, interval_hours=24
        )

        result = check_and_send_proactive_messages()

        self.assertEqual(result["sent_count"], 1)
        # Exactly one AI message across every conversation: the eligible pair's.
        self.assertEqual(Message.objects.filter(sender_type=Message.SenderType.AI).count(), 1)
        self.assertFalse(
            Message.objects.filter(conversation__user__in=[banned, deleted], sender_type=Message.SenderType.AI).exists()
        )

    # ── reset_counter ────────────────────────────────────────────────────────

    def test_reset_counter_clears_consecutive_count(self):
        self.pref.consecutive_proactive_without_reply = 3
        self.pref.save(update_fields=["consecutive_proactive_without_reply", "updated_at"])

        ProactiveChatService.reset_counter(self.user, self.character)

        self.pref.refresh_from_db()
        self.assertEqual(self.pref.consecutive_proactive_without_reply, 0)
