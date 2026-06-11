"""Tests for PH-155 G6 — live refresh endpoint + RefreshRegistry + git_poll_cron.

Test coverage:
  TC1  POST /git/refresh — valid secret, repo configured → 202 {status:"queued"}
  TC2  POST /git/refresh — debounce window → 202 {status:"coalesced"}
  TC3  POST /git/refresh — refresh_secret not set on board → 403
  TC4  POST /git/refresh — wrong token → 401
  TC5  POST /git/refresh — git_refresh_enabled=False → 503-ish (disabled status)
  TC6  POST /git/refresh — no repo configured → 409
  TC7  POST /git/refresh — no token header, secret configured → 401
  TC8  RefreshRegistry — get_lock returns same lock per repo_id
  TC9  RefreshRegistry — should_coalesce False before dispatch
  TC10 RefreshRegistry — should_coalesce True after mark_dispatched within window
  TC11 RefreshRegistry — should_coalesce False after debounce window expires
  TC12 RefreshRegistry — acquire_sync_lock releases on exception
  TC13 RefreshRegistry — two concurrent gather tasks cannot hold lock simultaneously
  TC14 git_poll_cron — poller skips repo when lock held (simulates in-flight refresh)
  TC15 git_poll_cron — mask_webhook_secret also masks refresh_secret
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Repository, Workflow
from app.db.session import get_db_session
from app.git.refresh import RefreshRegistry
from app.main import app
from app.services.boards import mask_webhook_secret
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# In-memory DB + seed fixtures
# ---------------------------------------------------------------------------

SECRET = "super-secret-token-123"
VALID_HEADERS = {"X-Git-Refresh-Token": SECRET}


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(mem_session: AsyncSession) -> dict[str, Any]:
    """Board with admin + member; board.roles includes refresh_secret."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    roles_with_secret: dict[str, Any] = dict(DEFAULT_WEB_ROLES)
    roles_with_secret["refresh_secret"] = SECRET

    board = Board(
        key="RF",
        name="Refresh Test Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=roles_with_secret,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))
    await mem_session.flush()

    # Attach a repository row.
    repo = Repository(
        slug="rf",
        name="rf",
        is_primary=True,
        board_id=board.id,
        provider="local",
        local_path="/repos/test/rf",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.commit()

    refreshed_board = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.repositories))
        )
    ).scalar_one()

    return {
        "board": refreshed_board,
        "admin": admin,
        "repo": repo,
        "session": mem_session,
    }


@pytest_asyncio.fixture
async def seeded_no_secret(mem_session: AsyncSession) -> dict[str, Any]:
    """Board with NO refresh_secret in roles."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    board = Board(
        key="NS",
        name="No Secret Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))

    repo = Repository(
        slug="ns",
        name="ns",
        is_primary=True,
        board_id=board.id,
        provider="local",
        local_path="/repos/test/ns",
        default_branch="main",
    )
    mem_session.add(repo)
    await mem_session.commit()

    return {"board": board, "admin": admin, "session": mem_session}


@pytest_asyncio.fixture
async def seeded_no_repo(mem_session: AsyncSession) -> dict[str, Any]:
    """Board with refresh_secret but NO repository configured."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    mem_session.add(admin)
    await mem_session.flush()

    roles: dict[str, Any] = dict(DEFAULT_WEB_ROLES)
    roles["refresh_secret"] = SECRET

    board = Board(
        key="NR",
        name="No Repo Board",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=roles,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))
    await mem_session.commit()

    return {"board": board, "admin": admin, "session": mem_session}


# ---------------------------------------------------------------------------
# Client factory — bypasses current_actor DI
# ---------------------------------------------------------------------------


