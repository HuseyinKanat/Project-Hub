---
type: component
files: [backend/app/services/sonarqube.py, backend/app/api/boards.py, backend/app/api/scans.py, backend/app/db/models/core.py, frontend/src/components/sonarqube/SonarSetupSection.tsx, frontend/src/components/sonar/SonarDashboard.tsx, frontend/src/components/sonar/MetricCard.tsx, frontend/src/components/sonar/metricMeta.ts, frontend/src/components/SonarHealthPanel.tsx, frontend/src/components/SonarIssueDrawer.tsx, frontend/src/pages/BoardDetail.tsx, frontend/src/api/client.ts, frontend/src/types/api.ts, scripts/sonar-scan-board.sh, scripts/sonar-scan-watcher.sh]
last_touched_ticket: PH-243
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
  + container source + language + supported + `sonar.exclusions`). Pure (settings +
  filesystem only, no network), never-500. DRY between `scan` and `scan-plan`.
- `_resolve_container_source(board)` — the scan SOURCE resolver. **PH-242: PREFERS the
  board's primary linked `Repository.local_path` over the coarse `board.repos_path`.**
  `repos_path` is often the PARENT dir of the real code root (GXA: repos_path=`…/GameX`
  but code lives in `/repos/…/GameX/GameXCore`), so scanning `repos_path` binds Sonar to
  the wrong tree (0 LoC + junk dirs). Resolution: (1) primary repo with a truthy
  `local_path` → use it; AS-IS when it already starts with `settings.repos_root`
  (`/repos`, the documented column invariant — `local_path` is ALREADY container-form, do
  NOT re-translate), else (legacy host-form row) `to_container_path` with a `repos_path`
  fallback on `RepoPathError`; (2) no usable primary → existing `repos_path` →
  `to_container_path` (PH/KIM/FN/BENCH/GXI byte-for-byte unchanged). `host_source`
  (informational only) is set from `to_host_path(local_path)` best-effort when the repo
  branch wins. `project_key` resolution is INDEPENDENT (untouched) — a source change can't
  rename a project. Reading the primary repo goes through `_loaded_primary_repository`,
  which returns the primary ONLY when `Board.repositories` is already eager-loaded (the
  SQLAlchemy `inspect(board).unloaded` check) — an unloaded collection degrades to the
  fallback instead of an async lazy-load, keeping never-500. Both production callers
  (`get_board` → scan / scan-plan) eager-load `repositories`, so the primary branch is
  live there.
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
  (`-Dsonar.projectBaseDir=/repos/<path> -Dsonar.projectKey=<key>
  -Dsonar.projectName=<key> -Dsonar.sources=/repos/<path> -Dsonar.tests=
  -Dsonar.exclusions=… -Dsonar.scm.disabled=true`); the **`projectBaseDir` pin
  (PH-243)** anchors the scanner to the board's own source root so it does NOT load
  project-hub's `/usr/src/sonar-project.properties` (the compose `working_dir`), and the
  explicit empty `sonar.tests=` guards against PH's `backend/tests,tests` ever bleeding
  into a board scan; if `supported=false` (C#) it logs
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

**"Scan now" consumer** (PH-237 / C3, same `SonarSetupSection.tsx`): a THIRD admin action next
to Sync, consuming the PH-236 scan endpoints via `api.sonarqube.scan` (POST → `SonarScanResult`)
+ `api.sonarqube.getScanPlan` (GET → `SonarScanPlan`). A **lazy, admin-only** scan-plan query
keyed `['board', boardKey, 'sonar-scan-plan']` (`enabled: boardKey && tabActive && isAdmin`,
`staleTime 60s`, `retry:false`) resolves the detected `language` + CE `supported`/`reason`
BEFORE the click — it MUST stay `isAdmin`-gated because scan-plan is admin-only (403 for
non-admins → it would 403-spam). It feeds the status-panel "Language: <lang>" row + a
"Scannable" / "Not scannable in Community Edition" chip (with the honest `reason` below when
unsupported). The Scan button (Radar icon, NOT RefreshCw — visually DISTINCT from Sync's cheap
re-poll, with helper copy "runs a new analysis on the host … slower … in the background") is
DISABLED up-front when `plan.supported === false` (annotated with `reason`), or `!enabled` /
`!configured` / `busy`. On click, the scan mutation maps the SIX-value `scan_status`
(`queued|running|unsupported|disabled|unconfigured|error`) to HONEST inline copy:
`queued`/`running` → success-tone "Scan queued — analysis runs in the background; metrics appear
after it completes. Re-sync to refresh." (deliberately NOT the generic "status refreshed" line —
that would LIE about instant metrics); `unsupported` → the backend `message`/reason;
`unconfigured` → "Run Setup …"; `disabled` → "not enabled"; `error` → the message. The generic
Setup/Sync success region is EXCLUDED for scan (its honest async copy comes from
`scanResultFeedback`). Scan `onSuccess` invalidates the SAME three Sync families PLUS the
`['board', boardKey, 'sonar-scan-plan']` key (re-resolve language/support) — the metric is NOT
present immediately (honest async, not a bug; there is no progress endpoint, so NO polling/
progress-bar is invented). The scan mutation `onError` folds into the shared `mutationError`
(`setup ?? sync ?? scan`) so a hard 403 still surfaces "Admin role required". The
`SonarScanResult`/`SonarScanPlan` TS types mirror the backend schemas VERBATIM with the wire
field name `scan_status` (snake) 1:1 (a `status` rename would silently read `undefined`).

