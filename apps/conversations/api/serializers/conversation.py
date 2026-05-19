from rest_framework import serializers

from apps.conversations.models import Conversation
from apps.users.api.serializers.user import UserSerializer


class ConversationSerializer(serializers.ModelSerializer):
    participants = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = "__all__"

    def get_participants(self, obj):
        participants = obj.participants.select_related("user")
        return UserSerializer(
            [p.user for p in participants], many=True, context=self.context
        ).data