def make_client(actor: Actor, session: AsyncSession):  # type: ignore[return]
    from fastapi.testclient import TestClient

    async def _fake_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TC1 — valid secret + repo → 202 queued
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_valid_secret_queued(seeded: dict[str, Any]) -> None:
    """Valid secret dispatches background sync → 202 {status:'queued'}."""
    # Reset registry debounce state for this repo so we don't coalesce.
    seeded["repo"]
    fresh_registry = RefreshRegistry()

    with (
        patch("app.api.repositories.registry", fresh_registry),
        patch("app.api.repositories.run_in_background") as mock_bg,
    ):
        client = make_client(seeded["admin"], seeded["session"])
        try:
            resp = client.post("/api/boards/RF/git/refresh", headers=VALID_HEADERS)
            assert resp.status_code == 202, resp.text
            data = resp.json()
            assert data["ok"] is True
            assert data["status"] == "queued"
            mock_bg.assert_called_once()
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# TC2 — rapid second call → coalesced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_debounce_coalesces(seeded: dict[str, Any]) -> None:
    """Second POST within debounce window → status:'coalesced', sync NOT called twice."""
    seeded_repo: Repository = seeded["repo"]
    fresh_registry = RefreshRegistry()
    # Pre-mark so the first call is coalesced too (simplest way to test window).
    fresh_registry.mark_dispatched(seeded_repo.id)

    with (
        patch("app.api.repositories.registry", fresh_registry),
        patch("app.api.repositories.run_in_background") as mock_bg,
        patch(
            "app.api.repositories.get_settings",
            return_value=MagicMock(
                git_refresh_enabled=True,
                git_refresh_debounce_seconds=60.0,  # very wide window
                git_diff_max_bytes=1_048_576,
            ),
        ),
    ):
        client = make_client(seeded["admin"], seeded["session"])
        try:
            resp = client.post("/api/boards/RF/git/refresh", headers=VALID_HEADERS)
            assert resp.status_code == 202, resp.text
            data = resp.json()
            assert data["ok"] is True
            assert data["status"] == "coalesced"
            # run_in_background must NOT be called when coalescing.
            mock_bg.assert_not_called()
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# TC3 — refresh_secret not set → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_no_secret_403(seeded_no_secret: dict[str, Any]) -> None:
    """Board without refresh_secret → 403 (fails closed)."""
    client = make_client(seeded_no_secret["admin"], seeded_no_secret["session"])
    try:
        resp = client.post(
            "/api/boards/NS/git/refresh",
            headers={"X-Git-Refresh-Token": "whatever"},
        )
        assert resp.status_code == 403, resp.text
        data = resp.json()
        assert data["error"] == "refresh_disabled"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC4 — wrong token → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_wrong_token_401(seeded: dict[str, Any]) -> None:
    """Wrong token when secret IS set → 401."""
    fresh_registry = RefreshRegistry()
    with patch("app.api.repositories.registry", fresh_registry):
        client = make_client(seeded["admin"], seeded["session"])
        try:
            resp = client.post(
                "/api/boards/RF/git/refresh",
                headers={"X-Git-Refresh-Token": "wrong-token"},
            )
            assert resp.status_code == 401, resp.text
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# TC5 — git_refresh_enabled=False → disabled status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_disabled_globally(seeded: dict[str, Any]) -> None:
    """git_refresh_enabled=False → 503 with {ok:False, status:'disabled'} body.

    G6 AC (PH-165): the master kill switch returns HTTP 503 Service Unavailable
    so it is distinguishable from the 202 success path, while the body still
    carries the disabled intent for clients.
    """
    with patch(
        "app.api.repositories.get_settings",
        return_value=MagicMock(
            git_refresh_enabled=False,
            git_refresh_debounce_seconds=2.0,
            git_diff_max_bytes=1_048_576,
        ),
    ):
        client = make_client(seeded["admin"], seeded["session"])
        try:
            resp = client.post("/api/boards/RF/git/refresh", headers=VALID_HEADERS)
            assert resp.status_code == 503, resp.text
            data = resp.json()
            assert data["ok"] is False
            assert data["status"] == "disabled"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# TC6 — no repo configured → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_no_repo_409(seeded_no_repo: dict[str, Any]) -> None:
    """Board has refresh_secret but no Repository → 409 repo_not_configured."""
    fresh_registry = RefreshRegistry()
    with patch("app.api.repositories.registry", fresh_registry):
        client = make_client(seeded_no_repo["admin"], seeded_no_repo["session"])
        try:
            resp = client.post("/api/boards/NR/git/refresh", headers=VALID_HEADERS)
            assert resp.status_code == 409, resp.text
            data = resp.json()
            assert data["error"] == "repo_not_configured"
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# TC7 — no token header → 401 (treated as mismatch against non-empty secret)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_no_header_401(seeded: dict[str, Any]) -> None:
    """No X-Git-Refresh-Token header when secret IS set → 401."""
    fresh_registry = RefreshRegistry()
    with patch("app.api.repositories.registry", fresh_registry):
        client = make_client(seeded["admin"], seeded["session"])
        try:
            # No header supplied; fastapi sets it to None → compare_digest("", secret) → False.
            resp = client.post("/api/boards/RF/git/refresh")
            assert resp.status_code == 401, resp.text
        finally:
            clear_overrides()


