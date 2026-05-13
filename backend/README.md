# Backend

FastAPI + SQLAlchemy + Postgres + Redis. MCP server lives inside this app.

Before editing: read `../rules.md` and `../skills.md`.

## Layout

- `app/api/` — REST routes (admin UI, webhook receivers).
- `app/core/` — config, auth, permission engine, exceptions.
- `app/db/` — SQLAlchemy models and Alembic migrations.
- `app/mcp/` — MCP server: tool registry + tool implementations under `tools/`.
- `app/services/` — business logic. **Permission checks live here**, not in routes.
- `app/git/` — GitHub API client + webhook handler.
- `app/events/` — Redis pub-sub event bus + WebSocket gateway.

> **⚠ All commands run inside the `backend` container. See `../rules.md` § 1.**

## Run

The backend auto-starts via `docker compose up -d`. To watch logs:

```bash
docker compose logs -f backend
```

## Test

```bash
docker compose exec backend pytest
```

## Lint & typecheck

```bash
docker compose exec backend ruff check .
docker compose exec backend mypy --strict app
```

## Migrations

```bash
docker compose exec backend alembic revision --autogenerate -m "<description>"
docker compose exec backend alembic upgrade head
docker compose exec backend alembic downgrade -1
```

## Shell access

```bash
docker compose exec backend bash
docker compose exec postgres psql -U projecthub -d projecthub
docker compose exec redis redis-cli
```
