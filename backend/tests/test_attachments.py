"""PH-296: ticket evidence attachment service + content-serving tests.

Service-layer tests exercise the RBAC gate, checksum/size accounting, the
size/type rejections (413/415) with partial cleanup, the zero-copy ingest
traversal guard, persistence + audit history, and run_id provenance. One
TestClient test drives the byte-serving route to prove Range (206) support and
the security headers — auth is injected via ``dependency_overrides`` because the
seed tokens are fakes (``token_hash="x"`` never verifies).

PH-311 adds the ``phase`` tag coverage: valid/free-slug persist (REST + MCP
ingest paths), NULL passthrough back-compat, invalid-slug rejection with no
side effect (blob/row/event), response-shape, the MCP input-schema param, and
the migration round-trip (add nullable column → single head → clean drop).
"""

import hashlib
import importlib.util
import io
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AttachmentPhaseInvalid,
    AttachmentSourceInvalid,
    PayloadTooLarge,
    PermissionDenied,
    UnsupportedMediaType,
)
from app.db.models import Actor, Attachment, Board, BoardMembership, Ticket, TicketHistory
from app.main import app
from app.mcp.server import AddAttachmentInput
from app.services.attachments import (
    create_attachment,
    get_attachment,
    ingest_from_source_path,
    list_attachments,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _add_actor(
    session: AsyncSession, board: Board, role: str | None, name: str
) -> Actor:
    """Create an actor (optionally a board member with ``role``), memberships loaded."""
    actor = Actor(kind="agent", display_name=name, token_hash="x", is_active=True)
    session.add(actor)
    await session.flush()
    if role is not None:
        session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=role))
    await session.commit()
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


async def _add_ticket(
    session: AsyncSession, board: Board, reporter: Actor, key: str = "PH-1"
) -> Ticket:
    ticket = Ticket(
        key=key,
        board_id=board.id,
        type="task",
        title="Evidence ticket",
        description="",
        state="backlog",
        reporter_id=reporter.id,
        priority="medium",
        labels=[],
    )
    session.add(ticket)
    await session.commit()
    return ticket


def _stream(data: bytes):
    """A StreamFactory yielding a fresh in-memory reader over ``data``."""
    return lambda: io.BytesIO(data)


def _files_under(root) -> list:
    return [p for p in root.rglob("*") if p.is_file()]


@pytest.fixture
def attach_root(tmp_path, monkeypatch):
    """Point attachments_root at a tmp dir (the cached Settings singleton)."""
    settings = get_settings()
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(settings, "attachments_root", str(root))
    return root


# ---------------------------------------------------------------------------
# Persistence + checksum + history + run_id
# ---------------------------------------------------------------------------


async def test_create_persists_row_file_history_and_checksum(seed, db_session, attach_root):
    ticket = await _add_ticket(db_session, seed.board, seed.pm)
    data = b"device timeline evidence\n" * 32

    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="timeline.txt",
        content_type="text/plain",
        open_stream=_stream(data),
        kind="log",
        run_id="run-42",
    )

    # Metadata + checksum + provenance
    assert att.size_bytes == len(data)
    assert att.checksum_sha256 == hashlib.sha256(data).hexdigest()
    assert att.kind == "log"
    assert att.source == "human"
    assert att.run_id == "run-42"

    # Blob on disk under a UUID shard — client filename NEVER in the path
    blob = attach_root / att.storage_key
    assert blob.is_file()
    assert blob.read_bytes() == data
    assert att.storage_key == f"{str(att.id)[:2]}/{att.id}"
    assert "timeline" not in att.storage_key

    # Audit history row
    history = (
        await db_session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket.id,
                TicketHistory.event_type == "attachment_added",
            )
        )
    ).scalars().all()
    assert len(history) == 1
    assert history[0].new_value["attachment_id"] == str(att.id)
    assert history[0].new_value["filename"] == "timeline.txt"


async def test_content_type_is_normalized(seed, db_session, attach_root):
    await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="note.txt",
        content_type="Text/Plain; charset=utf-8",
        open_stream=_stream(b"hi"),
    )
    assert att.content_type == "text/plain"


# ---------------------------------------------------------------------------
# Rejections: 415 unsupported type, 413 oversize + partial cleanup
# ---------------------------------------------------------------------------


async def test_rejects_unsupported_media_type(seed, db_session, attach_root):
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(UnsupportedMediaType):
        await create_attachment(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="x.svg",
            content_type="image/svg+xml",
            open_stream=_stream(b"<svg/>"),
        )
    assert _files_under(attach_root) == []


