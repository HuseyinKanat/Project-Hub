"""PH-296: ticket evidence attachment service + content-serving tests.

Service-layer tests exercise the RBAC gate, checksum/size accounting, the
size/type rejections (413/415) with partial cleanup, the zero-copy ingest
traversal guard, persistence + audit history, and run_id provenance. One
TestClient test drives the byte-serving route to prove Range (206) support and
the security headers — auth is injected via ``dependency_overrides`` because the
seed tokens are fakes (``token_hash="x"`` never verifies).
"""

import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AttachmentSourceInvalid,
    PayloadTooLarge,
    PermissionDenied,
    UnsupportedMediaType,
)
from app.db.models import Actor, Attachment, Board, BoardMembership, Ticket, TicketHistory
from app.main import app
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
