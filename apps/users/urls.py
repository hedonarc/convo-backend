from django.urls import path

from apps.users.api.views.me import MeView
from apps.users.api.views.user import UserDetailView, UsersListView

urlpatterns = [
    path("users/<int:user_id>", UserDetailView.as_view()),
    path("users/", UsersListView.as_view()),
    path("me/", MeView.as_view()),
]
