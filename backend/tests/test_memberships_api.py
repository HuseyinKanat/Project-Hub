"""Backend API tests for PH-39 board membership endpoints.

Test cases (AC19 — minimum 6):
  TC1: GET /api/boards/{id}/members → 200, correct member list (list ok)
  TC2: POST /api/boards/{id}/members → 201, new membership returned (add ok)
  TC3: POST duplicate → 409 duplicate_membership (add duplicate)
  TC4: POST unknown role → 422 unknown_role (add unknown role)
  TC5: DELETE last-admin → 409 last_admin (last-admin remove guard)
  TC6: POST by non-admin caller → 403 permission_denied (non-admin rejected)
  TC7: PATCH role → 200, role updated (update role)
  TC8: PATCH last-admin demotion → 409 last_admin (last-admin PATCH guard)
  TC9: DELETE existing membership → 204 (remove ok)
  TC10: GET /api/actors → 200, actors list (actors endpoint)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

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
    """Seed: one board with admin + pm members; one extra actor not yet joined."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    pm_actor = Actor(kind="human", display_name="PM", token_hash="x", is_active=True)
    extra = Actor(
        kind="agent",
        display_name="Extra Bot",
        agent_role_hint="backend_dev",
        token_hash="x",
        is_active=True,
    )
    mem_session.add_all([admin, pm_actor, extra])
    await mem_session.flush()

    board = Board(
        key="TS",
        name="Test Board",
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
            BoardMembership(board_id=board.id, actor_id=pm_actor.id, role="pm"),
        ]
    )
    await mem_session.commit()

    # Reload with eager relations for DI use
    admin_full = (
        await mem_session.execute(
            select(Actor)
            .where(Actor.id == admin.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    pm_full = (
        await mem_session.execute(
            select(Actor)
            .where(Actor.id == pm_actor.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    extra_full = (
        await mem_session.execute(
            select(Actor)
            .where(Actor.id == extra.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()

    return {
        "session": mem_session,
        "board": board,
        "admin": admin_full,
        "pm": pm_full,
        "extra": extra_full,
    }


# ---------------------------------------------------------------------------
# Helper: TestClient with DI overrides
# ---------------------------------------------------------------------------


def make_client(actor: Actor, session: AsyncSession) -> TestClient:
    from app.api.deps import current_actor

    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# TC1: GET members → 200 + list
# ---------------------------------------------------------------------------


class TestListMembers:
    def test_list_ok_returns_200_and_members(self, seeded: dict[str, Any]) -> None:
        """TC1: list members returns 200 and all seeded memberships."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        client = make_client(admin, session)
        try:
            resp = client.get(f"/api/boards/{board.id}/members")
        finally:
            clear_overrides()

        assert resp.status_code == 200
        data = resp.json()
        assert "members" in data
        # admin + pm seeded
        assert len(data["members"]) == 2
        roles = {m["role"] for m in data["members"]}
        assert "admin" in roles
        assert "pm" in roles
        # actor sub-object present
        assert "actor" in data["members"][0]
        assert "display_name" in data["members"][0]["actor"]


# ---------------------------------------------------------------------------
# TC2: POST add member → 201
# ---------------------------------------------------------------------------


class TestAddMember:
    def test_add_ok_returns_201(self, seeded: dict[str, Any]) -> None:
        """TC2: admin can add a new actor to the board → 201."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        extra = seeded["extra"]
        client = make_client(admin, session)
        try:
            resp = client.post(
                f"/api/boards/{board.id}/members",
                json={"actor_id": str(extra.id), "role": "backend_dev"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "backend_dev"
        assert data["actor"]["id"] == str(extra.id)
        assert data["actor"]["display_name"] == "Extra Bot"

    # TC3: duplicate membership → 409
    def test_add_duplicate_returns_409(self, seeded: dict[str, Any]) -> None:
        """TC3: adding an actor already in the board returns 409."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        pm = seeded["pm"]
        client = make_client(admin, session)
        try:
            resp = client.post(
                f"/api/boards/{board.id}/members",
                json={"actor_id": str(pm.id), "role": "pm"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 409
        assert resp.json()["error"] == "duplicate_membership"

    # TC4: unknown role → 422
    def test_add_unknown_role_returns_422(self, seeded: dict[str, Any]) -> None:
        """TC4: adding a member with a role not in board.roles returns 422."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        extra = seeded["extra"]
        client = make_client(admin, session)
        try:
            resp = client.post(
                f"/api/boards/{board.id}/members",
                json={"actor_id": str(extra.id), "role": "nonexistent_role"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 422
        assert resp.json()["error"] == "unknown_role"

    # AC13: nonexistent actor_id → 404
    def test_add_nonexistent_actor_returns_404(self, seeded: dict[str, Any]) -> None:
        """AC13: POST with a bogus actor UUID returns 404 actor not found."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        bogus_id = str(uuid4())
        client = make_client(admin, session)
        try:
            resp = client.post(
                f"/api/boards/{board.id}/members",
                json={"actor_id": bogus_id, "role": "backend_dev"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"
        assert resp.json()["resource"] == "actor"

    # TC6: non-admin caller → 403
    def test_non_admin_caller_returns_403(self, seeded: dict[str, Any]) -> None:
        """TC6: a non-admin member cannot add members (403)."""
        pm_actor = seeded["pm"]
        session = seeded["session"]
        board = seeded["board"]
        extra = seeded["extra"]
        client = make_client(pm_actor, session)
        try:
            resp = client.post(
                f"/api/boards/{board.id}/members",
                json={"actor_id": str(extra.id), "role": "backend_dev"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"


# ---------------------------------------------------------------------------
# TC7: PATCH update role → 200
# ---------------------------------------------------------------------------


class TestUpdateMemberRole:
    def test_update_role_ok_returns_200(self, seeded: dict[str, Any]) -> None:
        """TC7: admin can change a non-admin member's role."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        pm_actor = seeded["pm"]
        client = make_client(admin, session)
        try:
            resp = client.patch(
                f"/api/boards/{board.id}/members/{pm_actor.id}",
                json={"role": "reviewer"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 200
        assert resp.json()["role"] == "reviewer"

    # TC8: demote last admin → 409
    def test_demote_last_admin_returns_409(self, seeded: dict[str, Any]) -> None:
        """TC8: demoting the last admin via PATCH returns 409 last_admin."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        client = make_client(admin, session)
        try:
            resp = client.patch(
                f"/api/boards/{board.id}/members/{admin.id}",
                json={"role": "pm"},
            )
        finally:
            clear_overrides()

        assert resp.status_code == 409
        assert resp.json()["error"] == "last_admin"


# ---------------------------------------------------------------------------
# TC5 + TC9: DELETE remove member
# ---------------------------------------------------------------------------


class TestRemoveMember:
    def test_remove_last_admin_returns_409(self, seeded: dict[str, Any]) -> None:
        """TC5: removing the last admin returns 409 last_admin."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        client = make_client(admin, session)
        try:
            resp = client.delete(f"/api/boards/{board.id}/members/{admin.id}")
        finally:
            clear_overrides()

        assert resp.status_code == 409
        assert resp.json()["error"] == "last_admin"

    def test_remove_non_admin_member_returns_204(self, seeded: dict[str, Any]) -> None:
        """TC9: admin can remove a non-admin member → 204."""
        admin = seeded["admin"]
        session = seeded["session"]
        board = seeded["board"]
        pm_actor = seeded["pm"]
        client = make_client(admin, session)
        try:
            resp = client.delete(f"/api/boards/{board.id}/members/{pm_actor.id}")
        finally:
            clear_overrides()

        assert resp.status_code == 204

    def test_remove_non_admin_caller_returns_403(self, seeded: dict[str, Any]) -> None:
        """TC6 variant: non-admin cannot delete a member."""
        pm_actor = seeded["pm"]
        session = seeded["session"]
        board = seeded["board"]
        client = make_client(pm_actor, session)
        try:
            resp = client.delete(f"/api/boards/{board.id}/members/{pm_actor.id}")
        finally:
            clear_overrides()

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# TC10: GET /api/actors → 200 + actors list
# ---------------------------------------------------------------------------


class TestListActors:
    def test_get_actors_returns_200_and_actor_list(self, seeded: dict[str, Any]) -> None:
        """TC10: any authenticated actor can list all active actors."""
        pm_actor = seeded["pm"]
        session = seeded["session"]
        client = make_client(pm_actor, session)
        try:
            resp = client.get("/api/actors")
        finally:
            clear_overrides()

        assert resp.status_code == 200
        data = resp.json()
        assert "actors" in data
        # We seeded 3 actors (admin, pm, extra) — all active
        assert len(data["actors"]) == 3
        for actor_data in data["actors"]:
            assert "id" in actor_data
            assert "display_name" in actor_data
            assert "kind" in actor_data
