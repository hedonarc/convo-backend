from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations.services import presence


class PresenceView(APIView):
    """
    Initial presence snapshot used by the Chat page on mount. Returns the
    status of every user who shares a conversation with the caller (plus
    the caller themselves), so the sidebar can render avatar dots
    immediately without waiting for the next presence_changed event.

    Real-time updates after this point arrive over the user WebSocket
    (`presence_changed` events).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        peer_ids = presence.get_peer_user_ids(request.user.id)
        # Include self so the frontend can render its own dot consistently.
        user_ids = [request.user.id, *peer_ids]
        statuses = presence.get_statuses(user_ids)
        return Response(
            {str(uid): payload for uid, payload in statuses.items()},
            status=status.HTTP_200_OK,
        )
