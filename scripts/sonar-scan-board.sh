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
# PH-257 — C# (Unity) path: a plan element with language=="csharp" is dispatched to the
# HOST-side SonarScanner for .NET (run_dotnet_scanner: Unity SyncSolution → dotnet-
# sonarscanner begin → dotnet build → end) instead of the container sonar-scanner-cli (the
# CLI is a stub for C#). It uses the element's HOST_SOURCE path (not container_source).
# Missing dotnet SDK / dotnet-sonarscanner / Unity .sln → HONEST 'skipped' (never a fake
# 'ok'); the backend already gates csharp on SONAR_DOTNET_ENABLED so the host runner only
# sees a csharp job once the operator declared the prerequisite ready.
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

# ---------------------------------------------------------------------------
# run_dotnet_scanner <project_key> <host_source> <exclusions> <slug> — PH-257: real C#
# (Unity) analysis via the HOST-side SonarScanner for .NET (begin → dotnet build → end).
#
# The container sonar-scanner-cli CANNOT analyze C# (empirical probe: indexes .cs but
# reports 0 ncloc / 0 issues — a stub), so a csharp repo is analyzed here on the HOST:
#   1) GUARD: dotnet SDK + dotnet-sonarscanner + a real host_source dir must exist; any
#      missing → HONEST skip (return 2 → caller emits 'skipped', NEVER a fake 'ok').
#   2) Unity has NO committed .sln/.csproj (generated). If absent, generate it via the
#      Unity editor batch-mode UnityEditor.SyncVS.SyncSolution (editor version read from
#      ProjectSettings/ProjectVersion.txt). If a .sln still can't be produced → skip.
#   3) SonarScanner.MSBuild begin (HOST-reachable sonar url — NOT the compose-internal
#      hostname) → dotnet build <sln> → end. Real C# ncloc + issues upload to SonarQube.
#
# Returns: 0 = scanned ok, 1 = scanner ran but failed, 2 = honest skip (precondition
# missing). The caller maps 2 → 'skipped', 1 → 'failed', 0 → 'ok'. SECRET-FREE: the token
# is passed as a /d: arg, NEVER echoed; no shell tracing is enabled.
# ---------------------------------------------------------------------------

run_dotnet_scanner() {
    _pk="$1"; _host="$2"; _excl="$3"; _slug="$4"

    # GUARD 1 — host toolchain. The container backend can't see the host's dotnet, so the
    # backend already gated csharp on SONAR_DOTNET_ENABLED; here we independently re-check
    # the ACTUAL tools and honest-skip if the operator enabled the flag but didn't install.
    if ! command -v dotnet >/dev/null 2>&1; then
        log "repo '${_slug:-?}' (csharp): 'dotnet' not found on host — install the .NET SDK (brew install --cask dotnet-sdk). Honest skip (no fake scan)."
        return 2
    fi
    if ! command -v dotnet-sonarscanner >/dev/null 2>&1; then
        log "repo '${_slug:-?}' (csharp): 'dotnet-sonarscanner' not found — run 'dotnet tool install --global dotnet-sonarscanner'. Honest skip."
        return 2
    fi
    # GUARD 2 — host source. C# needs the HOST path (.sln + dotnet build read the host fs),
    # NOT the container /repos path. A null/absent host_source can't be scanned → skip.
    if [ -z "$_host" ] || [ ! -d "$_host" ]; then
        log "repo '${_slug:-?}' (csharp): host_source '${_host:-<empty>}' is not a directory on host — skipping (no block)."
        return 2
    fi

    # STEP 2 — ensure a .sln exists (Unity generates it; not committed). Pick the newest
    # existing .sln; if none, batch-generate via Unity SyncSolution.
    _sln="$(find "$_host" -maxdepth 1 -name '*.sln' 2>/dev/null | head -1 || true)"
    if [ -z "$_sln" ]; then
        log "repo '${_slug:-?}' (csharp): no .sln in '${_host}' — generating via Unity SyncSolution (batch)."
        if ! _dotnet_generate_unity_solution "$_host" "$_slug"; then
            log "repo '${_slug:-?}' (csharp): Unity could not generate a .sln (license/headless/SyncSolution fail) — honest skip."
            return 2
        fi
        _sln="$(find "$_host" -maxdepth 1 -name '*.sln' 2>/dev/null | head -1 || true)"
        if [ -z "$_sln" ]; then
            log "repo '${_slug:-?}' (csharp): still no .sln after SyncSolution — honest skip."
            return 2
        fi
    fi
    log "repo '${_slug:-?}' (csharp): using solution '${_sln}'."

    # STEP 3 — begin → build → end. The HOST-reachable sonar url (SONAR_SCAN_URL), NOT the
    # compose-internal SONARQUBE_URL (sonarscanner runs on the host, can't resolve the
    # docker service hostname). Token via /d:sonar.token — NEVER echoed.
    _rc=0
    set +e
    (
        cd "$_host" || exit 90
        dotnet-sonarscanner begin \
            /k:"$_pk" \
            /n:"$_pk" \
            /d:sonar.host.url="$SONAR_SCAN_URL" \
            /d:sonar.token="${SONARQUBE_TOKEN:-}" \
            /d:sonar.exclusions="${_excl:-}" \
            /d:sonar.scm.disabled=true >/dev/null
        _b=$?
        [ "$_b" -ne 0 ] && exit "$_b"
        dotnet build "$_sln" >/dev/null
        _bd=$?
        # ALWAYS run `end` to upload whatever was collected, even if build had warnings/
        # errors (partial analysis still beats none); surface a non-zero build as failed.
        dotnet-sonarscanner end /d:sonar.token="${SONARQUBE_TOKEN:-}" >/dev/null
        _e=$?
        [ "$_e" -ne 0 ] && exit "$_e"
        exit "$_bd"
    )
    _rc=$?
    set -e

    if [ "$_rc" -eq 0 ]; then
        log "repo '${_slug:-?}' (csharp): .NET analysis uploaded for projectKey=${_pk}."
        return 0
    fi
    log "WARNING: dotnet sonarscanner pipeline exited ${_rc} for projectKey=${_pk} (repo '${_slug:-?}') — analysis NOT refreshed. NOT blocked."
    return 1
}

