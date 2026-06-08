---
type: component
files: [backend/app/services/sonarqube.py, backend/app/api/boards.py, frontend/src/components/sonarqube/SonarSetupSection.tsx, scripts/sonar-scan-board.sh]
last_touched_ticket: PH-236
related: [[components/backend]], [[components/frontend]]
status: active
---

# SonarQube Integration (board health)

> Self-hosted SonarQube Community ingestion: a poll cron caches each board's quality
> snapshot, an issues proxy lists live issues, and one-click setup/sync/status
> endpoints link a board to its project — all best-effort, never-500, secret-free.

## Current behavior

`backend/app/services/sonarqube.py` is the single integration surface for a
self-hosted **SonarQube Community Build**. Everything is gated by the
`settings.sonarqube_enabled` kill switch (default `False` → the stack boots with no
sonar dependency) and bounded by a `_TIMEOUT=10s` httpx timeout. Error isolation is
layered: the httpx clients return `None` / `status="unreachable"` on ANY error
(down / 401 / malformed JSON / project not yet scanned), and every consumer degrades
to a status flag rather than raising.

**Project key resolution** (`resolve_project_key`) is the shared contract: it returns
`Board.sonarqube_project_key` first, then a key from the `sonarqube_project_key_map`
JSON setting, else `None` (board skipped — not an error). All consumers (poll cron,
issues proxy, scanner, setup/sync) agree on this one key.

**Poll cron** (PH-193, `sonarqube_poll_cron` → `poll_board`): the FastAPI lifespan
creates the task only when `sonarqube_enabled and interval > 0`. Each tick resolves
the key, fetches the quality-gate status + measures, upserts the single
`SonarQubeMetric` row per board (`uq_sonarqube_metric_board`), and publishes a
`sonarqube_synced` event on `board:{id}` for the frontend to live-patch.

**Issues proxy** (PH-203, `fetch_issues` + `GET .../sonarqube/issues`): proxies
`/api/issues/search`, strips the `<projectKey>:` component prefix to a relative file
path, and returns a graceful-200 `SonarIssuesResponse` (`status` ∈ ok / unreachable /
not_configured / no_project_key). `dashboard_url` is HOST-facing
(`sonarqube_scan_url` + `/project/issues?id=key`), never the compose-internal
`sonarqube_url`, never the token.

**Setup / sync / status** (PH-223, three thin service fns over three routes on
`/api/boards/{board_id}`, all graceful-200):
- `setup_board_project(session, board, project_key?)` + `POST .../sonarqube/setup`
  (admin) — persists `Board.sonarqube_project_key` to the supplied key or the derived
  default (`derive_default_project_key`). **Default-key precedence (PH-229):**
  (1) **PH literal FIRST** → `project-hub` (must match `sonar-project.properties`; never
  basename-derived even though the basename happens to coincide); (2) a NON-PH board WITH a
  resolvable `repos_path` → the **path basename** (`/Users/.../kims` → `kims`,
  `.../GameX` → `GameX`) — the natural scanner project identity, the "uses the board path"
  requirement; (3) fallback → `board.key.lower()` for a null path, an empty basename, or a
  `RepoPathError` (path outside `HOST_HOME` / `..`). `_path_basename_key` validates via
  `to_container_path` then takes the HOST basename and never raises (so a bad path degrades,
  no 500). **Idempotent** — only writes when the value changes. Provisioning = **scan-time
  auto-create**: persisting the key is enough (the post-merge `sonar-scanner` auto-creates
  the Community project on first analysis); NO admin-API `projects/create` call (no admin
  token provisioned — out of scope); the scanner working dir itself stays out of this Python
  module. The key is persisted even when sonar is disabled (config allowed offline).
- `sync_board_now(session, board)` + `POST .../sonarqube/sync` (admin) — an on-demand
  **re-poll** (reuses `poll_board` → reads the *existing* analysis, fast, 10s-bounded),
  upserts the metric cache, returns the fresh status. It does NOT trigger a scanner run
  (scans stay post-merge in `sonar-scan.sh`). When disabled it makes NO live attempt.
