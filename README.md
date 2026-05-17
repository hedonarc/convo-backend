# Convo

Convo backend is a backend for a real-time chat platform.

## Architecture

- `backend/`: Django REST Framework API for authentication, messaging, and user management. Includes real-time support via Django Channels and Redis.

## Documentation Map

- Project docs index: [`docs/index.md`](./docs/index.md)
[text](../Convo/docs)- Backend docs:
  - Setup: [`docs/backend/setup.md`](./docs/backend/setup.md)
  - Development: [`docs/backend/development.md`](./docs/backend/development.md)
  - Architecture: [`docs/backend/architecture.md`](./docs/backend/architecture.md)
  - API: [`docs/backend/api.md`](./docs/backend/api.md)
  - Testing: [`docs/backend/testing.md`](./docs/backend/testing.md)
  - Translations: [`docs/backend/translations.md`](./docs/backend/translations.md)

### Running Backend Commands
To run individual backend commands (like migrations, shell, etc.):
```bash
uv run manage.py <command>
```

## Development and Contributing

- Backend workflows (Ruff, migrations, profiling): [`docs/backend/development.md`](./docs/backend/development.md)
- Global contribution standards: [`CONTRIBUTING.md`](./CONTRIBUTING.md)

## 🤝 Contributors

This project is developed by:

- **Abubakar Khawaja** — Full Stack Developer (React + Django)
- **Muhammad Suleman Butt** — Full Stack Developer (React / React Native + Django)
