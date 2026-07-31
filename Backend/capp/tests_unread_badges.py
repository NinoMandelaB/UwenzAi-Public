# capp/tests_unread_badges.py
"""
Unread badges for the chat list and the messages nav tab.

The property under test is that "unread" is always derived from the read
watermark and never from a stored counter: a counter drifts the moment a
message is deleted, a conversation is reset, or a push is dropped, and a badge
that cannot be cleared is worse than no badge at all.

Guests get the same treatment as accounts throughout. A guest conversation is
owned by ``guest_session_id`` rather than ``user``, and every ownership check
here exists because forgetting one branch is how the guest chat regressed
before.
"""

import json
import uuid
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from capp.classes.chat import unread_service
from capp.classes.guest.guest_identity_service import hash_credential
from capp.consumers import SparkPoolConsumer
from capp.models import Conversation, GuestIdentity, Message
from capp.tests import _make_character, _make_user
from capp.tests_guest_identity import GuestTestMixin

LIST_URL = "/api/v1/conversations/"
UNREAD_URL = "/api/v1/conversations/unread/"

IN_MEMORY_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def bearer(user):
    """A JWT for ``user``, skipping the sign-in flow and its reCAPTCHA."""
    return str(AccessToken.for_user(user))


def read_url(conversation_id):
    return f"/api/v1/conversations/{conversation_id}/read/"


def _ai_message(conversation, content="hello", **kwargs):
    return Message.objects.create(
        conversation=conversation,
        sender_type=Message.SenderType.AI,
        content=content,
        **kwargs,
    )


class UnreadCountTests(TestCase):
    """Per-conversation counts and the aggregate, for an authenticated owner."""

    def setUp(self):
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.token = bearer(self.user)

    def auth_get(self, url):
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def auth_post(self, url):
        return self.client.post(
            url, data={}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )

    def listed(self):
        """``{conversation_id: unread_count}`` as the chat list reports it."""
        response = self.auth_get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        return {row["conversation_id"]: row["unread_count"] for row in response.json()["data"]["conversations"]}

    def test_an_ai_reply_makes_the_conversation_unread(self):
        _ai_message(self.conversation)

        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 1)

    def test_the_users_own_messages_are_never_unread(self):
        Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="hi",
        )
        Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.SYSTEM,
            content="out of sparks",
        )

        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 0)

    def test_marking_read_clears_the_badge(self):
        _ai_message(self.conversation)
        _ai_message(self.conversation, content="still there?")

        response = self.auth_post(read_url(self.conversation.conversation_id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["unread_count"], 0)
        self.assertEqual(response.json()["data"]["total_unread"], 0)
        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 0)

    def test_a_reply_arriving_after_a_read_is_unread_again(self):
        _ai_message(self.conversation)
        self.auth_post(read_url(self.conversation.conversation_id))

        _ai_message(self.conversation, content="are you there?")

        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 1)

    def test_counts_are_per_conversation_and_sum_to_the_nav_total(self):
        other = Conversation.objects.create(
            user=self.user,
            character=_make_character(name="Zuri", slug="zuri"),
        )
        _ai_message(self.conversation)
        _ai_message(self.conversation, content="second")
        _ai_message(other, content="from another chat")

        listed = self.listed()
        self.assertEqual(listed[str(self.conversation.conversation_id)], 2)
        self.assertEqual(listed[str(other.conversation_id)], 1)

        summary = self.auth_get(UNREAD_URL).json()["data"]
        self.assertEqual(summary["total_unread"], 3)
        self.assertEqual(
            {row["conversation_id"]: row["unread_count"] for row in summary["conversations"]},
            {str(self.conversation.conversation_id): 2, str(other.conversation_id): 1},
        )

    def test_reading_one_conversation_leaves_the_others_unread(self):
        other = Conversation.objects.create(
            user=self.user,
            character=_make_character(name="Zuri", slug="zuri"),
        )
        _ai_message(self.conversation)
        _ai_message(other, content="from another chat")

        self.auth_post(read_url(self.conversation.conversation_id))

        self.assertEqual(self.auth_get(UNREAD_URL).json()["data"]["total_unread"], 1)

    def test_a_conversation_read_after_its_last_reply_reports_nothing(self):
        _ai_message(self.conversation)
        self.conversation.last_read_at = timezone.now() + timedelta(minutes=1)
        self.conversation.save(update_fields=["last_read_at"])

        self.assertEqual(unread_service.unread_count_for(self.conversation), 0)

    def test_another_accounts_conversation_cannot_be_marked_read(self):
        intruder = _make_user(username="mallory", email="mallory@example.com")
        token = bearer(intruder)

        response = self.client.post(
            read_url(self.conversation.conversation_id),
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 403)
        self.conversation.refresh_from_db()

    def test_marking_an_unknown_conversation_read_is_a_404(self):
        response = self.auth_post(read_url(uuid.uuid4()))

        self.assertEqual(response.status_code, 404)

    def test_a_conversation_starts_read(self):
        """
        A freshly created chat must not arrive pre-badged. The same default is
        what the migration writes onto existing rows, so deploying this does
        not light up every conversation in the product at once.
        """
        self.assertEqual(unread_service.unread_count_for(self.conversation), 0)


