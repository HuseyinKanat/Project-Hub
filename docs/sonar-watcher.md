# SonarQube auto-scan watcher — runbook (PH-239)

The **SonarQube "Scan now" button only enqueues a job** — the backend runs inside a
container and cannot `docker compose run` the scanner. A small **host-side watcher
daemon** (`scripts/sonar-scan-watcher.sh`) is the ONE host piece that makes scans run
automatically with no operator hand-running a shell script.

```
"Scan now" → POST /api/boards/{key}/sonarqube/scan
           → backend persists SonarScanJob(state=queued)
watcher    → GET  /api/scans/pending            (long-poll, default every 10s)
           → POST /api/scans/{id}/claim          (queued → running)
           → bash scripts/sonar-scan-board.sh KEY (runs the scanner container)
           → POST /api/scans/{id}/complete        (running → done/failed)
backend    → on done-success: immediate poll_board ingest → SonarQubeMetric upsert
             + sonarqube_synced WS event (the 300s poll cron is the backstop)
```

If the watcher is **not running**, jobs simply sit `queued` — honestly visible as
queued (never fake-done). The first successful scan auto-provisions the SonarQube
Community project (no admin `projects/create` needed); `localhost:9000/dashboard?id=<key>`
resolves afterwards.

## Prerequisites

- `SONARQUBE_ENABLED=true` in `.env` (the watcher idles quietly when it is not).
- Docker + `docker compose` on the host (the watcher invokes the scanner via the
  existing `scripts/sonar-scan-board.sh`, which uses the `--profile scan` service).
- `jq` **or** `python3` for JSON parsing (the watcher refuses to start with neither).
- A valid admin bearer in `SONAR_API_TOKEN` (defaults to the dev admin token so a
  local run "just works"; set a real token for prod).

## Start it — pick one

### Foreground (dev, see the logs live)

```bash
bash scripts/sonar-scan-watcher.sh
```

### Background (dev)

```bash
nohup bash scripts/sonar-scan-watcher.sh > /tmp/sonar-watcher.log 2>&1 &
tail -f /tmp/sonar-watcher.log
```

Stop it with `kill <pid>` (it traps SIGINT/SIGTERM and prints a clean stop line).

### macOS — launchd (unattended, this dev box)

Create `~/Library/LaunchAgents/com.projecthub.sonar-watcher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.projecthub.sonar-watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/huseyinkanat/Documents/project-hub/scripts/sonar-scan-watcher.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/huseyinkanat/Documents/project-hub</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/sonar-watcher.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/sonar-watcher.err</string>
</dict>
</plist>
```

Load / unload:

```bash
launchctl load   ~/Library/LaunchAgents/com.projecthub.sonar-watcher.plist
launchctl unload ~/Library/LaunchAgents/com.projecthub.sonar-watcher.plist
```

### Linux — systemd (unattended)

`/etc/systemd/system/sonar-watcher.service`:

```ini
[Unit]
Description=ProjectHub SonarQube scan watcher
After=docker.service

[Service]
Type=simple
WorkingDirectory=/path/to/project-hub
ExecStart=/bin/bash /path/to/project-hub/scripts/sonar-scan-watcher.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sonar-watcher
sudo journalctl -u sonar-watcher -f
```

### Degraded fallback — cron (NOT preferred)

A cron line can poll-and-drain the queue once a minute. It is coarse (1-minute
granularity, no clean `running` signal mid-tick) — use only if a long-running daemon
is impossible:

```cron
* * * * * cd /path/to/project-hub && SONARQUBE_ENABLED=true bash scripts/sonar-scan-watcher.sh --once >> /tmp/sonar-watcher.log 2>&1
```

> Note: the watcher currently loops forever; a true `--once` drain mode is a small
> follow-up if cron is ever required. The daemon (launchd/systemd) is the supported path.

## Configuration

| Env var                | Default                  | Meaning                                            |
|------------------------|--------------------------|----------------------------------------------------|
| `SONARQUBE_ENABLED`    | (unset)                  | Must be `true` or the watcher idles.               |
| `BACKEND_PORT`         | `8000`                   | Backend port for `/api/scans/*`.                   |
| `SONAR_API_TOKEN`      | `change-me-on-first-login` | Admin bearer for `/api/scans/*`.                 |
| `SONAR_WATCH_INTERVAL` | `10`                     | Poll cadence (seconds).                            |

## Verifying it works

1. Configure board KIM (setup persists the `kims` project key) and click **Scan now**.
2. `curl -s -H "Authorization: Bearer <token>" localhost:8000/api/scans/pending` →
   shows the queued job.
3. Start the watcher → it claims, runs the scanner, completes.
4. `localhost:9000/dashboard?id=kims` resolves (project auto-created on first scan).
5. The board health panel flips to `ok` with real metrics within seconds (immediate
   ingest) or within one 300s poll-cron interval at worst (SonarQube async-indexing race).

## Troubleshooting

- **Jobs stuck `queued`** → the watcher is not running. Start it (above). This is the
  honest end-state, never a fake `done`.
- **Job goes `failed` with "no scan ran"** → the runner skipped (sonar disabled /
  unconfigured / unsupported language / SonarQube unreachable). Check the watcher log
  lines (indented scanner output) for the reason.
- **`dashboard?id=<key>` still 404 after a `done` job** → SonarQube indexes
  asynchronously; the immediate poll may race ahead. The 300s poll cron is the backstop —
  the metric appears within one interval.
