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

## [2026-06-04] ingest | G1 repository model + config API | [PH-150]

Touched: components/backend.md
Summary: Added Repository ORM model (1 board:0..1 repo, FK CASCADE), repositories
service (upsert/detach/get), REST endpoints (PUT/DELETE /repository, GET /git/status),
and Alembic migration 20260604_0006. Design decisions bullet: GitPython chosen for G2+
reader, string+CHECK over DB Enum for provider, selectinload eager-loading pattern.
Files added: api/repositories.py, services/repositories.py, migration 0006,
tests/test_repositories_api.py, tests/test_repository_model.py.

## [2026-06-04] ingest | G2 docker mount + hardened git reader | [PH-151]

No wiki update: .codemap empty, git reader page deferred to G3 per architect decision.
New files: app/git/reader.py, tests/test_git_reader.py. Infrastructure: Dockerfile git layer,
docker-compose.yml mount, pyproject.toml GitPython>=3.1.43, config.py repos_root field.

## [2026-06-04] ingest | PH-152 G3 git cache + sync + WS | git-integration page created, .codemap seeded | [PH-152]

Touched: components/git-integration.md (new), components/backend.md (frontmatter + design decisions), .codemap (5 entries added), index.md (Components list + Stats).
Summary: New `components/git-integration.md` page covering G1–G3 git integration subsystem (reader, sync, webhook, parser, repositories API). `.codemap` seeded with 5 entries for the git hot-set. `components/backend.md` updated with `sync.py`, `_linkage.py`, migration 0007 in files list and 4 new design decision bullets referencing PH-152.

## [2026-06-04] ingest | G4 git read API (graph/branches/commits/commit-detail) | [PH-153]

Touched: components/git-integration.md (frontmatter last_touched_ticket→PH-153, files list +git_queries.py, G4 section added to Current behavior, 3 design decision bullets, 2 gotcha bullets), .codemap (git_queries.py mapping added), log.md.
Summary: New `app/services/git_queries.py` service (cache-only reads, BFS ahead/behind, cursor pagination, short-sha resolve). Four endpoints added to `app/api/repositories.py` (graph, branches, commits, commits/{sha}). New `RepoNotConfigured` 409 exception in `core/exceptions.py`. Seven Pydantic schemas added to `schemas.py` (GitCommitSummary, GitCommitDetail, GitCommitFileEntry, GitBranchEntry, GitGraphResponse, GitCommitsListResponse, GitBranchesListResponse).

## [2026-06-04] ingest | G5 diff API (commit diff + range diff + ticket commits) | [PH-154]

Touched: components/git-integration.md (frontmatter last_touched_ticket→PH-154, files list +tickets.py, G5 section added to Current behavior, 5 design decision bullets, 3 gotcha bullets), .codemap (tickets.py mapping added), log.md.
Summary: New reader functions `diff_text`/`range_diff`/`adiff_text`/`arange_diff` with `FileDiff`/`DiffResult` dataclasses. Two new routes in `app/api/repositories.py` (`GET /git/commits/{sha}/diff`, `GET /git/diff`). One new route in `app/api/tickets.py` (`GET /tickets/{key}/commits`). New `ticket_commits_payload` service in `git_queries.py`. Five new Pydantic schemas in `schemas.py` (`FileDiff`, `DiffResponse`, `RangeDiffResponse`, `TicketCommitEntry`, `TicketCommitsResponse`). `git_diff_max_bytes=1MiB` setting added. Key design decisions: two-pass numstat-then-patch, byte-cap truncation, `diff.external` removed from `_SAFE_CONFIG_FLAGS` (empty-string exec bug).

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

## [2026-06-04] ingest | G5 diff.external RCE fix — --no-ext-diff on patch-generating diff calls | [PH-154]

Security revision: updated `components/git-integration.md` design decision for
`diff.external` handling. Previous entry (G5 initial) recorded removing
`diff.external` from `_SAFE_CONFIG_FLAGS` as accepted risk; reviewer probe
confirmed local `.git/config` `diff.external` still executed. Fix: `--no-ext-diff`
flag added to both `git diff --numstat` and per-file `git diff --unified=N` calls
in `_build_diff_files` (`app/git/reader.py`). Regression test
`test_diff_text_ext_diff_not_triggered` added with script-file probe.

## [2026-06-04] ingest | G6 live refresh endpoint + background poller | [PH-155]