# _dotnet_generate_unity_solution <host_source> <slug> — batch-mode Unity SyncSolution to
# produce the .sln/.csproj a Unity project doesn't commit. Reads the editor version from
# ProjectSettings/ProjectVersion.txt → resolves the matching Unity.app; falls back to the
# newest installed editor. Returns 0 if Unity was launched without error, non-zero on any
# precondition miss (caller honest-skips). NEVER blocks the pipeline.
_dotnet_generate_unity_solution() {
    _uhost="$1"; _uslug="$2"
    _pv="$_uhost/ProjectSettings/ProjectVersion.txt"
    _editor=""
    if [ -f "$_pv" ]; then
        _ver="$(grep -E '^m_EditorVersion:' "$_pv" 2>/dev/null | head -1 | sed 's/^m_EditorVersion:[[:space:]]*//' | tr -d '\r')"
        if [ -n "$_ver" ]; then
            _cand="/Applications/Unity/Hub/Editor/${_ver}/Unity.app/Contents/MacOS/Unity"
            [ -x "$_cand" ] && _editor="$_cand"
        fi
    fi
    if [ -z "$_editor" ]; then
        # Fallback: newest installed Hub editor.
        _cand="$(ls -d /Applications/Unity/Hub/Editor/*/Unity.app/Contents/MacOS/Unity 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$_cand" ] && [ -x "$_cand" ] && _editor="$_cand"
    fi
    if [ -z "$_editor" ]; then
        log "repo '${_uslug:-?}' (csharp): no Unity editor found under /Applications/Unity/Hub/Editor — cannot generate .sln."
        return 1
    fi
    log "repo '${_uslug:-?}' (csharp): launching Unity (${_editor}) -batchmode SyncSolution ..."
    _rc=0
    set +e
    "$_editor" -batchmode -nographics -quit \
        -projectPath "$_uhost" \
        -executeMethod UnityEditor.SyncVS.SyncSolution \
        -logFile - >/dev/null 2>&1
    _rc=$?
    set -e
    return "$_rc"
}

# scan_one <index> — read element[index]'s fields, validate, run the scanner. Echoes a
# per-repo outcome word (ok|failed|skipped) on stdout (the LAST line of its output) so the
# caller can aggregate. Never exits, never aborts the caller. PH-257: a csharp repo is
# dispatched to the HOST .NET pipeline (run_dotnet_scanner) using host_source; every other
# language keeps the container sonar-scanner-cli path (run_scanner) VERBATIM.
scan_one() {
    _idx="$1"
    _pk="$(_plan_field "$_idx" project_key)"
    _src="$(_plan_field "$_idx" container_source)"
    _host="$(_plan_field "$_idx" host_source)"
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
    if [ -z "$_pk" ]; then
        log "repo '${_slug:-?}' plan missing project_key — skipping this repo (no block)."
        echo skipped
        return 0
    fi

    # PH-257 — csharp → HOST .NET pipeline (uses host_source). begin/build/end map: 2 →
    # skipped (honest precondition miss), 1 → failed, 0 → ok. NEVER a fake ok.
    if [ "$_lang" = "csharp" ]; then
        run_dotnet_scanner "$_pk" "$_host" "$_excl" "$_slug"
        _dnrc=$?
        case "$_dnrc" in
            0) echo ok ;;
            1) echo failed ;;
            *) echo skipped ;;
        esac
        return 0
    fi

    if [ -z "$_src" ]; then
        log "repo '${_slug:-?}' plan missing container_source — skipping this repo (no block)."
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
