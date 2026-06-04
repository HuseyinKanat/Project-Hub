---
type: component
files:
  - backend/app/git/reader.py
  - backend/app/git/sync.py
  - backend/app/git/parser.py
  - backend/app/git/webhook.py
  - backend/app/git/refresh.py
  - backend/app/api/repositories.py
  - backend/app/api/tickets.py
  - backend/app/services/git_queries.py
status: active
last_touched_ticket: PH-155
related:
  - "[[components/backend]]"
  - "[[index]]"
  - "[[overview]]"
---

# Git Integration

> Local-first git readback layer (G1–G6): repository config, hardened reader, commit cache + WebSocket fan-out, read API, diff API, live refresh endpoint + background poller.

## Current behavior

The git integration is split across five layers:

**G1 — Repository config** (`app/api/repositories.py`, `app/services/repositories.py`): REST endpoints (`PUT/DELETE /api/boards/{key}/git/repository`, `GET /api/boards/{key}/git/status`) and ORM (`Repository` model, 1 board : 0..1 repo, FK `boards.id` ondelete=CASCADE). Validates that `local_path` starts with `/repos/` (allowlist enforced at the reader level). Provider values restricted to `github|gitlab|local` via CHECK constraint.

**G2 — Hardened reader** (`app/git/reader.py`): Read-only GitPython wrapper with five defense-in-depth layers (path allowlist via `realpath`, env hardening with `GIT_CONFIG_NOSYSTEM=1`+`GIT_CONFIG_GLOBAL=/dev/null`, per-call `-c` overrides disabling `core.fsmonitor`/`core.pager`/`protocol.file.allow`, `search_parent_directories=False`, no write calls). Public async wrappers (`aopen_repo`, `alist_branches`, `awalk_commits`, `acommit_files`) use `asyncio.to_thread` to avoid blocking the event loop. Returns typed dataclasses (`BranchInfo`, `CommitInfo`, `CommitFileChange`). **G5 adds**: `diff_text`/`range_diff` (sync) + `adiff_text`/`arange_diff` (async) for on-demand unified diff generation; new typed return shapes `FileDiff` (per-file patch entry) and `DiffResult` (aggregate with top-level `truncated` flag).

**G4 — Read API** (`app/api/repositories.py`, `app/services/git_queries.py`): Four cache-backed GET endpoints under `/api/boards/{key}/git/`: `graph` (DAG payload with limit + branch-filter; `GitGraphResponse` = commits[] + branches[] + tags[]), `branches` (all branches with ahead/behind vs. default; `GitBranchesListResponse`), `commits` (cursor-paginated by `before=<sha>` → committed_at filter; `GitCommitsListResponse`), `commits/{sha}` (full commit detail + per-file numstat; `GitCommitDetail`). All auth via `_require_board_member`; no Repository row → 409 `repo_not_configured`; empty cache → 200 empty arrays; short sha (≥7 chars) accepted in `commits/{sha}`, collision → 404. Service layer in `git_queries.py` (cache-only, no reader.py on request path).

**G5 — Diff API** (`app/api/repositories.py`, `app/api/tickets.py`, `app/services/git_queries.py`, `app/git/reader.py`): Three new GET endpoints for on-demand patch text: (1) `GET /api/boards/{key}/git/commits/{sha}/diff?path&context` — single-commit diff against first parent (or empty tree for initial commit); live reader call; `DiffResponse{sha, files[], truncated}`. (2) `GET /api/boards/{key}/git/diff?base&head&path&context` — three-dot merge-base range diff (`base...head`); `RangeDiffResponse{base, head, files[], truncated}`. (3) `GET /api/tickets/{key}/commits` — cache-only list of commits linked to a ticket via `git_commit_tickets`; `TicketCommitsResponse{branch_name, commits[]}` with per-commit aggregated `additions`/`deletions`/`files_changed` from `git_commit_files`; no diff text inlined. Reader uses two-pass strategy: numstat first (binary detection, file list), then per-file patch with byte-cap accumulator (`git_diff_max_bytes=1 MiB` from `Settings`). Binary files yield `patch=null`+`is_binary=true`; files beyond the cap yield `patch=null`+`truncated=true` per file; top-level `truncated=true` signals any cap hit.

