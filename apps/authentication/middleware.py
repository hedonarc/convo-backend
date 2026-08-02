from http.cookies import SimpleCookie
import logging

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from utils import ws

logger = logging.getLogger(__name__)

User = get_user_model()

MAX_TOKEN_LENGTH = 2048


@database_sync_to_async
def get_user(token_key):
    """Resolve a token to its user, or AnonymousUser if it does not hold up."""
    try:
        token = AccessToken(token_key)
        user_id = token["user_id"]
        return User.objects.get(id=user_id)
    except (TokenError, User.DoesNotExist):
        return AnonymousUser()
    except Exception as e:
        logger.exception("Unexpected error during token validation: %s", e)
        return AnonymousUser()


def access_token_from(scope) -> str | None:
    """Read the access token from the handshake's cookie header.

    Cookies only. A token in the query string would be written to proxy and
    access logs, and the browser sends the httpOnly cookie on the WebSocket
    handshake anyway.
    """
    raw_cookie_header = dict(scope.get("headers", [])).get(b"cookie", b"").decode()
    cookie = SimpleCookie()
    cookie.load(raw_cookie_header)

    morsel = cookie.get(settings.SIMPLE_JWT.get("AUTH_COOKIE", "access_token"))
    if morsel is None or len(morsel.value) > MAX_TOKEN_LENGTH:
        return None
    return morsel.value or None


async def reject(receive, send, code):
    """Refuse the handshake in a way the browser can actually read.

    Two hazards, one after the other. Daphne turns a close sent before
    `websocket.accept` into an HTTP 403 and drops the code, so we accept
    first. Then the proxy in front of the app drops a close frame sent that
    soon after the upgrade, so the reason also travels as data.
    """
    await receive()
    await send({"type": "websocket.accept"})
    await send({"type": "websocket.send", "text": ws.rejection_frame(code)})
    await send({"type": "websocket.close", "code": code})


class JWTAuthMiddleware:
    """Authenticate a WebSocket handshake from the auth cookie."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        token_key = access_token_from(scope)
        if not token_key:
            logger.error("WebSocket Connection Rejected : No Token")
            await reject(receive, send, ws.NO_TOKEN)
            return

        user = await get_user(token_key)
        if user.is_anonymous:
            logger.error("WebSocket Connection Rejected : Invalid Token")
            await reject(receive, send, ws.INVALID_TOKEN)
            return

        scope["user"] = user
        return await self.inner(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
