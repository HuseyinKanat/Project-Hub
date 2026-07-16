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

import base64
import hashlib
import importlib.util
import io
import json
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import (
    JSON,
    Column,
    MetaData,
    Table,
    Uuid,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AttachmentContentInvalid,
    AttachmentMetadataInvalid,
    AttachmentPhaseInvalid,
    AttachmentSourceInvalid,
    NotFound,
    PayloadTooLarge,
    PermissionDenied,
    UnsupportedMediaType,
)
from app.db.models import Actor, Attachment, Board, BoardMembership, Ticket, TicketHistory
from app.main import app
from app.mcp.server import (
    _TOOL_INPUT_MODELS,
    TOOLS,
    AddAttachmentContentInput,
    AddAttachmentInput,
    DeleteAttachmentInput,
    UpdateAttachmentInput,
)
from app.services.attachments import (
    create_attachment,
    delete_attachment,
    get_attachment,
    ingest_from_content,
    ingest_from_source_path,
    list_attachments,
    update_attachment,
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

    # Chain integrity: exactly one alembic head (no branching). PH-313 stacks
    # ph313attachmentcrud on top, so this migration is no longer the tip — assert
    # it remains a reachable revision inside the single-headed chain.
    migrations_dir = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single alembic head, got {heads}"
    assert script.get_revision("ph311attachmentphase") is not None

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


# ---------------------------------------------------------------------------
# PH-313: update_attachment (metadata-only) + delete_attachment (hard) +
# RBAC (update=impl+qa+pm, delete=qa+pm only) + REST/MCP wiring + migration.
# ---------------------------------------------------------------------------


async def _seed_attachment(db_session, seed, attach_root, **kw):
    """Convenience: ensure PH-1 exists and create one attachment (backend author)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    defaults = dict(
        actor=seed.backend,
        ticket_id="PH-1",
        filename="e.txt",
        content_type="text/plain",
        open_stream=_stream(b"evidence-bytes"),
    )
    defaults.update(kw)
    return await create_attachment(db_session, **defaults)


# --- update: metadata mutation + blob immutability --------------------------


async def test_update_changes_only_provided_field(seed, db_session, attach_root):
    """AC1: updating phase alone changes phase; kind/run_id are untouched; response reflects."""
    from app.services.serializers import attachment_response

    att = await _seed_attachment(db_session, seed, attach_root, kind="log", run_id="run-1")
    updated = await update_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        attachment_id=str(att.id),
        fields={"phase": "iter-3-pass"},
    )
    assert updated.phase == "iter-3-pass"
    assert updated.kind == "log"          # untouched
    assert updated.run_id == "run-1"      # untouched
    assert attachment_response(updated).phase == "iter-3-pass"


async def test_update_blob_columns_and_bytes_immutable(seed, db_session, attach_root):
    """AC2: an update never touches blob columns OR the on-disk bytes/checksum."""
    data = b"immutable evidence bytes\n" * 8
    att = await _seed_attachment(
        db_session, seed, attach_root, open_stream=_stream(data), kind="log", run_id="run-1"
    )
    before = (
        att.storage_key,
        att.filename,
        att.content_type,
        att.size_bytes,
        att.checksum_sha256,
    )
    blob = attach_root / att.storage_key
    bytes_before = blob.read_bytes()

    updated = await update_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        attachment_id=str(att.id),
        fields={"phase": "iter-2-pass", "kind": "screenshot", "run_id": "run-2"},
    )
    # metadata moved
    assert (updated.phase, updated.kind, updated.run_id) == ("iter-2-pass", "screenshot", "run-2")
    # blob columns byte-for-byte identical
    assert (
        updated.storage_key,
        updated.filename,
        updated.content_type,
        updated.size_bytes,
        updated.checksum_sha256,
    ) == before
    # on-disk bytes + checksum unchanged
    assert blob.read_bytes() == bytes_before
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == before[4]


async def test_update_null_clears_and_omitted_untouched(seed, db_session, attach_root):
    """AC11: phase=null CLEARS; an omitted key is untouched; run_id=null clears too."""
    att = await _seed_attachment(
        db_session, seed, attach_root, phase="repro", run_id="run-1", kind="log"
    )
    aid = str(att.id)

    # (1) explicit null clears phase; omitted run_id/kind survive
    u1 = await update_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=aid,
        fields={"phase": None},
    )
    assert u1.phase is None
    assert u1.run_id == "run-1"
    assert u1.kind == "log"

    # (2) update kind only — phase stays cleared, run_id untouched
    u2 = await update_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=aid,
        fields={"kind": "screenshot"},
    )
    assert u2.kind == "screenshot"
    assert u2.phase is None
    assert u2.run_id == "run-1"

    # (3) run_id=null clears it
    u3 = await update_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=aid,
        fields={"run_id": None},
    )
    assert u3.run_id is None
    assert u3.kind == "screenshot"


async def test_update_rejects_invalid_phase_slug(seed, db_session, attach_root):
    """AC3: an invalid phase slug on update → AttachmentPhaseInvalid, row unchanged."""
    att = await _seed_attachment(db_session, seed, attach_root, phase="repro")
    with pytest.raises(AttachmentPhaseInvalid):
        await update_attachment(
            db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=str(att.id),
            fields={"phase": "Iter 2!"},
        )
    row = (
        await db_session.execute(select(Attachment).where(Attachment.id == att.id))
    ).scalar_one()
    assert row.phase == "repro"  # no side effect


@pytest.mark.parametrize(
    "fields, bad_field",
    [
        ({}, None),                       # AC11 — empty map
        ({"filename": "x.png"}, "filename"),  # unknown (blob) key rejected
        ({"storage_key": "hack"}, "storage_key"),  # blob column not updatable
        ({"kind": "a" * 21}, "kind"),     # AC9 — over column width (20)
        ({"run_id": "a" * 121}, "run_id"),  # AC9 — over column width (120)
        ({"kind": None}, "kind"),         # kind is NOT nullable
        ({"kind": 123}, "kind"),          # non-string kind
    ],
)
async def test_update_invalid_metadata_rejected_no_side_effect(
    seed, db_session, attach_root, fields, bad_field
):
    """AC9/AC11: empty/unknown/over-width/typed metadata → clean 422 (never 500), row intact."""
    att = await _seed_attachment(
        db_session, seed, attach_root, kind="orig", run_id="orig-run", phase="repro"
    )
    with pytest.raises(AttachmentMetadataInvalid) as ei:
        await update_attachment(
            db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=str(att.id),
            fields=fields,
        )
    assert ei.value.field == bad_field
    assert ei.value.status == 422
    row = (
        await db_session.execute(select(Attachment).where(Attachment.id == att.id))
    ).scalar_one()
    assert (row.kind, row.run_id, row.phase) == ("orig", "orig-run", "repro")


async def test_update_writes_history_event(seed, db_session, attach_root):
    """AC7: a successful update writes an attachment_updated history row (old→new)."""
    ticket = await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt",
        content_type="text/plain", open_stream=_stream(b"x"), kind="log", phase="repro",
    )
    await update_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=str(att.id),
        fields={"phase": "iter-2-pass"},
    )
    hist = (
        await db_session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket.id,
                TicketHistory.event_type == "attachment_updated",
            )
        )
    ).scalars().all()
    assert len(hist) == 1
    assert hist[0].old_value["phase"] == "repro"
    assert hist[0].new_value["phase"] == "iter-2-pass"
    assert hist[0].new_value["attachment_id"] == str(att.id)


# --- update: RBAC + ticket-scope -------------------------------------------


async def test_update_rbac_stranger_and_reviewer_denied(seed, db_session, attach_root):
    """AC6: a stranger (no membership) and a reviewer (ticket.read only) cannot update."""
    att = await _seed_attachment(db_session, seed, attach_root)
    stranger = await _add_actor(db_session, seed.board, None, "Stranger")
    reviewer = await _add_actor(db_session, seed.board, "reviewer", "Reviewer")
    for actor in (stranger, reviewer):
        with pytest.raises(PermissionDenied):
            await update_attachment(
                db_session, actor=actor, ticket_id="PH-1", attachment_id=str(att.id),
                fields={"kind": "x"},
            )


async def test_update_authorizes_before_attachment_lookup(seed, db_session, attach_root):
    """AC6: an unauthorized caller gets 403 even for a non-existent attachment_id."""
    await _add_ticket(db_session, seed.board, seed.pm)
    stranger = await _add_actor(db_session, seed.board, None, "Stranger")
    with pytest.raises(PermissionDenied):
        await update_attachment(
            db_session, actor=stranger, ticket_id="PH-1",
            attachment_id=str(uuid.uuid4()), fields={"kind": "x"},
        )


async def test_update_cross_ticket_404(seed, db_session, attach_root):
    """AC10: updating an attachment under a DIFFERENT ticket key → 404, no mutation."""
    await _add_ticket(db_session, seed.board, seed.pm, key="PH-1")
    await _add_ticket(db_session, seed.board, seed.pm, key="PH-2")
    att = await create_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt",
        content_type="text/plain", open_stream=_stream(b"x"), kind="log",
    )
    with pytest.raises(NotFound):
        await update_attachment(
            db_session, actor=seed.backend, ticket_id="PH-2", attachment_id=str(att.id),
            fields={"kind": "hacked"},
        )
    row = (
        await db_session.execute(select(Attachment).where(Attachment.id == att.id))
    ).scalar_one()
    assert row.kind == "log"  # unchanged


# --- delete: hard delete + not-idempotent + blob unlink ---------------------


async def test_delete_removes_row_and_blob(seed, db_session, attach_root):
    """AC4: pm hard-deletes → row gone, blob unlinked, list empty."""
    att = await _seed_attachment(db_session, seed, attach_root, open_stream=_stream(b"bytes"))
    blob = attach_root / att.storage_key
    assert blob.is_file()

    res = await delete_attachment(
        db_session, actor=seed.pm, ticket_id="PH-1", attachment_id=str(att.id)
    )
    assert res == {"deleted": True, "id": str(att.id)}
    assert not blob.exists()               # blob unlinked
    assert _files_under(attach_root) == []
    assert await list_attachments(db_session, actor=seed.pm, ticket_id="PH-1") == []
    cnt = (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()
    assert cnt == 0


async def test_delete_second_call_404(seed, db_session, attach_root):
    """AC5: deleting the same id a second time → 404 (delete is NOT idempotent)."""
    att = await _seed_attachment(db_session, seed, attach_root)
    await delete_attachment(db_session, actor=seed.pm, ticket_id="PH-1", attachment_id=str(att.id))
    with pytest.raises(NotFound):
        await delete_attachment(
            db_session, actor=seed.pm, ticket_id="PH-1", attachment_id=str(att.id)
        )


async def test_delete_writes_history_snapshot(seed, db_session, attach_root):
    """AC7: a delete writes an attachment_deleted history row carrying the metadata snapshot."""
    ticket = await _add_ticket(db_session, seed.board, seed.pm)
    att = await create_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="clip.txt",
        content_type="text/plain", open_stream=_stream(b"x"), kind="log",
        run_id="run-9", phase="repro",
    )
    checksum = att.checksum_sha256
    await delete_attachment(db_session, actor=seed.pm, ticket_id="PH-1", attachment_id=str(att.id))
    hist = (
        await db_session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket.id,
                TicketHistory.event_type == "attachment_deleted",
            )
        )
    ).scalars().all()
    assert len(hist) == 1
    snap = hist[0].old_value
    assert snap["attachment_id"] == str(att.id)
    assert snap["filename"] == "clip.txt"
    assert snap["checksum_sha256"] == checksum
    assert snap["kind"] == "log"
    assert snap["run_id"] == "run-9"
    assert snap["phase"] == "repro"


async def test_delete_unlink_failure_does_not_raise(seed, db_session, attach_root, monkeypatch):
    """AC4 robustness: a POST-commit blob-unlink OSError is swallowed — delete still succeeds."""
    att = await _seed_attachment(db_session, seed, attach_root)

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "unlink", _boom)
    res = await delete_attachment(
        db_session, actor=seed.pm, ticket_id="PH-1", attachment_id=str(att.id)
    )
    assert res["deleted"] is True
    # row is gone even though the blob unlink failed (row delete already committed)
    cnt = (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()
    assert cnt == 0


# --- delete: DAR RBAC (qa+pm only) + ticket-scope --------------------------


async def test_delete_dar_rbac(seed, db_session, attach_root):
    """AC6: implementer/reviewer/stranger CANNOT delete (403); qa + pm CAN."""
    await _add_ticket(db_session, seed.board, seed.pm)
    qa = await _add_actor(db_session, seed.board, "qa", "QA")
    reviewer = await _add_actor(db_session, seed.board, "reviewer", "Reviewer")
    stranger = await _add_actor(db_session, seed.board, None, "Stranger")

    async def _mk(name):
        return await create_attachment(
            db_session, actor=seed.backend, ticket_id="PH-1", filename=name,
            content_type="text/plain", open_stream=_stream(b"x"),
        )

    target = await _mk("t.txt")
    # backend_dev holds attachment.update but NOT attachment.delete
    for actor in (seed.backend, reviewer, stranger):
        with pytest.raises(PermissionDenied):
            await delete_attachment(
                db_session, actor=actor, ticket_id="PH-1", attachment_id=str(target.id)
            )
    # target survived every denied attempt
    assert (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one() == 1

    # qa + pm succeed
    await delete_attachment(db_session, actor=qa, ticket_id="PH-1", attachment_id=str(target.id))
    other = await _mk("o.txt")
    await delete_attachment(
        db_session, actor=seed.pm, ticket_id="PH-1", attachment_id=str(other.id)
    )
    assert await list_attachments(db_session, actor=seed.pm, ticket_id="PH-1") == []


async def test_implementer_can_update_but_not_delete(seed, db_session, attach_root):
    """AC6: the same backend_dev updates fine but is denied delete (cap split)."""
    att = await _seed_attachment(db_session, seed, attach_root)
    updated = await update_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=str(att.id),
        fields={"kind": "screenshot"},
    )
    assert updated.kind == "screenshot"
    with pytest.raises(PermissionDenied):
        await delete_attachment(
            db_session, actor=seed.backend, ticket_id="PH-1", attachment_id=str(att.id)
        )


async def test_delete_authorizes_before_attachment_lookup(seed, db_session, attach_root):
    """AC6: an unauthorized caller gets 403 for a non-existent id (authz before lookup)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(PermissionDenied):
        await delete_attachment(
            db_session, actor=seed.backend, ticket_id="PH-1",  # backend lacks delete
            attachment_id=str(uuid.uuid4()),                    # and it doesn't exist
        )


async def test_delete_cross_ticket_404(seed, db_session, attach_root):
    """AC10: deleting an attachment under a DIFFERENT ticket key → 404; original survives."""
    await _add_ticket(db_session, seed.board, seed.pm, key="PH-1")
    await _add_ticket(db_session, seed.board, seed.pm, key="PH-2")
    att = await create_attachment(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt",
        content_type="text/plain", open_stream=_stream(b"x"),
    )
    with pytest.raises(NotFound):
        await delete_attachment(
            db_session, actor=seed.pm, ticket_id="PH-2", attachment_id=str(att.id)
        )
    assert len(await list_attachments(db_session, actor=seed.backend, ticket_id="PH-1")) == 1


# --- RBAC template + KNOWN_PERMISSIONS guards ------------------------------


def test_defaults_template_grants_update_delete_correctly():
    """AC8: DEFAULT_WEB_ROLES — implementers+qa+pm get update; only qa+pm get delete."""
    from app.services.defaults import DEFAULT_WEB_ROLES

    roles = DEFAULT_WEB_ROLES["roles"]
    update_holders = (
        "backend_dev", "frontend_dev", "qa", "pm",
        "unity_dev", "android_dev", "ios_dev", "data_engineer", "ml_engineer",
    )
    for role in update_holders:
        assert "attachment.update" in roles[role]["permissions"], role
    # delete is DARER — qa + pm only
    assert "attachment.delete" in roles["qa"]["permissions"]
    assert "attachment.delete" in roles["pm"]["permissions"]
    for role in ("backend_dev", "frontend_dev", "unity_dev", "android_dev"):
        assert "attachment.delete" not in roles[role]["permissions"], role
    # architect + reviewer get NEITHER cap
    for role in ("architect", "reviewer"):
        assert "attachment.update" not in roles[role]["permissions"], role
        assert "attachment.delete" not in roles[role]["permissions"], role


def test_known_permissions_include_new_caps():
    from app.core.permissions import KNOWN_PERMISSIONS

    assert {"attachment.update", "attachment.delete"} <= KNOWN_PERMISSIONS


# --- MCP wiring -------------------------------------------------------------


def test_mcp_update_delete_tools_registered():
    """Both tools are in the TOOLS catalog (with permission) and the input-model map."""
    by_name = {t.name: t for t in TOOLS}
    assert by_name["update_attachment"].permission == "attachment.update"
    assert by_name["delete_attachment"].permission == "attachment.delete"
    assert _TOOL_INPUT_MODELS["update_attachment"] is UpdateAttachmentInput
    assert _TOOL_INPUT_MODELS["delete_attachment"] is DeleteAttachmentInput

    parsed = UpdateAttachmentInput.model_validate(
        {"id": "PH-1", "attachment_id": "abc", "fields": {"phase": "repro", "kind": None}}
    )
    assert parsed.fields == {"phase": "repro", "kind": None}
    d = DeleteAttachmentInput.model_validate({"id": "PH-1", "attachment_id": "abc"})
    assert d.attachment_id == "abc"


async def test_mcp_dispatch_update_then_delete(seed, db_session, attach_root):
    """The MCP dispatch drives update (metadata) then delete (hard) end-to-end."""
    from app.mcp.server import _dispatch_tool

    att = await _seed_attachment(db_session, seed, attach_root, kind="log")
    upd = await _dispatch_tool(
        "update_attachment",
        {"id": "PH-1", "attachment_id": str(att.id), "fields": {"phase": "iter-2-pass"}},
        seed.backend,
        db_session,
    )
    assert upd["phase"] == "iter-2-pass"
    assert upd["kind"] == "log"

    dele = await _dispatch_tool(
        "delete_attachment",
        {"id": "PH-1", "attachment_id": str(att.id)},
        seed.pm,
        db_session,
    )
    assert dele == {"deleted": True, "id": str(att.id)}


async def test_mcp_update_metadata_error_detail_carries_field(seed, db_session, attach_root):
    """AC9 at the MCP boundary: the domain-error detail surfaces the offending `field`."""
    from app.mcp.server import _dispatch_tool, _domain_error_detail

    att = await _seed_attachment(db_session, seed, attach_root)
    with pytest.raises(AttachmentMetadataInvalid) as ei:
        await _dispatch_tool(
            "update_attachment",
            {"id": "PH-1", "attachment_id": str(att.id), "fields": {"run_id": "a" * 121}},
            seed.backend,
            db_session,
        )
    detail = _domain_error_detail(ei.value)
    assert detail["error"] == "attachment_metadata_invalid"
    assert detail["field"] == "run_id"


# --- REST endpoints (PATCH + DELETE) ---------------------------------------


def _override(session, actor):
    """Wire the in-memory session + a fixed actor onto the app for a TestClient run."""
    from app.api.deps import current_actor
    from app.db.session import get_db_session

    async def _fake_session():
        yield session

    async def _fake_actor():
        return actor

    app.dependency_overrides[get_db_session] = _fake_session
    app.dependency_overrides[current_actor] = _fake_actor


async def test_rest_patch_updates_and_null_clears(seed, db_session, attach_root):
    """AC1/AC11 over HTTP: PATCH sets given fields; explicit null clears; omitted untouched."""
    att = await _seed_attachment(
        db_session, seed, attach_root, kind="log", run_id="run-1", phase="repro"
    )
    _override(db_session, seed.backend)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        url = f"/api/tickets/PH-1/attachments/{att.id}"

        r = client.patch(url, json={"phase": "iter-2-pass", "kind": "screenshot"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["phase"] == "iter-2-pass"
        assert body["kind"] == "screenshot"
        assert body["run_id"] == "run-1"  # omitted → untouched

        # explicit null clears phase; kind/run_id (omitted) survive
        r2 = client.patch(url, json={"phase": None})
        assert r2.status_code == 200, r2.text
        assert r2.json()["phase"] is None
        assert r2.json()["kind"] == "screenshot"
    finally:
        app.dependency_overrides.clear()


async def test_rest_patch_empty_and_overflow_422(seed, db_session, attach_root):
    """AC9/AC11 over HTTP: empty body → 422; over-width kind → 422 carrying field=kind."""
    att = await _seed_attachment(db_session, seed, attach_root)
    _override(db_session, seed.backend)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        url = f"/api/tickets/PH-1/attachments/{att.id}"

        empty = client.patch(url, json={})
        assert empty.status_code == 422, empty.text
        assert empty.json()["error"] == "attachment_metadata_invalid"

        over = client.patch(url, json={"kind": "a" * 21})
        assert over.status_code == 422, over.text
        assert over.json()["error"] == "attachment_metadata_invalid"
        assert over.json()["field"] == "kind"
    finally:
        app.dependency_overrides.clear()


async def test_rest_patch_invalid_phase_422(seed, db_session, attach_root):
    """AC3 over HTTP: a bad phase slug surfaces as 422 attachment_phase_invalid."""
    att = await _seed_attachment(db_session, seed, attach_root, phase="repro")
    _override(db_session, seed.backend)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        url = f"/api/tickets/PH-1/attachments/{att.id}"
        r = client.patch(url, json={"phase": "Bad Phase"})
        assert r.status_code == 422, r.text
        assert r.json()["error"] == "attachment_phase_invalid"
        assert r.json()["phase"] == "Bad Phase"
    finally:
        app.dependency_overrides.clear()


async def test_rest_delete_204_then_404(seed, db_session, attach_root):
    """AC4/AC5 over HTTP: DELETE → 204 (empty body, blob gone); second DELETE → 404."""
    att = await _seed_attachment(db_session, seed, attach_root, open_stream=_stream(b"bytes"))
    blob = attach_root / att.storage_key
    assert blob.is_file()
    _override(db_session, seed.pm)  # pm holds attachment.delete
    try:
        client = TestClient(app, raise_server_exceptions=True)
        url = f"/api/tickets/PH-1/attachments/{att.id}"

        r = client.delete(url)
        assert r.status_code == 204, r.text
        assert r.content == b""  # 204 carries no body

        r2 = client.delete(url)
        assert r2.status_code == 404, r2.text
        assert r2.json()["error"] == "not_found"
    finally:
        app.dependency_overrides.clear()

    assert not blob.exists()
    assert (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one() == 0


async def test_rest_delete_forbidden_for_implementer(seed, db_session, attach_root):
    """AC6 over HTTP: an implementer (no delete cap) → 403; row + blob untouched."""
    att = await _seed_attachment(db_session, seed, attach_root)
    blob = attach_root / att.storage_key
    _override(db_session, seed.backend)  # backend_dev lacks attachment.delete
    try:
        client = TestClient(app, raise_server_exceptions=True)
        r = client.delete(f"/api/tickets/PH-1/attachments/{att.id}")
        assert r.status_code == 403, r.text
        assert r.json()["error"] == "permission_denied"
    finally:
        app.dependency_overrides.clear()
    assert blob.is_file()  # denied before any mutation
    assert (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one() == 1


# --- migration: idempotent board-roles cap backfill -------------------------


def _load_crud_caps_migration():
    """Import the PH-313 caps-backfill migration module by file path."""
    path = (
        Path(__file__).resolve().parents[1]
        / "app" / "db" / "migrations" / "versions"
        / "20260716_0015_ph_313_attachment_crud_caps.py"
    )
    spec = importlib.util.spec_from_file_location("ph313_crud_caps_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_crud_caps_migration_backfill_idempotent_and_single_head(tmp_path):
    """AC8: idempotent board-roles backfill (update→impl+qa+pm, delete→qa+pm), single head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load_crud_caps_migration()
    assert module.revision == "ph313attachmentcrud"
    assert module.down_revision == "ph311attachmentphase"

    migrations_dir = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"expected a single alembic head, got {heads}"
    assert "ph313attachmentcrud" in heads

    roles_before = {
        "roles": {
            "backend_dev": {"permissions": ["ticket.read", "attachment.add"]},
            "frontend_dev": {"permissions": ["ticket.read", "attachment.add"]},
            "pm": {"permissions": ["ticket.create", "attachment.add"]},
            "qa": {"permissions": ["ticket.read", "attachment.add"]},
            "reviewer": {"permissions": ["ticket.read"]},
            "architect": {"permissions": ["ticket.create"]},
        }
    }

    meta = MetaData()
    boards_tbl = Table(
        "boards",
        meta,
        Column("id", Uuid(as_uuid=True), primary_key=True),
        Column("roles", JSON),
    )
    board_id = uuid.uuid4()

    engine = create_engine(f"sqlite:///{tmp_path / 'caps.db'}")
    original_op = module.op
    try:
        with engine.connect() as conn:
            meta.create_all(conn)
            conn.execute(boards_tbl.insert().values(id=board_id, roles=roles_before))
            conn.commit()
            module.op = Operations(MigrationContext.configure(conn))

            def _perms():
                raw = conn.execute(
                    select(boards_tbl.c.roles).where(boards_tbl.c.id == board_id)
                ).scalar_one()
                data = json.loads(raw) if isinstance(raw, str) else raw
                return {r: data["roles"][r]["permissions"] for r in data["roles"]}

            module.upgrade()
            conn.commit()
            after = _perms()
            # update → every implementer + qa + pm
            for role in ("backend_dev", "frontend_dev", "pm", "qa"):
                assert "attachment.update" in after[role], role
            # delete → qa + pm ONLY
            assert "attachment.delete" in after["pm"]
            assert "attachment.delete" in after["qa"]
            assert "attachment.delete" not in after["backend_dev"]
            assert "attachment.delete" not in after["frontend_dev"]
            # reviewer + architect → neither
            for role in ("reviewer", "architect"):
                assert "attachment.update" not in after[role], role
                assert "attachment.delete" not in after[role], role

            # idempotent: re-running upgrade adds no duplicates
            module.upgrade()
            conn.commit()
            after2 = _perms()
            assert after2["pm"].count("attachment.update") == 1
            assert after2["pm"].count("attachment.delete") == 1
            assert after2["qa"].count("attachment.delete") == 1
            assert after2["backend_dev"].count("attachment.update") == 1

            # downgrade removes ONLY the added caps; originals survive
            module.downgrade()
            conn.commit()
            restored = _perms()
            assert "attachment.update" not in restored["backend_dev"]
            assert "attachment.add" in restored["backend_dev"]  # untouched
            assert "attachment.update" not in restored["pm"]
            assert "attachment.delete" not in restored["pm"]
            assert "attachment.add" in restored["pm"]  # untouched
            assert "attachment.delete" not in restored["qa"]
            assert restored["reviewer"] == ["ticket.read"]  # never touched
    finally:
        module.op = original_op
        engine.dispose()


# ---------------------------------------------------------------------------
# PH-315: add-path kind/run_id length validation (update parity). The caps are
# enforced on BOTH add entry points (REST create + MCP ingest) via the SAME
# shared _validate_metadata_lengths that the PH-313 update-path already uses —
# rejection is side-effect-free (pre-write gate), boundary values persist.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over_kwargs, bad_field",
    [
        ({"kind": "a" * 21}, "kind"),       # AC1 — one over the 20 cap
        ({"run_id": "a" * 121}, "run_id"),  # AC2 — one over the 120 cap
    ],
)
async def test_create_rejects_overlong_metadata_no_side_effect(
    seed, db_session, attach_root, over_kwargs, bad_field
):
    """AC1/AC2: over-width kind/run_id on the REST add-path → 422(field), no blob/row/event."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(AttachmentMetadataInvalid) as ei:
        await create_attachment(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="e.txt",
            content_type="text/plain",
            open_stream=_stream(b"hi"),
            **over_kwargs,
        )
    assert ei.value.field == bad_field
    assert ei.value.status == 422
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


@pytest.mark.parametrize(
    "over_kwargs, bad_field",
    [
        ({"kind": "a" * 21}, "kind"),       # AC1 — one over the 20 cap
        ({"run_id": "a" * 121}, "run_id"),  # AC2 — one over the 120 cap
    ],
)
async def test_ingest_rejects_overlong_metadata_no_side_effect(
    seed, db_session, attach_root, tmp_path, monkeypatch, over_kwargs, bad_field
):
    """AC1/AC2: over-width kind/run_id on the MCP zero-copy add-path → 422(field), no side effect.

    The source file is valid + reachable — the length gate fires INSIDE
    ``_persist_attachment`` (after path-validation + the stat size-gate), so the
    request is otherwise a legitimate ingest. The side-effect assertion targets
    ``attach_root`` (never written); the read-only source under repos is untouched.
    """
    await _add_ticket(db_session, seed.board, seed.pm)
    settings = get_settings()
    monkeypatch.setattr(settings, "host_home", str(tmp_path))
    monkeypatch.setattr(settings, "repos_root", str(tmp_path))
    src = tmp_path / "evidence.txt"
    src.write_bytes(b"reachable ingest source\n")

    with pytest.raises(AttachmentMetadataInvalid) as ei:
        await ingest_from_source_path(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            source_path=str(src),
            **over_kwargs,
        )
    assert ei.value.field == bad_field
    assert ei.value.status == 422
    # Nothing landed in the attachments store (no blob / row / event).
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


async def test_create_boundary_kind_run_id_persist_verbatim(seed, db_session, attach_root):
    """AC3: kind==20 AND run_id==120 (exactly at the caps) persist verbatim."""
    from app.services.attachments import _KIND_MAX_LEN, _RUN_ID_MAX_LEN

    ticket = await _add_ticket(db_session, seed.board, seed.pm)
    kind = "a" * _KIND_MAX_LEN       # exactly 20
    run_id = "b" * _RUN_ID_MAX_LEN   # exactly 120

    att = await create_attachment(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="t.txt",
        content_type="text/plain",
        open_stream=_stream(b"boundary evidence"),
        kind=kind,
        run_id=run_id,
    )
    # stored verbatim, exactly at the cap
    assert att.kind == kind and len(att.kind) == _KIND_MAX_LEN
    assert att.run_id == run_id and len(att.run_id) == _RUN_ID_MAX_LEN

    # row + blob + exactly one attachment_added event
    blob = attach_root / att.storage_key
    assert blob.is_file()
    events = (
        await db_session.execute(
            select(func.count())
            .select_from(TicketHistory)
            .where(
                TicketHistory.ticket_id == ticket.id,
                TicketHistory.event_type == "attachment_added",
            )
        )
    ).scalar_one()
    assert events == 1


def test_validate_metadata_lengths_single_shared_helper():
    """AC5: one shared kind/run_id length validator (None/at-cap pass, overflow 422)."""
    from app.services.attachments import (
        _KIND_MAX_LEN,
        _RUN_ID_MAX_LEN,
        _validate_metadata_lengths,
    )

    # None + exactly-at-cap values are a passthrough (no raise)
    assert _validate_metadata_lengths(None, None) is None
    assert _validate_metadata_lengths("a" * _KIND_MAX_LEN, "b" * _RUN_ID_MAX_LEN) is None

    # one-over each → 422 naming the offending field
    with pytest.raises(AttachmentMetadataInvalid) as ek:
        _validate_metadata_lengths("a" * (_KIND_MAX_LEN + 1), None)
    assert ek.value.field == "kind" and ek.value.status == 422
    with pytest.raises(AttachmentMetadataInvalid) as er:
        _validate_metadata_lengths(None, "b" * (_RUN_ID_MAX_LEN + 1))
    assert er.value.field == "run_id" and er.value.status == 422


def test_length_cap_defined_in_exactly_one_place():
    """AC5: the kind/run_id length cap is NOT duplicated — one `len(...) > _*_MAX_LEN` site each."""
    import inspect as _inspect

    from app.services import attachments as _mod

    src = _inspect.getsource(_mod)
    assert src.count("> _KIND_MAX_LEN") == 1, "kind cap duplicated outside shared helper"
    assert src.count("> _RUN_ID_MAX_LEN") == 1, "run_id cap duplicated outside shared helper"


# ===========================================================================
# PH-318: add_attachment_content — inline-base64 (byte-carrying) MCP add path
# ===========================================================================


async def _count_attachments(db_session) -> int:
    return (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()


async def _count_added_events(db_session) -> int:
    return (
        await db_session.execute(
            select(func.count())
            .select_from(TicketHistory)
            .where(TicketHistory.event_type == "attachment_added")
        )
    ).scalar_one()


async def _assert_no_side_effect(db_session, attach_root) -> None:
    """A rejected content-path call leaves NO blob, NO row, NO attachment_added event."""
    assert _files_under(attach_root) == []
    assert await _count_attachments(db_session) == 0
    assert await _count_added_events(db_session) == 0


def _spy_b64decode(monkeypatch):
    """Patch base64.b64decode with a call-recording spy; returns the call list."""
    import app.services.attachments as _attach_mod

    calls: list = []
    real = base64.b64decode

    def _spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(_attach_mod.base64, "b64decode", _spy)
    return calls


# --- AC1 / AC8: happy path — one row/blob/event, checksum + provenance -------


async def test_content_persists_row_file_history_and_checksum(seed, db_session, attach_root):
    """AC1/AC8: valid inline b64 → ONE row+blob+attachment_added, byte-identical persist."""
    ticket = await _add_ticket(db_session, seed.board, seed.pm)
    raw = b"\x89PNG\r\n\x1a\n" + b"remote-agent-screenshot" * 4
    content_b64 = base64.b64encode(raw).decode()

    att = await ingest_from_content(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="shot.png",
        content_b64=content_b64,
        kind="screenshot",
    )

    # Type guessed from filename; provenance is agent; size + checksum over the decoded bytes.
    assert att.content_type == "image/png"
    assert att.source == "agent"
    assert att.kind == "screenshot"
    assert att.size_bytes == len(raw)
    assert att.checksum_sha256 == hashlib.sha256(raw).hexdigest()

    # Blob on disk under a UUID shard — client filename NEVER in the path.
    blob = attach_root / att.storage_key
    assert blob.is_file()
    assert blob.read_bytes() == raw
    assert att.storage_key == f"{str(att.id)[:2]}/{att.id}"
    assert "shot" not in att.storage_key

    # list echoes it, and EXACTLY one attachment_added event was written (single write path).
    listed = await list_attachments(db_session, actor=seed.backend, ticket_id="PH-1")
    assert [a.id for a in listed] == [att.id]
    events = (
        await db_session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket.id,
                TicketHistory.event_type == "attachment_added",
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].new_value["attachment_id"] == str(att.id)
    assert events[0].new_value["filename"] == "shot.png"


async def test_mcp_dispatch_add_attachment_content(seed, db_session, attach_root):
    """AC1 at the MCP boundary: _dispatch_tool drives the inline-b64 add end-to-end."""
    from app.mcp.server import _dispatch_tool

    await _add_ticket(db_session, seed.board, seed.pm)
    raw = b'{"result": "pass", "iters": 6}'
    out = await _dispatch_tool(
        "add_attachment_content",
        {
            "id": "PH-1",
            "filename": "report.json",
            "content_b64": base64.b64encode(raw).decode(),
            "kind": "report",
            "phase": "iter-2-pass",
        },
        seed.backend,
        db_session,
    )
    assert out["content_type"] == "application/json"
    assert out["source"] == "agent"
    assert out["size_bytes"] == len(raw)
    assert out["kind"] == "report"
    assert out["phase"] == "iter-2-pass"
    assert out["filename"] == "report.json"


async def test_content_and_ingest_agree_on_type_and_source(
    seed, db_session, attach_root, tmp_path, monkeypatch
):
    """AC10: the inline path guesses content_type from filename + source='agent', same as ingest."""
    await _add_ticket(db_session, seed.board, seed.pm)
    settings = get_settings()
    monkeypatch.setattr(settings, "host_home", str(tmp_path))
    monkeypatch.setattr(settings, "repos_root", str(tmp_path))
    src = tmp_path / "e.png"
    src.write_bytes(b"png-bytes")

    via_source = await ingest_from_source_path(
        db_session, actor=seed.backend, ticket_id="PH-1", source_path=str(src)
    )
    via_content = await ingest_from_content(
        db_session,
        actor=seed.backend,
        ticket_id="PH-1",
        filename="e.png",
        content_b64=base64.b64encode(b"png-bytes").decode(),
    )
    assert via_source.content_type == via_content.content_type == "image/png"
    assert via_source.source == via_content.source == "agent"


# --- AC2: post-decode cap 413 (real decoded length) -------------------------


async def test_content_post_decode_cap_413_no_side_effect(
    seed, db_session, attach_root, monkeypatch
):
    """AC2: decoded size > attachment_mcp_max_bytes → 413(limit==cap), no blob/row/event.

    cap=100 (not divisible by 3) so a 101-byte payload encodes to EXACTLY the pre-decode
    ceiling (passes the fast-fail) yet trips the authoritative post-decode gate.
    """
    await _add_ticket(db_session, seed.board, seed.pm)
    monkeypatch.setattr(get_settings(), "attachment_mcp_max_bytes", 100)
    raw = b"x" * 101
    content_b64 = base64.b64encode(raw).decode()
    assert len(content_b64) == 4 * ((100 + 2) // 3)  # exactly the ceiling → passes fast-fail

    with pytest.raises(PayloadTooLarge) as ei:
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="big.png",
            content_b64=content_b64,
        )
    assert ei.value.limit == 100
    assert ei.value.status == 413
    await _assert_no_side_effect(db_session, attach_root)


# --- AC3: pre-decode fast-fail (decode NOT called) --------------------------


async def test_content_pre_decode_fast_fail_before_decode(
    seed, db_session, attach_root, monkeypatch
):
    """AC3: an over-ceiling string is rejected 413 BEFORE base64.b64decode runs."""
    await _add_ticket(db_session, seed.board, seed.pm)
    monkeypatch.setattr(get_settings(), "attachment_mcp_max_bytes", 100)
    decode_calls = _spy_b64decode(monkeypatch)

    # 300 bytes → 400 encoded chars, far above the 136-char ceiling for cap=100.
    content_b64 = base64.b64encode(b"x" * 300).decode()
    assert len(content_b64) > 4 * ((100 + 2) // 3)

    with pytest.raises(PayloadTooLarge) as ei:
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="huge.png",
            content_b64=content_b64,
        )
    assert ei.value.limit == 100
    assert decode_calls == []  # decode never reached → no decoded buffer allocated
    await _assert_no_side_effect(db_session, attach_root)


# --- AC4: malformed base64 → 422 AttachmentContentInvalid (not 500) ---------


@pytest.mark.parametrize(
    "bad_b64",
    [
        "not base64 %%%",   # non-alphabet chars + whitespace
        "abc",              # length not a multiple of 4 (bad padding, validate=True)
        "aGVsbG8=\n",       # valid 'hello' + embedded newline (validate=True rejects it)
        "café",             # non-ASCII string
        "YWJj!",            # trailing non-alphabet char
    ],
)
async def test_content_malformed_base64_422_no_side_effect(
    seed, db_session, attach_root, bad_b64
):
    """AC4: undecodable content is a clean 422 (code=attachment_content_invalid), no side effect."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(AttachmentContentInvalid) as ei:
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="x.png",
            content_b64=bad_b64,
        )
    assert ei.value.status == 422
    assert ei.value.code == "attachment_content_invalid"
    await _assert_no_side_effect(db_session, attach_root)


async def test_mcp_content_malformed_base64_error_detail(seed, db_session, attach_root):
    """AC4 at the MCP boundary: malformed b64 surfaces as a domain error detail, not a 500."""
    from app.mcp.server import _dispatch_tool, _domain_error_detail

    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(AttachmentContentInvalid) as ei:
        await _dispatch_tool(
            "add_attachment_content",
            {"id": "PH-1", "filename": "x.png", "content_b64": "not*valid"},
            seed.backend,
            db_session,
        )
    detail = _domain_error_detail(ei.value)
    assert detail["error"] == "attachment_content_invalid"
    assert "base64" in detail["message"].lower()


# --- AC5: content-type allowlist 415 ----------------------------------------


@pytest.mark.parametrize("filename", ["x.svg", "noextension"])
async def test_content_unsupported_media_type_415_no_side_effect(
    seed, db_session, attach_root, filename
):
    """AC5: a filename whose guessed type is outside the allowlist → 415, no side effect."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(UnsupportedMediaType):
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename=filename,
            content_b64=base64.b64encode(b"payload").decode(),
        )
    await _assert_no_side_effect(db_session, attach_root)


# --- AC6: shared phase / kind / run_id gates fire on the content path too ----


async def test_content_invalid_phase_422_no_side_effect(seed, db_session, attach_root):
    """AC6: an invalid phase slug → 422 (AttachmentPhaseInvalid) with no side effect."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(AttachmentPhaseInvalid):
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="x.png",
            content_b64=base64.b64encode(b"payload").decode(),
            phase="Bad Phase!",
        )
    await _assert_no_side_effect(db_session, attach_root)


@pytest.mark.parametrize(
    "over_kwargs, bad_field",
    [
        ({"kind": "k" * 21}, "kind"),
        ({"run_id": "r" * 121}, "run_id"),
    ],
)
async def test_content_overlong_metadata_422_no_side_effect(
    seed, db_session, attach_root, over_kwargs, bad_field
):
    """AC6: over-width kind/run_id on the content path → 422(field), no side effect."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(AttachmentMetadataInvalid) as ei:
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="x.png",
            content_b64=base64.b64encode(b"payload").decode(),
            **over_kwargs,
        )
    assert ei.value.field == bad_field
    assert ei.value.status == 422
    await _assert_no_side_effect(db_session, attach_root)


# --- AC7: authorize BEFORE decode (403 precedes 413/422) --------------------


async def test_content_authorizes_before_decode_403(
    seed, db_session, attach_root, monkeypatch
):
    """AC7: a caller lacking attachment.add → 403 with the content NEVER decoded, no side effect."""
    await _add_ticket(db_session, seed.board, seed.pm)
    stranger = await _add_actor(db_session, seed.board, None, "Stranger")
    decode_calls = _spy_b64decode(monkeypatch)

    with pytest.raises(PermissionDenied):
        await ingest_from_content(
            db_session,
            actor=stranger,
            ticket_id="PH-1",
            filename="x.png",
            content_b64=base64.b64encode(b"secret bytes").decode(),
        )
    assert decode_calls == []  # 403 precedes any base64 decode
    await _assert_no_side_effect(db_session, attach_root)


# --- AC9: MCP registration + input schema -----------------------------------


def test_mcp_add_attachment_content_registered():
    """AC9: tool registered — attachment.add perm, 6-field schema, and description keywords."""
    from app.mcp.server import _build_mcp_tool_list

    by_name = {t.name: t for t in TOOLS}
    assert by_name["add_attachment_content"].permission == "attachment.add"
    assert _TOOL_INPUT_MODELS["add_attachment_content"] is AddAttachmentContentInput

    # model defaults: kind → "other", run_id/phase → None; the three required fields validate.
    parsed = AddAttachmentContentInput.model_validate(
        {"id": "PH-1", "filename": "s.png", "content_b64": "aGk="}
    )
    assert parsed.kind == "other"
    assert parsed.run_id is None and parsed.phase is None

    tool = next(
        t for t in _build_mcp_tool_list() if t["name"] == "add_attachment_content"
    )
    props = set(tool["inputSchema"]["properties"])
    assert {"id", "filename", "kind", "content_b64", "run_id", "phase"} <= props
    assert set(tool["inputSchema"]["required"]) == {"id", "filename", "content_b64"}

    desc = by_name["add_attachment_content"].description.lower()
    assert "remote" in desc
    assert "8 mib" in desc
    assert "mp4" in desc
    assert "rest" in desc
