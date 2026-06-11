"""PH-255 — per-repo SonarQube SCAN + SYNC endpoints (single-repo actions).

The per-repo successors of the board-level ``/sonarqube/{scan,sync}`` — each targets
ONE resolved repo (``{selector}`` = slug|id) on the boards router (reuses
``require_board_admin`` + ``resolve_repository`` verbatim, mirroring PH-254 setup).

Two network-free layers (mirrors test_sonarqube_multirepo.py / test_sonarqube_repo_setup.py):

  Service —
    ``request_repo_scan``: real in-memory sqlite + a real temp source tree (``.kt`` →
    scannable/queued; ``.cs`` → unsupported) exercises enqueue + per-(board,repo)
    idempotency + the honest non-scannable statuses (decision A — 200, no job).
    ``sync_repo_now``: ``fetch_board_metrics`` patched (network-free) for the
    with-analysis upsert; left unpatched (real client → fast-skip in-memory) is not
    used — the never-scanned path is forced via a patched ``poll_repo``-False / a fetch
    returning None so it falls through to ``unscanned_repo_health`` (decision B).

  Endpoint — a FastAPI TestClient with current_actor + get_db_session DI overrides:
    scan happy (200, queued, job_id set, exactly 1 job), scan unsupported (200,
    job_id=null, 0 jobs), scan disabled (200, job_id=null), sync with-analysis (200,
    live metrics + upserted row), sync never-scanned (200, honest unscanned card, NOT
    404), sync disabled (200, no live attempt), 404 unknown repo/board, 403 non-admin,
    and the SECRET-FREE invariants.
"""

from __future__ import annotations

import os
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
from app.db.models import (
    Actor,
    Board,
    BoardMembership,
    Repository,
    SonarQubeMetric,
    SonarScanJob,
    Workflow,
)
from app.db.session import get_db_session
from app.main import app
from app.services import repo_paths, sonarqube
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

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


def _settings(
    *, enabled: bool = True, dotnet_enabled: bool = False, root: str = "/Users/huseyinkanat"
) -> MagicMock:
    s = MagicMock()
    s.sonarqube_enabled = enabled
    # PH-257 — must be an explicit bool; a bare MagicMock attribute is truthy and would
    # silently flip a csharp board from needs_dotnet_setup to queued.
    s.sonar_dotnet_enabled = dotnet_enabled
    s.sonarqube_url = "http://sonarqube:9000"
    s.sonarqube_scan_url = "http://localhost:9000"
    s.sonarqube_token = _SECRET
    s.sonarqube_project_key_map = ""
    s.host_home = root
    s.repos_root = root
    return s


def _patch_all(settings: MagicMock):
    """Patch get_settings everywhere the scan/sync path reads it (svc + repo_paths + route)."""
    return (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(repo_paths, "get_settings", return_value=settings),
        patch("app.api.boards.get_settings", return_value=settings),
    )


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("// x\n")


async def _seed_board(
    session: AsyncSession,
    *,
    key: str = "GXA",
    project_key: str | None = "GameX",
    primary_local_path: str,
    sibling_specs: list[tuple[str, str]] | None = None,  # (slug, local_path)
    member_role: str = "admin",
) -> tuple[Board, Actor]:
    """Seed a board + admin/member actor + a primary repo + N siblings (distinct paths)."""
    workflow = Workflow(
        name=f"wf-{key}-{uuid4().hex[:6]}",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=False,
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
        repos_path=primary_local_path,
    )
    session.add(board)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=member_role))

    session.add(
        Repository(
            board_id=board.id,
            slug="gamexcore",
            name="GameX Core",
            is_primary=True,
            provider="local",
            default_branch="main",
            local_path=primary_local_path,
        )
    )
    for slug, local_path in sibling_specs or []:
        session.add(
            Repository(
                board_id=board.id,
                slug=slug,
                name=slug,
                is_primary=False,
                provider="local",
                default_branch="main",
                local_path=local_path,
            )
        )
    await session.commit()
    return await _refetch(session, board.id), await _refetch_actor(session, actor.id)


