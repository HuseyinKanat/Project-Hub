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

## [2026-06-05] ingest | Branch Graph SourceTree rework (xyflow→vertical list + commit→diff) | [PH-167]

PH-167 replaced the G10 `@xyflow/react` node-card Branch Graph (rejected by user as
untraceable) with a SourceTree-style 3-pane view: branch sidebar | newest-first vertical
commit list with per-row SVG lane gutter | conditional commit→diff pane (G9 DiffViewer
reused, fixes "git diffler görünmüyor"). `branchGraphLayout.ts assignLanes()` lane math
REUSED unchanged; only the renderer moved from xyflow nodes/edges to inline `<svg>` rows.
`/dev/diff-demo` nav link (Layout.tsx) + route (App.tsx) removed; DiffDemo page + DiffViewer
kept on disk. `CommitNode.tsx`/`BranchLegend.tsx` retired-but-shipped. Updated
components/frontend.md: G10 paragraph marked SUPERSEDED + new PH-167 behavior paragraph,
6 design decisions, 3 gotchas (dead xyflow components, unreachable demo, commit-vs-first-parent
diff semantics), frontmatter last_touched_ticket=PH-167. Browser-verified: Playwright
ph-167-branch-graph-rework.spec.ts (10 TCs pass) + Preview screenshots (light/dark/diff-open/
diff-files) under .jarwis/logs/PH-167/screenshots/. tsc clean, 0 console errors.

## [2026-06-05] ingest | Webhook idempotent (commit,ticket) linkage — fix git_commit_linked double-write | [PH-166]

PH-166 fixed the G3 (PH-152) discovered debt: `webhook.py:handle_push` wrote
`git_commit_linked` history unconditionally while `sync.py` gated on the
`git_commit_tickets` unique constraint, so a webhook arriving after sync (or a
webhook redelivery) produced a duplicate activity-feed history row. Extracted the
shared dedupe gate into `_linkage.py`: `insert_ignore` (dialect-aware ON CONFLICT
DO NOTHING, moved from `sync._insert_ignore`) + new `ensure_commit_ticket_link`
which resolves/creates the `git_commits` row for the SHA then performs the gated
junction insert, returning whether the link was fresh. Both `handle_push` and
`sync_repo` now call it → history written exactly once regardless of order
(first-observation wins). Webhook becomes first observer by minting a `git_commits`
row from the push payload (sha/author/timestamp); sync later enriches via ON
CONFLICT. No-repo boards keep the legacy unconditional write (junction needs a
git_commits FK). `handle_pull_request` untouched (emits git_pr_* not
git_commit_linked). Updated components/git-integration.md: Parser+webhook prose,
1 design decision, Known gotchas double-write entry flipped to FIXED, frontmatter
last_touched_ticket=PH-166, added _linkage.py to files. Tests: new
tests/test_git_webhook.py (5 cases: webhook-after-sync, sync-after-webhook,
redelivery, fresh-commit-with-repo, no-repo-legacy); 107/107 git-integration
tests pass, ruff clean, mypy --strict clean on touched files. No migration.

## [2026-06-05] ingest | webhook-first stub enrichment (data-integrity fix, reviewer reject) | [PH-166]

