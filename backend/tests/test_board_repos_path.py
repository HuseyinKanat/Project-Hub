"""PH-228 — Board.repos_path model field + serializer exposure.

Covers:
- The ORM round-trips repos_path (host path persists, defaults to None).
- serializers.board_response exposes repos_path (backfilled value AND null).
- BoardUpdate is NOT extended with repos_path in PH-228 (editability deferred to
  PH-230) — the PATCH surface stays unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.models import Actor, Board, Workflow
from app.schemas import BoardUpdate
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.serializers import board_response


@pytest_asyncio.fixture
async def mem_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


async def _make_board(session: AsyncSession, *, repos_path: str | None) -> Board:
    workflow = Workflow(
        name="Default",
        states=DEFAULT_STATES,
        transitions=DEFAULT_TRANSITIONS,
        is_default=True,
    )
    session.add(workflow)
    await session.flush()
    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    session.add(admin)
    await session.flush()
    board = Board(
        key="BP",
        name="Board Path Test",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
        repos_path=repos_path,
    )
    session.add(board)
    await session.commit()
    return board


async def _reload(session: AsyncSession, board_id: uuid.UUID) -> Board:
    return (
        await session.execute(
            select(Board)
            .where(Board.id == board_id)
            .options(
                selectinload(Board.repositories),
                selectinload(Board.workflow),
                selectinload(Board.sonarqube_metrics),
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_repos_path_persists_host_value(mem_session: AsyncSession) -> None:
    board = await _make_board(
        mem_session, repos_path="/Users/huseyinkanat/Documents/kims"
    )
    fetched = (
        await mem_session.execute(select(Board).where(Board.id == board.id))
    ).scalar_one()
    assert fetched.repos_path == "/Users/huseyinkanat/Documents/kims"


@pytest.mark.asyncio
async def test_repos_path_defaults_to_none(mem_session: AsyncSession) -> None:
    board = await _make_board(mem_session, repos_path=None)
    fetched = (
        await mem_session.execute(select(Board).where(Board.id == board.id))
    ).scalar_one()
    assert fetched.repos_path is None


@pytest.mark.asyncio
async def test_serializer_exposes_repos_path(mem_session: AsyncSession) -> None:
    board = await _make_board(
        mem_session, repos_path="/Users/huseyinkanat/Documents/project-hub"
    )
    refreshed = await _reload(mem_session, board.id)
    resp = board_response(refreshed)
    assert resp.repos_path == "/Users/huseyinkanat/Documents/project-hub"


@pytest.mark.asyncio
async def test_serializer_repos_path_null_when_unset(mem_session: AsyncSession) -> None:
    board = await _make_board(mem_session, repos_path=None)
    refreshed = await _reload(mem_session, board.id)
    resp = board_response(refreshed)
    assert resp.repos_path is None


def test_board_update_accepts_repos_path() -> None:
    # PH-230 fulfills the PH-228-deferred editability — BoardUpdate now carries
    # repos_path (validated at the API boundary via repo_paths → 422).
    assert "repos_path" in BoardUpdate.model_fields
    # Field is optional (None default) so existing Name/Description-only PATCHes
    # are unaffected.
    parsed = BoardUpdate.model_validate({"name": "X"})
    assert parsed.repos_path is None
    # An explicit path round-trips unchanged onto the schema.
    parsed2 = BoardUpdate.model_validate(
        {"repos_path": "/Users/huseyinkanat/Documents/project-hub"}
    )
    assert parsed2.repos_path == "/Users/huseyinkanat/Documents/project-hub"


@pytest.mark.asyncio
async def test_update_board_sets_and_clears_repos_path(
    mem_session: AsyncSession,
) -> None:
    # PH-230: update_board() persists a new repos_path and clears it on "".
    from app.services.boards import update_board

    board = await _make_board(mem_session, repos_path=None)
    await update_board(
        mem_session, board, repos_path="/Users/huseyinkanat/Documents/project-hub"
    )
    await mem_session.commit()
    fetched = await _reload(mem_session, board.id)
    assert fetched.repos_path == "/Users/huseyinkanat/Documents/project-hub"

    # Empty string clears to NULL (valid "no path" state, not an error).
    await update_board(mem_session, board, repos_path="")
    await mem_session.commit()
    cleared = await _reload(mem_session, board.id)
    assert cleared.repos_path is None

    # None means "not provided" → leaves the value untouched.
    await update_board(
        mem_session, board, repos_path="/Users/huseyinkanat/Documents/project-hub"
    )
    await mem_session.commit()
    await update_board(mem_session, board, name="Renamed")  # repos_path omitted
    await mem_session.commit()
    untouched = await _reload(mem_session, board.id)
    assert untouched.repos_path == "/Users/huseyinkanat/Documents/project-hub"
