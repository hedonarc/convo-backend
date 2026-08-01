# Backend Architecture

High-level backend architecture notes.

## Current Modules

- `config/`: Django settings, root WSGI/ASGI routing.
- `apps/`: Local Django apps (`authentication`, `users`, `conversations`).
  - `conversations/`:
    - `consumers.py`: `AsyncWebsocketConsumer` for real-time messaging. Uses action-based routing to handle incoming frames.
- `utils/`: Shared helper utilities.

## Real-time Messaging

Convo uses **Django Channels** to handle WebSocket connections.

- **ASGI Server:** Daphne is used as the ASGI application server.
- **Consumer Logic:** We use asynchronous consumers (`AsyncWebsocketConsumer`) to manage long-lived connections efficiently.
- **Channel Layer:** A Redis-backed channel layer (`channels_redis`) is used for group communication (e.g., broadcasting messages to all participants in a conversation).

## Where the rules live

Consumers parse frames and write to sockets. Everything else sits in
`services/`, grouped by the rule it owns rather than by mechanism:

| module | owns |
|---|---|
| `message_service` | persisting a message and announcing it |
| `delivery` | when a message counts as delivered |
| `read_receipts` | how far a participant has read |
| `presence` | online / away / offline across a user's tabs |
| `conversation_service` | creating conversations, and who belongs to one |
| `realtime` | what each announcement contains |
| `fanout` | group names and event types |
| `redis_store` | the presence store's connection |

Each is `async`-callable where a consumer needs it; the
`database_sync_to_async` wrapping is an implementation detail rather than a
module of its own. A previous `queries.py` grouped every ORM call together
because they shared that wrapper, which meant a single rule — advancing a
delivery pointer, say — was split across two files.

## Redis has two jobs

Both read `settings.REDIS_URL`, but they are otherwise unrelated:

| | Channel layer | Presence store |
|---|---|---|
| owned by | `channels_redis` | `services/redis_store.py` |
| holds | transient pub/sub for `group_send` | `presence:*` keys, our own schema and TTLs |
| configured by | `CHANNEL_LAYERS` | `REDIS_CLIENT` (dotted path to an adapter) |
| in tests | `InMemoryChannelLayer` | `fakeredis` |

`REDIS_CLIENT` is the seam. `settings/test.py` points it at `fake_client`, which is why the suite needs no running Redis and CI has no service container.

## Putting an event on the wire

`services/fanout.py` owns every group name and event type. The event type
string must equal a handler method name on the receiving consumer — that is
how the channel layer dispatches — so `tests/test_fanout.py` asserts each one
resolves. Call `fanout.to_conversation(...)` or `fanout.to_users(...)` rather
than building group names by hand.


## Planned Additions

- PostgreSQL for production deployments.