# ---------------------------------------------------------------------------
# TC8 — RefreshRegistry: same lock object per repo_id
# ---------------------------------------------------------------------------


def test_registry_same_lock_per_repo() -> None:
    """get_lock returns the same asyncio.Lock for the same repo_id."""
    reg = RefreshRegistry()
    rid = uuid.uuid4()
    lock1 = reg.get_lock(rid)
    lock2 = reg.get_lock(rid)
    assert lock1 is lock2


# ---------------------------------------------------------------------------
# TC9 — should_coalesce False before any dispatch
# ---------------------------------------------------------------------------


def test_registry_should_coalesce_false_before_dispatch() -> None:
    """should_coalesce returns False when no dispatch has been recorded."""
    reg = RefreshRegistry()
    rid = uuid.uuid4()
    assert reg.should_coalesce(rid, debounce_seconds=2.0) is False


# ---------------------------------------------------------------------------
# TC10 — should_coalesce True immediately after mark_dispatched
# ---------------------------------------------------------------------------


def test_registry_should_coalesce_true_after_dispatch() -> None:
    """should_coalesce returns True immediately after mark_dispatched."""
    reg = RefreshRegistry()
    rid = uuid.uuid4()
    reg.mark_dispatched(rid)
    assert reg.should_coalesce(rid, debounce_seconds=5.0) is True


# ---------------------------------------------------------------------------
# TC11 — should_coalesce False after debounce window expires (mock monotonic)
# ---------------------------------------------------------------------------


def test_registry_should_coalesce_false_after_window() -> None:
    """should_coalesce returns False once debounce window has elapsed."""
    from time import monotonic


    reg = RefreshRegistry()
    rid = uuid.uuid4()
    reg.mark_dispatched(rid)

    # Simulate time passing beyond the debounce window.
    past = monotonic() - 10.0  # 10 seconds ago
    reg._last_request_monotonic[rid] = past

    assert reg.should_coalesce(rid, debounce_seconds=2.0) is False


# ---------------------------------------------------------------------------
# TC12 — acquire_sync_lock releases on exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_lock_releases_on_exception() -> None:
    """Lock must be released even when the body raises."""
    reg = RefreshRegistry()
    rid = uuid.uuid4()
    lock = reg.get_lock(rid)

    assert not lock.locked()
    try:
        async with reg.acquire_sync_lock(rid):
            assert lock.locked()
            raise ValueError("boom")
    except ValueError:
        pass
    assert not lock.locked()


# ---------------------------------------------------------------------------
# TC13 — concurrent gather: only one task holds the lock at a time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_lock_exclusivity_concurrent() -> None:
    """Two concurrent tasks cannot hold the same repo lock simultaneously."""
    reg = RefreshRegistry()
    rid = uuid.uuid4()

    concurrent_count = 0
    max_concurrent = 0

    async def worker() -> None:
        nonlocal concurrent_count, max_concurrent
        async with reg.acquire_sync_lock(rid):
            concurrent_count += 1
            if concurrent_count > max_concurrent:
                max_concurrent = concurrent_count
            await asyncio.sleep(0.01)  # yield
            concurrent_count -= 1

    await asyncio.gather(worker(), worker(), worker())
    assert max_concurrent == 1, f"Expected max 1 concurrent, got {max_concurrent}"