- `build_setup_status(session, board, reachable?)` + `GET .../sonarqube/status`
  (member) — assembles the `SonarSetupStatus` from settings + the cached
  `SonarQubeMetric` with **no network call** (a read must never hang). **PH-235 —
  HONEST status classification.** It no longer derives `reachable` from metric
  presence (that overload made a configured-but-never-scanned board render a FALSE
  "unreachable"). Instead it computes `has_analysis = metric is not None` and an
  explicit `status` discriminator ∈ `disabled | unconfigured | no_analysis | ok |
  unreachable`: `not enabled → disabled`; `not configured → unconfigured`;
  `reachable is False` (ONLY a real failed live `sync`) `→ unreachable`; the pure
  read path (`reachable is None`) or a succeeded sync `→ ok if has_analysis else
  no_analysis`. Absence of a metric on the read path becomes **`no_analysis`, never
  a false `unreachable`** — `reachable=False` is reachable ONLY via `sync` passing a
  genuinely failed live attempt (a real outage is never masked). `reachable` is kept
  for backward compat (best-effort = `has_analysis` on the read path) but the UI
  keys its messaging off `status`. `_setup_status_message` is driven off `status`:
  `no_analysis` → "linked to <key> — no analysis yet (run a scan)" (NOT unreachable).

**Per-board scan — "Scan now" (PH-236, C2). `scan` ≠ `sync`.** `sync_board_now`
re-polls a board's EXISTING analysis; `request_board_scan` plans a NEW analysis run.
The backend runs INSIDE a container and CANNOT `docker compose run`, so the scan
endpoint NEVER launches the scanner — it is a cheap, NON-blocking, never-500 PLANNER:
- `detect_board_language(container_source)` — infers the primary language from the
  board's code tree at the translated `/repos/<rel>` path (the scanner sees the SAME
  tree). A **Unity layout** (`Assets/` + (`ProjectSettings/` | `Packages/`)) →
  `csharp` (a marker shortcut that wins before extension counting, since Unity
  `.csproj`/`.sln` are generated, not in VCS); otherwise a bounded `os.walk`
  (skipping `build`/`.gradle`/`node_modules`/`Library`/`Temp`/… and capped at
  `_DETECT_MAX_FILES=4000`) tallies recognized source extensions and returns the most
  common language. Missing/empty/unreadable → `None`. NEVER raises.
- `_language_supported(language)` — SonarQube **Community Edition** support gate.
  KNOWN-unsupported (`csharp`) → False; everything else (incl. unknown `None`) → True
  (optimistic — let the scanner's own sensors decide; only languages we KNOW CE can't
  do are gated to `unsupported`).
- `build_scan_plan(board) -> SonarScanPlan` — the single shared resolution (project_key
  + `to_container_path` + language + supported + `sonar.exclusions`). Pure (settings +
  filesystem only, no network), never-500. DRY between `scan` and `scan-plan`.
- `request_board_scan(session, board) -> SonarScanResult` + `POST .../sonarqube/scan`
  (admin) — returns a `scan_status` enum in order: no key → `unconfigured`; disabled →
  `disabled`; no/invalid `repos_path` (RepoPathError) → `error`; **unsupported language
  (C#) → `unsupported`** (honest, NOT a fake `queued`); else → `queued` (intent
  recorded; the message names the host command). NEVER blocks, NEVER 500. v1 transport
  is the scan-plan HTTP read (no shared-volume file coupling — sidesteps container↔host
  uid issues); `session` is accepted for future manifest persistence.
- `GET .../sonarqube/scan-plan` (admin) → the **FROZEN** JSON the host runner + frontend
  C3 (PH-237) consume: `{ project_key, container_source, host_source, language,
  supported, reason, exclusions }`. The seam that keeps the host script dumb — the
  backend owns ALL key/path/language resolution in one place.
