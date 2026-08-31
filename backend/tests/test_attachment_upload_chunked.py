"""PH-341: chunked MCP attachment upload (add_attachment_begin/chunk/commit).

Covers the AC-5 matrix at the service layer + the MCP dispatch boundary:
  (i)   > 8 MiB end-to-end begin -> chunk xN -> commit succeeds; size/checksum
        match the source (and the same payload is rejected by the inline path).
  (ii)  cumulative payload > 25 MiB → 413, session aborted (no staging blob, no row).
  (iii) content-type outside the allowlist → 415 at begin (no session row).
  (iv)  actor without attachment.add → 403 at begin (no session row).
  (v)   seq gap → 409 carrying expected_seq.
  (vi)  chunk/commit by a non-owner actor → 404 (no existence leak).
  (vii) an expired session is GC-swept (row + staging blob gone).
Plus: per-chunk > 8 MiB → 413 (session preserved for retry), empty commit → 409,
sha256 mismatch → 409 (session preserved) / match → success, tool registration,
the AC-4 description refresh, and the migration round-trip / single-head check.
"""

import base64
import hashlib
import importlib.util
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AttachmentContentInvalid,
    AttachmentUploadInvalid,
    NotFound,
    PayloadTooLarge,
    PermissionDenied,
    UnsupportedMediaType,
)
from app.db.models import (
    Actor,
    Attachment,
    AttachmentUploadSession,
    Board,
    BoardMembership,
    Ticket,
)
from app.services import attachment_uploads
from app.services.attachments import (
    _staging_path_for,
    append_chunk,
    begin_upload_session,
    commit_upload_session,
    ingest_from_content,
)

# asyncio_mode = "auto" (pyproject) → async tests are auto-collected; no marker needed.

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


async def _add_actor(session: AsyncSession, board: Board, role: str | None, name: str) -> Actor:
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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


async def _count_attachments(session: AsyncSession) -> int:
    return len((await session.execute(select(Attachment))).scalars().all())


async def _count_sessions(session: AsyncSession) -> int:
    return len((await session.execute(select(AttachmentUploadSession))).scalars().all())


@pytest.fixture
def attach_root(tmp_path, monkeypatch):
    """Point attachments_root at a tmp dir (the cached Settings singleton)."""
    settings = get_settings()
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(settings, "attachments_root", str(root))
    return root


# ---------------------------------------------------------------------------
# (i) happy path: > 8 MiB end-to-end, size + checksum fidelity
# ---------------------------------------------------------------------------


async def test_over_8mib_end_to_end_matches_source(seed, db_session, attach_root):
    """AC-5(i): a 12 MiB upload the inline path REJECTS goes through chunked verbatim."""
    await _add_ticket(db_session, seed.board, seed.pm)
    size = 12 * 1024 * 1024  # 12 MiB > the 8 MiB inline cap, < the 25 MiB disk cap
    raw = os.urandom(size)
    expected_sha = hashlib.sha256(raw).hexdigest()

    # The inline single-call path cannot carry this (proves the gap being closed).
    with pytest.raises(PayloadTooLarge):
        await ingest_from_content(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="rec.mp4",
            content_b64=_b64(raw),
        )

    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="rec.mp4", kind="recording"
    )
    assert upload.next_seq == 0
    assert upload.content_type == "video/mp4"

    chunk = 6 * 1024 * 1024
    seq = 0
    for offset in range(0, size, chunk):
        result = await append_chunk(
            db_session,
            actor=seed.backend,
            upload_id=str(upload.id),
            seq=seq,
            data_b64=_b64(raw[offset : offset + chunk]),
        )
        seq += 1
        assert result.next_seq == seq
    assert result.bytes_received == size

    att = await commit_upload_session(
        db_session, actor=seed.backend, upload_id=str(upload.id), sha256=expected_sha
    )
    assert att.size_bytes == size
    assert att.checksum_sha256 == expected_sha
    assert att.content_type == "video/mp4"
    assert att.source == "agent"
    assert att.kind == "recording"

    # Persisted blob is byte-identical; the session row + staging blob are gone.
    blob = attach_root / att.storage_key
    assert blob.read_bytes() == raw
    assert await _count_sessions(db_session) == 0
    assert not _staging_path_for(upload.staging_key).exists()


