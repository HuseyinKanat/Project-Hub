#!/usr/bin/env bash
# sonar-scan-board.sh — best-effort per-board SonarQube analysis (PH-236, C2).
#
# Sibling of sonar-scan.sh (which scans project-hub post-merge, UNCHANGED). This one
# scans ANY board's own code under its own projectKey. The backend runs inside a
# container and cannot `docker compose run`, so the "Scan now" endpoint only ENQUEUES
# (returns scan_status=queued); THIS host script is the runner that actually invokes
# the scanner.
#
# Usage:  scripts/sonar-scan-board.sh <board-key>      (e.g. GXA, FN, KIM)
#
# It curls the backend scan-plan endpoint
#   GET /api/boards/<key>/sonarqube/scan-plan
# which returns { project_key, container_source, host_source, language, supported,
# reason, exclusions }. If supported it runs the scanner with per-board -D props
# against container_source (/repos/<path>); if NOT supported (e.g. C#/Unity in
# Community Edition) it logs the reason and exits 0 (no scan).
#
# HARD RULE (PH-194/PH-208 contract): this script ALWAYS exits 0. Every path
# (disabled / no key / unsupported / unreachable / scan failure) logs and continues.
# A scan problem must NEVER block. `set -e` guards the cheap setup; the scanner run is
# guarded so its non-zero exit is captured + swallowed. The SonarQube TOKEN is loaded
# from .env (gitignored) — NEVER committed, NEVER echoed.
#
# Env (loaded from .env if present):
#   SONARQUBE_ENABLED   must equal "true" (case-insensitive) or the scan is skipped.
#   SONARQUBE_TOKEN     SonarQube user token, injected into the scanner container.
#   SONARQUBE_URL       compose-internal scanner target (default http://sonarqube:9000).
#   SONARQUBE_PORT      published host port for sonarqube (default 9000).
#   SONAR_SCAN_URL      HOST-reachable sonarqube probe URL (default localhost:PORT).
#   BACKEND_PORT        published backend port for the scan-plan curl (default 8000).
#   SONAR_API_TOKEN     OPTIONAL bearer token for the backend scan-plan call (admin).
#                       Defaults to the dev admin token so a local run "just works".

set -e

log() { echo "[sonar-scan-board] $*"; }

# ---------------------------------------------------------------------------
# Args + repo root + .env.
# ---------------------------------------------------------------------------

BOARD_KEY="${1:-}"
if [ -z "$BOARD_KEY" ]; then
    log "usage: scripts/sonar-scan-board.sh <board-key>  (e.g. GXA) — nothing to do, exiting 0."
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env"
    set +a
fi

# ---------------------------------------------------------------------------
# GUARD A — enable flag. Skip (exit 0) unless SONARQUBE_ENABLED == true.
# ---------------------------------------------------------------------------

ENABLED_LC="$(printf '%s' "${SONARQUBE_ENABLED:-}" | tr '[:upper:]' '[:lower:]')"
if [ "$ENABLED_LC" != "true" ]; then
    log "SONARQUBE_ENABLED is not 'true' (got '${SONARQUBE_ENABLED:-unset}') — skipping scan."
    exit 0
fi

# ---------------------------------------------------------------------------
# GUARD B — tooling + reachability.
# ---------------------------------------------------------------------------

: "${SONARQUBE_URL:=http://sonarqube:9000}"
: "${SONARQUBE_PORT:=9000}"
: "${SONAR_SCAN_URL:=http://localhost:${SONARQUBE_PORT}}"
: "${BACKEND_PORT:=8000}"
# The dev admin token (matches CLAUDE.md `change-me-on-first-login`); override for prod.
: "${SONAR_API_TOKEN:=change-me-on-first-login}"

if ! command -v curl >/dev/null 2>&1; then
    log "curl not found — cannot fetch the scan plan; skipping scan."
    exit 0
fi
if ! command -v docker >/dev/null 2>&1; then
    log "docker not found — cannot run the scanner container; skipping scan."
    exit 0
fi

# SonarQube must be UP (the scanner uploads to it).
STATUS="$(curl -fsS -m 5 "${SONAR_SCAN_URL}/api/system/status" 2>/dev/null || true)"
if ! printf '%s' "$STATUS" | grep -q '"status":"UP"'; then
    log "SonarQube at ${SONAR_SCAN_URL} not reachable / not UP — skipping scan (no block)."
    exit 0
fi

