# capp/tests_chat_send_path.py
"""
The chat send path, end to end, for the two things that broke it in production.

**The CORS allowlist.** Every paid endpoint carries ``Idempotency-Key``. The
frontend and the API are separate Railway services, so every send is
cross-origin, and a header missing from the preflight response is refused by the
browser *before the request is sent*. The API therefore records nothing at all:
no 4xx, no log line, no Celery task. That is indistinguishable from a backend
outage from the outside, which is why this is pinned by a test rather than left
to review.

**The flag.** ``BILLING_REQUIRE_IDEMPOTENCY_KEY`` is read from the environment,
where it is the string ``"false"``. A boolean parser that treats any non-empty
string as true would turn a compatibility switch into a hard rejection of every
keyless send, so the flag is exercised here through the same parser
``settings.py`` uses, against the literal strings Railway holds.
"""

import os
import uuid
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase, override_settings

from capp.classes.billing import idempotency
from capp.classes.guest.guest_identity_service import hash_credential
from capp.constants import MESSAGE_PAIR_COST
from capp.models import (
    BillingReservation,
    Conversation,
    GuestIdentity,
    Message,
    SparkPool,
)
from capp.tests import _make_character, _make_user
from eaaicproject.settings import env_bool

FRONTEND_ORIGIN = "https://app.example.com"
ENSURE_URL = "/api/v1/guest/session/"

FLAG_ENV_VALUES = [
    # (environment value, what settings.py must read it as)
    ("false", False),  # the literal string configured on Railway today
    ("true", True),
    (None, False),  # unset — the documented default
]


def send_url(conversation_id):
    return f"/api/v1/conversations/{conversation_id}/messages/"


def flag_from_env(raw):
    """``BILLING_REQUIRE_IDEMPOTENCY_KEY`` as ``settings.py`` would read it."""
    with mock.patch.dict(os.environ, {}, clear=False):
        if raw is None:
            os.environ.pop("BILLING_REQUIRE_IDEMPOTENCY_KEY", None)
        else:
            os.environ["BILLING_REQUIRE_IDEMPOTENCY_KEY"] = raw
        return env_bool("BILLING_REQUIRE_IDEMPOTENCY_KEY", False)


# ======================================================================
# The CORS preflight contract
# ======================================================================


@override_settings(CORS_ALLOWED_ORIGINS=[FRONTEND_ORIGIN], ALLOWED_HOSTS=["*"])
class IdempotencyKeyCorsTests(SimpleTestCase):
    """
    A preflight that omits ``Idempotency-Key`` silently disables chat.

    These assertions are on the response the browser inspects, not on the
    setting, because the setting is only half the contract: the header name the
    client sends has to be the name the server allows, in the casing-insensitive
    form the middleware emits.
    """

    def preflight(self, request_headers="content-type,idempotency-key", origin=FRONTEND_ORIGIN):
        return Client().options(
            send_url(uuid.uuid4()),
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS=request_headers,
        )

    def allowed_headers(self, response):
        raw = response.headers.get("access-control-allow-headers", "")
        return {value.strip().lower() for value in raw.split(",") if value.strip()}

    def test_preflight_allows_the_idempotency_key_header(self):
        response = self.preflight()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), FRONTEND_ORIGIN)
        self.assertIn("idempotency-key", self.allowed_headers(response))

    def test_preflight_still_allows_the_headers_every_request_needs(self):
        allowed = self.allowed_headers(self.preflight())

        # Widening the list must not have replaced the defaults: authorization
        # carries the JWT and x-csrftoken is what guest sends are checked with.
        self.assertLessEqual({"authorization", "content-type", "x-csrftoken"}, allowed)

    def test_the_allowed_header_is_the_one_the_client_is_told_to_send(self):
        """
        ``idempotency.HEADER`` is the single source of truth for the name, and it
        is what the frontend sends. Allowing some other spelling would pass the
        test above while still failing in a browser.
        """
        from django.conf import settings

        configured = {str(header).lower() for header in settings.CORS_ALLOW_HEADERS}
        self.assertIn(idempotency.HEADER.lower(), configured)

    def test_the_echoed_key_is_readable_by_the_browser(self):
        """
        A server-minted key is returned in the response header so a retry can
        reuse it. Cross-origin JavaScript cannot read a header that is not
        exposed, so without this the echo exists but is invisible.
        """
        from django.conf import settings

        exposed = {str(header).lower() for header in settings.CORS_EXPOSE_HEADERS}
        self.assertIn(idempotency.HEADER.lower(), exposed)

    def test_an_unknown_origin_is_still_refused(self):
        """Widening the header list must not have widened the origin list."""
        response = self.preflight(origin="https://attacker.example.com")

        self.assertIsNone(response.headers.get("access-control-allow-origin"))


