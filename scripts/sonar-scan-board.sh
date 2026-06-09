#!/usr/bin/env bash
# sonar-scan-board.sh — best-effort per-board SonarQube analysis (PH-236, C2; PH-248 per-repo).
#
# Sibling of sonar-scan.sh (which scans project-hub post-merge, UNCHANGED). This one
# scans ANY board's own code under its own projectKey. The backend runs inside a
# container and cannot `docker compose run`, so the "Scan now" endpoint only ENQUEUES
# (returns scan_status=queued); THIS host script is the runner that actually invokes
# the scanner.
#
# Usage:  scripts/sonar-scan-board.sh <board-key> [repo-slug]   (e.g. GXA gamexsdk)
#
# It curls the backend scan-plans LIST endpoint
#   GET /api/boards/<key>/sonarqube/scan-plans
# which returns a JSON ARRAY of per-repo plan elements, each
#   { project_key, container_source, host_source, language, supported, reason,
#     exclusions, repo_id, repo_slug }.
# A board may own N linked repos (PH-246: GXA → gamexcore + gamexsdk + gamexandroiddemoapp),
# so this is N elements; a single-repo board (PH/KIM/FN) is a 1-element list.
#
# PH-248 — TWO MODES, selected by the optional 2nd arg <repo-slug>:
#
#   Mode 1 — TARGETED (watcher path, the NORMAL flow): a repo-slug is given. Pick the
#     ONE plan element whose repo_slug matches and scan EXACTLY that repo. One claimed
#     SonarScanJob = one repo = one scanner run, so GXA's 3 jobs scan 3 distinct Sonar
#     projects (NOT 9). The existing per-job /complete already ingests the right repo_id.
#     A slug not found in the list (repo removed between enqueue + run) → log + skipped.
#
#   Mode 2 — ITERATE-ALL (manual / post-merge / legacy no-slug): iterate EVERY plan
#     element, run the scanner once per supported=true repo, skip supported=false repos
#     with an honest log line, and NEVER abort the loop on one repo's failure. Emits a
#     single AGGREGATE marker. Also the back-compat path for a not-yet-updated watcher
#     that forwards no slug (= pre-PH-248 behavior, but now over the LIST endpoint).
#
# Both modes parse the SAME /scan-plans JSON ARRAY (jq → python3 fallback preserved for
# the array shape — no new hard deps). Mode 1 simply filters the list to one element.
#
# HARD RULE (PH-194/PH-208 contract): this script ALWAYS exits 0. Every path
# (disabled / no key / unsupported / unreachable / scan failure) logs and continues.
# A scan problem must NEVER block. `set -e` guards the cheap setup; each scanner run is
# guarded so its non-zero exit is captured + swallowed. The SonarQube TOKEN is loaded
# from .env (gitignored) — NEVER committed, NEVER echoed. NO `timeout` dependency
# (macOS lacks GNU coreutils `timeout`; curl's native `-m` flags are portable).
#
# Env (loaded from .env if present):
#   SONARQUBE_ENABLED   must equal "true" (case-insensitive) or the scan is skipped.
#   SONARQUBE_TOKEN     SonarQube user token, injected into the scanner container.
#   SONARQUBE_URL       compose-internal scanner target (default http://sonarqube:9000).
#   SONARQUBE_PORT      published host port for sonarqube (default 9000).
#   SONAR_SCAN_URL      HOST-reachable sonarqube probe URL (default localhost:PORT).
#   BACKEND_PORT        published backend port for the scan-plans curl (default 8000).
#   SONAR_API_TOKEN     OPTIONAL bearer token for the backend scan-plans call (admin).
#                       Defaults to the dev admin token so a local run "just works".
#
# PH-239 — HONEST RESULT CHANNEL (R4): this script ALWAYS exits 0 (deploy contract), so
# its exit code can't tell a caller whether the SCANNER itself succeeded. For the
# auto-scan watcher (scripts/sonar-scan-watcher.sh) it therefore emits a final
# machine-parseable marker line on stdout:
#       SONAR_SCAN_RESULT=ok        a real scanner run succeeded (RC 0) → ingest
#       SONAR_SCAN_RESULT=failed    the scanner ran but exited non-zero → record failed
#       SONAR_SCAN_RESULT=skipped   no scan happened (disabled/unconfigured/unsupported/
#                                   unreachable) — NOT a failure, the watcher leaves the
#                                   job queued or completes it as failed per its policy
# The marker is the LAST line; the watcher greps for it. In Mode 1 it reflects the single
# targeted repo. In Mode 2 it is an AGGREGATE over the loop: `failed` if ≥1 scanner exited
# non-zero, else `ok` if ≥1 repo scanned ok, else `skipped` (nothing scannable). Adding it
# does NOT change the exit-0 contract.

