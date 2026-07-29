# capp/tests_message_pagination.py
"""
Coverage for cursor-paginated conversation history.

History used to return only the newest 50 messages with no way to reach what
came before, and the ``before`` parameter compared ``created_at`` alone — which
skips or repeats rows whenever two messages share a timestamp. These tests pin
the composite ``(created_at, message_id)`` cursor: every message is reachable
exactly once, pages never overlap, and a tampered cursor is refused.
"""

import base64
import uuid
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from capp.classes.chat import message_history
from capp.classes.guest.guest_identity_service import hash_credential
from capp.models import Conversation, GuestIdentity, Message
from capp.tests import RECAPTCHA_OK, _bearer_token, _make_character, _make_user
from capp.tests_guest_identity import GuestTestMixin

BASE_TIME = timezone.now() - timedelta(days=30)


def _seed_messages(conversation, count, start=BASE_TIME, step=timedelta(seconds=1), prefix="m"):
    """Create ``count`` messages with explicit, ordered timestamps.

    ``created_at`` is ``auto_now_add``, so the timestamps are written afterwards
    with an UPDATE. Returns the messages oldest-first.
    """
    created = []
    for index in range(count):
        message = Message.objects.create(
            conversation=conversation,
            sender_type=(Message.SenderType.USER if index % 2 == 0 else Message.SenderType.AI),
            content=f"{prefix}-{index}",
            status=Message.StatusChoices.SENT,
        )
        stamp = start + (step * index)
        Message.objects.filter(pk=message.pk).update(created_at=stamp)
        message.created_at = stamp
        created.append(message)
    return created


class HistoryCursorUnitTests(TestCase):
    """The cursor codec and page-size clamp, independent of the HTTP layer."""

    def test_cursor_round_trips(self):
        message = mock.Mock(created_at=BASE_TIME, message_id=uuid.uuid4())
        cursor = message_history.decode_cursor(message_history.encode_cursor(message))

        self.assertEqual(cursor.created_at, BASE_TIME)
        self.assertEqual(cursor.message_id, message.message_id)

    def test_cursor_is_opaque_and_url_safe(self):
        message = mock.Mock(created_at=BASE_TIME, message_id=uuid.uuid4())
        encoded = message_history.encode_cursor(message)

        self.assertNotIn("=", encoded)
        self.assertNotIn("+", encoded)
        self.assertNotIn("/", encoded)

    def test_malformed_cursors_are_rejected(self):
        broken = [
            "",
            "   ",
            "not-base64-!!",
            base64.urlsafe_b64encode(b"only-one-part").decode(),
            base64.urlsafe_b64encode(b"not-a-date|" + str(uuid.uuid4()).encode()).decode(),
            base64.urlsafe_b64encode(BASE_TIME.isoformat().encode() + b"|not-a-uuid").decode(),
            base64.urlsafe_b64encode(b"2026-01-01T00:00:00|" + str(uuid.uuid4()).encode()).decode(),
        ]
        for value in broken:
            with self.subTest(cursor=value):
                with self.assertRaises(message_history.InvalidCursor):
                    message_history.decode_cursor(value)

    def test_page_size_is_bounded(self):
        self.assertEqual(message_history.resolve_page_size(None), message_history.DEFAULT_PAGE_SIZE)
        self.assertEqual(message_history.resolve_page_size("abc"), message_history.DEFAULT_PAGE_SIZE)
        self.assertEqual(message_history.resolve_page_size("0"), message_history.DEFAULT_PAGE_SIZE)
        self.assertEqual(message_history.resolve_page_size("-5"), message_history.DEFAULT_PAGE_SIZE)
        self.assertEqual(message_history.resolve_page_size("10"), 10)
        self.assertEqual(message_history.resolve_page_size("100000"), message_history.MAX_PAGE_SIZE)


