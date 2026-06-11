#!/usr/bin/env bash
# tests/scripts/test_sonar_scan_board.sh — hermetic parse-level test for the per-repo
# SonarQube host runner (PH-248). NO network, NO docker, NO live SonarQube.
#
# Strategy: prepend a throwaway fake-bin dir to PATH that STUBS `curl`, `docker`, and
# `sonar-scanner`. The stub `curl` answers /api/system/status with "UP" and /scan-plans
# with a FIXTURE JSON array; the stub `docker` records one line per `docker compose run`
# invocation (we count scanner runs + capture the `-D` args). The REAL
# scripts/sonar-scan-board.sh runs unmodified against these stubs.
#
# Asserts (the PH-248 acceptance criteria, exercised against a 3-element fixture):
#   T1  Mode 2 (no slug) iterates ALL supported elements → 3 scanner runs, each under its
#       own projectKey/sources; supported=false element is SKIPPED (no scanner run).
#   T2  Mode 1 (slug given) selects EXACTLY 1 matching element → 1 scanner run.
#   T3  A 1-element list (single-repo board) → EXACTLY 1 scanner run with byte-identical
#       `-D` args to the legacy single-/scan-plan invocation.
#   T4  A supported=false-only fixture (Mode 2) → 0 scanner runs, marker `skipped`, exit 0.
#   T5  One repo's scanner failing (Mode 2) → the OTHER repos still scan (never-abort),
#       aggregate marker `failed`, exit 0.
#   T6  jq-ABSENT (python3 fallback) → same plan count + same slug-index selection.
#   T7  Mode 1 with an unknown slug → 0 scanner runs, marker `skipped`, exit 0.
#   T8  PH-257 REGRESSION — a supported=true CSHARP repo with NO host dotnet (sandbox) →
#       run_dotnet_scanner honest-returns 2 under `set -e`, yet the C# branch STILL emits
#       marker `skipped` and exits 0 (the qa_failed marker-swallow bug stays fixed).
#   T9  PH-257 STATIC — the runner source carries the `set +e` guard around the C# dispatch
#       AND the .sln-existence success check (rc is NOT trusted — the Unity 6 no-op trap).
#
# Run:  bash tests/scripts/test_sonar_scan_board.sh
# Exit: 0 = all pass; non-zero = a failure (prints which assertion).

set -u

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/../.." && pwd)"
RUNNER="$REPO_ROOT/scripts/sonar-scan-board.sh"

FAILS=0
pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# ---------------------------------------------------------------------------
# Fixtures — scan-plans JSON arrays (mirror SonarScanPlanResponse field names).
# ---------------------------------------------------------------------------

# 3-element multi-repo board (GXA-shaped): 2 supported + 1 unsupported (C#).
PLANS_3='[
  {"project_key":"GameX","container_source":"/repos/GameX/GameXCore","host_source":"/h/GameXCore","language":"kotlin","supported":true,"reason":"","exclusions":"**/vendor/**","repo_id":"11111111-1111-1111-1111-111111111111","repo_slug":"gamexcore"},
  {"project_key":"GameX-gamexsdk","container_source":"/repos/GameX/GameXSdk","host_source":"/h/GameXSdk","language":"java","supported":true,"reason":"","exclusions":"**/vendor/**","repo_id":"22222222-2222-2222-2222-222222222222","repo_slug":"gamexsdk"},
  {"project_key":"GameX-gamexandroiddemoapp","container_source":"/repos/GameX/Demo","host_source":"/h/Demo","language":"csharp","supported":false,"reason":"C# unsupported in Community Edition","exclusions":null,"repo_id":"33333333-3333-3333-3333-333333333333","repo_slug":"gamexandroiddemoapp"}
]'

# 1-element single-repo board (PH/KIM-shaped) — byte-identity check vs legacy.
PLANS_1='[
  {"project_key":"project-hub","container_source":"/repos/project-hub","host_source":"/h/project-hub","language":"python","supported":true,"reason":"","exclusions":"**/node_modules/**","repo_id":"44444444-4444-4444-4444-444444444444","repo_slug":"project-hub"}
]'

# All-unsupported fixture (Mode 2 → 0 scans, skipped).
PLANS_UNSUP='[
  {"project_key":"U1","container_source":"/repos/u1","host_source":"/h/u1","language":"csharp","supported":false,"reason":"C# unsupported","exclusions":null,"repo_id":"55555555-5555-5555-5555-555555555555","repo_slug":"u1"}
]'

