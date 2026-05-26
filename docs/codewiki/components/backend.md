---
type: component
files:
  - backend/app/main.py
  - backend/app/cli.py
  - backend/app/schemas.py
  - backend/app/api/tickets.py
  - backend/app/api/boards.py
  - backend/app/api/actors.py
  - backend/app/api/auth.py
  - backend/app/api/websocket.py
  - backend/app/api/deps.py
  - backend/app/mcp/server.py
  - backend/app/core/permissions.py
  - backend/app/core/security.py
  - backend/app/core/exceptions.py
  - backend/app/core/config.py
  - backend/app/core/websocket_manager.py
  - backend/app/db/models/core.py
  - backend/app/db/session.py
  - backend/app/db/migrations/versions/
  - backend/app/services/tickets.py
  - backend/app/services/workflows.py
  - backend/app/services/stale_claims.py
  - backend/app/services/boards.py
  - backend/app/services/history.py
  - backend/app/services/notifications.py
  - backend/app/services/serializers.py
  - backend/app/events/bus.py
  - backend/app/events/dispatcher.py
  - backend/app/git/parser.py
  - backend/app/git/webhook.py
  - backend/pyproject.toml
last_touched_ticket: bootstrap
status: active
---

# Backend

> FastAPI 0.115 + SQLAlchemy 2 async + Alembic + PostgreSQL 16 + Redis 7 service that powers ProjectHub's ticket state machine, MCP tool catalog, and WebSocket fan-out.

## Current behavior

The backend is a single ASGI app (`backend/app/main.py`) that exposes ProjectHub as **two parallel surfaces over the same domain logic**: a REST API under `/api/*` for the React frontend and an MCP catalog under `/mcp/*` for agent clients (Claude Code, Jarwis sub-agents). Both surfaces route through identical service functions in `backend/app/services/`, so permission checks, history writes, and event publication are uniform regardless of caller. The app boots with a `lifespan` context manager that starts the Redis-backed `EventBus` and a `stale_claim_cron` background task, then mounts CORS plus the routers `auth`, `actors`, `boards`, `tickets`, `notifications`, `preferences`, `git`, `mcp_server`, and `websocket`. Centralized exception handlers (`app/core/exceptions.py`, registered via `register_exception_handlers`) translate domain errors — `NotFound`, `PermissionDenied`, `InvalidTransition`, `FieldGateNotMet`, `AlreadyClaimed` — into structured HTTP responses with consistent payload shapes.

Persistence is async SQLAlchemy 2 against PostgreSQL (with SQLite/`aiosqlite` fallback for tests). The ORM (`app/db/models/core.py`) models `Actor`, `Board`, `BoardMembership`, `Workflow`, `BoardWorkflow` (junction for per-board workflow ownership — PH-97), `Ticket`, `Comment`, `Notification`, `TicketHistory`, and `UserPreference`. JSON columns are dialect-aware (`JSONB` on Postgres, plain `JSON` elsewhere) and arrays degrade similarly. Tickets carry workflow-driven `state` plus rich domain fields (`technical_depth`, `impact_analysis`, `test_plan`, `acceptance_criteria`, `steps_to_reproduce`, `branch_name`, `labels[]`, `claimed_by`/`claimed_at`, `agent_phase`). Schema evolution is managed by Alembic — versions live in `backend/app/db/migrations/versions/` (currently 10 revisions, most carrying a `PH-XX` slug). Authentication is bearer-token based: `Actor.token_hash` stores a bcrypt hash (`app/core/security.py:hash_token` / `verify_token`); `api/deps.py:current_actor` resolves the request token by iterating active actors and `verify_token`-matching the bearer credential. Permission checks are board-scoped (`app/core/permissions.py:require_permission`) and understand wildcards (`*`, `state.transition:*`, `ticket.release:*`, `ticket.update_field:<field-list>`), plus an `:if_assignee` suffix that treats `claimed_by` as equivalent to `assignee_id` so a sub-agent that claimed without `assign_ticket` still passes the gate.

The MCP catalog (`app/mcp/server.py`) exposes the same tools over two transports — a legacy REST shape (`GET /mcp/tools`, `POST /mcp/call/{tool}`) and full MCP JSON-RPC 2.0 (`POST /mcp` with `initialize` / `tools/list` / `tools/call`) — both delegating to one `_dispatch_tool` so behavior, permissions, and history are identical. Headline tools: `list_boards`, `get_board`, `query_tickets`, `get_ticket`, `get_state` (~200-char Coordinator self-verify probe), `get_ticket_slice` (caller-projected field subset), `create_ticket`, `update_ticket`, `assign_ticket`, `transition_state`, `add_comment`, `claim_ticket`, `release_ticket`, `update_agent_phase`, `create_branch_for_ticket`, plus workflow CRUD. Write tools default to a minimal response (`{ok, id, updated_fields, state}`) and opt into the full ticket payload via `verbose=true` — a deliberate budget choice that lets Coordinator self-verify cheaply. The ticket state machine lives in `services/tickets.py:transition_ticket_state` and consults the active workflow's `transitions` list + `allowed_roles` for role gating, with `assignee` treated as equivalent to `claimed_by` so an agent that claimed without `assign_ticket` still passes the gate. Field gates (`_missing_gate_fields`) are workflow-configurable (`services/workflows.py:get_field_gates_for_ticket_transition`) — e.g. `in_review → in_test` may require `test_plan`. Every state mutation writes a `TicketHistory` row and publishes an `EventEnvelope` via the Redis pub/sub `EventBus` (`app/events/bus.py`); the `websocket` router subscribes per-board / per-ticket channels and streams envelopes to connected clients, with exponential-backoff reconnect and a `system_degradation` fallback envelope when Redis is unreachable. A separate `stale_claim_cron` (`services/stale_claims.py`, 60s interval, 5-minute timeout) sweeps tickets whose `claimed_at` exceeds the heartbeat threshold and auto-releases them — the safety net that lets sub-agents crash without leaving a dangling lock. The `app/cli.py` module provides operational commands (board bootstrap, `create_jarwis_actors`, `update_board_roles`) used by `jarwis-init.sh` and manual maintenance. Git commit parsing (`app/git/parser.py`) extracts `[A-Z]{2,5}-\d+` ticket keys from messages and validates conventional-commit format for the webhook ingestion in `app/git/webhook.py`.

## Design decisions (recent)

- Initial documentation [bootstrap] — auto-generated by jarwis-init bootstrap flow

## Known gotchas

(none discovered during bootstrap)

## Related

- [[overview]]
- [[index]]
- [[components/frontend]]