async def _refetch(session: AsyncSession, board_id) -> Board:
    return (
        await session.execute(
            select(Board)
            .where(Board.id == board_id)
            .options(
                selectinload(Board.workflow),
                selectinload(Board.memberships),
                selectinload(Board.repositories),
                selectinload(Board.sonarqube_metrics).selectinload(
                    SonarQubeMetric.repository
                ),
            )
        )
    ).scalar_one()


async def _refetch_actor(session: AsyncSession, actor_id) -> Actor:
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor_id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


def _repo_by_slug(board: Board, slug: str) -> Repository:
    return next(r for r in board.repositories if r.slug == slug)


def _snapshot(bugs: int = 3) -> sonarqube.SonarSnapshot:
    return sonarqube.SonarSnapshot(
        quality_gate_status="OK",
        bugs=bugs,
        vulnerabilities=0,
        code_smells=1,
        coverage=Decimal("80.00"),
        duplicated_lines_density=Decimal("1.00"),
        ncloc=100,
        raw_measures={"bugs": str(bugs)},
    )


# ===========================================================================
# Service: request_repo_scan — single-repo enqueue, idempotent, honest statuses
# ===========================================================================


async def test_service_scan_queues_one_job_for_scannable_repo(
    mem_session: AsyncSession, tmp_path
) -> None:
    """A scannable sibling (kotlin) → exactly ONE SonarScanJob for THAT repo_id."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, actor = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_repo_scan(
            mem_session, board, sibling, requested_by=actor.id
        )

    assert result.scan_status == "queued"
    assert result.repo_slug == "gamexsdk"
    assert result.project_key == "GameX-gamexsdk"
    assert result.job_id is not None
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 1  # only the targeted repo, not the whole board
    assert jobs[0].repo_id == sibling.id
    assert jobs[0].project_key == "GameX-gamexsdk"


async def test_service_scan_idempotent_per_board_repo(
    mem_session: AsyncSession, tmp_path
) -> None:
    """A 2nd scan while a queued job waits re-uses it (same job_id, no stacking)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, actor = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        r1 = await sonarqube.request_repo_scan(
            mem_session, board, sibling, requested_by=actor.id
        )
        r2 = await sonarqube.request_repo_scan(
            mem_session, board, sibling, requested_by=actor.id
        )

    assert r1.job_id == r2.job_id  # same job re-used
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 1  # not stacked to 2


async def test_service_scan_csharp_needs_dotnet_setup_no_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-257 / Decision A: a C# repo with SONAR_DOTNET_ENABLED off → 'needs_dotnet_setup',
    NO job, job_id=None (not 409/422). Honest skip, NOT the old 'unsupported' nor a fake
    queued.
    """
    core = os.path.join(str(tmp_path), "GameXCore")
    cs = os.path.join(str(tmp_path), "gamexsharp")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(cs, "src", "Program.cs"))
    board, actor = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsharp", cs)]
    )
    sharp = _repo_by_slug(board, "gamexsharp")
    s = _settings(dotnet_enabled=False, root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_repo_scan(
            mem_session, board, sharp, requested_by=actor.id
        )

    assert result.scan_status == "needs_dotnet_setup"
    assert result.job_id is None
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 0  # nothing enqueued for a non-scannable repo


async def test_service_scan_csharp_flag_on_queues_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-257: the SAME C# repo with SONAR_DOTNET_ENABLED=true → 'queued' + exactly 1 job."""
    core = os.path.join(str(tmp_path), "GameXCore")
    cs = os.path.join(str(tmp_path), "gamexsharp")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(cs, "src", "Program.cs"))
    board, actor = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsharp", cs)]
    )
    sharp = _repo_by_slug(board, "gamexsharp")
    s = _settings(dotnet_enabled=True, root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_repo_scan(
            mem_session, board, sharp, requested_by=actor.id
        )

    assert result.scan_status == "queued"
    assert result.job_id is not None
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 1


async def test_service_scan_disabled_no_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """sonar disabled → 'disabled', no job (never a fake queued)."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, actor = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(enabled=False, root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with p1, p2:
        result = await sonarqube.request_repo_scan(
            mem_session, board, sibling, requested_by=actor.id
        )

    assert result.scan_status == "disabled"
    assert result.job_id is None
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 0


# ===========================================================================
# Service: sync_repo_now — re-poll upsert (analysis) / honest unscanned (none)
# ===========================================================================


async def test_service_sync_with_analysis_upserts_metric(
    mem_session: AsyncSession, tmp_path
) -> None:
    """Reachable + has analysis → poll_repo upserts the (board,repo) row + live RepoHealth."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, _ = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with (
        p1,
        p2,
        patch.object(
            sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(3))
        ),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        health = await sonarqube.sync_repo_now(mem_session, board, sibling)

    assert health.repo_slug == "gamexsdk"
    assert health.project_key == "GameX-gamexsdk"
    assert health.quality_gate_status == "OK"
    assert health.bugs == 3
    assert health.fetched_at is not None
    metrics = (
        await mem_session.execute(
            select(SonarQubeMetric).where(
                SonarQubeMetric.board_id == board.id,
                SonarQubeMetric.repo_id == sibling.id,
            )
        )
    ).scalars().all()
    assert len(metrics) == 1  # exactly one (board_id, repo_id) row


