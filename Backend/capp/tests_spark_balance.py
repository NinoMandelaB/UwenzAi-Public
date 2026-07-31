"""
What the Sparks counter is told, over HTTP and on the socket.

Two properties are under test.

The first is a security property inherited from the guest credential work: a
guest balance is readable only by presenting the HttpOnly credential cookie. An
ownership key in a query string, a header or a body is not a credential and must
buy nothing, because that is precisely the takeover the cookie replaced.

The second is the display bug this suite was added for: the socket used to push
``sparks: 0`` to any guest whose quota row did not exist yet — every first-time
guest — and the browser cached that zero. Both readers now go through
``capp.classes.billing.balances``, so a guest who has never spent anything is
told what they actually have, before any chat activity.
"""

import json
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.test import Client, TestCase, TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.classes.guest.ws_ticket_service import GuestWebSocketTicketService
from capp.constants import GUEST_INITIAL_SPARKS
from capp.consumers import SparkPoolConsumer
from capp.models import GuestTokenQuota, GuestWebSocketTicket, SparkPool
from capp.tests import _make_user
from capp.tests_guest_identity import GuestTestMixin

COOKIE = settings.GUEST_CREDENTIAL_COOKIE_NAME

BALANCE_URL = "/api/v1/spark_pool/balance/"

# ``/api/v1/`` is included before ``/api/v1/spark_pool/``, so this path is taken
# by an older accounts-only view in ``package_views`` rather than the guest-aware
# one. The frontend used to call it and every guest fetch came back 401.
LEGACY_ACCOUNTS_ONLY_URL = "/api/v1/spark_pool/"

IN_MEMORY_CHANNELS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def _mint_guest(sparks=None):
    """A server-minted guest identity, as the ensure-session endpoint makes one."""
    identity, _credential = GuestIdentityService.mint()
    if sparks is not None:
        GuestTokenQuota.objects.create(session_id=identity.owner_key, sparks=sparks)
    return identity


def _spark_pool(user, sparks, is_active=True):
    return SparkPool.objects.create(user=user, sparks=sparks, token_balance=0, is_active=is_active)


def _spark_pool_ticket(identity):
    result = GuestWebSocketTicketService.issue(identity, GuestWebSocketTicket.Channel.SPARK_POOL)
    return result["data"]["ticket"]


class GuestBalanceEndpointTests(GuestTestMixin, TestCase):
    """The cookie decides whose balance is returned, or nobody's."""

    def test_a_cookieless_request_is_refused(self):
        response = self.client.get(BALANCE_URL)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["data"], "guest_session_required")

    def test_the_legacy_route_serves_accounts_only(self):
        """
        A tripwire, not an endorsement. ``/api/v1/spark_pool/`` reaches an
        accounts-only view, which is why a guest fetch aimed there could never
        succeed no matter when the credential cookie arrived. If this ever
        starts returning a guest balance, the two views have been merged and the
        frontend constant should be revisited.
        """
        self.ensure_session()

        self.assertEqual(self.client.get(LEGACY_ACCOUNTS_ONLY_URL).status_code, 401)

    def test_a_client_supplied_session_id_buys_nothing(self):
        """
        Knowing an ownership key is not the same as owning it. None of the
        places the old client-generated ID used to travel may authenticate a
        balance read.
        """
        identity = _mint_guest(sparks=999)

        attempts = [
            self.client.get(f"{BALANCE_URL}?session_id={identity.owner_key}"),
            self.client.get(BALANCE_URL, HTTP_X_GUEST_SESSION_ID=identity.owner_key),
            self.client.get(BALANCE_URL, data={"guest_session_id": identity.owner_key}),
        ]

        for response in attempts:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["data"], "guest_session_required")

    def test_a_forged_cookie_value_is_refused(self):
        _mint_guest(sparks=999)
        self.client.cookies[COOKIE] = "not-a-real-credential"

        self.assertEqual(self.client.get(BALANCE_URL).status_code, 401)

    def test_a_first_touch_guest_is_told_about_the_free_allowance(self):
        """The number the counter should show before any chat activity."""
        self.ensure_session()

        response = self.client.get(BALANCE_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"type": "guest", "sparks": GUEST_INITIAL_SPARKS})

    def test_the_balance_is_whatever_the_quota_row_holds(self):
        self.ensure_session()
        self.client.get(BALANCE_URL)  # first touch creates the row

        GuestTokenQuota.objects.update(sparks=42)

        self.assertEqual(self.client.get(BALANCE_URL).json()["data"]["sparks"], 42)

    def test_a_claimed_guest_is_not_granted_a_second_allowance(self):
        """
        After a transfer the guest row is kept and zeroed. Re-granting here
        would mint Sparks that were already moved into the account.
        """
        self.ensure_session()
        self.client.get(BALANCE_URL)
        quota = GuestTokenQuota.objects.get()
        quota.sparks = 0
        quota.claimed_at = quota.created_at
        quota.save(update_fields=["sparks", "claimed_at"])

        self.assertEqual(self.client.get(BALANCE_URL).json()["data"]["sparks"], 0)

    def test_two_guests_cannot_see_each_others_balance(self):
        self.ensure_session()
        self.client.get(BALANCE_URL)
        GuestTokenQuota.objects.update(sparks=7)

        other = Client()
        self.ensure_session(client=other)

        self.assertEqual(other.get(BALANCE_URL).json()["data"]["sparks"], GUEST_INITIAL_SPARKS)
        self.assertEqual(self.client.get(BALANCE_URL).json()["data"]["sparks"], 7)


class AccountBalanceEndpointTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.token = str(AccessToken.for_user(self.user))

    def get(self):
        return self.client.get(BALANCE_URL, HTTP_AUTHORIZATION=f"Bearer {self.token}")

    def test_the_active_pool_is_reported(self):
        _spark_pool(self.user, 1500)

        self.assertEqual(self.get().json()["data"], {"type": "authenticated", "sparks": 1500})

    def test_an_account_without_a_pool_reads_as_zero(self):
        self.assertEqual(self.get().json()["data"]["sparks"], 0)

    def test_a_deactivated_pool_reads_as_zero(self):
        _spark_pool(self.user, 1500, is_active=False)

        self.assertEqual(self.get().json()["data"]["sparks"], 0)


class _Socket:
    """
    Runs ``SparkPoolConsumer.connect`` without an ASGI server.

    ``channels.testing`` cannot be imported here: its package init pulls in
    daphne, which is not a dependency of this project.
    """

    def __init__(self, query):
        self.frames = []
        self.accepted = False
        self.close_code = None

        consumer = SparkPoolConsumer()
        consumer.scope = {"type": "websocket", "query_string": query.encode()}
        consumer.channel_name = f"test-{uuid.uuid4()}"
        consumer.channel_layer = get_channel_layer()
        consumer.send = self._send
        consumer.accept = self._accept
        consumer.close = self._close
        self.consumer = consumer

    async def _send(self, text_data=None, **kwargs):
        self.frames.append(json.loads(text_data))

    async def _accept(self, *args, **kwargs):
        self.accepted = True

    async def _close(self, code=None):
        self.close_code = code

    @property
    def first_frame(self):
        return self.frames[0]


def _connect(query):
    socket = _Socket(query)
    async_to_sync(socket.consumer.connect)()
    return socket


def _account_token(sparks, is_active=True):
    suffix = uuid.uuid4().hex[:8]
    user = _make_user(username=f"socket-{suffix}", email=f"{suffix}@example.com")
    _spark_pool(user, sparks, is_active=is_active)
    return str(AccessToken.for_user(user))


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNELS)
class ConnectTimeBalancePushTests(TransactionTestCase):
    """
    The counter has to be right before the user does anything.

    Guests connect with a single-use ticket rather than a token, and that path
    used to reach the balance read with no quota row and answer zero, so the
    number only became true once chatting had created the row.
    """

    def test_a_first_time_guest_is_pushed_the_allowance_not_zero(self):
        socket = _connect(f"ticket={_spark_pool_ticket(_mint_guest())}")

        self.assertTrue(socket.accepted)
        self.assertEqual(socket.first_frame, {"type": "spark_pool_balance", "sparks": GUEST_INITIAL_SPARKS})

    def test_the_connect_push_leaves_the_guest_allowance_on_record(self):
        """The pushed number is the balance, not a display-only guess."""
        identity = _mint_guest()

        _connect(f"ticket={_spark_pool_ticket(identity)}")

        quota = GuestTokenQuota.objects.get(session_id=identity.owner_key)
        self.assertEqual(quota.sparks, GUEST_INITIAL_SPARKS)

    def test_a_returning_guest_is_pushed_the_stored_balance(self):
        socket = _connect(f"ticket={_spark_pool_ticket(_mint_guest(sparks=88))}")

        self.assertEqual(socket.first_frame["sparks"], 88)

    def test_an_account_is_pushed_its_balance_on_connect(self):
        socket = _connect(f"token={_account_token(1234)}")

        self.assertTrue(socket.accepted)
        self.assertEqual(socket.first_frame, {"type": "spark_pool_balance", "sparks": 1234})

    def test_an_account_with_a_deactivated_pool_is_pushed_zero(self):
        socket = _connect(f"token={_account_token(500, is_active=False)}")

        self.assertEqual(socket.first_frame["sparks"], 0)

    def test_a_socket_with_no_credential_at_all_is_closed(self):
        socket = _connect("")

        self.assertFalse(socket.accepted)
        self.assertEqual(socket.close_code, 4001)
        self.assertEqual(socket.frames, [])

    def test_a_raw_session_id_in_the_url_does_not_open_the_socket(self):
        """A URL is logged everywhere, so ownership keys are not accepted in one."""
        identity = _mint_guest(sparks=88)

        socket = _connect(f"session_id={identity.owner_key}")

        self.assertFalse(socket.accepted)
        self.assertEqual(socket.close_code, 4001)

    def test_a_spent_ticket_cannot_be_replayed(self):
        ticket = _spark_pool_ticket(_mint_guest())

        first = _connect(f"ticket={ticket}")
        replay = _connect(f"ticket={ticket}")

        self.assertTrue(first.accepted)
        self.assertFalse(replay.accepted)
        self.assertEqual(replay.close_code, 4001)
