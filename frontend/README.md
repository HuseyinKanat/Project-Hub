# Frontend

React 18 + Vite + Tailwind + shadcn/ui. Real-time via WebSocket.

Before editing: read `../rules.md` and `../skills.md`.

## Layout

- `src/pages/` — top-level routes (BoardView, TicketDetail, Settings).
- `src/components/` — reusable UI (Kanban, TicketCard, ActivityTimeline, AgentBadge).
- `src/stores/` — Zustand stores (auth, UI state).
- `src/api/` — TanStack Query hooks for backend.
- `src/ws/` — WebSocket client + subscription manager.
- `src/types/` — generated TypeScript types from backend OpenAPI.

> **⚠ All commands run inside the `frontend` container. See `../rules.md` § 1.**

## Run

The frontend auto-starts via `docker compose up -d`. Vite dev server on `http://localhost:5173` with HMR.

To watch logs:

```bash
docker compose logs -f frontend
```

## Install a new package

```bash
docker compose exec frontend npm install <package>
# Then rebuild the image so it persists:
docker compose build frontend
docker compose up -d frontend
```

## Type regeneration (after backend schema changes)

```bash
docker compose exec frontend npm run generate:types
```

## Lint & typecheck

```bash
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run lint
```
