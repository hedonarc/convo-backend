from apps.conversations.models import Message
from apps.conversations.services.realtime import broadcast_conversation_update


# TODO: @msulemanb For improvement wrap this function in a transaction
#       So if message creation fails,
#       the conversation last message pointer is not updated
def create_message(conversation, sender, content):
    message = Message.objects.create(
        conversation=conversation, sender=sender, content=content
    )

    # 🔥 update conversation last message pointer
    conversation.last_message = message
    conversation.save(update_fields=["last_message", "updated_at"])

    # Notify every participant's per-user channel so their sidebar moves this
    # conversation to the top and shows the new last-message preview.
    broadcast_conversation_update(conversation)

    return message