set -e

log() { echo "[sonar-scan-board] $*"; }

# PH-239: emit the honest result marker (read by the auto-scan watcher) then exit 0.
emit_result() { echo "SONAR_SCAN_RESULT=$1"; exit 0; }

# ---------------------------------------------------------------------------
# Args + repo root + .env.
# ---------------------------------------------------------------------------

BOARD_KEY="${1:-}"
# PH-248: optional 2nd positional arg. Non-empty → Mode 1 (targeted). Empty/absent →
# Mode 2 (iterate-all). An empty-string 2nd arg is treated identically to absent (a
# legacy board-level watcher job has repo_slug=null → forwarded as "").
REPO_SLUG="${2:-}"
if [ -z "$BOARD_KEY" ]; then
    log "usage: scripts/sonar-scan-board.sh <board-key> [repo-slug]  (e.g. GXA gamexsdk) — nothing to do, exiting 0."
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
    emit_result skipped
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
    emit_result skipped
fi
if ! command -v docker >/dev/null 2>&1; then
    log "docker not found — cannot run the scanner container; skipping scan."
    emit_result skipped
fi

# SonarQube must be UP (the scanner uploads to it).
STATUS="$(curl -fsS -m 5 "${SONAR_SCAN_URL}/api/system/status" 2>/dev/null || true)"
if ! printf '%s' "$STATUS" | grep -q '"status":"UP"'; then
    log "SonarQube at ${SONAR_SCAN_URL} not reachable / not UP — skipping scan (no block)."
    emit_result skipped
fi

# ---------------------------------------------------------------------------
# GUARD C — fetch the per-board scan PLANS (LIST) from the backend (single source of
# truth). The backend owns ALL resolution (key + path-translation + language + supported
# + per-repo project_key) — this script is a dumb consumer. PH-248: the runner now reads
# the LIST endpoint (one element per linked repo) instead of the single-object /scan-plan
# (the single-object endpoint is KEPT in the backend as the back-compat net).
# ---------------------------------------------------------------------------

PLANS_URL="http://localhost:${BACKEND_PORT}/api/boards/${BOARD_KEY}/sonarqube/scan-plans"
PLANS="$(curl -fsS -m 10 -H "Authorization: Bearer ${SONAR_API_TOKEN}" "$PLANS_URL" 2>/dev/null || true)"
if [ -z "$PLANS" ]; then
    log "could not fetch scan-plans for board '${BOARD_KEY}' at ${PLANS_URL} (backend down / not admin / unknown board) — skipping (no block)."
    emit_result skipped
fi

# ---------------------------------------------------------------------------
# JSON ARRAY parse — keep the jq → python3 → empty two-tier fallback discipline.
# The payload is now an ARRAY of plan elements (PH-248), not a single object. Each
# helper is independent + lenient (`// empty`) so an additive future field never breaks
# the parse (the same robustness the deployed object-parse had). The `_PLANS` global is
# read by both parse paths (avoids re-passing the payload through every call).
# ---------------------------------------------------------------------------

_PLANS="$PLANS"
_HAVE_JQ=0
if command -v jq >/dev/null 2>&1; then _HAVE_JQ=1; fi

# _plan_count → number of plan elements in the array (0 on malformed input).
_plan_count() {
    if [ "$_HAVE_JQ" -eq 1 ]; then
        printf '%s' "$_PLANS" | jq -r 'if type=="array" then length else 0 end' 2>/dev/null
    elif command -v python3 >/dev/null 2>&1; then
        printf '%s' "$_PLANS" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print(len(d) if isinstance(d,list) else 0)
except Exception:
    print(0)' 2>/dev/null
    else
        echo 0
    fi
}

