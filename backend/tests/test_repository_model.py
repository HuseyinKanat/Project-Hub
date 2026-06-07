"""ORM smoke tests for the Repository model (PH-150 G1; PH-221 multi-repo).

Covers:
- Repository creation with all fields (now incl. slug / name / is_primary)
- Default field values (provider='local', default_branch='main', is_primary=False)
- PH-221: slug is unique per board (uq_repository_board_slug); two repos with
  different slugs coexist on one board (1:N)
- FK cascade: deleting the Board removes ALL its Repository rows
- Relationship back-reference: board.repositories / repo.board
- PH-221: Board.primary_repository property returns the is_primary repo
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
        slug="repo",
        name="repo",
        is_primary=True,
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
    assert fetched.slug == "repo"
    assert fetched.name == "repo"
    assert fetched.is_primary is True
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
        slug="myrepo",
        name="myrepo",
        local_path="/repos/local/myrepo",
    )
    mem_session.add(repo)
    await mem_session.commit()
    await mem_session.refresh(repo)

    # ORM-level defaults (not server_default — those apply on raw INSERT)
    assert repo.provider == "local"
    assert repo.default_branch == "main"
    assert repo.is_primary is False


# ---------------------------------------------------------------------------
# TC3 (PH-221): slug is unique per board — duplicate slug raises IntegrityError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_slug_unique_per_board(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo1 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="dup",
        name="first",
        is_primary=True,
        local_path="/repos/local/first",
    )
    repo2 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="dup",  # same slug on the same board → violates uq_repository_board_slug
        name="second",
        local_path="/repos/local/second",
    )
    mem_session.add(repo1)
    await mem_session.flush()
    mem_session.add(repo2)
    with pytest.raises(IntegrityError):
        await mem_session.flush()
    await mem_session.rollback()


# ---------------------------------------------------------------------------
# TC3b (PH-221): two repos with DIFFERENT slugs coexist on one board (1:N)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_multiple_per_board(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo1 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="api",
        name="api",
        is_primary=True,
        local_path="/repos/local/api",
    )
    repo2 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="web",
        name="web",
        is_primary=False,
        local_path="/repos/local/web",
    )
    mem_session.add_all([repo1, repo2])
    await mem_session.commit()

    rows = (
        await mem_session.execute(select(Repository).where(Repository.board_id == board.id))
    ).scalars().all()
    assert len(rows) == 2
    assert {r.slug for r in rows} == {"api", "web"}


# ---------------------------------------------------------------------------
# TC4 (PH-221): Board.repositories collection + primary_repository property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_repositories_relationship_and_primary(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    primary = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="api",
        name="api",
        is_primary=True,
        local_path="/repos/local/api",
    )
    secondary = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="web",
        name="web",
        is_primary=False,
        local_path="/repos/local/web",
    )
    mem_session.add_all([primary, secondary])
    await mem_session.commit()

    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.repositories))
        )
    ).scalar_one()
    assert len(refreshed.repositories) == 2
    assert refreshed.primary_repository is not None
    assert refreshed.primary_repository.id == primary.id
    assert refreshed.primary_repository.board is refreshed  # back-reference works


# ---------------------------------------------------------------------------
# TC5: cascade delete — Board delete removes ALL Repository rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_delete_cascades_repositories(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    repo1 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="api",
        name="api",
        is_primary=True,
        local_path="/repos/local/api",
    )
    repo2 = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug="web",
        name="web",
        is_primary=False,
        local_path="/repos/local/web",
    )
    mem_session.add_all([repo1, repo2])
    await mem_session.commit()
    repo_ids = [repo1.id, repo2.id]

    await mem_session.delete(board)
    await mem_session.commit()

    for rid in repo_ids:
        orphan = (
            await mem_session.execute(select(Repository).where(Repository.id == rid))
        ).scalar_one_or_none()
        assert orphan is None, "Repository rows should be deleted by cascade"


# ---------------------------------------------------------------------------
# TC6: board without repositories → empty collection, primary None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_without_repository_is_empty(
    mem_session: AsyncSession,
    seeded: dict[str, object],
) -> None:
    board = seeded["board"]
    assert isinstance(board, Board)

    refreshed = (
        await mem_session.execute(
            select(Board)
            .where(Board.id == board.id)
            .options(selectinload(Board.repositories))
        )
    ).scalar_one()
    assert refreshed.repositories == []
    assert refreshed.primary_repository is None