# ======================================================================
# Strict boolean parsing of the environment
# ======================================================================


class EnvBoolTests(SimpleTestCase):
    """
    ``env_bool`` gates DEBUG, TLS and the idempotency requirement, so a value it
    misreads changes behaviour with no other symptom.
    """

    def read(self, raw, default=False):
        with mock.patch.dict(os.environ, {}, clear=False):
            if raw is None:
                os.environ.pop("EAAIC_TEST_FLAG", None)
            else:
                os.environ["EAAIC_TEST_FLAG"] = raw
            return env_bool("EAAIC_TEST_FLAG", default)

    def test_unset_takes_the_default(self):
        self.assertFalse(self.read(None, default=False))
        self.assertTrue(self.read(None, default=True))

    def test_empty_takes_the_default(self):
        """
        A platform that exports a variable with no value has expressed no
        opinion. Reading that as False would silently switch off a flag whose
        default is True.
        """
        self.assertTrue(self.read("", default=True))
        self.assertTrue(self.read("   ", default=True))
        self.assertFalse(self.read("", default=False))

    def test_true_spellings(self):
        for raw in ("true", "1", "yes", "y", "on", "t"):
            with self.subTest(raw=raw):
                self.assertTrue(self.read(raw))

    def test_false_spellings_are_not_read_as_true(self):
        for raw in ("false", "0", "no", "n", "off", "f"):
            with self.subTest(raw=raw):
                self.assertFalse(self.read(raw, default=True))

    def test_case_and_surrounding_whitespace_are_ignored(self):
        for raw in ("TRUE", "True", " true ", "\tYes\n", "ON"):
            with self.subTest(raw=raw):
                self.assertTrue(self.read(raw))
        for raw in ("FALSE", "False", " false ", "\tNo\n", "OFF"):
            with self.subTest(raw=raw):
                self.assertFalse(self.read(raw, default=True))

    def test_an_unrecognised_value_raises_rather_than_defaulting(self):
        """
        A typo must not quietly become False: for a flag that defaults to True
        that is a silent downgrade, and the wrong value is never reported.
        """
        for raw in ("ture", "enabled", "maybe", "2", "null", "none"):
            with self.subTest(raw=raw):
                with self.assertRaises(ImproperlyConfigured):
                    self.read(raw, default=True)

    def test_the_railway_value_for_the_idempotency_flag_is_false(self):
        for raw, expected in FLAG_ENV_VALUES:
            with self.subTest(raw=raw):
                self.assertIs(flag_from_env(raw), expected)


# ======================================================================
# Sending a message, for both principals, under every flag value
# ======================================================================


class ChatSendPathTestCase(TestCase):
    """Fixtures shared by the authenticated and guest send tests."""

    def setUp(self):
        from django.core.cache import cache

        # ChatMessageThrottle is 10/minute against the process cache.
        cache.clear()
        self.character = _make_character(name="Zuri", slug="zuri")

    def assertDispatchedOnce(self, dispatch, conversation, message_id, **expected):
        dispatch.assert_called_once()
        self.assertEqual(
            dispatch.call_args.kwargs,
            {
                "conversation_id": str(conversation.conversation_id),
                "user_message_id": str(message_id),
                **expected,
            },
        )

    def post_send(self, conversation, operation_id=None, content="habari", **extra):
        headers = {}
        if operation_id is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = operation_id
        with mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = self.client.post(
                send_url(conversation.conversation_id),
                data={"content": content},
                content_type="application/json",
                **headers,
                **extra,
            )
        return response, dispatch


