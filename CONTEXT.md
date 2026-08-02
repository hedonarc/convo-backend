# Domain language

The words this codebase uses, and what they mean here. If a term is on this
list, use it — in code, in commits, in PRs. If you need a word that is not on
this list, add it.

## Core

**Conversation** — a thread between two or more people. Direct conversations
are keyed by their participant ids (`conversation_key`), so asking for one
twice returns the same one. A conversation with yourself is legal and keyed
`self_<id>`.

**Participant** — a user's membership in one conversation. Where that user's
read and delivery pointers live. Membership is the authorization check behind
every socket connection.

**Message** — one thing someone said. Edited messages keep their previous text
in `prev_content`; deleted ones are soft-deleted, keeping the row so pointers
that reference it stay valid.

**Invite** — a link that lets someone join a conversation they are not yet a
participant of.

## Realtime

**Room group** — `conversation_<id>`. Every socket currently watching one
conversation. Carries new messages, typing, and both kinds of receipt.

**Per-user channel** — `user_<id>`. Every socket one person has open,
regardless of what they are looking at. Carries the sidebar's updates,
presence, and invite notifications. This is what lets a user find out about a
conversation they do not have open.

**Event type** — the string in a `group_send` payload's `type` key. The
channel layer dispatches by calling the consumer method of that exact name, so
the two must match. Declared once in `services/fanout.py`; `test_fanout.py`
asserts each one resolves to a real handler.

**Fanout** — putting an event on a group. `services/fanout.py` is the only
module that builds group names or event types.

**Announcement** — a fanout described in domain terms rather than transport
terms: "move this conversation to the top of every sidebar". Lives in
`services/realtime.py`.

**Best-effort** — an announcement that must never break the operation that
triggered it. Persisting a message succeeds even when nobody can be told.
`fanout.best_effort` marks these.

## Pointers

Both are per-participant and both only ever move forward. A repeat or an older
id writes nothing, which is what stops the announcements they trigger from
bouncing between consumers.

**Delivery pointer** — `last_delivered_message_id`. How far a peer's messages
have reached this participant's device. Advances when a message arrives on
*any* of their sockets, which is why `services/delivery.py` owns the rule
rather than either consumer. Drives the double tick.

**Read pointer** — `last_read_message_id`. How far this participant has
actually looked. Set by an explicit `read` action from the client, never
inferred. Drives "Seen" and the unread dot.

## Presence

**Presence** — who is online. Held in Redis, never in the database, and
deliberately lost on restart.

**Connection state** — one socket's `online` or `away`, held in
`presence:conn:{<user>}:<channel>` with a TTL. The TTL is what makes a tab
that died without a disconnect frame disappear on its own.

**User status** — the roll-up: a user is as present as their most-present tab.
`online` if any is online, `away` if any is away, `offline` once none survive.

**Recompute** — reading the live connections, pruning the dead ones, and
writing the resulting user status. One atomic Redis script, because a
read-modify-write let two tabs persist a status neither computed.

**Manual away** — an explicit "set me to Away" from the account menu. Outranks
tab focus, and is session-scoped: the last tab leaving clears it, matching
Slack.

**Peers** — everyone who shares at least one conversation with a user. The
presence audience. Strangers never see each other's status.

## Infrastructure

**Channel layer** — `channels_redis`. Pub/sub transport for `group_send`.
Transient, and entirely Channels' business.

**Presence store** — our own Redis keys under `presence:*`, reached through
`services/redis_store.py`. Same server as the channel layer, same `REDIS_URL`
setting, unrelated job. Confusing these two is the single easiest way to get
lost in this codebase.

**Adapter** — which client `redis_store` hands out, named by
`settings.REDIS_CLIENT`: real Redis in production, fakeredis under test. It is
why the suite needs no running server.
