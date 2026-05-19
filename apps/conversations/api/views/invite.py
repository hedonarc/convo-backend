from datetime import timedelta
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.api.serializers.conversation import ConversationSerializer
from apps.conversations.api.serializers.invite import InviteCreateSerializer
from apps.conversations.models import Conversation, ConversationInvite, Participant

logger = logging.getLogger(__name__)


class InviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # --- Normalise email once; use this value everywhere below ----------
        email = serializer.validated_data["email"].strip().lower()

        # --- Guard: return the existing conversation if a pending invite
        #     already exists for this sender + email pair.
        #     This prevents duplicate conversations and duplicate invites even
        #     when the user clicks "Send" multiple times in quick succession.
        existing_invite = ConversationInvite.get_pending(
            email=email,
            created_by=request.user,
        )

        if existing_invite:
            # --- Rate Limit: 1 new invite per 24 hours ---------------------------
            available_after = existing_invite.updated_at + timedelta(hours=24)
            recent_invite_exists = timezone.now() < available_after

            if recent_invite_exists:
                return Response(
                    {
                        "error": "You can only send one invite every 24 hours.",
                        "available_after": available_after,
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            # Optionally re-send the invite email so the recipient gets a
            # reminder, but we never create new DB rows.
            self._send_invite_email(
                request.user,
                email,
                existing_invite.token,
            )

            # Update the invite count
            existing_invite.invite_count += 1
            existing_invite.save(update_fields=["invite_count"])

            return Response(
                {
                    **ConversationSerializer(existing_invite.conversation).data,
                    "action": "reminder_sent",
                },
                status=status.HTTP_200_OK,  # 200, not 201 — nothing was created
            )

        # --- Happy path: no pending invite and not rate limited --------------
        conversation = Conversation.objects.create(created_by=request.user)
        Participant.objects.create(conversation=conversation, user=request.user)

        invite = ConversationInvite.objects.create(
            email=email,
            conversation=conversation,
            created_by=request.user,
        )

        self._send_invite_email(request.user, email, invite.token)

        return Response(
            {
                **ConversationSerializer(conversation).data,
                "action": "created",
            },
            status=status.HTTP_201_CREATED,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_invite_email(self, sender_user, email: str, token: str) -> None:
        """Render and dispatch the invite email. Failures are logged, not raised."""
        invite_link = f"{settings.FRONTEND_URL}/register?invite={token}&email={email}"
        logger.debug("invite_link: %s", invite_link)

        html_content = render_to_string(
            "emails/invite.html",
            {
                "inviter_name": sender_user.username,
                "invite_link": invite_link,
            },
        )

        send_mail(
            subject=f"{sender_user.username} invited you to Convo",
            message=f"Join Convo and start chatting: {invite_link}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html_content,
            fail_silently=True,
        )


class InviteAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        try:
            invite = ConversationInvite.objects.get(token=token)
        except ConversationInvite.DoesNotExist:
            return Response(
                {"error": "Invalid invite token"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if invite.is_accepted:
            return Response(
                {"error": "Invite already accepted"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        Participant.objects.get_or_create(
            conversation=invite.conversation,
            user=request.user,
        )

        invite.is_accepted = True
        invite.save()

        return Response(
            ConversationSerializer(invite.conversation).data,
            status=status.HTTP_200_OK,
        )
