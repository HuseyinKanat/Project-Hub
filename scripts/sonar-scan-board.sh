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

# _mark_main_projects <host_source> — PH-257: force MAIN classification on the non-test
# generated .csproj. Unity references nunit (com.unity.ext.nunit) from EVERY csproj, which
# trips SonarScanner for .NET's test-project heuristic → all C# imported as TEST, MAIN
# ncloc=0. We add <SonarQubeTestProject>false</SonarQubeTestProject> to each .csproj whose
# filename does NOT contain "test" (case-insensitive), so the game/runtime/editor assemblies
# count as MAIN. Idempotent (skips if already marked). The .csproj are generated/ephemeral
# (cleaned up after the run) — NOT the user's committed files.
_mark_main_projects() {
    _mphost="$1"
    for _mcp in "$_mphost"/*.csproj; do
        [ -f "$_mcp" ] || continue
        _base="$(basename "$_mcp")"
        # genuine test assemblies keep auto-detection (skip the ones named *test*)
        if printf '%s' "$_base" | grep -qiE 'test'; then continue; fi
        # already marked? (idempotent)
        if grep -q 'SonarQubeTestProject' "$_mcp"; then continue; fi
        # Insert the property right after the FIRST <PropertyGroup>. Use awk (portable; BSD sed's
        # `0,/re/s//` GNU-ism silently no-ops on macOS — that was a real false-negative). Write
        # to a temp file then move (atomic-ish; no in-place flag portability worries).
        awk '!done && /<PropertyGroup>/ {
                 print
                 print "    <SonarQubeTestProject>false</SonarQubeTestProject>"
                 done=1
                 next
             }
             { print }' "$_mcp" > "${_mcp}.jarwistmp" 2>/dev/null \
            && mv "${_mcp}.jarwistmp" "$_mcp" 2>/dev/null \
            || rm -f "${_mcp}.jarwistmp" 2>/dev/null
    done
}

# ---------------------------------------------------------------------------
# run_dotnet_scanner <project_key> <host_source> <exclusions> <slug> — PH-257: real C#
# (Unity) analysis via the HOST-side SonarScanner for .NET (begin → dotnet build → end).
#
# The container sonar-scanner-cli CANNOT analyze C# (empirical probe: indexes .cs but
# reports 0 ncloc / 0 issues — a stub), so a csharp repo is analyzed here on the HOST:
#   1) GUARD: dotnet SDK + dotnet-sonarscanner + a real host_source dir must exist; any
#      missing → HONEST skip (return 2 → caller emits 'skipped', NEVER a fake 'ok').
#   2) Unity has NO committed .sln/.csproj (generated). If no .csproj, generate them via an
#      injected batch-mode editor script (_dotnet_generate_unity_solution; the legacy
#      SyncVS.SyncSolution is a no-op in Unity 6, so we drive the code-editor package's
#      SdkStyleProjectGeneration). If no .csproj can be produced → honest skip. The .sln is
#      optional (Unity 6 batch writes csproj reliably but the .sln write is order-dependent).
#   3) SonarScanner.MSBuild begin (HOST-reachable sonar url — NOT the compose-internal
#      hostname) → dotnet build (.sln if present, else every .csproj) → end. Real C# ncloc +
#      issues upload to SonarQube.
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

    # STEP 2 — ensure the .csproj compilation units exist (Unity generates them; not
    # committed). A .sln is OPTIONAL — the load-bearing artifacts are the .csproj (the real
    # C# sources SonarScanner analyzes). If no .csproj, batch-generate via the injected editor
    # script. PH-257: we used to require a .sln and build it; Unity 6 batch mode reliably
    # writes the .csproj set but the .sln write is order-dependent — so build the .csproj.
    _build_target="$(find "$_host" -maxdepth 1 -name '*.sln' 2>/dev/null | head -1 || true)"
    _have_csproj="$(find "$_host" -maxdepth 1 -name '*.csproj' 2>/dev/null | head -1 || true)"
    if [ -z "$_have_csproj" ]; then
        log "repo '${_slug:-?}' (csharp): no .csproj in '${_host}' — generating via Unity (batch)."
        if ! _dotnet_generate_unity_solution "$_host" "$_slug"; then
            log "repo '${_slug:-?}' (csharp): Unity could not generate project files — honest skip."
            return 2
        fi
        _build_target="$(find "$_host" -maxdepth 1 -name '*.sln' 2>/dev/null | head -1 || true)"
        _have_csproj="$(find "$_host" -maxdepth 1 -name '*.csproj' 2>/dev/null | head -1 || true)"
        if [ -z "$_have_csproj" ]; then
            log "repo '${_slug:-?}' (csharp): still no .csproj after generation — honest skip."
            return 2
        fi
    fi
    if [ -n "$_build_target" ]; then
        log "repo '${_slug:-?}' (csharp): building solution '${_build_target}'."
    else
        log "repo '${_slug:-?}' (csharp): no .sln — building the .csproj set directly."
    fi

    # STEP 2b — MAIN/TEST classification fix (PH-257). Unity bundles nunit (com.unity.ext.nunit)
    # and references it from EVERY generated .csproj — even Assembly-CSharp (the real game code).
    # SonarScanner for .NET's test-detection heuristic flags any project referencing
    # nunit.framework as a TEST project, so it imports ONLY test-code and reports ncloc=0 for
    # MAIN ("only TEST-code ... no MAIN-code" CE warning). To get real MAIN ncloc we force
    # <SonarQubeTestProject>false</SonarQubeTestProject> on the non-test csproj (everything whose
    # name does NOT contain "test"). These .csproj are generated/ephemeral (cleaned up after),
    # never committed — we are NOT mutating the user's project. SonarScanner honors this MSBuild
    # property and classifies those assemblies as MAIN.
    _mark_main_projects "$_host"

    # Merge a Unity-default exclusion set (Library/Temp/obj/bin are generated caches, not
    # source) with the plan's exclusions so the scan indexes only real C#. The plan _excl (if
    # any) is appended.
    _unity_excl="**/Library/**,**/Temp/**,**/obj/**,**/bin/**,**/Logs/**"
    if [ -n "${_excl:-}" ]; then
        _all_excl="${_unity_excl},${_excl}"
    else
        _all_excl="${_unity_excl}"
    fi

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
            /d:sonar.exclusions="${_all_excl}" \
            /d:sonar.scm.disabled=true >/dev/null
        _b=$?
        [ "$_b" -ne 0 ] && exit "$_b"
        # Build the .sln if Unity produced one; otherwise build every generated .csproj so the
        # SonarScanner MSBuild integration collects analysis for each compilation unit. A
        # single .csproj that fails to build does NOT abort the rest (best-effort, partial
        # analysis beats none) — the worst per-project rc is surfaced as the build rc.
        _bd=0
        if [ -n "$_build_target" ]; then
            dotnet build "$_build_target" >/dev/null
            _bd=$?
        else
            for _cp in *.csproj; do
                [ -f "$_cp" ] || continue
                dotnet build "$_cp" >/dev/null
                _cprc=$?
                [ "$_cprc" -ne 0 ] && _bd="$_cprc"
            done
        fi
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

# _dotnet_generate_unity_solution <host_source> <slug> — batch-mode Unity .sln/.csproj
# generation for a Unity project that doesn't commit them.
#
# WHY NOT plain SyncVS (PH-257 qa_failed fix): QA proved that on Unity 6000.x,
# `UnityEditor.SyncVS.SyncSolution` exits rc 0 / "Exiting batchmode successfully" but
# generates NO .sln/.csproj — it is a LEGACY no-op in Unity 6 (project generation moved
# into the code-editor packages com.unity.ide.visualstudio / com.unity.ide.rider). The
# editor's DefaultExternalCodeEditor.SyncAll() (the active editor in a headless project with
# no VS/Rider selected) writes only a .sln and NO .csproj — the same trap. So we inject a
# tiny temporary editor script (Assets/Editor/JarwisSolutionSync.cs) whose static `Sync()`
# drives the CONCRETE generator subclass directly (via REFLECTION so it COMPILES regardless
# of which package is installed), trying in order:
#   a. Microsoft.Unity.VisualStudio.Editor.SdkStyleProjectGeneration    (Unity 6 default — writes BOTH csproj + sln)
#   b. Microsoft.Unity.VisualStudio.Editor.LegacyStyleProjectGeneration (older project setups)
#   c. Packages.Rider.Editor.ProjectGeneration.ProjectGeneration        (com.unity.ide.rider, if present)
#   d. UnityEditor.SyncVS.SyncSolution()                                (very old editors; .sln-only fallback)
# Each concrete type's parameterless ctor wires the AssemblyNameProvider/FileIOProvider/
# GUIDProvider (the abstract base ProjectGeneration ctor does NOT → NRE). The injected .cs +
# its .meta + any <Compile Include=".../JarwisSolutionSync.cs"/> line baked into a generated
# .csproj are ALWAYS removed afterward, so the project is left byte-identical. We NEVER touch
# the manifest/Packages — if no code-editor package is installed, no .csproj appears → skip.
#
# SUCCESS CRITERION: ≥1 *.csproj must ACTUALLY exist after the run (rc is NOT trusted — that
# was the no-op trap; the .sln is optional). Returns 0 only if .csproj were produced; non-zero
# otherwise (caller skips). NEVER blocks the pipeline; NEVER mutates committed project files.
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

    # Inject the temporary editor script. Unique name + Editor/ folder (compile target). We
    # ALWAYS clean it up (both .cs and Unity's generated .meta) so the project is untouched.
    _editdir="$_uhost/Assets/Editor"
    _injected="$_editdir/JarwisSolutionSync.cs"
    _created_editdir=0
    if [ ! -d "$_editdir" ]; then
        mkdir -p "$_editdir" 2>/dev/null && _created_editdir=1
    fi
    # shellcheck disable=SC2317  # trap body runs on RETURN
    _cleanup_injected() {
        rm -f "$_injected" "${_injected}.meta" 2>/dev/null || true
        # The injected .cs was part of the Editor assembly when the .csproj was generated, so
        # every generated .csproj now has a <Compile Include="...JarwisSolutionSync.cs" /> line
        # pointing at the file we just deleted → `dotnet build` would fail CS2001. Strip that
        # one line from every generated .csproj (sed in-place; the file is generated/ephemeral,
        # never committed) so the build sees only the user's real sources.
        for _cp in "$_uhost"/*.csproj; do
            [ -f "$_cp" ] || continue
            sed -i '' '/JarwisSolutionSync\.cs/d' "$_cp" 2>/dev/null \
                || sed -i '/JarwisSolutionSync\.cs/d' "$_cp" 2>/dev/null || true
        done
        # Only remove the Editor dir if WE created it and it is now empty.
        if [ "$_created_injected_editdir" = "1" ]; then
            rmdir "$_editdir" 2>/dev/null || true
            rm -f "${_editdir}.meta" 2>/dev/null || true
        fi
    }
    _created_injected_editdir="$_created_editdir"

    cat > "$_injected" <<'JARWIS_SYNC_EOF'
// AUTO-GENERATED by sonar-scan-board.sh (PH-257) — TEMPORARY. Removed after the batch run.
// Triggers Unity project (.sln/.csproj) generation across editor versions via reflection so
// it compiles whether com.unity.ide.visualstudio, com.unity.ide.rider, or only the legacy
// SyncVS is present. NEVER edits manifest/Packages.
using System;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

public static class JarwisSolutionSync
{
    // Called via -executeMethod JarwisSolutionSync.Sync
    public static void Sync()
    {
        // Make sure the asset DB + script assemblies are imported so the generator sees the
        // real assembly set (a cold batch run can otherwise emit an .sln with NO .csproj).
        try { AssetDatabase.Refresh(); } catch (Exception e) { Debug.Log("[JarwisSolutionSync] refresh: " + e.Message); }

        bool ok = false;
        // Try, in order, the CONCRETE generator subclasses whose parameterless ctor properly
        // wires the AssemblyNameProvider / FileIOProvider / GUIDProvider. Building the abstract
        // base `ProjectGeneration` via Activator throws NRE (no installation wired), and the
        // editor's DefaultExternalCodeEditor.SyncAll() writes the .sln but NO .csproj — that is
        // exactly the Unity 6 trap QA hit. SdkStyleProjectGeneration (Unity 6 default) +
        // LegacyStyleProjectGeneration DO write both. [PH-257]
        // a. com.unity.ide.visualstudio (Unity 6 default) — SDK-style csproj.
        ok |= TryGeneration("Microsoft.Unity.VisualStudio.Editor.SdkStyleProjectGeneration");
        // b. com.unity.ide.visualstudio — legacy-style csproj (older project setups).
        if (!ok) ok |= TryGeneration("Microsoft.Unity.VisualStudio.Editor.LegacyStyleProjectGeneration");
        // c. com.unity.ide.rider — Rider's generator.
        if (!ok) ok |= TryGeneration("Packages.Rider.Editor.ProjectGeneration.ProjectGeneration");
        if (!ok) ok |= TryGeneration("Packages.Rider.Editor.ProjectGeneration");
        // d. legacy UnityEditor.SyncVS.SyncSolution() (very old editors; .sln only fallback).
        if (!ok) ok |= TryLegacySyncVS();
        Debug.Log("[JarwisSolutionSync] project generation attempted, succeeded=" + ok);
    }

    static Type FindType(string fullName)
    {
        foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
        {
            var t = asm.GetType(fullName, false);
            if (t != null) return t;
        }
        return null;
    }

    // Instantiate <typeName> (an IGenerator implementer) and force a FULL write of BOTH the
    // solution AND the .csproj files. We prefer GenerateAndWriteSolutionAndProjects() over
    // Sync(): Sync() has an OnPreGeneratingCSProjectFiles() early-exit that another package's
    // AssetPostprocessor can trip (returning true), so Sync() writes only the .sln and SKIPS
    // every .csproj — exactly the qa_failed trap (sln present, dotnet build can't find csproj).
    // GenerateAndWriteSolutionAndProjects() has no such guard. Fall back to Sync() if absent.
    static bool TryGeneration(string typeName)
    {
        try
        {
            var t = FindType(typeName);
            if (t == null) return false;
            var ctor = t.GetConstructor(Type.EmptyTypes);
            object inst = ctor != null ? ctor.Invoke(null) : Activator.CreateInstance(t);
            if (inst == null) return false;

            var genAll = t.GetMethod("GenerateAndWriteSolutionAndProjects", BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
            if (genAll != null)
            {
                genAll.Invoke(inst, null);
                Debug.Log("[JarwisSolutionSync] generated (solution + projects) via " + typeName + ".GenerateAndWriteSolutionAndProjects");
                return true;
            }
            var sync = t.GetMethod("Sync", BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
            if (sync == null) return false;
            sync.Invoke(inst, null);
            Debug.Log("[JarwisSolutionSync] generated via " + typeName + ".Sync (no GenerateAndWriteSolutionAndProjects)");
            return true;
        }
        catch (Exception e)
        {
            Debug.Log("[JarwisSolutionSync] " + typeName + " failed: " + e.Message);
            return false;
        }
    }

    static bool TryLegacySyncVS()
    {
        try
        {
            var t = FindType("UnityEditor.SyncVS");
            if (t == null) return false;
            var m = t.GetMethod("SyncSolution", BindingFlags.Public | BindingFlags.Static)
                    ?? t.GetMethods(BindingFlags.Public | BindingFlags.Static)
                         .FirstOrDefault(x => x.Name == "SyncSolution" && x.GetParameters().Length == 0);
            if (m == null) return false;
            m.Invoke(null, null);
            Debug.Log("[JarwisSolutionSync] generated via legacy UnityEditor.SyncVS.SyncSolution");
            return true;
        }
        catch (Exception e)
        {
            Debug.Log("[JarwisSolutionSync] legacy SyncVS failed: " + e.Message);
            return false;
        }
    }
}
JARWIS_SYNC_EOF

    log "repo '${_uslug:-?}' (csharp): launching Unity (${_editor}) -batchmode -executeMethod JarwisSolutionSync.Sync ..."
    set +e
    # First import of a fresh project can be slow; allow up to 600s. macOS lacks GNU
    # `timeout`, so guard with a background watchdog that kills the editor if it overruns.
    "$_editor" -batchmode -nographics -quit \
        -projectPath "$_uhost" \
        -executeMethod JarwisSolutionSync.Sync \
        -logFile - >/dev/null 2>&1 &
    _upid=$!
    ( sleep 600; kill -9 "$_upid" 2>/dev/null ) >/dev/null 2>&1 &
    _wdog=$!
    wait "$_upid" 2>/dev/null
    _rc=$?
    kill "$_wdog" 2>/dev/null || true
    set -e

    _cleanup_injected

    # SUCCESS CRITERION (PH-257): trust the FILESYSTEM, not Unity's rc (the no-op trap). The
    # load-bearing artifacts are the .csproj files — they carry the real C# compilation units
    # SonarScanner analyzes; `dotnet build` compiles them directly. The .sln is OPTIONAL: in
    # Unity 6 batch mode SdkStyleProjectGeneration reliably writes the .csproj set but the .sln
    # write is order/state-dependent (sometimes skipped). So require ≥1 .csproj; the .sln is a
    # nice-to-have. Demanding a .sln was the original false-negative (csproj present, build OK,
    # but we honest-skipped anyway). [PH-257]
    _csproj="$(find "$_uhost" -maxdepth 1 -name '*.csproj' 2>/dev/null | head -1 || true)"
    if [ -n "$_csproj" ]; then
        _nsln="$(find "$_uhost" -maxdepth 1 -name '*.sln' 2>/dev/null | wc -l | tr -d ' ')"
        _ncsproj="$(find "$_uhost" -maxdepth 1 -name '*.csproj' 2>/dev/null | wc -l | tr -d ' ')"
        log "repo '${_uslug:-?}' (csharp): Unity produced ${_ncsproj} .csproj (+ ${_nsln} .sln) (rc=${_rc})."
        return 0
    fi
    log "repo '${_uslug:-?}' (csharp): Unity ran (rc=${_rc}) but produced NO .csproj — a Unity code-editor package may be missing (com.unity.ide.visualstudio / com.unity.ide.rider) or generation failed. Honest skip; project left untouched."
    return 1
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
    # CRITICAL (PH-257 qa_failed fix): run_dotnet_scanner honest-returns 2 on a precondition
    # miss (no .sln/.csproj, no toolchain). It must NOT abort scan_one under `set -e` —
    # otherwise the per-repo marker word is never echoed and the script dies with no
    # SONAR_SCAN_RESULT (the qa_failed bug). A plain `set +e` wrapper is NOT enough here:
    # run_dotnet_scanner re-enables `set -e` internally (its own dotnet-pipeline guard), so
    # `set -e` is back ON by the time it returns 2 → the bare call still aborts. The robust
    # form is the `if`/`||` guard (immune to `set -e` regardless of the callee's set-state),
    # exactly like run_scanner's guard below. Capture the rc inside the condition.
    if [ "$_lang" = "csharp" ]; then
        if run_dotnet_scanner "$_pk" "$_host" "$_excl" "$_slug"; then
            _dnrc=0
        else
            _dnrc=$?
        fi
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
