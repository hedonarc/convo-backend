from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
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

    def test_conversation_list_shows_pending_invitation(self):
        """A conversation with an unaccepted invite exposes an invitation object."""
        self._authenticate(self.user)

        # Create an invite
        invite_email = "invitee@example.com"
        response = self.client.post(self.invite_url, {"email": invite_email})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Fetch conversation list
        response = self.client.get(self.conv_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Should have 1 conversation with a pending invitation
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        invitation = results[0]["invitation"]
        self.assertIsNotNone(invitation)
        self.assertFalse(invitation["is_accepted"])
        self.assertEqual(invitation["email"], invite_email)

    def test_accepted_invite_marks_invitation_accepted(self):
        """Accepting an invite flips its invitation.is_accepted to True."""
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
        invitation = results[0]["invitation"]
        self.assertIsNotNone(invitation)
        self.assertTrue(invitation["is_accepted"])

    def test_direct_conversation_has_no_invitation(self):
        """A direct conversation between two users has no invitation object."""
        self._authenticate(self.user)

        # Create a direct conversation (POST to /api/conversations/ with user_id)
        response = self.client.post(self.conv_list_url, {"user_id": self.other_user.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Fetch list
        response = self.client.get(self.conv_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["invitation"])

    def test_invite_rate_limit_is_per_recipient(self):
        """
        Rate limiting is per recipient, not global: a new recipient always
        succeeds, but re-inviting the same recipient within 24h is blocked.
        """
        self._authenticate(self.user)

        # 1. First invite (success)
        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 2. Different recipient — not rate limited (per-recipient by design)
        response = self.client.post(self.invite_url, {"email": "second@example.com"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # 3. Same recipient within 24h — blocked
        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(
            response.data["error"], "You can only send one invite every 24 hours."
        )

    def test_invite_resolve_returns_inviter_and_account_status(self):
        """
        GET /api/invites/<token>/ exposes inviter + canonical email without
        auth, so the Accept Invite screen can render before the user logs in.
        """
        self._authenticate(self.user)
        invite_email = "newcomer@example.com"
        self.client.post(self.invite_url, {"email": invite_email})
        token = ConversationInvite.objects.get(email=invite_email).token

        # Resolve without authentication — token is the credential.
        self.client.cookies.clear()
        response = self.client.get(f"/api/invites/{token}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], invite_email)
        self.assertEqual(response.data["inviter"]["username"], self.user.username)
        self.assertFalse(response.data["is_accepted"])
        self.assertFalse(response.data["has_account"])
        self.assertIsNotNone(response.data["conversation_id"])
        # Sensitive fields must NOT leak.
        self.assertNotIn("password", response.data["inviter"])

    def test_invite_resolve_has_account_true_when_user_exists(self):
        """has_account flips to True when a User already owns the invited email."""
        self._authenticate(self.user)
        # other_user already exists with john@example.com.
        self.client.post(self.invite_url, {"email": "john@example.com"})
        token = ConversationInvite.objects.get(email="john@example.com").token

        response = self.client.get(f"/api/invites/{token}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["has_account"])

    def test_invite_resolve_unknown_token_returns_404(self):
        response = self.client.get("/api/invites/not-a-real-token/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_invite_resend_after_cooldown_refreshes_window(self):
        """
        Once the 24h window has elapsed, re-inviting the same recipient sends a
        reminder (no new rows) and pushes the cooldown forward via updated_at.
        """
        self._authenticate(self.user)

        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        invite = ConversationInvite.objects.get(email="first@example.com")
        # Move the invite past the cooldown (update() bypasses auto_now).
        stale = timezone.now() - timedelta(hours=25)
        ConversationInvite.objects.filter(pk=invite.pk).update(updated_at=stale)

        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["action"], "reminder_sent")

        invite.refresh_from_db()
        self.assertEqual(invite.invite_count, 2)
        # updated_at must have moved forward, re-arming the 24h cooldown.
        self.assertGreater(invite.updated_at, stale)

        # Immediately re-inviting is blocked again.
        response = self.client.post(self.invite_url, {"email": "first@example.com"})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