async def test_service_sync_never_scanned_returns_unscanned_card(
    mem_session: AsyncSession, tmp_path
) -> None:
    """Decision B: fetch None (never scanned) → honest unscanned RepoHealth, no row, no raise."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, _ = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, _ = _patch_all(s)
    with (
        p1,
        p2,
        patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=None)),
        patch.object(sonarqube.EventBus, "publish", AsyncMock()),
    ):
        health = await sonarqube.sync_repo_now(mem_session, board, sibling)

    assert health.repo_slug == "gamexsdk"
    assert health.project_key == "GameX-gamexsdk"  # derived, non-null
    assert health.quality_gate_status is None
    assert health.fetched_at is None
    assert health.dashboard_url is None  # PH-252: null until first scan
    metrics = (
        await mem_session.execute(
            select(SonarQubeMetric).where(SonarQubeMetric.board_id == board.id)
        )
    ).scalars().all()
    assert len(metrics) == 0  # nothing written for a never-scanned repo


async def test_service_sync_disabled_no_live_attempt(
    mem_session: AsyncSession, tmp_path
) -> None:
    """sonar disabled → NO fetch_board_metrics call, honest current (unscanned) card."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, _ = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(enabled=False, root=str(tmp_path))
    fetch = AsyncMock(return_value=_snapshot(3))
    p1, p2, _ = _patch_all(s)
    with p1, p2, patch.object(sonarqube, "fetch_board_metrics", fetch):
        health = await sonarqube.sync_repo_now(mem_session, board, sibling)

    fetch.assert_not_awaited()  # kill switch: no live attempt
    assert health.repo_slug == "gamexsdk"
    assert health.quality_gate_status is None  # current state = unscanned


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


def _scan_url(board: Board, selector: str) -> str:
    return f"/api/boards/{board.id}/repositories/{selector}/sonarqube/scan"


def _sync_url(board: Board, selector: str) -> str:
    return f"/api/boards/{board.id}/repositories/{selector}/sonarqube/sync"


# --- scan endpoint ---------------------------------------------------------


async def test_endpoint_scan_queued_returns_job_id(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_scan_url(board, sibling.slug))
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_status"] == "queued"
    assert data["repo_slug"] == "gamexsdk"
    assert data["project_key"] == "GameX-gamexsdk"
    assert data["job_id"] is not None
    assert _SECRET not in resp.text
    assert "sonarqube:9000" not in resp.text


async def test_endpoint_scan_resolves_by_repo_id(
    mem_session: AsyncSession, tmp_path
) -> None:
    """Selector may be a row id (not just a slug) — resolve_repository id-first."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_scan_url(board, str(sibling.id)))
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json()["repo_slug"] == "gamexsdk"


async def test_endpoint_scan_csharp_needs_dotnet_setup_is_200_no_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-257 / Decision A: a C# repo (flag off) → 200 needs_dotnet_setup, job_id=null,
    never 409/422.
    """
    core = os.path.join(str(tmp_path), "GameXCore")
    cs = os.path.join(str(tmp_path), "gamexsharp")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(cs, "src", "Program.cs"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsharp", cs)]
    )
    sharp = _repo_by_slug(board, "gamexsharp")
    s = _settings(dotnet_enabled=False, root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_scan_url(board, sharp.slug))
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_status"] == "needs_dotnet_setup"
    assert data["job_id"] is None