async def test_rejects_oversize_and_cleans_partial(seed, db_session, attach_root, monkeypatch):
    await _add_ticket(db_session, seed.board, seed.pm)
    monkeypatch.setattr(get_settings(), "attachment_max_bytes", 16)

    with pytest.raises(PayloadTooLarge):
        await create_attachment(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="big.txt",
            content_type="text/plain",
            open_stream=_stream(b"x" * 64),
        )

    # Partial blob removed and no row committed
    assert _files_under(attach_root) == []
    count = (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_stranger_cannot_create_or_read(seed, db_session, attach_root):
    await _add_ticket(db_session, seed.board, seed.pm)
    stranger = await _add_actor(db_session, seed.board, None, "Stranger")

    with pytest.raises(PermissionDenied):
        await create_attachment(
            db_session,
            actor=stranger,
            ticket_id="PH-1",
            filename="e.txt",
            content_type="text/plain",
            open_stream=_stream(b"hi"),
        )

    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="e.txt",
        content_type="text/plain",
        open_stream=_stream(b"hi"),
    )
    with pytest.raises(PermissionDenied):
        await list_attachments(db_session, actor=stranger, ticket_id="PH-1")
    with pytest.raises(PermissionDenied):
        await get_attachment(db_session, actor=stranger, attachment_id=str(att.id))


async def test_role_without_cap_denied_create_but_can_list(seed, db_session, attach_root):
    await _add_ticket(db_session, seed.board, seed.pm)
    reviewer = await _add_actor(db_session, seed.board, "reviewer", "Reviewer")

    await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="e.txt",
        content_type="text/plain",
        open_stream=_stream(b"hi"),
    )

    # reviewer holds ticket.read (list OK) but not attachment.add (create denied)
    listed = await list_attachments(db_session, actor=reviewer, ticket_id="PH-1")
    assert len(listed) == 1
    with pytest.raises(PermissionDenied):
        await create_attachment(
            db_session,
            actor=reviewer,
            ticket_id="PH-1",
            filename="r.txt",
            content_type="text/plain",
            open_stream=_stream(b"x"),
        )


async def test_backend_and_qa_can_create(seed, db_session, attach_root):
    await _add_ticket(db_session, seed.board, seed.pm)
    qa = await _add_actor(db_session, seed.board, "qa", "QA")

    await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="b.txt",
        content_type="text/plain",
        open_stream=_stream(b"backend"),
    )
    await create_attachment(
        db_session,
        actor=qa,
        ticket_id="PH-1",
        filename="q.json",
        content_type="application/json",
        open_stream=_stream(b"{}"),
    )

    listed = await list_attachments(db_session, actor=qa, ticket_id="PH-1")
    assert len(listed) == 2


# ---------------------------------------------------------------------------
# Zero-copy ingest path traversal guard
# ---------------------------------------------------------------------------


async def test_ingest_rejects_traversal_and_outside_root(
    seed, db_session, attach_root, monkeypatch
):
    await _add_ticket(db_session, seed.board, seed.pm)
    settings = get_settings()
    monkeypatch.setattr(settings, "host_home", "/Users/huseyinkanat")
    monkeypatch.setattr(settings, "repos_root", "/repos")

    bad_paths = (
        "/Users/huseyinkanat/../etc/passwd",  # '..' traversal
        "/etc/passwd",  # absolute, outside the mounted host home
        "relative/not/absolute",  # not absolute
    )
    for bad in bad_paths:
        with pytest.raises(AttachmentSourceInvalid):
            await ingest_from_source_path(
                db_session, actor=seed.backend, ticket_id="PH-1", source_path=bad
            )
    # nothing landed on disk
    assert _files_under(attach_root) == []


# ---------------------------------------------------------------------------
# Content serving: Range (206) + security headers
# ---------------------------------------------------------------------------