class MessageHistoryPaginationTests(TestCase):
    """The authenticated owner walking back through a long conversation."""

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="hist", email="hist@example.com", verified=True)
        self.character = _make_character(name="Hista", slug="hista")
        self.conversation = Conversation.objects.create(user=self.owner, character=self.character)
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            self.token = _bearer_token(self.client, "hist")

    def _get(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items() if value not in (None, ""))
        url = f"/api/v1/conversations/{self.conversation.conversation_id}/messages-list/"
        if query:
            url = f"{url}?{query}"
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def _walk_all_pages(self, limit=None):
        """Follow ``next_before`` to the beginning; return (contents, page_count)."""
        collected = []
        cursor = None
        pages = 0
        while True:
            resp = self._get(before=cursor, limit=limit)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()["data"]
            collected = [msg["content"] for msg in data["messages"]] + collected
            pages += 1
            cursor = data["pagination"]["next_before"]
            if not data["pagination"]["has_more"]:
                self.assertIsNone(cursor)
                break
            self.assertIsNotNone(cursor)
            self.assertLess(pages, 50, "cursor walk did not terminate")
        return collected, pages

    def test_first_page_is_the_newest_bounded_slice_in_chronological_order(self):
        _seed_messages(self.conversation, 120)

        data = self._get().json()["data"]
        contents = [msg["content"] for msg in data["messages"]]

        self.assertEqual(len(contents), message_history.DEFAULT_PAGE_SIZE)
        self.assertEqual(contents[0], "m-70")
        self.assertEqual(contents[-1], "m-119")
        self.assertTrue(data["pagination"]["has_more"])
        self.assertEqual(data["pagination"]["page_size"], message_history.DEFAULT_PAGE_SIZE)
        self.assertIsNotNone(data["pagination"]["next_before"])

    def test_walking_cursors_covers_every_message_exactly_once(self):
        _seed_messages(self.conversation, 120)

        contents, pages = self._walk_all_pages()

        self.assertEqual(contents, [f"m-{index}" for index in range(120)])
        self.assertEqual(len(set(contents)), 120)
        self.assertEqual(pages, 3)

    def test_conversation_just_over_one_page(self):
        _seed_messages(self.conversation, 51)

        contents, pages = self._walk_all_pages()

        self.assertEqual(contents, [f"m-{index}" for index in range(51)])
        self.assertEqual(pages, 2)

    def test_exactly_one_page_reports_no_more_history(self):
        _seed_messages(self.conversation, 50)

        data = self._get().json()["data"]

        self.assertEqual(len(data["messages"]), 50)
        self.assertFalse(data["pagination"]["has_more"])
        self.assertIsNone(data["pagination"]["next_before"])

    def test_empty_conversation(self):
        data = self._get().json()["data"]

        self.assertEqual(data["messages"], [])
        self.assertFalse(data["pagination"]["has_more"])
        self.assertIsNone(data["pagination"]["next_before"])

    def test_identical_timestamps_paginate_without_gaps_or_duplicates(self):
        # Every message shares one timestamp: the whole ordering rests on the
        # message_id tie-breaker.
        _seed_messages(self.conversation, 120, step=timedelta(0))

        contents, _ = self._walk_all_pages()

        self.assertEqual(len(contents), 120)
        self.assertEqual(len(set(contents)), 120)

    def test_timestamp_collisions_at_a_page_boundary(self):
        # A pair sharing a timestamp straddles the boundary of a 10-row page.
        collision = BASE_TIME + timedelta(seconds=5)
        messages = _seed_messages(self.conversation, 30)
        Message.objects.filter(pk__in=[messages[9].pk, messages[10].pk]).update(created_at=collision)

        contents, _ = self._walk_all_pages(limit=10)

        self.assertEqual(len(contents), 30)
        self.assertEqual(len(set(contents)), 30)

    def test_custom_limit_is_honoured_and_capped(self):
        _seed_messages(self.conversation, 150)

        small = self._get(limit=5).json()["data"]
        self.assertEqual(len(small["messages"]), 5)
        self.assertEqual(small["pagination"]["page_size"], 5)

        oversized = self._get(limit=10_000).json()["data"]
        self.assertEqual(len(oversized["messages"]), message_history.MAX_PAGE_SIZE)
        self.assertEqual(oversized["pagination"]["page_size"], message_history.MAX_PAGE_SIZE)

    def test_repeating_a_cursor_returns_the_identical_page(self):
        _seed_messages(self.conversation, 120)
        cursor = self._get().json()["data"]["pagination"]["next_before"]

        first = self._get(before=cursor).json()["data"]["messages"]
        second = self._get(before=cursor).json()["data"]["messages"]

        self.assertEqual(first, second)

    def test_new_message_between_page_requests_does_not_shift_older_pages(self):
        _seed_messages(self.conversation, 120)
        first_page = self._get().json()["data"]
        cursor = first_page["pagination"]["next_before"]

        # A live reply lands while the user is scrolling upwards.
        _seed_messages(self.conversation, 1, start=timezone.now(), prefix="live")

        older = [msg["content"] for msg in self._get(before=cursor).json()["data"]["messages"]]

        self.assertNotIn("live-0", older)
        self.assertEqual(older, [f"m-{index}" for index in range(20, 70)])

    def test_deleting_a_message_leaves_no_gap_in_the_walk(self):
        messages = _seed_messages(self.conversation, 120)
        Message.objects.filter(pk=messages[80].pk).delete()

        contents, _ = self._walk_all_pages()

        expected = [f"m-{index}" for index in range(120) if index != 80]
        self.assertEqual(contents, expected)

    def test_deleted_cursor_target_is_rejected_rather_than_silently_restarting(self):
        messages = _seed_messages(self.conversation, 60)
        stale_id = str(messages[10].message_id)
        Message.objects.filter(pk=messages[10].pk).delete()

        resp = self._get(before=stale_id)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_cursor")

    def test_malformed_cursor_is_a_clear_400(self):
        _seed_messages(self.conversation, 60)

        for value in ["garbage", "!!!", base64.urlsafe_b64encode(b"tampered").decode(), str(uuid.uuid4())]:
            with self.subTest(cursor=value):
                resp = self._get(before=value)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["data"], "invalid_cursor")

    def test_bare_message_id_cursor_still_works(self):
        """The pre-cursor client form: ``before=<message_id>``."""
        messages = _seed_messages(self.conversation, 60)

        data = self._get(before=str(messages[30].message_id)).json()["data"]
        contents = [msg["content"] for msg in data["messages"]]

        self.assertEqual(contents[-1], "m-29")
        self.assertNotIn("m-30", contents)

    def test_unknown_query_params_do_not_break_the_first_page(self):
        _seed_messages(self.conversation, 10)

        data = self._get(before="", limit="").json()["data"]

        self.assertEqual(len(data["messages"]), 10)

    def test_page_costs_a_constant_number_of_queries(self):
        messages = _seed_messages(self.conversation, 120)
        # Replies point at their predecessor: serialising must not fetch parents.
        for older, newer in zip(messages, messages[1:]):
            Message.objects.filter(pk=newer.pk).update(reply_to=older.pk)

        with self.assertNumQueries(4) as small:
            self.assertEqual(self._get(limit=5).status_code, 200)
        with self.assertNumQueries(len(small.captured_queries)):
            self.assertEqual(self._get(limit=100).status_code, 200)

    def test_reply_to_is_reported_without_loading_the_parent(self):
        messages = _seed_messages(self.conversation, 2)
        Message.objects.filter(pk=messages[1].pk).update(reply_to=messages[0].pk)

        returned = self._get().json()["data"]["messages"]

        self.assertIsNone(returned[0]["reply_to"])
        self.assertEqual(returned[1]["reply_to"], str(messages[0].message_id))


