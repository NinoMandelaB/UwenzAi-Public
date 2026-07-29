import json
import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from capp.classes.guest.guest_identity_service import GuestIdentityService
from capp.classes.guest.ws_ticket_service import GuestWebSocketTicketService
from capp.models import GuestTokenQuota, GuestWebSocketTicket, SparkPool
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


def _query_params(scope):
    return {key: values[0] for key, values in parse_qs(scope.get("query_string", b"").decode()).items()}


def _legacy_ws_session_id_allowed() -> bool:
    """
    Whether a raw guest session ID in the query string is still accepted.

    A WebSocket URL is logged by every proxy it passes through, so the only
    reason this path exists is that browsers already in the wild connect that
    way. It is off by default and additionally dies with the legacy migration
    cutoff, so it cannot outlive the transition even if the flag is left on.
    """
    if not settings.GUEST_LEGACY_WS_SESSION_ID_ENABLED:
        return False
    enabled, _ = GuestIdentityService.legacy_migration_state()
    return enabled


@database_sync_to_async
def _consume_guest_ticket(raw_ticket, channel, conversation_id=None):
    """Spend a single-use ticket and return the guest's stable ownership key."""
    identity = GuestWebSocketTicketService.consume(raw_ticket, channel, conversation_id)
    return identity.owner_key if identity is not None else None


class SparkPoolConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that pushes spark_pool balance updates to authenticated
    users (via a short-lived JWT) and guests (via a single-use ticket).
    """

    async def connect(self):
        params = _query_params(self.scope)
        token = params.get("token")

        if token:
            # Authenticated user path
            try:
                access_token = AccessToken(token)
                user_id = access_token.get("user_id")
                self.user = await User.objects.aget(pk=user_id)
                self.is_guest = False
                self.room_group_name = f"spark_pool_{self.user.user_id}"
                logger.info(
                    "SparkPoolConsumer: authenticated user %s connecting",
                    self.user.user_id,
                )
            except TokenError as e:
                logger.warning("SparkPoolConsumer: Token validation failed: %s", str(e))
                await self.close(code=4003)
                return
            except User.DoesNotExist:
                logger.warning("SparkPoolConsumer: User not found for token")
                await self.close(code=4003)
                return
            except Exception:
                logger.exception("SparkPoolConsumer: Unexpected error during token validation")
                await self.close(code=4003)
                return
        else:
            owner_key = await self._resolve_guest(params)
            if owner_key is None:
                await self.close(code=4001)
                return
            self.user = None
            self.is_guest = True
            self.session_id = owner_key
            self.room_group_name = f"guest_spark_pool_{owner_key}"
            logger.info("SparkPoolConsumer: guest connecting")

        # Join the group and accept
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Send initial balance immediately after connection
        if self.is_guest:
            try:
                quota = await GuestTokenQuota.objects.aget(session_id=self.session_id)
                balance = quota.sparks
            except GuestTokenQuota.DoesNotExist:
                balance = 0
        else:
            try:
                spark_pool = await SparkPool.objects.aget(user=self.user)
                balance = spark_pool.sparks
            except SparkPool.DoesNotExist:
                balance = 0

        await self.send(
            text_data=json.dumps(
                {
                    "type": "spark_pool_balance",
                    "sparks": balance,
                }
            )
        )
        logger.info(
            "SparkPoolConsumer: sent initial balance %d to %s",
            balance,
            self.room_group_name,
        )

    async def _resolve_guest(self, params):
        ticket = params.get("ticket")
        if ticket:
            owner_key = await _consume_guest_ticket(ticket, GuestWebSocketTicket.Channel.SPARK_POOL)
            if owner_key is None:
                logger.warning("SparkPoolConsumer: guest ticket rejected")
            return owner_key

        legacy_session_id = params.get("session_id")
        if legacy_session_id and _legacy_ws_session_id_allowed():
            logger.info("SparkPoolConsumer: guest connecting with legacy session id")
            return legacy_session_id

        logger.warning("SparkPoolConsumer: no token or ticket, closing")
        return None

    async def receive(self, text_data=None, bytes_data=None):
        """
        Called when a message is received from the client.
        We don't need to process anything, but defining this method
        keeps the consumer alive and prevents immediate closure.
        """
        # Optionally handle ping/pong if needed

    async def spark_pool_balance(self, event):
        """
        Handler for balance updates pushed from the backend.
        """
        await self.send(
            text_data=json.dumps(
                {
                    "type": "spark_pool_balance",
                    "sparks": event["sparks"],
                }
            )
        )

    async def disconnect(self, close_code):
        """
        Called when the WebSocket is closed.
        """
        logger.info("SparkPoolConsumer disconnect: code=%s", close_code)

        # Only attempt to leave the group if we successfully joined
        if hasattr(self, "room_group_name") and hasattr(self, "channel_layer"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # Log disconnection details
        if hasattr(self, "is_guest") and self.is_guest:
            logger.info("Guest spark_pool disconnected")
        elif hasattr(self, "user") and self.user:
            logger.info("User spark_pool %s disconnected", self.user.user_id)
        else:
            logger.info("SparkPoolConsumer disconnected without proper state.")


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.room_group_name = f"conversation_{self.conversation_id}"

        params = _query_params(self.scope)
        token = params.get("token")

        if token:
            # Authenticated user
            try:
                access_token = AccessToken(token)
                user_id = access_token.get("user_id")
                self.user = await User.objects.aget(pk=user_id)
                self.is_guest = False  # ← explicit set
            except (TokenError, InvalidToken, User.DoesNotExist):
                await self.close(code=4003)
                return
        else:
            owner_key = await self._resolve_guest(params)
            if owner_key is None:
                await self.close(code=4001)
                return
            self.session_id = owner_key
            self.user = None
            self.is_guest = True  # ← explicit set

        # Verify conversation access
        if not await self._conversation_belongs_to_user():
            await self.close(code=4004)
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def _resolve_guest(self, params):
        ticket = params.get("ticket")
        if ticket:
            # The ticket is bound to this conversation, so a ticket minted for
            # another chat cannot be replayed here.
            owner_key = await _consume_guest_ticket(
                ticket,
                GuestWebSocketTicket.Channel.CHAT,
                self.conversation_id,
            )
            if owner_key is None:
                logger.warning("ChatConsumer: guest ticket rejected")
            return owner_key

        legacy_session_id = params.get("session_id")
        if legacy_session_id and _legacy_ws_session_id_allowed():
            logger.info("ChatConsumer: guest connecting with legacy session id")
            return legacy_session_id

        logger.warning("ChatConsumer: no token or ticket, closing")
        return None

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        # A rejected connection never reached the auth branches, so the identity
        # attributes may be absent here.
        if getattr(self, "is_guest", True):
            logger.info("Guest disconnected from conversation %s", self.conversation_id)
        else:
            logger.info(
                "User %s disconnected from conversation %s",
                self.user.username if self.user else "unknown",
                self.conversation_id,
            )

    # Receive message from WebSocket (if user sends via WS – optional)
    async def receive(self, text_data=None, bytes_data=None):
        # For now we ignore incoming messages – the user sends via HTTP POST.
        pass

    # Handler for 'chat.message' event sent by the Celery task
    async def chat_message(self, event):
        message = event["message"]
        await self.send(text_data=json.dumps(message))

    async def _conversation_belongs_to_user(self):
        from capp.models import Conversation

        try:
            conv = await Conversation.objects.aget(pk=self.conversation_id)
            if self.is_guest:
                return conv.guest_session_id == self.session_id
            else:
                return conv.user_id == self.user.user_id
        except Conversation.DoesNotExist:
            return False


class PingPongConsumer(AsyncWebsocketConsumer):
    """
    A simple WebSocket consumer used to verify real-time communication.

    This consumer establishes a WebSocket connection and responds to
    incoming messages. When a valid JSON payload with {"message": "ping"}
    is received, it responds with {"message": "pong"}.

    It also handles invalid JSON input and unknown commands gracefully.

    Purpose:
    - Validate WebSocket routing and ASGI configuration
    - Serve as a baseline for building more complex chat consumers
    """

    async def connect(self):
        try:
            await self.accept()
            await self.send(text_data=json.dumps({"message": "Connection established!"}))
        except Exception:
            await self.close(code=1011)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)

            if data.get("message") == "ping":
                await self.send(text_data=json.dumps({"message": "pong from WebSocket!"}))
            else:
                await self.send(text_data=json.dumps({"error": "Unknown command"}))

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({"error": "Invalid JSON"}))

        except Exception as e:
            await self.send(text_data=json.dumps({"error": f"Internal error: {str(e)}"}))

    async def disconnect(self, close_code):
        pass
