"""PH-236 (C2) — tests for per-board SonarQube scan: detect_board_language,
request_board_scan, build_scan_plan, and the scan / scan-plan endpoints.

Three layers, all network-free + scanner-free:

  Pure language detection (``detect_board_language``) — uses a real on-disk temp tree
  (tmp_path) so the os.walk / Unity-marker logic is exercised for real: Kotlin tree →
  ``kotlin`` (supported), Unity layout → ``csharp`` (unsupported), empty/missing → None.

  Service (``request_board_scan`` / ``build_scan_plan``) — a real in-memory sqlite
  session (the setup-test pattern) with ``get_settings`` patched, exercising the
  queued / disabled / unconfigured / unsupported / bad-path branches. ``to_container_path``
  is driven via patched host_home/repos_root so a tmp_path tree maps to itself.

  Endpoint (``api_board_sonarqube_scan`` / ``api_board_sonarqube_scan_plan``) — a
  FastAPI ``TestClient`` with DI overrides (the setup-endpoint pattern). Covers: admin
  200 (queued + unsupported), the FROZEN scan-plan shape, non-admin 403 via KEY (PH-233),
  null repos_path graceful (never-500), and the SECRET-FREE invariant.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Repository, Workflow
from app.db.session import get_db_session
from app.main import app
from app.services import repo_paths, sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# asyncio_mode="auto" (pyproject) auto-detects async tests — no module mark needed.

_SECRET = "super-secret-token"


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


async def _seed_board(
    session: AsyncSession,
    *,
    key: str = "GXA",
    project_key: str | None = "GameX",
    repos_path: str | None = "/Users/huseyinkanat/AndroidStudioProjects/GameX",
    member_role: str = "admin",
    repo_local_path: str | None = None,
    repo_is_primary: bool = True,
) -> tuple[Board, Actor]:
    workflow = Workflow(
        name=f"wf-{key}",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    session.add(workflow)
    await session.flush()

    actor = Actor(kind="human", display_name="A", token_hash="x", is_active=True)
    session.add(actor)
    await session.flush()

    board = Board(
        key=key,
        name=key,
        description="Test board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=actor.id,
        sonarqube_project_key=project_key,
        repos_path=repos_path,
    )
    session.add(board)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=member_role))
    if repo_local_path is not None:
        # PH-242: a linked repo whose local_path is the precise (sub-dir) code root,
        # distinct from the coarse board.repos_path parent.
        session.add(
            Repository(
                board_id=board.id,
                slug=f"{key.lower()}-repo",
                name=f"{key} repo",
                is_primary=repo_is_primary,
                provider="local",
                default_branch="main",
                local_path=repo_local_path,
            )
        )
    await session.commit()

    refreshed_board = (
        await session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(
                selectinload(Board.workflow),
                selectinload(Board.memberships),
                selectinload(Board.sonarqube_metric),
                selectinload(Board.repositories),  # PH-242: primary_repository async-safe
            )
        )
    ).scalar_one()
    refreshed_actor = (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    return refreshed_board, refreshed_actor


def _settings(
    *,
    enabled: bool = True,
    host_home: str = "/Users/huseyinkanat",
    repos_root: str = "/Users/huseyinkanat",
) -> MagicMock:
    """A settings double. host_home == repos_root makes to_container_path identity, so a
    HOST path under the temp/home root maps to the SAME on-disk path the detector reads.
    """
    settings = MagicMock()
    settings.sonarqube_enabled = enabled
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_scan_url = "http://localhost:9000"
    settings.sonarqube_token = _SECRET
    settings.sonarqube_project_key_map = ""
    settings.host_home = host_home
    settings.repos_root = repos_root
    return settings


def _patch_all(settings: MagicMock):
    """Patch get_settings in EVERY module the scan path reaches: the service, the
    api layer, AND repo_paths (``to_container_path`` calls its OWN get_settings — so
    host_home/repos_root must be patched there too or a tmp_path under /tmp raises
    RepoPathError against the real HOST_HOME).
    """
    return (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(repo_paths, "get_settings", return_value=settings),
        patch("app.api.boards.get_settings", return_value=settings),
    )


def _prod_patch():
    """PH-242 — patch with the LIVE container mapping (host_home=/Users/huseyinkanat,
    repos_root=/repos) so `/repos/...` is recognized as container-form and HOST paths
    under HOST_HOME map to `/repos/...`. Use for the source-resolution regression unit
    tests that assert real container semantics (the detector-identity `_settings()`
    default collapses repos_root onto host_home and is unsuitable here).
    """
    s = _settings(host_home="/Users/huseyinkanat", repos_root="/repos")
    p1, p2, p3 = _patch_all(s)
    return p1, p2, p3


# ===========================================================================
# detect_board_language — real on-disk trees (tmp_path)
# ===========================================================================


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("// x\n")


def test_detect_kotlin_tree_is_kotlin(tmp_path) -> None:
    root = str(tmp_path)
    _touch(os.path.join(root, "app", "src", "Main.kt"))
    _touch(os.path.join(root, "core", "Config.kt"))
    _touch(os.path.join(root, "build.gradle.kts"))
    assert sonarqube.detect_board_language(root) == "kotlin"
    assert sonarqube._language_supported("kotlin") is True


def test_detect_android_kotlin_marker_is_kotlin_supported(tmp_path) -> None:
    """PH-243 — an Android/Gradle-Kotlin project (build.gradle.kts marker + deep
    src/main/kotlin .kt) detects ``kotlin``/supported even with a few stray .py tooling
    files that could otherwise out-tally / exhaust the bounded walk → python."""
    root = str(tmp_path)
    _touch(os.path.join(root, "settings.gradle.kts"))
    _touch(os.path.join(root, "app", "build.gradle.kts"))
    _touch(os.path.join(root, "app", "src", "main", "kotlin", "Main.kt"))
    # Stray python tooling/scripts at root — the marker shortcut must beat the tally.
    _touch(os.path.join(root, "tools", "gen.py"))
    _touch(os.path.join(root, "scripts", "release.py"))
    assert sonarqube.detect_board_language(root) == "kotlin"
    assert sonarqube._language_supported("kotlin") is True


def test_detect_settings_gradle_kts_marker_is_kotlin(tmp_path) -> None:
    """PH-243 — a root settings.gradle.kts alone is a sufficient Kotlin-DSL signal."""
    root = str(tmp_path)
    _touch(os.path.join(root, "settings.gradle.kts"))
    # No .kt yet, plenty of .py — marker still wins (deterministic, pre-tally).
    for i in range(10):
        _touch(os.path.join(root, "buildscripts", f"task{i}.py"))
    assert sonarqube.detect_board_language(root) == "kotlin"


def test_detect_python_repo_without_gradle_marker_stays_python(tmp_path) -> None:
    """PH-243 — the Kotlin marker is conservative: a plain python repo (no *.gradle.kts)
    is NOT misclassified — existing extension-tally detection is preserved."""
    root = str(tmp_path)
    _touch(os.path.join(root, "app", "main.py"))
    _touch(os.path.join(root, "app", "util.py"))
    _touch(os.path.join(root, "setup.py"))
    assert sonarqube.detect_board_language(root) == "python"
    assert sonarqube._language_supported("python") is True


def test_detect_unity_layout_is_csharp_unsupported(tmp_path) -> None:
    """A Unity Assets/ + ProjectSettings/ layout → csharp (the honest C# gate)."""
    root = str(tmp_path)
    _touch(os.path.join(root, "Assets", "Scripts", "Blade.cs"))
    os.makedirs(os.path.join(root, "ProjectSettings"), exist_ok=True)
    os.makedirs(os.path.join(root, "Packages"), exist_ok=True)
    assert sonarqube.detect_board_language(root) == "csharp"
    assert sonarqube._language_supported("csharp") is False


def test_detect_csharp_by_extension_unsupported(tmp_path) -> None:
    """Plain .cs files (no Unity layout) still detect csharp → unsupported."""
    root = str(tmp_path)
    _touch(os.path.join(root, "src", "Program.cs"))
    _touch(os.path.join(root, "src", "Helper.cs"))
    assert sonarqube.detect_board_language(root) == "csharp"
    assert sonarqube._language_supported("csharp") is False


def test_detect_python_tree_is_python_supported(tmp_path) -> None:
    root = str(tmp_path)
    _touch(os.path.join(root, "app", "main.py"))
    _touch(os.path.join(root, "app", "util.py"))
    assert sonarqube.detect_board_language(root) == "python"
    assert sonarqube._language_supported("python") is True


def test_detect_skips_generated_dirs(tmp_path) -> None:
    """build/ + node_modules/ + Library/ are pruned so generated code doesn't skew."""
    root = str(tmp_path)
    _touch(os.path.join(root, "src", "Main.kt"))
    # A pile of .cs under generated/vendor dirs must NOT win over the one real .kt.
    for i in range(20):
        _touch(os.path.join(root, "build", f"Gen{i}.cs"))
        _touch(os.path.join(root, "node_modules", f"Vendor{i}.cs"))
    assert sonarqube.detect_board_language(root) == "kotlin"


def test_detect_missing_dir_is_none(tmp_path) -> None:
    assert sonarqube.detect_board_language(str(tmp_path / "nope")) is None


def test_detect_empty_tree_is_none(tmp_path) -> None:
    assert sonarqube.detect_board_language(str(tmp_path)) is None


def test_language_supported_unknown_is_optimistic() -> None:
    """Unknown language (None) is optimistic-supported; only C# is gated off."""
    assert sonarqube._language_supported(None) is True
    assert sonarqube._language_supported("rust") is True  # unknown → let sensors decide
    assert sonarqube._language_supported("csharp") is False


# ===========================================================================
# request_board_scan / build_scan_plan — service layer (real sqlite)
# ===========================================================================


async def test_request_scan_queued_kotlin(mem_session: AsyncSession, tmp_path) -> None:
    """A Kotlin board with a key + valid path → queued (the happy path)."""
    _touch(os.path.join(str(tmp_path), "src", "Main.kt"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "queued"
    assert result.project_key == "GameX"
    assert result.language == "kotlin"
    # container_source == the on-disk path (identity mapping in the test settings).
    assert result.container_source == str(tmp_path)
    # PH-239: the queued message now points at the auto-run watcher (no manual hand-run).
    assert "watcher" in result.message.lower()
    assert _SECRET not in result.message


async def test_request_scan_unsupported_csharp(mem_session: AsyncSession, tmp_path) -> None:
    """A Unity/C# board → unsupported (honest, NOT a fake queued)."""
    root = str(tmp_path)
    _touch(os.path.join(root, "Assets", "Scripts", "Blade.cs"))
    os.makedirs(os.path.join(root, "ProjectSettings"), exist_ok=True)
    board, _ = await _seed_board(
        mem_session, key="FN", repos_path=root, project_key="fruit-ninja2"
    )
    s = _settings(host_home=root, repos_root=root)
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "unsupported"
    assert result.language == "csharp"
    assert "community" in result.message.lower()


async def test_request_scan_disabled(mem_session: AsyncSession, tmp_path) -> None:
    """sonar disabled → disabled (never a scan)."""
    _touch(os.path.join(str(tmp_path), "Main.kt"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    s = _settings(enabled=False, host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "disabled"
    assert "disabled" in result.message.lower()


async def test_request_scan_unconfigured_no_key(mem_session: AsyncSession, tmp_path) -> None:
    """No project key → unconfigured (run setup first)."""
    board, _ = await _seed_board(mem_session, project_key=None, repos_path=str(tmp_path))
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "unconfigured"
    assert result.project_key is None


async def test_request_scan_null_repos_path_error(mem_session: AsyncSession) -> None:
    """No repos_path → error (graceful, never 500)."""
    board, _ = await _seed_board(mem_session, repos_path=None, project_key="GameX")
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "error"
    assert "repos_path" in result.message.lower()


async def test_request_scan_bad_path_error(mem_session: AsyncSession) -> None:
    """A repos_path outside HOST_HOME (RepoPathError) → error, never 500."""
    board, _ = await _seed_board(
        mem_session, repos_path="/etc/not-under-home", project_key="GameX"
    )
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "error"


async def test_build_scan_plan_frozen_shape(mem_session: AsyncSession, tmp_path) -> None:
    """The scan plan carries the FROZEN field set the host runner depends on."""
    _touch(os.path.join(str(tmp_path), "src", "Main.kt"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plan = sonarqube.build_scan_plan(board)
    assert plan.project_key == "GameX"
    assert plan.container_source == str(tmp_path)
    assert plan.host_source == str(tmp_path)
    assert plan.language == "kotlin"
    assert plan.supported is True
    assert plan.exclusions is not None and "build" in plan.exclusions
    assert isinstance(plan.reason, str) and plan.reason
    # PH-244: frozen key set must be exactly these 7 fields — no new key added (the
    # exclusions VALUE broadens / becomes language-conditional, but the SHAPE is frozen).
    assert set(plan.__dataclass_fields__) == {
        "project_key",
        "container_source",
        "host_source",
        "language",
        "supported",
        "reason",
        "exclusions",
    }


# ===========================================================================
# PH-244 — broadened vendored/native exclusions + java-without-binaries guard
# ===========================================================================
#
# Fix A: _SCAN_EXCLUSIONS broadened to cover the 6.1 GB vendored LiteRT/NDK/bazel tree
# that aborted the GXA scan. Fix B1: build_scan_plan appends **/*.java for non-java
# boards so JavaSensor has no input → cannot hard-abort. Fix B2 (script-side, the static
# -Dsonar.java.binaries=. guard) is asserted in the shell-grep block below.

# Each glob must be present in the broadened base set (AC #1). None of these match
# legitimate app source (src/main/{kotlin,java}, app/, frontend/src, backend/app).
_PH244_VENDORED_GLOBS = [
    "**/cpp/**",
    "**/.cxx/**",
    "**/*.so",
    "**/*.a",
    "**/*.o",
    "**/external/**",
    "**/bazel-*/**",
    "**/third_party/**",
    "**/vendor/**",
    "**/androidndk/**",
    "**/ndk/**",
    "**/toolchains/**",
]


def test_scan_exclusions_cover_vendored_native_globs() -> None:
    """PH-244 Fix A: _SCAN_EXCLUSIONS retains ALL the original globs AND adds the
    vendored/native/generated coverage that keeps multi-GB native trees out of the scan."""
    excl = sonarqube._SCAN_EXCLUSIONS
    # Original globs preserved (regression guard).
    for original in ("**/build/**", "**/.gradle/**", "**/node_modules/**", "**/venv/**"):
        assert original in excl, f"original glob {original} dropped"
    # New vendored/native/generated globs present.
    for glob in _PH244_VENDORED_GLOBS:
        assert glob in excl, f"vendored glob {glob} missing from _SCAN_EXCLUSIONS"
    # The base constant must NOT carry **/*.java — that is language-conditional (Fix B1),
    # appended per-plan only for non-java boards (a java board keeps .java analyzable).
    assert "**/*.java" not in excl


async def test_build_scan_plan_non_java_board_excludes_java(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-244 Fix B1: a non-java (kotlin GXA-like) board's plan excludes **/*.java so the
    JavaSensor has nothing to analyze and cannot abort with AnalysisException."""
    _touch(os.path.join(str(tmp_path), "src", "Main.kt"))  # → language=kotlin
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plan = sonarqube.build_scan_plan(board)
    assert plan.language == "kotlin"
    assert plan.exclusions is not None
    assert "**/*.java" in plan.exclusions
    # The vendored globs still ride along (base set plus the java exclusion).
    assert "**/cpp/**" in plan.exclusions


async def test_build_scan_plan_unknown_language_board_excludes_java(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-244 Fix B1: an unknown-language board (no recognized source → language=None)
    still excludes **/*.java — None != "java", so incidental java is dropped."""
    # Empty tree → detect_board_language returns None.
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plan = sonarqube.build_scan_plan(board)
    assert plan.language is None
    assert plan.exclusions is not None and "**/*.java" in plan.exclusions


async def test_build_scan_plan_java_board_keeps_java_analyzable(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-244 Fix B1: a java-PRIMARY board does NOT exclude **/*.java — real java stays
    analyzable (only the universal script-side java.binaries guard protects it)."""
    # A tree dominated by .java → detect_board_language returns "java".
    for i in range(3):
        _touch(os.path.join(str(tmp_path), "src", f"App{i}.java"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plan = sonarqube.build_scan_plan(board)
    assert plan.language == "java"
    assert plan.exclusions is not None
    assert "**/*.java" not in plan.exclusions
    # The vendored globs are still present for a java board (Fix A is unconditional).
    assert "**/cpp/**" in plan.exclusions


# ===========================================================================
# PH-243 — board-scan config isolation (projectBaseDir + empty sonar.tests)
# ===========================================================================
#
# The isolation is enforced SCRIPT-SIDE (scripts/sonar-scan-board.sh), since the -D
# flags are passed by the host runner, not the backend. The backend's contribution is
# the load-bearing ``container_source`` that the script uses as BOTH -Dsonar.sources
# AND (new) -Dsonar.projectBaseDir. We assert (a) build_scan_plan still emits the
# container_source the script will pin baseDir to, and (b) the script's -D block
# actually carries the isolation flags (grep on the shell source — the boundary we can
# verify without docker). The live ncloc>0 / no-PH-components check is the Coordinator's
# post-merge GXA re-scan (AC #2).

_BOARD_SCAN_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "sonar-scan-board.sh"
)
_PH_SELF_SCAN_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "scripts", "sonar-scan.sh"
)


async def test_scan_plan_emits_container_source_used_as_basedir(
    mem_session: AsyncSession, tmp_path
) -> None:
    """The script pins -Dsonar.projectBaseDir=$CONTAINER_SOURCE; assert build_scan_plan
    emits exactly that container_source (a non-empty in-container path) so the script's
    baseDir/sources both anchor to the board tree, not /usr/src."""
    _touch(os.path.join(str(tmp_path), "src", "Main.kt"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plan = sonarqube.build_scan_plan(board)
    assert plan.container_source == str(tmp_path)
    assert plan.container_source  # non-empty → baseDir is a real board path


@pytest.mark.skipif(
    not os.path.isfile(_BOARD_SCAN_SCRIPT),
    reason="scripts/ not mounted in this runtime (host-only); shell verified by bash -n + AC",
)
def test_board_scan_script_carries_basedir_and_empty_tests_isolation() -> None:
    """PH-243 shell-level gate: the board-scan -D block sets projectBaseDir to
    $CONTAINER_SOURCE and an explicit empty sonar.tests (so PH's sonar-project.properties
    + backend/tests,tests can never bleed into a board scan)."""
    with open(_BOARD_SCAN_SCRIPT) as fh:
        src = fh.read()
    assert '-Dsonar.projectBaseDir="$CONTAINER_SOURCE"' in src
    assert "-Dsonar.tests=" in src
    # The board scan must still target the board's own sources + key.
    assert '-Dsonar.sources="$CONTAINER_SOURCE"' in src
    assert '-Dsonar.projectKey="$PROJECT_KEY"' in src
    # PH-244 Fix B2: the static java-binaries guard so JavaSensor never hard-aborts on
    # a stray .java. Existing exclusions flag still present (Fix A/B1 ride through it).
    assert "-Dsonar.java.binaries=." in src
    assert '-Dsonar.exclusions="${EXCLUSIONS:-}"' in src


@pytest.mark.skipif(
    not os.path.isfile(_PH_SELF_SCAN_SCRIPT),
    reason="scripts/ not mounted in this runtime (host-only); shell verified by git diff + AC",
)
def test_ph_self_scan_script_unchanged_no_basedir() -> None:
    """PH-243 hard rule: the PH self-scan (sonar-scan.sh) is UNTOUCHED — it must NOT
    pin projectBaseDir (keeps CWD /usr/src so it still loads sonar-project.properties)."""
    with open(_PH_SELF_SCAN_SCRIPT) as fh:
        src = fh.read()
    assert "projectBaseDir" not in src
    assert "-Dsonar.tests=" not in src
    # PH-244 hard rule: the board-scan-only java-binaries guard must NOT appear in the
    # PH self-scan (PH has no .java; the static guard is board-scan-only).
    assert "sonar.java.binaries" not in src


# ===========================================================================
# PH-242 — scan source prefers primary repository.local_path over repos_path
# ===========================================================================


async def test_resolve_source_prefers_primary_repo_local_path(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a primary repo whose local_path (/repos/...) differs from the repos_path
    parent THEN _resolve_container_source returns local_path as-is (NOT repos_path)."""
    board, _ = await _seed_board(
        mem_session,
        repos_path="/Users/huseyinkanat/AndroidStudioProjects/GameX",
        repo_local_path="/repos/AndroidStudioProjects/GameX/GameXCore",
        project_key="GameX",
    )
    # PROD-form settings: repos_root=/repos (the live container mount), host_home the
    # real HOST_HOME — so /repos/... is recognized as container-form. Patch BOTH
    # sonarqube (the repos_root prefix check) AND repo_paths (any to_container_path).
    p1, p2, _ = _prod_patch()
    with p1, p2:
        source, reason = sonarqube._resolve_container_source(board)
    # local_path is already container-form → used as-is, NOT re-translated, NOT the
    # repos_path-derived parent (/repos/AndroidStudioProjects/GameX).
    assert source == "/repos/AndroidStudioProjects/GameX/GameXCore"
    assert reason is None


async def test_build_scan_plan_gxa_binds_to_gamexcore_subdir(
    mem_session: AsyncSession, tmp_path
) -> None:
    """GIVEN board GXA (repos_path parent + primary repo local_path GameXCore subdir)
    THEN build_scan_plan.container_source == the GameXCore code root, and the
    persisted project_key (GameX) is unchanged (no derived-key drift)."""
    # Lay a real Kotlin tree at the SUBDIR the local_path points to; the parent stays
    # empty so a repos_path scan would (wrongly) detect nothing.
    code_root = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(code_root, "app", "src", "Main.kt"))
    board, _ = await _seed_board(
        mem_session,
        repos_path=str(tmp_path),
        repo_local_path=code_root,  # container-form under the test repos_root
        project_key="GameX",
    )
    # repos_root == tmp_path so code_root starts with repos_root → used as-is.
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        plan = sonarqube.build_scan_plan(board)
    assert plan.container_source == code_root  # the GameXCore subdir, NOT tmp_path
    assert plan.project_key == "GameX"  # persisted key retained — no drift
    assert plan.language == "kotlin"
    assert plan.supported is True
    # host_source is the cosmetic host equivalent of the scanned tree (best-effort).
    assert plan.host_source == code_root  # to_host_path identity under test settings


async def test_resolve_source_no_repo_falls_back_to_repos_path(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a board with NO repositories THEN it falls back to the repos_path-derived
    container path (current behavior, byte-for-byte)."""
    board, _ = await _seed_board(
        mem_session,
        repos_path="/Users/huseyinkanat/Documents/project-hub",
        project_key="project-hub",
    )
    p1, p2, _ = _prod_patch()
    with p1, p2:
        source, reason = sonarqube._resolve_container_source(board)
    assert source == "/repos/Documents/project-hub"
    assert reason is None


async def test_resolve_source_ph_kim_style_root_unchanged(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a PH/KIM-style board whose repos_path IS the code root and no linked
    primary repo THEN container_source is unchanged (repos_path-derived)."""
    for key, repos_path, expected in (
        ("PH", "/Users/huseyinkanat/Documents/project-hub", "/repos/Documents/project-hub"),
        ("KIM", "/Users/huseyinkanat/Documents/kims", "/repos/Documents/kims"),
    ):
        board, _ = await _seed_board(
            mem_session, key=key, repos_path=repos_path, project_key="x"
        )
        p1, p2, _ = _prod_patch()
        with p1, p2:
            source, reason = sonarqube._resolve_container_source(board)
        assert source == expected
        assert reason is None


async def test_resolve_source_repos_with_no_primary_falls_back(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a board with repositories but NONE marked is_primary THEN it uses the
    repos_path fallback (no crash, deterministic)."""
    board, _ = await _seed_board(
        mem_session,
        repos_path="/Users/huseyinkanat/Documents/project-hub",
        repo_local_path="/repos/Documents/project-hub/sub",
        repo_is_primary=False,  # not primary → primary_repository is None → fallback
        project_key="project-hub",
    )
    p1, p2, _ = _prod_patch()
    with p1, p2:
        source, reason = sonarqube._resolve_container_source(board)
    assert source == "/repos/Documents/project-hub"
    assert reason is None


async def test_resolve_source_legacy_host_form_local_path_normalized(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a primary repo whose local_path is (defensively) stored in HOST form
    under HOST_HOME THEN it is normalized via to_container_path to /repos/..."""
    board, _ = await _seed_board(
        mem_session,
        repos_path="/Users/huseyinkanat/AndroidStudioProjects/GameX",
        # Legacy host-form row (does NOT start with /repos) — defensive branch.
        repo_local_path="/Users/huseyinkanat/AndroidStudioProjects/GameX/GameXCore",
        project_key="GameX",
    )
    p1, p2, _ = _prod_patch()
    with p1, p2:
        source, reason = sonarqube._resolve_container_source(board)
    assert source == "/repos/AndroidStudioProjects/GameX/GameXCore"
    assert reason is None


async def test_resolve_source_unnormalizable_local_path_falls_back(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a primary repo with a local_path neither container-form nor under
    HOST_HOME (unresolvable) THEN it never 500s and falls back to repos_path."""
    board, _ = await _seed_board(
        mem_session,
        repos_path="/Users/huseyinkanat/Documents/project-hub",
        repo_local_path="/etc/not-under-home/junk",  # not /repos, not under HOST_HOME
        project_key="project-hub",
    )
    p1, p2, _ = _prod_patch()
    with p1, p2:
        source, reason = sonarqube._resolve_container_source(board)
    assert source == "/repos/Documents/project-hub"  # graceful fallback
    assert reason is None


async def test_resolve_source_unloaded_repositories_falls_back_no_crash(
    mem_session: AsyncSession,
) -> None:
    """GIVEN a Board whose `repositories` relationship is NOT eager-loaded (a bare
    query) THEN _resolve_container_source falls back to repos_path WITHOUT triggering
    an async lazy-load (MissingGreenlet) — never-500 async-safety guard (PH-242)."""
    board, _ = await _seed_board(
        mem_session,
        repos_path="/Users/huseyinkanat/Documents/project-hub",
        repo_local_path="/repos/Documents/project-hub/GameXCore",  # would-win IF loaded
        project_key="project-hub",
    )
    # Re-fetch the board WITHOUT selectinload(Board.repositories) → unloaded collection.
    bare = (
        await mem_session.execute(select(Board).where(Board.id == board.id))
    ).scalar_one()
    mem_session.expire(bare, ["repositories"])  # ensure the relationship is unloaded
    p1, p2, _ = _prod_patch()
    with p1, p2:
        source, reason = sonarqube._resolve_container_source(bare)
    # Guard treats the unloaded collection as "no primary repo" → repos_path fallback,
    # NOT the repo local_path, and crucially does NOT raise.
    assert source == "/repos/Documents/project-hub"
    assert reason is None


# ===========================================================================
# Endpoint layer — TestClient with DI overrides
# ===========================================================================


def _make_client(actor: Actor, session: AsyncSession) -> TestClient:
    from app.api.deps import current_actor

    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def _clear() -> None:
    app.dependency_overrides.clear()


async def test_endpoint_scan_queued_admin_200(mem_session: AsyncSession, tmp_path) -> None:
    _touch(os.path.join(str(tmp_path), "src", "Main.kt"))
    board, admin = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    client = _make_client(admin, mem_session)
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/scan")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_status"] == "queued"
    assert data["project_key"] == "GameX"
    assert data["language"] == "kotlin"
    assert _SECRET not in resp.text


async def test_endpoint_scan_unsupported_csharp_200(mem_session: AsyncSession, tmp_path) -> None:
    root = str(tmp_path)
    _touch(os.path.join(root, "Assets", "Scripts", "Blade.cs"))
    os.makedirs(os.path.join(root, "ProjectSettings"), exist_ok=True)
    board, admin = await _seed_board(
        mem_session, key="FN", repos_path=root, project_key="fruit-ninja2"
    )
    client = _make_client(admin, mem_session)
    s = _settings(host_home=root, repos_root=root)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/scan")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_status"] == "unsupported"
    assert data["language"] == "csharp"


async def test_endpoint_scan_null_path_graceful_200(mem_session: AsyncSession) -> None:
    """A board with no repos_path → 200 error status, NEVER 500."""
    board, admin = await _seed_board(mem_session, repos_path=None, project_key="GameX")
    client = _make_client(admin, mem_session)
    s = _settings()
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/scan")
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json()["scan_status"] == "error"


async def test_endpoint_scan_non_admin_via_key_is_403(mem_session: AsyncSession, tmp_path) -> None:
    """PH-233: a non-admin calling scan via the board KEY → 403 (admin-gated)."""
    _touch(os.path.join(str(tmp_path), "Main.kt"))
    board, member = await _seed_board(
        mem_session, repos_path=str(tmp_path), project_key="GameX", member_role="developer"
    )
    client = _make_client(member, mem_session)
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.post(f"/api/boards/{board.key}/sonarqube/scan")
    finally:
        _clear()
    assert resp.status_code == 403


async def test_endpoint_scan_admin_via_key_is_allowed(mem_session: AsyncSession, tmp_path) -> None:
    """An admin calling scan via the board KEY → 200 (PH-233 key-or-uuid)."""
    _touch(os.path.join(str(tmp_path), "Main.kt"))
    board, admin = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    client = _make_client(admin, mem_session)
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.post(f"/api/boards/{board.key}/sonarqube/scan")
    finally:
        _clear()
    assert resp.status_code == 200


async def test_endpoint_scan_unknown_board_is_404(mem_session: AsyncSession) -> None:
    _, admin = await _seed_board(mem_session)
    client = _make_client(admin, mem_session)
    s = _settings()
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.post(f"/api/boards/{uuid4()}/sonarqube/scan")
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_scan_plan_frozen_shape(mem_session: AsyncSession, tmp_path) -> None:
    """The scan-plan endpoint returns the FROZEN JSON the host runner consumes."""
    _touch(os.path.join(str(tmp_path), "src", "Main.kt"))
    board, admin = await _seed_board(mem_session, repos_path=str(tmp_path), project_key="GameX")
    client = _make_client(admin, mem_session)
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/scan-plan")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    # FROZEN field set — the host script + C3 depend on EXACTLY these keys.
    assert set(data.keys()) == {
        "project_key",
        "container_source",
        "host_source",
        "language",
        "supported",
        "reason",
        "exclusions",
    }
    assert data["project_key"] == "GameX"
    assert data["container_source"] == str(tmp_path)
    assert data["supported"] is True
    assert data["language"] == "kotlin"
    assert _SECRET not in resp.text


async def test_endpoint_scan_plan_unsupported_csharp(mem_session: AsyncSession, tmp_path) -> None:
    root = str(tmp_path)
    _touch(os.path.join(root, "Assets", "Scripts", "Blade.cs"))
    os.makedirs(os.path.join(root, "ProjectSettings"), exist_ok=True)
    board, admin = await _seed_board(
        mem_session, key="FN", repos_path=root, project_key="fruit-ninja2"
    )
    client = _make_client(admin, mem_session)
    s = _settings(host_home=root, repos_root=root)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/scan-plan")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    assert data["supported"] is False
    assert data["language"] == "csharp"


async def test_endpoint_scan_plan_non_admin_via_key_is_403(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "Main.kt"))
    board, member = await _seed_board(
        mem_session, repos_path=str(tmp_path), project_key="GameX", member_role="developer"
    )
    client = _make_client(member, mem_session)
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=s),
            patch.object(repo_paths, "get_settings", return_value=s),
            patch("app.api.boards.get_settings", return_value=s),
        ):
            resp = client.get(f"/api/boards/{board.key}/sonarqube/scan-plan")
    finally:
        _clear()
    assert resp.status_code == 403
