"""PH-335 — derived per-epic progress rollup (read-only, per-board).

Proves the aggregation service ``services.progress.epic_progress`` + the dedicated
``api/progress.py`` router across:
  - AC1: per-epic + board + ungrouped buckets, served by the NEW router.
  - AC2: story-points-weighted vs count-based percent (single rule, no knobs);
         child-less epic -> 0.0 with no div-by-zero.
  - AC3: soft-delete excluded (num + denom); other-board tickets excluded;
         unknown board -> 404, non-member -> 403; done from workflow ``category``,
         NOT a literal "done" string.
  - AC4: ungrouped bucket, child-less epic 0/0, ONE query + in-memory group-by
         (no N+1 -> query count is constant in the number of epics).

Model mirrors ``test_board_scope_authz.py``: an in-memory SQLite DB, actors with
eager-loaded memberships, REST via FastAPI dependency overrides.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.core.exceptions import NotFound, PermissionDenied
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Ticket, Workflow
from app.db.session import get_db_session
from app.main import app
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.progress import epic_progress


@dataclass
class World:
    engine: AsyncEngine
    session: AsyncSession
    aa: Board  # key AA — the richly-populated board under test
    admin: Actor  # member of AA
    alice: Actor  # member of AA (used for member REST 200)
    outsider: Actor  # no membership -> 403


@pytest_asyncio.fixture
async def mem() -> AsyncIterator[tuple[AsyncEngine, AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    async with session_local() as session:
        yield engine, session
    await engine.dispose()


async def _reload_actor(session: AsyncSession, actor: Actor) -> Actor:
    """Reload WITH memberships eager-loaded (mirrors prod current_actor)."""
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor.id)
            .options(selectinload(Actor.memberships))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _t(  # tiny Ticket factory to keep the fixture readable
    *,
    key: str,
    board: Board,
    reporter: Actor,
    type: str = "feature",
    state: str = "backlog",
    epic_id: object = None,
    story_points: int | None = None,
    deleted_at: object = None,
) -> Ticket:
    return Ticket(
        key=key,
        board_id=board.id,
        type=type,
        title=key,
        state=state,
        reporter_id=reporter.id,
        epic_id=epic_id,
        story_points=story_points,
        deleted_at=deleted_at,
    )


@pytest_asyncio.fixture
async def world(mem: tuple[AsyncEngine, AsyncSession]) -> World:
    engine, session = mem
    from datetime import UTC, datetime

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    session.add(workflow)
    await session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    alice = Actor(kind="agent", display_name="jarwis-backend@alice", token_hash="y", is_active=True)
    outsider = Actor(kind="agent", display_name="jarwis-qa@nobody", token_hash="z", is_active=True)
    session.add_all([admin, alice, outsider])
    await session.flush()

    aa = Board(
        key="AA", name="Alpha", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    bb = Board(
        key="BB", name="Bravo", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    session.add_all([aa, bb])
    await session.flush()

    session.add_all(
        [
            BoardMembership(board_id=aa.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=aa.id, actor_id=alice.id, role="backend_dev"),
            BoardMembership(board_id=bb.id, actor_id=admin.id, role="admin"),
        ]
    )

    # Epics + children. Keys chosen so epic sort order is E1 < E2 < E3.
    e1 = _t(key="AA-1", board=aa, reporter=admin, type="epic")
    e2 = _t(key="AA-5", board=aa, reporter=admin, type="epic")
    e3 = _t(key="AA-8", board=aa, reporter=admin, type="epic")  # child-less
    e4 = _t(  # soft-deleted epic -> excluded; its child falls to ungrouped
        key="AA-11", board=aa, reporter=admin, type="epic", deleted_at=datetime.now(UTC)
    )
    session.add_all([e1, e2, e3, e4])
    await session.flush()

    session.add_all(
        [
            # E1 -> both children pointed -> weighted (20%) differs from count (50%)
            _t(key="AA-2", board=aa, reporter=admin, state="done", epic_id=e1.id, story_points=2),
            _t(key="AA-3", board=aa, reporter=admin, state="in_progress", epic_id=e1.id,
               story_points=8),
            # soft-deleted child of E1 -> excluded from BOTH num + denom
            _t(key="AA-4", board=aa, reporter=admin, state="done", epic_id=e1.id,
               story_points=100, deleted_at=datetime.now(UTC)),
            # E2 -> one child unpointed -> count path (50%), NOT weighted (0%)
            _t(key="AA-6", board=aa, reporter=admin, state="done", epic_id=e2.id),
            _t(key="AA-7", board=aa, reporter=admin, state="in_progress", epic_id=e2.id,
               story_points=4),
            # ungrouped: no epic
            _t(key="AA-9", board=aa, reporter=admin, state="done"),
            _t(key="AA-10", board=aa, reporter=admin, type="bug", state="backlog"),
            # child of the soft-deleted epic E4 -> dangling ref -> ungrouped (no phantom)
            _t(key="AA-12", board=aa, reporter=admin, state="in_progress", epic_id=e4.id),
            # OTHER board -> must never be counted in AA's rollup
            _t(key="BB-1", board=bb, reporter=admin, state="done"),
        ]
    )
    await session.commit()

    return World(
        engine=engine,
        session=session,
        aa=aa,
        admin=await _reload_actor(session, admin),
        alice=await _reload_actor(session, alice),
        outsider=await _reload_actor(session, outsider),
    )


# ===========================================================================
# Service seam — aggregation correctness
# ===========================================================================


class TestAggregation:
    @pytest.mark.asyncio
    async def test_epic_buckets_shape_and_counts(self, world: World) -> None:
        """AC1/AC4: per-epic + board + ungrouped buckets with correct item counts."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")

        assert resp.board_id == str(world.aa.id)
        assert [e.epic_key for e in resp.epics] == ["AA-1", "AA-5", "AA-8"]

        by_key = {e.epic_key: e for e in resp.epics}
        e1 = by_key["AA-1"]
        assert (e1.done, e1.total) == (1, 2)
        assert e1.state_histogram == {"done": 1, "in_progress": 1}

        e2 = by_key["AA-5"]
        assert (e2.done, e2.total) == (1, 2)

        e3 = by_key["AA-8"]  # child-less
        assert (e3.done, e3.total) == (0, 0)
        assert e3.state_histogram == {}

    @pytest.mark.asyncio
    async def test_weighted_when_all_children_pointed(self, world: World) -> None:
        """AC2: E1 all pointed (2 done / 8 in_progress) -> 20% weighted (NOT 50% count)."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")
        e1 = next(e for e in resp.epics if e.epic_key == "AA-1")
        assert e1.weighted_pct == pytest.approx(20.0)  # 100*2/(2+8); count would be 50.0

    @pytest.mark.asyncio
    async def test_count_when_any_child_unpointed(self, world: World) -> None:
        """AC2: E2 has an unpointed child -> count path (50%), NOT weighted (0%)."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")
        e2 = next(e for e in resp.epics if e.epic_key == "AA-5")
        assert e2.weighted_pct == pytest.approx(50.0)  # 100*1/2; weighted would be 0/4 = 0.0

    @pytest.mark.asyncio
    async def test_childless_epic_no_div_by_zero(self, world: World) -> None:
        """AC2: a child-less epic reports weighted_pct 0.0 with no ZeroDivisionError."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")
        e3 = next(e for e in resp.epics if e.epic_key == "AA-8")
        assert e3.weighted_pct == 0.0

    @pytest.mark.asyncio
    async def test_ungrouped_bucket(self, world: World) -> None:
        """AC4: no-epic tickets + a child of a soft-deleted epic aggregate to ungrouped."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")
        assert (resp.ungrouped.done, resp.ungrouped.total) == (1, 3)
        assert resp.ungrouped.state_histogram == {"done": 1, "backlog": 1, "in_progress": 1}
        assert resp.ungrouped.weighted_pct == pytest.approx(100.0 / 3)

    @pytest.mark.asyncio
    async def test_board_rollup_over_all_non_deleted(self, world: World) -> None:
        """AC5: board bucket spans ALL non-deleted board tickets (soft-deleted excluded)."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")
        # 10 non-deleted AA rows (AA-4 + AA-11 deleted, BB-1 other board); 3 done.
        assert (resp.board.done, resp.board.total) == (3, 10)
        assert resp.board.weighted_pct == pytest.approx(30.0)
        assert resp.board.state_histogram == {"backlog": 4, "done": 3, "in_progress": 3}

    @pytest.mark.asyncio
    async def test_soft_delete_and_board_scope_excluded(self, world: World) -> None:
        """AC3: the soft-deleted done child (sp=100) is NOT in E1; BB-1 not in any bucket."""
        resp = await epic_progress(world.session, actor=world.admin, board_id="AA")
        e1 = next(e for e in resp.epics if e.epic_key == "AA-1")
        # AA-4 would have made E1 2/3 done and skewed the weight; it must be gone.
        assert (e1.done, e1.total) == (1, 2)
        # BB-1 (done, other board) must not inflate the board rollup.
        assert resp.board.total == 10  # not 11


# ===========================================================================
# AC3 — auth gate at the service seam (unknown -> 404 FIRST, non-member -> 403)
# ===========================================================================


class TestAuthGate:
    @pytest.mark.asyncio
    async def test_unknown_board_404(self, world: World) -> None:
        with pytest.raises(NotFound):
            await epic_progress(world.session, actor=world.admin, board_id="ZZ")

    @pytest.mark.asyncio
    async def test_non_member_403(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await epic_progress(world.session, actor=world.outsider, board_id="AA")


# ===========================================================================
# AC3 — done-detection reads the workflow category, not a literal "done"
# ===========================================================================


class TestDoneDetectionByCategory:
    @pytest.mark.asyncio
    async def test_done_from_category_not_literal_string(
        self, mem: tuple[AsyncEngine, AsyncSession]
    ) -> None:
        _engine, session = mem
        custom_states = [
            {"name": "todo", "category": "new", "is_initial": True, "is_terminal": False},
            {"name": "shipped", "category": "done", "is_initial": False, "is_terminal": True},
        ]
        workflow = Workflow(
            name="Custom", states=custom_states, transitions=[], is_default=False
        )
        session.add(workflow)
        await session.flush()

        admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
        session.add(admin)
        await session.flush()

        cc = Board(
            key="CC", name="Charlie", description="", project_type="web_app",
            workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
        )
        session.add(cc)
        await session.flush()
        session.add(BoardMembership(board_id=cc.id, actor_id=admin.id, role="admin"))

        epic = _t(key="CC-1", board=cc, reporter=admin, type="epic")
        session.add(epic)
        await session.flush()
        session.add_all(
            [
                # workflow's done-category state -> counts as done
                _t(key="CC-2", board=cc, reporter=admin, state="shipped", epic_id=epic.id),
                # literal "done" is NOT a state of THIS workflow -> must NOT count
                _t(key="CC-3", board=cc, reporter=admin, state="done", epic_id=epic.id),
            ]
        )
        await session.commit()

        reloaded = await _reload_actor(session, admin)
        resp = await epic_progress(session, actor=reloaded, board_id="CC")
        item = resp.epics[0]
        assert item.done == 1  # only "shipped" (category done), NOT the literal "done"
        assert item.total == 2


# ===========================================================================
# AC4 — no N+1: the query count is constant in the number of epics
# ===========================================================================


class TestNoNPlusOne:
    @pytest.mark.asyncio
    async def test_query_count_constant_in_epic_count(
        self, mem: tuple[AsyncEngine, AsyncSession]
    ) -> None:
        engine, session = mem
        workflow = Workflow(
            name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS,
            is_default=True,
        )
        session.add(workflow)
        await session.flush()
        admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
        session.add(admin)
        await session.flush()

        async def _seed(key: str, n_epics: int) -> None:
            board = Board(
                key=key, name=key, description="", project_type="web_app",
                workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
            )
            session.add(board)
            await session.flush()
            session.add(BoardMembership(board_id=board.id, actor_id=admin.id, role="admin"))
            n = 1
            for _ in range(n_epics):
                epic = _t(key=f"{key}-{n}", board=board, reporter=admin, type="epic")
                session.add(epic)
                await session.flush()
                n += 1
                for j in range(2):
                    session.add(
                        _t(
                            key=f"{key}-{n}", board=board, reporter=admin,
                            state="done" if j == 0 else "in_progress", epic_id=epic.id,
                        )
                    )
                    n += 1
            await session.commit()

        await _seed("SM", n_epics=2)
        await _seed("BG", n_epics=8)
        reloaded_admin = await _reload_actor(session, admin)

        selects: list[str] = []

        def _before(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _before)
        try:
            await epic_progress(session, actor=reloaded_admin, board_id="SM")
            n_small = len(selects)
            selects.clear()
            await epic_progress(session, actor=reloaded_admin, board_id="BG")
            n_big = len(selects)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _before)

        # Same number of SELECTs for 2 epics vs 8 epics -> O(1) in #epics (no N+1).
        assert n_small == n_big


# ===========================================================================
# REST — the NEW api/progress.py router + board-scope auth (AC1/AC3)
# ===========================================================================


def _make_client(actor: Actor, session: AsyncSession) -> TestClient:
    async def _fake_current_actor() -> Actor:
        return actor

    async def _fake_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[current_actor] = _fake_current_actor
    app.dependency_overrides[get_db_session] = _fake_session
    return TestClient(app, raise_server_exceptions=True)


def _clear() -> None:
    app.dependency_overrides.clear()


class TestRest:
    def test_member_200_shape(self, world: World) -> None:
        client = _make_client(world.alice, world.session)
        try:
            resp = client.get("/api/boards/AA/epics/progress")
        finally:
            _clear()
        assert resp.status_code == 200
        body = resp.json()
        assert body["board_id"] == str(world.aa.id)
        assert {"board", "epics", "ungrouped"} <= body.keys()
        assert [e["epic_key"] for e in body["epics"]] == ["AA-1", "AA-5", "AA-8"]

    def test_non_member_403(self, world: World) -> None:
        client = _make_client(world.outsider, world.session)
        try:
            resp = client.get("/api/boards/AA/epics/progress")
        finally:
            _clear()
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_unknown_board_404_before_403(self, world: World) -> None:
        client = _make_client(world.outsider, world.session)
        try:
            resp = client.get("/api/boards/ZZZ/epics/progress")
        finally:
            _clear()
        assert resp.status_code == 404
