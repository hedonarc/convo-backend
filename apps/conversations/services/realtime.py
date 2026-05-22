"""
Realtime fanout helpers — push conversation-level updates to every
participant's per-user channel.

Synchronous wrapper around `channel_layer.group_send` for use from regular
Django views / services. Failures are logged and swallowed so a Redis hiccup
can't break message persistence.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.conversations.api.serializers.conversation import ConversationSerializer
from apps.conversations.api.serializers.message import MessageSerializer
from apps.conversations.models import Conversation, Message

logger = logging.getLogger(__name__)


def broadcast_conversation_update(conversation: Conversation) -> None:
    """
    Send the conversation snapshot to every participant's `user_<id>` group.

    Called from `create_message`, direct-conversation creation, and invite
    acceptance — anywhere the conversation list might need to update for any
    participant.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    try:
        payload = ConversationSerializer(conversation).data
        participant_ids = list(
            conversation.participants.values_list("user_id", flat=True)
        )
        send = async_to_sync(channel_layer.group_send)
        for user_id in participant_ids:
            send(
                f"user_{user_id}",
                {
                    "type": "conversation_updated_event",
                    "conversation": payload,
                },
            )
    except Exception:
        logger.exception(
            "Failed to broadcast conversation update for conversation=%s",
            conversation.id,
        )


def _broadcast_message_event(
    conversation_id: int, message: Message, event_type: str
) -> None:
    """Shared body for message_edited / message_deleted broadcasts."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    try:
        payload = MessageSerializer(message).data
        async_to_sync(channel_layer.group_send)(
            f"conversation_{conversation_id}",
            {"type": event_type, "message": payload},
        )
    except Exception:
        logger.exception(
            "Failed to broadcast %s for conversation=%s message=%s",
            event_type,
            conversation_id,
            message.id,
        )


def broadcast_message_edited(conversation_id: int, message: Message) -> None:
    """Push a message_edited frame to every client in the conversation room."""
    _broadcast_message_event(conversation_id, message, "message_edited_event")


def broadcast_message_deleted(conversation_id: int, message: Message) -> None:
    """Push a message_deleted frame to every client in the conversation room."""
    _broadcast_message_event(conversation_id, message, "message_deleted_event")
