---
type: component
files:
  - backend/app/git/reader.py
  - backend/app/git/sync.py
  - backend/app/git/parser.py
  - backend/app/git/webhook.py
  - backend/app/api/repositories.py
status: active
last_touched_ticket: PH-152
related:
  - "[[components/backend]]"
  - "[[index]]"
  - "[[overview]]"
---

# Git Integration

> Local-first git readback layer (G1–G3): repository config, hardened reader, commit cache + WebSocket fan-out. G4-G6 (read API, diff, refresh) build on this foundation.

## Current behavior

The git integration is split across four layers:

**G1 — Repository config** (`app/api/repositories.py`, `app/services/repositories.py`): REST endpoints (`PUT/DELETE /api/boards/{key}/git/repository`, `GET /api/boards/{key}/git/status`) and ORM (`Repository` model, 1 board : 0..1 repo, FK `boards.id` ondelete=CASCADE). Validates that `local_path` starts with `/repos/` (allowlist enforced at the reader level). Provider values restricted to `github|gitlab|local` via CHECK constraint.

**G2 — Hardened reader** (`app/git/reader.py`): Read-only GitPython wrapper with five defense-in-depth layers (path allowlist via `realpath`, env hardening with `GIT_CONFIG_NOSYSTEM=1`+`GIT_CONFIG_GLOBAL=/dev/null`, per-call `-c` overrides disabling `fsmonitor`/`diff.external`/`core.pager`/`protocol.file.allow`, `search_parent_directories=False`, no write calls). Public async wrappers (`aopen_repo`, `alist_branches`, `awalk_commits`, `acommit_files`) use `asyncio.to_thread` to avoid blocking the event loop. Returns typed dataclasses (`BranchInfo`, `CommitInfo`, `CommitFileChange`).

**G3 — Sync service** (`app/git/sync.py`): `sync_repo(session, board) → SyncResult` is the single entry-point for populating the git cache tables. Flow: (1) resolve Repository row — return `SyncResult(skipped=True)` immediately if none; (2) open repo via G2 reader; (3) upsert `git_branches` (delete stale branches); (4) walk commits via `awalk_commits(since_sha=last_synced_sha)` — delta if `last_synced_sha` is set, full backfill (up to `git_backfill_limit`) if not; (5) for each new commit: INSERT into `git_commits` ON CONFLICT DO NOTHING, then `git_commit_files` bulk insert, then per ticket-key: INSERT `git_commit_tickets` ON CONFLICT DO NOTHING — if freshly inserted, write `TicketHistory(event_type='git_commit_linked')` + publish per-ticket EventEnvelope; (6) update `repository.last_synced_sha/at`; (7) publish board-scoped `git_synced` EventEnvelope with `ticket_id='system'` sentinel.

**Parser + webhook** (`app/git/parser.py`, `app/git/webhook.py`): `parse_commit()` extracts `[A-Z]{2,5}-\d+` ticket keys and validates conventional-commit format. `webhook.py` handles GitHub push/delete/PR events, writing `TicketHistory` rows independently of sync. Both paths use `app/git/_linkage.py:find_ticket_by_key` and `get_system_actor_id` (shared helpers extracted in G3).

**Cache tables** (migration `20260604_0007`): `git_commits` (sha unique per repo), `git_branches` (name unique per repo, refreshed each sync), `git_commit_files` (immutable per commit), `git_commit_tickets` (junction, unique (commit_id, ticket_id) = dedupe gate).

## Design decisions (recent)

- `app/git/_linkage.py` extracted from `webhook.py` to be shared by `sync.py` — avoids duplication of `find_ticket_by_key` + `get_system_actor_id`; underscore prefix = internal to `app/git/` [PH-152] — smallest safe refactor, backward-compat aliases in webhook.py kept for existing test imports
- sync.py uses `git_commit_tickets` unique constraint as dedupe gate — if webhook lands first, sync's INSERT returns no rows and history write is skipped; first-observation wins (timestamps anchored to first link) [PH-152] — see Risks: webhook-after-sync asymmetry is documented as follow-up
- board-scope `git_synced` envelope reuses `EventEnvelope` with `ticket_id="system"` sentinel — same pattern as `system_degradation` in `bus.py`; the `ticket:system` channel has no subscribers (harmless extra PUBLISH) [PH-152]
- `_insert_ignore` branches on dialect: PostgreSQL uses `INSERT … ON CONFLICT DO NOTHING RETURNING id` (reliable RETURNING to detect new vs. conflict); SQLite uses a pre-check SELECT then INSERT (no RETURNING support in older SA) [PH-152] — keeps tests runnable on in-memory SQLite without mocking
- `git_backfill_limit=2000` in `Settings` — protects against OOM on large repos; G6 manual refresh may surface the cap to callers; oldest commits below the cap are absent from cache [PH-152]
- GitPython chosen over dulwich for reader (rename detection, unified diff fidelity) [PH-151]
- Repository model uses string `provider` + CHECK constraint instead of DB Enum (migration flexibility) [PH-150]

## Known gotchas

- Force-pushed default branch: `walk_commits(since_sha=X)` raises `GitCommandError` when X is unreachable; `sync.py` catches this and falls back to full backfill (idempotent via unique constraints). If even the full backfill fails, `SyncResult(skipped=False, new_commits=0)` is returned without raising. [PH-152]
- Webhook-after-sync double-write asymmetry: `sync.py` checks `git_commit_tickets` before writing history; `webhook.py` does NOT — so a webhook arriving after sync will write a second `git_commit_linked` history row for the same (commit, ticket) pair. Mitigation: webhook is authoritative for GitHub-origin pushes; sync is for local-mount commits. Permanent dedupe = route webhook through `git_commit_tickets` too — deferred to a follow-up ticket. [PH-152]
- `session.flush()` required before `publish_ticket_event` — `history.id` must be non-null (generated by flush, not commit). `sync.py` flushes per-commit; ensure the order is `add(history) → flush → publish`. [PH-152]
- `git_commit_files` has no unique constraint on `(commit_id, path)` because rename pairs can produce duplicate paths (old + new both map to the same `b_path`). Use `commit_id` index only; no deduplication at insert. [PH-152]

## Related

- [[components/backend]]
- [[index]]
- [[overview]]
