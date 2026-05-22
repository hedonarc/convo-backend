from rest_framework import serializers

from apps.conversations.api.serializers.invite import ConversationInviteSerializer
from apps.conversations.api.serializers.message import MessageSerializer
from apps.conversations.models import Conversation
from apps.users.api.serializers.user import UserSerializer


class ConversationSerializer(serializers.ModelSerializer):
    invitation = ConversationInviteSerializer(read_only=True)
    participants = serializers.SerializerMethodField()
    last_message = MessageSerializer(read_only=True)
    # Per-participant read pointer:
    # { "<user_id>": <last_read_message_id | null>, ... }
    # Used by the frontend to compute the unread dot for the current user and
    # the "Seen" indicator for other participants on own messages.
    read_receipts = serializers.SerializerMethodField()
    # Per-participant delivery pointer — drives the gray double-tick on own
    # messages. Always ≤ read_receipts[user] once the user has read.
    delivery_receipts = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "created_by",
            "conversation_key",
            "last_message",
            "created_at",
            "updated_at",
            "invitation",
            "participants",
            "read_receipts",
            "delivery_receipts",
        ]

    def get_participants(self, obj):
        participants = obj.participants.select_related("user")
        return UserSerializer(
            [p.user for p in participants], many=True, context=self.context
        ).data

    def get_read_receipts(self, obj):
        return {str(p.user_id): p.last_read_message_id for p in obj.participants.all()}

    def get_delivery_receipts(self, obj):
        return {
            str(p.user_id): p.last_delivered_message_id for p in obj.participants.all()
        }