async def test_mcp_dispatch_begin_chunk_commit(seed, db_session, attach_root):
    """AC-1 at the MCP boundary: the wire path drives begin→chunk→commit + response shapes."""
    from app.mcp.server import _dispatch_tool

    await _add_ticket(db_session, seed.board, seed.pm)
    raw = b"\x89PNG\r\n\x1a\n" + os.urandom(2048)
    settings = get_settings()

    begun = await _dispatch_tool(
        "add_attachment_begin",
        {"id": "PH-1", "filename": "shot.png", "kind": "screenshot", "phase": "iter-2-pass"},
        seed.backend,
        db_session,
    )
    assert begun["next_seq"] == 0
    assert begun["chunk_max_bytes"] == settings.attachment_mcp_max_bytes
    assert begun["max_total_bytes"] == settings.attachment_max_bytes
    assert "expires_at" in begun and "upload_id" in begun

    chunked = await _dispatch_tool(
        "add_attachment_chunk",
        {"upload_id": begun["upload_id"], "seq": 0, "data_b64": _b64(raw)},
        seed.backend,
        db_session,
    )
    assert chunked == {
        "upload_id": begun["upload_id"],
        "next_seq": 1,
        "bytes_received": len(raw),
    }

    committed = await _dispatch_tool(
        "add_attachment_commit",
        {"upload_id": begun["upload_id"]},
        seed.backend,
        db_session,
    )
    assert committed["content_type"] == "image/png"
    assert committed["size_bytes"] == len(raw)
    assert committed["phase"] == "iter-2-pass"
    assert committed["checksum_sha256"] == hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# (ii) cumulative cap → 413 + abort (no row, no staging blob)
# ---------------------------------------------------------------------------


async def test_cumulative_cap_aborts_session_no_residue(seed, db_session, attach_root, monkeypatch):
    """AC-5(ii)/AC-3: exceeding attachment_max_bytes aborts — no >cap staging, no attachment."""
    await _add_ticket(db_session, seed.board, seed.pm)
    settings = get_settings()
    monkeypatch.setattr(settings, "attachment_max_bytes", 10)  # tiny cumulative cap
    monkeypatch.setattr(settings, "attachment_mcp_max_bytes", 8)  # per-chunk cap

    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    # First 6-byte chunk is fine (bytes_received=6).
    await append_chunk(
        db_session, actor=seed.backend, upload_id=str(upload.id), seq=0, data_b64=_b64(b"aaaaaa")
    )
    assert _staging_path_for(upload.staging_key).exists()

    # Second 6-byte chunk trips 6+6=12 > 10 → 413 AND aborts the session.
    with pytest.raises(PayloadTooLarge) as exc:
        await append_chunk(
            db_session,
            actor=seed.backend,
            upload_id=str(upload.id),
            seq=1,
            data_b64=_b64(b"bbbbbb"),
        )
    assert exc.value.limit == 10

    assert await _count_sessions(db_session) == 0  # row aborted
    assert not _staging_path_for(upload.staging_key).exists()  # staging swept
    assert _files_under(attach_root) == []  # no attachment blob either
    assert await _count_attachments(db_session) == 0


async def test_per_chunk_cap_413_preserves_session(seed, db_session, attach_root, monkeypatch):
    """A single chunk over attachment_mcp_max_bytes → 413 WITHOUT aborting (retry allowed)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    settings = get_settings()
    monkeypatch.setattr(settings, "attachment_mcp_max_bytes", 4)  # per-chunk cap

    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    with pytest.raises(PayloadTooLarge) as exc:
        await append_chunk(
            db_session,
            actor=seed.backend,
            upload_id=str(upload.id),
            seq=0,
            data_b64=_b64(b"toolong"),
        )
    assert exc.value.limit == 4

    # Session survives; seq did not advance; no staging bytes were written.
    fresh = (
        await db_session.execute(
            select(AttachmentUploadSession).where(AttachmentUploadSession.id == upload.id)
        )
    ).scalar_one()
    assert fresh.next_seq == 0
    assert fresh.bytes_received == 0
    assert not _staging_path_for(upload.staging_key).exists()


# ---------------------------------------------------------------------------
# (iii) content-type allowlist + (iv) RBAC — both reject at begin, no row
# ---------------------------------------------------------------------------


async def test_begin_rejects_type_outside_allowlist_415(seed, db_session, attach_root):
    """AC-5(iii): a filename guessing outside the allowlist → 415 at begin, no session row."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(UnsupportedMediaType):
        await begin_upload_session(
            db_session, actor=seed.backend, ticket_id="PH-1", filename="evil.exe"
        )
    assert await _count_sessions(db_session) == 0