class HistoryContentFidelityTests(TestCase):
    """Older pages must carry everything the newest page carries."""

    def setUp(self):
        cache.clear()
        self.owner = _make_user(username="fidel", email="fidel@example.com", verified=True)
        self.character = _make_character(name="Fidelia", slug="fidelia")
        self.conversation = Conversation.objects.create(user=self.owner, character=self.character)
        with mock.patch(
            "capp.classes.auth.recaptcha.RecaptchaService.verify_token",
            return_value=RECAPTCHA_OK,
        ):
            self.token = _bearer_token(self.client, "fidel")

    def _get(self, before=None):
        url = f"/api/v1/conversations/{self.conversation.conversation_id}/messages-list/"
        if before:
            url = f"{url}?before={before}"
        return self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def _older_page(self):
        cursor = self._get().json()["data"]["pagination"]["next_before"]
        self.assertIsNotNone(cursor)
        return self._get(before=cursor).json()["data"]["messages"]

    def test_image_only_message_on_an_older_page_gets_a_fresh_signed_url(self):
        image = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.AI,
            content="",
            status=Message.StatusChoices.SENT,
            metadata={"type": "generated_image", "image_name": "generated/old-page.webp"},
        )
        Message.objects.filter(pk=image.pk).update(created_at=BASE_TIME)
        _seed_messages(self.conversation, 60, start=BASE_TIME + timedelta(minutes=1))

        signed = "https://bucket.railway.app/generated/old-page.webp?X-Amz-Signature=fresh"
        with mock.patch("capp.views.conversation_views.default_storage.url", return_value=signed):
            older = self._older_page()

        image_rows = [msg for msg in older if msg["message_id"] == str(image.message_id)]
        self.assertEqual(len(image_rows), 1)
        self.assertEqual(image_rows[0]["image_url"], signed)
        self.assertEqual(image_rows[0]["content"], "")
        self.assertEqual(image_rows[0]["type"], "generated_image")

    def test_system_message_on_an_older_page_keeps_its_localisation_code(self):
        system = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.SYSTEM,
            content="You've reached your daily free limit.",
            status=Message.StatusChoices.SENT,
            metadata={"code": "guest_daily_limit"},
        )
        Message.objects.filter(pk=system.pk).update(created_at=BASE_TIME)
        _seed_messages(self.conversation, 60, start=BASE_TIME + timedelta(minutes=1))

        older = self._older_page()
        row = next(msg for msg in older if msg["message_id"] == str(system.message_id))

        self.assertEqual(row["sender_type"], "system")
        self.assertEqual(row["code"], "guest_daily_limit")

    def test_content_is_decrypted_on_every_page(self):
        secret = "siri kubwa sana"
        first = Message.objects.create(
            conversation=self.conversation,
            sender_type=Message.SenderType.USER,
            content=secret,
            status=Message.StatusChoices.SENT,
        )
        Message.objects.filter(pk=first.pk).update(created_at=BASE_TIME)
        _seed_messages(self.conversation, 60, start=BASE_TIME + timedelta(minutes=1))

        older = self._older_page()
        row = next(msg for msg in older if msg["message_id"] == str(first.message_id))

        self.assertEqual(row["content"], secret)

    def test_character_info_accompanies_every_page(self):
        _seed_messages(self.conversation, 60)

        older = self._get(before=self._get().json()["data"]["pagination"]["next_before"]).json()["data"]

        self.assertEqual(older["character"]["name"], "Fidelia")
        self.assertEqual(older["character"]["chat_state"], "active")
        self.assertGreater(len(older["messages"]), 0)


