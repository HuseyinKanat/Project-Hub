"""PH-327 — board-scope authorization (broken access control hotfix).

Proves the additive board-membership READ gate on boards + tickets across the
SHARED service seam (so BOTH the REST endpoints AND the MCP ``_dispatch_tool``
channel are closed — a REST-only fix is explicitly rejected), plus the PATCH
board write-IDOR fix. Model: ``alice`` is a member of ONE board (like emrehan),
``bob`` of another, ``admin`` of BOTH (like huseyin, seeded into every board at
creation → membership-scope is regression-free for them). ``outsider`` has no
membership.

Ordering is load-bearing everywhere: unknown board/ticket → 404 FIRST,
resolved-but-non-member → 403 SECOND (mirrors the 5 existing gates).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.deps import current_actor
from app.cli import create_board
from app.core.exceptions import NotFound, PermissionDenied
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, Ticket, Workflow
from app.db.session import get_db_session
from app.main import app
from app.mcp.server import _dispatch_tool
from app.services.boards import list_boards
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES
from app.services.tickets import get_ticket_for_read, query_tickets


@dataclass
class Scope:
    session: AsyncSession
    board_a: Board  # key AA — alice + admin
    board_b: Board  # key BB — bob + admin
    admin: Actor  # member of BOTH boards
    alice: Actor  # member of AA only (the "emrehan" — one board)
    bob: Actor  # member of BB only
    outsider: Actor  # no membership
    ticket_a: Ticket  # lives on AA
    ticket_b: Ticket  # lives on BB


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
    """Reload WITH memberships eager-loaded (mirrors prod current_actor / MCP resolve).

    ``populate_existing`` forces the collection to REFRESH even if the identity-mapped
    instance already carries a loaded ``memberships`` (so a reload after a new membership
    row is written reflects it, instead of returning the stale cached collection).
    """
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor.id)
            .options(selectinload(Actor.memberships))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _reload_board(session: AsyncSession, board: Board) -> Board:
    return (
        await session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.workflow))
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
    bob = Actor(kind="agent", display_name="jarwis-backend@bob", token_hash="x", is_active=True)
    outsider = Actor(
        kind="agent", display_name="jarwis-qa@nobody", token_hash="x", is_active=True
    )
    mem_session.add_all([admin, alice, bob, outsider])
    await mem_session.flush()

    board_a = Board(
        key="AA", name="Alpha", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    board_b = Board(
        key="BB", name="Bravo", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    mem_session.add_all([board_a, board_b])
    await mem_session.flush()

    mem_session.add_all(
        [
            BoardMembership(board_id=board_a.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_a.id, actor_id=alice.id, role="backend_dev"),
            BoardMembership(board_id=board_b.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=board_b.id, actor_id=bob.id, role="backend_dev"),
        ]
    )

    ticket_a = Ticket(
        key="AA-1", board_id=board_a.id, type="feature", title="A one",
        state="backlog", reporter_id=admin.id,
    )
    ticket_b = Ticket(
        key="BB-1", board_id=board_b.id, type="feature", title="B one",
        state="backlog", reporter_id=admin.id,
    )
    mem_session.add_all([ticket_a, ticket_b])
    await mem_session.commit()

    return Scope(
        session=mem_session,
        board_a=await _reload_board(mem_session, board_a),
        board_b=await _reload_board(mem_session, board_b),
        admin=await _reload_actor(mem_session, admin),
        alice=await _reload_actor(mem_session, alice),
        bob=await _reload_actor(mem_session, bob),
        outsider=await _reload_actor(mem_session, outsider),
        ticket_a=ticket_a,
        ticket_b=ticket_b,
    )


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


# ===========================================================================
# Service seam — list_boards / query_tickets / get_ticket_for_read
# ===========================================================================


class TestServiceSeam:
    @pytest.mark.asyncio
    async def test_list_boards_scoped_to_member(self, scope: Scope) -> None:
        """AC1/AC6: a one-board member sees exactly 1; the all-boards admin sees both."""
        alice_boards = await list_boards(scope.session, scope.alice)
        assert {b.key for b in alice_boards} == {"AA"}  # NOT {AA, BB}

        admin_boards = await list_boards(scope.session, scope.admin)
        assert {b.key for b in admin_boards} == {"AA", "BB"}  # regression-free

    @pytest.mark.asyncio
    async def test_list_boards_empty_for_non_member(self, scope: Scope) -> None:
        assert await list_boards(scope.session, scope.outsider) == []

    @pytest.mark.asyncio
    async def test_query_tickets_board_id_gate(self, scope: Scope) -> None:
        """AC3: board_id of a non-member board → 403; unknown board → 404 FIRST."""
        with pytest.raises(PermissionDenied):
            await query_tickets(scope.session, actor=scope.alice, board_id="BB")
        with pytest.raises(NotFound):
            await query_tickets(scope.session, actor=scope.alice, board_id="ZZ")

    @pytest.mark.asyncio
    async def test_query_tickets_membership_scope(self, scope: Scope) -> None:
        """AC3/AC6: board_id omitted → only the actor's member boards' tickets."""
        alice_tickets = await query_tickets(scope.session, actor=scope.alice)
        assert {t.key for t in alice_tickets} == {"AA-1"}

        admin_tickets = await query_tickets(scope.session, actor=scope.admin)
        assert {t.key for t in admin_tickets} == {"AA-1", "BB-1"}

        assert await query_tickets(scope.session, actor=scope.outsider) == []

    @pytest.mark.asyncio
    async def test_query_tickets_own_board_ok(self, scope: Scope) -> None:
        rows = await query_tickets(scope.session, actor=scope.alice, board_id="AA")
        assert {t.key for t in rows} == {"AA-1"}

    @pytest.mark.asyncio
    async def test_get_ticket_for_read_gate(self, scope: Scope) -> None:
        """AC3: non-member of the ticket's board → 403; unknown ticket → 404; own → ok."""
        with pytest.raises(PermissionDenied):
            await get_ticket_for_read(scope.session, scope.alice, "BB-1")
        with pytest.raises(NotFound):
            await get_ticket_for_read(scope.session, scope.alice, "ZZ-999")
        ticket = await get_ticket_for_read(scope.session, scope.alice, "AA-1")
        assert ticket.key == "AA-1"


