#!/usr/bin/env bash
# scripts/staging-refresh.sh — PH-333
#
# Idempotent staging refresh: tear the staging instance FULLY down (staging-scoped
# volumes ONLY) then re-seed from a fresh schema-only snapshot + synthetic fixture.
# Running it twice yields the identical clean state.
#
# LIVE-SAFETY INVARIANT: `down -v` here removes ONLY projecthub_staging_* volumes
# because of `-p projecthub_staging`. A BARE `docker compose down -v` in this repo would
# nuke the LIVE `project-hub_*` volumes — the hard guard below refuses to run without
# the staging project name.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

STAGING_PROJECT="projecthub_staging"
STG="docker compose -p ${STAGING_PROJECT} --env-file .env.staging -f docker-compose.yml -f docker-compose.staging.yml"

# HARD GUARD — never `down -v` without the staging project namespace.
case " $STG " in
  *" -p ${STAGING_PROJECT} "*) : ;;
  *) echo "refusing to run: staging invocation is missing -p ${STAGING_PROJECT}" >&2; exit 1 ;;
esac

log() { printf '\n\033[1;33m[staging-refresh]\033[0m %s\n' "$*"; }

if [ ! -f .env.staging ]; then
  log "no .env.staging yet — nothing to tear down; staging-up.sh will create it."
else
  log "tearing down staging (down -v => ONLY projecthub_staging_* volumes; live untouched)…"
  $STG down -v --remove-orphans
fi

log "re-seeding via staging-up.sh…"
exec "${SCRIPT_DIR}/staging-up.sh"
