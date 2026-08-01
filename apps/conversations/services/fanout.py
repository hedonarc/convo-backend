"""The one place that puts an event on the channel layer.

Two strings decide where an event ends up, and neither is checked at import
time:

  * the **group name** — which sockets receive it
  * the **event type** — which consumer method runs, because the channel layer
    dispatches by looking up a method of that exact name

A typo or a renamed handler therefore fails silently at runtime. Collecting
both kinds of string here gives one list to keep in step with the consumers,
and `test_fanout.py` asserts every type below resolves to a real handler.

Every send is best-effort: a channel layer hiccup is logged and swallowed so
it cannot take down message persistence or socket handling.
"""

from functools import wraps
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def best_effort(fn):
    """Stop a failed announcement from taking down whatever triggered it.

    Persisting a message must succeed even when nobody can be told about it.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("Announcement %s failed", fn.__name__)

    return wrapper


# Event types. Each must match a handler method name on the consumer that
# receives it — see the module docstring.
NEW_MESSAGE = "chat_message_event"
TYPING = "typing_event"
READ_RECEIPT = "read_event"
DELIVERY_RECEIPT = "delivered_event"
MESSAGE_EDITED = "message_edited_event"
MESSAGE_DELETED = "message_deleted_event"
CONVERSATION_UPDATED = "conversation_updated_event"
PRESENCE_CHANGED = "presence_changed_event"
INVITE_ACCEPTED = "invite_accepted_event"

CONVERSATION_EVENTS = frozenset(
    {
        NEW_MESSAGE,
        TYPING,
        READ_RECEIPT,
        DELIVERY_RECEIPT,
        MESSAGE_EDITED,
        MESSAGE_DELETED,
    }
)
USER_EVENTS = frozenset({CONVERSATION_UPDATED, PRESENCE_CHANGED, INVITE_ACCEPTED})


def conversation_group(conversation_id) -> str:
    """Sockets watching one conversation."""
    return f"conversation_{conversation_id}"


def user_group(user_id) -> str:
    """Every socket one user has open, across all their conversations."""
    return f"user_{user_id}"


async def send(group: str, event_type: str, **payload) -> None:
    """Put one event on *group*, logging and swallowing any failure."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        await channel_layer.group_send(group, {"type": event_type, **payload})
    except Exception:
        logger.exception("Failed to send %s to %s", event_type, group)


async def to_conversation(conversation_id, event_type: str, **payload) -> None:
    """Announce to everyone currently watching a conversation."""
    await send(conversation_group(conversation_id), event_type, **payload)


async def to_users(user_ids, event_type: str, **payload) -> None:
    """Announce to each user's own group, wherever they are in the app."""
    for user_id in user_ids:
        await send(user_group(user_id), event_type, **payload)


send_sync = async_to_sync(send)
to_conversation_sync = async_to_sync(to_conversation)
to_users_sync = async_to_sync(to_users)