**G3 — Sync service** (`app/git/sync.py`): `sync_repo(session, board) → SyncResult` is the single entry-point for populating the git cache tables. Flow: (1) resolve Repository row — return `SyncResult(skipped=True)` immediately if none; (2) open repo via G2 reader; (3) upsert `git_branches` (delete stale branches); (4) walk commits via `awalk_commits(since_sha=last_synced_sha)` — delta if `last_synced_sha` is set, full backfill (up to `git_backfill_limit`) if not; (5) for each new commit: INSERT into `git_commits` ON CONFLICT DO NOTHING, then `git_commit_files` bulk insert, then per ticket-key: INSERT `git_commit_tickets` ON CONFLICT DO NOTHING — if freshly inserted, write `TicketHistory(event_type='git_commit_linked')` + publish per-ticket EventEnvelope; (6) update `repository.last_synced_sha/at`; (7) publish board-scoped `git_synced` EventEnvelope with `ticket_id='system'` sentinel.

**G6 — Live refresh + background poller** (`app/git/refresh.py`, `app/api/repositories.py`): `POST /api/boards/{key}/git/refresh` — shared-secret auth via `board.roles["refresh_secret"]` (`hmac.compare_digest`; 403 if unset, 401 if mismatch, 503 if `git_refresh_enabled=False`). Debounce via `RefreshRegistry.should_coalesce()` (monotonic clock, 2s default); coalesced calls return `202 {status:"coalesced"}` immediately without dispatching. Non-coalesced: `registry.mark_dispatched()` then `run_in_background(_locked_sync_repo(board_id))` returns `202 {status:"queued"}` within milliseconds. `RefreshRegistry` (module-level singleton in `refresh.py`) maintains per-repo `asyncio.Lock` and monotonic last-dispatch timestamps. `_locked_sync_repo` opens a fresh `SessionLocal()`, fetches board + repo, acquires the per-repo lock via `registry.acquire_sync_lock()`, calls `sync_repo()`. Background poller `git_poll_cron()` (lifespan task, mirrors `stale_claim_cron`) runs every `git_poll_interval_seconds` (default 30s): scans `Repository` rows with `last_synced_at < now - interval OR NULL`, skips repos whose lock is already held (`lock.locked()` pre-check — optimisation; lock is the correctness boundary), acquires lock and calls `sync_repo()` for due repos. Three new `Settings` fields: `git_poll_interval_seconds=30`, `git_refresh_debounce_seconds=2.0`, `git_refresh_enabled=True`. Poller is disabled if either flag is False/0. `mask_webhook_secret` in `boards.py` extended to also mask `refresh_secret`.

**Parser + webhook** (`app/git/parser.py`, `app/git/webhook.py`): `parse_commit()` extracts `[A-Z]{2,5}-\d+` ticket keys and validates conventional-commit format. `webhook.py` handles GitHub push/delete/PR events, writing `TicketHistory` rows independently of sync. Both paths use `app/git/_linkage.py:find_ticket_by_key` and `get_system_actor_id` (shared helpers extracted in G3).

**Cache tables** (migration `20260604_0007`): `git_commits` (sha unique per repo), `git_branches` (name unique per repo, refreshed each sync), `git_commit_files` (immutable per commit), `git_commit_tickets` (junction, unique (commit_id, ticket_id) = dedupe gate).

## Design decisions (recent)

