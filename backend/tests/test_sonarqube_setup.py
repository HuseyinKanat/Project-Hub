"""PH-223 — tests for SonarQube one-click setup + sync-now + status.

Two layers, both network-free:

  Service (services/sonarqube.{setup_board_project, sync_board_now,
  build_setup_status}) — a real in-memory sqlite session (the poll-test pattern)
  exercises the persist + idempotency + cached-metric assembly for real. The httpx
  layer is patched via ``fetch_board_metrics`` / EventBus so no network is touched.

  Endpoint (api/boards.api_board_sonarqube_{setup,sync,status}) — a FastAPI
  ``TestClient`` with ``current_actor`` + ``get_db_session`` DI overrides (the
  membership / issues-endpoint pattern). Covers: setup persists derived + custom key
  + idempotency, sync re-polls + updates cache, status no-mutation, the disabled +
  unreachable graceful-200 paths (never 500), the admin gate (403 for non-admin), and
  the SECRET-FREE invariant (no token anywhere in any response body).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, SonarQubeMetric, Workflow
from app.db.session import get_db_session
from app.main import app
from app.services import sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.sonarqube import SonarSnapshot

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
    key: str = "PH",
    project_key: str | None = None,
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
        name="ProjectHub",
        description="Test board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=actor.id,
        sonarqube_project_key=project_key,
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
                selectinload(Board.sonarqube_metrics),
            )
        )
    ).scalar_one()
    refreshed_actor = (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    return refreshed_board, refreshed_actor


def _settings(*, enabled: bool = True, scan_url: str = "http://localhost:9000") -> MagicMock:
    settings = MagicMock()
    settings.sonarqube_enabled = enabled
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_scan_url = scan_url
    settings.sonarqube_token = _SECRET
    settings.sonarqube_project_key_map = ""
    return settings


def _snapshot() -> SonarSnapshot:
    return SonarSnapshot(
        quality_gate_status="OK",
        bugs=1,
        vulnerabilities=0,
        code_smells=3,
        coverage=Decimal("88.5"),
        duplicated_lines_density=Decimal("1.2"),
        ncloc=1000,
        raw_measures={"bugs": "1"},
    )


# ===========================================================================
# Service layer — real sqlite, httpx layer patched
# ===========================================================================


def test_derive_default_project_key_ph_is_project_hub() -> None:
    ph = MagicMock()
    ph.key = "PH"
    ph.repos_path = "/Users/huseyinkanat/Documents/project-hub"
    # PH literal FIRST — never basename-derived, even though the basename would
    # also be "project-hub" (must not depend on that coincidence).
    assert sonarqube.derive_default_project_key(ph) == "project-hub"
    other = MagicMock()
    other.key = "SHOP"
    other.repos_path = None
    assert sonarqube.derive_default_project_key(other) == "shop"


def test_derive_default_project_key_basename_for_non_ph_with_path() -> None:
    """PH-229: a non-PH board WITH a repos_path derives the key from the basename."""
    kim = MagicMock()
    kim.key = "KIM"
    kim.repos_path = "/Users/huseyinkanat/Documents/kims"
    # basename "kims" overrides the bare key-lower "kim".
    assert sonarqube.derive_default_project_key(kim) == "kims"

    gxa = MagicMock()
    gxa.key = "GXA"
    gxa.repos_path = "/Users/huseyinkanat/AndroidStudioProjects/GameX"
    assert sonarqube.derive_default_project_key(gxa) == "GameX"


def test_derive_default_project_key_null_path_falls_back_to_key() -> None:
    """A non-PH board with no path → board.key.lower() (graceful)."""
    board = MagicMock()
    board.key = "SHOP"
    board.repos_path = None
    assert sonarqube.derive_default_project_key(board) == "shop"


def test_derive_default_project_key_repopath_error_falls_back_to_key() -> None:
    """A non-PH board whose path is outside HOST_HOME / has '..' → key default."""
    outside = MagicMock()
    outside.key = " X "  # only the key matters; basename must not be used
    outside.key = "SHOP"
    outside.repos_path = "/etc/not-under-home"
    assert sonarqube.derive_default_project_key(outside) == "shop"

    traversal = MagicMock()
    traversal.key = "SHOP"
    traversal.repos_path = "/Users/huseyinkanat/../escape"
    assert sonarqube.derive_default_project_key(traversal) == "shop"


async def test_setup_board_project_basename_default_non_ph(mem_session: AsyncSession) -> None:
    """PH-229: setup on a non-PH board with a repos_path persists the basename key."""
    board, _ = await _seed_board(mem_session, key="KIM", project_key=None)
    board.repos_path = "/Users/huseyinkanat/Documents/kims"
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        key = await sonarqube.setup_board_project(mem_session, board, None)
    assert key == "kims"
    assert board.sonarqube_project_key == "kims"


async def test_setup_board_project_ph_still_project_hub_with_path(
    mem_session: AsyncSession,
) -> None:
    """PH board keeps 'project-hub' even with a repos_path set (literal precedence)."""
    board, _ = await _seed_board(mem_session, key="PH", project_key=None)
    board.repos_path = "/Users/huseyinkanat/Documents/project-hub"
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        key = await sonarqube.setup_board_project(mem_session, board, None)
    assert key == "project-hub"


async def test_setup_board_project_bad_path_graceful(mem_session: AsyncSession) -> None:
    """A non-PH board with a RepoPathError path → key.lower() default, no 500."""
    board, _ = await _seed_board(mem_session, key="GXA", project_key=None)
    board.repos_path = "/etc/not-under-home"
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        key = await sonarqube.setup_board_project(mem_session, board, None)
    assert key == "gxa"


async def test_setup_board_project_derives_default(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key=None)
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        key = await sonarqube.setup_board_project(mem_session, board, None)
    assert key == "project-hub"
    assert board.sonarqube_project_key == "project-hub"


async def test_setup_board_project_custom_key_overrides(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key=None)
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        key = await sonarqube.setup_board_project(mem_session, board, "custom-key")
    assert key == "custom-key"
    assert board.sonarqube_project_key == "custom-key"


async def test_setup_board_project_idempotent(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key=None)
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        k1 = await sonarqube.setup_board_project(mem_session, board, None)
        k2 = await sonarqube.setup_board_project(mem_session, board, None)
    assert k1 == k2 == "project-hub"
    # Exactly one board, key unchanged on the second (no-op) call.
    assert board.sonarqube_project_key == "project-hub"


async def test_build_setup_status_disabled(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with patch.object(sonarqube, "get_settings", return_value=_settings(enabled=False)):
        status = await sonarqube.build_setup_status(mem_session, board)
    assert status.enabled is False
    assert status.reachable is False
    assert status.configured is True
    assert status.project_key == "project-hub"
    assert "disabled" in status.message.lower()
    # PH-235: explicit honest discriminator.
    assert status.status == "disabled"


async def test_build_setup_status_no_key(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key=None)
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        status = await sonarqube.build_setup_status(mem_session, board)
    assert status.configured is False
    assert status.project_key is None
    assert status.dashboard_url is None
    assert "no" in status.message.lower()
    # PH-235: enabled-but-no-key → unconfigured, no analysis.
    assert status.status == "unconfigured"
    assert status.has_analysis is False


# ===========================================================================
# PH-235 — status honesty: configured-but-no-analysis ≠ unreachable.
# These lock the bug fix: the pure-READ path (reachable=None) for a configured
# board with NO cached metric must report status=="no_analysis" (NOT a false
# "unreachable"), while a genuinely failed live sync (reachable=False) still
# reports "unreachable" so a real outage is never masked.
# ===========================================================================


async def test_build_setup_status_no_analysis_is_not_unreachable(
    mem_session: AsyncSession,
) -> None:
    """Read path, configured, NO metric → no_analysis (the bug case is now honest)."""
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        # reachable=None == the pure-read path (no live probe).
        status = await sonarqube.build_setup_status(mem_session, board)
    assert status.status == "no_analysis"
    assert status.has_analysis is False
    assert status.configured is True
    # The MESSAGE must not lie: no "unreachable" wording, an honest "no analysis".
    assert "unreachable" not in status.message.lower()
    assert "no analysis" in status.message.lower()
    assert status.message == "linked to project-hub — no analysis yet (run a scan)"


async def test_build_setup_status_ok_when_metric_present(mem_session: AsyncSession) -> None:
    """Read path, configured, metric present → ok / has_analysis (no regression)."""
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with (
        patch.object(sonarqube, "get_settings", return_value=_settings()),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        await sonarqube.poll_board(mem_session, board)
        status = await sonarqube.build_setup_status(mem_session, board)
    assert status.status == "ok"
    assert status.has_analysis is True
    assert status.message == "linked to project-hub"


async def test_build_setup_status_real_failed_sync_is_unreachable(
    mem_session: AsyncSession,
) -> None:
    """A REAL failed live sync (reachable=False) → unreachable (outage not masked)."""
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        # reachable=False == sync's poll_board returned False AFTER a live fetch.
        status = await sonarqube.build_setup_status(mem_session, board, reachable=False)
    assert status.status == "unreachable"
    assert status.reachable is False
    assert status.has_analysis is False
    assert "unreachable" in status.message.lower()


async def test_build_setup_status_dashboard_url_is_host_facing(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        status = await sonarqube.build_setup_status(mem_session, board)
    assert status.dashboard_url == "http://localhost:9000/dashboard?id=project-hub"
    # Never the compose-internal host, never the token.
    assert "sonarqube:9000" not in (status.dashboard_url or "")


async def test_build_setup_status_surfaces_cached_metric(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with (
        patch.object(sonarqube, "get_settings", return_value=_settings()),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        await sonarqube.poll_board(mem_session, board)
        status = await sonarqube.build_setup_status(mem_session, board)
    assert status.quality_gate_status == "OK"
    assert status.last_metric_fetched_at is not None
    assert status.reachable is True  # cached metric present


async def test_sync_board_now_repolls_and_updates_cache(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with (
        patch.object(sonarqube, "get_settings", return_value=_settings()),
        patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())
        ) as mock_fetch,
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        status = await sonarqube.sync_board_now(mem_session, board)

    mock_fetch.assert_awaited_once()
    assert status.reachable is True
    assert status.quality_gate_status == "OK"
    assert status.last_metric_fetched_at is not None
    # Cache row was upserted.
    metric = (
        await mem_session.execute(
            select(SonarQubeMetric).where(SonarQubeMetric.board_id == board.id)
        )
    ).scalar_one()
    assert metric.quality_gate_status == "OK"


async def test_sync_board_now_disabled_no_probe(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with (
        patch.object(sonarqube, "get_settings", return_value=_settings(enabled=False)),
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock()) as mock_fetch,
    ):
        status = await sonarqube.sync_board_now(mem_session, board)
    # Kill switch: NO live attempt at all.
    mock_fetch.assert_not_awaited()
    assert status.enabled is False
    assert status.reachable is False


async def test_sync_board_now_unreachable_graceful(mem_session: AsyncSession) -> None:
    board, _ = await _seed_board(mem_session, project_key="project-hub")
    with (
        patch.object(sonarqube, "get_settings", return_value=_settings()),
        # fetch returns None → poll_board returns False (down / 401 / unscanned).
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=None)),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        status = await sonarqube.sync_board_now(mem_session, board)
    assert status.reachable is False
    assert status.configured is True
    assert "unreachable" in status.message.lower() or "no analysis" in status.message.lower()


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


async def test_endpoint_setup_persists_derived_key(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/setup", json={})
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["project_key"] == "project-hub"
    assert data["enabled"] is True
    assert _SECRET not in resp.text


async def test_endpoint_setup_custom_key_overrides(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(
                f"/api/boards/{board.id}/sonarqube/setup",
                json={"project_key": "custom-key"},
            )
    finally:
        _clear()

    assert resp.status_code == 200
    assert resp.json()["project_key"] == "custom-key"


async def test_endpoint_setup_idempotent(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            r1 = client.post(f"/api/boards/{board.id}/sonarqube/setup", json={})
            r2 = client.post(f"/api/boards/{board.id}/sonarqube/setup", json={})
    finally:
        _clear()

    assert r1.status_code == r2.status_code == 200
    assert r1.json()["project_key"] == r2.json()["project_key"] == "project-hub"


async def test_endpoint_setup_disabled_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings(enabled=False)),
            patch("app.api.boards.get_settings", return_value=_settings(enabled=False)),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/setup", json={})
    finally:
        _clear()

    # Disabled → 200 status (key still persisted), NEVER 500.
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["reachable"] is False
    assert data["project_key"] == "project-hub"  # config allowed offline
    assert "disabled" in data["message"].lower()


async def test_endpoint_sync_repolls_and_updates(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
            patch.object(
                sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())
            ) as mock_fetch,
            patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/sync")
    finally:
        _clear()

    assert resp.status_code == 200
    mock_fetch.assert_awaited_once()
    data = resp.json()
    assert data["reachable"] is True
    assert data["quality_gate_status"] == "OK"
    assert data["last_metric_fetched_at"] is not None
    assert _SECRET not in resp.text


async def test_endpoint_sync_disabled_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings(enabled=False)),
            patch("app.api.boards.get_settings", return_value=_settings(enabled=False)),
            patch.object(sonarqube, "fetch_board_metrics", AsyncMock()) as mock_fetch,
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/sync")
    finally:
        _clear()

    assert resp.status_code == 200
    mock_fetch.assert_not_awaited()  # no live probe on a disabled server
    data = resp.json()
    assert data["enabled"] is False
    assert data["reachable"] is False


async def test_endpoint_sync_unreachable_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
            patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=None)),
            patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/sync")
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["reachable"] is False
    assert data["configured"] is True


async def test_endpoint_status_read_no_mutation(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
            # A status read must NEVER touch the network.
            patch.object(sonarqube, "fetch_board_metrics", AsyncMock()) as mock_fetch,
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/status")
    finally:
        _clear()

    assert resp.status_code == 200
    mock_fetch.assert_not_awaited()  # no blocking probe on the read path
    data = resp.json()
    assert data["configured"] is True
    assert data["project_key"] == "project-hub"
    # PH-235: the read-path response carries the honest status + has_analysis, and
    # a configured-but-unscanned board is no_analysis (NOT a false unreachable).
    assert data["status"] == "no_analysis"
    assert data["has_analysis"] is False
    assert _SECRET not in resp.text


async def test_endpoint_status_disabled_is_graceful_200(mem_session: AsyncSession) -> None:
    board, admin = await _seed_board(mem_session, project_key="project-hub")
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings(enabled=False)),
            patch("app.api.boards.get_settings", return_value=_settings(enabled=False)),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/status")
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["reachable"] is False


async def test_endpoint_setup_non_admin_is_403(mem_session: AsyncSession) -> None:
    board, member = await _seed_board(mem_session, project_key=None, member_role="developer")
    client = _make_client(member, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/setup", json={})
    finally:
        _clear()
    # Non-admin POST setup is rejected by the admin gate.
    assert resp.status_code == 403


async def test_endpoint_sync_non_admin_is_403(mem_session: AsyncSession) -> None:
    board, member = await _seed_board(
        mem_session, project_key="project-hub", member_role="developer"
    )
    client = _make_client(member, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/sync")
    finally:
        _clear()
    assert resp.status_code == 403


async def test_endpoint_status_allowed_for_non_admin_member(mem_session: AsyncSession) -> None:
    board, member = await _seed_board(
        mem_session, project_key="project-hub", member_role="developer"
    )
    client = _make_client(member, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.get(f"/api/boards/{board.id}/sonarqube/status")
    finally:
        _clear()
    # GET status is member-readable (current_actor gate, not admin).
    assert resp.status_code == 200


async def test_endpoint_setup_missing_board_is_404(mem_session: AsyncSession) -> None:
    _, admin = await _seed_board(mem_session, project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{uuid4()}/sonarqube/setup", json={})
    finally:
        _clear()
    # PH-233: an unknown board now resolves-before-authz → 404 NotFound, NEVER a
    # misleading 403. (Pre-fix the admin gate could only see a UUID and 403'd here.)
    assert resp.status_code == 404


# ===========================================================================
# PH-233 — require_board_admin must resolve the board KEY (not just UUID).
# The pre-fix gate did uuid.UUID(board_id) and 403'd on any KEY, so a genuine
# admin was blanket-denied for every key-based admin call. Every test below
# drives the endpoint with the board KEY (the UI always sends the key); these
# are red on the old gate, green after the get_board(key-or-uuid) rewrite.
# ===========================================================================


async def test_endpoint_setup_admin_via_key_is_allowed(mem_session: AsyncSession) -> None:
    """Canonical failing-first test: admin POST setup by KEY → 200 (was 403)."""
    board, admin = await _seed_board(mem_session, key="BENCH", project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{board.key}/sonarqube/setup", json={})
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json()["configured"] is True


async def test_endpoint_setup_admin_via_uuid_still_allowed(mem_session: AsyncSession) -> None:
    """No regression on the previously-working UUID path: admin setup by UUID → 200."""
    board, admin = await _seed_board(mem_session, key="BENCH", project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{board.id}/sonarqube/setup", json={})
    finally:
        _clear()
    assert resp.status_code == 200


async def test_endpoint_sync_admin_via_key_is_allowed(mem_session: AsyncSession) -> None:
    """Admin POST sync by KEY → 200 (the second admin-gated sonar endpoint)."""
    board, admin = await _seed_board(mem_session, key="BENCH", project_key="bench")
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
            patch.object(
                sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot())
            ),
            patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        ):
            resp = client.post(f"/api/boards/{board.key}/sonarqube/sync")
    finally:
        _clear()
    assert resp.status_code == 200


async def test_endpoint_setup_non_admin_via_key_is_403(mem_session: AsyncSession) -> None:
    """A genuine non-admin must STILL get 403 via the KEY path (authz, not deny-all)."""
    board, member = await _seed_board(
        mem_session, key="BENCH", project_key=None, member_role="developer"
    )
    client = _make_client(member, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post(f"/api/boards/{board.key}/sonarqube/setup", json={})
    finally:
        _clear()
    assert resp.status_code == 403


async def test_endpoint_setup_unknown_key_is_404_not_403(mem_session: AsyncSession) -> None:
    """Unknown board KEY with an admin token → 404 (resolve-before-authz), not 403."""
    _, admin = await _seed_board(mem_session, key="BENCH", project_key=None)
    client = _make_client(admin, mem_session)
    try:
        with (
            patch.object(sonarqube, "get_settings", return_value=_settings()),
            patch("app.api.boards.get_settings", return_value=_settings()),
        ):
            resp = client.post("/api/boards/ZZZNOSUCH/sonarqube/setup", json={})
    finally:
        _clear()
    assert resp.status_code == 404
