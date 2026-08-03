"""PH-336 — board-scoped notes / guardrails store (DB + REST + MCP).

Proves the additive ``board_notes`` migration, the ``services.board_notes`` CRUD +
404-then-403 membership gate, the dedicated ``api/board_notes.py`` router, and the
read-only MCP ``get_board_notes`` tool across:
  - AC1: additive ``board_notes`` table (single head ``ph330agentowner``); EXACT
         columns incl. ``created_at``/``updated_at``, NO severity/tag; ``board_id`` FK
         ``ondelete=CASCADE``; plain up/down round-trip; ORM↔migration parity.
  - AC2: CRUD — newest-first list (body+author+ts); create persists w/ ``created_by``
         =caller + returns 201-shape; blank/whitespace body -> 422 (no partial row);
         cross-board delete -> 404 (never a cross-board delete); unknown board -> 404
         FIRST, non-member -> 403 SECOND.
  - AC3: MCP ``get_board_notes`` read-only; membership-gated (non-member -> Permission
         Denied, unknown board -> NotFound); NO mutation.
  - AC5: net-new store — a created note round-trips through the DB (no CLAUDE.md I/O).

Model mirrors ``test_epic_progress.py`` / ``test_board_create_rest.py``: an in-memory
SQLite DB, actors with eager-loaded memberships, REST via FastAPI dependency overrides.
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

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

from app.api.deps import current_actor
from app.core.exceptions import NotFound, PermissionDenied
from app.db.base import Base
from app.db.models import Actor, Board, BoardMembership, BoardNote, Workflow
from app.db.session import get_db_session
from app.main import app
from app.mcp.server import _dispatch_tool
from app.services.board_notes import create_note, delete_note, list_notes
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

_MIGRATION_FILE = "app/db/migrations/versions/20260803_0019_ph_336_board_notes.py"


def _load_migration():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("ph336_mig", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# AC1 — additive migration (single head), exact schema, up/down round-trip
# ===========================================================================


class TestMigration:
    def test_revises_verified_head(self) -> None:
        """AC1: down_revision is the single verified Alembic head ``ph330agentowner``."""
        mig = _load_migration()
        assert mig.revision == "ph336boardnotes"
        assert mig.down_revision == "ph330agentowner"

    def test_up_down_round_trips_with_exact_schema(self) -> None:
        """AC1: plain CREATE TABLE with EXACTLY the agreed columns (NO severity/tag),
        ``board_id`` FK ``ondelete=CASCADE``, and a clean plain drop_table downgrade."""
        mig = _load_migration()
        eng = sa.create_engine("sqlite://")
        with eng.connect() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
            # Pre-336 schema slice the migration's FKs reference.
            conn.execute(sa.text("CREATE TABLE actors (id TEXT PRIMARY KEY, display_name TEXT)"))
            conn.execute(sa.text("CREATE TABLE boards (id TEXT PRIMARY KEY, key TEXT)"))
            conn.commit()

            ctx = MigrationContext.configure(conn)
            assert type(ctx.impl).__name__ == "SQLiteImpl"
            with Operations.context(ctx):
                mig.upgrade()
            conn.commit()

            insp = sa.inspect(conn)
            assert "board_notes" in insp.get_table_names()
            cols = {c["name"] for c in insp.get_columns("board_notes")}
            assert cols == {"id", "board_id", "body", "created_by", "created_at", "updated_at"}
            # The round-1 taxonomy cut is load-bearing — assert it never crept back.
            assert "severity" not in cols
            assert "tag" not in cols

            # board_id FK CASCADEs (integrity insurance); created_by does NOT.
            fks = {
                tuple(fk["constrained_columns"]): fk
                for fk in insp.get_foreign_keys("board_notes")
            }
            assert fks[("board_id",)]["referred_table"] == "boards"
            assert fks[("board_id",)]["options"].get("ondelete") == "CASCADE"
            assert fks[("created_by",)]["referred_table"] == "actors"
            assert not fks[("created_by",)]["options"].get("ondelete")

            # created_by is nullable (a deleted/absent author never blocks a note).
            by_name = {c["name"]: c for c in insp.get_columns("board_notes")}
            assert by_name["created_by"]["nullable"] is True
            assert by_name["body"]["nullable"] is False

            with Operations.context(ctx):
                mig.downgrade()
            conn.commit()
            assert "board_notes" not in sa.inspect(conn).get_table_names()

    def test_create_all_carries_board_notes(self) -> None:
        """AC1: the ORM test schema (Base.metadata.create_all) materializes the table
        with a CASCADE board_id FK — 1:1 parity with the migration."""
        eng = sa.create_engine("sqlite://")
        Base.metadata.create_all(eng)
        insp = sa.inspect(eng)
        assert "board_notes" in insp.get_table_names()
        board_fk = next(
            fk for fk in insp.get_foreign_keys("board_notes")
            if fk["constrained_columns"] == ["board_id"]
        )
        assert board_fk["options"].get("ondelete") == "CASCADE"


# ===========================================================================
# In-memory world (service / MCP async tests + REST sync tests share it)
# ===========================================================================


@dataclass
class World:
    session: AsyncSession
    ph: Board  # board under test
    zz: Board  # a SECOND board (cross-board delete guard)
    admin: Actor  # member of PH
    member: Actor  # member of PH (agent — proves agents PULL)
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

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    member = Actor(
        kind="agent", display_name="jarwis-backend@alice", token_hash="y", is_active=True
    )
    outsider = Actor(kind="agent", display_name="jarwis-qa@nobody", token_hash="z", is_active=True)
    session.add_all([admin, member, outsider])
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
            BoardMembership(board_id=ph.id, actor_id=member.id, role="backend_dev"),
        ]
    )
    await session.commit()

    return World(
        session=session,
        ph=ph,
        zz=zz,
        admin=await _reload_actor(session, admin),
        member=await _reload_actor(session, member),
        outsider=await _reload_actor(session, outsider),
    )


# ===========================================================================
# AC2 — service CRUD correctness
# ===========================================================================


class TestServiceCrud:
    @pytest.mark.asyncio
    async def test_create_strips_persists_and_resolves_author(self, world: World) -> None:
        """AC2/AC5: create trims the body, stamps ``created_by``=caller, resolves the
        author name, and the row round-trips through the DB."""
        created = await create_note(
            world.session, actor=world.admin, board_id="PH", body="  mind the lock_timeout  "
        )
        assert created.body == "mind the lock_timeout"  # stripped
        assert created.created_by == world.admin.id
        assert created.created_by_name == "Admin"
        assert created.board_id == world.ph.id

        listed = await list_notes(world.session, actor=world.admin, board_id="PH")
        assert [n.body for n in listed.notes] == ["mind the lock_timeout"]
        assert listed.notes[0].created_by_name == "Admin"

    @pytest.mark.asyncio
    async def test_list_newest_first(self, world: World) -> None:
        """AC2: GET orders newest-first (created_at DESC). Timestamps are set explicitly
        so the order is deterministic regardless of insert tick."""
        world.session.add_all(
            [
                BoardNote(
                    board_id=world.ph.id, body="older", created_by=world.admin.id,
                    created_at=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                BoardNote(
                    board_id=world.ph.id, body="newer", created_by=world.admin.id,
                    created_at=datetime(2024, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        await world.session.commit()
        listed = await list_notes(world.session, actor=world.admin, board_id="PH")
        assert [n.body for n in listed.notes] == ["newer", "older"]

    @pytest.mark.asyncio
    async def test_list_is_board_scoped(self, world: World) -> None:
        """AC2: a note on another board never leaks into this board's list."""
        world.session.add(
            BoardNote(board_id=world.zz.id, body="zeta-only", created_by=world.admin.id)
        )
        await world.session.commit()
        listed = await list_notes(world.session, actor=world.admin, board_id="PH")
        assert listed.notes == []

    @pytest.mark.asyncio
    async def test_blank_body_rejected_no_row(self, world: World) -> None:
        """AC2 (UC E1): empty/whitespace body raises before any row is written."""
        for bad in ("", "   ", "\n\t "):
            with pytest.raises(ValueError):
                await create_note(world.session, actor=world.admin, board_id="PH", body=bad)
        listed = await list_notes(world.session, actor=world.admin, board_id="PH")
        assert listed.notes == []

    @pytest.mark.asyncio
    async def test_delete_removes(self, world: World) -> None:
        created = await create_note(world.session, actor=world.admin, board_id="PH", body="x")
        await delete_note(
            world.session, actor=world.admin, board_id="PH", note_id=created.id
        )
        listed = await list_notes(world.session, actor=world.admin, board_id="PH")
        assert listed.notes == []

    @pytest.mark.asyncio
    async def test_delete_unknown_note_404(self, world: World) -> None:
        with pytest.raises(NotFound):
            await delete_note(
                world.session, actor=world.admin, board_id="PH", note_id=uuid4()
            )

    @pytest.mark.asyncio
    async def test_delete_cross_board_404(self, world: World) -> None:
        """AC2: deleting a ZZ note via the PH path -> 404 (never a cross-board delete),
        and the ZZ note is untouched."""
        zz_note = BoardNote(board_id=world.zz.id, body="zeta", created_by=world.admin.id)
        world.session.add(zz_note)
        await world.session.commit()
        with pytest.raises(NotFound):
            await delete_note(
                world.session, actor=world.admin, board_id="PH", note_id=zz_note.id
            )
        still_there = (
            await world.session.execute(select(BoardNote).where(BoardNote.id == zz_note.id))
        ).scalar_one_or_none()
        assert still_there is not None