# ---------------------------------------------------------------------------
# TC14 — poller skips repo when lock is held
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poller_skips_locked_repo() -> None:
    """git_poll_cron skips a repo whose lock is already held (refresh in flight)."""
    from app.git.refresh import _scan_and_sync_due_repos

    rid = uuid.uuid4()
    fresh_registry = RefreshRegistry()
    lock = fresh_registry.get_lock(rid)

    # Mock DB scan to return a fake repo.
    fake_repo = MagicMock()
    fake_repo.id = rid
    fake_repo.board_id = uuid.uuid4()
    fake_repo.last_synced_at = None  # due for poll

    sync_call_count = 0

    async def fake_scan() -> None:
        # Actually call the inner logic using patched registry + session.
        nonlocal sync_call_count
        # simulate: lock is held
        async with lock:
            # check: a concurrent _scan_and_sync_due_repos should skip
            pass

    # Hold the lock while scan runs.
    held = asyncio.create_task(_hold_lock(lock))
    await asyncio.sleep(0)  # let task acquire

    with (
        patch("app.git.refresh.registry", fresh_registry),
        patch(
            "app.git.refresh.SessionLocal",
            return_value=_fake_session_ctx([fake_repo], rid),
        ),
        patch("app.git.refresh.sync_repo") as mock_sync,
    ):
        await _scan_and_sync_due_repos()
        # Sync should NOT have been called because the lock was held.
        assert not mock_sync.called, "sync_repo must not be called when lock is held"

    held.cancel()
    try:
        await held
    except asyncio.CancelledError:
        pass


async def _hold_lock(lock: asyncio.Lock) -> None:
    """Helper: acquire lock and hold it until cancelled."""
    async with lock:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass


class _fake_session_ctx:
    """Minimal async context manager that pretends to be SessionLocal()."""

    def __init__(self, repos: list[Any], board_id_for_get: uuid.UUID) -> None:
        self._repos = repos
        self._board_id = board_id_for_get

    async def __aenter__(self) -> _fake_session_ctx:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def execute(self, _stmt: Any) -> Any:
        result = MagicMock()
        result.scalars.return_value.all.return_value = self._repos
        return result

    async def get(self, model: Any, pk: Any) -> Any:
        # Return a fake board.
        fake_board = MagicMock()
        fake_board.id = pk
        fake_board.key = "TEST"
        return fake_board


# ---------------------------------------------------------------------------
# TC15 — mask_webhook_secret also masks refresh_secret
# ---------------------------------------------------------------------------


def test_mask_webhook_secret_also_masks_refresh_secret() -> None:
    """mask_webhook_secret must mask refresh_secret alongside webhook_secret."""
    roles: dict[str, Any] = {
        "webhook_secret": "abc123",
        "refresh_secret": "xyz789",
        "admin": ["board.admin"],
    }
    masked = mask_webhook_secret(roles)
    assert masked["webhook_secret"] == "*****"
    assert masked["refresh_secret"] == "*****"
    # Other keys untouched.
    assert masked["admin"] == ["board.admin"]
    # Original dict not mutated.
    assert roles["webhook_secret"] == "abc123"
    assert roles["refresh_secret"] == "xyz789"


# ---------------------------------------------------------------------------
# PH-205 — git_poll_cron re-raises CancelledError (graceful cancellation, S7497)
# ---------------------------------------------------------------------------


async def test_git_poll_cron_reraises_cancellederror() -> None:
    """git_poll_cron must re-raise CancelledError so the task ends *cancelled*.

    Before PH-205 the loop swallowed the cancel with `break`, finishing the
    task normally — the framework could not tell the task acknowledged the
    cancel. After the fix the CancelledError propagates: task.cancelled() True.
    """
    from app.git import refresh as refresh_mod

    started = asyncio.Event()

    async def _block_forever() -> None:
        started.set()
        await asyncio.sleep(3600)

    with (
        patch.object(refresh_mod, "_scan_and_sync_due_repos", _block_forever),
        patch.object(
            refresh_mod, "get_settings", return_value=MagicMock(git_poll_interval_seconds=0)
        ),
    ):
        task = asyncio.create_task(refresh_mod.git_poll_cron())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # The cancel was propagated, not swallowed → graceful shutdown is correct.
    assert task.cancelled()
