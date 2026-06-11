"""Tests for GET /api/auth/me endpoint (PH-48).

Test cases:
1. Admin token → 200, actor + non-empty memberships
2. PM token → 200, actor + memberships
3. Invalid/missing token → 403 (PermissionDenied, app returns 403 not 401)
4. Non-member actor (zero memberships) → 200, memberships == []
5. Response schema validation (actor fields present)
6. OpenAPI exposes /api/auth/me in routes
"""

from collections.abc import AsyncIterator
from typing import Any

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
    """Seed: one board, admin+pm members, one non-member actor."""
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    mem_session.add(workflow)
    await mem_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    pm = Actor(kind="human", display_name="PM", token_hash="x", is_active=True)
    non_member = Actor(
        kind="agent",
        display_name="NonMember Bot",
        agent_role_hint="backend_dev",
        token_hash="x",
        is_active=True,
    )
    mem_session.add_all([admin, pm, non_member])
    await mem_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="Test board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.flush()

    mem_session.add_all([
        BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"),
        BoardMembership(board_id=board.id, actor_id=pm.id, role="pm"),
    ])
    await mem_session.commit()

    # Reload with eager relations
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
            .where(Actor.id == pm.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    non_member_full = (
        await mem_session.execute(
            select(Actor)
            .where(Actor.id == non_member.id)
            .options(selectinload(Actor.memberships))
        )
    ).scalar_one()

    return {
        "session": mem_session,
        "board": board,
        "admin": admin_full,
        "pm": pm_full,
        "non_member": non_member_full,
    }


# ---------------------------------------------------------------------------
# Helper: build a TestClient with DI overrides for a given actor
# ---------------------------------------------------------------------------


def make_client(actor: Actor, session: AsyncSession) -> TestClient:
    """Return a TestClient that overrides both current_actor and get_db_session."""
    from app.api.deps import current_actor

    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    client = TestClient(app, raise_server_exceptions=True)
    return client


def clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetMeEndpoint:
    def test_admin_token_returns_200_with_actor_and_memberships(
        self, seeded: dict[str, Any]
    ) -> None:
        """AC-1: Admin token → 200, actor + non-empty memberships."""
        admin = seeded["admin"]
        session = seeded["session"]
        client = make_client(admin, session)
        try:
            resp = client.get("/api/auth/me")
        finally:
            clear_overrides()

        assert resp.status_code == 200
        data = resp.json()
        assert data["actor"]["display_name"] == "Admin"
        assert data["actor"]["id"] == str(admin.id)
        assert len(data["memberships"]) == 1
        assert data["memberships"][0]["board_key"] == "PH"
        assert data["memberships"][0]["role"] == "admin"

    def test_pm_token_returns_200_with_memberships(
        self, seeded: dict[str, Any]
    ) -> None:
        """AC-2: PM token → 200, actor + memberships."""
        pm = seeded["pm"]
        session = seeded["session"]
        client = make_client(pm, session)
        try:
            resp = client.get("/api/auth/me")
        finally:
            clear_overrides()

        assert resp.status_code == 200
        data = resp.json()
        assert data["actor"]["display_name"] == "PM"
        assert len(data["memberships"]) == 1
        assert data["memberships"][0]["role"] == "pm"

    def test_invalid_token_returns_403(self) -> None:
        """AC-3: Invalid/missing token → 403 (PermissionDenied with required=valid_bearer_token).

        Note: The AC says 401 but the app's PermissionDenied exception maps to 403.
        This is existing behaviour inherited from all other endpoints; see discovered debt.
        """
        clear_overrides()  # Ensure no overrides so real auth runs
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_missing_token_returns_403(self) -> None:
        """AC-3: Missing token → 403."""
        clear_overrides()
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 403

    def test_non_member_actor_returns_empty_memberships(
        self, seeded: dict[str, Any]
    ) -> None:
        """AC-4: Actor with no board memberships → 200, memberships == []."""
        non_member = seeded["non_member"]
        session = seeded["session"]
        client = make_client(non_member, session)
        try:
            resp = client.get("/api/auth/me")
        finally:
            clear_overrides()

        assert resp.status_code == 200
        data = resp.json()
        assert data["actor"]["display_name"] == "NonMember Bot"
        assert data["memberships"] == []

    def test_response_schema_has_required_actor_fields(
        self, seeded: dict[str, Any]
    ) -> None:
        """AC-5: Response schema has required actor fields (Pydantic validation)."""
        admin = seeded["admin"]
        session = seeded["session"]
        client = make_client(admin, session)
        try:
            resp = client.get("/api/auth/me")
        finally:
            clear_overrides()

        assert resp.status_code == 200
        actor_data = resp.json()["actor"]
        for field in ("id", "kind", "display_name"):
            assert field in actor_data, f"Missing field: {field}"

    def test_openapi_exposes_auth_me_route(self) -> None:
        """AC-5: OpenAPI schema lists /api/auth/me endpoint."""
        clear_overrides()
        client = TestClient(app)
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/auth/me" in paths