# ===========================================================================
# AC2 — auth gate at the service seam (unknown -> 404 FIRST, non-member -> 403)
# ===========================================================================


class TestAuthGate:
    @pytest.mark.asyncio
    async def test_unknown_board_404(self, world: World) -> None:
        with pytest.raises(NotFound):
            await list_notes(world.session, actor=world.admin, board_id="NOPE")

    @pytest.mark.asyncio
    async def test_list_non_member_403(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await list_notes(world.session, actor=world.outsider, board_id="PH")

    @pytest.mark.asyncio
    async def test_create_non_member_403(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await create_note(world.session, actor=world.outsider, board_id="PH", body="x")

    @pytest.mark.asyncio
    async def test_delete_non_member_403(self, world: World) -> None:
        created = await create_note(world.session, actor=world.admin, board_id="PH", body="x")
        with pytest.raises(PermissionDenied):
            await delete_note(
                world.session, actor=world.outsider, board_id="PH", note_id=created.id
            )


# ===========================================================================
# AC3 — MCP get_board_notes: read-only, membership-gated
# ===========================================================================


class TestMcpReadTool:
    @pytest.mark.asyncio
    async def test_get_board_notes_returns_notes(self, world: World) -> None:
        """AC3: a board member (agent) PULLs the notes read-only via the MCP tool."""
        await create_note(world.session, actor=world.admin, board_id="PH", body="guardrail A")
        res = await _dispatch_tool("get_board_notes", {"board": "PH"}, world.member, world.session)
        assert [n["body"] for n in res["notes"]] == ["guardrail A"]
        note = res["notes"][0]
        assert note["created_by_name"] == "Admin"
        assert {"id", "board_id", "body", "created_by", "created_by_name", "created_at"} == set(
            note.keys()
        )

    @pytest.mark.asyncio
    async def test_get_board_notes_non_member_denied(self, world: World) -> None:
        with pytest.raises(PermissionDenied):
            await _dispatch_tool("get_board_notes", {"board": "PH"}, world.outsider, world.session)

    @pytest.mark.asyncio
    async def test_get_board_notes_unknown_board_not_found(self, world: World) -> None:
        with pytest.raises(NotFound):
            await _dispatch_tool("get_board_notes", {"board": "NOPE"}, world.admin, world.session)


# ===========================================================================
# AC2/AC4 — REST router (dedicated api/board_notes.py) + board-scope auth
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
    def test_create_201_then_list(self, world: World) -> None:
        """AC2/AC4: POST returns a 201 BoardNote (body stripped, author resolved) and a
        subsequent GET lists it. Strict newest-first ordering is proven deterministically
        at the service seam (needs backdated timestamps the API cannot set)."""
        client = _make_client(world.admin, world.session)
        try:
            created = client.post("/api/boards/PH/notes", json={"body": "  from REST  "})
            listed = client.get("/api/boards/PH/notes")
        finally:
            _clear()
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["body"] == "from REST"  # stripped
        assert body["created_by"] == str(world.admin.id)
        assert body["created_by_name"] == "Admin"
        assert listed.status_code == 200
        assert [n["body"] for n in listed.json()["notes"]] == ["from REST"]

    def test_get_empty_list_member(self, world: World) -> None:
        client = _make_client(world.member, world.session)
        try:
            resp = client.get("/api/boards/PH/notes")
        finally:
            _clear()
        assert resp.status_code == 200
        assert resp.json() == {"notes": []}

    def test_delete_204_then_gone(self, world: World) -> None:
        client = _make_client(world.admin, world.session)
        try:
            created = client.post("/api/boards/PH/notes", json={"body": "todelete"})
            note_id = created.json()["id"]
            deleted = client.delete(f"/api/boards/PH/notes/{note_id}")
            listed = client.get("/api/boards/PH/notes")
        finally:
            _clear()
        assert created.status_code == 201
        assert deleted.status_code == 204
        assert listed.json()["notes"] == []

    def test_empty_body_422_no_row(self, world: World) -> None:
        """AC2 (UC E1): an empty body -> 422 at request parse, no partial row."""
        client = _make_client(world.admin, world.session)
        try:
            resp = client.post("/api/boards/PH/notes", json={"body": ""})
            listed = client.get("/api/boards/PH/notes")
        finally:
            _clear()
        assert resp.status_code == 422
        assert listed.json()["notes"] == []

    def test_whitespace_body_422(self, world: World) -> None:
        """AC2 (UC E1): a whitespace-only body -> 422 (validator strips then rejects)."""
        client = _make_client(world.admin, world.session)
        try:
            resp = client.post("/api/boards/PH/notes", json={"body": "   "})
        finally:
            _clear()
        assert resp.status_code == 422

    def test_get_non_member_403(self, world: World) -> None:
        client = _make_client(world.outsider, world.session)
        try:
            resp = client.get("/api/boards/PH/notes")
        finally:
            _clear()
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"

    def test_post_non_member_403(self, world: World) -> None:
        client = _make_client(world.outsider, world.session)
        try:
            resp = client.post("/api/boards/PH/notes", json={"body": "x"})
        finally:
            _clear()
        assert resp.status_code == 403

    def test_get_unknown_board_404(self, world: World) -> None:
        client = _make_client(world.admin, world.session)
        try:
            resp = client.get("/api/boards/NOPE/notes")
        finally:
            _clear()
        assert resp.status_code == 404

    def test_delete_absent_note_404(self, world: World) -> None:
        """AC2: deleting a note that does not exist on the board -> 404. The dedicated
        cross-board (a real ZZ row targeted via the PH path) 404 is proven at the service
        seam (``TestServiceCrud.test_delete_cross_board_404``)."""
        client = _make_client(world.admin, world.session)
        try:
            resp = client.delete(f"/api/boards/PH/notes/{uuid4()}")
        finally:
            _clear()
        assert resp.status_code == 404
