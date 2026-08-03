"""PH-338 — per-board SINGLETON project-summary store (DB + REST + MCP read/write).

Proves the additive ``board_summaries`` migration, the ``services.board_summary``
read + full-upsert + delete with the asymmetric auth ladder (read = membership, write
= ``board.summary.write`` cap), the dedicated ``api/board_summary.py`` router, and the
MCP ``get_board_summary`` / ``set_board_summary`` tools across:
  - AC1: additive ``board_summaries`` table (single head ``ph336boardnotes``); EXACT
         columns; ``board_id`` FK CASCADE + UNIQUE (singleton); ``milestones`` JSON
         nullable=False; plain up/down round-trip; ORM↔migration parity.
  - AC2: singleton (0..1) — a second upsert UPDATEs the same row (UNIQUE(board_id));
         board delete CASCADEs the summary.
  - AC3: REST CRUD — GET absent -> 200 null (vs unknown board 404); PUT full-upsert;
         DELETE 204 (absent -> 404); invalid milestone -> 422 no partial write; auth
         404-then-403.
  - AC4: MCP ``get_board_summary`` read-only, membership-gated (null when absent).
  - AC5: MCP ``set_board_summary`` + ``board.summary.write`` gate — pm/admin/orchestrator
         write; a read-only role (backend_dev) AND a non-member are denied; write->read
         consistent (English status planned|active|done).

Model mirrors ``test_board_notes.py``: an in-memory SQLite DB, actors with eager-loaded
memberships, REST via FastAPI dependency overrides.
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload

from app import schemas
from app.api.deps import current_actor
from app.core.exceptions import NotFound, PermissionDenied
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, BoardSummary, Workflow
from app.db.session import get_db_session
from app.main import app
from app.mcp.server import _dispatch_tool
from app.services.board_summary import delete_summary, get_summary, upsert_summary
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

_MIGRATION_FILE = "app/db/migrations/versions/20260804_0020_ph_338_board_summary.py"


def _load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("ph338_mig", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _upsert(**kwargs: object) -> schemas.BoardSummaryUpsert:
    """Build an upsert body; ``milestones`` accepts Milestone kwargs dicts."""
    raw_milestones = kwargs.pop("milestones", [])
    milestones = [schemas.Milestone(**m) for m in raw_milestones]  # type: ignore[arg-type]
    return schemas.BoardSummaryUpsert(milestones=milestones, **kwargs)  # type: ignore[arg-type]


# ===========================================================================
# AC1 — additive migration (single head), exact schema, up/down round-trip
# ===========================================================================


class TestMigration:
    def test_revises_verified_head(self) -> None:
        """AC1: down_revision is the single verified Alembic head ``ph336boardnotes``."""
        mig = _load_migration()
        assert mig.revision == "ph338boardsummary"
        assert mig.down_revision == "ph336boardnotes"

    def test_up_down_round_trips_with_exact_schema(self) -> None:
        """AC1: plain CREATE TABLE with EXACTLY the agreed columns, ``board_id`` FK
        ``ondelete=CASCADE`` + UNIQUE (singleton), ``milestones`` NOT NULL, and a clean
        plain drop_table downgrade."""
        mig = _load_migration()
        eng = sa.create_engine("sqlite://")
        with eng.connect() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
            # Pre-338 schema slice the migration's FKs reference.
            conn.execute(sa.text("CREATE TABLE actors (id TEXT PRIMARY KEY, display_name TEXT)"))
            conn.execute(sa.text("CREATE TABLE boards (id TEXT PRIMARY KEY, key TEXT)"))
            conn.commit()

            ctx = MigrationContext.configure(conn)
            assert type(ctx.impl).__name__ == "SQLiteImpl"
            with Operations.context(ctx):
                mig.upgrade()
            conn.commit()

            insp = sa.inspect(conn)
            assert "board_summaries" in insp.get_table_names()
            cols = {c["name"] for c in insp.get_columns("board_summaries")}
            assert cols == {
                "id", "board_id", "purpose", "status", "progress", "highlights",
                "milestones", "updated_by", "created_at", "updated_at",
            }

            by_name = {c["name"]: c for c in insp.get_columns("board_summaries")}
            # Sections are nullable (a partial summary is valid); milestones is NOT.
            assert by_name["purpose"]["nullable"] is True
            assert by_name["status"]["nullable"] is True
            assert by_name["progress"]["nullable"] is True
            assert by_name["highlights"]["nullable"] is True
            assert by_name["milestones"]["nullable"] is False
            assert by_name["board_id"]["nullable"] is False
            assert by_name["updated_by"]["nullable"] is True

            # board_id FK CASCADEs; updated_by does NOT (deleted author -> null name).
            fks = {
                tuple(fk["constrained_columns"]): fk
                for fk in insp.get_foreign_keys("board_summaries")
            }
            assert fks[("board_id",)]["referred_table"] == "boards"
            assert fks[("board_id",)]["options"].get("ondelete") == "CASCADE"
            assert fks[("updated_by",)]["referred_table"] == "actors"
            assert not fks[("updated_by",)]["options"].get("ondelete")

            # UNIQUE(board_id) — the singleton (0..1 per board) invariant.
            uniques = insp.get_unique_constraints("board_summaries")
            assert any(u["column_names"] == ["board_id"] for u in uniques)

            with Operations.context(ctx):
                mig.downgrade()
            conn.commit()
            assert "board_summaries" not in sa.inspect(conn).get_table_names()

    def test_create_all_carries_board_summaries(self) -> None:
        """AC1: the ORM test schema (Base.metadata.create_all) materializes the table
        with a CASCADE + UNIQUE board_id FK — 1:1 parity with the migration."""
        eng = sa.create_engine("sqlite://")
        Base.metadata.create_all(eng)
        insp = sa.inspect(eng)
        assert "board_summaries" in insp.get_table_names()
        board_fk = next(
            fk for fk in insp.get_foreign_keys("board_summaries")
            if fk["constrained_columns"] == ["board_id"]
        )
        assert board_fk["options"].get("ondelete") == "CASCADE"
        uniques = insp.get_unique_constraints("board_summaries")
        assert any(u["column_names"] == ["board_id"] for u in uniques)

    def test_board_id_ondelete_cascade_is_functional(self) -> None:
        """AC2: the ``board_id`` ON DELETE CASCADE actually removes a summary when its
        board is deleted — tested at the DB level (raw DELETE with foreign_keys ON),
        the exact mechanism the app relies on (there is no board-DELETE endpoint; the
        CASCADE only fires on a CLI/DB board delete)."""
        mig = _load_migration()
        eng = sa.create_engine("sqlite://")
        with eng.connect() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=ON"))
            conn.execute(sa.text("CREATE TABLE actors (id TEXT PRIMARY KEY, display_name TEXT)"))
            conn.execute(sa.text("CREATE TABLE boards (id TEXT PRIMARY KEY, key TEXT)"))
            conn.commit()
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mig.upgrade()
            conn.commit()

            conn.execute(sa.text("INSERT INTO boards (id, key) VALUES ('b1', 'PH')"))
            conn.execute(
                sa.text(
                    "INSERT INTO board_summaries (id, board_id, milestones) "
                    "VALUES ('s1', 'b1', '[]')"
                )
            )
            conn.commit()
            assert conn.execute(sa.text("SELECT count(*) FROM board_summaries")).scalar() == 1
            # Deleting the board CASCADEs its summary away.
            conn.execute(sa.text("DELETE FROM boards WHERE id = 'b1'"))
            conn.commit()
            assert conn.execute(sa.text("SELECT count(*) FROM board_summaries")).scalar() == 0


# ===========================================================================
# In-memory world (service / MCP async tests + REST sync tests share it)
# ===========================================================================


@dataclass
class World:
    session: AsyncSession
    ph: Board  # board under test
    zz: Board  # a SECOND board (isolation guard)
    admin: Actor  # member of PH (role admin — has "*")
    pm: Actor  # member of PH (role pm — has board.summary.write)
    orchestrator: Actor  # member of PH (role orchestrator — has board.summary.write)
    member: Actor  # member of PH (role backend_dev — READ-ONLY for summary write)
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
    return (
        await session.execute(
            select(Actor)
            .where(Actor.id == actor.id)
            .options(selectinload(Actor.memberships))
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest_asyncio.fixture
async def world(mem: tuple[AsyncEngine, AsyncSession]) -> World:
    _engine, session = mem
    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    session.add(workflow)
    await session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="a", is_active=True)
    pm = Actor(kind="agent", display_name="jarwis-pm@alice", token_hash="p", is_active=True)
    orchestrator = Actor(
        kind="agent", display_name="jarwis-orchestrator@alice", token_hash="o", is_active=True
    )
    member = Actor(
        kind="agent", display_name="jarwis-backend@alice", token_hash="m", is_active=True
    )
    outsider = Actor(kind="agent", display_name="jarwis-qa@nobody", token_hash="z", is_active=True)
    session.add_all([admin, pm, orchestrator, member, outsider])
    await session.flush()

    ph = Board(
        key="PH", name="ProjectHub", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    zz = Board(
        key="ZZ", name="Zeta", description="", project_type="web_app",
        workflow_id=workflow.id, roles=DEFAULT_WEB_ROLES, created_by=admin.id,
    )
    session.add_all([ph, zz])
    await session.flush()
    session.add_all(
        [
            BoardMembership(board_id=ph.id, actor_id=admin.id, role="admin"),
            BoardMembership(board_id=ph.id, actor_id=pm.id, role="pm"),
            BoardMembership(board_id=ph.id, actor_id=orchestrator.id, role="orchestrator"),
            BoardMembership(board_id=ph.id, actor_id=member.id, role="backend_dev"),
        ]
    )
    await session.commit()

    return World(
        session=session,
        ph=ph,
        zz=zz,
        admin=await _reload_actor(session, admin),
        pm=await _reload_actor(session, pm),
        orchestrator=await _reload_actor(session, orchestrator),
        member=await _reload_actor(session, member),
        outsider=await _reload_actor(session, outsider),
    )


# ===========================================================================
# AC2 — service singleton upsert + cascade + read
# ===========================================================================


class TestServiceCrud:
    @pytest.mark.asyncio
    async def test_get_none_when_absent(self, world: World) -> None:
        """AC3: a board with no summary -> None (REST maps to 200 null)."""
        assert await get_summary(world.session, actor=world.member, board_id="PH") is None

    @pytest.mark.asyncio
    async def test_upsert_creates_then_reads(self, world: World) -> None:
        """AC2/AC5: create persists sections + milestones, stamps updated_by=writer,
        resolves the writer name, and round-trips through the DB."""
        created = await upsert_summary(
            world.session,
            actor=world.pm,
            board_id="PH",
            data=_upsert(
                purpose="Kanban for agents",
                status="healthy",
                milestones=[{"title": "M1", "status": "active", "order": 0}],
            ),
        )
        assert created.board_id == world.ph.id
        assert created.purpose == "Kanban for agents"
        assert created.status == "healthy"
        assert created.updated_by == world.pm.id
        assert created.updated_by_name == "jarwis-pm@alice"
        assert [m.title for m in created.milestones] == ["M1"]
        assert created.milestones[0].status == "active"

        got = await get_summary(world.session, actor=world.member, board_id="PH")
        assert got is not None
        assert got.purpose == "Kanban for agents"
        assert got.updated_by_name == "jarwis-pm@alice"

    @pytest.mark.asyncio
    async def test_second_upsert_updates_singleton_no_duplicate(self, world: World) -> None:
        """AC2: a second upsert UPDATEs the same row — never a second row (UNIQUE)."""
        await upsert_summary(
            world.session, actor=world.pm, board_id="PH", data=_upsert(purpose="v1")
        )
        updated = await upsert_summary(
            world.session, actor=world.admin, board_id="PH", data=_upsert(purpose="v2", status="s2")
        )
        assert updated.purpose == "v2"
        assert updated.status == "s2"
        assert updated.updated_by == world.admin.id  # last writer wins
        # Exactly ONE row for the board.
        rows = (
            await world.session.execute(
                select(BoardSummary).where(BoardSummary.board_id == world.ph.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_milestones_round_trip_with_due_date(self, world: World) -> None:
        """AC5/R4: a milestone due_date serializes to ISO (mode=json) and parses back to
        a ``date`` — no SQLite JSON-encode blowup on the raw date object."""
        await upsert_summary(
            world.session,
            actor=world.pm,
            board_id="PH",
            data=_upsert(
                milestones=[
                    {"title": "Ship", "target": "GA", "status": "planned", "order": 2,
                     "due_date": date(2026, 9, 1)},
                    {"title": "Beta", "status": "done", "order": 1},
                ]
            ),
        )
        got = await get_summary(world.session, actor=world.member, board_id="PH")
        assert got is not None
        assert len(got.milestones) == 2
        ship = next(m for m in got.milestones if m.title == "Ship")
        assert ship.due_date == date(2026, 9, 1)
        assert ship.target == "GA"
        assert ship.status == "planned"

    @pytest.mark.asyncio
    async def test_delete_removes(self, world: World) -> None:
        await upsert_summary(
            world.session, actor=world.pm, board_id="PH", data=_upsert(purpose="x")
        )
        await delete_summary(world.session, actor=world.pm, board_id="PH")
        assert await get_summary(world.session, actor=world.member, board_id="PH") is None

    @pytest.mark.asyncio
    async def test_delete_absent_404(self, world: World) -> None:
        with pytest.raises(NotFound):
            await delete_summary(world.session, actor=world.pm, board_id="PH")


# ===========================================================================
# AC3/AC5 — auth ladders (read = membership; write = board.summary.write)
# ===========================================================================


class TestAuthGate:
    @pytest.mark.asyncio
    async def test_get_unknown_board_404(self, world: World) -> None:
        with pytest.raises(NotFound):
            await get_summary(world.session, actor=world.admin, board_id="NOPE")

    @pytest.mark.asyncio
    async def test_get_non_member_403(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await get_summary(world.session, actor=world.outsider, board_id="PH")

    @pytest.mark.asyncio
    async def test_upsert_non_member_403(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await upsert_summary(
                world.session, actor=world.outsider, board_id="PH", data=_upsert(purpose="x")
            )

    @pytest.mark.asyncio
    async def test_upsert_read_only_role_403(self, world: World) -> None:
        """AC5: a board MEMBER holding a read-only role (backend_dev) still cannot write —
        the gate is the cap, not mere membership."""
        with pytest.raises(PermissionDenied):
            await upsert_summary(
                world.session, actor=world.member, board_id="PH", data=_upsert(purpose="x")
            )
        # ...and nothing was written (no partial state).
        assert await get_summary(world.session, actor=world.member, board_id="PH") is None

    @pytest.mark.asyncio
    async def test_upsert_write_roles_ok(self, world: World) -> None:
        """AC5: pm, admin, orchestrator ALL carry board.summary.write."""
        for writer in (world.pm, world.admin, world.orchestrator):
            res = await upsert_summary(
                world.session, actor=writer, board_id="PH",
                data=_upsert(purpose=f"by {writer.display_name}"),
            )
            assert res.purpose == f"by {writer.display_name}"

    @pytest.mark.asyncio
    async def test_upsert_unknown_board_404(self, world: World) -> None:
        with pytest.raises(NotFound):
            await upsert_summary(
                world.session, actor=world.pm, board_id="NOPE", data=_upsert(purpose="x")
            )

    @pytest.mark.asyncio
    async def test_delete_read_only_role_403(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await delete_summary(world.session, actor=world.member, board_id="PH")


# ===========================================================================
# AC4/AC5 — MCP get_board_summary (read) + set_board_summary (write)
# ===========================================================================


class TestMcpTools:
    @pytest.mark.asyncio
    async def test_get_board_summary_null_when_absent(self, world: World) -> None:
        """AC4: a member PULLs a board with no summary -> null (not an error)."""
        res = await _dispatch_tool(
            "get_board_summary", {"board": "PH"}, world.member, world.session
        )
        assert res is None

    @pytest.mark.asyncio
    async def test_get_board_summary_returns_summary(self, world: World) -> None:
        await upsert_summary(
            world.session, actor=world.pm, board_id="PH",
            data=_upsert(
                purpose="pulled", milestones=[{"title": "A", "status": "active", "order": 0}]
            ),
        )
        res = await _dispatch_tool(
            "get_board_summary", {"board": "PH"}, world.member, world.session
        )
        assert res is not None
        assert res["purpose"] == "pulled"
        assert res["milestones"][0]["title"] == "A"
        assert res["updated_by_name"] == "jarwis-pm@alice"
        assert set(res.keys()) == {
            "board_id", "purpose", "status", "progress", "highlights",
            "milestones", "updated_by", "updated_by_name", "created_at", "updated_at",
        }

    @pytest.mark.asyncio
    async def test_get_board_summary_non_member_denied(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await _dispatch_tool(
                "get_board_summary", {"board": "PH"}, world.outsider, world.session
            )

    @pytest.mark.asyncio
    async def test_get_board_summary_unknown_board_not_found(self, world: World) -> None:
        with pytest.raises(NotFound):
            await _dispatch_tool("get_board_summary", {"board": "NOPE"}, world.admin, world.session)

    @pytest.mark.asyncio
    async def test_set_board_summary_write_then_read_consistent(self, world: World) -> None:
        """AC5: pm writes via MCP; a subsequent get (MCP + service) returns it (English
        status planned|active|done preserved)."""
        payload = {
            "board": "PH",
            "purpose": "agent kanban",
            "status": "green",
            "progress": "80%",
            "highlights": "PH-338 shipped",
            "milestones": [
                {"title": "MVP", "status": "done", "order": 0},
                {"title": "GA", "status": "planned", "order": 1, "due_date": "2026-09-01"},
            ],
        }
        written = await _dispatch_tool("set_board_summary", payload, world.pm, world.session)
        assert written["purpose"] == "agent kanban"
        assert [m["status"] for m in written["milestones"]] == ["done", "planned"]

        read_back = await _dispatch_tool(
            "get_board_summary", {"board": "PH"}, world.member, world.session
        )
        assert read_back["highlights"] == "PH-338 shipped"
        assert read_back["milestones"][1]["due_date"] == "2026-09-01"

    @pytest.mark.asyncio
    async def test_set_board_summary_non_member_denied(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await _dispatch_tool(
                "set_board_summary", {"board": "PH", "purpose": "x"}, world.outsider, world.session
            )

    @pytest.mark.asyncio
    async def test_set_board_summary_read_only_role_denied(self, world: World) -> None:
        """AC5: a member with a read-only role (backend_dev) cannot write via MCP."""
        with pytest.raises(PermissionDenied):
            await _dispatch_tool(
                "set_board_summary", {"board": "PH", "purpose": "x"}, world.member, world.session
            )


# ===========================================================================
# AC3 — REST router (dedicated api/board_summary.py) + auth + validation
# ===========================================================================


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


class TestRest:
    def test_get_absent_returns_200_null(self, world: World) -> None:
        """AC3: no summary yet -> 200 + null (distinct from the unknown-board 404)."""
        client = _make_client(world.member, world.session)
        try:
            resp = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert resp.status_code == 200
        assert resp.json() is None

    def test_put_creates_200_then_get(self, world: World) -> None:
        client = _make_client(world.pm, world.session)
        try:
            put = client.put(
                "/api/boards/PH/summary",
                json={
                    "purpose": "from REST",
                    "milestones": [{"title": "M1", "status": "active", "order": 0}],
                },
            )
            got = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["purpose"] == "from REST"
        assert body["updated_by"] == str(world.pm.id)
        assert body["updated_by_name"] == "jarwis-pm@alice"
        assert body["milestones"][0]["title"] == "M1"
        assert got.status_code == 200
        assert got.json()["purpose"] == "from REST"

    def test_put_updates_singleton(self, world: World) -> None:
        client = _make_client(world.pm, world.session)
        try:
            client.put("/api/boards/PH/summary", json={"purpose": "v1"})
            second = client.put("/api/boards/PH/summary", json={"purpose": "v2"})
            got = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert second.status_code == 200
        assert got.json()["purpose"] == "v2"

    def test_delete_204_then_null(self, world: World) -> None:
        client = _make_client(world.pm, world.session)
        try:
            client.put("/api/boards/PH/summary", json={"purpose": "todelete"})
            deleted = client.delete("/api/boards/PH/summary")
            got = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert deleted.status_code == 204
        assert got.json() is None

    def test_delete_absent_404(self, world: World) -> None:
        client = _make_client(world.pm, world.session)
        try:
            resp = client.delete("/api/boards/PH/summary")
        finally:
            _clear()
        assert resp.status_code == 404

    def test_get_unknown_board_404(self, world: World) -> None:
        client = _make_client(world.admin, world.session)
        try:
            resp = client.get("/api/boards/NOPE/summary")
        finally:
            _clear()
        assert resp.status_code == 404

    def test_get_non_member_403(self, world: World) -> None:
        client = _make_client(world.outsider, world.session)
        try:
            resp = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_put_non_member_403(self, world: World) -> None:
        client = _make_client(world.outsider, world.session)
        try:
            resp = client.put("/api/boards/PH/summary", json={"purpose": "x"})
        finally:
            _clear()
        assert resp.status_code == 403

    def test_put_read_only_role_403(self, world: World) -> None:
        """AC5: a board member with a read-only role (backend_dev) -> 403 on write."""
        client = _make_client(world.member, world.session)
        try:
            resp = client.put("/api/boards/PH/summary", json={"purpose": "x"})
            got = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert resp.status_code == 403
        assert got.json() is None  # no partial write

    def test_invalid_milestone_status_422_no_partial_write(self, world: World) -> None:
        """AC3: an invalid milestone status -> 422 at parse, NO row written."""
        client = _make_client(world.pm, world.session)
        try:
            resp = client.put(
                "/api/boards/PH/summary",
                json={"milestones": [{"title": "X", "status": "bogus", "order": 0}]},
            )
            got = client.get("/api/boards/PH/summary")
        finally:
            _clear()
        assert resp.status_code == 422
        assert got.json() is None

    def test_blank_milestone_title_422(self, world: World) -> None:
        client = _make_client(world.pm, world.session)
        try:
            resp = client.put(
                "/api/boards/PH/summary",
                json={"milestones": [{"title": "", "status": "active", "order": 0}]},
            )
        finally:
            _clear()
        assert resp.status_code == 422

    def test_negative_milestone_order_422(self, world: World) -> None:
        client = _make_client(world.pm, world.session)
        try:
            resp = client.put(
                "/api/boards/PH/summary",
                json={"milestones": [{"title": "X", "status": "active", "order": -1}]},
            )
        finally:
            _clear()
        assert resp.status_code == 422
