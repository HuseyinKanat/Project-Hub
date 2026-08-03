#!/usr/bin/env bash
# scripts/staging-up.sh — PH-333
#
# Bring up the ISOLATED staging clone: schema-only snapshot of the LIVE DB (zero rows,
# zero secrets) + a SYNTHETIC bootstrap seed, on ALTERNATE host ports, in its own
# project namespace (projecthub_staging) with its own volumes.
#
# LIVE-SAFETY INVARIANT: the live `project-hub` project is NEVER mutated. The ONLY
# touch of live is a strictly READ-ONLY `pg_dump --schema-only` via `exec`. Every
# WRITE goes to the projecthub_staging project (distinct volume namespace + no pg/redis
# host port). See docs/staging/README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

STAGING_PROJECT="projecthub_staging"
LIVE_PROJECT="${LIVE_PROJECT:-project-hub}"

# The single reusable staging invocation — carries all four isolation levers
# (project namespace, interpolation env-file, layered override). NEVER a bare
# `docker compose` for staging work.
STG="docker compose -p ${STAGING_PROJECT} --env-file .env.staging -f docker-compose.yml -f docker-compose.staging.yml"
LIVE="docker compose -p ${LIVE_PROJECT}"

SCHEMA_DUMP="$(mktemp -t staging_schema.XXXXXX.sql)"
trap 'rm -f "$SCHEMA_DUMP"' EXIT

log() { printf '\n\033[1;36m[staging-up]\033[0m %s\n' "$*"; }

# --- 1. Preflight ----------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "docker not found on PATH" >&2; exit 1; }
docker info    >/dev/null 2>&1 || { echo "docker daemon not running" >&2; exit 1; }

if [ ! -f .env.staging ]; then
  log "WARN: .env.staging missing — creating it from .env.staging.example."
  log "      Defaults are staging-safe; edit it if you want, then re-run."
  cp .env.staging.example .env.staging
fi

# Source staging env for HOST-side use (ports, staging pg creds, admin token).
set -a; . ./.env.staging; set +a
: "${BACKEND_PORT:?set BACKEND_PORT in .env.staging}"
: "${POSTGRES_USER:?set POSTGRES_USER in .env.staging}"
: "${POSTGRES_DB:?set POSTGRES_DB in .env.staging}"
: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD in .env.staging}"

# --- 2. Data plane (sonar NOT named => excluded, AC3) ----------------------
log "starting staging data plane (postgres + redis)…"
$STG up -d postgres redis

log "waiting for staging postgres to become healthy…"
pg_ok=0
for _ in $(seq 1 30); do
  cid="$($STG ps -q postgres 2>/dev/null || true)"
  if [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" = "healthy" ]; then
    pg_ok=1; break
  fi
  sleep 2
done
[ "$pg_ok" = 1 ] || { echo "staging postgres did not become healthy" >&2; exit 1; }

# --- 3. Schema-only dump from LIVE (READ-ONLY; zero rows, zero secrets) -----
# `exec` on the LIVE container runs pg_dump using the container's OWN credentials
# (single-quoted $POSTGRES_USER/$POSTGRES_DB expand INSIDE the live container), so this
# script needs no knowledge of live creds and can only READ.
if $LIVE ps --status running --services 2>/dev/null | grep -qx postgres; then
  log "dumping LIVE schema (read-only, --schema-only => real shape, ZERO rows)…"
  $LIVE exec -T postgres sh -c \
    'pg_dump --schema-only --no-owner --no-privileges -U "$POSTGRES_USER" "$POSTGRES_DB"' \
    > "$SCHEMA_DUMP"
  log "live schema captured ($(wc -l < "$SCHEMA_DUMP" | tr -d ' ') lines, 0 rows)"
else
  log "live postgres ($LIVE_PROJECT) not running — building schema from alembic only"
  : > "$SCHEMA_DUMP"
fi

# --- 4. Restore schema into STAGING ----------------------------------------
if [ -s "$SCHEMA_DUMP" ]; then
  log "restoring schema into staging postgres…"
  $STG exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    < "$SCHEMA_DUMP" >/dev/null
fi

# --- 5. Reconcile Alembic (stamp head, THEN upgrade head w/ lock_timeout) ---
# Backend service is intentionally still DOWN. A one-off `run --rm --no-deps` container
# performs the migration reconciliation against the schema-present, unstamped, zero-row
# DB. WHY stamp-then-upgrade: a --schema-only restore recreates every table (so a plain
# `upgrade head` would hit `relation already exists`) but leaves alembic_version EMPTY —
# `stamp head` records we are already at head, then `upgrade head` is a clean no-op (and
# is the EXACT lock_timeout'd line PH-334's staging-smoke gate reuses for REAL pending
# migrations). lock_timeout is mandatory (CLAUDE.md): a lock-blocked upgrade otherwise
# hangs SILENTLY and looks like success.
if [ -s "$SCHEMA_DUMP" ]; then
  log "alembic stamp head (schema restored => version table is empty)…"
  $STG run --rm --no-deps -T backend alembic stamp head
fi
log "alembic upgrade head (PGOPTIONS lock_timeout=15s — no-op at head / applies pending)…"
$STG run --rm --no-deps -T -e PGOPTIONS="-c lock_timeout=15s" backend alembic upgrade head

# --- 6. App plane + SYNTHETIC seed -----------------------------------------
log "starting staging app plane (backend + frontend)…"
$STG up -d backend frontend

log "seeding synthetic fixture (workflow + admin + PH board + backlog tickets)…"
# bootstrap reads ADMIN_* / SECRET_KEY from the staging env => admin token is SYNTHETIC
# (from .env.staging ADMIN_PASSWORD), never a live copy — AC4 zero-live-secret holds.
$STG exec -T backend python -m app.cli bootstrap

# --- 7. Verify: /health 200 + >=1 board visible ----------------------------
log "waiting for staging backend /health on :${BACKEND_PORT}…"
health_ok=0
for _ in $(seq 1 45); do
  if curl -fs "http://localhost:${BACKEND_PORT}/health" >/dev/null 2>&1; then health_ok=1; break; fi
  sleep 2
done
[ "$health_ok" = 1 ] || { echo "staging backend /health never came up on :${BACKEND_PORT}" >&2; exit 1; }
log "health => $(curl -fs "http://localhost:${BACKEND_PORT}/health")"

# /api/boards is auth-gated + membership-scoped (PH-327); the bootstrap admin's bearer
# token IS ADMIN_PASSWORD, and bootstrap makes it a member of the PH board.
boards="$(curl -fs -H "Authorization: Bearer ${ADMIN_PASSWORD}" "http://localhost:${BACKEND_PORT}/api/boards" || true)"
log "GET /api/boards => ${boards}"
echo "$boards" | grep -q '"key"' \
  || { echo "no board visible in the staging clone (expected >=1 synthetic board)" >&2; exit 1; }

log "STAGING UP ✅  backend http://localhost:${BACKEND_PORT}  frontend http://localhost:${FRONTEND_PORT:-5183}"
log "live project '${LIVE_PROJECT}' untouched (only a read-only schema dump was taken)."