# PH-257 — csharp SUPPORTED fixture (the .NET gate is flipped on). supported=true means the
# runner reaches run_dotnet_scanner (HOST .NET pipeline). In this hermetic sandbox `dotnet`
# is ABSENT (env -i + curated TOOLBIN with no dotnet), so run_dotnet_scanner honest-returns 2
# → the C# branch must still emit `skipped` and exit 0. This is the REGRESSION for the
# qa_failed `set -e` marker-swallow bug: a bare call would abort scan_one before the marker.
PLANS_CSHARP_SUP='[
  {"project_key":"FN","container_source":"/repos/fn","host_source":"/h/fn","language":"csharp","supported":true,"reason":"routes through host .NET pipeline","exclusions":"**/Library/**","repo_id":"66666666-6666-6666-6666-666666666666","repo_slug":"fruit-ninja2"}
]'

# ---------------------------------------------------------------------------
# run_case — build a fake-bin sandbox, run the REAL runner, capture stdout + the
# recorded scanner invocations.
#   $1 = fixture JSON (the /scan-plans payload)
#   $2 = "" for jq-allowed, "nojq" to HIDE jq (force the python3 fallback)
#   $3 = "" for the scanner to succeed, "fail:<projectKey>" to make that one repo fail
#   $@ (4+) = extra args passed to the runner after the board key (e.g. a repo-slug)
# Sets globals: OUT (runner stdout), SCAN_LINES file path (one line per scanner run),
#               RESULT (the SONAR_SCAN_RESULT marker), RC (runner exit code).
# ---------------------------------------------------------------------------
run_case() {
    _fixture="$1"; _nojq="$2"; _failspec="$3"; shift 3

    SBX="$(mktemp -d)"
    BIN="$SBX/bin"
    mkdir -p "$BIN"
    SCAN_LINES="$SBX/scanner-invocations.log"
    : > "$SCAN_LINES"

    printf '%s' "$_fixture" > "$SBX/plans.json"

    # Stub curl: /api/system/status → UP; /scan-plans → fixture; else empty.
    cat > "$BIN/curl" <<CURL
#!/usr/bin/env bash
for a in "\$@"; do url="\$a"; done   # last arg is the URL
case "\$url" in
  *"/api/system/status"*) printf '%s' '{"status":"UP"}' ;;
  *"/sonarqube/scan-plans"*) cat "$SBX/plans.json" ;;
  *) printf '' ;;
esac
exit 0
CURL
    chmod +x "$BIN/curl"

    # Stub docker: record `docker compose ... run ... sonar-scanner -D...` and honor the
    # fail spec (a matching -Dsonar.projectKey=<failspec> → exit 1).
    cat > "$BIN/docker" <<DOCKER
#!/usr/bin/env bash
# Only care about the scanner run; ignore other docker subcommands.
case " \$* " in
  *" sonar-scanner "*)
    echo "\$*" >> "$SCAN_LINES"
    failpk="${_failspec#fail:}"
    if [ -n "${_failspec}" ] && printf '%s' "\$*" | grep -qF -- "-Dsonar.projectKey=\${failpk} "; then
      exit 1
    fi
    exit 0
    ;;
esac
exit 0
DOCKER
    chmod +x "$BIN/docker"

    # Build a hermetic TOOLBIN of REAL coreutils the runner needs (resolved on the OUTER
    # PATH), so `env -i` doesn't strip them. jq is symlinked ONLY when NOT in the nojq
    # case — omitting it makes `command -v jq` miss → the runner takes the python3 path.
    TOOLBIN="$SBX/toolbin"; mkdir -p "$TOOLBIN"
    for t in python3 bash sh sed tr grep cat dirname tail head cut printf mktemp env true; do
        p="$(command -v "$t" 2>/dev/null)" && ln -sf "$p" "$TOOLBIN/$t"
    done
    if [ "$_nojq" != "nojq" ]; then
        p="$(command -v jq 2>/dev/null)" && ln -sf "$p" "$TOOLBIN/jq"
    fi

    # SONARQUBE_ENABLED=true + a token so the guards pass. env -i for a clean slate; the
    # repo .env is moved aside for the whole run (see restore_env) so it can't override.
    OUT="$(cd "$REPO_ROOT" && env -i \
        PATH="$BIN:$TOOLBIN" HOME="$SBX" \
        SONARQUBE_ENABLED=true SONARQUBE_TOKEN=faketoken \
        SONARQUBE_URL=http://sonarqube:9000 SONAR_SCAN_URL=http://localhost:9000 \
        BACKEND_PORT=8000 SONAR_API_TOKEN=faketoken \
        bash "$RUNNER" GXA "$@" 2>&1)"
    RC=$?
    RESULT="$(printf '%s\n' "$OUT" | grep -o 'SONAR_SCAN_RESULT=[a-z]*' | tail -1 | cut -d= -f2)"
    # Count non-empty recorded scanner invocations (no trailing-newline artifact).
    SCAN_COUNT=0
    while IFS= read -r _l; do [ -n "$_l" ] && SCAN_COUNT=$((SCAN_COUNT + 1)); done < "$SCAN_LINES"
    _LAST_SBX="$SBX"
}

