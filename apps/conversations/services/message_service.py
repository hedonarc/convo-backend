from channels.db import database_sync_to_async
from django.db import transaction

from apps.conversations.api.serializers.message import MessageSerializer
from apps.conversations.models import Message
from apps.conversations.services import fanout
from apps.conversations.services.realtime import broadcast_conversation_update


@fanout.best_effort
def announce_new_message(conversation, message: Message) -> None:
    """Everything that goes on the wire when a message is sent.

    Two audiences: whoever has the conversation open, and every participant's
    sidebar. Both live here so the full effect of a send is readable in one
    place.
    """
    fanout.to_conversation_sync(
        conversation.id,
        fanout.NEW_MESSAGE,
        message=MessageSerializer(message).data,
    )
    broadcast_conversation_update(conversation)


def post_message(conversation, sender, content: str) -> Message:
    """Persist a message and announce it once the write is durable.

    The two writes move together, and announcing waits for commit — a
    rolled-back transaction can no longer leave clients holding a message
    that does not exist, or a sidebar pointing at one.
    """
    with transaction.atomic():
        message = Message.objects.create(
            conversation=conversation, sender=sender, content=content
        )
        conversation.last_message = message
        conversation.save(update_fields=["last_message", "updated_at"])
        transaction.on_commit(lambda: announce_new_message(conversation, message))

    return message


post_message_async = database_sync_to_async(post_message)
