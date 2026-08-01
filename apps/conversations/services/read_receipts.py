"""Read receipts — marking how far a participant has read, and telling people.

Mirrors `delivery.py`. Delivery says "it reached your device"; reading says
"you looked at it". Both advance a per-participant pointer and both announce
to the same two audiences.
"""

from channels.db import database_sync_to_async

from apps.conversations.models import Message, Participant
from apps.conversations.services import fanout
from apps.conversations.services.realtime import broadcast_conversation_update


@database_sync_to_async
def _advance_pointer(user, conversation, message_id) -> bool:
    """Point *user* at *message_id*, refusing ids from other conversations.

    Without that check a client could mark any message in the database as
    read, including ones it is not allowed to see.
    """
    belongs = Message.objects.filter(
        id=message_id, conversation=conversation, is_deleted=False
    ).exists()
    if not belongs:
        return False

    Participant.objects.filter(user=user, conversation=conversation).update(
        last_read_message_id=message_id
    )
    return True


async def record_read(user, conversation, message_id: int) -> bool:
    """Mark *message_id* read for *user* and announce it.

    Returns False when the message is not part of this conversation, leaving
    the caller to report it.

    Two audiences, as with delivery: the room updates its seen indicator, and
    every participant's sidebar clears or keeps its unread dot.
    """
    if not await _advance_pointer(user, conversation, message_id):
        return False

    await fanout.to_conversation(
        conversation.id,
        fanout.READ_RECEIPT,
        user_id=user.id,
        message_id=message_id,
    )
    await database_sync_to_async(broadcast_conversation_update)(conversation)
    return True
