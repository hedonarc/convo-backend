from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.views import TokenRefreshView

from apps.authentication.api.serializers.authentication import (
    LoginSerializer,
    RegisterSerializer,
)
from apps.authentication.utils import get_tokens_for_user
from apps.users.api.serializers.user import UserSerializer
from utils.translations import t


# test branch
def _set_auth_cookies(response, token: dict) -> None:
    """Attach access + refresh JWT as httpOnly cookies."""
    jwt_settings = settings.SIMPLE_JWT
    secure = jwt_settings.get("AUTH_COOKIE_SECURE", False)
    httponly = jwt_settings.get("AUTH_COOKIE_HTTP_ONLY", True)
    samesite = jwt_settings.get("AUTH_COOKIE_SAMESITE", "Lax")

    response.set_cookie(
        key=jwt_settings["AUTH_COOKIE"],
        value=token["access"],
        max_age=int(jwt_settings["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        secure=secure,
        httponly=httponly,
        samesite=samesite,
    )
    response.set_cookie(
        key=jwt_settings["AUTH_COOKIE_REFRESH"],
        value=token["refresh"],
        max_age=int(jwt_settings["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        secure=secure,
        httponly=httponly,
        samesite=samesite,
    )


class RegisterView(APIView):
    throttle_classes = [AnonRateThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token = get_tokens_for_user(user)

        response = Response(
            data={
                "message": t("register.success"),
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )
        _set_auth_cookies(response, token)
        return response


class LoginView(APIView):
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token = get_tokens_for_user(user)

        response = Response(
            data={
                "message": t("login.success"),
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, token)
        return response


class LogoutView(APIView):
    def post(self, request):
        response = Response(
            {"message": t("logout.success")},
            status=status.HTTP_200_OK,
        )
        response.delete_cookie(settings.SIMPLE_JWT["AUTH_COOKIE"])
        response.delete_cookie(settings.SIMPLE_JWT["AUTH_COOKIE_REFRESH"])
        return response


class TokenRefreshCookieView(TokenRefreshView):
    """
    Reads the refresh token from the httpOnly cookie, delegates rotation +
    blacklisting to simplejwt's TokenRefreshView, then moves the resulting
    tokens back into httpOnly cookies.
    """

    def post(self, request, *args, **kwargs):
        refresh_cookie = settings.SIMPLE_JWT.get("AUTH_COOKIE_REFRESH", "refresh_token")
        raw_refresh = request.COOKIES.get(refresh_cookie)

        if not raw_refresh:
            return Response(
                {"detail": "Refresh token missing."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Inject into request.data so the parent view's serializer picks it up
        request.data["refresh"] = raw_refresh

        try:
            response = super().post(request, *args, **kwargs)
        except (InvalidToken, TokenError) as e:
            return Response({"detail": str(e)}, status=status.HTTP_401_UNAUTHORIZED)

        # Move tokens from JSON body → httpOnly cookies
        _set_auth_cookies(response, response.data)
        response.data = {"message": "Token refreshed."}
        return response