# ===========================================================================
# MCP dispatch — the SHARED seam must be closed on the agent channel too (AC5)
# ===========================================================================


class TestMcpChannel:
    @pytest.mark.asyncio
    async def test_mcp_list_boards_scoped(self, scope: Scope) -> None:
        alice_res = await _dispatch_tool("list_boards", {}, scope.alice, scope.session)
        assert {b["key"] for b in alice_res} == {"AA"}
        admin_res = await _dispatch_tool("list_boards", {}, scope.admin, scope.session)
        assert {b["key"] for b in admin_res} == {"AA", "BB"}

    @pytest.mark.asyncio
    async def test_mcp_get_board_gate(self, scope: Scope) -> None:
        with pytest.raises(PermissionDenied):
            await _dispatch_tool("get_board", {"board_id": "BB"}, scope.alice, scope.session)
        with pytest.raises(NotFound):
            await _dispatch_tool("get_board", {"board_id": "ZZ"}, scope.alice, scope.session)
        ok = await _dispatch_tool("get_board", {"board_id": "AA"}, scope.alice, scope.session)
        assert ok["key"] == "AA"

    @pytest.mark.asyncio
    async def test_mcp_query_tickets_gate_and_scope(self, scope: Scope) -> None:
        with pytest.raises(PermissionDenied):
            await _dispatch_tool(
                "query_tickets", {"board_id": "BB"}, scope.alice, scope.session
            )
        scoped = await _dispatch_tool("query_tickets", {}, scope.alice, scope.session)
        assert {t["key"] for t in scoped} == {"AA-1"}

    @pytest.mark.asyncio
    async def test_mcp_single_ticket_reads_gated(self, scope: Scope) -> None:
        """AC5: get_ticket / get_state / get_ticket_slice / query_history all 403 for
        a non-member on another board's ticket."""
        for tool, payload in [
            ("get_ticket", {"id": "BB-1"}),
            ("get_state", {"id": "BB-1"}),
            ("get_ticket_slice", {"id": "BB-1", "include": ["description"]}),
            ("query_history", {"id": "BB-1"}),
        ]:
            with pytest.raises(PermissionDenied):
                await _dispatch_tool(tool, payload, scope.alice, scope.session)

    @pytest.mark.asyncio
    async def test_mcp_single_ticket_reads_ok_for_member(self, scope: Scope) -> None:
        res = await _dispatch_tool("get_state", {"id": "AA-1"}, scope.alice, scope.session)
        assert res["id"] == "AA-1"


# ===========================================================================
# REST — boards + tickets read endpoints
# ===========================================================================


