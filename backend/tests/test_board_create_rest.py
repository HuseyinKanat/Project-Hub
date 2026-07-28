"""PH-331 — POST /api/boards endpoint (admin-gated self-service board creation).

Covers AC2 (201 + BoardResponse), AC3 (default workflow/roles seeded + calling
admin added as a member), AC4 (global-admin gate: admin-of-some-board passes,
non-admin/non-member → 403, no bearer → 403), AC5 (validation 422 + duplicate key
409), AC7 (existing GET /api/boards list still works).

Model mirrors test_board_scope_authz.py: ``admin`` is an admin of board AA;
``alice`` is a non-admin member of AA; ``outsider`` has no membership.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

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


@dataclass
class Scope:
    session: AsyncSession
    admin: Actor  # admin of AA
    alice: Actor  # non-admin member of AA
    outsider: Actor  # no membership


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    async with session_local() as session:
        yield session
    await engine.dispose()


async def _reload_actor(session: AsyncSession, actor: Actor) -> Actor:
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor.id)
            .options(selectinload(Actor.memberships))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest_asyncio.fixture
async def scope(mem_session: AsyncSession) -> Scope:
    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    alice = Actor(kind="agent", display_name="jarwis-backend@alice", token_hash="x", is_active=True)
    outsider = Actor(kind="agent", display_name="jarwis-qa@nobody", token_hash="x", is_active=True)
    mem_session.add_all([admin, alice, outsider])
    await mem_session.flush()

    board_a = Board(
        key="AA", name="Alpha", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    mem_session.add(board_a)
    await mem_session.flush()
    mem_session.add_all(
        [
            BoardMembership(board_id=board_a.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_a.id, actor_id=alice.id, role="backend_dev"),
        ]
    )
    await mem_session.commit()

    return Scope(
        session=mem_session,
        admin=await _reload_actor(mem_session, admin),
        alice=await _reload_actor(mem_session, alice),
        outsider=await _reload_actor(mem_session, outsider),
    )


def _make_client(actor: Actor | None, session: AsyncSession) -> TestClient:
    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db_session] = _fake_session
    if actor is not None:
        async def _fake_current_actor() -> Actor:
            return actor

        app.dependency_overrides[current_actor] = _fake_current_actor
    return TestClient(app, raise_server_exceptions=True)


def _clear() -> None:
    app.dependency_overrides.clear()


class TestCreateBoardRest:
    def test_admin_creates_board_201(self, scope: Scope) -> None:
        """AC2/AC3: admin-of-some-board creates a board → 201 + BoardResponse; the
        board is persisted, seeded with the default workflow/roles, and the calling
        admin is added as an admin member."""
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.post(
                "/api/boards",
                json={"key": "TDI", "name": "Test Driven", "description": "d"},
            )
        finally:
            _clear()
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["key"] == "TDI"
        assert body["name"] == "Test Driven"
        assert body["roles"]  # DEFAULT_WEB_ROLES present
        assert body["workflow"]["is_default"] is True

    @pytest.mark.asyncio
    async def test_admin_create_persists_and_seeds_membership(self, scope: Scope) -> None:
        """AC3: after the REST create, the board row + the calling admin's membership
        exist in the DB."""
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.post("/api/boards", json={"key": "TDI", "name": "T"})
        finally:
            _clear()
        assert resp.status_code == 201

        board = (
            await scope.session.execute(select(Board).where(Board.key == "TDI"))
        ).scalar_one()
        membership = (
            await scope.session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id,
                    BoardMembership.actor_id == scope.admin.id,
                    BoardMembership.role == "admin",
                )
            )
        ).scalar_one_or_none()
        assert membership is not None

    def test_non_admin_member_403(self, scope: Scope) -> None:
        """AC4: a non-admin member (backend_dev, no * / board.create) → 403, no board."""
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.post("/api/boards", json={"key": "NOPE", "name": "x"})
        finally:
            _clear()
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_non_member_403(self, scope: Scope) -> None:
        """AC4: an actor with zero memberships → 403 (holds no cap anywhere)."""
        client = _make_client(scope.outsider, scope.session)
        try:
            resp = client.post("/api/boards", json={"key": "NOPE", "name": "x"})
        finally:
            _clear()
        assert resp.status_code == 403

    def test_no_bearer_403(self, scope: Scope) -> None:
        """AC4: missing bearer → 403 via current_actor BEFORE the handler runs."""
        client = _make_client(None, scope.session)  # current_actor NOT overridden
        try:
            resp = client.post("/api/boards", json={"key": "NOPE", "name": "x"})
        finally:
            _clear()
        assert resp.status_code == 403

    def test_duplicate_key_409(self, scope: Scope) -> None:
        """AC5: creating a board whose key already exists → 409 Conflict."""
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.post("/api/boards", json={"key": "AA", "name": "dup"})
        finally:
            _clear()
        assert resp.status_code == 409
        assert resp.json()["error"] == "conflict"

    def test_missing_key_422(self, scope: Scope) -> None:
        """AC5: a missing required field (key) → 422 (Pydantic)."""
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.post("/api/boards", json={"name": "no key"})
        finally:
            _clear()
        assert resp.status_code == 422

    def test_missing_name_422(self, scope: Scope) -> None:
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.post("/api/boards", json={"key": "ZZ"})
        finally:
            _clear()
        assert resp.status_code == 422

    def test_key_too_long_422(self, scope: Scope) -> None:
        """AC5: key beyond 5 chars → 422 (Board.key is String(5))."""
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.post("/api/boards", json={"key": "TOOLONG", "name": "x"})
        finally:
            _clear()
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_list_still_works_after_create(self, scope: Scope) -> None:
        """AC7: the existing GET /api/boards (membership-scoped list) is unaffected, and
        the creator sees the new board (its admin-membership puts it in scope).

        The admin is RELOADED before the GET so ``actor.memberships`` reflects the new
        TDI membership — mirroring prod, where ``current_actor`` re-materialises the
        actor with memberships eager-loaded on every request (the test override would
        otherwise return the stale pre-create instance)."""
        client = _make_client(scope.admin, scope.session)
        try:
            create = client.post("/api/boards", json={"key": "TDI", "name": "T"})
        finally:
            _clear()
        assert create.status_code == 201

        fresh_admin = await _reload_actor(scope.session, scope.admin)
        client = _make_client(fresh_admin, scope.session)
        try:
            resp = client.get("/api/boards")
        finally:
            _clear()
        assert resp.status_code == 200
        keys = {b["key"] for b in resp.json()["boards"]}
        assert "AA" in keys and "TDI" in keys  # admin is a member of both