cleanup_case() { [ -n "${_LAST_SBX:-}" ] && rm -rf "$_LAST_SBX"; }

# ---------------------------------------------------------------------------
# IMPORTANT: the runner sources $REPO_ROOT/.env if present, which may set
# SONARQUBE_ENABLED=false (kill switch) and override our env -i exports (it sources .env
# AFTER our env is in place, so .env WINS). To keep the test hermetic we temporarily
# move a real .env aside for the duration of the run.
# ---------------------------------------------------------------------------
ENV_BAK=""
if [ -f "$REPO_ROOT/.env" ]; then
    ENV_BAK="$REPO_ROOT/.env.phtest.bak"
    mv "$REPO_ROOT/.env" "$ENV_BAK"
fi
restore_env() { [ -n "$ENV_BAK" ] && [ -f "$ENV_BAK" ] && mv "$ENV_BAK" "$REPO_ROOT/.env"; }
trap 'restore_env' EXIT

echo "=== PH-248 sonar-scan-board.sh parse-level tests ==="

# --- T1: Mode 2 iterates all SUPPORTED elements (3-element fixture → 2 supported scans).
run_case "$PLANS_3" "" ""
echo "[T1] Mode 2 / 3-element fixture (2 supported, 1 unsupported)"
[ "$SCAN_COUNT" -eq 2 ] && pass "exactly 2 scanner runs (the 2 supported repos)" || fail "expected 2 scanner runs, got $SCAN_COUNT"
grep -qF -- "-Dsonar.projectKey=GameX " "$SCAN_LINES" && grep -qF -- "-Dsonar.projectKey=GameX-gamexsdk " "$SCAN_LINES" \
    && pass "scanned GameX + GameX-gamexsdk under their own projectKeys" || fail "missing per-repo projectKey args"
! grep -qF -- "-Dsonar.projectKey=GameX-gamexandroiddemoapp" "$SCAN_LINES" \
    && pass "unsupported (csharp) repo NOT scanned" || fail "unsupported repo was scanned"
printf '%s\n' "$OUT" | grep -q "is NOT scannable" && pass "honest skip log line for the unsupported repo" || fail "no skip log for unsupported repo"
[ "$RESULT" = "ok" ] && pass "aggregate marker = ok (≥1 ok, 0 failed)" || fail "expected marker ok, got '$RESULT'"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "expected exit 0, got $RC"
cleanup_case

# --- T2: Mode 1 (slug) selects EXACTLY 1 element.
run_case "$PLANS_3" "" "" gamexsdk
echo "[T2] Mode 1 / slug=gamexsdk"
[ "$SCAN_COUNT" -eq 1 ] && pass "exactly 1 scanner run (the targeted repo)" || fail "expected 1 scanner run, got $SCAN_COUNT"
grep -qF -- "-Dsonar.projectKey=GameX-gamexsdk " "$SCAN_LINES" && pass "scanned the matching repo's projectKey" || fail "wrong/missing projectKey"
! grep -qF -- "-Dsonar.projectKey=GameX " "$SCAN_LINES" && pass "did NOT scan the other repos" || fail "scanned a non-targeted repo"
[ "$RESULT" = "ok" ] && pass "marker = ok" || fail "expected marker ok, got '$RESULT'"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "expected exit 0, got $RC"
cleanup_case

# --- T3: 1-element list → exactly 1 scanner run with the full PH-242/243/244 `-D` set.
run_case "$PLANS_1" "" "" project-hub
echo "[T3] single-repo board (1-element list, slug=project-hub) → byte-identical -D args"
[ "$SCAN_COUNT" -eq 1 ] && pass "exactly 1 scanner run" || fail "expected 1 scanner run, got $SCAN_COUNT"
LINE="$(cat "$SCAN_LINES")"
for d in \
    "-Dsonar.projectBaseDir=/repos/project-hub" \
    "-Dsonar.projectKey=project-hub" \
    "-Dsonar.projectName=project-hub" \
    "-Dsonar.sources=/repos/project-hub" \
    "-Dsonar.tests=" \
    "-Dsonar.exclusions=**/node_modules/**" \
    "-Dsonar.java.binaries=." \
    "-Dsonar.scm.disabled=true" ; do
    printf '%s' "$LINE" | grep -qF -- "$d" && pass "carries $d" || fail "MISSING $d"
