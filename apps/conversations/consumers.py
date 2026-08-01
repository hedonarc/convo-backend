import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.conversations.constants import MAX_MESSAGE_LENGTH
from apps.conversations.services import fanout, presence
from apps.conversations.services.conversation_service import (
    conversation_for_participant_async,
)
from apps.conversations.services.delivery import is_peer_message, record_delivery
from apps.conversations.services.message_service import post_message_async
from apps.conversations.services.read_receipts import record_read
from utils.translations import t

logger = logging.getLogger(__name__)


class ClientSocket:
    """How both consumers talk to a browser."""

    async def reject(self, code: int) -> None:
        """Close with *code* after accepting, so the browser can read it.

        Daphne answers a pre-accept close with an HTTP 403 handshake rejection
        and discards the application code, leaving the browser with 1006.
        """
        await self.accept()
        await self.close(code=code)

    async def send_frame(self, frame_type: str, data) -> None:
        """Write one typed frame to this socket."""
        await self.send(text_data=json.dumps({"type": frame_type, "data": data}))

    async def send_error(self, message: str) -> None:
        """Errors carry their text at the top level rather than under `data`."""
        await self.send(text_data=json.dumps({"type": "error", "message": message}))

    async def route(self, text_data: str, handlers: dict) -> None:
        """Parse an inbound frame and hand it to the handler *handlers* names."""
        try:
            payload = json.loads(text_data)
            action = payload.get("action")
            data = payload.get("data", {})
        except (json.JSONDecodeError, AttributeError):
            await self.send_error(t("websocket.invalid_json"))
            return

        handler_name = handlers.get(action)
        if handler_name is None:
            await self.send_error(t("websocket.unknown_action").format(action=action))
            return

        await getattr(self, handler_name)(data)


class ConversationConsumer(ClientSocket, AsyncWebsocketConsumer):
    """One connection per open conversation."""

    ACTION_HANDLERS = {
        "send_message": "handle_send_message",
        "typing": "handle_typing",
        "read": "handle_read_receipt",
    }

    async def connect(self):
        self.user = self.scope["user"]
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]

        # JWTAuthMiddleware already blocks anonymous users; this is a net.
        if not self.user.is_authenticated:
            await self.reject(4001)
            return

        self.conversation = await conversation_for_participant_async(
            self.user, self.conversation_id
        )
        if self.conversation is None:
            logger.warning(
                "Access denied: User %s not in conversation %s",
                self.user.id,
                self.conversation_id,
            )
            await self.reject(4003)
            return

        self.room_group_name = fanout.conversation_group(self.conversation_id)
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        logger.info(
            "WebSocket connected: user %s joined conversation %s",
            self.user.id,
            self.conversation_id,
        )

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )
            logger.info(
                "WebSocket disconnected: user %s left conversation %s (code=%s)",
                self.user.id,
                self.conversation_id,
                close_code,
            )

    async def receive(self, text_data):
        await self.route(text_data, self.ACTION_HANDLERS)

    # ── Actions from the client ─────────────────────────────────────────────

    async def handle_send_message(self, data: dict):
        content = data.get("content", "").strip()
        if not content:
            await self.send_error(t("messages.empty_content"))
            return

        if len(content) > MAX_MESSAGE_LENGTH:
            await self.send_error(
                t("messages.too_long").format(max_length=MAX_MESSAGE_LENGTH)
            )
            return

        await post_message_async(self.conversation, self.user, content)

    async def handle_typing(self, data: dict):
        await fanout.to_conversation(
            self.conversation.id,
            fanout.TYPING,
            user_id=self.user.id,
            is_typing=bool(data.get("is_typing", True)),
        )

    async def handle_read_receipt(self, data: dict):
        message_id = data.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0:
            await self.send_error("A valid integer message_id is required")
            return

        if not await record_read(self.user, self.conversation, message_id):
            await self.send_error(t("messages.not_found_in_conversation"))

    # ── Events from the channel layer ───────────────────────────────────────

    async def chat_message_event(self, event):
        message = event["message"]

        if is_peer_message(message, self.user):
            await record_delivery(self.user, self.conversation.id, message["id"])

        await self.send_frame("new_message", message)

    async def delivered_event(self, event):
        """Skipped for the participant who confirmed it — no echo to self."""
        if event["user_id"] == self.user.id:
            return

        await self.send_frame(
            "delivered_receipt",
            {"user_id": event["user_id"], "message_id": event["message_id"]},
        )

    async def typing_event(self, event):
        """Skipped for the sender — no echo to self."""
        if event["user_id"] == self.user.id:
            return

        await self.send_frame(
            "typing",
            {"user_id": event["user_id"], "is_typing": event["is_typing"]},
        )

    async def read_event(self, event):
        await self.send_frame(
            "read_receipt",
            {"user_id": event["user_id"], "message_id": event["message_id"]},
        )

    async def message_edited_event(self, event):
        await self.send_frame("message_edited", event["message"])

    async def message_deleted_event(self, event):
        await self.send_frame("message_deleted", event["message"])


