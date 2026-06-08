---
type: component
files: [backend/app/services/sonarqube.py, backend/app/api/boards.py]
last_touched_ticket: PH-223
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
  default (`derive_default_project_key`: PH → `project-hub` to match
  `sonar-project.properties`; else board key lowercased). **Idempotent** — only writes
  when the value changes. Provisioning = **scan-time auto-create**: persisting the key
  is enough (the post-merge `sonar-scanner` auto-creates the Community project on first
  analysis); NO admin-API `projects/create` call (no admin token provisioned — out of
  scope). The key is persisted even when sonar is disabled (config allowed offline).
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

## Design decisions (recent)

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

- The PH default key MUST be `project-hub` (not `ph`) [PH-223] — it has to equal
  `sonar-project.properties` `sonar.projectKey`, or the scanner WRITE and the poller
  READ diverge and the dashboard shows empty.
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
