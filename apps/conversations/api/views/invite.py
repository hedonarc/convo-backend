from datetime import timedelta
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.api.serializers.conversation import ConversationSerializer
from apps.conversations.api.serializers.invite import (
    InviteCreateSerializer,
    InviteResolveSerializer,
)
from apps.conversations.models import Conversation, ConversationInvite, Participant
from apps.conversations.services.realtime import (
    broadcast_conversation_update,
    notify_invite_accepted,
)
from utils.translations import t

logger = logging.getLogger(__name__)


class InviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        # Reuse the existing invite/conversation so repeated clicks don't create
        # duplicate rows for the same sender + email pair.
        existing_invite = ConversationInvite.get_pending(
            email=email,
            created_by=request.user,
        )

        if existing_invite:
            # Rate limit: one invite per recipient per 24 hours.
            available_after = existing_invite.updated_at + timedelta(hours=24)
            recent_invite_exists = timezone.now() < available_after

            if recent_invite_exists:
                return Response(
                    {
                        "error": t("invites.rate_limit_error"),
                        "available_after": available_after,
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            # Re-send the email as a reminder without creating new rows.
            self._send_invite_email(
                request.user,
                email,
                existing_invite.token,
            )

            existing_invite.invite_count += 1
            existing_invite.save(update_fields=["invite_count", "updated_at"])

            return Response(
                {
                    **ConversationSerializer(existing_invite.conversation).data,
                    "action": "reminder_sent",
                },
                status=status.HTTP_200_OK,  # 200, not 201 — nothing was created
            )

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

    def _send_invite_email(self, sender_user, email: str, token: str) -> None:
        """Render and dispatch the invite email. Failures are logged, not raised."""
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        # Token-only URL — the backend resolves email + inviter from the token,
        # so the link stays short, trustworthy, and doesn't leak the email in
        # logs / browser history / shoulder-surfing.
        invite_link = f"{frontend_url}/invite/{token}"
        logger.debug("invite_link: %s", invite_link)

        html_content = render_to_string(
            "emails/invite.html",
            {
                "inviter_name": sender_user.username,
                "invite_link": invite_link,
            },
        )

        send_mail(
            subject=t("emails.invite_subject").format(
                inviter_name=sender_user.username
            ),
            message=t("emails.invite_body").format(invite_link=invite_link),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html_content,
            fail_silently=True,
        )


class InviteResolveView(APIView):
    """
    Public lookup of an invite by its token. Used by the Accept Invite screen
    to render inviter name + canonical email before the user authenticates.

    Auth: AllowAny. The 64-char URL-safe token itself is the credential; only
    a sliver of public-safe data is returned (no conversation contents).
    """

    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            invite = ConversationInvite.objects.select_related("created_by").get(
                token=token
            )
        except ConversationInvite.DoesNotExist:
            return Response(
                {"error": t("invites.invalid_token")},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            InviteResolveSerializer(invite, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class InviteAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token):
        try:
            invite = ConversationInvite.objects.get(token=token)
        except ConversationInvite.DoesNotExist:
            return Response(
                {"error": t("invites.invalid_token")},
                status=status.HTTP_404_NOT_FOUND,
            )

        if invite.is_accepted:
            return Response(
                {"error": t("invites.already_accepted")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        Participant.objects.get_or_create(
            conversation=invite.conversation,
            user=request.user,
        )

        invite.is_accepted = True
        invite.save()

        # Notify both participants — sender's sidebar swaps the pending panel
        # for a regular chat; new participant sees the conversation appear.
        broadcast_conversation_update(invite.conversation)
        # Inviter-only celebratory notification — drives the "X joined the
        # conversation" toast on the sender's side. Fires after the sidebar
        # update so the conversation row exists before the toast points at it.
        notify_invite_accepted(invite, request.user)

        return Response(
            ConversationSerializer(invite.conversation).data,
            status=status.HTTP_200_OK,
        )
