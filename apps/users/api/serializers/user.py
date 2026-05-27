from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=False, validators=[validate_password]
    )

    avatar = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "password",
            "avatar",
            "date_joined",
        ]
        # date_joined is the Django default — owned by the server, never
        # written by the client. Read-only here so the field shows up in
        # responses but can't be set via PATCH /api/me/.
        read_only_fields = ["date_joined"]

    def get_avatar(self, obj):
        request = self.context.get("request")
        if obj.avatar:
            return request.build_absolute_uri(obj.avatar.url)
        return None

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        if password:
            instance.set_password(password)
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)

        if instance.avatar:
            request = self.context.get("request")
            if request is not None:
                data["avatar"] = request.build_absolute_uri(instance.avatar.url)
            else:
                # No HTTP request in scope — typically a WebSocket fanout
                # (broadcast_conversation_update, notify_invite_accepted).
                # Fall back to the configured BACKEND_URL so the frontend
                # still receives an absolute URL it can fetch from any
                # origin, instead of a relative path it would resolve
                # against its own host and 404.
                backend_url = getattr(settings, "BACKEND_URL", "").rstrip("/")
                if backend_url:
                    data["avatar"] = f"{backend_url}{instance.avatar.url}"

        return data
