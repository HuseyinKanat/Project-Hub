"""PH-296 QA verification tests — gaps the reviewer flagged for QA closure.

Two behaviours the existing ``test_attachments.py`` suite left uncovered:

  1. **Symlink-escape ingest reject.** ``test_attachments.py`` proves the string
     traversal guards (``..`` / outside-root / non-absolute) but NOT a symlink that
     lives *inside* the mount yet ``resolve()``s to a target *outside* it. This is
     the defence ``_resolve_source_under_repos`` adds with its ``.resolve()`` +
     parent check; here we exercise it with a real on-disk symlink.

  2. **Real-token content-route auth.** The committed Range test injects the actor
     via ``dependency_overrides[_actor_for_content]`` — so it never exercises the
     bespoke ``?token=`` / ``Authorization: Bearer`` resolver against a real hashed
     token. These tests mint an actor with a genuine ``hash_token`` hash and drive
     the route with the raw token (valid / invalid / missing), overriding ONLY the
     DB-session dependency (in-memory sqlite wiring), never the authenticator.
"""

import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import AttachmentSourceInvalid
from app.core.security import hash_token
from app.db.models import Actor, Attachment, Board, BoardMembership, Ticket
from app.db.session import get_db_session
from app.main import app
from app.services.attachments import create_attachment, ingest_from_source_path

# ---------------------------------------------------------------------------
# Helpers / fixtures (self-contained — no cross-test-module imports)
# ---------------------------------------------------------------------------


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


async def _add_actor_with_token(
    session: AsyncSession, board: Board, role: str, name: str, raw_token: str
) -> Actor:
    """Create an actor whose ``token_hash`` is a genuine bcrypt hash of ``raw_token``.

    Low rounds keep the hash fast; ``verify_token`` self-describes rounds so the
    real ``get_actor_from_token`` bcrypt check still validates it.
    """
    actor = Actor(
        kind="agent",
        display_name=name,
        token_hash=hash_token(raw_token, rounds=4),
        is_active=True,
    )
    session.add(actor)
    await session.flush()
    session.add(BoardMembership(board_id=board.id, actor_id=actor.id, role=role))
    await session.commit()
    return (
        await session.execute(
            select(Actor).where(Actor.id == actor.id).options(selectinload(Actor.memberships))
        )
    ).scalar_one()


def _stream(data: bytes):
    return lambda: io.BytesIO(data)


def _files_under(root) -> list:
    return [p for p in root.rglob("*") if p.is_file()]


@pytest.fixture
def attach_root(tmp_path, monkeypatch):
    settings = get_settings()
    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(settings, "attachments_root", str(root))
    return root


# ---------------------------------------------------------------------------
# 1. Symlink-escape ingest reject
# ---------------------------------------------------------------------------


async def test_ingest_rejects_symlink_escaping_repos_root(
    seed, db_session, attach_root, tmp_path, monkeypatch
):
    """A symlink *inside* the mount that resolves *outside* it must be rejected.

    Authorization passes (seed.backend holds attachment.add), so the request
    reaches the path validator — proving the reject is the symlink-escape guard,
    not an RBAC denial. Nothing lands on disk.
    """
    settings = get_settings()
    repos = tmp_path / "repos"
    repos.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("host secret outside the mount\n")

    # A symlink that lives under repos_root but points to a real file outside it.
    evil = repos / "evidence.png"
    evil.symlink_to(secret)

    # host_home == repos_root → to_container_path is identity; the escape must be
    # caught by the post-resolve() parent check, not the string guards.
    monkeypatch.setattr(settings, "host_home", str(repos))
    monkeypatch.setattr(settings, "repos_root", str(repos))

    await _add_ticket(db_session, seed.board, seed.pm)

    with pytest.raises(AttachmentSourceInvalid):
        await ingest_from_source_path(
            db_session,
            actor=seed.backend,
            ticket_id="PH-1",
            source_path=str(evil),
        )

    # Rejected before any blob was written, and no row committed.
    assert _files_under(attach_root) == []
    count = (
        await db_session.execute(select(func.count()).select_from(Attachment))
    ).scalar_one()
    assert count == 0


async def test_ingest_accepts_regular_file_under_root(
    seed, db_session, attach_root, tmp_path, monkeypatch
):
    """Control: a real regular file under the mount ingests fine (guards not over-broad)."""
    settings = get_settings()
    repos = tmp_path / "repos"
    repos.mkdir()
    good = repos / "shot.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload-bytes")
    monkeypatch.setattr(settings, "host_home", str(repos))
    monkeypatch.setattr(settings, "repos_root", str(repos))

    await _add_ticket(db_session, seed.board, seed.pm)
    att = await ingest_from_source_path(
        db_session, actor=seed.backend, ticket_id="PH-1", source_path=str(good)
    )
    assert att.source == "agent"
    assert att.content_type == "image/png"  # guessed from .png suffix
    assert att.filename == "shot.png"


# ---------------------------------------------------------------------------
# 2. Real-token content-route auth (no _actor_for_content override)
# ---------------------------------------------------------------------------


async def test_content_route_real_token_auth_paths(seed, db_session, attach_root):
    """?token= / Bearer header resolve a REAL hashed token; bad/missing → 403.

    Only ``get_db_session`` is overridden (sqlite wiring). The authenticator
    (``_actor_for_content`` → ``get_actor_from_token``) runs for real, so this
    covers what the dependency-override Range test cannot.
    """
    raw_token = "qa-real-secret-token-abc123"
    reader = await _add_actor_with_token(
        db_session, seed.board, "qa", "QA Reader", raw_token
    )

    await _add_ticket(db_session, seed.board, seed.pm)
    data = b"evidence-body-" * 40
    att = await create_attachment(
        db_session,
        actor=reader,
        ticket_id="PH-1",
        filename="clip.txt",
        content_type="text/plain",
        open_stream=_stream(data),
    )

    async def _fake_session():
        yield db_session

    app.dependency_overrides[get_db_session] = _fake_session
    try:
        client = TestClient(app, raise_server_exceptions=True)
        url = f"/api/tickets/PH-1/attachments/{att.id}/content"

        # valid ?token= → 200 + secure headers + exact bytes
        ok = client.get(url, params={"token": raw_token})
        assert ok.status_code == 200, ok.text
        assert ok.headers.get("x-content-type-options") == "nosniff"
        assert ok.headers.get("accept-ranges") == "bytes"
        assert ok.content == data
        assert hashlib.sha256(ok.content).hexdigest() == att.checksum_sha256

        # valid Authorization: Bearer header → 200
        hdr = client.get(url, headers={"Authorization": f"Bearer {raw_token}"})
        assert hdr.status_code == 200, hdr.text

        # invalid token → 403 (get_actor_from_token returns None)
        bad = client.get(url, params={"token": "not-a-real-token"})
        assert bad.status_code == 403, bad.text
        assert bad.json()["required"] == "valid_bearer_token"

        # missing token AND header → 403
        none = client.get(url)
        assert none.status_code == 403, none.text
        assert none.json()["required"] == "authenticated_actor"

        # valid token + Range → 206 with Content-Range
        ranged = client.get(
            url, params={"token": raw_token}, headers={"Range": "bytes=0-9"}
        )
        assert ranged.status_code == 206, ranged.text
        assert ranged.headers.get("content-range") == f"bytes 0-9/{len(data)}"
        assert len(ranged.content) == 10
    finally:
        app.dependency_overrides.clear()
