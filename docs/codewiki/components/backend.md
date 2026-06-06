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
  - backend/app/git/reader.py
  - backend/app/git/sync.py
  - backend/app/git/_linkage.py
  - backend/app/api/repositories.py
  - backend/app/services/repositories.py
  - backend/app/db/migrations/versions/20260604_0006_ph_150_repositories.py
  - backend/app/db/migrations/versions/20260604_0007_ph_152_git_cache.py
  - backend/app/db/migrations/versions/20260606_0008_ph_193_sonarqube_metric.py
  - backend/app/services/sonarqube.py
  - backend/pyproject.toml
last_touched_ticket: PH-193
status: active
---

# Backend

> FastAPI 0.115 + SQLAlchemy 2 async + Alembic + PostgreSQL 16 + Redis 7 service that powers ProjectHub's ticket state machine, MCP tool catalog, and WebSocket fan-out.

## Current behavior

The backend is a single ASGI app (`backend/app/main.py`) that exposes ProjectHub as **two parallel surfaces over the same domain logic**: a REST API under `/api/*` for the React frontend and an MCP catalog under `/mcp/*` for agent clients (Claude Code, Jarwis sub-agents). Both surfaces route through identical service functions in `backend/app/services/`, so permission checks, history writes, and event publication are uniform regardless of caller. The app boots with a `lifespan` context manager that starts the Redis-backed `EventBus` and a `stale_claim_cron` background task, then mounts CORS plus the routers `auth`, `actors`, `boards`, `tickets`, `notifications`, `preferences`, `git`, `repositories` (PH-150 G1), `mcp_server`, and `websocket`. Centralized exception handlers (`app/core/exceptions.py`, registered via `register_exception_handlers`) translate domain errors — `NotFound`, `PermissionDenied`, `InvalidTransition`, `FieldGateNotMet`, `AlreadyClaimed` — into structured HTTP responses with consistent payload shapes.

Persistence is async SQLAlchemy 2 against PostgreSQL (with SQLite/`aiosqlite` fallback for tests). The ORM (`app/db/models/core.py`) models `Actor`, `Board`, `BoardMembership`, `Workflow`, `BoardWorkflow` (junction for per-board workflow ownership — PH-97), `Ticket`, `Comment`, `Notification`, `TicketHistory`, `UserPreference`, and `Repository` (PH-150 G1 — git config, 1 board : 0..1 repo, FK `boards.id` ondelete=CASCADE). JSON columns are dialect-aware (`JSONB` on Postgres, plain `JSON` elsewhere) and arrays degrade similarly. Tickets carry workflow-driven `state` plus rich domain fields (`technical_depth`, `impact_analysis`, `test_plan`, `acceptance_criteria`, `steps_to_reproduce`, `branch_name`, `labels[]`, `claimed_by`/`claimed_at`, `agent_phase`). Schema evolution is managed by Alembic — versions live in `backend/app/db/migrations/versions/` (currently 10 revisions, most carrying a `PH-XX` slug). Authentication is bearer-token based: `Actor.token_hash` stores a bcrypt hash (`app/core/security.py:hash_token` / `verify_token`); `api/deps.py:current_actor` resolves the request token by iterating active actors and `verify_token`-matching the bearer credential. Permission checks are board-scoped (`app/core/permissions.py:require_permission`) and understand wildcards (`*`, `state.transition:*`, `ticket.release:*`, `ticket.update_field:<field-list>`), plus an `:if_assignee` suffix that treats `claimed_by` as equivalent to `assignee_id` so a sub-agent that claimed without `assign_ticket` still passes the gate.

The MCP catalog (`app/mcp/server.py`) exposes the same tools over two transports — a legacy REST shape (`GET /mcp/tools`, `POST /mcp/call/{tool}`) and full MCP JSON-RPC 2.0 (`POST /mcp` with `initialize` / `tools/list` / `tools/call`) — both delegating to one `_dispatch_tool` so behavior, permissions, and history are identical. Headline tools: `list_boards`, `get_board`, `query_tickets`, `get_ticket`, `get_state` (~200-char Coordinator self-verify probe), `get_ticket_slice` (caller-projected field subset), `create_ticket`, `update_ticket`, `assign_ticket`, `transition_state`, `add_comment`, `claim_ticket`, `release_ticket`, `update_agent_phase`, `create_branch_for_ticket`, plus workflow CRUD. Write tools default to a minimal response (`{ok, id, updated_fields, state}`) and opt into the full ticket payload via `verbose=true` — a deliberate budget choice that lets Coordinator self-verify cheaply. The ticket state machine lives in `services/tickets.py:transition_ticket_state` and consults the active workflow's `transitions` list + `allowed_roles` for role gating, with `assignee` treated as equivalent to `claimed_by` so an agent that claimed without `assign_ticket` still passes the gate. Field gates (`_missing_gate_fields`) are workflow-configurable (`services/workflows.py:get_field_gates_for_ticket_transition`) — e.g. `in_review → in_test` may require `test_plan`. Every state mutation writes a `TicketHistory` row and publishes an `EventEnvelope` via the Redis pub/sub `EventBus` (`app/events/bus.py`); the `websocket` router subscribes per-board / per-ticket channels and streams envelopes to connected clients, with exponential-backoff reconnect and a `system_degradation` fallback envelope when Redis is unreachable. A separate `stale_claim_cron` (`services/stale_claims.py`, 60s interval, 5-minute timeout) sweeps tickets whose `claimed_at` exceeds the heartbeat threshold and auto-releases them — the safety net that lets sub-agents crash without leaving a dangling lock. A third lifespan task, `sonarqube_poll_cron` (`services/sonarqube.py`, PH-193), polls a self-hosted SonarQube Community Build for each board's main-branch quality metrics, upserts the latest snapshot into the `sonarqube_metrics` table (1 board : 0..1 row, unique `board_id`), and publishes a `sonarqube_synced` envelope on `board:{id}`; it is gated on `settings.sonarqube_enabled` (default False → task never created, no-op) and surfaces as `BoardResponse.health` (eager-loaded via `selectinload(Board.sonarqube_metric)`, mirroring `repository`). The `app/cli.py` module provides operational commands (board bootstrap, `create_jarwis_actors`, `update_board_roles`) used by `jarwis-init.sh` and manual maintenance. Git commit parsing (`app/git/parser.py`) extracts `[A-Z]{2,5}-\d+` ticket keys from messages and validates conventional-commit format for the webhook ingestion in `app/git/webhook.py`.

