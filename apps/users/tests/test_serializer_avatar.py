"""
Regression: WebSocket fanout (broadcast_conversation_update,
notify_invite_accepted) calls UserSerializer without a `request` context.
Before the fix, the serializer left the avatar as the default ImageField
representation (`/media/avatars/...`), which the frontend resolved against
its own origin and 404'd — making peer avatars appear to "vanish" the
moment a `conversation_updated` event arrived.

These tests pin the contract: with or without a request, an absolute URL
comes back as long as the user actually has an avatar.
"""

import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.images import ImageFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIRequestFactory

from apps.users.api.serializers.user import UserSerializer

User = get_user_model()


def _tiny_png() -> ImageFile:
    """A 1×1 white PNG, just enough to satisfy ImageField validation."""
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, format="PNG")
    buf.seek(0)
    return ImageFile(buf, name="test.png")


_TMP_MEDIA = tempfile.mkdtemp(prefix="convo_avatar_test_")


@override_settings(MEDIA_ROOT=_TMP_MEDIA, BACKEND_URL="http://backend.example")
class UserSerializerAvatarUrlTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username="u", email="u@example.com", password="x"
        )
        self.user.avatar.save("test.png", _tiny_png(), save=True)

    def test_no_request_falls_back_to_backend_url(self):
        """
        Without a request in context (the WS fanout path), the serializer
        must still emit an absolute URL — built from settings.BACKEND_URL.
        """
        data = UserSerializer(self.user).data

        self.assertTrue(
            data["avatar"].startswith("http://backend.example/"),
            f"expected backend-prefixed URL, got {data['avatar']!r}",
        )
        self.assertEqual(
            data["avatar"], f"http://backend.example{self.user.avatar.url}"
        )

    def test_with_request_uses_request_host(self):
        """
        The original REST behavior — request.build_absolute_uri — keeps
        working when a request is in context.
        """
        factory = APIRequestFactory()
        request = factory.get("/api/me/", HTTP_HOST="api.example.com")

        data = UserSerializer(self.user, context={"request": request}).data

        self.assertTrue(
            data["avatar"].startswith("http://api.example.com/"),
            f"expected request-derived URL, got {data['avatar']!r}",
        )

    def test_no_avatar_serializes_as_null(self):
        """Users without an avatar still come back with a null avatar field."""
        other = User.objects.create_user(
            username="bare", email="bare@example.com", password="x"
        )

        data = UserSerializer(other).data

        self.assertIsNone(data["avatar"])