async def test_begin_without_cap_denied_403_no_row(seed, db_session, attach_root):
    """AC-5(iv): an actor lacking attachment.add → 403 at begin, before any session row.

    Uses ``architect`` — a board member that holds ticket.read but NOT attachment.add
    (it is not an evidence producer), so the denial is the cap miss, not non-membership.
    (NB: ``reviewer`` now holds attachment.add as of PH-343, so it is no longer a
    without-cap role.)
    """
    await _add_ticket(db_session, seed.board, seed.pm)
    architect = await _add_actor(db_session, seed.board, "architect", "Architect")
    with pytest.raises(PermissionDenied):
        await begin_upload_session(
            db_session, actor=architect, ticket_id="PH-1", filename="shot.png"
        )
    assert await _count_sessions(db_session) == 0


async def test_begin_declared_size_over_cap_413(seed, db_session, attach_root):
    """A declared_size over the 25 MiB cap is refused up front (courtesy fast-fail)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(PayloadTooLarge):
        await begin_upload_session(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            filename="rec.mp4",
            declared_size=get_settings().attachment_max_bytes + 1,
        )
    assert await _count_sessions(db_session) == 0


# ---------------------------------------------------------------------------
# (v) seq gap → 409 expected_seq
# ---------------------------------------------------------------------------


async def test_seq_gap_409_carries_expected_seq(seed, db_session, attach_root):
    """AC-5(v): a non-monotonic seq → AttachmentUploadInvalid(409) with expected_seq."""
    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    with pytest.raises(AttachmentUploadInvalid) as exc:
        await append_chunk(
            db_session, actor=seed.backend, upload_id=str(upload.id), seq=1, data_b64=_b64(b"x")
        )
    assert exc.value.expected_seq == 0
    assert exc.value.status == 409


async def test_chunk_malformed_base64_422(seed, db_session, attach_root):
    """A malformed (wrapped/non-alphabet) chunk → a clean 422, never a 500."""
    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    with pytest.raises(AttachmentContentInvalid):
        await append_chunk(
            db_session, actor=seed.backend, upload_id=str(upload.id), seq=0, data_b64="not@base64!"
        )


# ---------------------------------------------------------------------------
# (vi) non-owner → 404 on chunk AND commit
# ---------------------------------------------------------------------------


async def test_non_owner_cannot_chunk_or_commit_404(seed, db_session, attach_root):
    """AC-5(vi): a DIFFERENT actor (even one WITH attachment.add) → 404, no existence leak."""
    await _add_ticket(db_session, seed.board, seed.pm)
    qa = await _add_actor(db_session, seed.board, "qa", "QA")  # holds attachment.add
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    await append_chunk(
        db_session, actor=seed.backend, upload_id=str(upload.id), seq=0, data_b64=_b64(b"data")
    )
    with pytest.raises(NotFound):
        await append_chunk(
            db_session, actor=qa, upload_id=str(upload.id), seq=1, data_b64=_b64(b"more")
        )
    with pytest.raises(NotFound):
        await commit_upload_session(db_session, actor=qa, upload_id=str(upload.id))


async def test_unknown_upload_id_404(seed, db_session, attach_root):
    """A malformed / unknown upload_id resolves to the same 404 (no 500)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    with pytest.raises(NotFound):
        await append_chunk(
            db_session, actor=seed.backend, upload_id="not-a-uuid", seq=0, data_b64=_b64(b"x")
        )


