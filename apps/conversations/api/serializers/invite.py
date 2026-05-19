from rest_framework import serializers

from apps.conversations.models import ConversationInvite


class InviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class ConversationInviteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationInvite
        fields = ["email", "is_accepted", "updated_at"]
        read_only_fields = ["is_accepted", "updated_at"]
