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
from apps.conversations.models import Conversation

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
