"""API integration tests for PH-150 G1 repository config endpoints.

Endpoints under test:
  PUT    /api/boards/{key}/repository  — upsert (admin only)
  DELETE /api/boards/{key}/repository  — detach (admin only)
  GET    /api/boards/{key}/git/status  — status (any member)
  GET    /api/boards/{key}             — board response includes repository summary

Test cases (mapped to AC):
  TC1  PUT creates new repository → 200 + RepositoryResponse
  TC2  PUT second time with same payload → 200, same id (idempotent upsert)
  TC3  GET /git/status with repo configured → {connected:true, repository:...}
  TC4  GET /git/status without repo → {connected:false, repository:null}
  TC5  DELETE → 204, subsequent GET status → {connected:false}
  TC6  PUT invalid local_path (no /repos/ prefix) → 422
  TC7  PUT provider=github without remote_url → 422
  TC8  PUT local_path traversal /repos/../etc → 422
  TC9  GET /api/boards/{key} includes repository summary when connected
  TC10 GET /api/boards/{key} has repository=null when not connected
  TC11 PUT by non-member → 403
  TC12 DELETE when no repo attached → 404
  TC13 GET /git/status by non-member → 403
  TC14 Member (non-admin) can read status but cannot PUT
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Workflow
from app.db.session import get_db_session
from app.main import app
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# In-memory DB fixtures
# ---------------------------------------------------------------------------


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
    """Seed: board with admin + member actor; one outsider with no membership."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    member = Actor(kind="human", display_name="Member", token_hash="x", is_active=True)
    outsider = Actor(kind="human", display_name="Outsider", token_hash="x", is_active=True)
    mem_session.add_all([admin, member, outsider])
    await mem_session.flush()

    board = Board(
        key="RP",
        name="Repo API Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add_all(
        [
            BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board.id, actor_id=member.id, role="backend_dev"),
        ]
    )
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
        "member": member,
        "outsider": outsider,
        "session": mem_session,
    }


# ---------------------------------------------------------------------------
# Client factory — injects actor + session via DI override
# ---------------------------------------------------------------------------


def make_client(actor: Actor, session: AsyncSession) -> TestClient:
    """Create a TestClient with current_actor and get_db_session overridden."""

    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def clear_overrides() -> None:
    app.dependency_overrides.clear()


VALID_PAYLOAD: dict[str, Any] = {
    "provider": "local",
    "default_branch": "main",
    "local_path": "/repos/test/myrepo",
}


# ---------------------------------------------------------------------------
# TC1: PUT creates repository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_repository_creates(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        resp = client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["provider"] == "local"
        assert data["local_path"] == "/repos/test/myrepo"
        assert data["default_branch"] == "main"
        assert "id" in data
        assert "board_id" in data
        assert "created_at" in data
        assert "updated_at" in data
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC2: PUT is idempotent — same id, field updates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_repository_upsert_idempotent(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        r1 = client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
        assert r1.status_code == 200
        id1 = r1.json()["id"]

        r2 = client.put(
            "/api/boards/RP/repository",
            json={**VALID_PAYLOAD, "default_branch": "develop"},
        )
        assert r2.status_code == 200
        assert r2.json()["id"] == id1  # same row
        assert r2.json()["default_branch"] == "develop"  # updated
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC3: GET /git/status with repo configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_status_connected(seeded: dict[str, Any]) -> None:
    admin_client = make_client(seeded["admin"], seeded["session"])
    try:
        admin_client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
    finally:
        clear_overrides()

    member_client = make_client(seeded["member"], seeded["session"])
    try:
        resp = member_client.get("/api/boards/RP/git/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["repository"] is not None
        assert data["repository"]["local_path"] == "/repos/test/myrepo"
        assert data["last_synced_at"] is None  # not yet synced (G3)
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC4: GET /git/status without repo → connected=false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_status_disconnected_returns_null(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["member"], seeded["session"])
    try:
        resp = client.get("/api/boards/RP/git/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert data["repository"] is None
        assert data["last_synced_at"] is None
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC5: DELETE detaches, subsequent status → disconnected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_repository_returns_204_and_removes_row(seeded: dict[str, Any]) -> None:
    admin_client = make_client(seeded["admin"], seeded["session"])
    try:
        admin_client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
        del_resp = admin_client.delete("/api/boards/RP/repository")
        assert del_resp.status_code == 204
    finally:
        clear_overrides()

    member_client = make_client(seeded["member"], seeded["session"])
    try:
        status_resp = member_client.get("/api/boards/RP/git/status")
        assert status_resp.json()["connected"] is False
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC6: PUT invalid local_path (no /repos/ prefix) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_repository_validation_invalid_path_prefix(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        resp = client.put(
            "/api/boards/RP/repository",
            json={**VALID_PAYLOAD, "local_path": "/etc/passwd"},
        )
        assert resp.status_code == 422
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC7: PUT provider=github without remote_url → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_repository_validation_github_requires_remote_url(
    seeded: dict[str, Any],
) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        resp = client.put(
            "/api/boards/RP/repository",
            json={
                "provider": "github",
                "local_path": "/repos/test/myrepo",
                "remote_url": None,
            },
        )
        assert resp.status_code == 422
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC8: PUT local_path with path traversal → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_repository_validation_path_traversal(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        resp = client.put(
            "/api/boards/RP/repository",
            json={**VALID_PAYLOAD, "local_path": "/repos/../etc/shadow"},
        )
        assert resp.status_code == 422
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC9: GET /api/boards/{key} includes repository summary when connected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_board_includes_repository_summary(seeded: dict[str, Any]) -> None:
    session: AsyncSession = seeded["session"]
    client = make_client(seeded["admin"], session)
    try:
        client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
        # Expire all cached ORM state so the next GET re-fetches from DB,
        # including the newly inserted repository row in selectinload.
        session.expire_all()
        resp = client.get("/api/boards/RP")
        assert resp.status_code == 200
        data = resp.json()
        assert "repository" in data
        assert data["repository"] is not None
        assert data["repository"]["local_path"] == "/repos/test/myrepo"
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC10: GET /api/boards/{key} has repository=null when not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_board_returns_null_when_not_connected(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        resp = client.get("/api/boards/RP")
        assert resp.status_code == 200
        data = resp.json()
        assert "repository" in data
        assert data["repository"] is None
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC11: PUT by non-member → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_repository_non_member_403(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["outsider"], seeded["session"])
    try:
        resp = client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
        assert resp.status_code == 403
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC12: DELETE when no repo attached → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_repository_not_found_404(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["admin"], seeded["session"])
    try:
        resp = client.delete("/api/boards/RP/repository")
        assert resp.status_code == 404
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC13: GET /git/status by non-member → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_status_non_member_403(seeded: dict[str, Any]) -> None:
    client = make_client(seeded["outsider"], seeded["session"])
    try:
        resp = client.get("/api/boards/RP/git/status")
        assert resp.status_code == 403
    finally:
        clear_overrides()


# ---------------------------------------------------------------------------
# TC14: Member (non-admin) can read status but cannot PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_can_read_status_but_not_write(seeded: dict[str, Any]) -> None:
    member_client = make_client(seeded["member"], seeded["session"])
    try:
        get_resp = member_client.get("/api/boards/RP/git/status")
        assert get_resp.status_code == 200

        put_resp = member_client.put("/api/boards/RP/repository", json=VALID_PAYLOAD)
        assert put_resp.status_code == 403
    finally:
        clear_overrides()
