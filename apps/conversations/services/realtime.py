"""What the app announces, in domain terms.

Each function answers "who needs to know, and what do they get?". How the
event reaches the wire is `fanout`'s problem.
"""

from apps.conversations.api.serializers.conversation import ConversationSerializer
from apps.conversations.api.serializers.message import MessageSerializer
from apps.conversations.models import Conversation, ConversationInvite, Message
from apps.conversations.services import fanout
from apps.users.api.serializers.user import UserSerializer


@fanout.best_effort
def broadcast_conversation_update(conversation: Conversation) -> None:
    """Move this conversation to the top of every participant's sidebar."""
    participant_ids = list(conversation.participants.values_list("user_id", flat=True))
    fanout.to_users_sync(
        participant_ids,
        fanout.CONVERSATION_UPDATED,
        conversation=ConversationSerializer(conversation).data,
    )


@fanout.best_effort
def notify_invite_accepted(invite: ConversationInvite, acceptor_user) -> None:
    """Tell the inviter alone that someone took them up on it.

    Distinct from `broadcast_conversation_update`, which reaches everyone —
    the acceptor should not be told about themselves joining.
    """
    fanout.to_users_sync(
        [invite.created_by_id],
        fanout.INVITE_ACCEPTED,
        acceptor=UserSerializer(acceptor_user).data,
        conversation_id=invite.conversation_id,
    )


@fanout.best_effort
def broadcast_message_edited(conversation_id: int, message: Message) -> None:
    """Replace an edited message for everyone in the conversation."""
    fanout.to_conversation_sync(
        conversation_id,
        fanout.MESSAGE_EDITED,
        message=MessageSerializer(message).data,
    )


@fanout.best_effort
def broadcast_message_deleted(conversation_id: int, message: Message) -> None:
    """Replace a soft-deleted message for everyone in the conversation."""
    fanout.to_conversation_sync(
        conversation_id,
        fanout.MESSAGE_DELETED,
        message=MessageSerializer(message).data,
    )
