"""
Redis-backed user presence — ephemeral, no database persistence.

Each user has two Redis keys:
  - presence:user:<id>  (hash)  fields: status, last_seen_at
  - presence:conns:<id> (hash)  field per active socket: channel_name -> conn_status
    (conn_status is "online" or "away" — the per-tab visibility state)

User status is the strongest state across all connections:
    online  if any tab is online
    away    elif any tab is away
    offline if no connections at all

Presence is ephemeral by design: a Redis crash or server restart leaves
everyone as offline until they reconnect, which is the same effect as a
network blip. No migration, no DB writes, no cleanup job.

Fanout target: only users who share at least one Conversation with the
changed user — see `get_peer_user_ids`. Strangers never see each other's
presence.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import redis

from apps.conversations.models import Participant

logger = logging.getLogger(__name__)

ONLINE = "online"
AWAY = "away"
OFFLINE = "offline"

# Mirrors CHANNEL_LAYERS["default"]["CONFIG"]["hosts"] in settings/base.py.
# Kept hardcoded for now to avoid pulling channels_redis internals; if the
# host ever moves, both will need updating in lockstep.
_REDIS_HOST = "127.0.0.1"
_REDIS_PORT = 6379
_REDIS_DB = 0

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=_REDIS_HOST,
            port=_REDIS_PORT,
            db=_REDIS_DB,
            decode_responses=True,
        )
    return _client


def _user_key(user_id: int) -> str:
    return f"presence:user:{user_id}"


def _conns_key(user_id: int) -> str:
    return f"presence:conns:{user_id}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _compute_user_status(conn_statuses: Iterable[str]) -> str:
    """Reduce per-connection states to a single user-level state."""
    statuses = list(conn_statuses)
    if not statuses:
        return OFFLINE
    if ONLINE in statuses:
        return ONLINE
    return AWAY


def _persist_status(user_id: int, status: str) -> tuple[bool, dict]:
    """
    Write the user-level status to Redis. Returns (changed, payload) where
    `changed` is True iff the status differs from what was stored, so callers
    can skip a broadcast on a no-op transition.
    """
    r = _redis()
    key = _user_key(user_id)
    previous = r.hget(key, "status")
    now = _now_iso()
    r.hset(key, mapping={"status": status, "last_seen_at": now})
    return previous != status, {
        "user_id": user_id,
        "status": status,
        "last_seen_at": now,
    }


# ── Mutators ────────────────────────────────────────────────────────────────


def mark_online(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Track a new socket as online and recompute user status."""
    r = _redis()
    r.hset(_conns_key(user_id), channel_name, ONLINE)
    return _persist_status(user_id, ONLINE)


def mark_visibility(
    user_id: int, channel_name: str, visible: bool
) -> tuple[bool, dict]:
    """
    Update a single connection's visibility (tab focused vs hidden) and
    recompute the user-level status. No-op for unknown channels.
    """
    r = _redis()
    if not r.hexists(_conns_key(user_id), channel_name):
        return False, get_status(user_id)
    r.hset(_conns_key(user_id), channel_name, ONLINE if visible else AWAY)
    statuses = r.hvals(_conns_key(user_id))
    return _persist_status(user_id, _compute_user_status(statuses))


def mark_offline(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Drop a socket; if it was the last one, flip user to offline."""
    r = _redis()
    r.hdel(_conns_key(user_id), channel_name)
    statuses = r.hvals(_conns_key(user_id))
    return _persist_status(user_id, _compute_user_status(statuses))


# ── Readers ─────────────────────────────────────────────────────────────────


def get_status(user_id: int) -> dict:
    """Read the cached status for one user. Missing keys → offline."""
    data = _redis().hgetall(_user_key(user_id))
    return {
        "user_id": user_id,
        "status": data.get("status", OFFLINE),
        "last_seen_at": data.get("last_seen_at"),
    }


def get_statuses(user_ids: Iterable[int]) -> dict[int, dict]:
    """Bulk read presence for many users in a single Redis round-trip."""
    user_ids = list(user_ids)
    if not user_ids:
        return {}
    pipe = _redis().pipeline()
    for uid in user_ids:
        pipe.hgetall(_user_key(uid))
    results = pipe.execute()
    return {
        uid: {
            "user_id": uid,
            "status": data.get("status", OFFLINE) if data else OFFLINE,
            "last_seen_at": data.get("last_seen_at") if data else None,
        }
        for uid, data in zip(user_ids, results, strict=True)
    }


def get_peer_user_ids(user_id: int) -> list[int]:
    """
    Return distinct user ids who share at least one conversation with
    *user_id*. Excludes the user themselves. Drives the presence fanout
    audience — strangers never see each other's status.
    """
    own_conv_ids = Participant.objects.filter(user_id=user_id).values_list(
        "conversation_id", flat=True
    )
    return list(
        Participant.objects.filter(conversation_id__in=own_conv_ids)
        .exclude(user_id=user_id)
        .values_list("user_id", flat=True)
        .distinct()
    )


# ── Broadcast ───────────────────────────────────────────────────────────────


def broadcast_presence(user_id: int) -> None:
    """
    Push the current status of *user_id* to every conversation peer's
    per-user channel. Failures are logged and swallowed so a Redis hiccup
    can't break socket handling.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    try:
        payload = get_status(user_id)
        peer_ids = get_peer_user_ids(user_id)
        send = async_to_sync(channel_layer.group_send)
        for pid in peer_ids:
            send(
                f"user_{pid}",
                {
                    "type": "presence_changed_event",
                    "user_id": user_id,
                    "status": payload["status"],
                    "last_seen_at": payload["last_seen_at"],
                },
            )
    except Exception:
        logger.exception("Failed to broadcast presence for user=%s", user_id)