class UserConsumer(ClientSocket, AsyncWebsocketConsumer):
    """One connection per logged-in user, for everything not tied to one chat.

    Carries the sidebar's cross-conversation updates, and owns presence: the
    socket's lifetime is what makes a user online, and a `visibility` or
    `set_status` action moves them between online and away without dropping it.
    """

    USER_ACTION_HANDLERS = {
        "visibility": "handle_visibility",
        "set_status": "handle_set_status",
    }

    # Must stay under half of presence.PRESENCE_TTL_SECONDS so a single missed
    # beat cannot sweep a live connection. See docs/adr/0001-presence-timings.
    HEARTBEAT_INTERVAL_SECONDS = 30

    async def connect(self):
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.reject(4001)
            return

        self.room_group_name = fanout.user_group(self.user.id)
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        changed, _ = await database_sync_to_async(presence.mark_online)(
            self.user.id, self.channel_name
        )
        if changed:
            await database_sync_to_async(presence.broadcast_presence)(self.user.id)

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        logger.info("User WebSocket connected: user=%s", self.user.id)

    async def disconnect(self, close_code):
        # Cancel first, or a beat in flight can resurrect the connection key
        # after the offline mark below has removed it.
        task = getattr(self, "_heartbeat_task", None)
        if task is not None:
            task.cancel()

        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )
            changed, _ = await database_sync_to_async(presence.mark_offline)(
                self.user.id, self.channel_name
            )
            if changed:
                await database_sync_to_async(presence.broadcast_presence)(self.user.id)
            logger.info(
                "User WebSocket disconnected: user=%s (code=%s)",
                self.user.id,
                close_code,
            )

    async def _heartbeat_loop(self):
        """Keep this connection's presence key alive while the socket is open."""
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
            try:
                await database_sync_to_async(presence.heartbeat)(
                    self.user.id, self.channel_name
                )
            except Exception:
                logger.exception("Presence heartbeat failed for user=%s", self.user.id)

    async def receive(self, text_data):
        await self.route(text_data, self.USER_ACTION_HANDLERS)

    # ── Actions from the client ─────────────────────────────────────────────

    async def handle_visibility(self, data: dict):
        """Tab focused or blurred. Never overrides an explicit `set_status`."""
        changed, _ = await database_sync_to_async(presence.mark_visibility)(
            self.user.id, self.channel_name, bool(data.get("visible", True))
        )
        if changed:
            await database_sync_to_async(presence.broadcast_presence)(self.user.id)

    async def handle_set_status(self, data: dict):
        """Explicit choice from the account menu, which outranks tab focus.

        Broadcasts even when the status did not change, so the menu's
        active-check confirms the click landed.
        """
        status = data.get("status")
        if status not in {"online", "away"}:
            return

        apply = (
            presence.set_manual_away if status == "away" else presence.clear_manual_away
        )
        await database_sync_to_async(apply)(self.user.id, self.channel_name)
        await database_sync_to_async(presence.broadcast_presence)(self.user.id)

    # ── Events from the channel layer ───────────────────────────────────────

    async def conversation_updated_event(self, event):
        """Push a conversation snapshot to the client.

        Also the only place a delivery pointer can advance for someone reading
        a different chat — without it the sender sits on a single tick.
        """
        conversation = event["conversation"]
        last_message = conversation.get("last_message")

        if is_peer_message(last_message, self.user) and await record_delivery(
            self.user, conversation["id"], last_message["id"]
        ):
            # A fresher snapshot is already on the wire; ours is now stale.
            return

        await self.send_frame("conversation_updated", conversation)

    async def presence_changed_event(self, event):
        await self.send_frame(
            "presence_changed",
            {
                "user_id": event["user_id"],
                "status": event["status"],
                "last_seen_at": event["last_seen_at"],
            },
        )

    async def invite_accepted_event(self, event):
        """Inviter-only: someone accepted, so the frontend can toast it."""
        await self.send_frame(
            "invite_accepted",
            {
                "acceptor": event["acceptor"],
                "conversation_id": event["conversation_id"],
            },
        )
