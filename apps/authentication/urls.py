from django.urls import path

from apps.authentication.api.views.authentication import (
    LoginView,
    LogoutView,
    RegisterView,
    TokenRefreshCookieView,
)

urlpatterns = [
    path("register/", RegisterView.as_view()),
    path("login/", LoginView.as_view()),
    path("logout/", LogoutView.as_view()),
    path("token/refresh/", TokenRefreshCookieView.as_view()),
]
