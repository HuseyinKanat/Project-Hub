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
  - frontend/src/pages/DiffDemo.tsx
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
  - frontend/src/components/diff/DiffViewer.tsx
  - frontend/src/components/diff/FileDiffView.tsx
  - frontend/src/components/diff/HunkView.tsx
  - frontend/src/lib/stateColor.ts
  - frontend/src/lib/utils.ts
  - frontend/src/lib/diff/parseDiff.ts
  - frontend/src/types/api.ts
  - frontend/src/types/git.ts
last_touched_ticket: PH-158
status: active
---

# Frontend

> React 18 + Vite + Tailwind + shadcn/ui-style component layer that consumes the [[components/backend]] REST API plus an MCP-over-HTTP control surface and renders the live ProjectHub kanban + ticket workspace, with real-time updates via a self-managed WebSocket hook.

## Current behavior

The frontend is a single-page React 18 app bundled with Vite 5 (`vite.config.ts`), strict TypeScript (`tsconfig.json`, `noUncheckedIndexedAccess`, `strict: true`), and Tailwind 3 (`tailwind.config.js`) for styling. It boots from `src/main.tsx`, which mounts `<App />` inside a top-level `QueryClientProvider` (TanStack Query 5, `retry: 1`, `refetchOnWindowFocus: false`). `src/App.tsx` wraps everything in `ThemeProvider` and `BrowserRouter` and declares the route table: a public `/login` and an `Outlet`-style protected shell (`RequireAuth → Layout`) that hosts `/` (Boards index), `/boards/:boardKey` (BoardDetail kanban), `/boards/:boardKey/settings` (BoardSettings + WorkflowEditor + MembersTab + PermissionMatrix), and `/boards/:boardKey/tickets/:ticketKey` (TicketDetail). The `Layout` shell (`src/components/Layout.tsx`) renders the persistent header (logo, Boards nav, `NotificationBell`, `ThemeToggle`, logout button) plus the `Outlet` for nested routes.

Backend communication is centralized in `src/api/client.ts`, which exposes a flat `api` object (a hand-rolled `request<T>` helper plus typed methods for boards, tickets, transitions, claims, comments, history, notifications, board membership, and actor listing). Two transports are used in parallel against the same backend: REST under `/api/*` for everything user-facing (boards, tickets, comments, notifications, auth/me, membership) and an MCP-over-HTTP envelope (`mcpCall<T>` → `POST /mcp/call/{tool}`, unwraps the `{ tool, result }` envelope) for workflow operations (`list_workflows`, `create_workflow`, `update_workflow`, `add_transition`, `set_field_gates`, `activate_workflow`, `ensure_board_workflow`, `delete_workflow`, `delete_state`). Both surfaces share auth via `getStoredToken()` from the Zustand auth store (`src/stores/auth.ts`); a bearer token is read from `localStorage["projecthub.token"]` and attached to every request. On `401` both helpers call `useAuth.getState().logout()`, clearing the token and triggering `RequireAuth` to redirect to `/login`. Errors are normalized into an `ApiRequestError` class carrying the HTTP status plus the parsed `{ error, message, detail }` body so call sites can surface backend domain errors (`invalid_transition`, `field_gate_not_met`, `permission_denied`) verbatim. Vite proxies `/api` and `/mcp` to `http://backend:8000` in dev (`vite.config.ts`), and the `@/*` import alias maps to `src/*`.

