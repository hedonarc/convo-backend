from channels.db import database_sync_to_async

from apps.conversations.models import Conversation, Participant
from apps.conversations.services.realtime import broadcast_conversation_update


def conversation_for_participant(user, conversation_id) -> Conversation | None:
    """The conversation, if *user* is a participant — otherwise None.

    The authorization check behind every socket connection and every receipt.
    """
    try:
        participant = Participant.objects.select_related("conversation").get(
            user=user, conversation_id=conversation_id
        )
        return participant.conversation
    except Participant.DoesNotExist:
        return None


conversation_for_participant_async = database_sync_to_async(
    conversation_for_participant
)


def generate_conversation_key(user1, user2):
    ids = sorted([user1.id, user2.id])
    return f"{ids[0]}_{ids[1]}"


def get_or_create_direct_conversation(sender, recipient):
    # SELF CHAT
    if sender.id == recipient.id:
        conversation, is_created = Conversation.objects.get_or_create(
            created_by=sender, conversation_key=f"self_{sender.id}"
        )

        Participant.objects.get_or_create(user=sender, conversation=conversation)

        return conversation, is_created

    # DM
    key = generate_conversation_key(sender, recipient)

    conversation, is_created = Conversation.objects.get_or_create(
        conversation_key=key, defaults={"created_by": sender}
    )

    if is_created:
        Participant.objects.bulk_create(
            [
                Participant(user=sender, conversation=conversation),
                Participant(user=recipient, conversation=conversation),
            ]
        )
        # Push the brand-new conversation to both participants' sidebars.
        broadcast_conversation_update(conversation)

    return conversation, is_created