done
cleanup_case

# --- T4: all-unsupported (Mode 2) → 0 scans, marker skipped, exit 0.
run_case "$PLANS_UNSUP" "" ""
echo "[T4] all-unsupported fixture (Mode 2)"
[ "$SCAN_COUNT" -eq 0 ] && pass "0 scanner runs" || fail "expected 0 scanner runs, got $SCAN_COUNT"
[ "$RESULT" = "skipped" ] && pass "marker = skipped" || fail "expected marker skipped, got '$RESULT'"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "expected exit 0, got $RC"
cleanup_case

# --- T5: one repo's scanner FAILS (Mode 2) → the other still scans, marker failed, exit 0.
run_case "$PLANS_3" "" "fail:GameX"
echo "[T5] Mode 2 never-abort: GameX scanner fails"
[ "$SCAN_COUNT" -eq 2 ] && pass "BOTH supported repos still attempted (never-abort)" || fail "expected 2 attempts, got $SCAN_COUNT"
[ "$RESULT" = "failed" ] && pass "aggregate marker = failed (≥1 failure surfaced)" || fail "expected marker failed, got '$RESULT'"
[ "$RC" -eq 0 ] && pass "exit 0 despite a scanner failure (best-effort contract)" || fail "expected exit 0, got $RC"
cleanup_case

# --- T6: jq ABSENT → python3 fallback must produce the SAME count + slug selection.
run_case "$PLANS_3" "nojq" "" gamexsdk
echo "[T6] python3 fallback (jq hidden) / Mode 1 slug=gamexsdk"
[ "$SCAN_COUNT" -eq 1 ] && pass "python3 parse → exactly 1 scanner run (slug-index match agrees with jq)" || fail "expected 1 scanner run, got $SCAN_COUNT"
grep -qF -- "-Dsonar.projectKey=GameX-gamexsdk " "$SCAN_LINES" && pass "python3 fallback selected the right element" || fail "python3 fallback picked the wrong element"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "expected exit 0, got $RC"
cleanup_case

run_case "$PLANS_3" "nojq" ""
echo "[T6b] python3 fallback (jq hidden) / Mode 2 iterate-all"
[ "$SCAN_COUNT" -eq 2 ] && pass "python3 parse → 2 supported scans (count agrees with jq)" || fail "expected 2 scanner runs, got $SCAN_COUNT"
cleanup_case

# --- T7: Mode 1 unknown slug → 0 scans, marker skipped, exit 0.
run_case "$PLANS_3" "" "" nosuchrepo
echo "[T7] Mode 1 / unknown slug"
[ "$SCAN_COUNT" -eq 0 ] && pass "0 scanner runs (slug not found)" || fail "expected 0 scanner runs, got $SCAN_COUNT"
[ "$RESULT" = "skipped" ] && pass "marker = skipped" || fail "expected marker skipped, got '$RESULT'"
printf '%s\n' "$OUT" | grep -q "not found in board" && pass "honest 'not found' log line" || fail "no not-found log line"
[ "$RC" -eq 0 ] && pass "exit 0" || fail "expected exit 0, got $RC"
cleanup_case

# --- T8: PH-257 regression — supported csharp repo, NO host dotnet → honest skip, NOT a
#         marker-swallow. Before the fix, `run_dotnet_scanner` returning 2 under `set -e`
#         aborted scan_one before the marker; the script died with NO SONAR_SCAN_RESULT.
run_case "$PLANS_CSHARP_SUP" "" "" fruit-ninja2
echo "[T8] PH-257 / supported csharp repo, host dotnet ABSENT (set -e guard regression)"
[ "$SCAN_COUNT" -eq 0 ] && pass "0 container scanner runs (C# uses the HOST .NET pipeline, not docker)" || fail "expected 0 docker scanner runs, got $SCAN_COUNT"
[ -n "$RESULT" ] && pass "a SONAR_SCAN_RESULT marker WAS emitted (set -e did NOT swallow it)" || fail "NO marker emitted — set -e marker-swallow regression!"
[ "$RESULT" = "skipped" ] && pass "marker = skipped (honest: dotnet absent → no scan ran)" || fail "expected marker skipped, got '$RESULT'"
[ "$RC" -eq 0 ] && pass "exit 0 (ALWAYS-exit-0 contract held through the C# honest skip)" || fail "expected exit 0, got $RC"
printf '%s\n' "$OUT" | grep -q "'dotnet' not found on host" && pass "honest 'dotnet not found' reason logged" || fail "no actionable dotnet-missing reason in log"
cleanup_case

