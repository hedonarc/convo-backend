from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.conversations.models import ConversationInvite
from apps.users.api.serializers.user import UserSerializer

User = get_user_model()


class InviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.strip().lower()


class ConversationInviteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationInvite
        fields = ["email", "is_accepted", "updated_at"]
        read_only_fields = ["is_accepted", "updated_at"]


class InviteResolveSerializer(serializers.ModelSerializer):
    """
    Public-facing view of an invite, used by the Accept Invite screen before the
    user is authenticated. Returns only what's needed to render the screen —
    inviter identity, the canonical email, and whether that email already has
    an account so the frontend can route to login vs register.
    """

    inviter = UserSerializer(source="created_by", read_only=True)
    conversation_id = serializers.IntegerField(read_only=True)
    has_account = serializers.SerializerMethodField()

    class Meta:
        model = ConversationInvite
        fields = ["email", "inviter", "conversation_id", "is_accepted", "has_account"]
        read_only_fields = fields

    def get_has_account(self, obj) -> bool:
        return User.objects.filter(email__iexact=obj.email).exists()
