from rest_framework import serializers

from apps.conversations.api.serializers.invite import ConversationInviteSerializer
from apps.conversations.models import Conversation
from apps.users.api.serializers.user import UserSerializer


class ConversationSerializer(serializers.ModelSerializer):
    invitation = ConversationInviteSerializer(read_only=True)
    participants = serializers.SerializerMethodField()

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
        ]

    def get_participants(self, obj):
        participants = obj.participants.select_related("user")
        return UserSerializer(
            [p.user for p in participants], many=True, context=self.context
        ).data
