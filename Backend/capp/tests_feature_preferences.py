# capp/tests_feature_preferences.py
"""
Tests for the per-account optional-feature toggles
(Settings → Advanced → feature preferences).

Covers:
- GET defaults availability on, except the opt-in memory editor.
- PATCH partially updates and persists.
- Validation: unknown keys and non-boolean values are rejected.
- Unauthenticated requests are rejected.
- _serialize_user exposes the effective preferences.
- Enforcement: the memory editor API 403s until the user enables the feature.
- FeaturePreferences fails closed for guests (None) and unknown keys.
"""

from django.test import TestCase
from rest_framework_simplejwt.tokens import RefreshToken

from capp.classes.auth.auth import Auth
from capp.classes.auth.feature_preferences import FeaturePreferences
from capp.models import Conversation, CustomUser
from capp.tests import _make_character, _make_user


class FeaturePreferencesApiTests(TestCase):
    def setUp(self):
        # _make_user enables every feature by default (existing suites rely on
        # that); these tests explicitly reset to unset preferences.
        self.user = _make_user(username="carol", email="carol@example.com")
        self.user.feature_preferences = {}
        self.user.save(update_fields=["feature_preferences"])

    def _token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    def _auth(self, user):
        return {"HTTP_AUTHORIZATION": f"Bearer {self._token(user)}"}

    def test_get_returns_availability_defaults_without_persisting_choices(self):
        resp = self.client.get("/api/v1/auth/feature-preferences/", **self._auth(self.user))
        self.assertEqual(resp.status_code, 200)
        prefs = resp.json()["data"]["feature_preferences"]
        self.assertEqual(
            prefs,
            {
                "reminders": True,
                "deepthink": True,
                "websearch": True,
                "memory_editor": False,
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.feature_preferences, {})

    def test_patch_persists_partial_update(self):
        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='{"reminders": true, "websearch": true}',
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(resp.status_code, 200)
        prefs = resp.json()["data"]["feature_preferences"]
        self.assertTrue(prefs["reminders"])
        self.assertTrue(prefs["websearch"])
        self.assertTrue(prefs["deepthink"])
        self.assertFalse(prefs["memory_editor"])

        # Persists across requests.
        resp = self.client.get("/api/v1/auth/feature-preferences/", **self._auth(self.user))
        prefs = resp.json()["data"]["feature_preferences"]
        self.assertTrue(prefs["reminders"])
        self.assertTrue(prefs["websearch"])

        # Turning one back off leaves the other untouched.
        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='{"websearch": false}',
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(resp.status_code, 200)
        prefs = resp.json()["data"]["feature_preferences"]
        self.assertTrue(prefs["reminders"])
        self.assertFalse(prefs["websearch"])

    def test_patch_rejects_unknown_keys(self):
        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='{"reminders": true, "telepathy": true}',
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("telepathy", str(resp.json()["data"]))

    def test_patch_rejects_non_boolean_values(self):
        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='{"reminders": "yes"}',
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(resp.status_code, 400)

    def test_patch_rejects_non_object_payload(self):
        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='[{"reminders": true}]',
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_requests_rejected(self):
        resp = self.client.get("/api/v1/auth/feature-preferences/")
        self.assertIn(resp.status_code, (401, 403))

        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='{"reminders": true}',
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_users_do_not_share_preferences(self):
        other = _make_user(username="dave", email="dave@example.com")
        other.feature_preferences = {"reminders": True}
        other.save(update_fields=["feature_preferences"])

        resp = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data='{"deepthink": true}',
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(resp.status_code, 200)

        other.refresh_from_db()
        self.assertEqual(other.feature_preferences.get("deepthink"), None)
        self.assertTrue(other.feature_preferences.get("reminders"))

    def test_serialize_user_exposes_preferences(self):
        self.user.feature_preferences = {"reminders": True}
        self.user.save(update_fields=["feature_preferences"])
        serialized = Auth._serialize_user(self.user)
        self.assertEqual(
            serialized["feature_preferences"],
            {
                "reminders": True,
                "deepthink": True,
                "websearch": True,
                "memory_editor": False,
            },
        )

    def test_saved_choices_are_preserved_when_missing_keys_use_defaults(self):
        self.user.feature_preferences = {"websearch": False, "reminders": False, "memory_editor": True}
        self.user.save(update_fields=["feature_preferences"])
        response = self.client.get("/api/v1/auth/feature-preferences/", **self._auth(self.user))
        self.assertEqual(
            response.json()["data"]["feature_preferences"],
            {"websearch": False, "reminders": False, "deepthink": True, "memory_editor": True},
        )
        response = self.client.patch(
            "/api/v1/auth/feature-preferences/",
            data={"deepthink": False},
            content_type="application/json",
            **self._auth(self.user),
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.feature_preferences,
            {"websearch": False, "reminders": False, "deepthink": False, "memory_editor": True},
        )

    def test_default_availability_does_not_activate_per_chat_modes(self):
        character = _make_character(name="Amani", slug="availability-defaults")
        conversation = Conversation.objects.create(user=self.user, character=character)
        self.assertTrue(FeaturePreferences.is_enabled(self.user, "websearch"))
        self.assertTrue(FeaturePreferences.is_enabled(self.user, "deepthink"))
        self.assertFalse(conversation.web_search_enabled)
        self.assertEqual(conversation.reasoning_effort, "fast")
        response = self.client.get(
            f"/api/v1/conversations/{conversation.conversation_id}/settings/", **self._auth(self.user)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"web_search_enabled": False, "reasoning_effort": "fast"})