# ---------------------------------------------------------------------------
# GUARD C — fetch the per-board scan plan from the backend (single source of truth).
# The backend owns ALL resolution (key + path-translation + language + supported);
# this script is a dumb consumer.
# ---------------------------------------------------------------------------

PLAN_URL="http://localhost:${BACKEND_PORT}/api/boards/${BOARD_KEY}/sonarqube/scan-plan"
PLAN="$(curl -fsS -m 10 -H "Authorization: Bearer ${SONAR_API_TOKEN}" "$PLAN_URL" 2>/dev/null || true)"
if [ -z "$PLAN" ]; then
    log "could not fetch scan-plan for board '${BOARD_KEY}' at ${PLAN_URL} (backend down / not admin / unknown board) — skipping (no block)."
    exit 0
fi

# Parse the FROZEN scan-plan JSON. Prefer jq; fall back to python3; never depend on a
# token-bearing field (the plan is secret-free by contract).
_json_get() {
    # $1 = key
    if command -v jq >/dev/null 2>&1; then
        printf '%s' "$PLAN" | jq -r --arg k "$1" '.[$k] // empty' 2>/dev/null
    elif command -v python3 >/dev/null 2>&1; then
        printf '%s' "$PLAN" | python3 -c "import sys,json;d=json.load(sys.stdin);v=d.get('$1');print('' if v is None else v)" 2>/dev/null
    else
        echo ""
    fi
}

PROJECT_KEY="$(_json_get project_key)"
CONTAINER_SOURCE="$(_json_get container_source)"
LANGUAGE="$(_json_get language)"
SUPPORTED="$(_json_get supported)"
REASON="$(_json_get reason)"
EXCLUSIONS="$(_json_get exclusions)"

# supported must be exactly "true" (the JSON bool serialized by jq/python as true/True).
SUPPORTED_LC="$(printf '%s' "$SUPPORTED" | tr '[:upper:]' '[:lower:]')"
if [ "$SUPPORTED_LC" != "true" ]; then
    log "board '${BOARD_KEY}' (${LANGUAGE:-unknown}) is NOT scannable: ${REASON:-unsupported / unconfigured} — skipping scan (exit 0)."
    exit 0
fi
if [ -z "$PROJECT_KEY" ] || [ -z "$CONTAINER_SOURCE" ]; then
    log "board '${BOARD_KEY}' scan-plan missing project_key/container_source — skipping (no block)."
    exit 0
fi

if [ -z "${SONARQUBE_TOKEN:-}" ]; then
    log "WARNING: SONARQUBE_TOKEN is empty — the scan will likely fail auth, but proceeding best-effort."
fi

# ---------------------------------------------------------------------------
# Run the scanner with PER-BOARD -D props (best-effort). The sonar-scanner compose
# service mounts the board code at /repos (PH-236 compose change), so an absolute
# -Dsonar.sources=/repos/<path> resolves regardless of WORKDIR (/usr/src). No
# sonar-project.properties is used for a board scan — the -D overrides supply
# everything. -Dsonar.scm.disabled avoids needing the board's VCS in the container.
# ---------------------------------------------------------------------------

log "Scanning board '${BOARD_KEY}' (projectKey=${PROJECT_KEY}, language=${LANGUAGE:-auto}, sources=${CONTAINER_SOURCE}) ..."

SCAN_RC=0
set +e
SONAR_HOST_URL="$SONARQUBE_URL" SONAR_TOKEN="${SONARQUBE_TOKEN:-}" \
    docker compose --profile scan run --rm \
    -e SONAR_HOST_URL="$SONARQUBE_URL" \
    -e SONAR_TOKEN="${SONARQUBE_TOKEN:-}" \
    sonar-scanner \
    -Dsonar.projectKey="$PROJECT_KEY" \
    -Dsonar.projectName="$PROJECT_KEY" \
    -Dsonar.sources="$CONTAINER_SOURCE" \
    -Dsonar.exclusions="${EXCLUSIONS:-}" \
    -Dsonar.scm.disabled=true
SCAN_RC=$?
set -e

if [ "$SCAN_RC" -ne 0 ]; then
    log "WARNING: sonar-scanner exited ${SCAN_RC} for projectKey=${PROJECT_KEY} — analysis NOT refreshed. NOT blocked."
else
    log "Scan complete — '${PROJECT_KEY}' analysis uploaded. The PH-193 poll cron (or a sync) will ingest on its next tick."
fi

# HARD RULE: never propagate a scanner failure.
exit 0
