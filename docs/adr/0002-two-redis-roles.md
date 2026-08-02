# 2. One Redis URL, two unrelated roles

Date: 2026-08-02
Status: Accepted

## Context

Redis appears twice in this backend and the two uses have nothing to do with
each other beyond sharing a server:

- **Channel layer** — `channels_redis` uses it as pub/sub transport for
  `group_send`. We never touch keys; Channels owns the format and the
  lifetime.
- **Presence store** — our own keys under `presence:*`, with our own schema
  and TTLs.

They used to be configured through three independent code paths.
`presence.py` built its own client from `os.environ["REDIS_URL"]` and fell
back to a hardcoded `127.0.0.1`, bypassing Django settings entirely.
`CHANNEL_LAYERS` was declared in `settings/base.py` and again in
`settings/production.py`. `config/asgi.py` reimplemented host parsing for all
three shapes `channels_redis` accepts. A comment in `presence.py` conceded the
problem: *"if the host ever moves, both will need updating in lockstep."*

Local development hid it, because everything defaulted to the same host.

## Decision

`settings.REDIS_URL` is the single source. The channel layer, the presence
store and the startup probe all read it.

`settings.REDIS_CLIENT` names the adapter that `services/redis_store.py` hands
out — a dotted path, following the same idiom as `STORAGES` and
`AUTHENTICATION_BACKENDS`:

- production and local: `redis_store.real_client`
- test: `redis_store.fake_client` (fakeredis)

Production requires `REDIS_URL` with no default. A production boot with no
Redis should fail loudly rather than quietly talk to localhost.

Presence keys carry a `{user_id}` hash tag so one user's keys share a slot and
the recompute script stays legal on a clustered Redis.

## Consequences

- The test suite needs no infrastructure. `settings/test.py` runs the channel
  layer in-process and points the presence store at fakeredis, so CI has no
  service container and the suite is a few seconds faster.
- The two roles are still easy to confuse when reading. `redis_store.py`'s
  docstring and `CONTEXT.md` both name the distinction explicitly; keep doing
  that.
- fakeredis has real Redis semantics, including TTL and Lua, which the
  125-line hand-written fake it replaced did not. It does not advance time,
  so TTL *expiry* still has no test.

## Alternatives considered

**Separate `REDIS_URL` and `PRESENCE_REDIS_URL`.** Would allow splitting the
two roles across servers, which is a real option if presence traffic ever
starts crowding out the channel layer. Rejected for now: two settings that are
always equal is a worse default than one setting, and splitting later is a
small change.

**Reading the channel layer's configured host for the presence client.**
Tempting because it is automatically consistent, but it means importing
`channels_redis` internals and it inverts the dependency — presence would be
configured by a setting that exists for something else.