# ---------------------------------------------------------------------------
# commit edge cases: empty session, sha256 mismatch/match
# ---------------------------------------------------------------------------


async def test_commit_empty_session_409(seed, db_session, attach_root):
    """A commit with no chunks appended → 409 (nothing to finalize)."""
    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    with pytest.raises(AttachmentUploadInvalid):
        await commit_upload_session(db_session, actor=seed.backend, upload_id=str(upload.id))
    assert await _count_sessions(db_session) == 1  # preserved


async def test_commit_sha256_mismatch_409_preserves_session_no_attachment(
    seed, db_session, attach_root
):
    """A wrong client sha256 → 409, session PRESERVED, and NO bogus attachment created."""
    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    await append_chunk(
        db_session, actor=seed.backend, upload_id=str(upload.id), seq=0, data_b64=_b64(b"payload")
    )
    with pytest.raises(AttachmentUploadInvalid):
        await commit_upload_session(
            db_session, actor=seed.backend, upload_id=str(upload.id), sha256="deadbeef"
        )
    assert await _count_sessions(db_session) == 1  # kept for inspection/retry
    assert await _count_attachments(db_session) == 0  # no bogus row
    assert _staging_path_for(upload.staging_key).exists()  # staging kept

    # A correct sha256 then commits cleanly.
    good = hashlib.sha256(b"payload").hexdigest()
    att = await commit_upload_session(
        db_session, actor=seed.backend, upload_id=str(upload.id), sha256=good
    )
    assert att.checksum_sha256 == good
    assert await _count_sessions(db_session) == 0


# ---------------------------------------------------------------------------
# (vii) GC sweep of an abandoned/expired session (row + staging blob)
# ---------------------------------------------------------------------------


async def test_expired_session_gc_swept(seed, db_session, attach_root, monkeypatch):
    """AC-5(vii)/AC-3: expire_upload_sessions removes the row AND unlinks the staging blob."""

    @asynccontextmanager
    async def _fake_sessionlocal() -> AsyncIterator[AsyncSession]:
        yield db_session  # borrowed — conftest owns the lifecycle

    monkeypatch.setattr(attachment_uploads, "SessionLocal", _fake_sessionlocal)

    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    await append_chunk(
        db_session,
        actor=seed.backend,
        upload_id=str(upload.id),
        seq=0,
        data_b64=_b64(b"stale-bytes"),
    )
    staging = _staging_path_for(upload.staging_key)
    assert staging.exists()

    # Backdate the session past its TTL, then sweep.
    row = (
        await db_session.execute(
            select(AttachmentUploadSession).where(AttachmentUploadSession.id == upload.id)
        )
    ).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(hours=2)
    await db_session.commit()

    swept = await attachment_uploads.expire_upload_sessions()
    assert swept == 1
    assert await _count_sessions(db_session) == 0
    assert not staging.exists()


async def test_gc_noop_when_nothing_expired(seed, db_session, attach_root, monkeypatch):
    """A live (unexpired) session is left untouched by the sweep."""

    @asynccontextmanager
    async def _fake_sessionlocal() -> AsyncIterator[AsyncSession]:
        yield db_session

    monkeypatch.setattr(attachment_uploads, "SessionLocal", _fake_sessionlocal)

    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    assert await attachment_uploads.expire_upload_sessions() == 0
    assert await _count_sessions(db_session) == 1
    assert upload.id is not None


async def test_expired_session_rejected_inline_and_aborted(seed, db_session, attach_root):
    """Belt-and-suspenders: a chunk on an already-expired session → 404 AND aborts it."""
    await _add_ticket(db_session, seed.board, seed.pm)
    upload = await begin_upload_session(
        db_session, actor=seed.backend, ticket_id="PH-1", filename="e.txt"
    )
    await append_chunk(
        db_session, actor=seed.backend, upload_id=str(upload.id), seq=0, data_b64=_b64(b"x")
    )
    staging = _staging_path_for(upload.staging_key)
    row = (
        await db_session.execute(
            select(AttachmentUploadSession).where(AttachmentUploadSession.id == upload.id)
        )
    ).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    with pytest.raises(NotFound):
        await append_chunk(
            db_session, actor=seed.backend, upload_id=str(upload.id), seq=1, data_b64=_b64(b"y")
        )
    assert await _count_sessions(db_session) == 0
    assert not staging.exists()


