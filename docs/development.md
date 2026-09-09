# Backend Development

This guide covers backend workflows and quality standards.

## Code Quality (Ruff)

Run from `backend/`:

```bash
uv run ruff check .
uv run ruff check --select I .
uv run ruff check --fix .
uv run ruff format .
```

## Django Migrations

```bash
uv run manage.py makemigrations
uv run manage.py migrate
uv run manage.py showmigrations
```

## Profiling

### Django Silk
[Django Silk](https://github.com/jazzband/django-silk) is integrated for live profiling of API requests and database queries.

- **Access Dashboard:** `http://127.0.0.1:8000/silk/`
- **Configuration:** Profiling is enabled via `SILKY_PYTHON_PROFILER = True` in `settings/local.py`.

## ASGI & WebSockets

During development, `uv run manage.py runserver` will automatically use the ASGI application defined in `config/asgi.py` (powered by Daphne) to handle both HTTP and WebSocket connections.

```base
docker run --rm -p 6379:6379 redis:7
```

Ensure **Redis** is running, as the `CHANNEL_LAYERS` setting expects it for WebSocket group communication.


#### Advanced Profiling

##### Profile Specific Code Blocks

```python
from silk.profiling.profiler import silk_profile

@silk_profile(name="Expensive Calculation")
def my_view(request):
    # ...
```

##### Profile Specific Database Queries

```python
from silk.profiling.profiler import silk_profile

with silk_profile(name="Custom Query Info"):
    result = User.objects.filter(is_active=True)
    # ...
```

## Seeding demo data

An empty database tells you nothing — no unread dot, no ticks, no ordering, no
scrollback. `seed_demo` fills one with a cast and a set of conversations chosen
to put every visible state on screen at once.

```bash
make seed          # add the demo cast and conversations
make seed-fresh    # wipe conversations and demo accounts first, then seed
```

Log in as `demo` / `demo12345`. Every seeded peer shares that password, so a
second browser signed in as `maya` is enough to watch typing, presence and
receipts move in real time.

What it deliberately covers:

| conversation | shows |
|---|---|
| Maya Chen | unread messages — the sidebar dot |
| Dan Kowalski | your last message delivered but unread — single tick |
| Tomás Ferreira | delivered to their device — double tick |
| Priya Nair | an edited message |
| Saeed Al-Amin | a soft-deleted message |
| admin | the empty-conversation state |

Timestamps are spread over the last few days so relative times ("17m", "3h",
"1d") render realistically and the sidebar has a natural order.

`seed_demo` refuses to run with `DEBUG=False`. It creates accounts with known
passwords and is only ever meant for local use.