class GuestUnreadTests(GuestTestMixin, TestCase):
    """The guest half of the same contract, keyed by the guest's owner key."""

    def setUp(self):
        super().setUp()
        self.character = _make_character()
        self.owner_key = self.owner_key_for(self.ensure_session())
        self.conversation = Conversation.objects.create(
            guest_session_id=self.owner_key,
            character=self.character,
        )

    def owner_key_for(self, raw_credential):
        """The stable ownership key behind a guest's credential cookie."""
        return GuestIdentity.objects.get(credential_hash=hash_credential(raw_credential)).owner_key

    def listed(self):
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        return {row["conversation_id"]: row["unread_count"] for row in response.json()["data"]["conversations"]}

    def test_a_guest_sees_its_own_unread_count(self):
        _ai_message(self.conversation)

        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 1)
        self.assertEqual(self.client.get(UNREAD_URL).json()["data"]["total_unread"], 1)

    def test_a_guest_can_mark_a_conversation_read(self):
        _ai_message(self.conversation)

        response = self.client.post(
            read_url(self.conversation.conversation_id), data={}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["unread_count"], 0)
        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 0)

    def test_a_guest_cannot_mark_another_guests_conversation_read(self):
        _ai_message(self.conversation)
        other_client = Client()
        self.ensure_session(other_client)

        response = other_client.post(
            read_url(self.conversation.conversation_id),
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.listed()[str(self.conversation.conversation_id)], 1)

    def test_a_guest_total_excludes_other_guests(self):
        other_client = Client()
        stranger = Conversation.objects.create(
            guest_session_id=self.owner_key_for(self.ensure_session(other_client)),
            character=_make_character(name="Zuri", slug="zuri"),
        )
        _ai_message(stranger, content="not yours")
        _ai_message(stranger, content="also not yours")
        _ai_message(self.conversation)

        self.assertEqual(self.client.get(UNREAD_URL).json()["data"]["total_unread"], 1)

    def test_the_unread_summary_needs_a_guest_session(self):
        response = Client().get(UNREAD_URL)

        self.assertEqual(response.status_code, 401)


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class UnreadBroadcastTests(TestCase):
    """
    The live push. Badges live on the chat list and the nav tab, neither of
    which holds a per-conversation socket, so the update has to travel on the
    owner-wide channel instead.
    """

    def setUp(self):
        self.user = _make_user()
        self.character = _make_character()
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.layer = get_channel_layer()

    def subscribe(self, group):
        channel = f"test-{uuid.uuid4()}"
        async_to_sync(self.layer.group_add)(group, channel)
        return channel

    def receive(self, channel):
        return async_to_sync(self.layer.receive)(channel)

    def test_a_new_ai_message_pushes_the_count_to_the_owner(self):
        channel = self.subscribe(f"spark_pool_{self.user.user_id}")
        _ai_message(self.conversation)

        unread_service.broadcast_unread(self.conversation)

        event = self.receive(channel)
        self.assertEqual(event["type"], "unread.update")
        self.assertEqual(event["conversation_id"], str(self.conversation.conversation_id))
        self.assertEqual(event["unread_count"], 1)
        self.assertEqual(event["total_unread"], 1)

    def test_a_guests_push_goes_to_the_guest_group(self):
        guest_conversation = Conversation.objects.create(
            guest_session_id="guest-owner-key",
            character=self.character,
        )
        channel = self.subscribe("guest_spark_pool_guest-owner-key")
        _ai_message(guest_conversation)

        unread_service.broadcast_unread(guest_conversation)

        event = self.receive(channel)
        self.assertEqual(event["unread_count"], 1)

    def test_marking_read_pushes_a_zero(self):
        _ai_message(self.conversation)
        channel = self.subscribe(f"spark_pool_{self.user.user_id}")
        token = bearer(self.user)

        self.client.post(
            read_url(self.conversation.conversation_id),
            data={},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        event = self.receive(channel)
        self.assertEqual(event["unread_count"], 0)
        self.assertEqual(event["total_unread"], 0)

    def test_the_task_pushes_an_update_alongside_the_replayed_reply(self):
        """
        The replay branch resends a reply the client may never have received.
        The badge has to be resent with it, or a dropped push leaves the chat
        list permanently disagreeing with the message history.
        """
        from capp.classes.billing.idempotency import result_key
        from capp.tasks import process_ai_response

        operation_id = str(uuid.uuid4())
        user_message = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content="hi",
            client_operation_id=operation_id,
        )
        _ai_message(self.conversation, content="hey", client_operation_id=result_key(operation_id))
        channel = self.subscribe(f"spark_pool_{self.user.user_id}")

        process_ai_response(self.conversation.conversation_id, user_message.message_id)

        event = self.receive(channel)
        self.assertEqual(event["type"], "unread.update")
        self.assertEqual(event["unread_count"], 1)

    def test_an_ownerless_conversation_is_not_broadcast(self):
        orphan = Conversation.objects.create(character=self.character)

        unread_service.broadcast_unread(orphan)  # must not raise

    def test_the_consumer_puts_the_update_on_the_wire(self):
        """The event name and field names the frontend switches on."""
        consumer = SparkPoolConsumer()
        frames = []

        async def capture(text_data=None, **kwargs):
            frames.append(json.loads(text_data))

        consumer.send = capture
        async_to_sync(consumer.unread_update)(
            {
                "type": "unread.update",
                "conversation_id": "abc",
                "unread_count": 3,
                "total_unread": 7,
            }
        )

        self.assertEqual(
            frames,
            [{"type": "unread_update", "conversation_id": "abc", "unread_count": 3, "total_unread": 7}],
        )