# ---------------------------------------------------------------------------
# Tool registration + AC-4 description refresh
# ---------------------------------------------------------------------------


def test_chunked_tools_registered():
    """The three tools are in the dispatch model map AND the advertised TOOLS catalog."""
    from app.mcp.server import (
        _TOOL_INPUT_MODELS,
        TOOLS,
        AddAttachmentBeginInput,
        AddAttachmentChunkInput,
        AddAttachmentCommitInput,
    )

    assert _TOOL_INPUT_MODELS["add_attachment_begin"] is AddAttachmentBeginInput
    assert _TOOL_INPUT_MODELS["add_attachment_chunk"] is AddAttachmentChunkInput
    assert _TOOL_INPUT_MODELS["add_attachment_commit"] is AddAttachmentCommitInput
    names = {t.name for t in TOOLS}
    assert {"add_attachment_begin", "add_attachment_chunk", "add_attachment_commit"} <= names
    for t in TOOLS:
        if t.name.startswith("add_attachment_"):
            assert t.permission == "attachment.add"


def test_ac4_content_description_points_to_chunked_not_rest():
    """AC-4: add_attachment_content drops 'MUST use REST' and points to the chunked path."""
    from app.mcp.server import TOOLS

    desc = next(t.description for t in TOOLS if t.name == "add_attachment_content")
    assert "REST multipart upload instead" not in desc
    assert "add_attachment_begin" in desc
    assert "never raw REST" in desc


def test_chunked_tools_advertised_in_tools_list():
    """The three tools carry a JSON input schema in the MCP tools/list payload."""
    from app.mcp.server import _build_mcp_tool_list

    listed = {t["name"]: t for t in _build_mcp_tool_list()}
    for name in ("add_attachment_begin", "add_attachment_chunk", "add_attachment_commit"):
        assert name in listed
        assert listed[name]["inputSchema"]["type"] == "object"


def test_upload_invalid_error_detail_carries_expected_seq():
    """The MCP error flattener surfaces expected_seq so an agent can resume."""
    from app.mcp.server import _domain_error_detail

    detail = _domain_error_detail(AttachmentUploadInvalid("gap", expected_seq=3))
    assert detail["error"] == "attachment_upload_invalid"
    assert detail["expected_seq"] == 3


# ---------------------------------------------------------------------------
# Migration round-trip + single head
# ---------------------------------------------------------------------------


def _load_upload_migration():
    """Import the PH-341 migration module by file path (versions/ isn't a package)."""
    path = (
        Path(__file__).resolve().parents[1]
        / "app" / "db" / "migrations" / "versions"
        / "20260831_0021_ph_341_attachment_upload_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("ph341_migration_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upload_sessions_migration_round_trip_and_single_head(tmp_path):
    """The migration creates/drops the table cleanly and the chain has exactly one head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _load_upload_migration()
    assert module.revision == "ph341uploadsessions"
    assert module.down_revision == "ph338boardsummary"

    migrations_dir = Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single alembic head, got {heads}"
    assert script.get_revision("ph341uploadsessions") is not None

    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    original_op = module.op
    try:
        with engine.connect() as conn:
            module.op = Operations(MigrationContext.configure(conn))
            module.upgrade()
            conn.commit()

            cols = {c["name"] for c in inspect(conn).get_columns("attachment_upload_sessions")}
            assert {
                "id", "ticket_id", "author_id", "filename", "content_type", "kind",
                "run_id", "phase", "staging_key", "bytes_received", "next_seq",
                "declared_size", "expires_at", "created_at", "updated_at",
            } <= cols
            index_names = {
                ix["name"] for ix in inspect(conn).get_indexes("attachment_upload_sessions")
            }
            assert "ix_attachment_upload_sessions_expires_at" in index_names

            module.downgrade()
            conn.commit()
            assert not inspect(conn).has_table("attachment_upload_sessions")
    finally:
        module.op = original_op
        engine.dispose()
