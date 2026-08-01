import logging
import os
from urllib.parse import urlparse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.local")

from django.core.asgi import get_asgi_application

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.conf import settings
from django.db import connection

from apps.authentication.middleware import JWTAuthMiddlewareStack
from apps.conversations.routing import websocket_urlpatterns

logger = logging.getLogger(__name__)


def _redact(url: str) -> str:
    """Strip credentials from a connection URL before logging — Neon /
    Upstash credentials would otherwise show up in Render's log stream."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}{parsed.path or ''}"
    except Exception:
        return "<unparseable>"


def _check_database() -> None:
    """Best-effort DB ping at boot. Logged-only — Django would lazily
    error on first query anyway; this just gives an early heads-up in the
    Render log stream so DSN / network problems are obvious on deploy."""
    try:
        connection.ensure_connection()
        db = settings.DATABASES["default"]
        engine = db.get("ENGINE", "?").rsplit(".", 1)[-1]
        host = db.get("HOST") or "?"
        port = db.get("PORT") or ""
        name = db.get("NAME", "?")
        suffix = f":{port}" if port else ""
        logger.info(
            "[startup] database connected: %s://%s%s/%s", engine, host, suffix, name
        )
    except Exception as exc:
        logger.error("[startup] database connection FAILED: %s", exc)


def _check_redis() -> None:
    """Best-effort Redis ping at boot, through the same client presence uses.

    The channel layer reads the same `REDIS_URL`, so one ping covers both.
    """
    from apps.conversations.services import redis_store

    try:
        redis_store.client().ping()
        logger.info("[startup] redis connected: %s", _redact(settings.REDIS_URL))
    except Exception as exc:
        logger.error("[startup] redis connection FAILED: %s", exc)


# Run once per worker boot — visible in Daphne / runserver logs.
_check_database()
_check_redis()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
