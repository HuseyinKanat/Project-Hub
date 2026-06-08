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

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Workflow
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
    await session.commit()

    refreshed_board = (
        await session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(
                selectinload(Board.workflow),
                selectinload(Board.memberships),
                selectinload(Board.sonarqube_metric),
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
    assert "scripts/sonar-scan-board.sh" in result.message
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
