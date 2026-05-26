---
type: component
files:
  - frontend/index.html
  - frontend/package.json
  - frontend/vite.config.ts
  - frontend/tsconfig.json
  - frontend/tailwind.config.js
  - frontend/src/main.tsx
  - frontend/src/App.tsx
  - frontend/src/api/client.ts
  - frontend/src/stores/auth.ts
  - frontend/src/hooks/useWebSocket.ts
  - frontend/src/hooks/useMe.ts
  - frontend/src/hooks/useEnsureBoardWorkflow.ts
  - frontend/src/pages/Boards.tsx
  - frontend/src/pages/BoardDetail.tsx
  - frontend/src/pages/BoardSettings.tsx
  - frontend/src/pages/TicketDetail.tsx
  - frontend/src/pages/Login.tsx
  - frontend/.env.development.example
  - frontend/src/components/Layout.tsx
  - frontend/src/components/RequireAuth.tsx
  - frontend/src/components/ThemeProvider.tsx
  - frontend/src/components/WorkflowEditor.tsx
  - frontend/src/components/PermissionMatrix.tsx
  - frontend/src/components/MembersTab.tsx
  - frontend/src/components/NotificationBell.tsx
  - frontend/src/components/MermaidBlock.tsx
  - frontend/src/components/MarkdownRenderer.tsx
  - frontend/src/lib/stateColor.ts
  - frontend/src/lib/utils.ts
  - frontend/src/types/api.ts
last_touched_ticket: PH-148
status: active
---

# Frontend

> React 18 + Vite + Tailwind + shadcn/ui-style component layer that consumes the [[components/backend]] REST API plus an MCP-over-HTTP control surface and renders the live ProjectHub kanban + ticket workspace, with real-time updates via a self-managed WebSocket hook.

## Current behavior

The frontend is a single-page React 18 app bundled with Vite 5 (`vite.config.ts`), strict TypeScript (`tsconfig.json`, `noUncheckedIndexedAccess`, `strict: true`), and Tailwind 3 (`tailwind.config.js`) for styling. It boots from `src/main.tsx`, which mounts `<App />` inside a top-level `QueryClientProvider` (TanStack Query 5, `retry: 1`, `refetchOnWindowFocus: false`). `src/App.tsx` wraps everything in `ThemeProvider` and `BrowserRouter` and declares the route table: a public `/login` and an `Outlet`-style protected shell (`RequireAuth → Layout`) that hosts `/` (Boards index), `/boards/:boardKey` (BoardDetail kanban), `/boards/:boardKey/settings` (BoardSettings + WorkflowEditor + MembersTab + PermissionMatrix), and `/boards/:boardKey/tickets/:ticketKey` (TicketDetail). The `Layout` shell (`src/components/Layout.tsx`) renders the persistent header (logo, Boards nav, `NotificationBell`, `ThemeToggle`, logout button) plus the `Outlet` for nested routes.

Backend communication is centralized in `src/api/client.ts`, which exposes a flat `api` object (a hand-rolled `request<T>` helper plus typed methods for boards, tickets, transitions, claims, comments, history, notifications, board membership, and actor listing). Two transports are used in parallel against the same backend: REST under `/api/*` for everything user-facing (boards, tickets, comments, notifications, auth/me, membership) and an MCP-over-HTTP envelope (`mcpCall<T>` → `POST /mcp/call/{tool}`, unwraps the `{ tool, result }` envelope) for workflow operations (`list_workflows`, `create_workflow`, `update_workflow`, `add_transition`, `set_field_gates`, `activate_workflow`, `ensure_board_workflow`, `delete_workflow`, `delete_state`). Both surfaces share auth via `getStoredToken()` from the Zustand auth store (`src/stores/auth.ts`); a bearer token is read from `localStorage["projecthub.token"]` and attached to every request. On `401` both helpers call `useAuth.getState().logout()`, clearing the token and triggering `RequireAuth` to redirect to `/login`. Errors are normalized into an `ApiRequestError` class carrying the HTTP status plus the parsed `{ error, message, detail }` body so call sites can surface backend domain errors (`invalid_transition`, `field_gate_not_met`, `permission_denied`) verbatim. Vite proxies `/api` and `/mcp` to `http://backend:8000` in dev (`vite.config.ts`), and the `@/*` import alias maps to `src/*`.

