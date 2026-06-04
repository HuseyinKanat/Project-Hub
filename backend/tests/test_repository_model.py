"""ORM smoke tests for the Repository model (PH-150 G1).

Covers:
- Repository creation with all fields
- Default field values (provider='local', default_branch='main')
- unique constraint: board can have at most one repository
- FK cascade: deleting the Board removes the Repository row
- Relationship back-reference: board.repository / repo.board
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, Repository, Workflow
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


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
async def seeded(mem_session: AsyncSession) -> dict[str, object]:
    """Minimal seed: one workflow, one board, one admin actor."""
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
        key="RM",
        name="Repo Model Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    mem_session.add(board)
    await mem_session.commit()
    return {"board": board, "admin": admin, "workflow": workflow}


# ---------------------------------------------------------------------------
# TC1: basic creation with all fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_create_full_fields(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        provider="github",
        remote_url="https://github.com/example/repo.git",
        default_branch="develop",
        local_path="/repos/example/repo",
    )
    mem_session.add(repo)
    await mem_session.commit()

    fetched = (
        await mem_session.execute(select(Repository).where(Repository.board_id == board.id))
    ).scalar_one()
    assert fetched.provider == "github"
    assert fetched.remote_url == "https://github.com/example/repo.git"
    assert fetched.default_branch == "develop"
    assert fetched.local_path == "/repos/example/repo"
    assert fetched.last_synced_sha is None
    assert fetched.last_synced_at is None


# ---------------------------------------------------------------------------
# TC2: default values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_default_values(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/local/myrepo",
    )
    mem_session.add(repo)
    await mem_session.commit()
    await mem_session.refresh(repo)

    # ORM-level defaults (not server_default — those apply on raw INSERT)
    assert repo.provider == "local"
    assert repo.default_branch == "main"


# ---------------------------------------------------------------------------
# TC3: unique constraint — second repo for same board raises IntegrityError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_unique_per_board(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo1 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/local/first",
    )
    repo2 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/local/second",
    )
    mem_session.add(repo1)
    await mem_session.flush()
    mem_session.add(repo2)
    with pytest.raises(IntegrityError):
        await mem_session.flush()
    await mem_session.rollback()


# ---------------------------------------------------------------------------
# TC4: Board.repository relationship back-reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_repository_relationship(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/local/myrepo",
    )
    mem_session.add(repo)
    await mem_session.commit()

    # Reload board with repository relationship eager-loaded.
    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.repository))
        )
    ).scalar_one()
    assert refreshed.repository is not None
    assert refreshed.repository.id == repo.id
    assert refreshed.repository.board is refreshed  # back-reference works


# ---------------------------------------------------------------------------
# TC5: cascade delete — Board delete removes Repository row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_delete_cascades_repository(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        local_path="/repos/local/todelete",
    )
    mem_session.add(repo)
    await mem_session.commit()

    repo_id = repo.id

    await mem_session.delete(board)
    await mem_session.commit()

    orphan = (
        await mem_session.execute(select(Repository).where(Repository.id == repo_id))
    ).scalar_one_or_none()
    assert orphan is None, "Repository row should be deleted by cascade"


# ---------------------------------------------------------------------------
# TC6: board without repository → relationship returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_without_repository_is_none(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.repository))
        )
    ).scalar_one()
    assert refreshed.repository is None