**Auto-scan execution loop** (PH-239): "Scan now" now actually scans with NO operator
hand-running a script. `request_board_scan` PERSISTS a `SonarScanJob(state=queued)` (table
`sonar_scan_jobs`: `board_id`, `project_key` snapshot, `state` queued→running→done|failed,
`requested_by`, `requested_at`/`started_at`/`finished_at`, `detail`; idempotent per board).
A host daemon `scripts/sonar-scan-watcher.sh` long-polls `GET /api/scans/pending`
(`backend/app/api/scans.py`), `POST .../claim`s each job (queued→running, 409-guarded against
a double run), runs the UNCHANGED `scripts/sonar-scan-board.sh <key>` (which now emits a
`SONAR_SCAN_RESULT=ok|failed|skipped` marker so its always-exit-0 contract doesn't hide the
real outcome), then `POST .../complete {success, detail}`. On `success=true` the backend
IMMEDIATELY calls `poll_board` to ingest the fresh analysis (metrics in seconds; the 300s poll
cron is the backstop for SonarQube's async indexing); on failure it records `detail` and does
NOT ingest. The first successful scan auto-provisions the Community project (no admin
`projects/create`), so `localhost:9000/dashboard?id=<key>` resolves afterwards. The watcher is
the ONE host piece — if it is down, jobs sit honestly `queued` (never fake-done). Runbook:
`docs/sonar-watcher.md` (launchd/systemd/nohup; cron is a documented degraded fallback).

**In-app quality dashboard** (PH-241 / epic PH-238 child C, `frontend/src/components/sonar/`):
the SonarQube READ surface now has a native, branded home in the board's MAIN area — a third
**"Quality"** tab (`#quality`, refresh-safe) in `BoardDetail.tsx`'s tablist alongside Kanban /
Branch Graph, rendering `<SonarDashboard>` in a `role="tabpanel"` (`#panel-quality`). It is
PURELY PRESENTATIONAL + prop-driven (`{ health, boardKey, projectKey }`, mirroring
`SonarHealthPanel`) — NO new endpoint, NO new fetch: it reuses `BoardResponse.health` (the 7
metrics) + `sonarqube_project_key` (empty-state copy) + `useSonarLiveCounts` (live BUG/VULN/
CODE_SMELL totals reconciled `?? health.*`, same as the strip so counts never diverge), so the
existing `sonarqube_synced` WS invalidation in BoardDetail refreshes the grid + counts live with
ZERO extra wiring. Layout: a **quality-gate hero** (Passed/Failed/Warning/Unknown reusing the
strip's GATE_MAP tones; on ERROR a "See failing conditions in SonarQube" deep-link — BoardHealth
carries only the aggregate gate, not per-condition detail) + a **6-card responsive grid**
(`grid-cols-1 sm:2 lg:3`). Each card (`MetricCard.tsx`) shows the metric value (count→integer,
percent→`NN.N%`, **null→em-dash "—" NOT a fabricated 0**, Risk R1), a canonical static SonarQube
description + a good-direction hint, sourced from `metricMeta.ts`'s `METRIC_META`/`GATE_META`
(pure data, no JSX, unit-testable). Issue-backed cards (bugs/vulns/smells, `issueType != null`)
are real focusable `<button>`s (`aria-haspopup="dialog"`, `aria-label="View N bugs"`) that mount
the UNCHANGED `SonarIssueDrawer` on click — DISABLED when count is 0/null so they never open an
empty drawer; coverage/duplications/ncloc/gate are non-interactive `<div>`s. The dashboard lifts
`openType`/`triggerRefs`/`handleClose` so focus restores to the clicked card on drawer close
(a11y parity with the strip). A host-facing "Open in SonarQube" link uses the `dashboard_url`
from the existing issues query. **Honest empty states (Risk R3):** `health == null` + key set →
"Linked to &lt;key&gt; — no analysis yet · run a scan" (KIM's live state); no key → "Connect a
project key" — NEVER blank or a fake-zero grid. `SonarHealthPanel` is KEPT as the glanceable
strip and gains ONE optional `onOpenDashboard` prop → a "View details →" deep-link that calls
`switchTab("quality")` (existing callers unaffected; no other strip behavior changed).

## Design decisions (recent)

- board scans isolated via `-Dsonar.projectBaseDir=$CONTAINER_SOURCE`; Kotlin/Gradle detection marker [PH-243] — a SECOND root cause behind "every project bound to a nonsense code section" (PH-242 fixed only the source PATH). The `sonar-scanner` compose service runs with `working_dir=/usr/src` + `.:/usr/src:ro`, so `/usr/src` carries project-hub's OWN `sonar-project.properties`. sonar-scanner-cli reads `sonar-project.properties` from `projectBaseDir`, which DEFAULTS to the CWD (`/usr/src`); `sonar-scan-board.sh` never set it, so EVERY board scan inherited PH's `sonar.tests=backend/tests,tests` (+ coverage + python.version). The `-Dsonar.sources` override masks `sonar.sources` but NOT `sonar.tests` → GameX (projectKey=GameX) Code view showed exactly PH's `backend/tests` + `tests/e2e`, 0 ncloc for GameXCore's 133 real `.kt`. **Fix (script-side, no SonarScanPlan shape change — PH-236 frozen contract preserved):** pin `-Dsonar.projectBaseDir="$CONTAINER_SOURCE"` (the board's own root, which has NO sonar-project.properties) → clean isolation at the source, not a `-D` whack-a-mole; relative paths resolve inside the board tree. Belt-and-suspenders `-Dsonar.tests=` (explicit empty) so a future baseDir regression can never silently re-bleed PH test dirs. `projectBaseDir == container_source` is a pure script-side derivation of the already-emitted field — `build_scan_plan`/`SonarScanPlan` UNCHANGED. **SECONDARY:** `detect_board_language` gained a Gradle/Kotlin marker shortcut (sibling to the Unity shortcut, BEFORE the extension tally): a root `build.gradle.kts`/`settings.gradle.kts` → `kotlin`. An Android/Kotlin project's deep `src/main/kotlin` tree (or stray `.py` tooling) could out-tally / exhaust the bounded `_DETECT_MAX_FILES=4000` walk and misclassify as `python`; since `language` drives the `supported` gate, a real Kotlin board (GameXCore) must classify `kotlin`/supported. Conservative marker-file check won't misfire on a real python repo. **HARD RULE held:** `scripts/sonar-scan.sh` (PH self-scan) + `sonar-project.properties` are byte-for-byte UNCHANGED — the PH self-scan keeps CWD `/usr/src` and loads its props on purpose; only `sonar-scan-board.sh` changed. Verified: new detect tests (Android-Kotlin marker + stray .py → kotlin, settings.gradle.kts-alone → kotlin, plain python repo stays python) + shell-level grep assertions (board-scan carries baseDir/empty-tests; sonar-scan.sh has neither) + full sonar suite green, ruff clean, mypy adds 0 errors on `sonarqube.py`. Live GXA re-scan (ncloc>0, no PH components) is the Coordinator post-merge op.
- scan source prefers `primary_repository.local_path` over `board.repos_path` [PH-242] — Sonar scans bound to the WRONG codebase because `_resolve_container_source` used ONLY the coarse `board.repos_path` (often the PARENT dir) and never consulted the board's linked `Repository.local_path` (the precise, git-synced code root). Concrete break: board GXA had repos_path=`/Users/.../GameX` (parent) but the code lives in `/repos/AndroidStudioProjects/GameX/GameXCore`, so the `GameX` Sonar project showed 0 LoC bound to junk dirs (backend/tests, tests/e2e). Boards that worked did so only because their repos_path COINCIDED with the code root (PH, KIM). Fix scoped to `_resolve_container_source` only: prefer the primary repo's `local_path` (used AS-IS — the column invariant guarantees container-form `/repos/...`, re-translating via `to_container_path` would raise since it expects a HOST path), defensive `to_container_path` only for a legacy host-form row, `repos_path` fallback otherwise (PH/KIM/FN/BENCH/GXI unchanged byte-for-byte). `project_key` resolution kept DECOUPLED/untouched (persisted GXA=`GameX` retained — a source change can't rename a project). `host_source` (informational) set from `to_host_path(local_path)` best-effort when the repo branch wins (cosmetic; `container_source` is load-bearing). **Async-safety guard `_loaded_primary_repository`:** reading `primary_repository` iterates `board.repositories`, which would lazy-load (`MissingGreenlet`) in async if not eager-loaded — both prod callers `selectinload` it, but the guard uses `inspect(board).unloaded` to degrade an unloaded collection to the fallback instead of crashing (never-500 hard contract; also fixed a real `test_sonarqube_scan_jobs.py` breakage where the test helper builds a bare-loaded board). GameX cleanup is a post-merge re-scan (overwrites the stale 0-LoC project in place — no explicit deletion). Verified: 7 new regression tests in `test_sonarqube_scan.py` (GXA→GameXCore subdir, no-repo fallback, PH/KIM root unchanged, no-primary fallback, legacy host-form normalize, unnormalizable fallback, unloaded-collection no-crash) + full sonar suite 121 green, ruff clean, mypy adds 0 errors.
- in-app SonarQube quality dashboard = a board-detail "Quality" tab, NOT a route / not Settings [PH-241] — the user said sonar "ana kisimda gorunmuyor" (only the header strip + the Settings config tab existed). PH-241 adds a native cyan-on-black dashboard as a THIRD `BoardDetail` tab (`#quality`), the most consistent placement with the app's existing in-board tab idiom — rejected: a dedicated `/boards/:key/quality` route (loses the shared board header + WS connection BoardDetail owns), header-strip expansion (the user wanted a real main-area surface, not a bigger strip), and the Settings tab (owns CONFIG, not the read dashboard). **Zero new data layer (Risk R6):** `SonarDashboard` is prop-driven from the SAME `["board", boardKey]` cache + `useSonarLiveCounts` the strip already uses, so the existing `sonarqube_synced` WS handler gives live refresh for free — no new endpoint, no new TanStack key family. New files `components/sonar/{SonarDashboard,MetricCard}.tsx` + `metricMeta.ts` (METRIC_META: key/label/canonical-description/unit/goodDirection/issueType/icon; GATE_META exported separately to dodge `noUncheckedIndexedAccess` on `METRIC_META[0]`). **`.codemap` gotcha (Risk R7):** the new dir is `components/sonar/` — the existing `components/sonarqube/*` glob does NOT cover it, so a new `frontend/src/components/sonar/*.tsx → components/sonarqube.md` line was added to keep the dashboard sync-gated. Reused UNCHANGED: `SonarIssueDrawer` (drill-down, lists `component:line`), `useSonarIssues`, `api/client`, `types/api`. `SonarHealthPanel` kept (not folded) + an optional `onOpenDashboard` deep-link. **LIVE browser-verified (preview, dark+light):** KIM (no analysis) → honest "Linked to kims — no analysis yet" empty state, no crash; PH (has metrics) → gate hero "Failed" + 6 cards (Bugs 0, Vulns 0, Code Smells 11, Coverage 47.0%, Duplications 1.2%, ncloc 23386) with descriptions; clicking Code Smells opened the drawer with 11 rows (first `frontend/src/pages/BoardSettings.tsx:357`) + dashboard_url; close restored focus to the card; no console errors.

- auto-scan execution via a host watcher daemon + `SonarScanJob` lifecycle [PH-239] — "Scan now" used to ONLY enqueue intent (it told the operator to hand-run `scripts/sonar-scan-board.sh kims`) so nothing ever scanned automatically and KIM's `kims` project was never auto-provisioned (404). PH-239 closes the loop: `request_board_scan` now PERSISTS a `SonarScanJob(state=queued)` row (new `sonar_scan_jobs` table; idempotent per board — re-clicking while one is queued REUSES that row, no duplicate scanner run, R5) instead of only returning intent. A NEW non-board-scoped router `backend/app/api/scans.py` exposes the watcher seam: `GET /api/scans/pending` (the queue, secret-free `[{job_id, board_key, project_key}]`), `POST /api/scans/{id}/claim` (queued→running, atomic — a second claim → 409, the double-run guard R2), `POST /api/scans/{id}/complete {success, detail}` (running→done|failed; **on success the backend immediately calls `poll_board` to ingest the metrics** so they appear within seconds, not after the 300s poll cron — the cron stays the backstop for SonarQube's async-indexing race R3; on failure it records `detail` and does NOT ingest). A NEW host daemon `scripts/sonar-scan-watcher.sh` long-polls `pending`, claims each job, runs the UNCHANGED `sonar-scan-board.sh <key>`, and POSTs `complete`. **ADR — mechanism (b) watcher chosen, NOT (a) docker-socket-into-backend** (root-equivalent host control = privilege-escalation surface, rejected on the same security posture as PH-236) NOR (c) cron (coarse 1-min granularity, no clean `running` signal — documented as a degraded fallback only in `docs/sonar-watcher.md`). **Honest RC channel (R4):** `sonar-scan-board.sh` ALWAYS exits 0 (deploy contract), so it now emits a final `SONAR_SCAN_RESULT=ok|failed|skipped` marker line the watcher parses (its exit code can't carry the scanner's real outcome). Auto-provisioning is unchanged (Community auto-creates the project on the first successful scan — no admin `projects/create`). The `scan-plan` JSON shape stays FROZEN. The honest non-scannable outcomes (unsupported/unconfigured/disabled) enqueue NO job (PH-236/235 contract preserved). Migration `e3a479aa5c01` is strictly additive (new table + 2 indexes; no existing table rewritten — safe on the live Kims DB). Runbook: `docs/sonar-watcher.md` (launchd/systemd/nohup). Verified via mocked-scanner pytest (`tests/test_sonarqube_scan_jobs.py`): queue-persist, idempotent re-enqueue, pending/claim/complete contract, 409 double-claim guard, immediate-ingest-on-success / no-ingest-on-failure. **Review hardening:** the 404 `"scan job not found"` literal is hoisted to `_JOB_NOT_FOUND` in `scans.py` (S1192, no behavior change); `claim_scan_job` reads the job via `_get_job(..., for_update=True)` (`SELECT ... FOR UPDATE`) so the read-check-write claim is row-locked — two racing watchers can't both observe `queued` and both claim (TOCTOU closed). The conflict path raises before any write so no lock lingers; `complete_scan_job` is unchanged (no lock).
- frontend "Scan now" is scan-plan-gated + honest-async, DISTINCT from Sync [PH-237] — `SonarSetupSection` gained a third admin action wired to PH-236's `POST .../sonarqube/scan` (`api.sonarqube.scan`) + `GET .../sonarqube/scan-plan` (`api.sonarqube.getScanPlan`). Key calls: (1) the scan-plan query is **lazy AND `isAdmin`-gated** (`enabled: boardKey && tabActive && isAdmin`) — scan-plan is admin-only, so firing it for a non-admin would 403-spam (Risk R1); a non-admin sees NO scan-plan call AND no Scan button (the Actions card is already `{isAdmin && …}`). (2) Support is surfaced UP FRONT: the resolved `language` + a "Scannable"/"Not scannable in Community Edition" chip render in the status panel, and `plan.supported === false` DISABLES the Scan button with the honest `reason` annotation — the user is not surprised on click. (3) The success copy is HONEST about async: `queued`/`running` → "Scan queued — analysis runs in the background; metrics appear after it completes. Re-sync to refresh." (NOT the generic "status refreshed" — that implies instant metrics, the whole point of the epic; Risk R3). All SIX `scan_status` values map to a clear inline message (Risk R5). (4) Radar icon + helper copy make Scan visually + behaviorally DISTINCT from Sync's cheap re-poll (Risk R4). (5) NO progress polling / live-scan indicator invented — the backend is fire-and-forget (no job-id endpoint); `queued` + honest copy + a four-family invalidation (Sync's three + the scan-plan key) is the whole contract (Risk R6). The `scan_status` TS field matches the snake-case wire 1:1 (a `status` rename → silent `undefined`; Risk R2). LIVE browser-verified (Playwright, `tests/e2e/ph-237-sonarqube-scan-now.spec.ts`, against real GXA/FN data): GXA (Kotlin, `supported=true`) → Scannable chip + Scan enabled → click → honest queued copy + scan-plan re-fetch; FN (C#/Unity, `supported=false`) → "Not scannable in Community Edition" + Scan disabled with reason, no POST on force-click; non-admin → no Scan button + scan-plan never fires.
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

- A board scan MUST pin `-Dsonar.projectBaseDir=$CONTAINER_SOURCE` or it bleeds PH's `sonar-project.properties` [PH-243] — the `sonar-scanner` compose service has `working_dir=/usr/src` with project-hub bind-mounted there (`.:/usr/src:ro`), and `/usr/src` carries PH's OWN `sonar-project.properties`. sonar-scanner-cli loads `sonar-project.properties` from `projectBaseDir`, which defaults to the CWD — so without the pin EVERY board scan inherits PH's config (`sonar.tests=backend/tests,tests`, coverage, python.version) and the board's project shows PH's test dirs at 0 ncloc. Setting baseDir to the board's own root (no sonar-project.properties there) is the isolation. Do NOT "simplify" by removing the baseDir flag and relying only on `-Dsonar.sources` — sources masks `sonar.sources` but NOT `sonar.tests`, which is exactly why the bleed happened.
- `-Dsonar.sources` does NOT mask `sonar.tests` — only baseDir isolation stops the test-dir bleed [PH-243] — the original board-scan passed `-Dsonar.sources=/repos/<path>` and assumed that fully isolated the board. It only overrode `sonar.sources`; the inherited `sonar.tests=backend/tests,tests` (from PH's props) stayed live, so PH's `backend/tests` + `tests/e2e` showed up as TEST components in the board project. The real fix is baseDir (no foreign props loaded at all) + an explicit empty `sonar.tests=`; never add board-scan `-D` neutralizers one property at a time expecting `-Dsonar.sources` to cover the rest.
- `scripts/sonar-scan.sh` (PH self-scan) must NEVER pin `projectBaseDir` [PH-243] — the two scripts are siblings but OPPOSITE: `sonar-scan-board.sh` pins baseDir to escape PH's props; `sonar-scan.sh` (the post-merge PH self-scan) deliberately keeps CWD `/usr/src` so it DOES load `sonar-project.properties` (that file IS the PH self-scan config). Editing the wrong script (adding baseDir to `sonar-scan.sh`) silently breaks the PH self-scan. `test_ph_self_scan_script_unchanged_no_basedir` asserts `sonar-scan.sh` carries no `projectBaseDir`/`-Dsonar.tests=`.
- `detect_board_language` Kotlin/Gradle marker fires BEFORE the extension tally [PH-243] — a root `build.gradle.kts`/`settings.gradle.kts` short-circuits to `kotlin` regardless of the file tally, because a real Android/Kotlin project's deep `src/main/kotlin` tree (or stray `.py` build scripts) can out-tally or exhaust the bounded `_DETECT_MAX_FILES=4000` walk and misclassify as `python` — which would wrongly drive `_language_supported`. The marker is a conservative root-file presence check (won't misfire on a real python repo, which has no `*.gradle.kts`). It is a sibling of the Unity `Assets/`+`ProjectSettings/` shortcut and must stay ordered after Unity but before the tally.
- `Repository.local_path` is ALREADY container-form (`/repos/...`) — do NOT pass it through `to_container_path` [PH-242] — the column invariant (core.py: NOT NULL, "must start with `/repos/`") means the primary repo's `local_path` is the IN-CONTAINER path already. `to_container_path` expects a HOST path under `HOST_HOME` and RAISES `RepoPathError` for a `/repos/...` input, so `_resolve_container_source` uses `local_path` AS-IS when it starts with `settings.repos_root`; `to_container_path` is only the DEFENSIVE branch for a legacy/host-form row, and even then a `RepoPathError` falls back to `repos_path` (never-500). Contrast `board.repos_path`, which IS a HOST path and DOES go through `to_container_path`.
- reading `board.primary_repository` requires `repositories` eager-loaded — guard via `_loaded_primary_repository` [PH-242] — `primary_repository` iterates `self.repositories`; in async an UNLOADED collection triggers a lazy-load → `MissingGreenlet` (crash). Both production scan callers go through `get_board`, which `selectinload(Board.repositories)`, so the primary branch is live there. But any bare-loaded `Board` (e.g. a test helper or a future caller doing `select(Board)` without the option) must NOT crash — `_loaded_primary_repository` checks `inspect(board).unloaded` and returns None (→ `repos_path` fallback) when the collection isn't loaded. Do NOT remove this guard to "simplify"; it is the never-500 backstop and is regression-tested (`test_resolve_source_unloaded_repositories_falls_back_no_crash`).
- `sonar-scan-board.sh` ALWAYS exits 0, so read `SONAR_SCAN_RESULT=`, NOT its exit code [PH-239] — the per-board runner hard-guarantees exit 0 (the PH-194/208 deploy contract: a scan problem must never block). Its exit code therefore tells a caller NOTHING about whether the SCANNER succeeded. The watcher must parse the final `SONAR_SCAN_RESULT=ok|failed|skipped` marker line the script now emits on stdout — keying off `$?` would report every run (even an auth failure or a skipped unsupported board) as success and falsely ingest/complete-success. `skipped` (disabled/unconfigured/unsupported/unreachable — no analysis produced) is completed as `failed` with a detail, NOT success.
- `/api/scans/*` is NOT board-scoped — it uses `current_actor`, not `require_board_admin` [PH-239] — a scan job is addressed by its own id (the watcher has no board context when draining the queue), so the three endpoints live on their own `/api/scans` router gated by an authenticated actor (the watcher sends the same admin bearer the host scripts already use via `SONAR_API_TOKEN`). Do NOT try to reuse `require_board_admin` here (it needs a `board_id` path param that these routes do not have). The 404 (unknown/malformed job id) and 409 (illegal lifecycle transition) are DELIBERATE client-error signals, not never-500 violations.
- The scan-plan query MUST stay `isAdmin`-gated, or non-admins 403-spam [PH-237] — `GET .../sonarqube/scan-plan` is admin-only (`require_board_admin`). The frontend's scan-plan `useQuery` is `enabled: Boolean(boardKey) && enabled && isAdmin` — drop the `isAdmin` clause and every non-admin viewing the SonarQube tab fires a guaranteed-403 request (and a retry storm without `retry:false`). The Scan button itself is already hidden for non-admins (the Actions card is `{isAdmin && …}`), but the QUERY is the easier thing to leave ungated. Keep both gated.
- The `SonarScanResult` TS field is `scan_status` (snake), NOT `status` [PH-237] — the wire key from `SonarScanResponse` is `scan_status`; `request<T>` does NO key remapping, so naming the interface field `status` (like `SonarSetupStatus.status`) makes every `switch` branch read `undefined` and silently fall through to the default. The interface field name must match the JSON 1:1. (`SonarSetupStatus.status` legitimately matches ITS wire field `status` — different endpoint, do not copy the name across.)
- "Scan now" success is async — `queued` does NOT mean metrics are present [PH-237] — the Scan button's success copy deliberately says metrics appear AFTER the background host run ("re-sync to refresh"), never "status refreshed". The scan `onSuccess` invalidates the status/board/issues families + the scan-plan key, but the SonarHealthPanel tile will still show the OLD (or no) analysis until `scripts/sonar-scan-board.sh <key>` runs on the host AND a subsequent poll/sync ingests measures. Do NOT "fix" this by reusing the generic "Done — status refreshed" line — that re-introduces the dishonest instant-metrics implication the epic exists to kill.
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
