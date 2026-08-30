# capp/tests_push.py
"""
Web Push subscriptions and delivery.

The properties under test:

- Subscriptions are strictly per-user. A token only ever touches its own rows;
  one account must never detach another account's device.
- The endpoint is the identity of a device. Re-subscribing upserts instead of
  duplicating, and on a shared device the endpoint migrates to the most
  recently subscribed account.
- Delivery is best-effort and content-free: push services see who messaged,
  never what was said, and a dead endpoint (404/410) is pruned on sight
  instead of retried forever.
- Unset VAPID keys disable the feature cleanly rather than crashing — CI and
  fresh dev checkouts have no keys.
"""

import json
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from celery.exceptions import SoftTimeLimitExceeded
from rest_framework_simplejwt.tokens import AccessToken

from capp.classes.push.push_service import PushService
from capp.models import Conversation, Message, PushSubscription
from capp.tests import _make_character, _make_user

KEY_URL = "/api/v1/push/vapid-public-key/"
SUBSCRIBE_URL = "/api/v1/push/subscribe/"
UNSUBSCRIBE_URL = "/api/v1/push/unsubscribe/"

VAPID_SETTINGS = {
    "WEBPUSH_VAPID_PUBLIC_KEY": "BPublicTestKey",
    "WEBPUSH_VAPID_PRIVATE_KEY": "private-test-key",
    "WEBPUSH_VAPID_SUBJECT": "mailto:test@example.com",
    "WEBPUSH_ALLOWED_ORIGINS": ("https://push.example.com",),
    "WEBPUSH_TIMEOUT_SECONDS": 5,
    "WEBPUSH_MAX_SUBSCRIPTIONS_PER_USER": 10,
}

NO_VAPID_SETTINGS = {
    "WEBPUSH_VAPID_PUBLIC_KEY": "",
    "WEBPUSH_VAPID_PRIVATE_KEY": "",
}


def bearer(user):
    """A JWT for ``user``, skipping the sign-in flow and its reCAPTCHA."""
    return str(AccessToken.for_user(user))


def payload(endpoint="https://push.example.com/some/endpoint"):
    return {
        "endpoint": endpoint,
        "keys": {"p256dh": "p256dh-key", "auth": "auth-secret"},
    }


class VapidPublicKeyTests(TestCase):
    @override_settings(**VAPID_SETTINGS)
    def test_returns_key_when_configured(self):
        response = self.client.get(KEY_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["public_key"], "BPublicTestKey")

    @override_settings(**NO_VAPID_SETTINGS)
    def test_503_when_not_configured(self):
        response = self.client.get(KEY_URL)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["data"], "push_not_configured")


