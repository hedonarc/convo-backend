"""Delivery receipts — the one place that decides a message has been delivered.

A participant's delivery pointer advances when a peer's message reaches any of
their sockets. Which socket it arrives on is an accident of what the user is
looking at, so both consumers route through `record_delivery` rather than each
carrying its own copy of the rule.
"""

import logging

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer

from apps.conversations.models import Conversation, Participant
from apps.conversations.services.realtime import broadcast_conversation_update

logger = logging.getLogger(__name__)


def is_peer_message(message: dict | None, user) -> bool:
    """Is *message* something *user* receiving would count as a delivery?"""
    return bool(
        message and not message.get("is_deleted") and message.get("sender") != user.id
    )


@database_sync_to_async
def _advance_pointer(user, conversation_id, message_id) -> Conversation | None:
    """Move the pointer forward only, returning the conversation if it moved.

    The `exclude` is what makes this idempotent: a pointer already at or past
    *message_id* updates no rows, so callers can fire this as often as they
    like without producing a second announcement.
    """
    moved = (
        Participant.objects.filter(user=user, conversation_id=conversation_id)
        .exclude(last_delivered_message_id__gte=message_id)
        .update(last_delivered_message_id=message_id)
    )
    if not moved:
        return None

    return (
        Conversation.objects.select_related("last_message")
        .filter(id=conversation_id, participants__user=user)
        .first()
    )


async def record_delivery(user, conversation_id: int, message_id: int) -> bool:
    """Mark *message_id* delivered for *user*, announcing it if that is news.

    Returns True when the pointer moved, which also means a fresh conversation
    snapshot is already on the wire and the caller need not send its own.

    Announcing is part of recording, not a separate step callers can forget:
    the conversation room gets the receipt so the sender's ticks update, and
    every participant's sidebar gets a snapshot carrying the new pointer.

    Termination: those snapshots come back through this function on each
    participant's socket, but by then the pointer is already at *message_id*,
    so the second pass moves nothing and stops.
    """
    conversation = await _advance_pointer(user, conversation_id, message_id)
    if conversation is None:
        return False

    channel_layer = get_channel_layer()
    if channel_layer is not None:
        try:
            await channel_layer.group_send(
                f"conversation_{conversation_id}",
                {
                    "type": "delivered_event",
                    "user_id": user.id,
                    "message_id": message_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to broadcast delivery receipt for user=%s message=%s",
                user.id,
                message_id,
            )

    await database_sync_to_async(broadcast_conversation_update)(conversation)
    return True
