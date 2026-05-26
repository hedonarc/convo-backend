from django.urls import path

from .api.views.conversation import ConversationView
from .api.views.invite import InviteAcceptView, InviteResolveView, InviteView
from .api.views.message import MessageView
from .api.views.presence import PresenceView

urlpatterns = [
    path("conversations/", ConversationView.as_view()),
    path("conversations/<int:conversation_id>/messages/", MessageView.as_view()),
    path(
        "conversations/<int:conversation_id>/messages/<int:message_id>/",
        MessageView.as_view(),
    ),
    path("invites/", InviteView.as_view()),
    path("invites/<str:token>/", InviteResolveView.as_view()),
    path("invites/<str:token>/accept/", InviteAcceptView.as_view()),
    path("presence/", PresenceView.as_view()),
]
