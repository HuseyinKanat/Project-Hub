# Codewiki Log

> Append-only chronology of wiki maintenance.
>
> **Format**: `## [YYYY-MM-DD] <op> | <title> | [TICKET-KEY]`
> **Ops**: `bootstrap` | `ingest` | `lint` | `query`
>
> **Tail** (last 10 entries):
> ```bash
> grep "^## \[" docs/codewiki/log.md | tail -10
> ```

## [INIT] bootstrap | codewiki scaffolded | [no-ticket]

Created by `jarwis-init.sh`.
Directories: `components/`, `concepts/`, `api/`, `decisions/`.
Subsequent entries will be added by sub-agents during the ingest flow.

## [2026-05-26] ingest | useWebSocket 1c7f53fb fingerprint removed (3 sites + 1 file deleted) | [PH-148]

Touched: components/frontend.md
Summary: Hard-coded `1c7f53fb` token-prefix heuristic eliminated from useWebSocket.ts:319-324, utils/auth-fix.ts (file deleted), and pages/Login.tsx. New `frontend/.env.development.example` documents the dev-mode token knobs. Close codes are now the sole signal for auth-related reconnect classification.

## [2026-05-26] bootstrap | initial codewiki filled (2 pages) | [no-ticket]

Touched: components/backend.md, components/frontend.md
Summary: Architect bootstrap pass — `backend/` and `frontend/` skeleton pages
replaced with real Current behavior / Design decisions / Known gotchas content.
Backend page: 30 source files referenced, 0 gotchas surfaced (no TODO/FIXME/HACK
markers in `backend/app/`). Frontend page: 29 source files referenced, 5
gotchas surfaced (legacy BoardSettingsDialog TODOs, hard-coded jarwis-backend
token heuristic in `useWebSocket.ts`, 4 deprecated workflow REST helpers in
`api/client.ts`, empty `src/ws/` directory desync with README, `auth.ts`
write-then-verify localStorage warning).
