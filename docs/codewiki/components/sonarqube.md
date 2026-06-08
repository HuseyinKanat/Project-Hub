---
type: component
files: [backend/app/services/sonarqube.py, backend/app/api/boards.py, frontend/src/components/sonarqube/SonarSetupSection.tsx]
last_touched_ticket: PH-229
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
  `SonarQubeMetric` with **no network call** (a read must never hang). `reachable` is
  passed in by `sync` (the live-attempt result), else derived from cached-metric
  freshness on the pure read path.

`SonarSetupStatus` (frozen for PH-226 / C6) is SECRET-FREE:
`{ enabled, reachable, configured, project_key, last_metric_fetched_at,
quality_gate_status, dashboard_url, message }` — never the token, never the
compose-internal URL; `dashboard_url` = `sonarqube_scan_url` + `/dashboard?id=key`.

**Frontend consumer** (PH-226 / C6, `frontend/src/components/sonarqube/SonarSetupSection.tsx`,
rendered as the `sonarqube` tab in `BoardSettings.tsx`): a single member-level status
query keyed `['board', boardKey, 'sonar-setup']` (DEDICATED key — not `['board', boardKey]`
— so it never collides with BoardDetail's `BoardResponse` cache) drives a status panel
(quality-gate pill, `project_key`, relative `last_metric_fetched_at`, enabled/reachable/
configured chips, an "Open dashboard" anchor `target=_blank rel='noopener noreferrer'`,
omitted when `dashboard_url` is null) plus the UX-state banners (enabled=false → "not enabled
on this server" + buttons disabled; reachable=false → unreachable note, Sync stays enabled for
retry; configured=false → Setup is the glowing primary). Two ADMIN mutations back the buttons:
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
- NEVER add a synchronous reachability probe to `GET .../status` [PH-223] — a read must
  not block on a down SonarQube; report `reachable` from `last_metric_fetched_at`.
- Secret leak is the HIGH risk [PH-223 / PH-203] — `SonarSetupStatus` / `SonarIssuesResponse`
  and every log line must never carry `sonarqube_token` or the compose-internal
  `sonarqube_url`; only `sonarqube_scan_url`-derived host links.
- A genuinely missing board is a legit 404 [PH-203 / PH-223] — the never-500 rule applies
  only to SonarQube degradation, not to a non-existent board (`get_board` → NotFound).
  For admin-gated setup/sync, an unknown board UUID is rejected by `require_board_admin`
  (403) before `get_board` runs.

## Related

- [[components/backend]] — owns the board model, REST router, lifespan task wiring
- [[components/frontend]] — `SonarHealthPanel` + the PH-226 settings buttons consume these