async def test_content_route_supports_range_and_headers(seed, db_session, attach_root):
    await _add_ticket(db_session, seed.board, seed.pm)
    data = b"A" * 500
    att = await create_attachment(
        db_session,
        actor=seed.admin,
        ticket_id="PH-1",
        filename="clip.txt",
        content_type="text/plain",
        open_stream=_stream(data),
    )
    admin = (
        await db_session.execute(
            select(Actor).where(Actor.id == seed.admin.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()

    from app.api.attachments import _actor_for_content
    from app.db.session import get_db_session

    async def _fake_session():
        yield db_session

    async def _fake_actor():
        return admin

    app.dependency_overrides[get_db_session] = _fake_session
    app.dependency_overrides[_actor_for_content] = _fake_actor
    try:
        client = TestClient(app, raise_server_exceptions=True)
        url = f"/api/tickets/PH-1/attachments/{att.id}/content"

        full = client.get(url)
        assert full.status_code == 200, full.text
        assert full.headers.get("accept-ranges") == "bytes"
        assert full.headers.get("x-content-type-options") == "nosniff"
        assert full.content == data

        ranged = client.get(url, headers={"Range": "bytes=0-99"})
        assert ranged.status_code == 206, ranged.text
        assert ranged.headers.get("content-range") == "bytes 0-99/500"
        assert len(ranged.content) == 100
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# PH-309: text/markdown allowlist (.md spec-doc uploads)
# ---------------------------------------------------------------------------


async def test_accepts_and_persists_text_markdown(seed, db_session, attach_root):
    """text/markdown is accepted, param-stripped/lower-cased, and persisted."""
    await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="spec.md",
        content_type="Text/Markdown; charset=utf-8",
        open_stream=_stream(b"# Spec\n\nbody\n"),
    )
    # normalized to the canonical allowlist member
    assert att.content_type == "text/markdown"
    # blob committed under the UUID shard (persistence proof)
    blob = attach_root / att.storage_key
    assert blob.is_file()


def test_text_markdown_in_allowed_types_set():
    """The default allowlist admits text/markdown without dropping prior members."""
    allowed = get_settings().attachment_allowed_types_set
    assert "text/markdown" in allowed
    # regression: previously-allowed types remain members
    assert {
        "image/png",
        "image/jpeg",
        "video/mp4",
        "text/plain",
        "application/json",
    } <= allowed


@pytest.mark.parametrize("bad_type", ["application/zip", "image/svg+xml", "text/html"])
async def test_rejects_types_outside_allowlist(seed, db_session, attach_root, bad_type):
    """Non-allowlisted types (zip + the svg/html regression) are rejected, no blob."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(UnsupportedMediaType):
        await create_attachment(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="x.bin",
            content_type=bad_type,
            open_stream=_stream(b"data"),
        )
    assert _files_under(attach_root) == []


# ---------------------------------------------------------------------------
# PH-311: phase tag — persist (REST + MCP), NULL back-compat, invalid rejection,
# response shape, MCP input schema, migration round-trip.
# ---------------------------------------------------------------------------


async def test_create_persists_valid_phase_and_response_shape(seed, db_session, attach_root):
    """AC2/AC6: a convention phase persists on the row AND serializes in the response."""
    from app.services.serializers import attachment_response

    await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="t.txt",
        content_type="text/plain",
        open_stream=_stream(b"evidence"),
        phase="iter-2-fail",
    )
    assert att.phase == "iter-2-fail"
    assert attachment_response(att).phase == "iter-2-fail"


async def test_create_accepts_free_slug_phase(seed, db_session, attach_root):
    """AC6: a valid-but-off-convention slug is accepted verbatim (shape-only gate)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="t.txt",
        content_type="text/plain",
        open_stream=_stream(b"x"),
        phase="smoke-check",
    )
    assert att.phase == "smoke-check"


async def test_phase_defaults_to_null(seed, db_session, attach_root):
    """AC4: phase omitted → row.phase IS NULL and the response serializes phase=None."""
    from app.services.serializers import attachment_response

    await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="t.txt",
        content_type="text/plain",
        open_stream=_stream(b"x"),
    )
    assert att.phase is None
    assert attachment_response(att).phase is None


async def test_ingest_persists_phase(seed, db_session, attach_root, tmp_path, monkeypatch):
    """AC3: the MCP zero-copy ingest path threads phase → row + list_attachments echo it."""
    await _add_ticket(db_session, seed.board, seed.pm)
    settings = get_settings()
    monkeypatch.setattr(settings, "host_home", str(tmp_path))
    monkeypatch.setattr(settings, "repos_root", str(tmp_path))
    src = tmp_path / "evidence.txt"
    src.write_bytes(b"iteration pass evidence\n")

    att = await ingest_from_source_path(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        source_path=str(src),
        phase="iter-2-pass",
    )
    assert att.phase == "iter-2-pass"
    listed = await list_attachments(db_session, actor=seed.backend, ticket_id="PH-1")
    assert [a.phase for a in listed] == ["iter-2-pass"]


