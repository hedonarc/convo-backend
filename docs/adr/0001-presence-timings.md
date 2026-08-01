# 1. Presence TTL and heartbeat interval

Date: 2026-08-02
Status: Accepted

## Context

A user is online for as long as they hold a WebSocket. But a browser that
crashes, a laptop that sleeps, or a process killed mid-deploy never sends a
disconnect frame, so the socket's absence has to be inferred.

Each connection therefore writes a Redis key with a TTL, and `UserConsumer`
refreshes it on a timer. If the refreshes stop, the key expires and the
connection stops counting toward the user's status.

Two numbers govern this:

- `presence.PRESENCE_TTL_SECONDS` — how long a connection survives unrefreshed
- `UserConsumer.HEARTBEAT_INTERVAL_SECONDS` — how often it is refreshed

They are defined in different files because one is a property of the store and
the other of the socket, which is exactly why the relationship between them is
easy to break.

## Decision

`TTL = 90s`, `heartbeat = 30s`.

**The invariant: `heartbeat < TTL / 2`.** At 30s and 90s a connection survives
two missed beats, so an unlucky GC pause or a brief Redis blip does not show
the user as offline. Change one number and you must change the other.

Steady-state cost is one Redis `EXPIRE` per connection per interval — one op,
deliberately the cheapest call in the module.

## Consequences

- A crashed tab is reflected to peers within roughly `TTL` seconds. 90s is
  slow enough to be noticeable; it is the price of not paying for a shorter
  TTL with proportionally more Redis traffic.
- These values favour a responsive "user appears online" over Redis op budget.
  To cut spend, raise both together and keep the invariant.
- Presence is ephemeral by design. There is no reconciliation job; a restart
  simply shows everyone offline until they reconnect.

## Alternatives considered

**Longer TTL with a sweeper job.** Cheaper in steady state, but adds a
scheduled task and a second source of truth for liveness. Not worth it at this
scale.

**Redis keyspace notifications on expiry.** Would let peers learn immediately
rather than at the next recompute. Requires enabling `notify-keyspace-events`
on the server, which is not guaranteed on managed Redis. Revisit if offline
detection latency becomes a real complaint.