State management is intentionally lean. TanStack Query owns server cache; mutations invalidate keyed queries to refetch board/ticket state after writes. Zustand only holds the auth token (`useAuth`) — there is no global UI store. Real-time updates flow through `src/hooks/useWebSocket.ts`, a 400-line custom hook that opens `ws(s)://<host>/ws/boards/:boardId?token=<bearer>` and pumps `EventEnvelope` messages from the backend Redis bus into React state. It implements: ping-pong heartbeat (default 30 s) with latency-derived `ConnectionQuality` (`excellent | good | poor | disconnected`), exponential-backoff reconnect with jitter (base 2 s, ×1.5, cap 30 s, max 10 attempts), structured `error` / `system_degradation` message routing, a connection-timeout watchdog (default 15 s), and stable callback refs so consumers can hot-swap `onMessage` without forcing reconnects. Pages call `useWebSocket({ boardId, token, onMessage })` and `queryClient.invalidateQueries(...)` inside their `onMessage` handler to keep the kanban + ticket views live. Theming is class-based dark mode (`darkMode: "class"` in `tailwind.config.js`) driven by `ThemeProvider`; state-color resolution lives in `src/lib/stateColor.ts`, which prefers a per-workflow-state `color` hex (rendered at 10 %/30 % alpha for background/border) and falls back to a `STATE_CATEGORIES`-keyed Tailwind class. Ticket markdown rendering uses `react-markdown` + `remark-gfm` (`MarkdownRenderer.tsx`) with `MermaidBlock.tsx` upgrading fenced ```mermaid blocks into diagrams via the `mermaid` lib. The workflow editor (`components/WorkflowEditor.tsx`, ≈ 27 KB) is built on `@xyflow/react` (formerly React Flow) for visual state/transition editing, with sortable dnd via `@dnd-kit/core` + `@dnd-kit/sortable`. TypeScript domain types live in `src/types/api.ts` (`Priority`, `TicketType`, `AgentPhase`, `ActorSummary`, `WorkflowState`, `FieldGates`, etc.) and are kept in lockstep with the backend via `npm run generate:types` (openapi-typescript script, per `frontend/README.md`). All dev/build/test commands run inside the `frontend` Docker container per the project's container-only rule (`rules.md` §1): `docker compose exec frontend npm run typecheck | lint | dev | build`.

## Design decisions (recent)

- Removed env-specific token fingerprint heuristic from WebSocket reconnect handler + 2 sibling sites [PH-148] — close codes are the source of truth, not token-prefix string match. `useWebSocket.ts:319-324` 1c7f53fb branch deleted; `utils/auth-fix.ts` deleted (dead code, zero importers); `pages/Login.tsx` DEV_TOKENS literal map replaced with `VITE_DEV_TOKEN_<role>` env-var reads from `.env.development.local` (gitignored, `.env.development.example` committed). Production bundle verified clean of fingerprint.
- Initial documentation [bootstrap] — auto-generated by jarwis-init bootstrap flow

## Known gotchas

- `src/components/BoardSettingsDialog.tsx:62,80,110` — three `TODO: Implement actual API call` markers for board member add / remove / role-change inside the legacy `BoardSettingsDialog`; live membership management has since moved to `MembersTab.tsx` + `MembershipRow.tsx` against the real `/api/boards/:id/members` REST endpoints, so this dialog appears to be an obsolete code path that still ships. Reconcile or delete before next refactor. [bootstrap]
- `src/api/client.ts:158-172` — four `@deprecated` workflow REST helpers (`addWorkflowState`, `deleteWorkflowState`, `updateWorkflowStates`, `updateWorkflowTransitions`) hit endpoints the backend never implemented (PH-21 era, 404). They are still exported; new callers must use the `mcpCall`-backed workflow helpers below them (`listWorkflows`, `addTransition`, `setFieldGates`, etc.). [bootstrap]
- `src/ws/` directory is empty — the layout documented in `frontend/README.md` ("`src/ws/` — WebSocket client + subscription manager") no longer matches reality. The actual implementation lives in `src/hooks/useWebSocket.ts`; the empty `ws/` folder + README line will mislead newcomers until cleaned up. [bootstrap]
- `src/stores/auth.ts:20-43` — `setToken` performs a localStorage write then immediately re-reads to assert sync, logging a warning on mismatch. This is dev-only diagnostic noise (gated on `import.meta.env.DEV`) but indicates a previously-observed token desync; if you touch the auth store, preserve the write-then-verify pattern. [bootstrap]

## Related

- [[overview]]
- [[index]]
- [[components/backend]]
