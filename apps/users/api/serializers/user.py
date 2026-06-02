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
            url = instance.avatar.url

            data["avatar"] = (
                request.build_absolute_uri(url)
                if request
                else f"{settings.BACKEND_URL.rstrip('/')}{url}"
            )

        return data
