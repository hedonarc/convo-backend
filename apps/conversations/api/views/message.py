from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.api.serializers.message import (
    MessageSerializer,
    SendOrEditMessageSerializer,
)
from apps.conversations.models import Message
from apps.conversations.pagination import MessageCursorPagination
from apps.conversations.permissions import IsConversationParticipant
from apps.conversations.services.message_service import post_message
from apps.conversations.services.realtime import (
    broadcast_message_deleted,
    broadcast_message_edited,
)
from apps.conversations.utils import get_conversation_or_404, get_message_or_404
from utils.translations import t


class MessageView(APIView):
    permission_classes = [IsAuthenticated, IsConversationParticipant]

    def get(self, request, conversation_id):
        """Get list of messages of a single conversation"""
        conversation = get_conversation_or_404(conversation_id)

        messages = Message.objects.filter(conversation=conversation).order_by(
            "-created_at"
        )

        paginator = MessageCursorPagination()

        paginated_messages = paginator.paginate_queryset(messages, request, view=self)

        serializer = MessageSerializer(paginated_messages, many=True)

        return paginator.get_paginated_response(serializer.data)

    def post(self, request, conversation_id):
        conversation = get_conversation_or_404(conversation_id)
        serializer = SendOrEditMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = post_message(
            conversation=conversation,
            sender=request.user,
            content=serializer.validated_data["content"],
        )

        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)

    def patch(self, request, conversation_id, message_id):
        conversation = get_conversation_or_404(conversation_id)
        message = get_message_or_404(conversation, message_id)

        self.check_object_permissions(request, message)

        serializer = SendOrEditMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Adding previous message to prev_content and saving new message to content
        new_content = serializer.validated_data["content"]

        if not new_content.strip():
            return Response(
                {"error": t("messages.empty_edit_error")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message.prev_content = message.content
        message.content = new_content
        message.edited_at = timezone.now()
        message.save(update_fields=["prev_content", "content", "edited_at"])

        broadcast_message_edited(conversation.id, message)

        return Response(MessageSerializer(message).data, status=status.HTTP_200_OK)

    def delete(self, request, conversation_id, message_id):
        conversation = get_conversation_or_404(conversation_id)
        message = get_message_or_404(conversation, message_id)

        self.check_object_permissions(request, message)

        # soft delete logic
        message.prev_content = message.content
        message.content = ""
        message.is_deleted = True
        message.deleted_at = timezone.now()
        message.save(
            update_fields=["prev_content", "content", "is_deleted", "deleted_at"]
        )

        broadcast_message_deleted(conversation.id, message)

        return Response(
            {"message": t("messages.deleted_success")},
            status=status.HTTP_200_OK,
        )