class AuthenticatedSendTests(ChatSendPathTestCase):
    def setUp(self):
        super().setUp()
        self.user = _make_user(username="sender", email="sender@example.com", verified=True)
        SparkPool.objects.create(user=self.user, sparks=10000, token_balance=0, is_active=True)
        self.conversation = Conversation.objects.create(user=self.user, character=self.character)
        self.client = Client()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self._token()}"}

    def _token(self):
        from capp.tests import RECAPTCHA_OK

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

    def test_a_keyed_send_is_accepted_under_every_flag_value(self):
        for raw, flag in FLAG_ENV_VALUES:
            with self.subTest(env=raw):
                operation_id = str(uuid.uuid4())
                with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag):
                    response, dispatch = self.post_send(self.conversation, operation_id=operation_id, **self.auth)

                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.headers[idempotency.HEADER], operation_id)

                message = Message.objects.get(client_operation_id=operation_id)
                self.assertEqual(message.sender_type, Message.SenderType.USER)
                self.assertDispatchedOnce(
                    dispatch,
                    self.conversation,
                    message.message_id,
                    is_guest=False,
                    guest_session_id=None,
                )

    def test_a_keyless_send_is_accepted_while_the_flag_is_off(self):
        """
        The compatibility case this flag exists for: a browser running the
        bundle from before the header was added must keep working, billed once
        against a key the server mints.
        """
        for raw in ("false", None):
            with self.subTest(env=raw):
                with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag_from_env(raw)):
                    response, dispatch = self.post_send(self.conversation, **self.auth)

                self.assertEqual(response.status_code, 201)

                minted = response.headers[idempotency.HEADER]
                self.assertEqual(idempotency.parse(minted), minted)

                message = Message.objects.get(message_id=response.json()["data"]["message_id"])
                self.assertEqual(message.client_operation_id, minted)
                self.assertDispatchedOnce(
                    dispatch,
                    self.conversation,
                    message.message_id,
                    is_guest=False,
                    guest_session_id=None,
                )

    def test_a_keyless_send_is_refused_only_once_the_flag_is_on(self):
        with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag_from_env("true")):
            response, dispatch = self.post_send(self.conversation, **self.auth)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_idempotency_key")
        dispatch.assert_not_called()
        self.assertFalse(Message.objects.exists())

    def test_the_flag_rejects_a_missing_key_and_not_an_unrelated_bad_field(self):
        """
        Turning the flag on must not turn every other 400 into an idempotency
        error, or the frontend cannot tell the user what to fix.
        """
        with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag_from_env("true")):
            response, dispatch = self.post_send(
                self.conversation, operation_id=str(uuid.uuid4()), content="   ", **self.auth
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "message_content_required")
        dispatch.assert_not_called()

    def test_a_malformed_key_is_named_distinctly(self):
        response, dispatch = self.post_send(self.conversation, operation_id="not-a-uuid", **self.auth)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_idempotency_key")
        dispatch.assert_not_called()

    def test_replaying_a_key_returns_the_first_message_and_dispatches_once(self):
        operation_id = str(uuid.uuid4())
        first, first_dispatch = self.post_send(self.conversation, operation_id=operation_id, **self.auth)
        replay, replay_dispatch = self.post_send(self.conversation, operation_id=operation_id, **self.auth)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            replay.json()["data"]["message_id"],
            first.json()["data"]["message_id"],
        )
        first_dispatch.assert_called_once()
        replay_dispatch.assert_not_called()
        self.assertEqual(Message.objects.filter(client_operation_id=operation_id).count(), 1)

    def test_a_send_that_the_server_keyed_produces_exactly_one_reservation(self):
        """
        The key minted by the request has to reach the task, because that is
        what the Sparks are held against. One reservation, carrying that key, is
        the proof the linkage survived.
        """
        with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag_from_env("false")):
            response, _ = self.post_send(self.conversation, **self.auth)

        minted = response.headers[idempotency.HEADER]
        message = Message.objects.get(client_operation_id=minted)

        self.run_task(message)

        reservation = BillingReservation.objects.get()
        self.assertEqual(reservation.idempotency_key, minted)
        self.assertEqual(reservation.user_id, self.user.pk)
        self.assertEqual(int(reservation.amount_sparks), MESSAGE_PAIR_COST)
        self.assertEqual(reservation.status, BillingReservation.Status.SETTLED)

    def run_task(self, message):
        from capp.classes.ai.cortecs_client import CortecsClient
        from capp.classes.ai.prompt_builder import PromptBuilder
        from capp.tasks import process_ai_response

        completion = {
            "choices": [{"message": {"content": "Nzuri sana!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "test-model",
        }
        with mock.patch.object(
            PromptBuilder,
            "build_messages",
            return_value=[{"role": "user", "content": message.content}],
        ), mock.patch.object(CortecsClient, "chat_completion", return_value=completion):
            process_ai_response(
                str(message.conversation.conversation_id),
                str(message.message_id),
            )


class GuestSendTests(ChatSendPathTestCase):
    """
    Guests are identified only by their HttpOnly credential cookie, so their
    send carries no Authorization header and is CSRF-checked instead. It is the
    path most likely to be broken by a change to either.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()
        raw = self.ensure_session()
        self.identity = GuestIdentity.objects.get(credential_hash=hash_credential(raw))
        self.conversation = Conversation.objects.create(
            character=self.character,
            guest_session_id=self.identity.owner_key,
        )

    def ensure_session(self):
        from django.conf import settings

        response = self.client.post(ENSURE_URL, data={}, content_type="application/json")
        self.assertIn(response.status_code, (200, 201))
        return self.client.cookies[settings.GUEST_CREDENTIAL_COOKIE_NAME].value

    def test_a_keyed_guest_send_is_accepted_under_every_flag_value(self):
        for raw, flag in FLAG_ENV_VALUES:
            with self.subTest(env=raw):
                operation_id = str(uuid.uuid4())
                with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag):
                    response, dispatch = self.post_send(self.conversation, operation_id=operation_id)

                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.headers[idempotency.HEADER], operation_id)

                message = Message.objects.get(client_operation_id=operation_id)
                self.assertDispatchedOnce(
                    dispatch,
                    self.conversation,
                    message.message_id,
                    is_guest=True,
                    guest_session_id=self.identity.owner_key,
                )

    def test_a_keyless_guest_send_is_accepted_while_the_flag_is_off(self):
        for raw in ("false", None):
            with self.subTest(env=raw):
                with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag_from_env(raw)):
                    response, dispatch = self.post_send(self.conversation)

                self.assertEqual(response.status_code, 201)

                minted = response.headers[idempotency.HEADER]
                message = Message.objects.get(client_operation_id=minted)
                self.assertDispatchedOnce(
                    dispatch,
                    self.conversation,
                    message.message_id,
                    is_guest=True,
                    guest_session_id=self.identity.owner_key,
                )

    def test_a_keyless_guest_send_is_refused_only_once_the_flag_is_on(self):
        with override_settings(BILLING_REQUIRE_IDEMPOTENCY_KEY=flag_from_env("true")):
            response, dispatch = self.post_send(self.conversation)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["data"], "invalid_idempotency_key")
        dispatch.assert_not_called()
        self.assertFalse(Message.objects.exists())

    def test_a_send_without_the_guest_cookie_is_named_distinctly(self):
        """
        401 ``guest_session_required`` is what tells the UI to refresh the
        session rather than showing a generic failure.
        """
        anonymous = Client()
        with mock.patch("capp.views.conversation_views.process_ai_response.delay") as dispatch:
            response = anonymous.post(
                send_url(self.conversation.conversation_id),
                data={"content": "habari"},
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["data"], "guest_session_required")
        dispatch.assert_not_called()

    def test_a_guest_cannot_send_into_someone_elses_conversation(self):
        other = Conversation.objects.create(
            character=self.character,
            guest_session_id="00000000-0000-0000-0000-000000000001",
        )

        response, dispatch = self.post_send(other, operation_id=str(uuid.uuid4()))

        self.assertEqual(response.status_code, 404)
        dispatch.assert_not_called()
