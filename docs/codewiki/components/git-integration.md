---
type: component
files:
  - backend/app/git/reader.py
  - backend/app/git/sync.py
  - backend/app/git/parser.py
  - backend/app/git/webhook.py
  - backend/app/api/repositories.py
  - backend/app/services/git_queries.py
status: active
last_touched_ticket: PH-153
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

**G4 — Read API** (`app/api/repositories.py`, `app/services/git_queries.py`): Four cache-backed GET endpoints under `/api/boards/{key}/git/`: `graph` (DAG payload with limit + branch-filter; `GitGraphResponse` = commits[] + branches[] + tags[]), `branches` (all branches with ahead/behind vs. default; `GitBranchesListResponse`), `commits` (cursor-paginated by `before=<sha>` → committed_at filter; `GitCommitsListResponse`), `commits/{sha}` (full commit detail + per-file numstat; `GitCommitDetail`). All auth via `_require_board_member`; no Repository row → 409 `repo_not_configured`; empty cache → 200 empty arrays; short sha (≥7 chars) accepted in `commits/{sha}`, collision → 404. Service layer in `git_queries.py` (cache-only, no reader.py on request path).

**G3 — Sync service** (`app/git/sync.py`): `sync_repo(session, board) → SyncResult` is the single entry-point for populating the git cache tables. Flow: (1) resolve Repository row — return `SyncResult(skipped=True)` immediately if none; (2) open repo via G2 reader; (3) upsert `git_branches` (delete stale branches); (4) walk commits via `awalk_commits(since_sha=last_synced_sha)` — delta if `last_synced_sha` is set, full backfill (up to `git_backfill_limit`) if not; (5) for each new commit: INSERT into `git_commits` ON CONFLICT DO NOTHING, then `git_commit_files` bulk insert, then per ticket-key: INSERT `git_commit_tickets` ON CONFLICT DO NOTHING — if freshly inserted, write `TicketHistory(event_type='git_commit_linked')` + publish per-ticket EventEnvelope; (6) update `repository.last_synced_sha/at`; (7) publish board-scoped `git_synced` EventEnvelope with `ticket_id='system'` sentinel.

**Parser + webhook** (`app/git/parser.py`, `app/git/webhook.py`): `parse_commit()` extracts `[A-Z]{2,5}-\d+` ticket keys and validates conventional-commit format. `webhook.py` handles GitHub push/delete/PR events, writing `TicketHistory` rows independently of sync. Both paths use `app/git/_linkage.py:find_ticket_by_key` and `get_system_actor_id` (shared helpers extracted in G3).

**Cache tables** (migration `20260604_0007`): `git_commits` (sha unique per repo), `git_branches` (name unique per repo, refreshed each sync), `git_commit_files` (immutable per commit), `git_commit_tickets` (junction, unique (commit_id, ticket_id) = dedupe gate).

## Design decisions (recent)

- G4 read API extends `repositories.py` router (single git router, no parallel `app/api/git_read.py`) [PH-153] — G1 docstring already promised G2-G6 would add routes here; keeps all `/git/*` reads in one file, minimises `main.py` churn
- ahead/behind computed via bounded BFS over cached `git_commits.parents` JSON column; returns `null` on overflow (BFS exceeds `git_backfill_limit=2000`) [PH-153] — exact calculation would need recursive CTE or in-memory graph; pragmatic compromise documented in `GitBranchEntry.ahead/behind` schema field comments
- repo-not-configured → 409 `RepoNotConfigured` (new `ProjectHubError` subclass) [PH-153] — empty arrays would be ambiguous (sync not run yet vs. no repo attached); 409 makes the prerequisite explicit for G8 frontend
- `app/git/_linkage.py` extracted from `webhook.py` to be shared by `sync.py` — avoids duplication of `find_ticket_by_key` + `get_system_actor_id`; underscore prefix = internal to `app/git/` [PH-152] — smallest safe refactor, backward-compat aliases in webhook.py kept for existing test imports
- sync.py uses `git_commit_tickets` unique constraint as dedupe gate — if webhook lands first, sync's INSERT returns no rows and history write is skipped; first-observation wins (timestamps anchored to first link) [PH-152] — see Risks: webhook-after-sync asymmetry is documented as follow-up
- board-scope `git_synced` envelope reuses `EventEnvelope` with `ticket_id="system"` sentinel — same pattern as `system_degradation` in `bus.py`; the `ticket:system` channel has no subscribers (harmless extra PUBLISH) [PH-152]
- `_insert_ignore` branches on dialect: PostgreSQL uses `INSERT … ON CONFLICT DO NOTHING RETURNING id` (reliable RETURNING to detect new vs. conflict); SQLite uses a pre-check SELECT then INSERT (no RETURNING support in older SA) [PH-152] — keeps tests runnable on in-memory SQLite without mocking
- `git_backfill_limit=2000` in `Settings` — protects against OOM on large repos; G6 manual refresh may surface the cap to callers; oldest commits below the cap are absent from cache [PH-152]
- GitPython chosen over dulwich for reader (rename detection, unified diff fidelity) [PH-151]
- Repository model uses string `provider` + CHECK constraint instead of DB Enum (migration flexibility) [PH-150]

## Known gotchas

- Cursor pagination in `GET /git/commits` uses `committed_at` not a sequence id — duplicate timestamps (rapid-fire commits in the same second) are stabilised by a `sha DESC` tie-break in `ORDER BY committed_at DESC, sha DESC`. Two commits with identical timestamp + sha prefix collision can still cause a commit to appear on both pages; acceptable edge case. [PH-153]
- `GET /git/commits/{sha}` accepts both 40-hex and short sha (≥7 chars matching `git_commits.sha LIKE sha%`); if ≥2 rows match the prefix the endpoint returns 404 (not 500). Artificially seeded test commits whose prefixes collide will trigger this path — keep test sha values distinct in their first 8 chars. [PH-153]
- Force-pushed default branch: `walk_commits(since_sha=X)` raises `GitCommandError` when X is unreachable; `sync.py` catches this and falls back to full backfill (idempotent via unique constraints). If even the full backfill fails, `SyncResult(skipped=False, new_commits=0)` is returned without raising. [PH-152]
- Webhook-after-sync double-write asymmetry: `sync.py` checks `git_commit_tickets` before writing history; `webhook.py` does NOT — so a webhook arriving after sync will write a second `git_commit_linked` history row for the same (commit, ticket) pair. Mitigation: webhook is authoritative for GitHub-origin pushes; sync is for local-mount commits. Permanent dedupe = route webhook through `git_commit_tickets` too — deferred to a follow-up ticket. [PH-152]
- `session.flush()` required before `publish_ticket_event` — `history.id` must be non-null (generated by flush, not commit). `sync.py` flushes per-commit; ensure the order is `add(history) → flush → publish`. [PH-152]
- `git_commit_files` has no unique constraint on `(commit_id, path)` because rename pairs can produce duplicate paths (old + new both map to the same `b_path`). Use `commit_id` index only; no deduplication at insert. [PH-152]

## Related

- [[components/backend]]
- [[index]]
- [[overview]]
