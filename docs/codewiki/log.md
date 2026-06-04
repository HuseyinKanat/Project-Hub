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