class FeaturePreferencesEnforcementTests(TestCase):
    """The memory editor API must fail closed until the feature is enabled."""

    def setUp(self):
        self.user = _make_user(username="erica", email="erica@example.com")
        self.user.feature_preferences = {}
        self.user.save(update_fields=["feature_preferences"])
        self.character = _make_character(name="Amina", slug="amina-feature-prefs")

    def _token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    def _auth(self, user):
        return {"HTTP_AUTHORIZATION": f"Bearer {self._token(user)}"}

    def test_memory_editor_blocked_until_enabled(self):
        url = f"/api/v1/memory/editor/{self.character.character_id}/"
        resp = self.client.get(url, **self._auth(self.user))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["data"], "feature_disabled")

        # Enable → list works (empty memory is a 200 with empty sections).
        self.user.feature_preferences = {"memory_editor": True}
        self.user.save(update_fields=["feature_preferences"])
        resp = self.client.get(url, **self._auth(self.user))
        self.assertEqual(resp.status_code, 200)


class FeaturePreferencesHelperTests(TestCase):
    def test_guest_and_none_user_fail_closed(self):
        prefs = FeaturePreferences.get(None)
        self.assertEqual(
            prefs,
            {
                "reminders": False,
                "deepthink": False,
                "websearch": False,
                "memory_editor": False,
            },
        )
        self.assertFalse(FeaturePreferences.is_enabled(None, "websearch"))

    def test_unknown_key_is_never_enabled(self):
        user = _make_user(username="frank", email="frank@example.com")
        user.feature_preferences = {"telepathy": True}
        user.save(update_fields=["feature_preferences"])
        self.assertFalse(FeaturePreferences.is_enabled(user, "telepathy"))

    def test_malformed_stored_value_fails_closed(self):
        user = _make_user(username="grace", email="grace@example.com")
        CustomUser.objects.filter(user_id=user.user_id).update(feature_preferences=["not", "a", "dict"])
        user.refresh_from_db()
        prefs = FeaturePreferences.get(user)
        self.assertFalse(any(prefs.values()))

    def test_non_boolean_saved_values_fail_closed_instead_of_enabling_features(self):
        user = _make_user(username="invalidflags", email="invalidflags@example.com")
        for value in (None, "true", "false", 1, 0, [], {}):
            with self.subTest(value=value):
                user.feature_preferences = {key: value for key in FeaturePreferences.FEATURE_KEYS}
                self.assertFalse(any(FeaturePreferences.get(user).values()))