# _plan_field <index> <key> → the value of element[index][key] ("" if missing/null).
_plan_field() {
    _i="$1"; _k="$2"
    if [ "$_HAVE_JQ" -eq 1 ]; then
        printf '%s' "$_PLANS" | jq -r --argjson i "$_i" --arg k "$_k" '.[$i][$k] // empty' 2>/dev/null
    elif command -v python3 >/dev/null 2>&1; then
        printf '%s' "$_PLANS" | I="$_i" K="$_k" python3 -c 'import sys,json,os
try:
    d=json.load(sys.stdin); i=int(os.environ["I"]); k=os.environ["K"]
    v=d[i].get(k)
    print("" if v is None else v)
except Exception:
    pass' 2>/dev/null
    else
        echo ""
    fi
}

# _plan_index_by_slug <slug> → the 0-based index of the first element whose repo_slug
# matches ("" if none). Used by Mode 1 to filter the list to one element.
_plan_index_by_slug() {
    _s="$1"
    if [ "$_HAVE_JQ" -eq 1 ]; then
        printf '%s' "$_PLANS" | jq -r --arg s "$_s" 'map(.repo_slug) | index($s) // empty' 2>/dev/null
    elif command -v python3 >/dev/null 2>&1; then
        printf '%s' "$_PLANS" | S="$_s" python3 -c 'import sys,json,os
try:
    d=json.load(sys.stdin); s=os.environ["S"]
    slugs=[e.get("repo_slug") for e in d]
    print(slugs.index(s) if s in slugs else "")
except Exception:
    pass' 2>/dev/null
    else
        echo ""
    fi
}

PLAN_COUNT="$(_plan_count)"
if [ -z "$PLAN_COUNT" ] || [ "$PLAN_COUNT" -lt 1 ] 2>/dev/null; then
    log "scan-plans for board '${BOARD_KEY}' is empty / not an array — nothing scannable, skipping (no block)."
    emit_result skipped
fi

if [ -z "${SONARQUBE_TOKEN:-}" ]; then
    log "WARNING: SONARQUBE_TOKEN is empty — the scan will likely fail auth, but proceeding best-effort."
fi

# ---------------------------------------------------------------------------
# run_scanner <project_key> <container_source> <exclusions> — invoke sonar-scanner for
# ONE repo (PH-248 extracted helper; returns the scanner RC, does NOT exit). Carries the
# PH-242/243/244 `-D` props VERBATIM, just parameterized per-repo. The ONLY delta vs the
# pre-PH-248 single invocation is that the values now come from a per-repo plan element.
#
# PH-243 — CONFIG ISOLATION: -Dsonar.projectBaseDir pins the board's own source root so
# the scanner does NOT load project-hub's /usr/src/sonar-project.properties (the compose
# working_dir), and -Dsonar.tests= (explicit empty) guards PH's backend/tests,tests from
# ever bleeding into a board scan. The absolute -Dsonar.sources still resolves regardless.
# PH-244 — static -Dsonar.java.binaries=. (board root, always exists) so SonarQube's
# JavaSensor never HARD-ABORTS on a stray .java. -Dsonar.scm.disabled avoids needing the
# board's VCS in the container. NONE of these flags added/removed — board-scan-only (the
# SIBLING sonar-scan.sh / PH self-scan does NOT carry java.binaries / baseDir-pin).
# ---------------------------------------------------------------------------

run_scanner() {
    _pk="$1"; _src="$2"; _excl="$3"
    log "Scanning projectKey=${_pk} (sources=${_src}, baseDir=${_src}) ..."
    _rc=0
    set +e
    SONAR_HOST_URL="$SONARQUBE_URL" SONAR_TOKEN="${SONARQUBE_TOKEN:-}" \
        docker compose --profile scan run --rm \
        -e SONAR_HOST_URL="$SONARQUBE_URL" \
        -e SONAR_TOKEN="${SONARQUBE_TOKEN:-}" \
        sonar-scanner \
        -Dsonar.projectBaseDir="$_src" \
        -Dsonar.projectKey="$_pk" \
        -Dsonar.projectName="$_pk" \
        -Dsonar.sources="$_src" \
        -Dsonar.tests= \
        -Dsonar.exclusions="${_excl:-}" \
        -Dsonar.java.binaries=. \
        -Dsonar.scm.disabled=true
    _rc=$?
    set -e
    return "$_rc"
}