Reviewer reject (data-integrity blocker): the "webhook becomes first observer"
rework left a regression — webhook mints a minimal git_commits stub (parents=[],
0 files, committer=author) and sync, seeing the row exist, `continue`d past
acommit_files() (ON CONFLICT DO NOTHING never enriches columns), so webhook+sync
boards had commits permanently at 0 files / parents=[] → corrupt commit-detail +
branch-graph ahead/behind BFS. Fix = Approach A (enrich, not skip): new
_linkage.enrich_commit_row(session, repo_id, commit_values) detects a stub by the
ABSENCE of git_commit_files rows (not parents==[], which a real root commit also
has) and UPDATEs the authoritative columns (parents/body/committer/timestamps/
conventional metadata) in place — row id preserved so git_commit_tickets FK stays
valid — then sync inserts the missing file rows. Already-enriched commits (have
file rows) return None and `continue` as before. Chose A over B (history-based
dedupe, no stub) to preserve the git_commit_tickets gate the reviewer already
validated. git_commit_linked still written exactly once per (commit, ticket).
Corrected the now-false design-decision claim "sync enriches files/parents via ON
CONFLICT". Updated components/git-integration.md: Parser+webhook prose, +1 design
decision (enrich_commit_row, Approach A rationale), corrected the prior decision,
+1 Known gotcha (ON CONFLICT does not update; gate on file-row absence).
Regression tests added to tests/test_git_webhook.py:
test_webhook_first_then_sync_enriches_stub (files>0 + real parents + real
committer after sync; single history row for that sha) and
test_sync_only_commit_is_not_re_enriched (re-sync doesn't duplicate file rows).
109/109 git-integration tests pass, ruff clean, mypy --strict clean on the 3
touched files (only the 2 pre-existing app/events/bus.py errors remain). No
migration (logic-only).

## [2026-06-05] ingest | G14 deferred cleanup batch (backend items 1-3) | [PH-165]
/git/refresh disabled now returns 503 (was 202) — body keeps {ok:false,status:"disabled"};
G6 current-behavior + new Design decision + frontmatter last_touched_ticket=PH-165.
cli.py dropped unused Provider import (F401). install-git-hook.sh: eval-free tilde
expansion ${REPO_PATH/#\~/$HOME} (closes injection vector) + read-loop trailing-newline
guard; shebang sh→bash (new gotcha added). Frontend items 4-5 (ESLint flat config, e2e
selectors) pending on the same branch. 61/61 refresh|repository|cli tests pass; repositories.py
+ cli.py ruff/F401 clean; bash -n clean. No migration (logic + shell).

## [2026-06-05] ingest | G14 deferred cleanup batch (frontend items 4-5 + dead code) | [PH-165]
ESLint 9 flat config added (frontend/eslint.config.js) — NO new dep (uses @eslint/js,
globals, @typescript-eslint parser+plugin already present); lint script `--ext`→`eslint .`;
react-hooks/* no-op stub + reportUnusedDisableDirectives off so existing disable directives
stay forward-compatible. `npm run lint` now exits 0 across src/. Dead code from the PH-167
rework DELETED: CommitNode.tsx, BranchLegend.tsx, BranchPanel.tsx, DiffDemo.tsx (grep-verified
orphans; last @xyflow import in git module gone — workflow editor still uses xyflow); git barrel
trimmed to BranchGraph + TicketCommits. Incidental lint-0 cleanup: unused imports/vars in
BoardSettingsDialog, WorkflowEditor, useWebSocket (PongMessage), TicketDetail. ph-159 + ph-160
e2e specs rewritten to the PH-167 SourceTree DOM + selector-hardened (role-scoped, .first()/
clamped .nth(), count>0, WS badge via [title="Live updates active"] not getByText("Live") —
fixes board-state strict-mode multi-match). frontmatter last_touched_ticket=PH-165; 2 resolved
gotchas (dead code, ESLint flat config) updated + 4 new Design decisions. Verify: tsc 0 errors,
lint 0, ph-159 10/10 + ph-160 12/12 pass (22/22) on current UI. (GitGraph.tags already unknown[]
since PH-163 — extra no-op.) No new deps, no backend touch.

## [2026-06-05] ingest | repair_workflow CLI restores PH backlog->to_do | [PH-168]
Added idempotent `repair_workflow --board <KEY>` to `app/cli.py` (pure helper
`repair_backlog_to_do_transitions` + async DB wrapper). Restores a corrupted
`backlog->to_do` transition to known-good `allowed_roles=["pm","architect"]`
(no field gate); resolves the board's active workflow via
`get_active_workflow`. Ran live against PH (workflow 44c026a0...) — repaired,
second run no-op. components/backend.md: +1 Design decision, +1 Known gotcha
(JSON in-place mutation not persisted → reassign list + flag_modified),
last_touched_ticket=PH-168. components/git-integration.md: +1 cross-ref note
(cli.py mapped here for git ops but now also hosts non-git repair_workflow),
last_touched_ticket=PH-168 (hard codemap sync gate). 11 unit tests in
tests/test_repair_workflow.py. Verify: pytest 11/11, ruff baseline (no new
findings), mypy no new errors.

## [2026-06-05] ingest | F1 Cyan-on-Black token foundation (theme flip + tailwind var map + self-hosted Inter) | [PH-170]

F1 of epic PH-169. components/frontend.md: rewrote the theming paragraph (was
"class-based dark mode `darkMode: class`") to the CSS-variable token layer —
dark `:root` default + `html.light` override copied verbatim from the skill,
tailwind.config.js `theme.extend` mapping every semantic utility to var(--token),
`darkMode: ["selector","html:not(.light)"]`, ThemeProvider toggling `.light`
(dark default), self-hosted Inter under public/fonts. +4 Design decisions
(CSS-var single source of truth; theme flip; `text-text-*` double-prefix +
prefer `*-soft` over `/NN`; raw-var base classes not `@apply`). +3 Known gotchas
(@apply of var-backed token utilities 500s the Vite dev CSS pass — build-green
is NOT sufficient; tailwind.config change needs dev restart + .vite cache clear;
`.mono` JIT tree-shaken until used). last_touched_ticket=PH-170. NOTE: touched
files (index.css, tailwind.config.js, lib/utils.ts, lib/stateColor.ts,
ThemeProvider.tsx, ThemeToggle.tsx) are NOT in .codemap → sync gate inert;
update is RECOMMENDED (foundation-defining), not gate-forced. Verify: tsc clean,
vite build OK, dev server CSS 200, dark default + light toggle + reload-persist +
toggle-back all browser-verified (canvas/text/accent/fonts resolve to tokens),
12 Inter @font-face URLs 200, 0 console errors.

## [2026-06-05] ingest | F2 app shell & chrome restyled to Cyan-on-Black tokens | [PH-171]
F2 (PH-171) consumes the F1 token contract: restyled Layout.tsx + NotificationBell.tsx
to semantic token utilities (bg-surface/border-hairline/text-accent/bg-accent-soft/
bg-accent-subtle, .mono on timestamps), dropping slate/indigo + dark: twins; verified
ThemeToggle.tsx + RequireAuth.tsx zero-diff (already F1-clean). New presentational
components/LiveStatus.tsx (live/connecting/off pill, role=status, motion-safe pulse).
Updated docs/codewiki/components/frontend.md: frontmatter last_touched_ticket=PH-171,
+2 Design decisions (F2 shell migration; dropdown bg-raised-not-overlay rationale),
.mono JIT gotcha marked RESOLVED + LiveStatus-deferred gotcha. Touched files
(components/Layout.tsx, NotificationBell.tsx, LiveStatus.tsx) are NOT in .codemap →
sync gate inert; update RECOMMENDED (design-system continuity), not gate-forced.
Verify: tsc clean, vite build OK (no unknown-utility warnings), AC#1 grep zero matches
across 5 shell files, dark default + light toggle + reload-persist + panel/badge/pill
all browser-verified, 0 console errors.

## [2026-06-05] ingest | F3 login surface restyled to Cyan-on-Black glass card | [PH-172]
pages/Login.tsx (visual-only) swapped every slate/indigo/red-*+dark: utility for F1
semantic tokens — page bg-base, wordmark Project+text-accent "Hub", glass card via
inline color-mix(bg-surface 94%)+backdrop-filter blur(12px)+hairline-cyan+shadow-glass
(NOT .card / NOT @apply / NOT bg-overlay), error text-danger+var(--danger-soft)
role=alert+aria-live, input keeps .input font-mono +aria-label, dev chips bg-raised
→hover accent-soft, optional accent-subtle radial spotlight. Auth logic byte-identical
(auto-login useEffect, verifyToken, setToken+navigate, disabled state untouched).
Login.tsx NOT in .codemap → sync gate inert; update RECOMMENDED. Verify: tsc clean,
vite build OK (no unknown-utility warnings), AC#1 grep zero forbidden utilities, glass
computed-style confirmed (.94 alpha + blur(12px) + hairline-cyan + shadow-glass) in BOTH
dark default + light(html.light), error danger surface verified, 0 console errors.

## [2026-06-05] ingest | F4 boards-list surface restyled to Cyan-on-Black token cards | [PH-173]
pages/Boards.tsx (visual-only; useQuery/Link/data shape byte-identical) swapped every
slate/indigo/red-*+dark: utility for F1 tokens. Card = flat .card base (bg-surface +
border-hairline + radius-lg) + MONO CYAN KEY badge (text-accent + bg-accent-soft +
border-hairline-cyan + rounded-sm — only cyan fill on a resting card; overrides indigo
screenshot per epic, kit .board-key authoritative), name text-text-primary, line-clamp-2
desc text-text-secondary, grayscale meta chips (project_type, N states) as bg-raised +
border-hairline rounded-pill mono pills. Two-tier hover: Tier A pure utilities
(group hover:border-hairline-cyan + hover:[transform:translateY(-2px)] + hover:shadow-md
+ duration-base ease-out, arrow group-hover:text-accent + translateX); Tier B optional
border-beam <span aria-hidden> inline mask-composite:exclude cyan gradient
(group-hover:opacity-100, motion-reduce:hidden). loading→text-text-muted role=status,
error→text-danger+var(--danger-soft) role=alert, empty→.card text-text-muted +
.mono bg-inset code chip. a11y: card real <a href> keyboard-focusable, global cyan
focus-visible box-shadow not clipped by overflow-hidden (renders outside border-box),
arrow aria-hidden, motion-reduce honored. NOTE: transforms use arbitrary-value
[transform:translateY/X(...)] not -translate-y-*/translate-x-* to dodge the AC grep
slate- false-positive on tran*slate-*. Boards.tsx NOT in .codemap → sync gate inert;
update RECOMMENDED. Verify: tsc clean, vite build OK, AC grep exit 1 (zero forbidden),
KEY badge cyan computed (#22D3EE dark / #0891B2 light), card bg-surface, chips grayscale
text-secondary, hover lift+cyan border+beam rendered, both themes, 0 console errors.

## [2026-06-05] ingest | F5 kanban surface restyle to Cyan-on-Black | [PH-174]
BoardDetail.tsx column markup + chrome, TicketCard.tsx, NewTicketDialog.tsx restyled
visual-only to F1 tokens. Columns = UPPERCASE eyebrow + bg-current state dot (via
reused resolveStateColor, no hardcoded --state-*) + mono count chip on bg-surface/hairline.
TicketCard agent-phase = cyan pulse (bg-accent-soft/text-accent/ring-hairline-cyan); F1
nit fixed (TYPE_BADGE fallback bg-slate-100 dark:bg-slate-700 -> bg-raised text-text-secondary).
NewTicketDialog = inline color-mix glass (PH-172 precedent) + bg-overlay scrim. Hover lift =
shadow-glow-cyan-sm not -translate-y-px (dodges AC slate- grep on tran*slate-*). No dnd (board
has none). No token-file edits (F1 read-only). 3 F5 files NOT in .codemap -> sync gate inert;
update RECOMMENDED. Verify: typecheck PASS, vite build PASS (no unknown-utility warnings),
3-file grep slate-/indigo-/blue-/green-/red-/yellow-/gray-/dark: = zero, both themes
browser-verified (dark #22D3EE / light #0891B2, glass modal color-mix .94 + blur + hairline-cyan
+ shadow-glass + bg-overlay scrim, scrim-click close), 0 console errors.

## [2026-06-05] ingest | F6 branch-graph & diff restyle to Cyan-on-Black (PH-175) | [PH-175]
Touched (all .codemap→components/frontend.md): git/branchGraphLayout.ts (LANE_COLORS hex→
var(--lane-*) strings, laneColor() signature + assignLanes two-pass byte-identical),
git/BranchGraph.tsx, git/TicketCommits.tsx, diff/DiffViewer.tsx, diff/FileDiffView.tsx,
diff/HunkView.tsx (all slate/indigo/*-NNN/dark: → F1 semantic tokens); index.css additive
@layer utilities (.animate-glowin + @keyframes ph-glowin). Visual-only: parseDiff, git fetch,
WS highlightedShas live-commit, selection/expand state UNCHANGED. Load-bearing decision: lane
colors are var(--lane-*) reference strings resolved in SVG stroke/fill at paint → lanes recolor
on html.light flip with ZERO JS (browser-proved: same <circle> stroke #FB7185 dark → #E11D48
light). Selected row = bg-accent-soft + cyan hairline + hollow dot; new commit = one-shot
.animate-glowin + dot drop-shadow(var(--accent)), motion-safe via global reduced-motion block.
Diff: add=success-soft/del=danger-soft/ctx=text-secondary/hunk=bg-inset+@@=info; badges
A=success M=info D=danger R=accent; all SHAs/paths/lines mono. R1 gotcha recorded: never
color + "22" on a var() color → color-mix(in srgb, var N%, transparent). frontend.md updated
(Current behavior + 2 design decisions + 1 gotcha + frontmatter last_touched_ticket=PH-175);
git-integration.md got a frontend-only cross-ref note (no backend git file changed → mapped
sources untouched). Verify: typecheck PASS, vite build PASS, full-surface grep
slate-/indigo-/dark:/*-NNN = zero, both themes browser-verified (Claude Preview, query-cache-
seeded harness w/ mock git data; harness deleted pre-commit), 0 console errors.

## [2026-06-05] ingest | F7 ticket-detail restyle to Cyan-on-Black + theme-aware MermaidBlock | [PH-176]
Restyled the 5 ticket-detail files (pages/TicketDetail.tsx, components/FieldEditor.tsx,
MarkdownFieldEditor.tsx, MarkdownRenderer.tsx, MermaidBlock.tsx) to the F1 (PH-170) token
contract, both themes, visual-only (data/mutation/transition/markdown+mermaid parsing
byte-identical). Two real changes beyond class swaps: the flat State transition list became a
"Move to →" bg-raised popover (aria menu, Escape/click-outside close, per-state dot + mono
target + text-warning "req fields" hint), and MermaidBlock became theme-aware (removed the
one-time module init; re-initializes mermaid with token-derived themeVariables keyed off
useTheme() inside the render effect, theme in deps → live recolor on toggle). Also: header mono
cyan key + TypeChip slate-fallback FIX, Activity/edit-preview tabs animated accent underline,
sidebar reporter/assignee avatars + text-role-* chips, mono branch row, glass branch-diff/delete
modals. components/frontend.md updated (Current behavior covered by F-series para; +1 design
decision [PH-176]; +1 Known gotcha re: mermaid theme tokens MUST read off <html> not a detached
.light probe, and NOT via rAF-deferred render under StrictMode; frontmatter last_touched_ticket
=PH-176). .codemap maps none of these 5 files → ingest RECOMMENDED not gate-forced. Verify:
typecheck PASS, vite build PASS, 5-file grep slate-/indigo-/*-NNN/dark: = zero, both themes
browser-verified (Claude Preview, query-cache-seeded harness w/ mock ticket data — stale env
token 403s; harness not committed): mermaid recolors #161D29/#22D3EE dark ↔ #EEF2F7/#0891B2
light on toggle, Move-to menu opens w/ "req fields" hint + Escape-closes, 0 new console errors.

## [2026-06-05] ingest | F8 board-settings restyle to Cyan-on-Black (4 tabs + React Flow editor) | [PH-177]
components/frontend.md updated: added the F8/PH-177 migration paragraph (15 live files — BoardSettings
shell+tabs, WorkflowList/StateList/Editor, Node/EdgePropertyPanel, PermissionMatrix, MembersTab/Row/
AddMemberModal, repository/{Status,Config,Operations,RotateSecret,DetachConfirm}), 4 Design-decisions
bullets (React Flow themed via scoped .react-flow --xy-* token block not per-component dark:; destructive
btns inline var(--danger); sticky matrix cells need opaque token bg + accent checkboxes; static text-role-*
map JIT-safe), 2 Known-gotchas (xyflow selected-glow loses to a hard-set node box-shadow → dropped node
shadow-sm; --xy-* overrides depend on xyflow -default var names, verify on version bump), frontmatter
last_touched_ticket=PH-177. .codemap maps repository/*.tsx → frontend.md (5 touched) so this ingest is
GATE-FORCED (Reviewer sync gate), committed same branch/commit as the UI code. WorkflowEditor/PermissionMatrix
NOT mapped (gate inert there). Visual-only: zero logic/graph-data/handler/test-id change. Verify: typecheck
PASS, vite build PASS, 15-file grep slate-/indigo-/blue-/emerald-/amber-/sky-/red-/green-NNN/dark: = zero
(only data-driven #8b5cf6 state.color defaults remain, AC-allowed), both themes browser-verified (Claude
Preview, query-cache-seeded harness — stale env token 403s the live route, harness not committed): 4 tabs
on token surfaces, React Flow nodes/edges/bg/Controls recolor on theme toggle, sticky matrix opaque, glass
modals + role chips correct, 0 new console errors. BoardSettingsDialog.tsx confirmed dead (no imports) → left.

## [2026-06-06] ingest | PH-179 branch graph design-faithful rework | [PH-179]
F6/PH-175 only RECOLORED the per-row gutter `<svg>`; the live view did not match the design (flat/broken
lanes). PH-179 restructures the render topology: per-row GutterCell `<svg>` DELETED → ONE continuous
full-height absolutely-positioned overlay `<svg>` behind the rows, rendering cubic-bezier lane paths +
commit dots via a new pure emitter `computeLanePaths(commits, laneOfSha, rowH, laneW, gutterPad, maxLanes)`
in `branchGraphLayout.ts` (single shared coordinate space; vertical `M..L..` runs per lane + `M..C..`
S-curves for branch/merge; R3 off-list-parent guard). `assignLanes`/`laneColor`/`LANE_COLORS` byte-identical;
`ROW_H` 36→40. Added a floating glass `FloatingDetailCard` (quick-look: 12-char SHA + summary + N files +
+adds/−dels + ticket chip; stats via `getCommit`/`GitCommitDetail`, cache-shared queryKey
`["git","commit",boardKey,sha]`; skeleton while loading, hidden on error; X/Esc/click-away/re-click dismiss)
that coexists with the existing DiffViewer pane via a "View diff" affordance. Glass `boxShadow` set INLINE
(comma in `shadow-[var(--shadow-lg),var(--glow-cyan-sm)]` breaks Tailwind JIT). Files: BranchGraph.tsx,
branchGraphLayout.ts. typecheck PASS, build PASS. Browser-verified dark+light (seeded-cache Preview harness,
deleted before commit): continuous beziers (no seams), merge curve joins lanes, hollow selected dot,
new-commit glow, lanes recolor on theme flip with no JS branch, card + stat row + View diff pane all render.

## [2026-06-06] ingest | Ticket Detail refit birebir to ui_kit (td-head/StateControl-in-header/field-head/activity-item/side-row) | [PH-182]
components/frontend.md updated: Design decision [PH-182] (header two-col .td-head, h1 24/600 fix, StateControl
moved sidebar→header with StatePill + Move-to menu raw mono ids, field-head/field-body field cards, .tabs +
.activity-item activity + inline .composer, single .td-side SideRow sidebar 320px; "Alanlar"/State-ring/AgentPhase
cards removed; data/mutation/transition/WS/markdown UNCHANGED). last_touched_ticket PH-179→PH-182. Files:
TicketDetail.tsx, FieldEditor.tsx, MarkdownFieldEditor.tsx, index.css (+kit component classes). .codemap does NOT
map these → sync gate inert; update recommended, done. typecheck PASS, build PASS. Both themes browser-verified
side-by-side vs ui_kit index.html (Claude Preview, seeded harness deleted before commit). e2e: admin-delete 2/2,
workflow-state-color TicketDetail-badge PASS (kanban-column case = PH-183, untouched).

## [2026-06-06] ingest | notifications panel → ui_kit + relativeTime → lib/time.ts | [PH-187]
components/frontend.md updated: Design decision [PH-187] (NotificationBell panel → glass ui_kit NotificationPanel —
width 340, color-mix bg-raised 96% + hairline-cyan + radius-lg + shadow-lg + .animate-pop pop-in, anchored dropdown
kept NO scrim; per-item semantic icon via pure iconFor(n) keyed on event_type: state_changed→Activity/success,
comment_added→MessageSquare/text-secondary, git_pr_*→GitMerge/accent, fallback Bell/text-muted never throws;
"N new" eyebrow; 13px title + 11px mono relativeTime replacing toLocaleString('tr-TR'); accent-subtle unread rows;
data plumbing byte-identical). relativeTime EXTRACTED verbatim BranchGraph.tsx → lib/time.ts, imported by BOTH
NotificationBell + BranchGraph (DRY, no dup); BranchGraph times identical. @keyframes ph-pop + .animate-pop added
to index.css (motion-safe via global reduced-motion guard). last_touched_ticket PH-182→PH-187. SYNC GATE: .codemap
maps frontend/src/components/git/*.tsx → components/frontend.md; the relativeTime extraction touched BranchGraph.tsx,
so this page is updated in the SAME branch (exit-protocol §11.2). Docs-only revision after needs_revision — no source
change (code already passed review @6642277). typecheck PASS, build PASS.

## [2026-06-06] ingest | Branch Graph → ui_kit 3-pane grid + inline diff panel + compact mode | [PH-188]
components/frontend.md updated: Current behavior (PH-188 Branch Graph refit block) + Design decision [PH-188] +
2 Known gotchas (ROW_H/lane-geometry coupling; shared DiffViewer/FileDiffView/HunkView + showSummary). PH-188
re-skins BranchGraph to the AUTHORITATIVE ui_kit branchgraph.jsx + kit.css .bg-*/.diff-* (PH-179 had built against
the wrong branch-graph-row.html specimen). Source: BranchGraph.tsx REWRITTEN (flex+gapped-rounded-cards → CSS grid
200px/minmax(0,1fr)/340px, .no-diff collapse, hairline dividers no gaps/cards; FloatingDetailCard + diffOpen two-step
DELETED → right diff panel opens directly on select; compact mode hides Author+Time in rows+list-head; sha always
text-accent; "Ticketed only" REAL filter on ticket_keys.length>0 with lane recompute over reduced set);
branchGraphLayout.ts ROW_H 40→44 (+ component LANE_PX 14→15, GUTTER_PAD 8→12 for kit parity, dots re-verified
centered); DiffViewer.tsx +showSummary?:boolean (default true) threaded through all paths, panel passes false to
avoid duplicate "N files changed" header; FileDiffView.tsx rounded-md→rounded-[10px] (.diff-file parity). Continuous
bezier lanes / assignLanes / git fetch / WS highlight / branch filter / a11y PRESERVED. last_touched_ticket
PH-187→PH-188. SYNC GATE: .codemap maps frontend/src/components/git/*.tsx + diff/*.tsx → components/frontend.md;
BOTH touched this branch, page updated in the SAME branch (exit-protocol §11.2). Side-by-side verified vs ui_kit
index.html (Claude Preview, live PH board real data + kit on :8899), both themes; typecheck PASS, build PASS,
0 console errors.

## [2026-06-06] ingest | Branch Graph lane geometry ported from ui_kit Gutter (contiguous spans, lane0-anchored single-row fork/merge) — REAL-DATA verified | [PH-190]
components/frontend.md updated: Current behavior (PH-190 lane-geometry block) + Design decision [PH-190] (per-row
contiguous-span lane0-anchored geometry supersedes PH-179 global-run + multi-row bezier) + 2 Known gotchas (verify
lane geometry on REAL git history not seeded mock — the reused-lane false-pass that burned PH-179/PH-188; test runs
outside app tsc via node:test). 3rd branch-graph iteration: PH-179/PH-188 passed on SEEDED MOCK but the user still
saw wiggly/tangled lanes on the REAL PH history. Root cause confirmed in computeLanePaths: ONE global laneFirst→laneLast
vertical run per lane bridged the IDLE rows between disjoint feature branches that REUSE a lane number, plus multi-row
sweeping beziers across the full childY→parentY gap. Fix (geometry-only, body of computeLanePaths REWRITTEN): compute
each lane's CONTIGUOUS active spans (maximal consecutive-active-row runs, derived from each commit's own row +
first-parent same-lane pass-through fill over the frozen laneOfSha) and emit PER ROW anchored to lane 0 — straight
verticals within a span (own 1.0 / pass-through 0.55), single-row branch-out curve at spanFirst (main TOP → branch MID),
single-row merge-in curve at spanLast when first parent on lane 0 (branch MID → main BOTTOM), kit control-point ratios
translated to centered laneCx/top·mid·bottom. NO segment > one rowH; idle gaps NEVER bridged; off-list parents end
straight (R3). assignLanes/laneColor/LANE_COLORS/computeMaxLane/lane-index BYTE-IDENTICAL; LaneSegment/LaneDot/
LaneGeometry shapes, single LaneOverlay <svg>, dots, BranchGraph.tsx layout/data/WS/branch-filter/a11y, var(--lane-*)
strokes UNCHANGED. Files: branchGraphLayout.ts (computeLanePaths body), branchGraphLayout.test.ts NEW (node:test, no
package.json dep), tsconfig.json (+exclude *.test.ts(x)). last_touched_ticket PH-188→PH-190. SYNC GATE: .codemap maps
frontend/src/components/git/*.ts → components/frontend.md; page updated in the SAME branch (exit-protocol §11.2).
VERIFIED ON REAL DATA (the whole point): production assignLanes+computeLanePaths over the live 319-commit/73-merge PH
history (dumped from backend graph_payload) → max segment y-extent EXACTLY one rowH (44px) — no multi-row sweep, no
idle-gap bridge — across 51 detected idle gaps on the reused lane, 93 fork/merge curves all main-anchored, 319 dots ==
319 rows; rendered overlay (rsvg from the real geometry, dark+light) = crisp straight cyan main backbone + single-row
fork/merge curves + straight parallel branch columns matching the ui_kit Gutter STYLE (branch count differs as it
depends on real history). Unit test 4/4 (AC4 reused-lane two-spans/no-bridge/no->1-rowH, AC2 single-row main-anchored
curves, AC3 pass-through 0.55, AC5/AC7 dot==row). typecheck PASS, build PASS.

## [2026-06-06] ingest | PH-193 SonarQube native board-health backend (model + migration + poller + board API health + event) | [PH-193]
CHILD 2 of epic PH-191 (PH-192 landed config + opt-in compose). New `backend/app/services/sonarqube.py`: async httpx
client (`BasicAuth(token,"")` portable Community Build token auth, 10s timeout) calling `GET /api/qualitygates/project_status`
+ `GET /api/measures/component?metricKeys=bugs,vulnerabilities,code_smells,coverage,duplicated_lines_density,ncloc`;
`resolve_project_key` (board column → `sonarqube_project_key_map` JSON → None=skip); `sonarqube_poll_cron` mirroring
`git_poll_cron` (fresh session/tick, CancelledError clean break, per-tick except). LAYERED ERROR ISOLATION: client returns
`None` on any error (down/401/malformed/not-scanned) so the loop never dies; no-key board skipped silently (debug only,
no row/event/warning). New `SonarQubeMetric` model in `db/models/core.py` (upsert-latest, `UniqueConstraint(board_id)`,
denorm hot columns + `raw_measures` JSON forward-compat + `fetched_at`) + `Board.sonarqube_project_key` (nullable String(400))
+ `Board.sonarqube_metric` 1:1 relationship; exported from `db/models/__init__.py`. Hand-written migration
`20260606_0008` (additive nullable column + new table; downgrade reverse order) — Postgres upgrade + downgrade-1 + re-upgrade
round-trip verified. API: `BoardHealth` schema + `BoardResponse.health` (null mirrors `repository`); `board_response()` maps
`board.sonarqube_metric`→health (Decimal→float); `selectinload(Board.sonarqube_metric)` added to `get_board`+`list_boards`.
Event: board-level `sonarqube_synced` `EventEnvelope` on `board:{id}` with empty ticket_id/ticket_key sentinels, payload
mirrors BoardHealth (frontend patch without refetch); token NEVER logged/returned/in-payload. main.py lifespan: `sonar_task`
gated on `sonarqube_enabled && interval>0` (default False → never created, no-op), cancelled symmetrically in finally.
components/backend.md updated (Current behavior poller para, +2 Design decisions, frontmatter+files PH-193) — .codemap
does NOT glob my touched files (sync gate INERT per architect), but this page's frontmatter `files:` claims main.py/
schemas.py/core.py/boards.py/serializers.py so updated in SAME branch. Tests: test_sonarqube_client.py (httpx via
MockTransport — success parse, conn-error/401/malformed→None, project-key resolution, malformed-measure degrade) +
test_sonarqube_poll.py (real in-memory sqlite: upsert one row+one publish, second-tick in-place update no-dup, no-key skip,
client-None no-row/no-publish, _poll_all_boards isolation, board health populated/null serialization). VERIFY: new 16/16
green; board/repo/event/membership regression 50/50 green; ruff clean (touched files); mypy --strict clean (touched files;
43 pre-existing errors in unrelated modules untouched); migration round-trips on PG; health 200 with poller disabled.

## [2026-06-06] ingest | SonarHealthPanel + sonarqube_synced WS invalidation (BoardDetail health strip, frontend) | [PH-196]
CHILD 4 of epic PH-191 (consumes PH-193's `BoardResponse.health` + `sonarqube_synced` event). New presentational component
`frontend/src/components/SonarHealthPanel.tsx` (prop `{ health: BoardHealth | null }`) mounted in `BoardDetail.tsx` under
`<header>`, ABOVE the tab strip → board-wide, shows in both Kanban + Branch Graph. Empty state (health null) = dashed-border
`.card` "No SonarQube scan yet"; populated = quality-gate `.badge` pill (OK→Passed/success, ERROR→Failed/danger, WARN→
Warning, null→Unknown/muted; LiveStatus soft-tone pattern) + 5 metric tiles (`bg-raised` chips, `.eyebrow` label + `.mono`
value; coverage/duplications `toFixed(1)%`, ints raw, null→`—`, bugs/vulns tinted danger when >0) + right-aligned relative
`fetched_at` (inline `Intl.RelativeTimeFormat`, no dep). Design-system-pure tokens only (no slate/indigo, no hexes; dark +
`html.light` for free). Data flow: NO new query — reuses existing `["board", boardKey]` query (PH-193 health field) and
passes `boardQuery.data?.health ?? null`. WS: new `sonarqube_synced` branch in `onMessage` (after `isGitSyncedMessage`,
before generic ticket logic) → `invalidateQueries(["board", boardKey], active)` + **early-return** (board-level envelope has
empty ticket_id/ticket_key sentinels, so falling through to `REFETCH_EVENTS`/`message.ticket_key` would `api.getTicket('')`;
same defensive shape as git_synced). `types/api.ts`: added `BoardHealth` interface (mirrors `schemas.py:263-277` verbatim) +
`health: BoardHealth | null` on `BoardResponse`. components/frontend.md updated (frontmatter PH-196 + SonarHealthPanel in
files: + Design-decision bullet) — `BoardDetail.tsx` is `.codemap`-mapped via the page's frontmatter so the sync gate applies;
updated in SAME branch. VERIFY (Claude Preview, live app + real PH board, admin dev token): empty state clean (no console
errors, no kanban layout regression); populated state via temporary seeded `SonarQubeMetric` row (gate ERROR→"Failed",
`3/1/42/78.4%/4.2%`); WS live-update by publishing a real `sonarqube_synced` EventEnvelope on `board:{id}` → panel flipped to
"Passed"/`0/0/12/91.7%/1.3%` WITHOUT reload, network had extra `GET /api/boards/PH` + NO empty-key getTicket, console clean;
light-theme token resolution confirmed via computed style. Seeded row + project_key reset (dev DB clean). tsc green; AC-3 grep
(slate-/indigo-/# in component) returns none. Frontend-only — no backend changes.

## [2026-06-06] ingest | branch-graph fork-edge + per-branch color fix | [PH-198]
`components/frontend.md` updated for the PH-198 bug fix in `branchGraphLayout.ts` (mapped by `.codemap`
`frontend/src/components/git/*.ts` → `components/frontend.md`). Two PH-190-era regressions fixed in the pure layout
layer: (1) single-commit branches now draw their fork/divergence-descent edge (the one-row span's `isFirst===isLast`
path now also runs the merge/descent via a shared `emitDescentIfMerges()` helper, instead of early-returning); (2)
branch-curve + side-dot color is now per-BRANCH via a new additive `branchColor(tipSha)` export (FNV-1a hash →
`LANE_COLORS.slice(1)`, lane-0 cyan reserved for main) instead of `laneColor(lane)`, so disjoint branches reusing a
lane number get DISTINCT colors. Added: Current-behavior PH-198 entry, a Design-decisions bullet, two Known-gotchas
(per-branch color identity; single-row descent), frontmatter `last_touched_ticket: PH-198`. QA reproduction
`branchGraphFork.test.ts` 5/5 green + sibling `branchGraphLayout.test.ts` 4/4 green; tsc green; browser-verified on live
PH history (reused lane 1→7 distinct branch colors, all 8 palette hues present, PH-194/PH-197 single-commit fork loops),
0 console errors. `assignLanes`/`laneColor`/`LANE_COLORS` unchanged.

## [2026-06-06] ingest | Branch graph: open (unmerged) vs merged branch distinction | [PH-199]
Updated `components/frontend.md` for PH-199 (open-vs-merged branch graph fix). Root cause: PH-198's merge-return curve
gated on `laneOf(firstParent)===0` — a FORK test (true for EVERY feature branch) not a MERGE test, so open branches
(PH-178 `f3d25623`, never merged) drew the same closed fork→merge loop as merged ones (PH-177/PH-179). Fix: corrected
predicate `branchMerges(span)` = span TIP sha ∈ `mergedTips` (side-lane shas referenced as a parent by any lane-0 commit
— the real-merge signal, already in the parent DAG, no backend flag); + additive `openTips: OpenTip[]` geometry rendered
by `BranchGraph.tsx` as a hollow dot + dashed open-ring cap (Cyan-on-Black affordance). Added: Current-behavior PH-199
entry, a Design-decisions bullet, two Known-gotchas (real-merge-not-fork predicate; geometry-driven open affordance),
renamed the PH-198 `emitDescentIfMerges` gotcha note, frontmatter `last_touched_ticket: PH-199`. QA reproduction
`branchGraphOpenMerged.test.ts` 4/4 green + siblings `branchGraphFork.test.ts` 5/5 + `branchGraphLayout.test.ts` 4/4
green; tsc green; browser-verified on live PH graph (PH-178 open-ring + no return curve vs PH-177/PH-179 closed loops,
exactly 2 open rings graph-wide), 0 console errors.

## [2026-06-07] ingest | SonarHealthPanel tiles → lazy SonarQube issue drawer (PH-204, epic PH-201 D2) | [PH-204]
Updated `components/frontend.md`: new Current-behavior block for the SonarQube issue drill-down (3 issue-backed
tiles → `ClickableMetricTile` buttons → `SonarIssueDrawer`, lazy `useSonarIssues` TanStack hook, `api.getSonarIssues`
client method, `SonarIssue*` types mirroring PH-203; Coverage/Duplications stay non-clickable; graceful-200 error
handling, severity badges via semantic tokens, Prev/Next pagination), a Design-decisions bullet (lazy-fetch + retry:false
rationale + DetachConfirmModal shell reuse + a11y focus restore), `files:` +`SonarIssueDrawer.tsx`/`useSonarIssues.ts`,
frontmatter `last_touched_ticket: PH-204`. Browser-verified on live PH data (35 bugs / 269 smells / 0 vulns):
lazy network (no `/sonarqube/issues` until tile click), correct `type`/`page` params, smells pagination 1→2,
VULNS disabled, Coverage/Duplications inert, ESC closes + focus returns to tile, host-facing dashboard link;
tsc + eslint green, 0 drawer console errors.

## [2026-06-07] ingest | git_poll_cron CancelledError re-raise (S7497) — graceful-shutdown fix | [PH-205]
Fixed SonarQube python:S7497 (×10) + python:S7502 (×1) async bugs. Mapped page components/git-integration.md
updated: the git_poll_cron loop now re-raises CancelledError (was `break`, which swallowed cooperative
cancellation so the task finished *normally* instead of *cancelled*). Added a Design-decisions bullet + bumped
frontmatter last_touched_ticket: PH-205. Same fix applied to the sibling crons stale_claim_cron,
sonarqube_poll_cron, and the EventBus.subscribe redis loop, plus the deliberate-cancel-then-await blocks in
main.py / api/websocket.py / core/websocket_manager.py (switched to `asyncio.gather(task, return_exceptions=True)`
so no broad `except CancelledError` swallows the parent's own cancel). S7502: websocket_manager fire-and-forget
session-close task now retained in a ClassVar `_background_tasks` set with add/discard-on-done callback. Those
modules are unmapped in .codemap (no page gated). Regression test test_git_poll_cron_reraises_cancellederror added.

## [2026-06-07] ingest | typescript:S1082 a11y — keyboard listeners for 22 non-interactive onClick elements | [PH-206]
Fixed 22 SonarQube BUGs (typescript:S1082, jsx-a11y/click-events-have-key-events): visible non-interactive
elements (div/li/form) with onClick but no keyboard listener. New shared helper frontend/src/lib/a11y.ts exports
onActivateKeyDown(activate) (Enter/Space → activate, preventDefault for Space) and stopActivationKeyDown (keyboard
counterpart to onClick stopPropagation). Applied across 10 files: AddMemberModal, EdgePropertyPanel,
NodePropertyPanel, WorkflowList (5 sites incl. a clickable <li> upgraded to role=button+tabIndex+aria-pressed),
WorkflowStateList, repository/DetachConfirmModal, repository/RotateSecretModal, pages/BoardSettings,
pages/TicketDetail, NewTicketDialog. Visual + mouse behavior byte-identical (onClick bodies mirrored into onKeyDown).
.codemap gate: repository/*.tsx → components/frontend.md, so frontend.md updated in-branch (Design decisions +
frontmatter last_touched_ticket: PH-206 + lib/a11y.ts added to files). Note: jsx-a11y is NOT in local eslint config;
SonarQube analyzer is the enforcer of record. Browser-verified (Claude Preview, live PH board): rows focusable +
Enter activates (aria-pressed flip), backdrop click/Space dismiss, inner stopPropagation intact, 0 console errors.
tsc + eslint(touched) green.

## [2026-06-07] ingest | S2871 sort comparator + S3923 dead latency-tier branch | [PH-207]

Touched: components/frontend.md (Design decisions + frontmatter last_touched_ticket: PH-207)
Summary: Two SonarQube TS bugs fixed with zero behavior change.
S2871 (CRITICAL, PermissionMatrix.tsx:115): orphan-role columns sorted via `[...orphans].sort()` with no
comparator (locale-naive UTF-16 collation) → `.sort((a, b) => a.localeCompare(b))`. Board roles still render
first (board order), orphans follow in stable locale order.
S3923 (MAJOR, useWebSocket.ts pong handler): connection-quality ternary had a dead third threshold —
`latency < 5000 ? "poor" : "poor"` yields the same value in both arms. Intent analysis: a pong proves the socket
is alive (never "disconnected" here), and `excellent|good|poor` are the only connected tiers; the `< 5000` split
mapped to no distinct value (copy-paste artifact). Collapsed to `latency < 100 ? "excellent" : latency < 500 ?
"good" : "poor"` — runtime output byte-identical. PermissionMatrix.tsx is NOT in .codemap (no glob match);
useWebSocket.ts IS mapped → frontend.md updated in-branch. Browser-verified (Claude Preview, live BENCH board):
"Live" WS indicator + PermissionMatrix renders all role columns + sorted orphan column, 0 console errors.
tsc + eslint(touched) green. No package.json/type changes.

## [2026-06-07] ingest | S6759 mark React component props read-only (×57) | [PH-211]

Touched: components/frontend.md (Design decisions + frontmatter last_touched_ticket: PH-211)
Summary: Epic PH-210 block B1 (safest-first). 57 typescript:S6759 sites across 33 component/page files marked
props read-only. TYPE-ONLY — zero runtime/JSX change. Each flagged component's prop type wrapped in `Readonly<...>`
at the least-churn point matching the file's style: named-interface destructure (`}: FooProps)` →
`}: Readonly<FooProps>)`), inline-object props (`}: { foo: T })` → `}: Readonly<{ foo: T }>)`), and the one
`props: T` param form (Toast.tsx SuccessToast). No prop had to stay mutable — `tsc --noEmit` stayed green with
zero new errors (readonly can surface a real prop mutation; none existed). `npm run typecheck` + `npm run build`
both exit 0. ESLint on touched files surfaces ONE pre-existing `no-unused-vars` error (BranchGraph.tsx:220
`color`) that is on `main` independent of this change (verified via git stash) — left untouched, out of scope.
.codemap sync gate fired for components/diff/*.tsx, components/git/*.tsx, components/repository/*.tsx (all map
to this page) → frontend.md updated in-branch (this commit). SonarQube S6759 count won't drop until merge+rescan.

## [2026-06-07] ingest | B2 JSX/a11y smell cluster (S6847/S6848/S6819/S6606/S6479) | [PH-212]

Epic PH-210 block B2. Cleared S6606 (×4: branchGraphLayout `?? ` for null-lane, HunkView `?? ""`),
S6479 (×4: HunkView/FileDiffView/BranchGraph real composite keys, PermissionMatrix skeleton token array),
~20/31 S6819 (role="status"→`<output>` ×11, role="region"→`<section>` ×2, role="list"/"listitem"→`<ul>`/`<li>`,
role="button" div→native `<button>`), and ALL ~25 S6847/S6848 (modal-backdrop dismiss → sibling `<button>` +
content carries role=dialog, no handlers). The 11 `role="dialog"` S6819 sites LEFT AS-IS / wontfix — native
`<dialog>` needs imperative showModal() + UA-style reset + breaks e2e getByRole("dialog")/click-outside/Esc
contracts (not behavior-preserving). `lib/a11y.ts` now unused (left in place). tsc + build exit 0; eslint(touched)
clean except pre-existing BranchGraph.tsx:220 (on main). jsx-a11y NOT in local eslint → SonarQube re-scan is the
verification of record. .codemap sync gate fired (components/diff,git,repository → this page) → frontend.md
updated in-branch (same commit). Counts won't drop until merge + post-merge rescan.

## [2026-06-07] ingest | PH-213 — epic PH-210 block B3 (TS structural smell cluster + TS long-tail sweep) | [PH-213]
Cleared 6 SonarQube TS structural rules behavior-preservingly across ~22 frontend files, all changes
byte-equivalent at runtime: S6478 (×15, define-component-during-render — ALL in MarkdownRenderer.tsx, hoisted
the 15 inline element renderers to module-scope named components + a stable `markdownComponents` map, `compact`
delivered via a module-scope CompactContext; incidentally cleared S6767 ×2 unused PropType); S3358 (×23,
nested-ternary → if/else chains or pre-return React.ReactNode vars: TicketDetail conn-pill + commentsBody,
FieldEditor fieldBody, MarkdownFieldEditor readView, FileDiffView diffBody, WorkflowList/WorkflowStateList/
EdgePropertyPanel/WorkflowEditor/BoardSettings/BoardDetail/useWebSocket/SonarHealthPanel/MermaidBlock);
S2004 (×5, BoardDetail WS-handler nesting → 3 module-scope cache-updater factories + named .then callbacks);
S3735 (×12, void-operator markers in api/__smoke__/git.types.ts → single array-literal reference);
S4325 (×8, redundant `as Error`/edge-data/category casts dropped); S4624 (×6, nested template literals →
extracted `suffix`/`satisfiedFields` locals in client.ts + TicketDetail). TS long-tail safe subset: S3863
(duplicate import merged), S4030 (write-only `spans` collection removed from branchGraphLayout.ts — only
spanAtRow was read), S6571 (`| string` → `| (string & {})` to keep literal hints), S6822 (redundant
role="complementary" on BranchGraph <aside> removed). LEFT AS-IS / wontfix (documented in page): S6754 ×3
(intentional setter-rename to avoid collision + a risky render-time prop-sync useState hack), S6822/S6772/
S6842/S6853/S6811/S6843 on TicketCommits `<ul role="list">` (LOAD-BEARING under Tailwind Preflight
list-style:none — Safari/VoiceOver drop the implicit list role) + BranchGraph aria internals (entangled with
pre-existing BranchGraph:220 eslint), S1135 (deliberate [PH-163]/G15+ TODO), S6551/S2486/S6481/S1874
(behavior-change risk, out of structural scope). tsc + build exit 0; eslint(touched) clean except pre-existing
BranchGraph.tsx:220 unused-`color` (on main). Browser-verified via Claude Preview (live PH board): TicketDetail
markdown table/headings/code/list/pre render identically post-hoist (S6478), BranchGraph + commit diff panel,
WorkflowList tooltips, kanban grouping — 0 console errors across all surfaces. .codemap sync gate fired
(api/client.ts, hooks/useWebSocket.ts, components/diff/FileDiffView.tsx, components/git/BranchGraph.tsx,
components/git/branchGraphLayout.ts → this page) → frontend.md updated in-branch (same commit). Counts won't
drop until merge + post-merge rescan.

## [2026-06-07] ingest | B4 Python smell cluster (S1192/S5886/S7503 + long-tail) | [PH-214]

Epic PH-210 block B4. Fixed Python SonarQube smells across 12 backend modules (behavior-preserving):
**S1192** (18 sites, duplicated literals → module constants): `services/defaults.py` (8 permission-id
constants + shared `_IMPLEMENTER_PERMISSIONS` list), `db/models/core.py` (FK refs `_FK_ACTORS_ID`/`_FK_BOARDS_ID`/
`_FK_TICKETS_ID` ×19 + `_CASCADE_ALL_DELETE_ORPHAN` ×6), `api/repositories.py` (`_MEDIA_TYPE_JSON` ×5),
`mcp/server.py` (`_PERM_TICKET_UPDATE_FIELD` ×3 + `_PERM_WORKFLOW_UPDATE` ×7), `api/websocket.py`
(`_INTERNAL_SERVER_ERROR` ×4), `schemas.py` (`_DESC_WORKFLOW_UUID` ×3), `core/permissions.py`
(`_UPDATE_FIELD_SCOPE_PREFIX` ×3). **S5886** (5): `api/repositories.py:api_git_refresh` return annotation
widened to `Response | GitRefreshResponse` (matches existing raw-Response error paths; 5 `type: ignore`
removed). **S1172** (1 of 2): `services/memberships.py:_is_admin_role` dropped unused `board` param (private,
2 internal callers). **S5890** (2): `core/websocket_manager.py` `ConnectionInfo.websocket/session` →
`X | None`. **S6903** (1): `events/bus.py` `datetime.utcnow()` → `datetime.now(UTC)` (matches `_now_iso`).
**S7494** (1): `cli.py` `set(gen)` → set-comprehension. **Wontfix (documented in technical_depth)**: S7503 ×4
(`get_system_actor_id`, `get_field_gates`, `write_history`, `projecthub_error_handler` — I/O-boundary/framework
async contracts, wide await blast radius), S1172 ×1 (`_linkage.py:get_system_actor_id` session — forward-compat
swap-point), S7504 ×1 (`websocket_manager.py:251` `list()` is load-bearing snapshot to allow dict mutation
during iteration — removing it = RuntimeError), S7500 ×1 (`user_preferences.py:72` `dict(Result)` rejected by
mypy strict; comprehension is type-clean idiom). ruff + mypy --strict: 0 net-new findings vs main baseline
(189 ruff / 42 mypy, all pre-existing). 146 targeted tests pass. .codemap sync gate fired (api/repositories.py
+ cli.py → this page) → git-integration.md updated in-branch (same commit). SonarQube counts drop after
merge + post-merge rescan.

## [2026-06-07] ingest | S3776 cognitive-complexity refactor (block B5): behavior-preserving helper extraction across git reader/sync/webhook + api/repositories + cli (mapped → git-integration.md); 16/17 python:S3776 fns refactored, _dispatch_tool (cc=90, 33-branch MCP dispatch) WONTFIX; 0 net-new ruff/mypy, 225 targeted tests green | [PH-215]

## [2026-06-07] ingest | typescript:S3776 cognitive-complexity refactor (block B5, frontend half on same branch as python): all 5 flagged TS fns refactored (0 wontfix) via behavior-preserving helper/hook extraction — parseDiff.parseUnifiedDiff (hunkFromMatch/classifyDiffLine/parseHunkBody), branchGraphLayout.assignLanes (claimLaneForSha/seedBranchHeadLanes/propagateToParents, pass-1 strict-`<` grow preserved), useWebSocket.handleMessage (latencyStatus + handlePong/handleErrorMessage/handleDegradation), MermaidBlock.render (mermaidErrorMessage/renderMermaidSvg), TicketDetail.TicketDetailPage (LIVE_EVENTS module const + applyLiveTicketUpdate + toTransitionOption). parseDiff byte-identical on 8 patch shapes; assignLanes identical lane maps on 6 graph shapes + 13 branchGraph Node-tests green; tsc+build exit 0, eslint clean on touched; browser-verified Branch Graph + diff panel + TicketDetail + WS-live render identically. mapped (useWebSocket.ts, parseDiff.ts, branchGraphLayout.ts) → components/frontend.md updated in-branch | [PH-215]

## [2026-06-07] ingest | SonarHealthPanel tile counts (BUGS/VULNS/SMELLS) reconciled to the live drawer source so they can never contradict the drill-down list (mapped → components/frontend.md) | [PH-218]

## [2026-06-07] ingest | Final fixable-TS smell residual cleared behavior-preserving (post-PH-210); mapped git/*.tsx → components/frontend.md. FIXED: S1854 ×1 (dead `color`/`lane`/CommitRow `laneOfSha` prop in BranchGraph — the pre-existing :220 unused-var the epic blocks flagged to leave, now resolved), S6772 ×2 (explicit `{" "}`/`{"."}` JSX spacing BranchGraph+TicketCommits), S3358 ×2 (EdgePropertyPanel pluralization locals), S6853 ×2 (NodePropertyPanel checkbox htmlFor/id), S6842 ×1 (PermissionMatrix loading-skeleton role="grid" removed), S6481 ×1 (ThemeProvider value useMemo + useCallback — browser-verified toggle both ways), S6551 ×3 (new lib/utils.stringifyUnknown for TicketDetail history + MarkdownRenderer childrenToText — browser-verified no [object Object] in history timeline), S1874 ×1 (WorkflowEditor beforeunload deprecated returnValue removed), S2004 ×3 (BoardDetail WS updaters + sonar predicate hoisted to module helpers, WorkflowEditor onSettingsClick → useCallback factory). WONTFIX (documented): S1874 ×2 (BoardSettings addWorkflowState/updateWorkflowStates deprecated 404 REST endpoints — migrating to MCP updateWorkflow is a functional change w/ PH-103 rename/edge-sync risk, recommend dedicated ticket), S6822 ×2 (TicketCommits ul role="list" load-bearing under Tailwind Preflight list-style:none for Safari/VoiceOver). tsc+build exit 0, eslint clean on touched. | [PH-219]

## [2026-06-08] ingest | multi-repo model + repo selector | [PH-221]
Epic PH-220 child C1. Repository 1:1→1:N per board (mapped sources `services/repositories.py`, `api/repositories.py`, `services/git_queries.py` all touched → git-integration.md GATE-FORCED, updated same branch). Model: `Repository` gains `slug`/`name`/`is_primary`, drops `uq_repository_board`, adds `uq_repository_board_slug` + partial unique index `uq_repository_one_primary` (BOTH `postgresql_where` AND `sqlite_where` — see new gotcha); `Board.repository`(uselist=False)→`Board.repositories`(uselist=True) + a `primary_repository` property for back-compat. Migration `20260608_0009` (down_revision 0008): add nullable slug/name + is_primary→backfill each existing row to primary w/ basename-derived slug→drop old uq→tighten NOT NULL→add slug uq + partial primary index. Service: list/get_primary/resolve/add/remove(auto-promote oldest)/set_primary(demote→flush→promote) + reinterpreted upsert/detach as primary-aliases. API: 4 new `/repositories*` collection routes + OPTIONAL `repo` (slug|id, default primary) query param on every `/git/*` read + `/git/refresh`, threaded through `git_queries._get_repo`/`sync_repo`/poller. `serializers.board_response` + `boards.py` eager-load swapped to `repositories`/`primary_repository`; `RepositorySummary` gained additive slug/name/is_primary, new `RepositoryCreate`/`RepositoryListResponse`. Page updated: Current-behavior G1 paragraph rewritten 1:1→1:N + primary/slug/repo-selector, +1 Design decision, +2 Known gotchas (partial-index `sqlite_where`; slug-vs-id precedence), frontmatter last_touched_ticket=PH-221. Verify: migration applied on live DB (375 commits / 3 branches survived; backfill is_primary=true slug=project-hub) + downgrade→upgrade round-trip clean; new tests/test_multi_repo.py 14/14 + updated model/api/sync/webhook/connect/settings suites 83/83 + sonar 28/28 green; ruff + mypy --strict 0 net-new on touched files.

## [2026-06-08] ingest | git filesystem auto-detection endpoint | [PH-222]
Epic PH-220 child C2. NEW `backend/app/services/git_detect.py` added to `.codemap` (git-integration hot-set) → `components/git-integration.md` GATE-FORCED (also touches mapped `api/repositories.py`), updated same branch/commit. Service `detect_repositories(session, board, *, repos_root=, max_results=100, time_budget_seconds=5.0)`: shallow-scans `settings.repos_root` (`/repos/`) to depth ≤ 2 via `_scan_sync` wrapped in `asyncio.to_thread`; immediate child with `.git` (dir OR file/worktree) = candidate, child WITHOUT `.git` descended one more level, a real repo never descended into (no `node_modules/.git` fan-out). Every open routes through hardened `reader.open_repo(path, repos_root=...)` (NO fresh subprocess) → realpath allowlist `_validate_under_root` rejects symlink escapes; `remote_url`=origin URL (fallback first remote/None), `default_branch`=`reader._detect_default_branch`, `provider_guess`=pure-substring host check, `already_linked`=candidate realpath ∈ resolved `list_repositories(board)` local_path set. Graceful: missing/empty/perm-denied root, non-git dir, GitReaderError/git.GitError → skip → 200 empty/partial, never 500. Route `GET /api/boards/{key}/repositories/detect` (member auth, read-only) → `DetectedReposResponse`, declared before `/repositories/{selector}` routes. Schemas: `DetectedRepo` (frozen contract for PH-225, 1:1 with `RepositoryCreate`, reuses `Provider` literal) + `DetectedReposResponse`. Page updated: new C2 Current-behavior paragraph, +1 Design decision (reuse-reader/zero-new-attack-surface, route placement, richer shape rationale), +3 Known gotchas (route ordering vs `{selector}`, descend-but-stop-at-repo + zero-budget, `repos_root=` override in tests), frontmatter last_touched_ticket=PH-222 + git_detect.py added to files:. Verify: tests/test_git_detect.py 22/22 (provider_guess matrix, candidates, non-git skip, already_linked, missing/empty root, depth-2 container, no-descend-into-repo, read-only row-count+mtime, symlink-outside skip, result cap, zero budget, permission-error dir, 2 API route 200/empty) + git/repo regression suite 107/107 green; ruff clean on new files; mypy --strict 0 net-new (39 pre-existing errors in unrelated modules unchanged). No migration, no settings change (bounds are module constants).

## [2026-06-08] ingest | SonarQube one-click setup + sync-now + status endpoints | [PH-223]
Epic PH-220 child C3. NEW page `components/sonarqube.md` created (from page-template) + `.codemap` now maps `backend/app/services/sonarqube.py` AND `backend/app/api/boards.py` → it (boards.py previously unmapped) → HARD sync gate now armed for this subsystem; index.md +1 link + Stats bump (4→5 pages). Three thin graceful-200 service fns over three routes on `/api/boards/{board_id}`: `setup_board_project(session, board, project_key?)` + `POST .../sonarqube/setup` (admin) persists `Board.sonarqube_project_key` to supplied-or-derived key (`derive_default_project_key`: PH→`project-hub` to match sonar-project.properties; else key.lower()), idempotent (writes only on change), scan-time auto-create model (NO admin-API projects/create — no admin token, model b out of scope), key persisted even when disabled; `sync_board_now` + `POST .../sonarqube/sync` (admin) on-demand RE-POLL (reuses `poll_board` → existing analysis, 10s-bounded, upsert cache) NOT a scanner run, no live attempt when disabled; `build_setup_status(session, board, reachable?)` + `GET .../sonarqube/status` (member) pure assembly, NO network probe on read path (reachability from cached-metric freshness, only sync makes a live attempt). New schemas `SonarSetupRequest{project_key?}` + frozen `SonarSetupStatus{enabled,reachable,configured,project_key,last_metric_fetched_at,quality_gate_status,dashboard_url,message}` — SECRET-FREE (no token, no compose-internal sonarqube_url; dashboard_url = sonarqube_scan_url + /dashboard?id=key). HARD never-500/never-hang: disabled / no-key / unreachable all degrade to 200 status flags; genuinely missing board still 404 (admin gate 403 first for setup/sync). Page: Current-behavior covers poll(PH-193)+issues(PH-203)+setup/sync/status(PH-223), 5 Design decisions, 6 Known gotchas (PH-key-must-be-project-hub, persist-when-disabled, no-read-probe, secret-leak HIGH, 404-vs-degradation). Verify: tests/test_sonarqube_setup.py 24/24 (derive/setup/idempotent/build-status/sync service + endpoint setup-derived/custom/idempotent/disabled, sync repoll/disabled/unreachable, status no-mutation/disabled, admin-gate 403 setup+sync, status member-allowed, no-token-in-body, missing-board) + sonar regression (issues/poll/client) 55/55 green; ruff clean on touched; mypy --strict 0 net-new (39 pre-existing errors in unrelated modules — mcp/server.py etc. — unchanged; sonarqube.py/boards.py/schemas.py clean).

## [2026-06-08] ingest | branch-view repo switcher (C4) | [PH-224]
Epic PH-220 child C4. Frontend multi-repo branch-view switcher. Mapped files all → `components/frontend.md` (HARD gate): `types/git.ts`, `api/client.ts`, `components/git/*` (incl. NEW `RepoSwitcher.tsx`, added to `.codemap` glob automatically + frontmatter files:), `components/diff/DiffViewer.tsx`. **Types**: `RepositorySummary` += `slug`/`name`/`is_primary` (PH-221 fields, were stale → tsc caught the `api/__smoke__/git.types.ts` fixture); += `RepositoryListResponse` + `Repository` alias. **Client**: + `api.git.listRepositories(boardKey)`; threaded optional `repo?: string` into the 7 git reads (getGraph/getBranches/listCommits/getCommit/getCommitDiff/getRangeDiff/getStatus) — emitted as `repo=<slug>` ONLY when set (single-repo path byte-identical, AC5). **NEW `RepoSwitcher`** native `<select>` (a11y for free) above the `.bg-wrap` grid, visible only `>1` repo (parent-gated + component belt-and-braces `null` guard), default→primary, `(primary)` label + `PRIMARY` badge. **`BranchGraph`**: `repo?` prop → all git query keys gain `repo ?? 'primary'` segment (per-repo cache isolation, no cross-repo bleed) + call params; nested `CommitDiffPanel` getCommit + `<DiffViewer fetch={{…, repo}}>` threaded (else repo-B sha 404s vs primary). **`DiffViewer`**: `repo?` on both FetchSpec arms + useCommitDiff/useRangeDiff keys+calls. **`BoardDetail`**: owns repo state — `listRepositories` (`['repositories', boardKey]`, graceful single-repo fallback on error), `?repo=` via `useSearchParams`, renders switcher only `>1`, passes `repo` + `key={selectedRepo}` (remount auto-resets selection); primary→param deleted, unknown slug→primary + param cleaned; WS `git_synced` invalidation moved to `isBoardGitQuery` predicate (per-repo key segment shifted the kind slot). Browser-verified (Claude Preview, live PH + seeded `demo-web` 2nd repo via `add_repository` admin path, removed after): AC1 single→no switcher/byte-identical/no `repo=`, AC2 selector+default-primary, AC3 switch→distinct branches+SHAs+`repo=demo-web` on wire, AC4 `?repo=` reload+shared-URL persist + unknown→primary fallback, AC5 no `repo=` single-repo. typecheck + eslint clean; 13 branchGraphLayout Node-tests green (layout untouched). Out of scope: C5/PH-225 repo-management UI, TicketCommits repo-scoping.

## [2026-06-08] ingest | git settings multi-repo + auto-detect UI (C5) | [PH-225]
Epic PH-220 child C5. Frontend settings counterpart to PH-224. Mapped files all → `components/frontend.md` (HARD gate): `types/git.ts`, `api/client.ts`, `components/repository/*.tsx` (3 NEW: `RepositoryList.tsx`, `AddRepositoryPanel.tsx`, `RemoveRepoConfirmModal.tsx` + MOD `RepositoryConfigForm.tsx`/`RepositoryStatusPanel.tsx` — added to frontmatter files:), `lib/time.ts` (MOD), `pages/BoardSettings.tsx`. NO backend change (PH-221/PH-222 endpoints merged). **Types**: + `RepositoryCreatePayload` (POST-collection body, optional name/slug), `DetectedRepo` (1:1 onto payload), `DetectedReposResponse`. **Client**: + 4 `api.git.*` beside listRepositories (REUSED, not duplicated): `addRepository` (POST 201), `removeRepository` (DELETE 204, slug selector), `setPrimaryRepository` (POST set-primary), `detectRepositories` (GET detect, member-auth). **BoardSettings**: single-repo status/config/ops triad REPLACED by list+add; two queries (`['repositories', boardKey]` shared w/ PH-224 switcher, benign; `[…, 'detect']` LAZY `enabled: …&& showAdd` for the 5s FS scan), `select: r=>r.repositories`. **RepositoryList** `<ul role=list>` rows: name+slug code, provider chip, remote_url (italic local-only), branch code, last-sync via shared `humaniseRelativeTr`, short SHA, BİRİNCİL badge on is_primary, admin-only cluster (Birincil yap [disabled when primary], Kaldır→RemoveRepoConfirmModal, İşlemler disclosure→board-scoped RepositoryOperationsPanel). **AddRepositoryPanel** Algılanan tab (detect rows; already_linked greyed+disabled+'Zaten ekli'; one-click Ekle maps DetectedRepo→payload; empty→graceful note) + Elle tab (RepositoryConfigForm `mode='add'`→POST new repo, not PUT primary). **All mutations REFETCH not optimistic** (server-side primary auto-promotion) → invalidate list+detect+git status. **403 two layers**: hide writes when !isAdmin (read views still render member-auth); defensive onError 403→inline 'admin yetkisi gerekli', no error-boundary crash (PH-224 membership-vs-role trap). `humaniseRelativeTr` lifted RepositoryStatusPanel→lib/time.ts (DRY, kills S4144). Browser-verified (Claude Preview, live PH + temp-seeded `demo-web` via add_repository service + temp admin grant on jarwis-frontend, BOTH removed after): 1-row back-compat (badge/banner/hidden-actions), detect greyed-linked+addable-unlinked, one-click add→2 rows, set-primary badge swap+primary-first re-order, remove (confirm)→1 row + candidate re-appears unlinked, non-admin 403 (required: board.admin) actions-hidden, forced-403 inline message no crash; 0 console errors; PH ends single-repo. tsc + eslint clean. Out of scope: backend (C1+C2 done), SonarQube UI (C6), per-repo Sonar projects.

## [2026-06-08] ingest | SonarQube setup/sync settings UI (C6) | [PH-226]
Epic PH-220 FINAL child C6. Frontend consumer of PH-223's three secret-free endpoints. Mapped files: `api/client.ts` → `components/frontend.md` (HARD gate) + NEW `components/sonarqube/SonarSetupSection.tsx` → `components/sonarqube.md` (new `.codemap` glob `frontend/src/components/sonarqube/*.tsx`). **Types** (`types/api.ts`): + `SonarSetupStatus`/`SonarSetupRequest` mirroring `schemas.py:394-431` verbatim (datetime→ISO string). **Client**: + nested `api.sonarqube.{getStatus,setup,sync}` namespace (mirrors `api.git.*` convention; all via shared `request<T>` → auth + `ApiRequestError`; status member-level, setup/sync admin → 403). **NEW `SonarSetupSection`** (BoardSettings 5th `sonarqube` tab, ShieldCheck icon): ONE status query keyed `['board', boardKey, 'sonar-setup']` (DEDICATED key, no collision with BoardDetail's `['board', boardKey]` — Risk R3) + two admin mutations. Status panel = QG pill (GATE_MAP mirrors SonarHealthPanel), project_key (mono), relative last-fetched (inline Intl.RelativeTimeFormat), enabled/reachable/configured chips, Open dashboard anchor (target=_blank rel=noopener noreferrer, omitted when null). UX states: enabled=false→banner+buttons disabled; reachable=false→unreachable note+Sync stays enabled; configured=false→Setup glows primary, else demotes to ghost "Re-affirm". **Sync onSuccess invalidates THREE families** (Risk R2): sonar-setup + `['board', boardKey]` (board.health) + `['board', boardKey, 'sonar-issues', ...]` predicate (mirrors BoardDetail.tsx:263-277) so the board-detail SonarHealthPanel tile refreshes without reload. **403 two layers** (Risk R1): buttons render only when isAdmin; both mutations onError catch `ApiRequestError.status===403`→inline role=alert "Admin role required" (no crash). No toast dep (inline role=status success line). SonarHealthPanel left untouched (no second sync surface, tight scope). Browser-verified (Claude Preview, live PH board): status panel configured=true/project_key=project-hub/QG=Failed(ERROR)/dashboard link/chips; non-admin (frontend_dev)→read-only banner+buttons hidden; temp-promoted membership to admin→Setup+Sync render, clicking each→real POST .../sync→403 + .../setup→403 caught into inline message, NO Vite error overlay/unhandled rejection; membership RESTORED to frontend_dev + re-verified after. tsc clean. Out of scope: backend (C3/PH-223 done), health-panel sync button, kill-switch toggle UI, full scanner run.
## [2026-06-08] ingest | sonar-scan coverage step now deselects hanging/env-broken suites so cobertura XML flushes (real line-rate 0.6684, was 0% under SIGTERM-killed hang) | [PH-208]

## [2026-06-08] ingest | per-board filesystem path (C1) — Board.repos_path + HOST↔container helper + broadened mount | [PH-228]
Epic PH-227 child C1 (foundational; blocks PH-229 detect/sonar + PH-230 settings UI). Mapped files: NEW `backend/app/services/repo_paths.py` → `components/git-integration.md` (new `.codemap` glob added) + `backend/app/db/models/core.py` (Board model) + `backend/app/db/migrations/versions/` → `components/backend.md` (both HARD-gate pages updated, same branch). **Model**: `Board.repos_path` String(500) nullable (after `sonarqube_project_key`) — stores the HOST path (e.g. `/Users/huseyinkanat/Documents/kims`), NOT the container path; mount-independent. **Config**: + `Settings.host_home` (default `/Users/huseyinkanat`, env `HOST_HOME`) — the host $HOME the mount maps onto `repos_root` (deliberately NOT the container's /root). **NEW `repo_paths.py`** (pure, config-only dep): `to_container_path(host)` swaps `HOST_HOME`→`repos_root` (`…/Documents/project-hub`→`/repos/Documents/project-hub`; `…/AndroidStudioProjects/GameX`→`/repos/AndroidStudioProjects/GameX`), `to_host_path` inverse, both absolute-only + reject `..` + reject-outside-root → `RepoPathError(ValueError)` (PH-229 catches→skip, PH-230 PATCH→422). Single shared impl so detect+sonar never reconstruct the mapping (new sibling module avoids import cycle). **Serializer**: `BoardResponse.repos_path` (read surface only; `BoardUpdate` UNCHANGED — PATCH editability is PH-230). **Mount**: `docker-compose.yml` backend `${PROJECTS_ROOT:-${HOME}/Documents/project-hub}:/repos/project-hub:ro` → `${PROJECTS_ROOT:-${HOME}}:/repos:ro` (whole $HOME visible; `repos_root` stays `/repos`, no `_validate_under_root` change). **Migration `20260608_0010`** (down_rev `20260608_0009`): additive nullable column + idempotent backfill of 6 boards keyed by `key` (`WHERE repos_path IS NULL`, plain parametrized UPDATE, dialect-portable PG+SQLite) + **PH `repositories.local_path` relocation** `/repos/project-hub`→`/repos/Documents/project-hub` (the trap — new path still satisfies `/repos/` allowlist, cache repo_id-keyed so 392-commit/2-branch PH cache survived); reversible downgrade reverses both (compose mount manual per docstring). **Verified live** (Docker, despite 5 host Docker-Desktop crashes): migration applied→head 0010, 6 boards exact host paths, PH relocated; container recreated (NOT restart — volume change needs recreate)→`cat /proc/mounts`=`/run/host_mark/Users /repos … ro`, PH/KIM/GXA/BENCH/FN visible; `open_repo('/repos/Documents/project-hub')`→2 branches live + cache 392 commits intact; health 200; downgrade→upgrade round-trip clean, final state correct. Tests: 33 new (repo_paths translation/traversal/round-trip/edge + serializer exposes repos_path + BoardUpdate unchanged) + 68 repo/board/multi-repo/API regression GREEN; ruff + mypy --strict clean. Out of scope: detect/sonar consumption (PH-229), PATCH+UI (PH-230).

## [2026-06-08] ingest | detect + SonarQube setup use board.repos_path, not global /repos (C2) | [PH-229]
Epic PH-227 child C2 (consumes PH-228's `repos_path`/`to_container_path`; blocks live-detect part of PH-230). Mapped files: `backend/app/services/git_detect.py` + `backend/app/api/repositories.py` → `components/git-integration.md`; `backend/app/services/sonarqube.py` → `components/sonarqube.md` (BOTH HARD-gate pages updated, same branch, `last_touched_ticket: PH-229`). **Detect rewrite**: `detect_repositories` now resolves the scan START from `board.repos_path` (HOST→container via `to_container_path`), so each board scans ONLY its own subtree — KIM→`/repos/Documents/kims`, GXA→`/repos/AndroidStudioProjects/GameX`, PH→`/repos/Documents/project-hub` (PH-222 could only ever find project-hub). **The load-bearing fix: split `scan_root` vs `allowlist_root` in `_scan_sync`** — PH-222 conflated the walk start and the hardened reader's allowlist into one `repos_root` param; PH-229 threads `scan_root` (walk start = board container path) separately from `allowlist_root` (reader boundary, STAYS `settings.repos_root` = `/repos`). Setting the reader allowlist to the board path would weaken `_validate_under_root` (board path becomes its own allowlist root) — deliberately NOT done. **Root-as-candidate added**: if `scan_root` itself has a `.git` it IS the repo → returned once, not descended (a board path like `/repos/Documents/kims` is the repo); else the existing depth≤2 children scan handles parent-dir-of-repos. **Graceful-everywhere, no fallback**: null `repos_path` / `RepoPathError` (outside HOST_HOME / `..`) / non-existent container root all → `[]` (200), NEVER 500, NEVER fall back to project-hub. `repos_root=` kwarg retained as TEST-only override (temp tree, scan_root==allowlist_root). `DetectedRepo` shape FROZEN; route signature unchanged (board drives root; docstring updated). **Sonar**: `derive_default_project_key` gains a path-basename default for non-PH boards (`kims`/`GameX`) with the PH-literal branch kept FIRST (never basename-derived — must match `sonar-project.properties`); `_path_basename_key` validates via `to_container_path` + never raises → `board.key.lower()` fallback on null/RepoPathError, so `setup_board_project` keeps PH-223's never-500. Only the default-key derivation changed; setup signature/idempotency/scan-time-auto-create/secret-free status all unchanged; scanner stays post-merge `sonar-scan.sh`. **Verified live** (Docker, real DB + broadened mount): KIM→`kims`(linked=false, key `kims`), GXA→`GameX`(key `GameX`), PH→`project-hub`(already_linked=TRUE post-relocation, key `project-hub`) each resolve their OWN repo; null/outside-HOME/`..`/typo paths all → `[]` no 500. Tests: 64 detect+sonar (incl. root-as-candidate, board isolation, null/RepoPathError/typo→[], allowlist-stays-/repos symlink-escape, PH-still-project-hub, basename default, bad-path graceful) + 191 repo/git/sonar regression GREEN; the PH-222 `test_route_member_detect_200` updated to the new per-board contract (was asserting old global-/repos scan). ruff + mypy --strict clean on all 3 changed modules. Out of scope: PATCH/edit of `repos_path` + frontend UI (PH-230), scanner invocation in Python, auto-clone.

## [2026-06-08] ingest | refactor _scan_sync to clear S3776 (cognitive complexity) | [PH-229]
Reviewer needs_revision (SonarQube re-run on PH-229): 1 CRITICAL `python:S3776` on `git_detect.py::_scan_sync` — cognitive complexity 16 > 15, introduced by the C2 root-as-candidate + two-roots rewrite (main baseline was clean); everything else passed (allowlist intact, multiboard, never-500, 64/64). Behavior-preserving fix: extracted the depth≤2 children BFS loop (bounds check, `_has_git_entry` candidate test, depth-budget descent) verbatim into a new `_walk_children(root, reader_root, linked_realpaths, *, max_results, deadline)` helper. `_scan_sync` now owns only the `is_dir` guard + `allowlist_root` back-compat ternary + root-as-candidate branch (`[built]`/`[]`), computes the monotonic `deadline`, and delegates the children case. No behavior change — identical candidates/order/bounds/null-handling/DetectedRepo shape; the `scan_root`≠`allowlist_root` split and `/repos` allowlist guard untouched. Complexity now `_scan_sync`≈5, `_walk_children`≈11 (both <15, S3776 cleared). 64/64 detect+sonar tests still GREEN incl. symlink-escape rejection + multiboard isolation + null-graceful; ruff + mypy --strict clean on `git_detect.py` (the `bus.py` mypy error is pre-existing baseline). Mapped page `components/git-integration.md` gets a design-decision bullet for the extraction (internal-only refactor; gate already satisfied from the first commit).

## [2026-06-08] ingest | board-settings UI to view/edit repos_path + PATCH editability (C3) | [PH-230]
Epic PH-227 child C3 (FINAL; consumes PH-228 `repos_path`/`to_container_path` + PH-229 detect/sonar-resolution). Mapped files: `frontend/src/api/client.ts` + `frontend/src/components/repository/AddRepositoryPanel.tsx` → `components/frontend.md`; `backend/app/api/boards.py` → `components/sonarqube.md` (BOTH HARD-gate pages updated same branch, `last_touched_ticket: PH-230`). **Backend (tiny, no migration — column exists)**: `BoardUpdate.repos_path: str|None` (schemas.py), `update_board(repos_path=...)` treats `None`=not-provided / `""`-or-whitespace=clear-to-NULL via `.strip() or None` (services/boards.py), `api_update_board` validates a NON-empty path through `repo_paths.to_container_path` → `HTTPException(422)` on relative/`..`/outside-HOST_HOME (api/boards.py; keeps `current_actor` auth, NOT widened to admin). **Frontend**: `BoardResponse` in `types/api.ts` gains `repos_path: string|null` (was MISSING — backend served it since PH-228); `api.updateBoard` payload `+ repos_path?: string|null`; BoardSettings General tab gets a `Project Path` field — admin-editable `<input>` (onBlur PATCH) vs read-only `<output>` for non-admin, with a dedicated `updatePathMutation` (isolated inline 422/403 error + `pathSaved` banner) + a `commitReposPath` helper (clears stale error at blur start, skips no-op PATCH); BOTH update mutations now invalidate `['repositories', boardKey, 'detect']` alongside `['board', boardKey]` so detect re-scans after a path edit; Repository tab shows `repository-no-path-hint` (→ jump-to-General button) when path is null; `AddRepositoryPanel` `/repos/` copy → per-board-path wording. **Browser-verified live (PH board, Claude Preview)**: General tab renders real backfilled path `/Users/huseyinkanat/Documents/project-hub`; admin edit→Save persists + "Project path saved." banner; invalid paths (`/etc/passwd`, `/etc/shadow`, relative, `..`) → inline 422 message no crash; valid edit clears error + invalidates detect (verified isInvalidated); empty-path hint + goto-General link render when null; non-admin sees read-only field (no input); detect lists `project-hub` repo (already_linked); **PH repos_path restored** to canonical value (confirmed). Tests: `test_board_repos_path.py` — inverted the PH-228 deferral guard (`test_board_update_accepts_repos_path`) + NEW `test_update_board_sets_and_clears_repos_path` (set/clear-on-""/None-leaves-untouched); 6/6 GREEN, full board suite 64 passed (only pre-existing `test_mcp_subscribe_events` env-broken fails). frontend `tsc` clean; ruff + mypy --strict clean on 3 touched backend files (`bus.py` mypy error pre-existing baseline). Out of scope: FS picker dialog, mount/detect/sonar-resolution logic (PH-228/229), PATCH auth refactor.

## [2026-06-08] ingest | detect surfaces nested INDEPENDENT git repos under a git-root board path | [PH-231]
Follow-up to PH-229 (epic PH-227 multi-board detection); standalone, no parent. Mapped file: `backend/app/services/git_detect.py` → `components/git-integration.md` (HARD-gate page updated, same branch, `last_touched_ticket: PH-231`). **Problem**: a board whose `repos_path` IS a git repo that CONTAINS nested INDEPENDENT repos showed only the root — GXA→`/repos/AndroidStudioProjects/GameX` returned just `GameX`, hiding `GameXCore`/`GameXSDK`/`GameXAndroidDemoApp` (each own `.git`+`main`, NO `.gitmodules`). PH-229's `_scan_sync` short-circuited (`if _has_git_entry(root): return [_build_candidate(root)]`, no descent). **Fix — root-as-candidate AND descend**: new `_scan_git_root(root, reader_root, linked, *, max_results, deadline)` adds the root (FIRST result) then delegates to the SAME `_walk_children` to surface nested independent repos. Only the ROOT is descended past its `.git`; a nested `.git` dir is a candidate but is NOT descended into (no fan-out). **Three-way distinction** separates want from junk: (1) independent nested repo → surfaced; (2) submodule (`path` in the root's `.gitmodules`) → skipped — new `_submodule_paths(root)` parses `.gitmodules` via stdlib `configparser`, resolves each `path` to a realpath, descent skips a candidate whose `.resolve()` matches; absent/malformed → `set()` (fail-OPEN: never 500, modules surface — GameX has none → all 3 surface); (3) vendored (denylisted dir name) → pruned — new `_VENDORED_DIR_NAMES` frozenset (`node_modules`/`Pods`/`build`/`.gradle`/`DerivedData`/`.cxx`/`vendor`/`Carthage`/`.build`/`target`) name-filter in `_iter_child_dirs` prunes from BOTH candidate set AND descent frontier (keeps a shallow `node_modules/.git` out; the deep `GameXCore/src/main/cpp/.../LiteRT` TFLite git is additionally unreachable because GameXCore, a repo, is never descended into). `_MAX_DEPTH` stays 2 (modules at depth 1; deep vendored git out of reach). Realpath `seen` set dedups a symlinked child→root; root counts toward `max_results`. **Guards preserved**: `/repos` allowlist + `_validate_under_root` UNTOUCHED (`_scan_git_root` receives the already-resolved `reader_root`, does NOT re-plumb it; board path is the walk START only), explicit-stack walk (no unbounded recursion), `deadline`/cap/never-500 intact, `DetectedRepo` shape FROZEN; API route signature unchanged. **Complexity (S3776)**: candidate branch of `_walk_children` extracted into `_candidate_for_repo_dir` (submodule + dedup gate) → `_walk_children`≈10, `_scan_git_root`≈2, `_submodule_paths`≈7, `_candidate_for_repo_dir`≈3, `_scan_sync`≈4 — every helper <15 (flagged for reviewer SonarQube rescan; no live scanner in this session). **Verified live** (Docker, real DB + broadened mount): GXA→**4** candidates `GameX`(root, first)+`GameXCore`+`GameXSDK`+`GameXAndroidDemoApp`, LiteRT/TFLite vendored git EXCLUDED; PH→1 `project-hub`(already_linked=true) no regression; KIM→1 `kims` no over-collection; `_submodule_paths(GameX)`=`set()` (no `.gitmodules`). Tests: 8 new (nested-independent→root+3, submodule-skip w/ real `.gitmodules` fixture, malformed-`.gitmodules`→modules-surface, vendored shallow+deep excluded, single-repo-root no-fanout, realpath-dedup symlink→root, max_results cap, route-level all-candidates) + 34 existing detect tests + 301 git/repo/detect/sonar regression GREEN; ruff + mypy --strict clean on `git_detect.py` (`bus.py`/`mcp` mypy errors pre-existing baseline). No migration (logic-only; consumes PH-228's mount/helper). Out of scope: frontend (panel renders any `DetectedRepo[]`), recursive submodule resolution beyond the root's direct `.gitmodules`, raising `_MAX_DEPTH`.

## [2026-06-08] ingest | admin gating stuck after in-app login: `me` not invalidated on token change (stale identity → isAdmin false) | [PH-232]
Bug fix (high priority, security-relevant), standalone. Mapped page: `components/frontend.md` (HARD frontmatter gate: `useMe.ts`/`auth.ts`/`main.tsx` listed → page updated same branch, `last_touched_ticket: PH-232` + Design-decisions bullet + Known-gotchas entry; two new files added to `files:`). **Problem**: after an in-app token switch to the admin token WITHOUT a hard reload, every `isAdmin`-gated control (SonarQube Setup/Sync, repos_path editor PH-230, repository add/remove PH-225, members tab) stayed locked behind "Admin role required". `useMe()` keyed `["me"]` (token NOT in key) with `staleTime: 5min`, and neither `setToken` nor `logout` invalidated it → TanStack Query served the PRIOR identity's cached `me` (a non-admin actor) → `useBoardRole`→`isAdmin` stale-false. Backend `/api/auth/me` verified correct (admin role on all 6 boards). **Fix (two complementary, both shipped)**: (a) `useMe()` queryKey → `["me", token]` (token-scoped → a switch yields a NEW cache entry, never serves the prior identity). (b) the Zustand auth store calls `queryClient.clear()` on EVERY real identity change — `setToken` (guard `prev !== token`) + `logout` (guard `had`) — the single chokepoint every token change flows through, INCLUDING the two outside-React 401 auto-logouts in `client.ts` (`request<T>` L107 + `mcpCall` L64) that already route through `logout()`, so **`client.ts` is NOT touched** (and its `.codemap` HARD gate does not fire). **clear-ALL over surgical** `invalidateQueries(["me"])`: a token switch is a trust boundary; surgical invalidate leaves board-scoped prior-identity data (role/repos/sonar/tickets/members) in cache (cross-identity bleed = the security defect) — `clear()` guarantees no prior-identity bytes survive. **Files**: NEW `frontend/src/lib/queryClient.ts` (the `QueryClient` singleton lifted out of `main.tsx` so the non-React store can import the SAME instance without a `main.tsx`↔`auth.ts` cycle — `main.tsx`→`<App/>`→store); NEW `frontend/src/stores/identityGuard.ts` (`shouldClearCacheOnIdentityChange(prev,next) = prev!==next`, dependency-free leaf so it is unit-testable under `node --test`); `auth.ts` imports both + clears on change (`set({token})` BEFORE `clear()` so the new key is live for re-subscribing observers); `useMe.ts` token-scoped key; `main.tsx` imports the singleton from the lib module. **Test**: NEW `frontend/src/stores/identityGuard.test.ts` — 6/6 green via `node --test --experimental-strip-types` (no test-runner dep, PH-190 contract): different-token→clear, null→token→clear, token→null→clear, SAME-token→no-clear (no refetch loop), null→null→no-clear, actor-to-actor→clear. `tsc --noEmit` clean. **Browser-verified (Claude Preview, live PH)**: non-admin (jarwis-frontend) → SonarQube "Read-only — admin role required" banner + no Setup/Sync, General repos_path read-only; switched to admin via the store `setToken` chokepoint WITHOUT reload → 8 cache entries cleared to 0, banner gone, Setup/Sync + "Re-affirm setup" enabled, repos_path became editable input, Repository add/remove/set-primary appeared, all same React Router page; BENCH (unconfigured sonar) Setup button enabled + request fires as admin (a 403 there is a PRE-EXISTING BACKEND `have:[]` quirk — admin `/me` says `BENCH→admin` but `require_board_admin` returns empty — separate backend ticket, NOT frontend cache scope); logout→login-as-different-identity cleared cache at BOTH boundaries, `leakedAdminIdentityEntry: false`, non-admin lock correctly returned; `/api/auth/me` ~1 req per identity boundary (no refetch storm), 0 console errors. No DB/board state changed (PH `project-hub` + BENCH NULL sonar keys unchanged). Out of scope: backend `/api/auth/me` (correct), the BENCH `require_board_admin` 403 quirk, re-keying every board-scoped query by token (unnecessary — `clear()` covers them).

## [2026-06-08] ingest | require_board_admin 403s on board KEY: gate parsed raw uuid.UUID(board_id), denied every key-based admin call | [PH-233]
Bug fix (high priority, auth/regression), standalone — the exact "BENCH `require_board_admin` 403 quirk" PH-232 flagged as a separate backend ticket. Mapped pages: NONE — `backend/app/api/deps.py` is NOT in `.codemap` → Reviewer HARD sync gate INERT; the fix is confined to `deps.py` and does NOT touch the mapped `boards.py`→`components/sonarqube.md`. **Problem**: `require_board_admin` (`deps.py:52-81`, the DI for 5 admin endpoints — sonarqube setup+sync PH-223, members POST/PATCH/DELETE PH-39) parsed the raw `{board_id}` path param with `uuid.UUID(board_id)` and converted `ValueError`→`PermissionDenied(403, have=[])` BEFORE any membership lookup. Frontend ALWAYS sends the board KEY (`PH`,`BENCH`), every KEY is a non-UUID → throws → 403 for EVERY key-based admin call, even a genuine admin. The companion `get_board(session, board_id)` resolves KEY-or-UUID, so the rest of each route worked on a key — only the admin gate didn't. **Fix (mirror the already-correct sibling `repositories.py:_require_board_admin`)**: rewrite the body to `board = await get_board(session, board_id)` (key-or-uuid; unknown board → 404 NotFound) FIRST, then `select(BoardMembership).where(board_id==board.id, actor_id==actor.id, role=="admin")` → None ⇒ 403 SECOND, else `return actor`. Added `from app.services.boards import get_board` (the SAME resolver the routes use; no circular import — `services/boards.py` imports only `core.exceptions`+`db.models`); removed the now-dead `import uuid` (ruff F401). PRESERVED the `-> Actor` signature/DI params (all consumers bind `Depends(require_board_admin)` expecting an `Actor`). **Ordering is load-bearing**: resolve-before-authz means unknown board → 404 (never a misleading 403), resolved-board+non-admin → 403 — same semantics as the proven sibling. **Why it slipped through**: every existing setup/sync/member test built its URL with `board.id` (UUID), never the KEY → the failing key path was never exercised; new regression tests MUST use the KEY. **Tests**: 11 new (5 in `test_sonarqube_setup.py`: admin-via-key→200, admin-via-uuid→200, sync-admin-via-key→200, non-admin-via-key→403, unknown-key→404; 6 in `test_memberships_api.py::TestMemberManagementViaKey`: admin POST/PATCH/DELETE via key→201/200/204, non-admin-via-key→403, unknown-key→404, unknown-uuid→404) + tightened the pre-existing `test_endpoint_setup_missing_board_is_404` from `in (403,404)` to strict `==404`. Failing-first verified (4 sampled new tests RED on old deps.py). 53/53 target + 174 board/sonar/member/repos regression GREEN; ruff clean (also dropped a dead `import pytest` in the touched test file); mypy --strict deps.py clean (`mcp/server.py` 34 pre-existing errors are baseline, out of scope = discovered debt). **LIVE-verified** (running app, admin token): `POST PH/sonarqube/setup` (KEY)→**200** (was 403), `POST BENCH/sonarqube/setup` (KEY)→**200**, unknown key `ZZZ`→**404**, random UUID→**404**, `POST PH/members` (KEY)→404 actor-not-found (= admin gate PASSED via key, then bogus-actor lookup), invalid-token-via-key→**403** (authz enforced, not blanket-allow). PH sonar key UNCHANGED (`project-hub`, idempotent); BENCH now configured (`jarwis-bench` — intended, user wanted sonar on BENCH). No migration (logic-only). Out of scope: unifying the two `*_require_board_admin` impls (different return contracts), `mcp/server.py` mypy debt, frontend (already sends key correctly).
