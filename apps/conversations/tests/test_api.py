from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.conversations.models import ConversationInvite

User = get_user_model()


class ConversationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="OwnerPass123!",
        )
        self.other_user = User.objects.create_user(
            username="john",
            email="john@example.com",
            password="JohnPass123!",
        )
        self.conv_list_url = "/api/conversations/"
        self.invite_url = "/api/invites/"

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.cookies["access_token"] = str(refresh.access_token)

    def test_conversation_list_shows_pending_state(self):
        """Test that a conversation with a pending invite is marked as is_pending."""
        self._authenticate(self.user)

        # Create an invite
        invite_email = "invitee@example.com"
        response = self.client.post(self.invite_url, {"email": invite_email})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Fetch conversation list
        response = self.client.get(self.conv_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Should have 1 conversation, marked as pending
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["is_pending"])
        self.assertEqual(results[0]["pending_email"], invite_email)

    def test_accepted_invite_clears_pending_state(self):
        """Test that accepting an invite removes the is_pending flag."""
        self._authenticate(self.user)

        # 1. Create invite
        invite_email = "invitee@example.com"
        self.client.post(self.invite_url, {"email": invite_email})

        invite = ConversationInvite.objects.get(email=invite_email)
        token = invite.token

        # 2. Accept invite (as other_user)
        self._authenticate(self.other_user)
        accept_url = f"/api/invites/{token}/accept/"
        response = self.client.post(accept_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 3. Check conversation list (as owner)
        self._authenticate(self.user)
        response = self.client.get(self.conv_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["is_pending"])
        self.assertIsNone(results[0]["pending_email"])

    def test_direct_conversation_is_not_pending(self):
        """
        Test that a direct conversation between two users is not marked as pending.
        """
        self._authenticate(self.user)

        # Create a direct conversation (POST to /api/conversations/ with user_id)
        response = self.client.post(self.conv_list_url, {"user_id": self.other_user.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Fetch list
        response = self.client.get(self.conv_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["is_pending"])
        self.assertIsNone(results[0]["pending_email"])

    def test_invite_rate_limit(self):
        """Test that users are limited to 1 new invite per 24 hours."""
        self._authenticate(self.user)

        # 1. First invite (success)
        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 2. Second invite to DIFFERENT email (failure - 429)
        response = self.client.post(self.invite_url, {"email": "second@example.com"})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.data["error"], "You can only send one invite every 24 hours."
        )

        # 3. Second invite to SAME email (success - 200 re-send)
        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["invite_already_pending"])