Touched: components/git-integration.md (frontmatter last_touched_ticket→PH-155,
files list +refresh.py, G6 section added to Current behavior, 4 design decision
bullets, 3 gotcha bullets), .codemap (refresh.py mapping added), log.md.
Summary: New `app/git/refresh.py` module — `RefreshRegistry` in-process singleton
(per-repo asyncio.Lock + monotonic debounce), `git_poll_cron()` lifespan background
task (mirrors stale_claim_cron), `_locked_sync_repo()` shared worker. New
`POST /api/boards/{key}/git/refresh` endpoint in `app/api/repositories.py` —
shared-secret auth (board.roles["refresh_secret"] + hmac.compare_digest), debounce,
fire-and-forget via run_in_background. Three new `Settings` fields
(git_poll_interval_seconds=30, git_refresh_debounce_seconds=2.0,
git_refresh_enabled=True). `GitRefreshResponse` schema added. `mask_webhook_secret`
extended to also mask refresh_secret. 15 new tests in test_git_refresh.py (endpoint
auth matrix, debounce coalesce, registry unit tests, poller skip-locked).

## [2026-06-04] ingest | G8 frontend git client + types | [PH-157]

Touched: components/frontend.md (frontmatter files+last_touched_ticket, G8 git client surface paragraph in Current behavior, 4 new Design decisions bullets, 5 new Known gotchas), components/git-integration.md (last_touched_ticket→PH-157, G8 design decision bullet), .codemap (3 new entries: types/git.ts, api/client.ts, hooks/useWebSocket.ts → components/frontend.md), log.md.
Summary: New `frontend/src/types/git.ts` — 20+ exported interfaces/types covering all G1-G7 git backend schemas (GitCommitSummary, GitCommitDetail, GitCommitFile/Entry, GitBranchEntry/Branch, GitGraphResponse/GitGraph, GitCommitsListResponse, GitBranchesListResponse, FileDiff, DiffResponse/CommitDiff, RangeDiffResponse/RangeDiff, GitStatus/GitStatusResponse, GitRefreshStatus, GitRefreshResponse, RepositorySummary, RepositoryResponse, RepositoryUpsertPayload, TicketCommitEntry, TicketCommitsResponse, GitSyncedPayload, GitProvider, GitChangeType). `api/client.ts` gains `api.git.*` namespace (9 methods: getGraph, getBranches, listCommits, getCommit, getCommitDiff, getRangeDiff, getStatus, refresh, getTicketCommits) + `api.setRepository`/`api.detachRepository` top-level admin methods. `useWebSocket.ts` gains exported `WebSocketMessage` interface + `isGitSyncedMessage()` type-guard. tsc --noEmit clean (0 errors). ESLint broken pre-G8 (missing flat config — pre-existing issue on main).

## [2026-06-04] ingest | G9 DiffViewer reusable component | [PH-158]