class HistoryAuthorizationTests(GuestTestMixin, TestCase):
    """Pagination must not become a way around conversation ownership."""

    def setUp(self):
        super().setUp()
        self.character = _make_character(name="Zuri", slug="zuri")

    def _history_url(self, conversation, before=None):
        url = f"/api/v1/conversations/{conversation.conversation_id}/messages-list/"
        return f"{url}?before={before}" if before else url

    def _guest_conversation(self, client):
        raw = self.ensure_session(client)
        identity = GuestIdentity.objects.get(credential_hash=hash_credential(raw))
        conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=identity.owner_key,
        )
        _seed_messages(conversation, 60)
        return conversation

    def test_guest_can_page_its_own_history(self):
        conversation = self._guest_conversation(self.client)

        first = self.client.get(self._history_url(conversation)).json()["data"]
        self.assertTrue(first["pagination"]["has_more"])

        older = self.client.get(self._history_url(conversation, first["pagination"]["next_before"])).json()["data"]

        self.assertEqual(
            [msg["content"] for msg in older["messages"]],
            [f"m-{index}" for index in range(10)],
        )

    def test_another_guest_cannot_page_someone_elses_history(self):
        victim = Client()
        conversation = self._guest_conversation(victim)
        cursor = victim.get(self._history_url(conversation)).json()["data"]["pagination"]["next_before"]

        attacker = Client()
        self.ensure_session(attacker)

        self.assertEqual(attacker.get(self._history_url(conversation)).status_code, 403)
        self.assertEqual(attacker.get(self._history_url(conversation, cursor)).status_code, 403)

    def test_cookieless_caller_cannot_page_guest_history(self):
        victim = Client()
        conversation = self._guest_conversation(victim)

        anonymous = Client()
        self.assertEqual(anonymous.get(self._history_url(conversation)).status_code, 403)

    def test_authenticated_user_cannot_page_another_users_history(self):
        owner = _make_user(username="owner9", email="owner9@example.com", verified=True)
        _make_user(username="nosy9", email="nosy9@example.com", verified=True)
        conversation = Conversation.objects.create(user=owner, character=self.character)
        _seed_messages(conversation, 60)

        owner_client = Client()
        owner_token = self.signin("owner9", client=owner_client).json()["data"]["access_token"]
        cursor = owner_client.get(
            self._history_url(conversation),
            HTTP_AUTHORIZATION=f"Bearer {owner_token}",
        ).json()[
            "data"
        ]["pagination"]["next_before"]

        intruder_client = Client()
        intruder_token = self.signin("nosy9", client=intruder_client).json()["data"]["access_token"]

        for url in (self._history_url(conversation), self._history_url(conversation, cursor)):
            resp = intruder_client.get(url, HTTP_AUTHORIZATION=f"Bearer {intruder_token}")
            self.assertEqual(resp.status_code, 403)

    def test_cursor_from_another_conversation_is_rejected(self):
        owner = _make_user(username="owner10", email="owner10@example.com", verified=True)
        mine = Conversation.objects.create(user=owner, character=self.character)
        other_character = _make_character(name="Other", slug="other10")
        theirs = Conversation.objects.create(user=owner, character=other_character)
        _seed_messages(mine, 10)
        foreign = _seed_messages(theirs, 10)[5]

        client = Client()
        token = self.signin("owner10", client=client).json()["data"]["access_token"]

        resp = client.get(
            self._history_url(mine, str(foreign.message_id)),
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["data"], "invalid_cursor")
