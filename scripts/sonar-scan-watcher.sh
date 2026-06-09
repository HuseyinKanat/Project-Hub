#!/usr/bin/env bash
# sonar-scan-watcher.sh — host-side auto-run daemon for per-board SonarQube scans (PH-239).
#
# THE missing piece that makes "Scan now" actually scan. The backend runs INSIDE a
# container and cannot `docker compose run`, so it only PERSISTS a queued SonarScanJob.
# This long-running HOST process closes the loop with NO operator hand-running a script:
#
#   loop forever:
#     GET  /api/scans/pending                  → [{job_id, board_key, project_key}, ...]
#     for each pending job:
#       POST /api/scans/{job_id}/claim         → queued → running (409 if already claimed)
#       bash scripts/sonar-scan-board.sh KEY   → runs the scanner (UNCHANGED runner)
#                                                reads the honest SONAR_SCAN_RESULT marker
#       POST /api/scans/{job_id}/complete      → running → done (success) | failed
#                                                done-success triggers an IMMEDIATE
#                                                backend poll_board ingest of the metrics
#     sleep SONAR_WATCH_INTERVAL
#
# Why a watcher (not a docker-socket mount / cron): the architect's ADR rejected mounting
# /var/run/docker.sock into the backend (root-equiv host control = privilege-escalation
# surface) and rejected cron (coarse granularity, no clean 'running' signal). The watcher
# reports state transitions immediately and is the same amount of host setup.
#
# RUNBOOK — how to start it (pick ONE):
#   foreground (dev):      bash scripts/sonar-scan-watcher.sh
#   background (dev):      nohup bash scripts/sonar-scan-watcher.sh > /tmp/sonar-watcher.log 2>&1 &
#   macOS unattended:      see docs/sonar-watcher.md (launchd plist)
#   linux unattended:      see docs/sonar-watcher.md (systemd unit)
#   degraded fallback:     a cron line is documented in docs/sonar-watcher.md (NOT preferred).
#
# Env (loaded from .env if present — same source the host scripts already use):
#   SONARQUBE_ENABLED      must be "true" or the watcher idles (does nothing, no spam).
#   BACKEND_PORT           backend port for the /api/scans/* calls (default 8000).
#   SONAR_API_TOKEN        admin bearer for /api/scans/* (default dev admin token).
#   SONAR_WATCH_INTERVAL   poll cadence seconds (default 10).
#
# This script NEVER exits non-zero on a transient error (a backend blip / a single bad
# job is logged and the loop continues). Stop it with Ctrl-C / kill (SIGINT/SIGTERM).

set -uo pipefail