@pytest.mark.parametrize(
    "bad_phase",
    [
        "Iter 2!",      # uppercase + space + punctuation (AC5 canonical example)
        "UPPER",        # uppercase
        "has space",    # whitespace
        "-lead",        # leading hyphen
        "trail-",       # trailing hyphen
        "double--hyphen",  # empty group
        "..",           # traversal-looking
        "with.dot",     # dot
        "under_score",  # underscore
        "repro\n",      # trailing newline (fullmatch closes the bare-$ loophole)
        "a" * 41,       # exceeds the 40-char cap
    ],
)
async def test_rejects_invalid_phase_no_side_effect(seed, db_session, attach_root, bad_phase):
    """AC5: an invalid phase raises 422 BEFORE any blob/row/event is created."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(AttachmentPhaseInvalid):
        await create_attachment(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="e.txt",
            content_type="text/plain",
            open_stream=_stream(b"hi"),
            phase=bad_phase,
        )
    # No blob on disk, no row committed, no attachment_added audit event.
    assert _files_under(attach_root) == []
    rows = (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()
    assert rows == 0
    events = (
        await db_session.execute(
            select(func.count())
            .select_from(TicketHistory)
            .where(TicketHistory.event_type == "attachment_added")
        )
    ).scalar_one()
    assert events == 0


def test_phase_slug_boundaries_accepted():
    """A bare 40-char slug and single-token/leading-digit slugs pass the shape gate."""
    from app.services.attachments import _validate_phase

    assert _validate_phase(None) is None
    assert _validate_phase("a" * 40) == "a" * 40  # exactly at the cap
    assert _validate_phase("repro") == "repro"
    assert _validate_phase("iter-10-pass") == "iter-10-pass"
    assert _validate_phase("v2") == "v2"


def test_mcp_add_attachment_input_accepts_phase():
    """The MCP add_attachment input schema carries an optional phase (default None)."""
    parsed = AddAttachmentInput.model_validate(
        {"id": "PH-1", "source_path": "/x/y.png", "phase": "iter-2-pass"}
    )
    assert parsed.phase == "iter-2-pass"
    # Omitted → None keeps PH-296/297 MCP callers working unchanged.
    assert AddAttachmentInput.model_validate({"id": "PH-1", "source_path": "/x"}).phase is None


async def test_rest_endpoint_persists_phase(seed, db_session, attach_root):
    """AC2 at the HTTP boundary: multipart phase Form field → 201 + response.phase + row.phase."""
    from app.api.deps import current_actor
    from app.db.session import get_db_session

    await _add_ticket(db_session, seed.board, seed.pm)

    async def _fake_session():
        yield db_session

    async def _fake_actor():
        return seed.backend

    app.dependency_overrides[get_db_session] = _fake_session
    app.dependency_overrides[current_actor] = _fake_actor
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/tickets/PH-1/attachments",
            files={"file": ("t.txt", b"evidence", "text/plain")},
            data={"phase": "repro"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["phase"] == "repro"
    finally:
        app.dependency_overrides.clear()

    row = (await db_session.execute(select(Attachment))).scalar_one()
    assert row.phase == "repro"


async def test_rest_endpoint_rejects_invalid_phase_422(seed, db_session, attach_root):
    """The new AttachmentPhaseInvalid surfaces as an automatic 422 with the rejected value."""
    from app.api.deps import current_actor
    from app.db.session import get_db_session

    await _add_ticket(db_session, seed.board, seed.pm)

    async def _fake_session():
        yield db_session

    async def _fake_actor():
        return seed.backend

    app.dependency_overrides[get_db_session] = _fake_session
    app.dependency_overrides[current_actor] = _fake_actor
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/api/tickets/PH-1/attachments",
            files={"file": ("t.txt", b"evidence", "text/plain")},
            data={"phase": "Bad Phase"},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error"] == "attachment_phase_invalid"
        assert body["phase"] == "Bad Phase"
    finally:
        app.dependency_overrides.clear()

    # Nothing persisted on the rejected request.
    assert _files_under(attach_root) == []
    rows = (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()
    assert rows == 0


def _load_phase_migration():
    """Import the PH-311 migration module by file path (versions/ isn't a package)."""
    path = (
        Path(__file__).resolve().parents[1]
        / "app" / "db" / "migrations" / "versions"
        / "20260716_0014_ph_311_attachment_phase.py"
    )
    spec = importlib.util.spec_from_file_location("ph311_migration_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase_migration_round_trip_and_single_head(tmp_path):
    """AC1: migration adds nullable phase (existing rows NULL), drops it clean, single head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load_phase_migration()
    assert module.revision == "ph311attachmentphase"
    assert module.down_revision == "ph296attachments"

    # Chain integrity: exactly one alembic head, and it is this revision.
    migrations_dir = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"expected a single alembic head, got {heads}"
    assert "ph311attachmentphase" in heads

    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    original_op = module.op
    try:
        with engine.connect() as conn:
            # Pre-migration attachments table with an existing row (no phase column).
            conn.execute(
                text("CREATE TABLE attachments (id VARCHAR PRIMARY KEY, filename VARCHAR)")
            )
            conn.execute(text("INSERT INTO attachments (id, filename) VALUES ('a', 'f.txt')"))
            conn.commit()

            module.op = Operations(MigrationContext.configure(conn))
            module.upgrade()
            conn.commit()

            cols = {c["name"]: c for c in inspect(conn).get_columns("attachments")}
            assert "phase" in cols
            assert cols["phase"]["nullable"] is True
            # Pre-existing row is backfilled as NULL.
            existing = conn.execute(text("SELECT phase FROM attachments WHERE id='a'")).scalar_one()
            assert existing is None

            module.downgrade()
            conn.commit()
            assert "phase" not in {c["name"] for c in inspect(conn).get_columns("attachments")}
    finally:
        module.op = original_op
        engine.dispose()
