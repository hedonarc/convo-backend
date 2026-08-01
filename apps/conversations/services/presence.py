"""Who is online, held in Redis and never in the database.

Three keys per user:

    presence:user:<id>            hash    status, last_seen_at, manual_away
    presence:conns:<id>           zset    index of this user's channel names
    presence:conn:<id>:<channel>  string  "online" | "away", TTL bound

A user is as present as their most-present tab: online if any is online,
away if any is away, offline once none survive. The per-channel key's TTL
is what decides survival, so a tab that dies without a disconnect frame
disappears on its own and `_recompute_user_status` prunes it from the index.

Losing Redis makes everyone offline until they reconnect, which is what a
network blip already looks like. Nothing to migrate, nothing to clean up.

See docs/adr/0001-presence-timings.md for the TTL and heartbeat trade-off.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
import logging
import time

from apps.conversations.models import Participant
from apps.conversations.services import fanout, redis_store

logger = logging.getLogger(__name__)

ONLINE = "online"
AWAY = "away"
OFFLINE = "offline"

# Paired with UserConsumer.HEARTBEAT_INTERVAL_SECONDS — see the ADR.
PRESENCE_TTL_SECONDS = 90

_redis = redis_store.client


def _user_key(user_id: int) -> str:
    return f"presence:user:{user_id}"


def _conns_index_key(user_id: int) -> str:
    return f"presence:conns:{user_id}"


def _conn_key(user_id: int, channel_name: str) -> str:
    return f"presence:conn:{user_id}:{channel_name}"


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
    """Store the user-level status, reporting whether it actually moved.

    Callers skip the broadcast when it did not.
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


def _record_channel(user_id: int, channel_name: str, status: str) -> None:
    """Index a channel and give it a TTL. Two ops, on transitions only."""
    r = _redis()
    r.set(_conn_key(user_id, channel_name), status, ex=PRESENCE_TTL_SECONDS)
    # The score is only a last-transition timestamp; liveness comes from the
    # per-channel key, which is why heartbeat never touches this zset.
    r.zadd(_conns_index_key(user_id), {channel_name: time.time()})


def _is_manual_away(user_id: int) -> bool:
    """Did the user choose "Away" themselves?

    Held on the user key rather than a connection key so tab focus and
    reconnects cannot quietly undo a stated intent.
    """
    return _redis().hget(_user_key(user_id), "manual_away") == "1"


def _recompute_user_status(user_id: int) -> tuple[bool, dict]:
    """Decide what the world sees, pruning connections that died silently.

    Every `mark_*` ends here, so the live/stale boundary is drawn once.
    Costs 5-6 Redis ops.
    """
    r = _redis()
    index_key = _conns_index_key(user_id)
    channels = r.zrange(index_key, 0, -1)
    if not channels:
        # "Away" is session-scoped, as in Slack: the last tab leaving clears it.
        r.hdel(_user_key(user_id), "manual_away")
        return _persist_status(user_id, OFFLINE)

    statuses = r.mget([_conn_key(user_id, c) for c in channels])
    live: list[str] = []
    stale: list[str] = []
    for chan, status in zip(channels, statuses, strict=True):
        if status is None:
            stale.append(chan)
        else:
            live.append(status)

    # Conditional so the steady-state path does not spend an op.
    if stale:
        r.zrem(index_key, *stale)

    if not live:
        r.hdel(_user_key(user_id), "manual_away")
        return _persist_status(user_id, OFFLINE)

    # Checked after liveness, so a stale override cannot pin an offline user.
    if _is_manual_away(user_id):
        return _persist_status(user_id, AWAY)

    return _persist_status(user_id, _compute_user_status(live))


# ── Mutators ────────────────────────────────────────────────────────────────


def mark_online(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Track a new socket as online and recompute user status."""
    _record_channel(user_id, channel_name, ONLINE)
    return _recompute_user_status(user_id)


def mark_visibility(
    user_id: int, channel_name: str, visible: bool
) -> tuple[bool, dict]:
    """Move one tab between visible and hidden. No-op for a dead channel."""
    r = _redis()
    if r.get(_conn_key(user_id, channel_name)) is None:
        return False, get_status(user_id)
    _record_channel(user_id, channel_name, ONLINE if visible else AWAY)
    return _recompute_user_status(user_id)


def heartbeat(user_id: int, channel_name: str) -> None:
    """Keep a live connection alive. One op, the dominant steady-state cost.

    Already expired is fine: the next connect re-establishes presence.
    """
    _redis().expire(_conn_key(user_id, channel_name), PRESENCE_TTL_SECONDS)


def mark_offline(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Drop a socket; if it was the last live one, flip user to offline."""
    r = _redis()
    r.delete(_conn_key(user_id, channel_name))
    r.zrem(_conns_index_key(user_id), channel_name)
    return _recompute_user_status(user_id)


def set_manual_away(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Pin the user to away until they say otherwise, or their last tab closes.

    This tab is marked away too, so clearing the override later leaves a
    consistent state.
    """
    r = _redis()
    _record_channel(user_id, channel_name, AWAY)
    r.hset(_user_key(user_id), "manual_away", "1")
    return _recompute_user_status(user_id)


def clear_manual_away(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Hand control back to tab visibility, with this tab counted as online."""
    r = _redis()
    _record_channel(user_id, channel_name, ONLINE)
    r.hdel(_user_key(user_id), "manual_away")
    return _recompute_user_status(user_id)


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
    """Everyone who shares a conversation with *user_id*, excluding them.

    The presence audience: strangers never see each other's status.
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


@fanout.best_effort
def broadcast_presence(user_id: int) -> None:
    """Tell every conversation peer — and the user's own tabs — the new status.

    Self is in the audience so the account menu's active-check confirms a
    manual flip, and so a user's other tabs stay in step.
    """
    payload = get_status(user_id)
    fanout.to_users_sync(
        [user_id, *get_peer_user_ids(user_id)],
        fanout.PRESENCE_CHANGED,
        user_id=user_id,
        status=payload["status"],
        last_seen_at=payload["last_seen_at"],
    )