async def test_endpoint_scan_disabled_is_200(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(enabled=False, root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_scan_url(board, sibling.slug))
    finally:
        _clear()

    assert resp.status_code == 200
    assert resp.json()["scan_status"] == "disabled"
    assert resp.json()["job_id"] is None


async def test_endpoint_scan_unknown_repo_is_404(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(core, "src", "Main.kt"))
    board, admin = await _seed_board(mem_session, primary_local_path=core)
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_scan_url(board, "no-such-repo"))
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_scan_unknown_board_is_404(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(core, "src", "Main.kt"))
    _, admin = await _seed_board(mem_session, primary_local_path=core)
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(
                f"/api/boards/{uuid4()}/repositories/gamexcore/sonarqube/scan"
            )
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_scan_non_admin_is_403(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(core, "src", "Main.kt"))
    board, member = await _seed_board(
        mem_session, primary_local_path=core, member_role="developer"
    )
    primary = _repo_by_slug(board, "gamexcore")
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(member, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_scan_url(board, primary.slug))
    finally:
        _clear()
    assert resp.status_code == 403


# --- sync endpoint ---------------------------------------------------------


async def test_endpoint_sync_with_analysis_returns_live_metrics(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with (
            p1,
            p2,
            p3,
            patch.object(
                sonarqube, "fetch_board_metrics", AsyncMock(return_value=_snapshot(3))
            ),
            patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        ):
            resp = client.post(_sync_url(board, sibling.slug))
    finally:
        _clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_slug"] == "gamexsdk"
    assert data["quality_gate_status"] == "OK"
    assert data["bugs"] == 3
    assert data["fetched_at"] is not None
    assert _SECRET not in resp.text
    assert "sonarqube:9000" not in resp.text


async def test_endpoint_sync_never_scanned_is_200_unscanned(
    mem_session: AsyncSession, tmp_path
) -> None:
    """Decision B: never scanned → 200 honest unscanned card, NOT a 404."""
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with (
            p1,
            p2,
            p3,
            patch.object(sonarqube, "fetch_board_metrics", AsyncMock(return_value=None)),
            patch.object(sonarqube.EventBus, "publish", AsyncMock()),
        ):
            resp = client.post(_sync_url(board, sibling.slug))
    finally:
        _clear()

    assert resp.status_code == 200  # not 404
    data = resp.json()
    assert data["project_key"] == "GameX-gamexsdk"
    assert data["quality_gate_status"] is None
    assert data["fetched_at"] is None
    assert data["dashboard_url"] is None


async def test_endpoint_sync_disabled_is_200(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    sdk = os.path.join(str(tmp_path), "gamexsdk")
    _touch(os.path.join(core, "src", "Main.kt"))
    _touch(os.path.join(sdk, "src", "Sdk.kt"))
    board, admin = await _seed_board(
        mem_session, primary_local_path=core, sibling_specs=[("gamexsdk", sdk)]
    )
    sibling = _repo_by_slug(board, "gamexsdk")
    s = _settings(enabled=False, root=str(tmp_path))
    fetch = AsyncMock(return_value=_snapshot(3))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3, patch.object(sonarqube, "fetch_board_metrics", fetch):
            resp = client.post(_sync_url(board, sibling.slug))
    finally:
        _clear()

    assert resp.status_code == 200
    fetch.assert_not_awaited()  # kill switch — no live attempt
    assert resp.json()["quality_gate_status"] is None


async def test_endpoint_sync_unknown_repo_is_404(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(core, "src", "Main.kt"))
    board, admin = await _seed_board(mem_session, primary_local_path=core)
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(admin, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_sync_url(board, "no-such-repo"))
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_sync_non_admin_is_403(
    mem_session: AsyncSession, tmp_path
) -> None:
    core = os.path.join(str(tmp_path), "GameXCore")
    _touch(os.path.join(core, "src", "Main.kt"))
    board, member = await _seed_board(
        mem_session, primary_local_path=core, member_role="developer"
    )
    primary = _repo_by_slug(board, "gamexcore")
    s = _settings(root=str(tmp_path))
    p1, p2, p3 = _patch_all(s)
    client = _make_client(member, mem_session)
    try:
        with p1, p2, p3:
            resp = client.post(_sync_url(board, primary.slug))
    finally:
        _clear()
    assert resp.status_code == 403
