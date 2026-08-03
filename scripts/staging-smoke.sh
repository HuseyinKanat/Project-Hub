#!/usr/bin/env bash
# scripts/staging-smoke.sh — PH-334
#
# PH-BOARD SELF-DEV DEPLOY GATE. Smoke-validates the locally-merged (NOT-yet-pushed) code
# + any PENDING migration against the ISOLATED staging clone (PH-333) and yields a
# GREEN/RED signal via its EXIT CODE — nothing else. It is run BY the Coordinator inside
# `~/Jarwis/contracts/exit-protocol.md` §8, AFTER the LOCAL `git merge --no-ff <branch>`
# into repo-root main and BEFORE the irreversible `git push origin main` + live
# `alembic upgrade head` + `docker compose restart backend`:
#
#   exit 0  = GREEN → Coordinator proceeds to push + live deploy (§8 b-e, unchanged)
#   exit !0 = RED   → Coordinator `git reset --hard PREV_MAIN` (un-merge; nothing was
#                     pushed, LIVE never touched) + bounce ticket to in_progress + report
#
# The git side (reset/push/restart) is the Coordinator's job, NOT this script's — this
# script ONLY tells you whether it is safe to deploy live.
#
# ── WHY THE ALEMBIC-FORWARD PATH (the entire point of this gate) ─────────────────────
# staging-up.sh restores a `pg_dump --schema-only` of LIVE, so its tables already exist
# and it must `stamp head` THEN `upgrade head` (a no-op) to reconcile alembic_version.
# That is CORRECT for standing an instance up — but it is a TRAP for a gate: `stamp head`
# marks the ticket's PENDING revision as already-applied, so the following `upgrade head`
# NO-OPS and the migration NEVER RUNS ⇒ a FALSE GREEN (the exact silent-success class
# this gate exists to catch). So this gate deliberately does NOT stamp and does NOT
# restore the live schema: it tears staging down to an EMPTY DB (`down -v`) and runs
# `alembic upgrade head` base→head, so every PENDING revision GENUINELY EXECUTES.
# `lock_timeout` is mandatory (CLAUDE.md discipline): a lock-blocked `upgrade head`
# otherwise hangs SILENTLY, printing only `Running upgrade …` — it LOOKS like success.
#
# ── LIVE-SAFETY ──────────────────────────────────────────────────────────────────────
# Every command carries `-p projecthub_staging` (the staging volume namespace); a hard
# guard refuses to run if that namespace is ever missing. `down -v` therefore wipes ONLY
# `projecthub_staging_*` volumes — the live `project-hub_*` volumes are never touched.
# Unlike staging-up.sh this gate does not even READ live (no pg_dump): it builds the
# schema purely from alembic, so there is zero live coupling.
#
# ── EXIT CODES (any non-zero = RED = do NOT deploy live) ─────────────────────────────
#   0  GREEN  — pending migration applied + /health 200 + GET /api/boards returned a board
#   1  RED    — migration failed / lock_timeout (the pending revision did NOT apply)
#   2  RED    — app plane / /health / critical endpoint failed (app / DB / ORM / auth)
#   3  RED    — misconfig (missing staging namespace, docker down, or .env.staging/vars)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

STAGING_PROJECT="projecthub_staging"

# The single reusable staging invocation — VERBATIM from staging-up.sh:24 /
# staging-refresh.sh:19 (all four isolation levers). NEVER a bare `docker compose`.
STG="docker compose -p ${STAGING_PROJECT} --env-file .env.staging -f docker-compose.yml -f docker-compose.staging.yml"

log() { printf '\n\033[1;35m[staging-smoke]\033[0m %s\n' "$*"; }
red() { printf '\n\033[1;31m[staging-smoke RED]\033[0m %s\n' "$*" >&2; }

# ── 0. Misconfig guards (exit 3) ─────────────────────────────────────────────────────
# HARD GUARD (reused from staging-refresh.sh:22-25): never operate without the staging
# project namespace. A bare `docker compose down -v` in this repo would nuke the LIVE
# project-hub_* volumes — this refuses to proceed if `-p projecthub_staging` is ever lost.
case " $STG " in
  *" -p ${STAGING_PROJECT} "*) : ;;
  *) red "staging invocation missing -p ${STAGING_PROJECT} — refusing (would risk LIVE volumes)"; exit 3 ;;
esac

command -v docker >/dev/null 2>&1 || { red "docker not found on PATH"; exit 3; }
docker info >/dev/null 2>&1        || { red "docker daemon not running"; exit 3; }

# Reuse staging-up.sh:36-40 — auto-create .env.staging from the committed example
# (staging-safe defaults) so the gate is self-contained. Only a TRUE misconfig (example
# also missing) is exit 3.
if [ ! -f .env.staging ]; then
  if [ -f .env.staging.example ]; then
    log "no .env.staging — creating it from .env.staging.example (staging-safe defaults)…"
    cp .env.staging.example .env.staging
  else
    red ".env.staging AND .env.staging.example both missing — cannot configure staging"
    exit 3
  fi