State management is intentionally lean. TanStack Query owns server cache; mutations invalidate keyed queries to refetch board/ticket state after writes. Zustand only holds the auth token (`useAuth`) — there is no global UI store. Real-time updates flow through `src/hooks/useWebSocket.ts`, a 400-line custom hook that opens `ws(s)://<host>/ws/boards/:boardId?token=<bearer>` and pumps `EventEnvelope` messages from the backend Redis bus into React state. It implements: ping-pong heartbeat (default 30 s) with latency-derived `ConnectionQuality` (`excellent | good | poor | disconnected`), exponential-backoff reconnect with jitter (base 2 s, ×1.5, cap 30 s, max 10 attempts), structured `error` / `system_degradation` message routing, a connection-timeout watchdog (default 15 s), and stable callback refs so consumers can hot-swap `onMessage` without forcing reconnects. Pages call `useWebSocket({ boardId, token, onMessage })` and `queryClient.invalidateQueries(...)` inside their `onMessage` handler to keep the kanban + ticket views live. Theming is class-based dark mode (`darkMode: "class"` in `tailwind.config.js`) driven by `ThemeProvider`; state-color resolution lives in `src/lib/stateColor.ts`, which prefers a per-workflow-state `color` hex (rendered at 10 %/30 % alpha for background/border) and falls back to a `STATE_CATEGORIES`-keyed Tailwind class. Ticket markdown rendering uses `react-markdown` + `remark-gfm` (`MarkdownRenderer.tsx`) with `MermaidBlock.tsx` upgrading fenced ```mermaid blocks into diagrams via the `mermaid` lib. The workflow editor (`components/WorkflowEditor.tsx`, ≈ 27 KB) is built on `@xyflow/react` (formerly React Flow) for visual state/transition editing, with sortable dnd via `@dnd-kit/core` + `@dnd-kit/sortable`. TypeScript domain types live in `src/types/api.ts` (`Priority`, `TicketType`, `AgentPhase`, `ActorSummary`, `WorkflowState`, `FieldGates`, etc.) and are kept in lockstep with the backend via `npm run generate:types` (openapi-typescript script, per `frontend/README.md`). All dev/build/test commands run inside the `frontend` Docker container per the project's container-only rule (`rules.md` §1): `docker compose exec frontend npm run typecheck | lint | dev | build`.

**G8 — Git client surface** (`src/types/git.ts`, `src/api/client.ts`, `src/hooks/useWebSocket.ts`): G8 adds the complete TypeScript type layer and API client for the G1–G7 git backend without introducing any UI components (those come in G9–G13). All git-surface types are defined in the new dedicated module `src/types/git.ts` (~230 LOC): literal unions (`GitProvider`, `GitChangeType`, `GitRefreshStatus`), repository types (`RepositorySummary`, `RepositoryResponse`, `RepositoryUpsertPayload`), commit types (`GitCommitSummary`, `GitCommitFile`/`GitCommitFileEntry` alias, `GitCommitDetail`), branch types (`GitBranchEntry`/`GitBranch` alias), list/graph responses (`GitGraphResponse`/`GitGraph`, `GitCommitsListResponse`, `GitBranchesListResponse`), diff types (`FileDiff`, `DiffResponse`/`CommitDiff`, `RangeDiffResponse`/`RangeDiff`), refresh types (`GitRefreshStatus`, `GitRefreshResponse`), ticket-commit types (`TicketCommitEntry`, `TicketCommitsResponse`), and WS payload type (`GitSyncedPayload`). Every interface carries a `@see backend/app/schemas.py:<ClassName>` JSDoc for manual cross-check. `src/api/client.ts` gains an `api.git` nested namespace (9 methods) plus two flat admin helpers (`api.setRepository`, `api.detachRepository`). The `api.git.refresh` method uses a direct `fetch` (no Bearer header) because the endpoint uses shared-secret auth via `X-Git-Refresh-Token` — see Gotchas. `src/hooks/useWebSocket.ts` gains an exported `isGitSyncedMessage(msg)` type-guard that narrows `WebSocketMessage` to `{ type: "git_synced"; payload: GitSyncedPayload }` for G9 invalidation use; `WebSocketMessage` interface is now exported. Existing consumers (`BoardDetail.tsx`, `TicketDetail.tsx`) are unmodified.

**G9 — DiffViewer component** (`src/lib/diff/parseDiff.ts`, `src/components/diff/*.tsx`, `src/pages/DiffDemo.tsx`): G9 introduces the complete diff rendering stack. `src/lib/diff/parseDiff.ts` is a custom unified diff parser (no external library; ~150 LOC; sideeffect-free pure function `parseUnifiedDiff(patch: string): Hunk[]`). It handles all real-world edge cases: hunk-without-comma (`@@ -1 +1 @@` → default 1 line), `\ No newline at end of file` meta lines, rename-only and mode-change-only patches (produce `[]`), and empty patch strings. The component tree has three layers: `HunkView.tsx` (internal, one hunk = `<table>` with 3 columns: old line no | new line no | content; row bg is `bg-green-50 dark:bg-green-950/30` for add, `bg-red-50 dark:bg-red-950/30` for del; hunks with >50 lines are collapsed by default with an expand button); `FileDiffView.tsx` (presentational, takes a `FileDiff` prop; handles binary marker `"Binary file — preview unavailable"`, truncated marker `"Diff truncated (file >1 MiB)"`, rename paths shown as `old → new`, change_type badge A/M/D/R/C with matching Tailwind colors, per-file collapse toggle); `DiffViewer.tsx` (container, 2-mode: `files={FileDiff[]}` data-prop or `fetch={kind:'commit'|'range', ...}` TanStack Query mode with loading/error states, summary header with file count and total +N/-M, empty state "No file changes"). A barrel export `src/components/diff/index.ts` re-exports all three. The dev route `/dev/diff-demo` (`src/pages/DiffDemo.tsx`) is registered outside `RequireAuth` (public) so QA/Reviewer can verify without login; it contains 8 hardcoded `FileDiff` samples covering every AC edge case plus a live commit-fetch form. Verified browser screenshot (light + dark) saved under `.jarwis/logs/PH-158/qa-screenshots/`.

## Design decisions (recent)

- Hand-written git types in dedicated module (not openapi-typescript regen) [PH-157] — preserves `api.ts` convention (string ISO datetime, explicit `null` literals, no `undefined`); openapi-typescript devDep present but no `generate:types` script; adding generator pipeline (script + CI + dual source-of-truth) is scope creep for G8. Mitigation: every interface has `@see schemas.py:<ClassName>` JSDoc; reviewer does line-by-line cross-check. openapi-typescript regen path documented as follow-up after G14.
- `api.git.*` nested namespace (not flat `api.*`) [PH-157] — 10 git methods would bloat the flat `api` object and reduce discoverability; nested namespace keeps git surface self-contained while maintaining the same `request<T>` + `ApiRequestError` contract. `api.setRepository`/`api.detachRepository` remain flat to mirror `api.updateBoard` (admin board-level symmetry).
- `api.git.refresh` uses direct `fetch`, not `request<T>` [PH-157] — the `POST /git/refresh` endpoint uses shared-secret auth (`X-Git-Refresh-Token` header), NOT Bearer token. Using `request<T>` would attach the Bearer header (incorrect); direct fetch avoids this while still wrapping error responses in `ApiRequestError` for consistency.
- `WebSocketMessage` exported from `useWebSocket.ts` [PH-157] — previously unexported (internal to hook); exported now so `isGitSyncedMessage()` type predicate can be used by G9+ consumers without duplicating the interface. Zero behavior change.
- Removed env-specific token fingerprint heuristic from WebSocket reconnect handler + 2 sibling sites [PH-148] — close codes are the source of truth, not token-prefix string match. `useWebSocket.ts:319-324` 1c7f53fb branch deleted; `utils/auth-fix.ts` deleted (dead code, zero importers); `pages/Login.tsx` DEV_TOKENS literal map replaced with `VITE_DEV_TOKEN_<role>` env-var reads from `.env.development.local` (gitignored, `.env.development.example` committed). Production bundle verified clean of fingerprint.
- Custom parser, no diff library [PH-158] — `diff2html` (~80 KB min+gz) and `react-diff-viewer` are over-engineering for inline unified diff display; backend produces clean `git diff -p --no-ext-diff` output with binary→null and byte-cap already handled; custom ~150 LOC parser covers 100 % of AC edge cases; parser module is isolated (`src/lib/diff/`) and independently testable; if word-level diff is ever needed, swap is low-cost.
- Container/presentational split for DiffViewer [PH-158] — `DiffViewer` (fetch + state) and `FileDiffView` (purely presentational, no queries) are separate files; G11/G12 consumers may use `FileDiffView` directly for already-fetched `FileDiff[]` while `DiffViewer` handles TanStack Query loading/error/cache lifecycle; testability: `FileDiffView` renders deterministically from props with no async code path.
- 2-mode API (`files` vs `fetch` prop) [PH-158] — TypeScript discriminated union `DiffViewerDataProps | DiffViewerFetchProps` enforces mutual exclusion at type-level; G11 (range diff, whole-page data) uses data mode; G12 (per-commit lazy fetch) uses fetch mode; callers cannot accidentally mix both.
- `collapseThreshold` prop (default 50) [PH-158] — large hunks (>50 lines) collapse by default, enabling lazy DOM rendering and avoiding visual overload for wide diffs; G11/G12 may override this if they want a higher threshold; threshold is a prop (not a constant) so no fork needed.
- `/dev/diff-demo` outside RequireAuth [PH-158] — moved to public route so QA/Reviewer can verify rendering without a live auth token; route only shows hardcoded samples (no sensitive data); live fetch form in section 3 still requires a valid board+sha; may be gated behind `import.meta.env.DEV` or removed in G14.
- Initial documentation [bootstrap] — auto-generated by jarwis-init bootstrap flow

## Known gotchas

- `parseUnifiedDiff` silently drops `\ No newline at end of file` from line counters [PH-158] — the `\ ` meta line is pushed as `type: 'meta'` but does NOT advance oldNo/newNo. If you compare rendered line numbers to a raw git diff, the meta row will cause the next line's number to appear one ahead. This is correct behaviour (the meta line is not a real source line), but it can confuse testers who count lines manually.
- Rename-only patches produce 0 hunks (empty array from parser) [PH-158] — a patch that is purely `diff --git a/old b/new\nrename from ...\nrename to ...` with no `@@` hunk header is valid git output and `parseUnifiedDiff` correctly returns `[]`. `FileDiffView` renders "No content changes" in this case. If you add hunk-count assertions in tests, distinguish rename-only from empty string.
- `DiffViewer fetch mode` does not invalidate cache on WS `git_synced` event [PH-158] — stale cache for the same sha will be served from TanStack Query until default staleTime (0, immediate refetch on next mount). G14 will add `queryClient.invalidateQueries(['git','diff'])` in the `isGitSyncedMessage` handler; do NOT add it in G9-scope PRs as it is intentionally scoped out.
- `api.git.refresh` must NOT use Bearer token [PH-157] — `POST /api/boards/{key}/git/refresh` authenticates via `X-Git-Refresh-Token` shared secret (see `board.roles["refresh_secret"]`). The `request<T>` helper unconditionally sets `Authorization: Bearer <token>`. `api.git.refresh` uses direct `fetch` to avoid this. If you refactor auth headers in `request<T>`, ensure `api.git.refresh` remains unaffected.
- `ahead`/`behind` null sentinel means deep divergence — not an error [PH-157] — `GitBranchEntry.ahead` and `GitBranchEntry.behind` are `number | null`. `null` means the BFS walk exceeded `git_backfill_limit` (2000 commits), not that the values are unknown. G9 UI must render "..." or equivalent for null (not "0" or empty). Logging null as an error is a false positive.
- Three-dot range diff semantics (base...head) — not two-dot [PH-157] — `api.git.getRangeDiff` wraps `GET /git/diff?base=&head=`, which uses merge-base (three-dot) semantics: `main...feature` shows only feature-branch-specific changes. If you need two-dot `base..head` (all commits between two refs regardless of branch point), use `api.git.listCommits` with cursor pagination instead.
- `api.git.refresh` 403 vs 401 distinction [PH-157] — `refresh_secret` not configured on the board → 403 (`refresh_disabled`); secret IS configured but token mismatches → 401 (`unauthorized`). The client wraps both in `ApiRequestError` with the respective status code. Callers should distinguish these to surface useful error messages to admins.
- `npm run lint` is broken on main pre-G8 [PH-157] — ESLint 9.39.4 requires `eslint.config.js` flat config file; the project has neither a flat config nor a `.eslintrc.*`. The `lint` script was already failing before this branch; G8 does NOT introduce this regression. Typecheck (`tsc --noEmit`) is the lint gate for G8.
- `src/components/BoardSettingsDialog.tsx:62,80,110` — three `TODO: Implement actual API call` markers for board member add / remove / role-change inside the legacy `BoardSettingsDialog`; live membership management has since moved to `MembersTab.tsx` + `MembershipRow.tsx` against the real `/api/boards/:id/members` REST endpoints, so this dialog appears to be an obsolete code path that still ships. Reconcile or delete before next refactor. [bootstrap]
- `src/api/client.ts:158-172` — four `@deprecated` workflow REST helpers (`addWorkflowState`, `deleteWorkflowState`, `updateWorkflowStates`, `updateWorkflowTransitions`) hit endpoints the backend never implemented (PH-21 era, 404). They are still exported; new callers must use the `mcpCall`-backed workflow helpers below them (`listWorkflows`, `addTransition`, `setFieldGates`, etc.). [bootstrap]
- `src/ws/` directory is empty — the layout documented in `frontend/README.md` ("`src/ws/` — WebSocket client + subscription manager") no longer matches reality. The actual implementation lives in `src/hooks/useWebSocket.ts`; the empty `ws/` folder + README line will mislead newcomers until cleaned up. [bootstrap]
- `src/stores/auth.ts:20-43` — `setToken` performs a localStorage write then immediately re-reads to assert sync, logging a warning on mismatch. This is dev-only diagnostic noise (gated on `import.meta.env.DEV`) but indicates a previously-observed token desync; if you touch the auth store, preserve the write-then-verify pattern. [bootstrap]

## Related

- [[overview]]
- [[index]]
- [[components/backend]]
