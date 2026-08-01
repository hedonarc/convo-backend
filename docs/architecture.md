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
- **Query Decoupling:** To keep consumers clean, all database interactions are abstracted into a `queries.py` module and wrapped in `database_sync_to_async`.
- **Channel Layer:** A Redis-backed channel layer (`channels_redis`) is used for group communication (e.g., broadcasting messages to all participants in a conversation).

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