fi

# Source staging env for HOST-side use (port + admin bearer) — staging-up.sh:42-47.
set -a; . ./.env.staging; set +a
if [ -z "${BACKEND_PORT:-}" ] || [ -z "${ADMIN_PASSWORD:-}" ]; then
  red "BACKEND_PORT / ADMIN_PASSWORD not set in .env.staging"
  exit 3
fi

# ── 1. FRESH staging: down -v (staging-scoped ONLY) ⇒ EMPTY DB ───────────────────────
# The leading `down -v` is what GUARANTEES the alembic-forward path: an empty DB with an
# empty alembic_version means `upgrade head` runs base→head and the pending revision
# genuinely EXECUTES. `-p projecthub_staging` scopes the wipe to staging; live untouched.
log "tearing down any prior staging (down -v ⇒ ONLY projecthub_staging_* volumes; live untouched)…"
$STG down -v --remove-orphans || { red "staging teardown failed (docker/compose issue)"; exit 3; }

log "starting staging data plane (postgres + redis)…"
$STG up -d postgres redis || { red "staging data plane failed to start"; exit 3; }

log "waiting for staging postgres to become healthy…"   # staging-up.sh:53-62
pg_ok=0
for _ in $(seq 1 30); do
  cid="$($STG ps -q postgres 2>/dev/null || true)"
  if [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" = "healthy" ]; then
    pg_ok=1; break
  fi
  sleep 2
done
[ "$pg_ok" = 1 ] || { red "staging postgres did not become healthy"; exit 3; }

# ── 2. THE GATE: apply the PENDING migration FORWARD (base→head) ─────────────────────
# EXACTLY the lock_timeout'd line staging-up.sh:99-100 pre-designated for PH-334 (see the
# comment at staging-up.sh:92), but with NO preceding `stamp head` — so against the empty
# DB this genuinely runs every pending revision. A lock-blocked apply surfaces as
# `canceling statement due to lock timeout` (non-zero) instead of a silent hang.
log "alembic upgrade head — FORWARD path, PGOPTIONS lock_timeout=15s (pending migration EXECUTES)…"
if ! $STG run --rm --no-deps -T -e PGOPTIONS="-c lock_timeout=15s" backend alembic upgrade head; then
  red "migration failed or lock-timed-out — the pending revision did NOT apply cleanly. Live deploy BLOCKED."
  exit 1
fi

# ── 3. App plane + synthetic seed (exit 2 on failure) ────────────────────────────────
log "starting staging app plane (backend + frontend)…"
$STG up -d backend frontend || { red "staging app plane failed to start (backend/frontend build or boot)"; exit 2; }

log "seeding synthetic fixture (workflow + admin + PH board + backlog)…"   # staging-up.sh:106-109
$STG exec -T backend python -m app.cli bootstrap \
  || { red "synthetic bootstrap seed failed — schema/app cannot accept the standard seed"; exit 2; }

# ── 4. Assert /health == 200 (exit 2) ────────────────────────────────────────────────
log "waiting for staging backend /health on :${BACKEND_PORT}…"   # staging-up.sh:112-119
health_ok=0
for _ in $(seq 1 45); do
  if curl -fs "http://localhost:${BACKEND_PORT}/health" >/dev/null 2>&1; then health_ok=1; break; fi
  sleep 2
done
if [ "$health_ok" != 1 ]; then
  red "backend /health never returned 200 on :${BACKEND_PORT} — app did not come up. Live deploy BLOCKED."
  exit 2
fi
log "health ⇒ $(curl -fs "http://localhost:${BACKEND_PORT}/health")"

# ── 5. Assert 1 critical DB-backed endpoint (exit 2) ─────────────────────────────────
# GET /api/boards is auth-gated + membership-scoped (PH-327) AND DB-backed: a broken
# migration / ORM regression / auth regression surfaces here, not just liveness. Same
# probe as staging-up.sh:121-126 with the synthetic admin bearer (bootstrap makes the
# admin a PH member ⇒ deterministic ≥1 board).
log "GET /api/boards (Bearer ADMIN_PASSWORD) on :${BACKEND_PORT}…"
boards="$(curl -fs -H "Authorization: Bearer ${ADMIN_PASSWORD}" "http://localhost:${BACKEND_PORT}/api/boards" || true)"
if ! printf '%s' "$boards" | grep -q '"key"'; then
  red "critical endpoint GET /api/boards did not return a board ('\"key\"' missing) — DB/ORM/auth regression. Live deploy BLOCKED."
  red "response was: ${boards:-<empty>}"
  exit 2
fi

log "GREEN ✅  pending migration applied + /health 200 + /api/boards ok — SAFE to deploy live."
log "(staging left running on :${BACKEND_PORT} for inspection; the next gate run's leading 'down -v' resets it.)"
exit 0
