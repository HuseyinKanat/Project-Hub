#!/usr/bin/env bash
# sonar-scan.sh — best-effort SonarQube main-branch analysis for project-hub (PH-194).
#
# Invoked from the project CLAUDE.md `## Post-done deployment (override)` block
# (post_merge_commands), i.e. AFTER the Coordinator merges a done ticket's branch to
# main. Refreshes the `project-hub` SonarQube project so the PH-193 poll cron ingests
# fresh measures on its next tick.
#
# HARD RULE: this script ALWAYS exits 0. Every path (disabled, unreachable, scan
# failure) logs and continues. A scanner problem must NEVER block or roll back a
# deploy. `set -e` is used for the cheap setup, but the scanner invocation is run
# guarded so its non-zero exit is captured, logged, and swallowed.
#
# Runs the scanner via Docker (sonarsource/sonar-scanner-cli) — no host install
# needed — using `docker compose --profile scan run` so the container joins the
# project-hub compose network where `sonarqube:9000` resolves.
#
# Env (loaded from .env if present; .env is gitignored — never commit a token):
#   SONARQUBE_ENABLED   must equal "true" (case-insensitive) or the scan is skipped.
#   SONARQUBE_TOKEN     SonarQube user token, injected into the scanner container.
#   SONARQUBE_URL       compose-internal scanner target (default http://sonarqube:9000).
#   SONARQUBE_PORT      published host port for sonarqube (default 9000).
#   SONAR_SCAN_URL      HOST-reachable probe URL (default http://localhost:${SONARQUBE_PORT}).
#                       Used only for the host-side reachability curl; the scanner
#                       container itself talks to SONARQUBE_URL on the compose network.
#
# Exit codes: ALWAYS 0. (Setup/usage errors before the guards also exit 0 — a deploy
# must not break because the scan harness has an env quirk.)
#
# Full runbook (token bootstrap, vm.max_map_count): see .env.example + PH-197.

set -e

log() { echo "[sonar-scan] $*"; }

# ---------------------------------------------------------------------------
# Resolve repo root (script lives in <root>/scripts/) and load .env if present.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/.env" ]; then
    # Export everything sourced so child processes (docker compose) inherit it,
    # without committing any secret (.env is gitignored).
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
# GUARD B — reachability. Probe the HOST-reachable URL; skip (exit 0) if not UP.
# ---------------------------------------------------------------------------

: "${SONARQUBE_URL:=http://sonarqube:9000}"
: "${SONARQUBE_PORT:=9000}"
: "${SONAR_SCAN_URL:=http://localhost:${SONARQUBE_PORT}}"

if ! command -v curl >/dev/null 2>&1; then
    log "curl not found — cannot probe SonarQube reachability; skipping scan."
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    log "docker not found — cannot run the scanner container; skipping scan."
    exit 0
fi

# `|| true` keeps `set -e` from killing us on a failed probe; we branch on STATUS.
STATUS="$(curl -fsS -m 5 "${SONAR_SCAN_URL}/api/system/status" 2>/dev/null || true)"
if ! printf '%s' "$STATUS" | grep -q '"status":"UP"'; then
    log "SonarQube at ${SONAR_SCAN_URL} not reachable / not UP — skipping scan (deploy continues)."
    exit 0
fi

if [ -z "${SONARQUBE_TOKEN:-}" ]; then
    log "WARNING: SONARQUBE_TOKEN is empty — the scan will likely fail auth, but we proceed best-effort."
fi

# ---------------------------------------------------------------------------
# Run the scanner (best-effort). Container joins the compose network via the
# profile-gated `sonar-scanner` service so its SONAR_HOST_URL=$SONARQUBE_URL
# (compose-internal sonarqube:9000) resolves. Any non-zero exit is logged as a
# WARNING and swallowed — we STILL exit 0.
# ---------------------------------------------------------------------------

log "Starting main-branch scan (projectKey=project-hub, target=${SONARQUBE_URL}) ..."

SCAN_RC=0
# Disable set -e around the scan so a scanner failure cannot propagate.
set +e
SONAR_HOST_URL="$SONARQUBE_URL" SONAR_TOKEN="${SONARQUBE_TOKEN:-}" \
    docker compose --profile scan run --rm \
    -e SONAR_HOST_URL="$SONARQUBE_URL" \
    -e SONAR_TOKEN="${SONARQUBE_TOKEN:-}" \
    sonar-scanner
SCAN_RC=$?
set -e

if [ "$SCAN_RC" -ne 0 ]; then
    log "WARNING: sonar-scanner exited ${SCAN_RC} — analysis NOT refreshed. Deploy is NOT blocked."
else
    log "Scan complete — 'project-hub' analysis refreshed. PH-193 poll cron will ingest on its next tick."
fi

# HARD RULE: never propagate a scanner failure to the caller.
exit 0
