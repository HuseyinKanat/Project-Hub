"""Test fixtures: in-memory sqlite session + seeded board/actor data."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Workflow
from app.main import app
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


@dataclass
class Seed:
    workflow: Workflow
    board: Board
    admin: Actor
    pm: Actor
    backend: Actor


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> Seed:
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    db_session.add(workflow)
    await db_session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    pm = Actor(kind="human", display_name="PM", token_hash="x", is_active=True)
    backend = Actor(
        kind="agent",
        display_name="Backend Bot",
        agent_id="claude-backend-1",
        agent_role_hint="backend",
        token_hash="x",
        is_active=True,
    )
    db_session.add_all([admin, pm, backend])
    await db_session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="Test board",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    db_session.add(board)
    await db_session.flush()

    db_session.add_all(
        [
            BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board.id, actor_id=pm.id, role="pm"),
            BoardMembership(board_id=board.id, actor_id=backend.id, role="backend_dev"),
        ]
    )
    await db_session.commit()

    # Reload with relationships eagerly populated.
    refreshed_board = (
        await db_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.workflow), selectinload(Board.memberships))
        )
    ).scalar_one()
    refreshed_admin = (
        await db_session.execute(
            select(Actor).where(Actor.id == admin.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    refreshed_pm = (
        await db_session.execute(
            select(Actor).where(Actor.id == pm.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()
    refreshed_backend = (
        await db_session.execute(
            select(Actor).where(Actor.id == backend.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()

    return Seed(
        workflow=workflow,
        board=refreshed_board,
        admin=refreshed_admin,
        pm=refreshed_pm,
        backend=refreshed_backend,
    )


@pytest.fixture
def test_client() -> TestClient:
    """FastAPI TestClient for endpoint tests."""
    return TestClient(app)
