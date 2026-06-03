import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.conversations.api.serializers.message import MessageSerializer
from apps.conversations.constants import MAX_MESSAGE_LENGTH
from apps.conversations.queries import (
    db_create_message,
    get_conversation_for_user,
    get_conversation_if_participant,
    message_belongs_to_conversation,
    update_delivery_receipt,
    update_read_receipt,
)
from apps.conversations.services import presence
from apps.conversations.services.realtime import broadcast_conversation_update
from utils.translations import t

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncWebsocketConsumer):
    # Mapping of client-sent action names → handler method names.
    # Built once at class definition time instead of on every incoming frame.
    ACTION_HANDLERS = {
        "send_message": "handle_send_message",
        "typing": "handle_typing",
        "read": "handle_read_receipt",
    }

    # -------------------------------------------------------------------------
    # Connection Lifecycle
    # -------------------------------------------------------------------------

    async def connect(self):
        self.user = self.scope["user"]
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]

        # Defensive guard — JWTAuthMiddleware already blocks anonymous users
        # before reaching this point, but we keep this as an explicit safety net.
        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        # Authorization — user must be a participant in the conversation.
        self.conversation = await get_conversation_for_user(
            self.user, self.conversation_id
        )
        if self.conversation is None:
            logger.warning(
                "Access denied: User %s not in conversation %s",
                self.user.id,
                self.conversation_id,
            )
            await self.close(code=4003)
            return

        self.room_group_name = f"conversation_{self.conversation_id}"

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

    # -------------------------------------------------------------------------
    # Incoming Message Dispatcher
    # -------------------------------------------------------------------------

    async def receive(self, text_data):
        """
        Parse incoming frames and route to the appropriate action handler.

        Expected payload schema:
            { "action": "<action_name>", "data": { ... } }
        """
        try:
            payload = json.loads(text_data)
            action = payload.get("action")
            data = payload.get("data", {})
        except (json.JSONDecodeError, AttributeError):
            await self.send_error(t("websocket.invalid_json"))
            return

        handler_name = self.ACTION_HANDLERS.get(action)
        if handler_name:
            await getattr(self, handler_name)(data)
        else:
            await self.send_error(t("websocket.unknown_action").format(action=action))

    # -------------------------------------------------------------------------
    # Action Handlers
    # -------------------------------------------------------------------------

    async def handle_send_message(self, data: dict):
        """
        Persist a new message to the DB then broadcast it to the room group.
        """
        content = data.get("content", "").strip()
        if not content:
            await self.send_error(t("messages.empty_content"))
            return

        if len(content) > MAX_MESSAGE_LENGTH:
            await self.send_error(
                t("messages.too_long").format(max_length=MAX_MESSAGE_LENGTH)
            )
            return

        message = await db_create_message(self.conversation, self.user, content)

        # MessageSerializer gives us a consistent payload identical to the REST API.
        message_data = dict(MessageSerializer(message).data)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message_event",
                "message": message_data,
            },
        )

    async def handle_typing(self, data: dict):
        """
        Broadcast a typing indicator to all other participants in the room.
        """
        is_typing = bool(data.get("is_typing", True))

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "typing_event",
                "user_id": self.user.id,
                "is_typing": is_typing,
            },
        )

    async def handle_read_receipt(self, data: dict):
        """
        Mark a message as read for this user and broadcast the receipt to the group.

        Validates that:
        - message_id is a positive integer
        - the message exists and belongs to this conversation (prevents marking
          arbitrary or cross-conversation IDs as read)
        """
        message_id = data.get("message_id")
        if not isinstance(message_id, int) or message_id <= 0:
            await self.send_error("A valid integer message_id is required")
            return

        message_exists = await message_belongs_to_conversation(
            self.conversation, message_id
        )
        if not message_exists:
            await self.send_error(t("messages.not_found_in_conversation"))
            return

        await update_read_receipt(self.user, self.conversation, message_id)

        # In-conversation peers get the seen indicator update.
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "read_event",
                "user_id": self.user.id,
                "message_id": message_id,
            },
        )

        # Sidebar / unread-dot also needs to know — fan out the updated
        # conversation (with refreshed read_receipts) to every participant's
        # per-user channel. Wrapped via database_sync_to_async because the
        # sync broadcaster reads from the DB and uses async_to_sync internally.
        await database_sync_to_async(broadcast_conversation_update)(self.conversation)

    # -------------------------------------------------------------------------
    # Group Event Handlers
    # These methods are invoked by the channel layer when a group_send message
    # arrives. The method name must match the "type" key (dots → underscores).
    # -------------------------------------------------------------------------

    async def chat_message_event(self, event):
        """Deliver a new message to the connected WebSocket client.

        Auto-marks delivery for peer-sent messages — by the time this handler
        runs, the message has been pushed to this participant's socket, so we
        know the client received it. Skips own sends (we don't deliver to
        ourselves) and short-circuits if the pointer is already at/past this id.
        """
        message = event["message"]

        if message["sender"] != self.user.id:
            changed = await update_delivery_receipt(
                self.user, self.conversation, message["id"]
            )
            if changed:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "delivered_event",
                        "user_id": self.user.id,
                        "message_id": message["id"],
                    },
                )
                # Sidebar / sender's other tabs need the updated delivery
                # pointer — fan out the fresh conversation snapshot.
                await database_sync_to_async(broadcast_conversation_update)(
                    self.conversation
                )

        await self.send(text_data=json.dumps({"type": "new_message", "data": message}))

    async def delivered_event(self, event):
        """Deliver a delivery receipt to the connected client.

        Skipped for the participant who confirmed delivery — they don't need
        their own receipt back. Same pattern as `typing_event`.
        """
        if event["user_id"] == self.user.id:
            return

        await self.send(
            text_data=json.dumps(
                {
                    "type": "delivered_receipt",
                    "data": {
                        "user_id": event["user_id"],
                        "message_id": event["message_id"],
                    },
                }
            )
        )

    async def typing_event(self, event):
        """Deliver a typing indicator to the connected WebSocket client.

        Skipped for the sender — a user does not need to receive
        their own typing indicator back.
        """
        if event["user_id"] == self.user.id:
            return

        await self.send(
            text_data=json.dumps(
                {
                    "type": "typing",
                    "data": {
                        "user_id": event["user_id"],
                        "is_typing": event["is_typing"],
                    },
                }
            )
        )

    async def read_event(self, event):
        """Deliver a read receipt to the connected WebSocket client."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "read_receipt",
                    "data": {
                        "user_id": event["user_id"],
                        "message_id": event["message_id"],
                    },
                }
            )
        )

    async def message_edited_event(self, event):
        """Deliver an edited-message snapshot to the connected client."""
        await self.send(
            text_data=json.dumps({"type": "message_edited", "data": event["message"]})
        )

    async def message_deleted_event(self, event):
        """Deliver a soft-deleted-message snapshot to the connected client."""
        await self.send(
            text_data=json.dumps({"type": "message_deleted", "data": event["message"]})
        )

    # -------------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------------

    async def send_error(self, message: str):
        """Send a structured error frame to the connected client."""
        await self.send(text_data=json.dumps({"type": "error", "message": message}))


class UserConsumer(AsyncWebsocketConsumer):
    """
    One connection per logged-in user. Joins the per-user fanout group
    `user_<id>` so the server can push events spanning any conversation the
    user participates in — primarily for the sidebar's conversation list to
    update in real time without one socket per conversation.

    Also owns presence: on connect/disconnect the user is flipped to
    online/offline in Redis and the change is broadcast to conversation
    peers. The client may send a `visibility` action to mark the tab as
    away (hidden) or online (visible) without dropping the socket.
    """

    USER_ACTION_HANDLERS = {
        # Automatic per-tab signal — tab focused or blurred. Doesn't
        # override an explicit user intent set via `set_status`.
        "visibility": "handle_visibility",
        # Explicit user intent from the account menu. Records / clears a
        # user-level manual override that survives tab-focus events,
        # reconnects, and multi-tab churn.
        "set_status": "handle_set_status",
    }

    # Keep meaningfully smaller than presence.PRESENCE_TTL_SECONDS so the
    # connection's TTL never lapses between beats. With TTL=90s and
    # interval=30s, the connection survives two missed beats (30s grace).
    # Tight timings favour snappy "user appears online" UX over Redis
    # op budget; if you need to bring spend down later, raise both numbers
    # together (keeping interval < TTL/2).
    HEARTBEAT_INTERVAL_SECONDS = 30

    async def connect(self):
        self.user = self.scope["user"]

        # Defensive — JWTAuthMiddleware already blocks anonymous users.
        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.room_group_name = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Flip presence to online and fan out to peers. Wrapped via
        # database_sync_to_async because get_peer_user_ids hits the ORM.
        changed, _ = await database_sync_to_async(presence.mark_online)(
            self.user.id, self.channel_name
        )
        if changed:
            await database_sync_to_async(presence.broadcast_presence)(self.user.id)

        # Background task that refreshes the Redis TTL on this connection's
        # presence keys. Without it, an idle tab would be swept out by the
        # TTL guard and stop being counted in the user's status.
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        logger.info("User WebSocket connected: user=%s", self.user.id)

    async def disconnect(self, close_code):
        # Stop the heartbeat *first* so a refresh can't race with the
        # offline-mark below and resurrect the connection in Redis.
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
        """Refresh the per-connection TTL on a timer while the socket is open."""
        try:
            while True:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
                try:
                    await database_sync_to_async(presence.heartbeat)(
                        self.user.id, self.channel_name
                    )
                except Exception:
                    # Redis hiccups shouldn't kill the socket — the next
                    # beat (or any user event) will catch up.
                    logger.exception(
                        "Presence heartbeat failed for user=%s", self.user.id
                    )
        except asyncio.CancelledError:
            # Expected on disconnect — let it propagate so the task ends.
            raise

    async def receive(self, text_data):
        """Route inbound frames — currently just the `visibility` ping."""
        try:
            payload = json.loads(text_data)
            action = payload.get("action")
            data = payload.get("data", {})
        except (json.JSONDecodeError, AttributeError):
            await self.send(
                text_data=json.dumps(
                    {"type": "error", "message": t("websocket.invalid_json")}
                )
            )
            return

        handler_name = self.USER_ACTION_HANDLERS.get(action)
        if handler_name:
            await getattr(self, handler_name)(data)

    async def handle_visibility(self, data: dict):
        """Mark this socket online (tab visible) or away (tab hidden)."""
        visible = bool(data.get("visible", True))
        changed, _ = await database_sync_to_async(presence.mark_visibility)(
            self.user.id, self.channel_name, visible
        )
        if changed:
            await database_sync_to_async(presence.broadcast_presence)(self.user.id)

    async def handle_set_status(self, data: dict):
        """
        Apply an explicit user intent from the account menu (`Set as
        Away` / `Set as Online`). Distinct from `handle_visibility`,
        which fires for every tab focus/blur — those mustn't override
        manual choices.

        Always broadcasts, even when the user-level status didn't change
        (e.g. clicking "Online" while already online), so the originating
        client sees the menu's active-check confirm the action landed.
        Cost: one extra broadcast per explicit click — bounded by user
        behaviour, not by traffic.
        """
        status = data.get("status")
        if status not in {"online", "away"}:
            return

        if status == "away":
            await database_sync_to_async(presence.set_manual_away)(
                self.user.id, self.channel_name
            )
        else:
            await database_sync_to_async(presence.clear_manual_away)(
                self.user.id, self.channel_name
            )

        await database_sync_to_async(presence.broadcast_presence)(self.user.id)

    # ── Group event handlers ────────────────────────────────────────────────

    async def conversation_updated_event(self, event):
        """Push a conversation snapshot to the connected client.

        Also doubles as the "delivered" trigger when the user isn't actively
        subscribed to the conversation's room (e.g. they're viewing a
        different chat). In that case the per-conversation `chat_message_event`
        handler — which normally bumps `last_delivered_message_id` — never
        runs for this user, so the sender would be stuck on the single-tick
        "Sent" state. We catch up by marking delivery here whenever the
        incoming snapshot brings a fresh peer message.

        Idempotency: `update_delivery_receipt` only writes when the incoming
        id is strictly newer, so the secondary `broadcast_conversation_update`
        we fire on a successful bump cannot recurse — the next pass through
        this handler sees the already-bumped pointer and short-circuits.
        """
        conversation = event["conversation"]
        last_message = conversation.get("last_message")

        if (
            last_message
            and not last_message.get("is_deleted")
            and last_message.get("sender") != self.user.id
        ):
            conversation_id = conversation["id"]
            message_id = last_message["id"]
            changed = await update_delivery_receipt(
                self.user, conversation_id, message_id
            )
            if changed:
                # Tell anyone currently in the conversation room (most
                # importantly: the sender) about the delivery so their
                # MessagePane updates without waiting for the next snapshot.
                await self.channel_layer.group_send(
                    f"conversation_{conversation_id}",
                    {
                        "type": "delivered_event",
                        "user_id": self.user.id,
                        "message_id": message_id,
                    },
                )
                # Re-broadcast the snapshot so every per-user channel —
                # including the sender's — sees the updated delivery pointer
                # in `delivery_receipts`. We skip forwarding the *stale*
                # snapshot below since a fresh one is already on the wire.
                refreshed = await get_conversation_if_participant(
                    self.user, conversation_id
                )
                if refreshed is not None:
                    await database_sync_to_async(broadcast_conversation_update)(
                        refreshed
                    )
                    return

        await self.send(
            text_data=json.dumps({"type": "conversation_updated", "data": conversation})
        )

    async def presence_changed_event(self, event):
        """Deliver a peer's presence transition to this client."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "presence_changed",
                    "data": {
                        "user_id": event["user_id"],
                        "status": event["status"],
                        "last_seen_at": event["last_seen_at"],
                    },
                }
            )
        )

    async def invite_accepted_event(self, event):
        """Push an inviter-only notification frame when an invitee accepts.

        Carries the acceptor's public profile + the conversation id so the
        frontend can render a toast ("X joined the conversation") and
        optionally focus the new conversation in the sidebar.
        """
        await self.send(
            text_data=json.dumps(
                {
                    "type": "invite_accepted",
                    "data": {
                        "acceptor": event["acceptor"],
                        "conversation_id": event["conversation_id"],
                    },
                }
            )
        )