Touched: components/frontend.md (frontmatter files + last_touched_ticket→PH-158, G9 DiffViewer paragraph added to Current behavior, 5 new Design decisions bullets, 3 new Known gotchas), .codemap (3 new entries: src/lib/diff/parseDiff.ts, src/components/diff/*.tsx, src/pages/DiffDemo.tsx → components/frontend.md), log.md.
Summary: New `src/lib/diff/parseDiff.ts` — custom unified diff parser (~150 LOC, no external library). `src/components/diff/` module: HunkView.tsx (3-col table, green/red row backgrounds, collapse for >50 lines), FileDiffView.tsx (binary/truncated markers, change_type badge, per-file expand/collapse), DiffViewer.tsx (2-mode: data-prop + TanStack Query fetch). Demo route `/dev/diff-demo` (public, 8 edge-case samples, live fetch form). Browser-verified with Playwright: 14/14 assertions pass, light+dark screenshots saved to .jarwis/logs/PH-158/qa-screenshots/. tsc clean.

## [2026-06-05] ingest | G10 BranchGraph tab — @xyflow/react commit graph + live WS sync | [PH-159]

Touched: components/frontend.md (frontmatter files + last_touched_ticket→PH-159, G10 BranchGraph paragraph added to Current behavior, 1 new Design decisions bullet, 1 new Known gotcha), .codemap (2 new entries: src/components/git/*.tsx + src/components/git/*.ts → components/frontend.md), log.md.
Summary: New `src/components/git/` module: BranchGraph.tsx (ReactFlow container, TanStack Query getGraph+getStatus, empty/noRepo/error/ready states), branchGraphLayout.ts (pure 2-pass lane algorithm ~150 LOC, O(N+E), useMemo-able), CommitNode.tsx (custom xyflow node: dot+short_sha+summary+ref chips+ticket_key links), BranchLegend.tsx (left rail, branch selection), index.ts (barrel). BoardDetail.tsx modified: tab strip (Kanban|Branch Graph, location.hash shareable), git_synced WS handler (queryClient.invalidateQueries + 3s highlight). Playwright-verified: 200 nodes rendered (light+dark), branch legend aria-pressed, commit click Selected indicator, hash URL. tsc clean. Screenshots: .jarwis/logs/PH-159/qa-screenshots/.

## [2026-06-05] ingest | G11 Branch detail panel — BranchPanel + state lift + 3-col layout | [PH-160]

Touched: components/frontend.md (frontmatter files+last_touched_ticket→PH-160, G11 BranchPanel paragraph added to Current behavior, 2 new Design decisions bullets [PH-160], 1 new Known gotcha [PH-160]), log.md.
Summary: New `frontend/src/components/git/BranchPanel.tsx` (~270 LOC) — right-column branch detail panel: header (branch name+HEAD badge+ticket_key link+close X), ahead/behind chip (null→...), commit list via `api.git.listCommits(boardKey,{branch,limit:30})` TanStack Query, DiffViewer range-diff toggle (disabled for default branch). `BoardDetail.tsx` gains `selectedBranch` state lift + tab-change useEffect + `flex flex-col lg:flex-row gap-3` wrapper around BranchGraph+BranchPanel. `index.ts` barrel updated with BranchPanel export. BranchGraph.tsx unchanged (callback reused). tsc clean. Preview-verified: layout, dark+light, empty state, flex-col mobile, ARIA structure. API-verified: getBranches+listCommits return correct data. Screenshots: .jarwis/logs/PH-160/.

## [2026-06-05] ingest | G13 backend: bearer alt-auth on /git/refresh + rotate-refresh-secret endpoint | [PH-162]

Touched: components/git-integration.md (frontmatter last_touched_ticket→PH-162, G6 section updated with hybrid auth + rotate endpoint, 3 Design decisions bullets [PH-162], 2 Known gotchas [PH-162]), .codemap (2 new entries: services/repositories.py → git-integration.md; frontend/src/components/repository/*.tsx → frontend.md), log.md.
Summary: Modified `app/api/repositories.py`: extended `api_git_refresh` with optional `Authorization: Bearer` alt-auth branch (admin membership check BEFORE shared-secret path — R1 safety ordering); added `_resolve_actor_from_bearer` helper; added `POST /repository/rotate-refresh-secret` endpoint (admin-only). New `RotateRefreshSecretResponse` Pydantic schema in `schemas.py`. New `rotate_refresh_secret` service helper in `services/repositories.py` (`secrets.token_hex(24)` + `flag_modified`). New test file `tests/test_repository_settings.py` — 6 tests covering AC-B1..B6 (bearer admin 202, non-admin bearer 403, shared-secret regression, rotate admin plaintext+masked, rotate non-admin 403, rotate then old-secret-401/new-secret-202). All 138 git/repository/refresh regression tests green. ruff + mypy clean on changed files.

## [2026-06-05] ingest | G13 frontend: BoardSettings Repository tab + 5 new components | [PH-162]

Touched: components/frontend.md (frontmatter files+last_touched_ticket→PH-162, G13 BoardSettings Repository paragraph added to Current behavior, 3 Design decisions bullets [PH-162], 2 Known gotchas [PH-162]), log.md.
Summary: New `frontend/src/components/repository/` module — 5 new components: RepositoryStatusPanel.tsx (dl key-value grid, humanised relative time, connected/disconnected states), RepositoryConfigForm.tsx (provider select + remote_url + default_branch + local_path + 422 inline errors), RepositoryOperationsPanel.tsx (Şimdi Yenile Bearer refresh + hook snippet + rotate/detach buttons), RotateSecretModal.tsx (confirm → API → one-shot plaintext display, cleared on close), DetachConfirmModal.tsx (board-key confirmation input). `BoardSettings.tsx` extended: TabValue adds "repository", GitBranch icon tab, gitStatusQuery (`["git", boardKey, "status"]`, enabled on activeTab), 3-panel repository tabpanel, read-only banner for non-admin. `api/client.ts`: `api.git.refresh` signature changed from `(boardKey, refreshToken)` to `(boardKey, opts?)` (hybrid Bearer/shared-secret), `api.rotateRefreshSecret` added. `types/git.ts`: `RotateRefreshSecretResponse` added. Smoke test `__smoke__/git.types.ts` updated for new signature. tsc clean (0 errors). Browser-verified: Repository tab AC-F1..F10 confirmed via Preview MCP snapshot + network log. Screenshots: .jarwis/logs/PH-162/qa-screenshots/.

## [2026-06-05] ingest | G12 ticket commits + branch range diff + G9 truncated fix | [PH-161]

Touched: components/frontend.md (frontmatter files+last_touched_ticket→PH-161, G12 TicketCommits paragraph added to Current behavior, 4 new Design decisions bullets [PH-161], 1 new Known gotcha [PH-161]), log.md.
Summary: New `frontend/src/components/git/TicketCommits.tsx` (~370 LOC) — expandable commit rows with numstat file list + per-file DiffViewer (kind:commit + path). `ActivitySection` gains `boardKey` prop, renders TicketCommits above GitEventBadge history feed. `useWebSocket.onMessage` invalidates `['ticket-commits', ticketKey]` query. Branch chip in TicketDetail sidebar Row "Branch" upgraded from span to button → range diff modal (`main...branch_name` format). `DiffViewer.tsx` FetchCommit gains optional `path?` field. `FileDiffView.tsx` G9 truncated fix (condition simplified to `file.truncated`). `BoardResponse` in `types/api.ts` gains `repository: RepositorySummary | null`. Browser-verified (Preview tool + DOM eval): all 8 ACs pass, 0 console errors. tsc clean. Screenshots: .jarwis/logs/PH-161/qa-screenshots/.

## [2026-06-05] lint | health check | [PH-163]

Manual `/codewiki lint` sweep triggered during G14 ingest pass.

- **Orphan pages**: 0 — all 3 component pages (`backend`, `frontend`, `git-integration`) listed in `index.md`. `overview.md` and `page-template.md` present. Dirs `concepts/`, `api/`, `decisions/` empty (no orphan risk).
- **Stale ticket refs**: spot-check PH-148, PH-150–PH-162 (all confirmed in project-hub query). 0 broken refs found in spot sample.
- **Code-wiki desync**: PH-163 touched files `BranchLegend.tsx`, `BranchPanel.tsx`, `BoardDetail.tsx`, `types/git.ts`, `DiffDemo.tsx` — all glob-matched by `.codemap` entries (`frontend/src/components/git/*.tsx`, `frontend/src/types/git.ts`, `frontend/src/pages/DiffDemo.tsx` → `components/frontend.md`). `vite.config.ts` matched by `frontend/src` glob in `.codemap`. Page updated in same branch — sync gate GREEN.
- **Broken wikilinks**: `[[components/backend]]`, `[[overview]]`, `[[index]]` in `frontend.md` all resolve. `[[components/frontend]]`, `[[index]]`, `[[overview]]` in `backend.md` and `git-integration.md` resolve. 0 broken wikilinks.
- **Contradicting claims**: `git-integration.md` G6 section notes `git_refresh_enabled=False → disabled` but does not specify HTTP status code for disabled path. Test `test_refresh_disabled_globally` uses `assert resp.status_code in (200,202)`. Contradiction deferred to PH-164 (202→503 backend contract change is out of scope for G14). **Known open item, not a lint error.**

Findings: 0 orphan, 0 broken ref, 0 broken wikilink, 0 desync (post-ingest), 1 deferred contradiction (PH-164).

## [2026-06-05] ingest | G14 polish + docs + codewiki sweep | [PH-163]

Touched: `components/frontend.md` (frontmatter `last_touched_ticket`→PH-163, 4 new Design decisions bullets [PH-163], 1 new Known gotcha [PH-163]).

Summary: G14 a11y + type fixes across 5 frontend files. `BoardDetail.tsx`: added `id="tab-kanban"` and `id="tab-graph"` to tab buttons (aria-labelledby now resolves). `BranchPanel.tsx`: `aria-controls` removed from collapsed diff button (target unmounted when closed); `aria-expanded=false` kept. `BranchLegend.tsx`: lane color offset bug fixed — use `idx` directly instead of `findIndex+1`. `types/git.ts`: `tags: never[]` → `tags: unknown[]` + JSDoc TODO. `DiffDemo.tsx`: stale comment corrected (route is public, not behind RequireAuth). `docs/permissions.md`: new `## Git integration endpoints` section with 12-row table + auth grammar notes. `README.md`: git integration bullet with ops + permissions links. tsc clean (0 errors).

## [2026-06-04] ingest | G7 install-git-hook.sh + connect_repository CLI + PH self-bootstrap + ops docs | [PH-156]

Touched: components/git-integration.md (frontmatter last_touched_ticket→PH-156,
files list +install-git-hook.sh +cli.py, G7 section added to Current behavior,
5 design decision bullets [PH-156], 3 gotcha bullets [PH-156]),
.codemap (2 new entries: scripts/install-git-hook.sh, backend/app/cli.py),
log.md.
Summary: New `scripts/install-git-hook.sh` — POSIX shell, idempotent BEGIN/END
marker block strategy, worktree-safe via `--git-common-dir`, 4 hook types
(post-commit/merge/checkout/rewrite), fire-and-forget bg curl. New
`connect_repository` CLI subcommand in `app/cli.py` — upsert_repository + refresh_secret
management (mint/rotate) + sync_repo backfill + JSON output. New `docs/operations.md`
(mount, CLI, hook install, secret rotation, troubleshooting). README link added.
PH board connected (230 commits backfilled, hook live-verified via smoke commit).
12 new tests in test_connect_repository.py (CLI: row+secret+backfill+idempotency;
script: fresh install, idempotent re-run, update on changed secret, append to existing
hook, worktree-safe, bare-repo guard, --hooks-dir override).