class TestRestBoards:
    def test_list_boards_scoped(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/boards")
        finally:
            _clear()
        assert resp.status_code == 200
        assert {b["key"] for b in resp.json()["boards"]} == {"AA"}

    def test_get_board_non_member_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/boards/BB")
        finally:
            _clear()
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_get_board_unknown_404_before_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/boards/ZZZ")
        finally:
            _clear()
        assert resp.status_code == 404

    def test_get_board_member_200(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/boards/AA")
        finally:
            _clear()
        assert resp.status_code == 200
        assert resp.json()["key"] == "AA"

    def test_sonarqube_issues_non_member_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/boards/BB/sonarqube/issues")
        finally:
            _clear()
        assert resp.status_code == 403

    def test_sonarqube_status_non_member_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/boards/BB/sonarqube/status")
        finally:
            _clear()
        assert resp.status_code == 403

    def test_sonarqube_status_member_200(self, scope: Scope) -> None:
        """A member still reads their own board's sonar status (graceful 200)."""
        client = _make_client(scope.bob, scope.session)
        try:
            resp = client.get("/api/boards/BB/sonarqube/status")
        finally:
            _clear()
        assert resp.status_code == 200


class TestRestTickets:
    def test_query_tickets_scoped(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets")
        finally:
            _clear()
        assert resp.status_code == 200
        assert {t["key"] for t in resp.json()["tickets"]} == {"AA-1"}

    def test_query_tickets_other_board_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets?board_id=BB")
        finally:
            _clear()
        assert resp.status_code == 403

    def test_get_ticket_non_member_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets/BB-1")
        finally:
            _clear()
        assert resp.status_code == 403

    def test_get_ticket_unknown_404(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets/ZZ-999")
        finally:
            _clear()
        assert resp.status_code == 404

    def test_get_ticket_member_200(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets/AA-1")
        finally:
            _clear()
        assert resp.status_code == 200
        assert resp.json()["key"] == "AA-1"

    def test_comments_non_member_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets/BB-1/comments")
        finally:
            _clear()
        assert resp.status_code == 403

    def test_history_non_member_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.get("/api/tickets/BB-1/history")
        finally:
            _clear()
        assert resp.status_code == 403

    def test_comments_member_200(self, scope: Scope) -> None:
        client = _make_client(scope.bob, scope.session)
        try:
            resp = client.get("/api/tickets/BB-1/comments")
        finally:
            _clear()
        assert resp.status_code == 200


# ===========================================================================
# PATCH /api/boards/{id} — write-IDOR (AC-IDOR): admin-only now
# ===========================================================================


class TestPatchBoardWriteIdor:
    def test_non_admin_member_cannot_patch(self, scope: Scope) -> None:
        """alice is a MEMBER of AA but not an admin → 403 (was 200: any actor could edit)."""
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.patch("/api/boards/AA", json={"description": "hijacked"})
        finally:
            _clear()
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_non_member_cannot_patch(self, scope: Scope) -> None:
        client = _make_client(scope.bob, scope.session)
        try:
            resp = client.patch("/api/boards/AA", json={"description": "hijacked"})
        finally:
            _clear()
        assert resp.status_code == 403

    def test_unknown_board_404_before_403(self, scope: Scope) -> None:
        client = _make_client(scope.alice, scope.session)
        try:
            resp = client.patch(f"/api/boards/{uuid4()}", json={"description": "x"})
        finally:
            _clear()
        assert resp.status_code == 404

    def test_admin_can_patch(self, scope: Scope) -> None:
        client = _make_client(scope.admin, scope.session)
        try:
            resp = client.patch("/api/boards/AA", json={"description": "legit admin edit"})
        finally:
            _clear()
        assert resp.status_code == 200
        assert resp.json()["description"] == "legit admin edit"


# ===========================================================================
# Scope-safety invariant: create_board seeds the admin membership the whole
# membership-SCOPE design leans on (no superadmin/created_by bypass exists).
# ===========================================================================


class TestCreateBoardSeedsMembershipInvariant:
    @pytest.mark.asyncio
    async def test_new_board_creator_is_member_and_in_scope(self, scope: Scope) -> None:
        """AC6: a board created via create_board seeds the (oldest-human) admin as an
        admin member, so the scoped list_boards immediately surfaces it — the guarantee
        that makes pure membership-scope regression-free for the admin/creator."""
        result = await create_board(
            "CC", "Charlie", description="new", session=scope.session
        )
        assert result["status"] == "created"

        membership = (
            await scope.session.execute(
                select(BoardMembership)
                .join(Board, BoardMembership.board_id == Board.id)
                .where(Board.key == "CC", BoardMembership.role == "admin")
            )
        ).scalar_one_or_none()
        assert membership is not None  # admin seeded → invariant holds

        # And the freshly-created board shows up in the admin's membership-scoped list.
        admin_reloaded = await _reload_actor(scope.session, scope.admin)
        assert "CC" in {b.key for b in await list_boards(scope.session, admin_reloaded)}
