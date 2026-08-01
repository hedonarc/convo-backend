"""The presence store's Redis connection.

Redis does two unrelated jobs in this project and it is worth keeping them
apart:

  * **Channel layer** — pub/sub transport for `group_send`, owned entirely by
    `channels_redis`. You never touch keys; see `CHANNEL_LAYERS`.
  * **Presence store** — this connection. Our own key schema and TTLs under
    `presence:*`, read and written by `services/presence.py`.

Same server, same `REDIS_URL` setting, different jobs. The adapter is chosen
by `settings.REDIS_CLIENT` so tests can swap in fakeredis and run without a
server; production points at a real one.
"""

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string
import redis

_client = None


def real_client():
    """Production adapter — a live server at `settings.REDIS_URL`."""
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def fake_client():
    """Test adapter — real Redis semantics, including TTL, held in memory."""
    import fakeredis

    return fakeredis.FakeRedis(decode_responses=True)


def client():
    """Process-wide client, built once from settings."""
    global _client
    if _client is None:
        _client = import_string(settings.REDIS_CLIENT)()
    return _client


def reset():
    """Drop the cached client so the next call rebuilds it."""
    global _client
    _client = None


@receiver(setting_changed)
def _reset_on_setting_change(sender, setting, **kwargs):
    if setting in {"REDIS_CLIENT", "REDIS_URL"}:
        reset()