# scan_one <index> — read element[index]'s fields, validate, run the scanner. Echoes a
# per-repo outcome word (ok|failed|skipped) on stdout (the LAST line of its output) so the
# caller can aggregate. Never exits, never aborts the caller.
scan_one() {
    _idx="$1"
    _pk="$(_plan_field "$_idx" project_key)"
    _src="$(_plan_field "$_idx" container_source)"
    _lang="$(_plan_field "$_idx" language)"
    _sup="$(_plan_field "$_idx" supported)"
    _reason="$(_plan_field "$_idx" reason)"
    _excl="$(_plan_field "$_idx" exclusions)"
    _slug="$(_plan_field "$_idx" repo_slug)"

    _sup_lc="$(printf '%s' "$_sup" | tr '[:upper:]' '[:lower:]')"
    if [ "$_sup_lc" != "true" ]; then
        log "repo '${_slug:-?}' (${_lang:-unknown}) is NOT scannable: ${_reason:-unsupported / unconfigured} — skipping this repo (no scan)."
        echo skipped
        return 0
    fi
    if [ -z "$_pk" ] || [ -z "$_src" ]; then
        log "repo '${_slug:-?}' plan missing project_key/container_source — skipping this repo (no block)."
        echo skipped
        return 0
    fi

    if run_scanner "$_pk" "$_src" "$_excl"; then
        log "Scan complete — '${_pk}' analysis uploaded. The watcher will ingest it via /complete (poll cron is the backstop)."
        echo ok
    else
        log "WARNING: sonar-scanner exited non-zero for projectKey=${_pk} (repo '${_slug:-?}') — analysis NOT refreshed. NOT blocked."
        echo failed
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Mode dispatch.
# ---------------------------------------------------------------------------

if [ -n "$REPO_SLUG" ]; then
    # -------- Mode 1 — TARGETED (watcher path). One job = one repo = one scanner run.
    log "Mode 1 (targeted): board='${BOARD_KEY}' repo-slug='${REPO_SLUG}' (1 of ${PLAN_COUNT} plan element(s))."
    IDX="$(_plan_index_by_slug "$REPO_SLUG")"
    if [ -z "$IDX" ]; then
        log "repo-slug '${REPO_SLUG}' not found in board '${BOARD_KEY}' scan-plans (repo removed between enqueue + run?) — skipping (no block)."
        emit_result skipped
    fi
    # scan_one's LAST stdout line is the per-repo outcome word; the log lines precede it.
    OUT="$(scan_one "$IDX")"
    printf '%s\n' "$OUT" | sed '$d'  # echo scan_one's log lines (everything but the marker word)
    RESULT="$(printf '%s\n' "$OUT" | tail -1)"
    emit_result "${RESULT:-skipped}"
fi

# -------- Mode 2 — ITERATE-ALL (manual / post-merge / legacy no-slug). Never-abort.
log "Mode 2 (iterate-all): board='${BOARD_KEY}', ${PLAN_COUNT} plan element(s)."
N_OK=0
N_FAILED=0
N_SKIPPED=0
i=0
while [ "$i" -lt "$PLAN_COUNT" ]; do
    OUT="$(scan_one "$i")"
    printf '%s\n' "$OUT" | sed '$d'  # echo scan_one's log lines (everything but the marker word)
    R="$(printf '%s\n' "$OUT" | tail -1)"
    case "$R" in
        ok)      N_OK=$((N_OK + 1)) ;;
        failed)  N_FAILED=$((N_FAILED + 1)) ;;
        *)       N_SKIPPED=$((N_SKIPPED + 1)) ;;
    esac
    i=$((i + 1))
done

log "Mode 2 summary for board '${BOARD_KEY}': ok=${N_OK} failed=${N_FAILED} skipped=${N_SKIPPED} (of ${PLAN_COUNT})."

# Aggregate marker: a real failure (≥1 non-zero scanner) is surfaced as `failed` so a
# human sees it; else `ok` if ≥1 repo scanned; else `skipped` (nothing scannable).
if [ "$N_FAILED" -gt 0 ]; then
    emit_result failed
elif [ "$N_OK" -gt 0 ]; then
    emit_result ok
else
    emit_result skipped
fi