- G6 in-process `RefreshRegistry` singleton with `asyncio.Lock` per repo [PH-155] — endpoint and poller share the same lock, preventing concurrent `sync_repo()` calls on the same repo; single-process scope only (multi-worker: Redis SETNX drop-in, `settings.redis_url` already wired)
- G6 shared-secret-only auth (no board membership / Bearer token) [PH-155] — post-commit hook is fire-and-forget from a shell script with no session; secret-or-nothing design fails closed (403 if unset) and uses constant-time `hmac.compare_digest` to prevent timing oracle
- G6 debounce uses monotonic clock, not wallclock [PH-155] — `time.monotonic()` is immune to NTP jumps or clock adjustments; volatile state (resets on restart) is intentional — post-restart, first request always triggers a sync immediately
- G6 poller skips locked repos via `lock.locked()` pre-check [PH-155] — avoids queuing behind an in-flight refresh (the refresh already covers the sync); note: pre-check is racy but harmless (both paths call idempotent `sync_repo`; lock is the correctness boundary, not the pre-check)
- G5 live reader on hot path with byte cap [PH-154] — patches computed on demand via `git diff` subprocess (two-pass: numstat then per-file patch); `git_diff_max_bytes=1 MiB` prevents DoS from huge commits; no patch caching in G5 (in-memory LRU deferred to G6+ if p95 latency warrants)
- G5 two-pass numstat-then-patch strategy [PH-154] — numstat is O(files) without loading patch bodies, detects binaries cheaply, allows early-exit before expensive per-file subprocess fan-out; single-pass `git diff -p` would require parsing a giant blob in Python
- G5 ticket commits endpoint is cache-only, no inline diff [PH-154] — UI calls `GET /git/commits/{sha}/diff` on demand per sha; inlining diffs in the list response would multiply payload size; service layer joins `git_commit_tickets → git_commits LEFT JOIN git_commit_files` aggregate via SQL (no N+1)
- G5 three-dot range semantics (`base...head`) [PH-154] — matches GitHub/GitLab PR-diff conventions; merge-base anchored so `main...feature` shows only feature-branch-specific changes; two-dot `base..head` available via existing commits endpoint
- `--no-ext-diff` on every patch-generating diff call [PH-154] — blocks `diff.external` RCE from local `.git/config` (reviewer-confirmed: injecting `diff.external=/path/script` in `.git/config` executed the script without this flag); `diff.external` is absent from `_SAFE_CONFIG_FLAGS` because `-c diff.external=` (empty string) causes git to exec `""` resulting in "cannot run : No such file or directory"; `--no-ext-diff` is the correct per-call mechanism — it disables external diff drivers without any side-effect on patch output; regression test `test_diff_text_ext_diff_not_triggered` uses a script-file probe (not inline sh -c) to verify this invariant
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

- G6 registry is volatile (resets on restart) [PH-155] — `_last_request_monotonic` is in-process memory; on restart all debounce state is lost. First refresh request post-restart triggers a sync regardless of recent activity. This is deliberate (no stale coalesce decision survives a process boundary; durable record is `repository.last_synced_at`).
- G6 single-process scope [PH-155] — `asyncio.Lock` does not span uvicorn workers (`--workers >1`). Current deployment is single worker; if scaled, replace `RefreshRegistry` with Redis SETNX (settings.redis_url is already wired). The `RefreshRegistry` public API (get_lock / should_coalesce / acquire_sync_lock) is the abstraction boundary for that swap.
- G6 debounce coalesces multiple pushes into one `git_synced` WS envelope [PH-155] — a 2s burst of 10 commits gets one envelope listing all commits (not 10 envelopes). Frontend must handle multi-commit payload; G8 already expects `new_commit_shas: list[str]`.
- Cache-vs-FS truth divergence after force-push [PH-154] — diff endpoint hits live FS; if a commit was force-pushed away after sync, `GitPython.commit(sha)` raises `BadName` (404) even if `git_commits` row still exists. This is correct behaviour (truth = FS), but can surprise callers who see a cached commit summary but 404 on its diff. Mitigation: G6 refresh will detect force-push and remove stale cache rows.
- Per-file subprocess fan-out for large refactor commits [PH-154] — `diff_text` issues one `git diff` subprocess per file (after the numstat pass). For a 500-file refactor this is 501 forks. Mitigation: numstat pass gives early visibility into N; future optimisation could fall back to single `git diff -p` + Python-side split for very large N. Deferred — measure p99 in production first.
- UTF-8 replace policy for mixed-encoding files [PH-154] — patch bytes decoded with `errors='replace'`, so a binary-looking but non-detected-binary file (e.g. Latin-1 encoding without NUL bytes) will show `�` replacement characters in the patch field. Frontend should handle gracefully.
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