- **Host runner** `scripts/sonar-scan-board.sh <board-key>` (sibling of
  `sonar-scan.sh`, which is UNCHANGED) — curls `scan-plan`; if `supported` runs
  `docker compose --profile scan run --rm sonar-scanner` with per-board `-D` props
  (`-Dsonar.projectKey=<key> -Dsonar.projectName=<key> -Dsonar.sources=/repos/<path>
  -Dsonar.exclusions=… -Dsonar.scm.disabled=true`); if `supported=false` (C#) it logs
  the reason + exits 0 (no scan). Keeps the PH-194/PH-208 contract: ALWAYS exit 0,
  token from `.env` only (never committed/echoed). On first analysis SonarQube
  **auto-creates** the project (e.g. `GameX`), then the poll cron / `sync` ingests the
  fresh measures. The `sonar-scanner` compose service gained
  `${PROJECTS_ROOT:-${HOME}}:/repos:ro` (mirroring the PH-228 backend mount) so it sees
  board code at the same `/repos/<rel>` path; `/usr/src` (project-hub self-scan) is
  kept. `SonarScanResult` / `SonarScanPlanResponse` are SECRET-FREE (no token, no
  compose-internal `sonarqube_url`).

`SonarSetupStatus` (PH-235 additive) is SECRET-FREE:
`{ status, has_analysis, enabled, reachable, configured, project_key,
last_metric_fetched_at, quality_gate_status, dashboard_url, message }` — never the
token, never the compose-internal URL; `dashboard_url` = `sonarqube_scan_url` +
`/dashboard?id=key`. `status` + `has_analysis` are additive (PH-235); every prior
field is unchanged (backward-compatible). `BoardResponse` also carries
`sonarqube_project_key` (PH-235) so the board-header panel can tell "no key" from
"key set, no analysis yet" without a separate status call.

**Frontend consumer** (PH-226 / C6, `frontend/src/components/sonarqube/SonarSetupSection.tsx`,
rendered as the `sonarqube` tab in `BoardSettings.tsx`): a single member-level status
query keyed `['board', boardKey, 'sonar-setup']` (DEDICATED key — not `['board', boardKey]`
— so it never collides with BoardDetail's `BoardResponse` cache) drives a status panel
(quality-gate pill, `project_key`, relative `last_metric_fetched_at`, enabled/reachable/
configured chips, an "Open dashboard" anchor `target=_blank rel='noopener noreferrer'`,
omitted when `dashboard_url` is null) plus the UX-state banners. **PH-235 — banners +
chips key off `status`, not the boolean trio:** the yellow `sonar-unreachable-banner`
shows ONLY for `status==="unreachable"` (a genuine outage); `status==="no_analysis"`
(configured but never scanned) shows a NEUTRAL `sonar-no-analysis-banner` ("No analysis
yet — run Sync (or a scan)") and an honest "No analysis" chip in place of the false
"Reachable off" chip (the Reachable chip renders only when `status` is `ok`/`unreachable`).
`enabled=false → "not enabled on this server" + buttons disabled; configured=false → Setup
is the glowing primary`. The board-detail `SonarHealthPanel` now takes a `projectKey` prop
(threaded from `BoardResponse.sonarqube_project_key` via `BoardDetail.tsx`): on a null-health
board with a key set it reads "Linked to <key> — no analysis yet · run a scan", and only a
null key keeps the original "Connect a project key to see quality metrics" copy. Two ADMIN
mutations back the buttons:
**Setup** (one-click, empty body → backend derives `project-hub` for PH; idempotent) and
**Sync now** (re-poll). Sync's `onSuccess` invalidates THREE families so a board-detail tab's
`SonarHealthPanel` tile refreshes without a reload — the status query, `['board', boardKey]`
(BoardResponse.health), and the `['board', boardKey, 'sonar-issues', ...]` live-counts family
(mirroring BoardDetail's `sonarqube_synced` WS handler). The dev/frontend_dev token lacks board
admin → setup/sync return 403; the buttons are hidden for `!isAdmin` AND both mutations'
`onError` catch `ApiRequestError.status===403` → an inline "Admin role required" message (no
crash). The SonarHealthPanel (board-detail header) is the SINGLE other Sonar surface and is left
untouched — there is NO second sync button there (settings owns the controls, per tight scope).

## Design decisions (recent)

- per-board scan = manifest/plan enqueue + HOST runner; docker.sock REJECTED [PH-236] — the backend container can't `docker compose run`, so `POST .../sonarqube/scan` (`request_board_scan`) only ENQUEUES (`scan_status=queued`) + the actual analysis runs HOST-side via `scripts/sonar-scan-board.sh`, which curls `GET .../sonarqube/scan-plan` (the FROZEN `{project_key, container_source, host_source, language, supported, reason, exclusions}` shape PH-237/the script depend on) and runs `sonar-scanner` with per-board `-D` props against `/repos/<path>`. Option 2 (mount `/var/run/docker.sock` into the backend so it spawns the scanner) was REJECTED — root-equivalent blast radius on a backend that already RO-mounts all of `$HOME` (PH-228) is not worth a convenience. The plan transport is an HTTP read (not a shared-volume file) to sidestep container-written/host-read uid issues (architect R2). The `sonar-scanner` compose service gained `${PROJECTS_ROOT:-${HOME}}:/repos:ro` (mirrors PH-228) so it sees board code; `/usr/src` (project-hub self-scan via `sonar-scan.sh` + `sonar-project.properties`) is UNCHANGED. LIVE-verified: `GXA` (Kotlin) → queued → host scanner auto-created the `GameX` project on the server; `FN` (Unity/C#) → unsupported.
- C# unsupported in CE → `scan_status=unsupported`, no silent fail / no fake queued [PH-236] — SonarQube Community Build cannot analyze C#/.NET (no SonarC# sensor), so `detect_board_language` flags a Unity layout (`Assets/` + `ProjectSettings/`|`Packages/`) OR `.cs`-dominant tree as `csharp` and `_language_supported` gates it to `unsupported` UP FRONT with an honest message — NOT a silent failure and NOT a fake `queued`. The host script likewise sees `supported=false` and exits 0 without scanning. Unknown/unrecognized languages stay optimistic-supported (let the scanner's sensors decide); only KNOWN-unsupported languages are gated. `scan` is DISTINCT from `sync` (sync re-polls existing analysis; scan plans a new run) — separate routes + behavior.
- honest status: no-analysis ≠ unreachable; added `status` enum + `has_analysis` [PH-235] — `build_setup_status` overloaded ONE boolean (`reachable`) to mean both "the server responded on a live attempt" AND "a cached metric exists", so on the pure-READ path `reachable_flag = bool(enabled and configured and metric is not None)` made a configured-but-never-scanned board (no metric row, e.g. GXA→GameX) render as `reachable=false` → the frontend's yellow "SonarQube server is unreachable" banner + a "Reachable off" chip + the board header's "Connect a project key" — all FALSE: the server is up, the board just has no analysis yet. The fix SEPARATES the two concepts: `has_analysis = metric is not None` (the truthful "we have data" signal) and an explicit `status` discriminator (`disabled|unconfigured|no_analysis|ok|unreachable`) that the UI keys ALL messaging off. The read path NEVER emits `reachable=false`/`unreachable` from metric-absence — absence becomes `no_analysis`; `reachable=false`/`unreachable` is reachable ONLY via the `sync` path passing a genuinely failed live attempt (so a REAL outage is still surfaced — locked by `test_build_setup_status_real_failed_sync_is_unreachable`). `reachable` is KEPT (additive/backward-compatible: best-effort = `has_analysis` on the read path) but superseded by `status`. `BoardResponse` gains `sonarqube_project_key` (additive nullable) so the board-header `SonarHealthPanel` distinguishes "no key" from "key set, no analysis yet" without a status call. **Scope = status/messaging classification ONLY** — NOT a new probe (the no-probe gotcha below stays valid); actual scanning is C2/PH-236. Browser-verified: GXA shows the honest "no analysis yet" (no false unreachable, header not "connect a key"); PH (has metrics) unchanged (`status=ok`, full panel).
- `board.repos_path` is now editable via PATCH `/api/boards/{id}` — and sonar key derivation reads its basename [PH-230] — C3 (epic PH-227 FINAL) extends `api_update_board` (`api/boards.py`) to accept `repos_path` (via `BoardUpdate.repos_path`) and `update_board(repos_path=...)`. **Validation reuses the detect/sonar contract**: a non-empty path is run through `repo_paths.to_container_path` inside the handler → `HTTPException(422)` on relative / `..` / outside-HOST_HOME (`RepoPathError` is a `ValueError` subclass authored for exactly this mapping); an empty string clears the path to NULL (board with no path = detection + basename-key disabled, falls back to `board.key.lower()` per PH-229's `_path_basename_key`). **No new auth gate**: the PATCH keeps `current_actor` (Name/Description stay pm-editable) — admin-gating is done in the BoardSettings UI field only, NOT widened to `require_board_admin` (would change Name/Description editability too). Consequence for THIS component: editing a board's path from the UI now changes the sonar default project key for non-PH boards (basename of the new path) on the NEXT setup/sync — PH still short-circuits to the `project-hub` literal first (unchanged). No migration (column exists since PH-228); `setup_board_project`/`build_setup_status`/`sync_board_now` signatures unchanged.
- Default project key is now path-basename-aware, PH literal kept FIRST [PH-229] — C2 (epic PH-227) makes `derive_default_project_key` "use the board path": a non-PH board with a `repos_path` derives its default key from the path basename (`kims`, `GameX`) instead of the bare `board.key.lower()`, because the basename IS the natural scanner project identity and now that PH-228 gives every board a real path, the key should reflect where the code lives. **The PH-literal branch is deliberately FIRST and never basename-derived** — even though `basename(/repos/Documents/project-hub)` coincidentally equals `project-hub`, depending on that coincidence is fragile: the key MUST equal `sonar-project.properties` `sonar.projectKey` or the post-merge scanner WRITE and the poller READ diverge (dashboard goes empty). So PH short-circuits before the basename path. **Total / never-raises:** `_path_basename_key` validates the path through `to_container_path` (consistency with detect's guard) and falls back to `board.key.lower()` on a null path, an empty basename, or a `RepoPathError` — so `setup_board_project` keeps PH-223's never-500 contract for a bad path. Scope held tight: only the DEFAULT-KEY derivation changed; `setup_board_project` signature, idempotent write-on-change, the scan-time-auto-create provisioning model, `build_setup_status`/`sync_board_now`, the secret-free `SonarSetupStatus`, and the dashboard-URL builder are ALL unchanged. The scanner invocation stays post-merge in `sonar-scan.sh` (NOT added here). No migration (logic-only; consumes PH-228's `repos_path`).
- settings-tab Setup/Sync UI; no second sync surface on the health panel [PH-226] — the
  one-click Setup + Sync buttons + status panel live in the BoardSettings `sonarqube` tab
  (mirrors the repository-tab admin-gating precedent), NOT on the board-detail
  `SonarHealthPanel` header (kept purely presentational). Status query key
  `['board', boardKey, 'sonar-setup']` is isolated from BoardResponse; Sync invalidates
  board.health + the sonar-issues family so the header tile still refreshes after a sync from
  settings. 403 (non-admin write) → buttons hidden + inline "Admin role required" (no crash).
- setup/sync/status endpoints + scan-time auto-create [PH-223] — "setup" = persist the
  project key only (auto-create covers one-click; no admin token, model (b) out of scope).
- sync = re-poll, NOT re-scan [PH-223] — reuse `poll_board` to read existing analysis
  within the 10s bound; a synchronous scanner run would block/timeout, so scans stay
  post-merge.
- GET status makes no live probe [PH-223] — reachability is derived from cached-metric
  freshness so a read never hangs on a down server; only `sync` makes a live attempt.
- issues proxy + host-facing dashboard deep links [PH-203] — graceful-200 status flags;
  `sonarqube_scan_url` only, never the internal URL or token.
- poll-cron + single upsert metric row per board [PH-193] — lifespan-gated task, layered
  error isolation, `sonarqube_synced` WS event for live board-health.

## Known gotchas

- The `sonar-scanner` compose service NEEDS the `${PROJECTS_ROOT:-${HOME}}:/repos:ro` mount or board scans see no code [PH-236] — it must mirror the PH-228 backend mount EXACTLY (same `${PROJECTS_ROOT:-${HOME}}` left side, same `/repos` right side) so a board's `repos_path` translated by `to_container_path` (`/repos/<rel>`) resolves to the SAME tree the backend detected the language from. If the two mounts diverge, the scanner's `-Dsonar.sources=/repos/<path>` points at nothing and the analysis is empty. The `/usr/src` mount (project-hub self-scan) is kept alongside — do NOT replace it.
- `scan` returns `queued` but metrics only appear after the host runner runs AND the next poll/sync — it is async BY DESIGN [PH-236] — `POST .../sonarqube/scan` does NOT analyze (the backend can't `docker compose run`); it records intent. You must run `scripts/sonar-scan-board.sh <key>` (the message tells you the exact command) for the analysis, then the PH-193 poll cron or a `sync` ingests the measures. So a `queued` response with a still-`no_analysis` board for a minute is expected, not a bug.
- `to_container_path` calls its OWN `get_settings` (in `repo_paths`) — tests must patch BOTH [PH-236] — patching only `sonarqube.get_settings` leaves `repo_paths.get_settings` returning the REAL `host_home`, so a tmp_path under `/tmp` raises `RepoPathError` and the scan path degrades to `error` instead of `queued`. `test_sonarqube_scan.py` patches `sonarqube`, `repo_paths`, AND `app.api.boards` get_settings together (see `_patch_all`). Same pitfall applies to any future test driving the path-translation branch.
- `detect_board_language` reads the FILESYSTEM, not just a string [PH-236] — unlike `derive_default_project_key`'s basename (a pure string), language detection `os.walk`s the real tree at `container_source`, so it needs the board code actually mounted at `/repos/<rel>` (the scanner mount). A configured board whose path is valid-but-unmounted detects `None` (generic sources scan) — that is the optimistic path, not an error. It is bounded (`_DETECT_MAX_FILES=4000`, skip-dirs pruned) so a huge Unity asset tree can't stall the cheap `scan` endpoint.
- The PH default key MUST be `project-hub` (not `ph`, not basename-derived) [PH-223 / PH-229] — it has to equal
  `sonar-project.properties` `sonar.projectKey`, or the scanner WRITE and the poller
  READ diverge and the dashboard shows empty. PH-229 added a path-basename default for
  non-PH boards but kept the PH-literal branch FIRST in `derive_default_project_key` so PH
  is NEVER routed through the basename path (even though `basename(.../project-hub)` would
  also yield `project-hub`, the literal must not depend on that coincidence). If you reorder
  that function, PH must short-circuit before any path logic.
- The basename default uses the HOST path, and a typo'd (unmounted) path still yields a key [PH-229] — `_path_basename_key` derives a STRING basename and only validates the path is well-formed + under `HOST_HOME` (via `to_container_path`); it does NOT check the path exists on disk. So a board whose `repos_path` is syntactically valid but not mounted (typo) still produces a basename key — that is intentional (sonar config is a string identity, decoupled from filesystem presence, unlike DETECT which needs the dir to exist). A path that is null, empty, or raises `RepoPathError` (outside `HOST_HOME` / `..`) is the only case that falls back to `board.key.lower()`.
- Setup persists the key even when `sonarqube_enabled=false` [PH-223] — the status then
  reports `enabled=false` so the UI shows "linked, but disabled", not a live link.
- NEVER add a synchronous reachability probe to `GET .../status` [PH-223 / PH-235] — a read
  must not block on a down SonarQube. (The no-probe rule still holds.) **CORRECTED (PH-235):**
  the old "report `reachable` from `last_metric_fetched_at`" guidance was the BUG — deriving
  `reachable` from metric presence made a configured-but-never-scanned board (no metric) report
  a FALSE `unreachable`. Metric presence now drives **`has_analysis`**, NEVER `reachable`. The
  pure-read path emits `status=no_analysis` (not `unreachable`) for a configured board with no
  metric; `reachable=false`/`status=unreachable` comes ONLY from a real failed live `sync`. Do
  NOT re-couple `reachable` to metric freshness — that re-introduces the false-outage signal.
- Secret leak is the HIGH risk [PH-223 / PH-203] — `SonarSetupStatus` / `SonarIssuesResponse`
  and every log line must never carry `sonarqube_token` or the compose-internal
  `sonarqube_url`; only `sonarqube_scan_url`-derived host links.
- A genuinely missing board is a legit 404 [PH-203 / PH-223 / PH-233] — the never-500 rule
  applies only to SonarQube degradation, not to a non-existent board (`get_board` → NotFound).
  For admin-gated setup/sync, `require_board_admin` now resolves the board via `get_board`
  FIRST, so an unknown board (KEY or UUID) → **404** (resolve-before-authz); a resolved board
  with no admin membership → 403. (Pre-PH-233 the gate parsed `uuid.UUID(board_id)` and 403'd
  on unknown ids — corrected.)
- Admin-gated board routes accept board KEY or UUID — the gate MUST resolve via `get_board`,
  never raw `uuid.UUID()` [PH-233]. `require_board_admin` (`api/deps.py`) once parsed the raw
  `{board_id}` path param with `uuid.UUID()` and 403'd on any non-UUID, so EVERY key-based
  admin call (the UI always sends the KEY) was blanket-denied, even for a real admin. It now
  mirrors the correct sibling `repositories.py:_require_board_admin`: `get_board(session,
  board_id)` (key-or-uuid) then the `BoardMembership` admin check. The ordering is
  load-bearing — resolve (404 on miss) BEFORE authz (403 on non-admin), never the reverse.

## Related

- [[components/backend]] — owns the board model, REST router, lifespan task wiring
- [[components/frontend]] — `SonarHealthPanel` + the PH-226 settings buttons consume these
