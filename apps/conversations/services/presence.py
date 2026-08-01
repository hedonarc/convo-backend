"""
Redis-backed user presence — ephemeral, no database persistence.

Each user has three Redis keys:
  - presence:user:<id>            (hash)    fields: status, last_seen_at
  - presence:conns:<id>           (zset)    member: channel_name (live channels index)
  - presence:conn:<id>:<channel>  (string)  value: "online" | "away", TTL bound

User status is the strongest state across all live connections:
    online  if any tab is online
    away    elif any tab is away
    offline if no live connections

Why three keys? The zset is the *index* of channel names this user has
ever had. The per-channel string holds that connection's visibility state
and is bound to a Redis TTL so it self-destructs if the consumer dies
without firing disconnect (browser crash, dev-server kill, OOM). On every
status read, recompute checks each indexed channel for key existence —
expired ones get pruned from the index in the same pass. The zset score
is unused for liveness (it carries the original expiration but isn't
refreshed on heartbeat) — actual liveness is determined by the per-channel
key's TTL.

The consumer is expected to call `heartbeat()` on a periodic loop while
the socket is open. Each heartbeat is **one** Redis op (EXPIRE on the
per-channel key). Tighter timings give snappier offline-detection at the
cost of more Redis traffic; the values below favour responsiveness over
op budget — peers see a crashed tab flip to offline within ~TTL seconds.

Tuning knobs:
  PRESENCE_TTL_SECONDS    — how long a connection survives without a beat
  HEARTBEAT defined in    — consumer side (UserConsumer.HEARTBEAT_INTERVAL_SECONDS)
  Keep heartbeat < TTL/2  — survives at least one missed beat.

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
import os
import time

import redis

from apps.conversations.models import Participant
from apps.conversations.services import fanout

logger = logging.getLogger(__name__)

ONLINE = "online"
AWAY = "away"
OFFLINE = "offline"

# How long a connection key lives without a heartbeat. Tight value favours
# fast offline-detection — a crashed tab is reflected to peers within ~TTL
# seconds. Pair with HEARTBEAT_INTERVAL_SECONDS <50% of this on the
# consumer side so a single missed beat doesn't sweep the connection.
PRESENCE_TTL_SECONDS = 90

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
        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            _client = redis.from_url(redis_url, decode_responses=True)
        else:
            _client = redis.Redis(
                host=_REDIS_HOST,
                port=_REDIS_PORT,
                db=_REDIS_DB,
                decode_responses=True,
            )
    return _client


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


def _record_channel(user_id: int, channel_name: str, status: str) -> None:
    """
    Register a channel with the index and write its per-channel status
    with a bounded TTL. Two ops — only called on actual transitions
    (mark_online / mark_visibility / mark_offline), never per-heartbeat.
    """
    r = _redis()
    r.set(_conn_key(user_id, channel_name), status, ex=PRESENCE_TTL_SECONDS)
    # Score is informational (last-transition timestamp). Liveness is
    # decided by per-channel key existence at recompute time, not by
    # score — that's why heartbeat doesn't touch the zset.
    r.zadd(_conns_index_key(user_id), {channel_name: time.time()})


def _is_manual_away(user_id: int) -> bool:
    """
    Has the user explicitly chosen "Away" via the menu? If so, the
    user-level status sticks at AWAY regardless of any tab-focus events
    or reconnects until they explicitly choose Online again (or every tab
    disconnects). Stored as a hash field on the user key so it survives
    per-connection churn — the bug being fixed: a tab-blur → tab-refocus
    cycle (or any WS reconnect) used to flip the user back to online and
    silently erase their stated intent.
    """
    return _redis().hget(_user_key(user_id), "manual_away") == "1"


def _recompute_user_status(user_id: int) -> tuple[bool, dict]:
    """
    Read the indexed channels, check which per-channel keys are still
    alive, prune any that aren't, and persist the computed user-level
    status. The single place that decides what the world sees — `mark_*`
    all funnel through this so the live/stale boundary is enforced
    uniformly.

    Op cost: ZRANGE + MGET + (HGET manual_away) + (ZREM if stale) +
    HGET status + HSET status = 5–6 ops.
    """
    r = _redis()
    index_key = _conns_index_key(user_id)
    channels = r.zrange(index_key, 0, -1)
    if not channels:
        # Fully offline — also drop the manual override so the next
        # session starts fresh in auto mode. We intentionally don't
        # persist the override across full disconnects; that matches
        # Slack semantics ("Away" is session-scoped, not durable).
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

    # Garbage-collect ZSET members whose per-channel key has expired.
    # Conditional so we don't burn an op on the steady-state happy path.
    if stale:
        r.zrem(index_key, *stale)

    if not live:
        # Fully offline (all channels were stale). Drop the manual override
        # so the next session starts fresh.
        r.hdel(_user_key(user_id), "manual_away")
        return _persist_status(user_id, OFFLINE)

    # Manual override takes precedence over auto computation. We still
    # check channel liveness above so a stale override on a fully-offline
    # user can't keep them showing "away" forever.
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
    """
    Update a single connection's visibility (tab focused vs hidden) and
    recompute the user-level status. No-op for unknown / expired channels.
    """
    r = _redis()
    if r.get(_conn_key(user_id, channel_name)) is None:
        return False, get_status(user_id)
    _record_channel(user_id, channel_name, ONLINE if visible else AWAY)
    return _recompute_user_status(user_id)


def heartbeat(user_id: int, channel_name: str) -> None:
    """
    Refresh the TTL on a live connection without changing its status.
    Exactly **one** Redis op (EXPIRE) — this is the dominant cost in
    steady state, so it's deliberately minimal.

    If the key has already expired EXPIRE returns 0 (no-op); we don't
    care, the next event or fresh connect will reestablish presence
    through mark_online.
    """
    _redis().expire(_conn_key(user_id, channel_name), PRESENCE_TTL_SECONDS)


def mark_offline(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """Drop a socket; if it was the last live one, flip user to offline."""
    r = _redis()
    r.delete(_conn_key(user_id, channel_name))
    r.zrem(_conns_index_key(user_id), channel_name)
    return _recompute_user_status(user_id)


def set_manual_away(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """
    Record explicit user intent: "I want to appear away." Survives tab
    focus/blur and WS reconnects until the user explicitly flips back to
    online (or their last tab disconnects). Also marks this tab's
    connection as away so the connection-level state agrees with the
    override — keeps things consistent if the override is later cleared.
    """
    r = _redis()
    _record_channel(user_id, channel_name, AWAY)
    r.hset(_user_key(user_id), "manual_away", "1")
    return _recompute_user_status(user_id)


def clear_manual_away(user_id: int, channel_name: str) -> tuple[bool, dict]:
    """
    Drop the manual override and flip this tab's connection back to
    online. After this, status reverts to auto behaviour — tab visibility
    drives the per-tab state, user-level rolls up from the strongest.
    """
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
