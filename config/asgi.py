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
    """Sync Redis PING against the channel layer's configured host. Skips
    silently if the backend isn't Redis (e.g. InMemoryChannelLayer in tests
    or when Channels isn't fully configured)."""
    channel_layers = getattr(settings, "CHANNEL_LAYERS", {})
    default = channel_layers.get("default", {})
    backend = default.get("BACKEND", "")
    if "redis" not in backend.lower():
        logger.info("[startup] channel layer is %s — skipping Redis check", backend)
        return

    hosts = default.get("CONFIG", {}).get("hosts", [])
    if not hosts:
        logger.warning("[startup] Redis channel layer configured with no hosts")
        return

    host = hosts[0]
    url = host if isinstance(host, str) else None
    try:
        import redis  # transitively available via channels-redis

        client = redis.from_url(url) if url else redis.Redis(**host)
        client.ping()
        client.close()
        logger.info("[startup] redis connected: %s", _redact(url) if url else "<dict>")
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
