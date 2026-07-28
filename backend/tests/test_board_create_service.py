"""PH-331 — shared create-board service + CLI-equivalence regression.

Proves AC1 (the create-board core was extracted into
``services.boards.create_board_with_defaults`` and the CLI still produces an
equivalent board with the same ``{key,id,status}`` dict + admin-membership +
default workflow/roles seeding) and the service's uniqueness contract (duplicate
key → ``Conflict`` 409).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cli import create_board
from app.core.exceptions import Conflict
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Workflow
from app.services.boards import create_board_with_defaults
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    async with session_local() as sess:
        yield sess
    await engine.dispose()


async def _seed_workflow_and_admin(sess: AsyncSession) -> Actor:
    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    sess.add(workflow)
    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    sess.add(admin)
    await sess.flush()
    return admin


class TestServiceCreateBoardWithDefaults:
    @pytest.mark.asyncio
    async def test_creates_board_workflow_roles_and_admin_membership(
        self, session: AsyncSession
    ) -> None:
        """AC3: the new board is seeded with the default workflow + DEFAULT_WEB_ROLES
        AND the passed admin_actor becomes a board ``admin`` member."""
        admin = await _seed_workflow_and_admin(session)
        board = await create_board_with_defaults(
            session,
            key="tdi",  # lowercase input → uppercased server-side
            name="Test Driven",
            description="desc",
            project_type="web_app",
            admin_actor=admin,
        )
        await session.commit()

        assert board.key == "TDI"
        assert board.name == "Test Driven"
        assert board.description == "desc"
        assert board.project_type == "web_app"
        assert board.roles == DEFAULT_WEB_ROLES
        assert board.created_by == admin.id
        # default workflow reused (not a new one)
        workflows = (await session.execute(select(Workflow))).scalars().all()
        assert len(workflows) == 1

        membership = (
            await session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id,
                    BoardMembership.actor_id == admin.id,
                    BoardMembership.role == "admin",
                )
            )
        ).scalar_one_or_none()
        assert membership is not None

    @pytest.mark.asyncio
    async def test_creates_default_workflow_when_absent(self, session: AsyncSession) -> None:
        """No default workflow yet → the service creates one (parity with old CLI)."""
        admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
        session.add(admin)
        await session.flush()

        board = await create_board_with_defaults(
            session, key="AA", name="Alpha", admin_actor=admin
        )
        await session.commit()
        assert board.workflow_id is not None
        wf = (await session.execute(select(Workflow))).scalars().all()
        assert len(wf) == 1 and wf[0].is_default is True

    @pytest.mark.asyncio
    async def test_duplicate_key_raises_conflict(self, session: AsyncSession) -> None:
        """AC5: a duplicate board key → Conflict (the 409 the REST layer maps)."""
        admin = await _seed_workflow_and_admin(session)
        await create_board_with_defaults(session, key="BB", name="Bravo", admin_actor=admin)
        await session.commit()

        with pytest.raises(Conflict):
            await create_board_with_defaults(session, key="BB", name="Bravo2", admin_actor=admin)

    @pytest.mark.asyncio
    async def test_duplicate_key_is_case_insensitive(self, session: AsyncSession) -> None:
        admin = await _seed_workflow_and_admin(session)
        await create_board_with_defaults(session, key="CC", name="Charlie", admin_actor=admin)
        await session.commit()
        with pytest.raises(Conflict):
            await create_board_with_defaults(session, key="cc", name="dup", admin_actor=admin)


class TestCliCreateBoardRegression:
    @pytest.mark.asyncio
    async def test_cli_still_creates_equivalent_board(self, session: AsyncSession) -> None:
        """AC1: cli.create_board delegates to the service but its observable behavior
        (default workflow + DEFAULT_WEB_ROLES + first-human admin membership + the
        {key,id,status:created} dict) is UNCHANGED."""
        await _seed_workflow_and_admin(session)
        result = await create_board("KIM", "Kims", description="resto", session=session)

        assert result["status"] == "created"
        assert result["key"] == "KIM"
        assert "id" in result

        board = (
            await session.execute(select(Board).where(Board.key == "KIM"))
        ).scalar_one()
        assert board.roles == DEFAULT_WEB_ROLES
        assert board.description == "resto"

        membership = (
            await session.execute(
                select(BoardMembership).where(
                    BoardMembership.board_id == board.id, BoardMembership.role == "admin"
                )
            )
        ).scalar_one_or_none()
        assert membership is not None

    @pytest.mark.asyncio
    async def test_cli_idempotent_existing(self, session: AsyncSession) -> None:
        """AC1: re-running on the same key returns status:existing (no raise)."""
        await _seed_workflow_and_admin(session)
        first = await create_board("DUP", "Dup", session=session)
        assert first["status"] == "created"
        second = await create_board("DUP", "Dup", session=session)
        assert second["status"] == "existing"
        assert second["id"] == first["id"]

    @pytest.mark.asyncio
    async def test_cli_no_admin_branch(self, session: AsyncSession) -> None:
        """AC1: no human admin actor → status:no_admin (unchanged branch)."""
        workflow = Workflow(
            name="Default",
            states=DEFAULT_STATES,
            transitions=DEFAULT_TRANSITIONS,
            is_default=True,
        )
        session.add(workflow)
        await session.flush()
        result = await create_board("NOA", "NoAdmin", session=session)
        assert result == {"status": "no_admin"}