@override_settings(**VAPID_SETTINGS)
class SubscribeTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {bearer(self.user)}"}

    def post(self, data):
        return self.client.post(SUBSCRIBE_URL, data=json.dumps(data), content_type="application/json", **self.auth)

    def test_requires_authentication(self):
        response = self.client.post(SUBSCRIBE_URL, data=json.dumps(payload()), content_type="application/json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_creates_subscription(self):
        response = self.post(payload())

        self.assertEqual(response.status_code, 200)
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.user, self.user)
        self.assertEqual(subscription.p256dh, "p256dh-key")
        self.assertEqual(subscription.auth, "auth-secret")

    def test_resubscribe_upserts_instead_of_duplicating(self):
        self.post(payload())
        refreshed = payload()
        refreshed["keys"] = {"p256dh": "rotated-p256dh", "auth": "rotated-auth"}
        response = self.post(refreshed)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().p256dh, "rotated-p256dh")

    def test_shared_device_endpoint_migrates_to_latest_account(self):
        other = _make_user(username="bob", email="bob@example.com")
        self.post(payload())

        response = self.client.post(
            SUBSCRIBE_URL,
            data=json.dumps(payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {bearer(other)}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().user, other)

    def test_shared_endpoint_with_different_credentials_is_rejected(self):
        other = _make_user(username="bob", email="bob@example.com")
        self.post(payload())
        takeover = payload()
        takeover["keys"] = {"p256dh": "attacker-key", "auth": "attacker-secret"}

        response = self.client.post(
            SUBSCRIBE_URL,
            data=json.dumps(takeover),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {bearer(other)}",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["data"], "endpoint_already_registered")
        self.assertEqual(PushSubscription.objects.get().user, self.user)

    def test_rejects_non_https_endpoint(self):
        response = self.post(payload(endpoint="http://push.example.com/insecure"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_endpoint")

    def test_rejects_unapproved_https_origin(self):
        response = self.post(payload(endpoint="https://attacker.example/internal-proxy"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_endpoint")

    def test_rejects_approved_hostname_with_attacker_suffix(self):
        response = self.post(payload(endpoint="https://push.example.com.attacker.example/x"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_endpoint")

    def test_rejects_missing_keys(self):
        response = self.post({"endpoint": "https://push.example.com/x"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_keys")

    def test_rejects_oversized_key(self):
        data = payload()
        data["keys"]["auth"] = "a" * 256
        response = self.post(data)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_keys")


@override_settings(**VAPID_SETTINGS)
class UnsubscribeTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {bearer(self.user)}"}

    def delete(self, data, **extra):
        return self.client.delete(
            UNSUBSCRIBE_URL, data=json.dumps(data), content_type="application/json", **{**self.auth, **extra}
        )

    def test_deletes_own_subscription(self):
        PushService.save_subscription(
            self.user, endpoint=payload()["endpoint"], p256dh="p256dh-key", auth="auth-secret"
        )

        response = self.delete({"endpoint": payload()["endpoint"]})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["unsubscribed"])
        self.assertEqual(PushSubscription.objects.count(), 0)

    def test_cannot_delete_another_users_subscription(self):
        other = _make_user(username="bob", email="bob@example.com")
        PushService.save_subscription(other, endpoint=payload()["endpoint"], p256dh="p256dh-key", auth="auth-secret")

        response = self.delete({"endpoint": payload()["endpoint"]})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["unsubscribed"])
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_requires_authentication(self):
        response = self.client.delete(
            UNSUBSCRIBE_URL,
            data=json.dumps({"endpoint": "https://push.example.com/x"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)


class NewMessagePushTests(TestCase):
    """Payload shape for both proactive messages and closed-app replies."""

    def setUp(self):
        self.user = _make_user()
        self.character = SimpleNamespace(name="Amina")
        self.conversation = SimpleNamespace(conversation_id="conv-1")

    @mock.patch.object(PushService, "send_to_user")
    def test_english_body_and_conversation_url(self, mock_send):
        PushService.send_new_message_push(self.user, self.character, self.conversation)

        mock_send.assert_called_once_with(
            self.user,
            title="Amina",
            body="sent you a message",
            url="/conversations/conv-1",
            unread_count=0,
        )

    @mock.patch.object(PushService, "send_to_user")
    def test_unread_count_is_the_owner_wide_total(self, mock_send):
        character = _make_character()
        conversation = Conversation.objects.create(user=self.user, character=character)
        Message.objects.create(conversation=conversation, sender_type=Message.SenderType.AI, content="hi")
        Message.objects.create(conversation=conversation, sender_type=Message.SenderType.AI, content="again")
        # The user's own messages are read by definition and must not count.
        Message.objects.create(conversation=conversation, sender_type=Message.SenderType.USER, content="me")

        PushService.send_new_message_push(self.user, character, conversation)

        self.assertEqual(mock_send.call_args.kwargs["unread_count"], 2)

    @mock.patch.object(PushService, "send_to_user")
    def test_body_follows_preferred_language(self, mock_send):
        self.user.preferred_language = "sw"

        PushService.send_new_message_push(self.user, self.character, self.conversation)

        self.assertEqual(mock_send.call_args.kwargs["body"], "amekutumia ujumbe")

    @mock.patch.object(PushService, "send_to_user")
    def test_unlisted_language_falls_back_to_english(self, mock_send):
        self.user.preferred_language = "sh"

        PushService.send_new_message_push(self.user, self.character, self.conversation)

        self.assertEqual(mock_send.call_args.kwargs["body"], "sent you a message")


@override_settings(**VAPID_SETTINGS)
class SendTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.subscription = PushService.save_subscription(
            self.user, endpoint="https://push.example.com/some/endpoint", p256dh="k", auth="a"
        )

    @mock.patch("capp.classes.push.push_service.webpush")
    def test_sends_metadata_only_payload(self, mock_webpush):
        attempted = PushService.send_to_user(
            self.user, title="Amina", body="sent you a message", url="/conversations/1"
        )

        self.assertEqual(attempted, 1)
        sent_payload = json.loads(mock_webpush.call_args.kwargs["data"])
        self.assertEqual(sent_payload, {"title": "Amina", "body": "sent you a message", "url": "/conversations/1"})
        self.assertEqual(mock_webpush.call_args.kwargs["ttl"], 24 * 60 * 60)
        self.assertEqual(mock_webpush.call_args.kwargs["timeout"], 5)
        self.assertIsNotNone(mock_webpush.call_args.kwargs["requests_session"])

    @mock.patch("capp.classes.push.push_service.webpush")
    def test_payload_carries_unread_count_when_given(self, mock_webpush):
        PushService.send_to_user(
            self.user, title="Amina", body="sent you a message", url="/conversations/1", unread_count=3
        )

        sent_payload = json.loads(mock_webpush.call_args.kwargs["data"])
        self.assertEqual(sent_payload["unread_count"], 3)

    @mock.patch("capp.classes.push.push_service.webpush")
    def test_prunes_gone_subscriptions(self, mock_webpush):
        from pywebpush import WebPushException

        response = mock.Mock(status_code=410)
        mock_webpush.side_effect = WebPushException("gone", response=response)

        PushService.send_to_user(self.user, title="Amina", body="sent you a message")

        self.assertEqual(PushSubscription.objects.count(), 0)

    @mock.patch("capp.classes.push.push_service.webpush")
    def test_delivery_failure_never_raises(self, mock_webpush):
        mock_webpush.side_effect = ConnectionError("push service down")

        attempted = PushService.send_to_user(self.user, title="Amina", body="sent you a message")

        self.assertEqual(attempted, 0)
        self.assertEqual(PushSubscription.objects.count(), 1)

    @mock.patch("capp.classes.push.push_service.webpush")
    def test_soft_time_limit_is_never_swallowed(self, mock_webpush):
        mock_webpush.side_effect = SoftTimeLimitExceeded()

        with self.assertRaises(SoftTimeLimitExceeded):
            PushService.send_to_user(self.user, title="Amina", body="sent you a message")

    @override_settings(WEBPUSH_MAX_SUBSCRIPTIONS_PER_USER=2)
    def test_subscription_count_is_bounded(self):
        for index in range(3):
            PushService.save_subscription(
                self.user,
                endpoint=f"https://push.example.com/device/{index}",
                p256dh=f"k-{index}",
                auth=f"a-{index}",
            )

        self.assertEqual(PushSubscription.objects.filter(user=self.user).count(), 2)

    @override_settings(**NO_VAPID_SETTINGS)
    @mock.patch("capp.classes.push.push_service.webpush")
    def test_unconfigured_is_a_noop(self, mock_webpush):
        attempted = PushService.send_to_user(self.user, title="Amina", body="sent you a message")

        self.assertEqual(attempted, 0)
        mock_webpush.assert_not_called()
