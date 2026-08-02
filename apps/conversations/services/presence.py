"""Who is online, held in Redis and never in the database.

Three keys per user:

    presence:user:{<id>}            hash    status, last_seen_at, manual_away
    presence:conns:{<id>}           zset    index of this user's channel names
    presence:conn:{<id>}:<channel>  string  "online" | "away", TTL bound

The braces are a Redis hash tag, so one user's three keys always land on the
same node and `_RECOMPUTE` can touch them in a single script.

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
    return f"presence:user:{{{user_id}}}"


def _conns_index_key(user_id: int) -> str:
    return f"presence:conns:{{{user_id}}}"


def _conn_prefix(user_id: int) -> str:
    return f"presence:conn:{{{user_id}}}:"


def _conn_key(user_id: int, channel_name: str) -> str:
    return _conn_prefix(user_id) + channel_name


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _record_channel(user_id: int, channel_name: str, status: str) -> None:
    """Index a channel and give it a TTL. Two ops, on transitions only."""
    r = _redis()
    r.set(_conn_key(user_id, channel_name), status, ex=PRESENCE_TTL_SECONDS)
    # The score is only a last-transition timestamp; liveness comes from the
    # per-channel key, which is why heartbeat never touches this zset.
    r.zadd(_conns_index_key(user_id), {channel_name: time.time()})


# Read the live connections, drop the dead ones and write the resulting user
# status, all in one indivisible step. Doing this as separate round-trips let
# two tabs interleave and persist a status neither of them computed.
_RECOMPUTE_LUA = """
local user_key, index_key = KEYS[1], KEYS[2]
local prefix, now = ARGV[1], ARGV[2]
local ONLINE, AWAY, OFFLINE = ARGV[3], ARGV[4], ARGV[5]

local channels = redis.call('ZRANGE', index_key, 0, -1)
local live, stale = {}, {}
for i = 1, #channels do
  local value = redis.call('GET', prefix .. channels[i])
  if value then live[#live + 1] = value else stale[#stale + 1] = channels[i] end
end

if #stale > 0 then redis.call('ZREM', index_key, unpack(stale)) end

local status
if #live == 0 then
  -- "Away" is session-scoped, as in Slack: the last tab leaving clears it.
  redis.call('HDEL', user_key, 'manual_away')
  status = OFFLINE
elseif redis.call('HGET', user_key, 'manual_away') == '1' then
  -- Checked after liveness, so a stale override cannot pin an offline user.
  status = AWAY
else
  status = AWAY
  for i = 1, #live do
    if live[i] == ONLINE then status = ONLINE break end
  end
end

local previous = redis.call('HGET', user_key, 'status')
redis.call('HSET', user_key, 'status', status, 'last_seen_at', now)
return {previous ~= status and 1 or 0, status}
"""

_recompute_script = None


def _recompute_user_status(user_id: int) -> tuple[bool, dict]:
    """Decide what the world sees, pruning connections that died silently.

    Every `mark_*` ends here, so the live/stale boundary is drawn once. Runs
    as one script rather than five round-trips, which is both cheaper and the
    only way two tabs racing cannot leave a status neither computed.
    """
    global _recompute_script
    if _recompute_script is None:
        _recompute_script = _redis().register_script(_RECOMPUTE_LUA)

    now = _now_iso()
    changed, status = _recompute_script(
        keys=[_user_key(user_id), _conns_index_key(user_id)],
        args=[_conn_prefix(user_id), now, ONLINE, AWAY, OFFLINE],
    )
    return bool(changed), {
        "user_id": user_id,
        "status": status,
        "last_seen_at": now,
    }


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