# --- T9: PH-257 static — the C# dispatch must be if-guarded (so an honest rc 1/2 can't abort
#         under `set -e`), generation success must be verified by .csproj existence (not the
#         Unity 6 no-op rc), the injected script must drive SdkStyleProjectGeneration + clean
#         up (incl. the baked-in csproj reference), and non-test csproj must be forced MAIN so
#         SonarScanner reports real ncloc (the nunit-everywhere test-misclassification fix).
echo "[T9] PH-257 / static guards in $RUNNER"
# The C# dispatch must invoke run_dotnet_scanner inside an `if`/`||` guard so an honest rc
# (1/2) can't abort scan_one under `set -e` — a bare call (even after `set +e`) would, because
# run_dotnet_scanner re-enables `set -e` internally before returning. Portable check: the
# run_dotnet_scanner DISPATCH line (in scan_one, not the definition/comments) must be the
# condition of an `if`. We match `if run_dotnet_scanner ` anywhere in the source.
if grep -qE '^[[:space:]]*if[[:space:]]+run_dotnet_scanner[[:space:]]' "$RUNNER"; then
    pass "run_dotnet_scanner dispatch is inside an if-guard (set -e cannot swallow the marker)"
else
    fail "run_dotnet_scanner is dispatched bare — set -e marker-swallow can recur"
fi
# And there is NO bare run_dotnet_scanner dispatch (a call line — quote-arg — not prefixed
# by `if`). The definition `run_dotnet_scanner() {` has no quote-arg so it never matches.
BARE_CALLS="$(grep -nE 'run_dotnet_scanner[[:space:]]+"' "$RUNNER" | grep -vE ':[[:space:]]*if[[:space:]]+run_dotnet_scanner' || true)"
if [ -z "$BARE_CALLS" ]; then
    pass "no bare run_dotnet_scanner dispatch (every call site is if-guarded)"
else
    fail "a BARE run_dotnet_scanner dispatch exists (not if-guarded): $BARE_CALLS"
fi
# Generation success trusts the FILESYSTEM (.csproj existence), not Unity's rc. The .csproj
# are the load-bearing compilation units; a .sln alone (the no-op trap) is not enough.
grep -qF "find \"\$_uhost\" -maxdepth 1 -name '*.csproj'" "$RUNNER" \
    && pass "Unity generation success is verified by *.csproj existence (rc not trusted)" \
    || fail "no *.csproj existence check after Unity run — Unity 6 no-op trap unguarded"
# The injected editor script drives the CONCRETE generator (SdkStyleProjectGeneration), not
# the legacy SyncVS no-op nor the bare base ProjectGeneration (which NREs).
grep -qF "JarwisSolutionSync.Sync" "$RUNNER" \
    && pass "uses injected editor script (-executeMethod JarwisSolutionSync.Sync)" \
    || fail "still relies on the legacy SyncVS no-op executeMethod"
grep -qF "SdkStyleProjectGeneration" "$RUNNER" \
    && pass "injected script drives SdkStyleProjectGeneration (writes real .csproj, not just .sln)" \
    || fail "injected script does not use SdkStyleProjectGeneration — Unity 6 .csproj would be missing"
# And the injected script + its baked-in csproj reference are always cleaned up.
grep -qF "_cleanup_injected" "$RUNNER" \
    && pass "injected editor script is cleaned up after the run" \
    || fail "no cleanup of the injected editor script — would leave project dirty"
grep -qF 'JarwisSolutionSync\.cs/d' "$RUNNER" \
    && pass "the injected script's <Compile Include> line is stripped from generated csproj (no CS2001)" \
    || fail "injected script reference not stripped from csproj — dotnet build would fail CS2001"
# MAIN/TEST classification fix: non-test csproj are forced MAIN so SonarScanner reports
# real ncloc (Unity references nunit from every csproj, which else flags all as TEST).
grep -qF "_mark_main_projects" "$RUNNER" \
    && pass "non-test csproj are marked MAIN (_mark_main_projects → SonarQubeTestProject=false)" \
    || fail "no MAIN/TEST classification fix — all C# would import as TEST, ncloc=0"
grep -qF "SonarQubeTestProject" "$RUNNER" \
    && pass "emits <SonarQubeTestProject>false</SonarQubeTestProject> for MAIN assemblies" \
    || fail "no SonarQubeTestProject marker — the nunit-reference test misclassification recurs"

echo "=== done: $FAILS failure(s) ==="
[ "$FAILS" -eq 0 ]
