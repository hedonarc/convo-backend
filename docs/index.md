# Convo Documentation

This directory contains backend project documentation.

## Sections

## Start Here

- Domain language: [`../CONTEXT.md`](../CONTEXT.md) — read this first
- Backend setup: [`setup.md`](./setup.md)
- Global contribution guide: [`../CONTRIBUTING.md`](../CONTRIBUTING.md)
- Backend development workflows: [`development.md`](./development.md)
- Backend translations: [`translations.md`](./translations.md)

## Decisions

Why the realtime layer is shaped the way it is. Read the relevant one before
changing that area.

- [`adr/0001-presence-timings.md`](./adr/0001-presence-timings.md) — the TTL and heartbeat invariant
- [`adr/0002-two-redis-roles.md`](./adr/0002-two-redis-roles.md) — one `REDIS_URL`, two unrelated jobs
- [`adr/0003-delivery-and-read-pointers.md`](./adr/0003-delivery-and-read-pointers.md) — why each pointer has exactly one home