log() { echo "[sonar-scan-watcher $(date '+%H:%M:%S')] $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env"
    set +a
fi

: "${BACKEND_PORT:=8000}"
# Dev admin token (matches CLAUDE.md `change-me-on-first-login`); override for prod.
: "${SONAR_API_TOKEN:=change-me-on-first-login}"
: "${SONAR_WATCH_INTERVAL:=10}"

BACKEND="http://localhost:${BACKEND_PORT}"
AUTH=(-H "Authorization: Bearer ${SONAR_API_TOKEN}")

if ! command -v curl >/dev/null 2>&1; then
    log "curl not found — the watcher cannot talk to the backend. Exiting."
    exit 1
fi

# jq preferred for parsing; python3 fallback; the watcher refuses to run with neither.
_have_jq=0
if command -v jq >/dev/null 2>&1; then _have_jq=1; fi
if [ "$_have_jq" -eq 0 ] && ! command -v python3 >/dev/null 2>&1; then
    log "neither jq nor python3 found — cannot parse pending jobs. Exiting."
    exit 1
fi

# Emit one TSV line per pending job: "<job_id>\t<board_key>\t<repo_slug>" (project_key
# not needed by the runner — it re-derives via scan-plans). PH-248: the 3rd column
# repo_slug is forwarded to the runner as its Mode-1 (targeted) 2nd arg so one claimed
# job scans EXACTLY its own repo (GXA's 3 jobs → 3 distinct projects, not 9). A legacy
# board-level job has repo_slug=null → emitted as an EMPTY column → the runner gets no
# 2nd arg → Mode 2 (iterate-all, = pre-PH-248 behavior, back-compat). Empty output = idle.
_parse_pending() {
    if [ "$_have_jq" -eq 1 ]; then
        jq -r '.[] | "\(.job_id)\t\(.board_key)\t\(.repo_slug // "")"' 2>/dev/null
    else
        python3 -c 'import sys,json
try:
    for j in json.load(sys.stdin):
        print(f"{j[\"job_id\"]}\t{j[\"board_key\"]}\t{j.get(\"repo_slug\") or \"\"}")
except Exception:
    pass'
    fi
}

# Trap so a graceful kill prints a clear stop line.
trap 'log "stopping (signal received)."; exit 0' INT TERM

log "started. backend=${BACKEND} interval=${SONAR_WATCH_INTERVAL}s. Polling /api/scans/pending ..."

while true; do
    ENABLED_LC="$(printf '%s' "${SONARQUBE_ENABLED:-}" | tr '[:upper:]' '[:lower:]')"
    if [ "$ENABLED_LC" != "true" ]; then
        # Kill switch respected: idle quietly (no work, no spam) until re-enabled.
        sleep "$SONAR_WATCH_INTERVAL"
        # Re-source .env so toggling SONARQUBE_ENABLED takes effect without a restart.
        if [ -f "$REPO_ROOT/.env" ]; then set -a; . "$REPO_ROOT/.env"; set +a; fi
        continue
    fi

    PENDING="$(curl -fsS -m 10 "${AUTH[@]}" "${BACKEND}/api/scans/pending" 2>/dev/null || true)"
    if [ -z "$PENDING" ] || [ "$PENDING" = "[]" ]; then
        sleep "$SONAR_WATCH_INTERVAL"
        continue
    fi

    while IFS=$'\t' read -r JOB_ID BOARD_KEY REPO_SLUG; do
        [ -z "$JOB_ID" ] && continue
        log "claiming job=${JOB_ID} board=${BOARD_KEY} repo=${REPO_SLUG:-<board-level>} ..."

        # Claim: queued → running. A 409 means another watcher beat us — skip it.
        CLAIM_CODE="$(curl -fsS -m 10 -o /dev/null -w '%{http_code}' -X POST \
            "${AUTH[@]}" "${BACKEND}/api/scans/${JOB_ID}/claim" 2>/dev/null || echo 000)"
        if [ "$CLAIM_CODE" != "200" ]; then
            log "claim job=${JOB_ID} returned HTTP ${CLAIM_CODE} (already claimed / gone) — skipping."
            continue
        fi

        # PH-248: forward repo_slug as the runner's Mode-1 (targeted) 2nd arg so this one
        # claimed job scans EXACTLY its own repo. A legacy board-level job has an empty
        # REPO_SLUG → call the runner with NO 2nd arg → Mode 2 (iterate-all = pre-PH-248
        # behavior, back-compat). The marker → /complete mapping below is UNCHANGED.
        log "running scanner for board=${BOARD_KEY} repo=${REPO_SLUG:-<board-level Mode 2>} ..."
        if [ -n "$REPO_SLUG" ]; then
            SCAN_OUT="$(bash "$SCRIPT_DIR/sonar-scan-board.sh" "$BOARD_KEY" "$REPO_SLUG" 2>&1)"
        else
            SCAN_OUT="$(bash "$SCRIPT_DIR/sonar-scan-board.sh" "$BOARD_KEY" 2>&1)"
        fi
        echo "$SCAN_OUT" | sed 's/^/    /'
        RESULT="$(printf '%s\n' "$SCAN_OUT" | grep -o 'SONAR_SCAN_RESULT=[a-z]*' | tail -1 | cut -d= -f2)"

        # Map the marker → complete payload. ok → success; failed/skipped → failure (a
        # skipped scan produced NO analysis, so it is NOT a success — record it honestly
        # with a detail so the operator sees why nothing ingested).
        if [ "$RESULT" = "ok" ]; then
            BODY='{"success": true, "detail": "scanner ok; ingesting"}'
            log "scanner OK for board=${BOARD_KEY} — completing success (immediate ingest)."
        elif [ "$RESULT" = "failed" ]; then
            BODY='{"success": false, "detail": "scanner exited non-zero"}'
            log "scanner FAILED for board=${BOARD_KEY} — completing failed."
        else
            BODY="{\"success\": false, \"detail\": \"no scan ran (result=${RESULT:-unknown}; disabled/unconfigured/unsupported/unreachable)\"}"
            log "scanner SKIPPED for board=${BOARD_KEY} (result=${RESULT:-unknown}) — completing failed (no analysis)."
        fi

        COMPLETE_CODE="$(curl -fsS -m 30 -o /dev/null -w '%{http_code}' -X POST \
            "${AUTH[@]}" -H 'Content-Type: application/json' -d "$BODY" \
            "${BACKEND}/api/scans/${JOB_ID}/complete" 2>/dev/null || echo 000)"
        log "complete job=${JOB_ID} returned HTTP ${COMPLETE_CODE}."
    done <<< "$(printf '%s' "$PENDING" | _parse_pending)"

    sleep "$SONAR_WATCH_INTERVAL"
done
