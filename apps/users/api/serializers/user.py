from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=False, validators=[validate_password]
    )

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
        read_only_fields = ["date_joined"]

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