## Design decisions (recent)

- SonarQube board-health poller + `SonarQubeMetric` upsert-latest cache [PH-193] — new `services/sonarqube.py` httpx client (`BasicAuth(token, "")` — portable Community Build token auth) calls `GET /api/qualitygates/project_status` + `GET /api/measures/component`; the poller mirrors `git_poll_cron` (fresh session per tick, `except asyncio.CancelledError` clean shutdown, per-tick `except Exception`). **Layered error isolation**: the client returns `None` on any error (SonarQube down / 401 / malformed JSON / project not yet scanned) so the loop never crashes; a board with no resolvable projectKey (`Board.sonarqube_project_key` null + not in `sonarqube_project_key_map`) is skipped silently. Model stores **latest only** (`UniqueConstraint(board_id)`, upsert in place) — not append-history; trends are out of scope. `raw_measures` JSON keeps the verbatim measure map for forward-compat (new metrics need no migration). Migration `20260606_0008` additive (nullable `boards.sonarqube_project_key` + new `sonarqube_metrics` table).
- board-level `sonarqube_synced` event reuses the ticket-shaped `EventEnvelope` with empty `ticket_id`/`ticket_key` sentinels [PH-193] — the WS board channel ignores ticket fields; payload mirrors `BoardHealth` so the frontend can patch cached `board.health` without a refetch. Token is never logged, never in the API response, never in the event payload.
- `repair_workflow --board <KEY>` CLI added to restore a corrupted `backlog->to_do` transition [PH-168] — an E2E test reset PH's active workflow and stripped `allowed_roles` from the `backlog->to_do` transition (injecting a stray `technical_depth` field_gate), which locked the whole board because the transition engine rejects every actor when `allowed_roles` is absent. The command resolves the board's *active* workflow via `services/boards.get_active_workflow` (active `BoardWorkflow` junction row, falling back to `board.workflow_id`) — the same resolution the engine uses — then rewrites **only** the `backlog->to_do` entry back to the known-good `{"allowed_roles": ["pm", "architect"]}` shape (mirrors `DEFAULT_TRANSITIONS[0]`, no field gate). Logic split into a pure helper `repair_backlog_to_do_transitions(transitions) -> (new_list, changed)` (deep-copies every other transition verbatim, raises `ValueError` if no `backlog->to_do` exists) and the async DB wrapper `repair_workflow`. Idempotent: a healthy workflow is a no-op that prints "already healthy". Run against PH live: `docker compose exec backend python -m app.cli repair_workflow --board PH`.
- G3 sync service (`app/git/sync.py`) adds 4 cache tables (`git_commits`, `git_branches`, `git_commit_files`, `git_commit_tickets`) populated by `sync_repo(session, board)`; G4-G5 read endpoints will serve from cache without spawning git subprocesses per request [PH-152] — migration `20260604_0007` additive; downgrade drops all 4 tables
- `git_commit_tickets` unique constraint `(commit_id, ticket_id)` is the dedupe gate for both webhook and sync paths; first-observation wins for history timestamps [PH-152]
- board-scoped `git_synced` WS envelope uses `ticket_id="system"` sentinel (same pattern as `system_degradation`) to signal cache refresh to the frontend [PH-152]
- `_linkage.py` extracted as shared internal module for `find_ticket_by_key` + `get_system_actor_id` used by both `webhook.py` and `sync.py` [PH-152]
- Initial documentation [bootstrap] — auto-generated by jarwis-init bootstrap flow
- GitPython chosen over dulwich for G2+ reader (SourceTree-grade diff sadakati — rename detection, unified diff, line-level) [PH-150] — dulwich diff fidelity insufficient for epic goal; image cost (~30MB git binary) justified by load-bearing diff quality for G5
- Repository model uses string `provider` column + CHECK constraint instead of DB Enum (migration flexibility — avoids Alembic Enum drop/add complexity on provider set expansion) [PH-150]
- `GET /api/boards/{key}/git/status` readable by any board member (read-only, no admin); PUT/DELETE restricted to board admin (write contract) [PH-150]
- `selectinload(Board.repository)` added to `get_board` + `list_boards` — eager-loads repo for `board_response` serialization; avoids async MissingGreenlet error from lazy-load [PH-150]

## Known gotchas

- Mutating a JSON column in place may not persist [PH-168] — `Workflow.transitions` is a JSON column. Editing a nested list/dict element in place (e.g. `transition["allowed_roles"] = [...]`) is not seen by SQLAlchemy's default change tracking, so the UPDATE never fires. `repair_workflow` sidesteps this by **reassigning a brand-new list** (`workflow.transitions = new_transitions`) *and* calling `flag_modified(workflow, "transitions")`. Any future workflow-JSON edit must do the same or the write is silently dropped.

## Related

- [[overview]]
- [[index]]
- [[components/frontend]]
