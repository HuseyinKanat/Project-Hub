"""PH-239 — tests for the SonarQube auto-scan job lifecycle (the host-watcher seam).

All network-free + scanner-free (the AC's "verifiable WITHOUT a live SonarQube"
contract): the only SonarQube touchpoint, ``poll_board`` on complete-success, is
MOCKED so no live server is required.

Three layers (mirroring test_sonarqube_scan.py):

  Service — ``request_board_scan`` now PERSISTS a ``SonarScanJob(queued)`` (idempotent
  per board); ``list_pending_scans`` / ``claim_scan_job`` / ``complete_scan_job`` drive
  the lifecycle. The double-run guard (claim a non-queued job → ScanJobConflict) and the
  immediate-ingest-on-success (poll_board called) / no-ingest-on-failure are asserted
  with a mocked poll_board.

  Endpoint — a FastAPI ``TestClient`` (DI overrides): GET /api/scans/pending returns the
  queue (and ``[]`` when idle), POST .../claim flips queued→running (409 on re-claim),
  POST .../complete flips running→done|failed. Secret-free invariant checked.

  Honest-status — unsupported/unconfigured/disabled boards enqueue NO job (PH-236/235
  contract preserved).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, SonarScanJob, Workflow
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
    key: str = "KIM",
    project_key: str | None = "kims",
    repos_path: str | None = "/Users/huseyinkanat/Documents/kims",
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
                selectinload(Board.sonarqube_metrics),
            )
        )
    ).scalar_one()
    refreshed_actor = (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    return refreshed_board, refreshed_actor


def _settings(
    *,
    enabled: bool = True,
    dotnet_enabled: bool = False,
    host_home: str = "/Users/huseyinkanat",
    repos_root: str = "/Users/huseyinkanat",
) -> MagicMock:
    settings = MagicMock()
    settings.sonarqube_enabled = enabled
    # PH-257 — explicit bool; a bare MagicMock attribute is truthy and would silently flip
    # a csharp board from needs_dotnet_setup to queued.
    settings.sonar_dotnet_enabled = dotnet_enabled
    settings.sonarqube_url = "http://sonarqube:9000"
    settings.sonarqube_scan_url = "http://localhost:9000"
    settings.sonarqube_token = _SECRET
    settings.sonarqube_project_key_map = ""
    settings.host_home = host_home
    settings.repos_root = repos_root
    return settings


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("x = 1\n")


def _patchers(settings: MagicMock):
    return (
        patch.object(sonarqube, "get_settings", return_value=settings),
        patch.object(repo_paths, "get_settings", return_value=settings),
    )


# ===========================================================================
# request_board_scan persistence — a queued scannable board persists a job row
# ===========================================================================


async def test_request_scan_persists_queued_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: POST scan on a scannable board PERSISTS SonarScanJob(state=queued)."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, actor = await _seed_board(
        mem_session, repos_path=str(tmp_path), project_key="kims"
    )
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2 = _patchers(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(
            mem_session, board, requested_by=actor.id
        )
    assert result.scan_status == "queued"
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].state == "queued"
    assert jobs[0].project_key == "kims"
    assert jobs[0].requested_by == actor.id
    # The queued message points at the watcher, not a manual hand-run.
    assert "watcher" in result.message.lower()
    assert _SECRET not in result.message


async def test_request_scan_idempotent_reuses_queued_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """Re-clicking Scan now while one job is queued REUSES it (no duplicate run)."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, actor = await _seed_board(mem_session, repos_path=str(tmp_path))
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2 = _patchers(s)
    with p1, p2:
        await sonarqube.request_board_scan(mem_session, board, requested_by=actor.id)
        await sonarqube.request_board_scan(mem_session, board, requested_by=actor.id)
    jobs = (
        await mem_session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalars().all()
    assert len(jobs) == 1  # second click did NOT stack a second queued job


async def test_request_scan_csharp_needs_dotnet_setup_persists_no_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    """PH-236/PH-257 contract: a csharp (Unity) board with SONAR_DOTNET_ENABLED off →
    needs_dotnet_setup and enqueues NO job (honest skip)."""
    root = str(tmp_path)
    _touch(os.path.join(root, "Assets", "Scripts", "Blade.cs"))
    os.makedirs(os.path.join(root, "ProjectSettings"), exist_ok=True)
    board, _ = await _seed_board(
        mem_session, key="FN", repos_path=root, project_key="fruit-ninja2"
    )
    s = _settings(dotnet_enabled=False, host_home=root, repos_root=root)
    p1, p2 = _patchers(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "needs_dotnet_setup"
    jobs = (
        await mem_session.execute(select(SonarScanJob))
    ).scalars().all()
    assert jobs == []


async def test_request_scan_unconfigured_persists_no_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    board, _ = await _seed_board(
        mem_session, project_key=None, repos_path=str(tmp_path)
    )
    with patch.object(sonarqube, "get_settings", return_value=_settings()):
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "unconfigured"
    assert (await mem_session.execute(select(SonarScanJob))).scalars().all() == []


async def test_request_scan_disabled_persists_no_job(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    s = _settings(enabled=False, host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2 = _patchers(s)
    with p1, p2:
        result = await sonarqube.request_board_scan(mem_session, board)
    assert result.scan_status == "disabled"
    assert (await mem_session.execute(select(SonarScanJob))).scalars().all() == []


# ===========================================================================
# list_pending_scans / claim_scan_job / complete_scan_job — service lifecycle
# ===========================================================================


async def _enqueue(session: AsyncSession, board: Board, tmp_path) -> SonarScanJob:
    s = _settings(host_home=str(tmp_path), repos_root=str(tmp_path))
    p1, p2 = _patchers(s)
    with p1, p2:
        await sonarqube.request_board_scan(session, board)
    return (
        await session.execute(
            select(SonarScanJob).where(SonarScanJob.board_id == board.id)
        )
    ).scalar_one()


async def test_list_pending_empty_when_idle(mem_session: AsyncSession) -> None:
    assert await sonarqube.list_pending_scans(mem_session) == []


async def test_list_pending_returns_queued(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    pending = await sonarqube.list_pending_scans(mem_session)
    assert len(pending) == 1
    assert pending[0].job_id == job.id
    assert pending[0].board_key == "KIM"
    assert pending[0].project_key == "kims"


async def test_claim_flips_queued_to_running(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    claimed = await sonarqube.claim_scan_job(mem_session, job.id)
    assert claimed.state == "running"
    assert claimed.started_at is not None
    # Now not pending anymore.
    assert await sonarqube.list_pending_scans(mem_session) == []


async def test_double_claim_is_conflict(
    mem_session: AsyncSession, tmp_path
) -> None:
    """R2: a second claim on the same job is rejected (double-run guard)."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    await sonarqube.claim_scan_job(mem_session, job.id)
    with pytest.raises(sonarqube.ScanJobConflict):
        await sonarqube.claim_scan_job(mem_session, job.id)


async def test_claim_unknown_job_not_found(mem_session: AsyncSession) -> None:
    with pytest.raises(sonarqube.ScanJobNotFound):
        await sonarqube.claim_scan_job(mem_session, uuid4())


async def test_complete_success_triggers_ingest(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: complete{success:true} → done + poll_board ingest (mocked) fires."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    await sonarqube.claim_scan_job(mem_session, job.id)

    mock_poll = AsyncMock(return_value=True)
    with patch.object(sonarqube, "poll_board", mock_poll):
        done = await sonarqube.complete_scan_job(
            mem_session, job.id, success=True
        )
    assert done.state == "done"
    assert done.finished_at is not None
    assert mock_poll.await_count == 1
    # The poll_board was called with the owning board.
    assert mock_poll.await_args.args[1].id == board.id
    assert done.detail == "ingested"


async def test_complete_failure_no_ingest(
    mem_session: AsyncSession, tmp_path
) -> None:
    """AC: complete{success:false} → failed, detail persisted, NO ingest."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    await sonarqube.claim_scan_job(mem_session, job.id)

    mock_poll = AsyncMock(return_value=True)
    with patch.object(sonarqube, "poll_board", mock_poll):
        failed = await sonarqube.complete_scan_job(
            mem_session, job.id, success=False, detail="scanner RC=2"
        )
    assert failed.state == "failed"
    assert failed.detail == "scanner RC=2"
    assert mock_poll.await_count == 0  # NO ingest on failure


async def test_complete_success_ingest_pending_detail(
    mem_session: AsyncSession, tmp_path
) -> None:
    """R3: if the immediate poll returns None (indexing race) → done, cron backstop note."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    await sonarqube.claim_scan_job(mem_session, job.id)

    mock_poll = AsyncMock(return_value=False)  # indexing not ready yet
    with patch.object(sonarqube, "poll_board", mock_poll):
        done = await sonarqube.complete_scan_job(mem_session, job.id, success=True)
    assert done.state == "done"
    assert "cron" in (done.detail or "").lower()


async def test_complete_non_running_is_conflict(
    mem_session: AsyncSession, tmp_path
) -> None:
    """Completing a still-queued (never-claimed) job → conflict."""
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, _ = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    with pytest.raises(sonarqube.ScanJobConflict):
        await sonarqube.complete_scan_job(mem_session, job.id, success=True)


# ===========================================================================
# Endpoint layer — /api/scans/* via TestClient
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


async def test_endpoint_pending_empty(mem_session: AsyncSession) -> None:
    _, actor = await _seed_board(mem_session)
    client = _make_client(actor, mem_session)
    try:
        resp = client.get("/api/scans/pending")
    finally:
        _clear()
    assert resp.status_code == 200
    assert resp.json() == []


async def test_endpoint_pending_lists_queued(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, actor = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    client = _make_client(actor, mem_session)
    try:
        resp = client.get("/api/scans/pending")
    finally:
        _clear()
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["job_id"] == str(job.id)
    assert data[0]["board_key"] == "KIM"
    assert data[0]["project_key"] == "kims"
    assert _SECRET not in resp.text


async def test_endpoint_claim_then_complete_flow(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, actor = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    client = _make_client(actor, mem_session)
    mock_poll = AsyncMock(return_value=True)
    try:
        with patch.object(sonarqube, "poll_board", mock_poll):
            claim = client.post(f"/api/scans/{job.id}/claim")
            assert claim.status_code == 200
            assert claim.json()["state"] == "running"
            # Re-claim → 409.
            assert client.post(f"/api/scans/{job.id}/claim").status_code == 409
            complete = client.post(
                f"/api/scans/{job.id}/complete", json={"success": True}
            )
            assert complete.status_code == 200
            assert complete.json()["state"] == "done"
    finally:
        _clear()
    assert mock_poll.await_count == 1


async def test_endpoint_complete_failure_status(
    mem_session: AsyncSession, tmp_path
) -> None:
    _touch(os.path.join(str(tmp_path), "app", "main.py"))
    board, actor = await _seed_board(mem_session, repos_path=str(tmp_path))
    job = await _enqueue(mem_session, board, tmp_path)
    client = _make_client(actor, mem_session)
    mock_poll = AsyncMock(return_value=True)
    try:
        with patch.object(sonarqube, "poll_board", mock_poll):
            client.post(f"/api/scans/{job.id}/claim")
            resp = client.post(
                f"/api/scans/{job.id}/complete",
                json={"success": False, "detail": "RC=2"},
            )
    finally:
        _clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "failed"
    assert body["detail"] == "RC=2"
    assert mock_poll.await_count == 0


async def test_endpoint_claim_unknown_job_404(mem_session: AsyncSession) -> None:
    _, actor = await _seed_board(mem_session)
    client = _make_client(actor, mem_session)
    try:
        resp = client.post(f"/api/scans/{uuid4()}/claim")
    finally:
        _clear()
    assert resp.status_code == 404


async def test_endpoint_claim_malformed_id_404(mem_session: AsyncSession) -> None:
    _, actor = await _seed_board(mem_session)
    client = _make_client(actor, mem_session)
    try:
        resp = client.post("/api/scans/not-a-uuid/claim")
    finally:
        _clear()
    assert resp.status_code == 404
