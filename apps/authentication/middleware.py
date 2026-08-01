from http.cookies import SimpleCookie
import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

logger = logging.getLogger(__name__)

User = get_user_model()


@database_sync_to_async
def get_user(token_key):
    """
    Validates the token and returns the corresponding user.
    """
    try:
        token = AccessToken(token_key)
        user_id = token["user_id"]
        return User.objects.get(id=user_id)
    except (TokenError, User.DoesNotExist):
        return AnonymousUser()
    except Exception as e:
        logger.exception("Unexpected error during token validation: %s", e)
        return AnonymousUser()


async def reject(receive, send, code):
    """Close the handshake with *code* in a way the browser can read.

    Daphne answers a close sent before `websocket.accept` with an HTTP 403
    handshake rejection and discards the application code, so the browser
    reports 1006. Accepting first costs one frame and delivers the real code.
    """
    await receive()
    await send({"type": "websocket.accept"})
    await send({"type": "websocket.close", "code": code})


class JWTAuthMiddleware:
    """
    Authenticate WebSocket connections using JWT token in query string.
    Rejects connection if token is missing or invalid.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        # Extract token from query string
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)

        token_list = query_params.get("token")
        token_key = token_list[0] if token_list else None

        # Prefer httpOnly cookie over query string
        raw_cookie_header = dict(scope.get("headers", [])).get(b"cookie", b"").decode()
        cookie = SimpleCookie()
        cookie.load(raw_cookie_header)
        cookie_name = settings.SIMPLE_JWT.get("AUTH_COOKIE", "access_token")
        morsel = cookie.get(cookie_name)
        if morsel:
            token_key = morsel.value

        if not token_key or len(token_key) > 2048:
            logger.error("WebSocket Connection Rejected : No Token")
            await reject(receive, send, 4001)
            return

        user = await get_user(token_key)

        if user.is_anonymous:
            logger.error("WebSocket Connection Rejected : Invalid Token")
            await reject(receive, send, 4002)
            return

        scope["user"] = user
        return await self.inner(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
