"""Ticket evidence attachment service (PH-296).

Mirrors the ``add_comment`` service pipeline (require_permission → persist →
write_history → commit → publish_ticket_event) but the payload is a file on
disk instead of an inline body. Two ingest paths converge on the same core
``_persist_attachment``:

  * REST multipart (``create_attachment``) — a human uploads bytes; the caller
    hands us a sync file-like (``UploadFile.file``).
  * MCP zero-copy (``ingest_from_source_path``) — an agent names a host path
    that is visible in-container under the read-only ``/repos`` mount; we
    validate + stat it, then stream it into storage without a round-trip.

Storage layout: ``attachments_root/{id[:2]}/{id}`` — a server-generated UUID
shard. The client filename NEVER enters the path (traversal-proof by
construction). All blocking file IO is offloaded via ``asyncio.to_thread`` so
the event loop is never parked on disk (matches ``git/reader`` idiom).
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AttachmentPhaseInvalid,
    AttachmentSourceInvalid,
    NotFound,
    PayloadTooLarge,
    UnsupportedMediaType,
)
from app.core.permissions import require_permission
from app.db.models import Actor, Attachment, Ticket
from app.events import publish_ticket_event
from app.services.boards import parse_uuid
from app.services.history import write_history
from app.services.repo_paths import RepoPathError, to_container_path
from app.services.tickets import get_ticket

_PERM_ATTACHMENT_ADD = "attachment.add"
_PERM_TICKET_READ = "ticket.read"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_CHUNK_SIZE = 1024 * 1024  # 1 MiB — streamed hash + size accounting granularity

# PH-311: attachment ``phase`` tag — a free slug (like ``kind``), only its SHAPE is
# enforced, not a closed enum. Convention (NOT validated as a set): repro |
# iter-<n>-fail | iter-<n>-pass | before | after. ``fullmatch`` anchors both ends
# strictly (no trailing-newline loophole that a bare ``$`` would admit).
_PHASE_MAX_LEN = 40
_PHASE_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# A zero-arg factory returning a fresh sync binary reader positioned at 0. Using
# a factory (not an open handle) lets the blocking ``open()`` itself run inside
# the worker thread for the ingest path.
StreamFactory = Callable[[], BinaryIO]


def _normalize_content_type(content_type: str | None) -> str:
    """Strip parameters (``; charset=...``) and lower-case; empty → octet-stream."""
    if not content_type:
        return _DEFAULT_CONTENT_TYPE
    return content_type.split(";", 1)[0].strip().lower() or _DEFAULT_CONTENT_TYPE


def _validate_phase(phase: str | None) -> str | None:
    """Validate an optional attachment ``phase`` tag (PH-311).

    ``None``/omitted is valid → returned unchanged (passthrough; keeps PH-296/297
    callers working). A provided value must be a slug matching
    ``^[a-z0-9]+(?:-[a-z0-9]+)*$`` and be ``<= 40`` chars — only the SHAPE is
    enforced, so convention-free-but-valid slugs (e.g. ``smoke-check``) are
    accepted. Invalid → :class:`AttachmentPhaseInvalid` (422), raised by the
    caller BEFORE any blob/row/event is written (no side effects on rejection).
    """
    if phase is None:
        return None
    if len(phase) > _PHASE_MAX_LEN or not _PHASE_SLUG_RE.fullmatch(phase):
        raise AttachmentPhaseInvalid(
            f"phase {phase!r} must be a slug matching "
            f"^[a-z0-9]+(?:-[a-z0-9]+)*$ and be <= {_PHASE_MAX_LEN} chars",
            phase=phase,
        )
    return phase


def _storage_key_for(attachment_id: uuid.UUID) -> str:
    """UUID shard key: ``{id[:2]}/{id}`` — bounded fan-out, no client input."""
    key = str(attachment_id)
    return f"{key[:2]}/{key}"


def _attachments_root() -> Path:
    return Path(get_settings().attachments_root)


def _resolve_storage_key(storage_key: str) -> Path:
    """Map a storage_key to its absolute path, asserting it stays under the root.

    Defense-in-depth: storage_key is server-generated, but we still verify the
    resolved path does not escape ``attachments_root`` before opening it.
    """
    root = _attachments_root().resolve()
    dest = (root / storage_key).resolve()
    if dest != root and root not in dest.parents:
        raise AttachmentSourceInvalid(f"storage_key {storage_key!r} escapes attachments_root")
    return dest


def resolve_attachment_path(attachment: Attachment) -> Path:
    """Absolute on-disk path of an attachment's blob (under attachments_root)."""
    return _resolve_storage_key(attachment.storage_key)


def _resolve_source_under_repos(source_path: str) -> Path:
    """Validate an MCP ingest source path against the read-only /repos mount.

    ``source_path`` is a HOST path (agents speak host paths); ``to_container_path``
    translates it to the in-container ``/repos/...`` path AND rejects non-absolute
    paths, ``..`` traversal, and anything outside the mounted host home. We then
    ``resolve()`` (symlink escape guard) and require a real regular file.
    """
    settings = get_settings()
    try:
        container = to_container_path(source_path)
    except RepoPathError as exc:
        raise AttachmentSourceInvalid(str(exc)) from exc
    root = Path(settings.repos_root).resolve()
    candidate = Path(container).resolve()
    if candidate != root and root not in candidate.parents:
        raise AttachmentSourceInvalid(f"{source_path!r} escapes the /repos mount")
    if not candidate.is_file():
        raise AttachmentSourceInvalid(f"{source_path!r} is not an existing file")
    return candidate


def _write_stream_sync(
    open_stream: StreamFactory,
    dest: Path,
    max_bytes: int,
    close_after: bool,
) -> tuple[str, int]:
    """Copy a reader → ``dest`` in chunks, computing sha256 + byte count.

    Enforces ``max_bytes`` mid-stream: on overflow the partial file is removed
    and :class:`PayloadTooLarge` is raised. Any other error also removes the
    partial. Runs entirely inside a worker thread (blocking IO).
    """
    hasher = hashlib.sha256()
    total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    reader = open_stream()
    try:
        try:
            with open(dest, "wb") as out:
                while True:
                    chunk = reader.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise PayloadTooLarge(limit=max_bytes)
                    hasher.update(chunk)
                    out.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)  # never leave a partial blob behind
            raise
    finally:
        if close_after:
            reader.close()
    return hasher.hexdigest(), total


async def _persist_attachment(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket: Ticket,
    filename: str,
    content_type: str,
    open_stream: StreamFactory,
    close_after: bool,
    kind: str,
    source: str,
    run_id: str | None,
    phase: str | None,
) -> Attachment:
    """Shared persist core: validate type+phase → stream to disk → row + history + event.

    Assumes the caller already loaded ``ticket`` (with board) and enforced
    ``attachment.add``. Both the content-type allowlist and the ``phase`` slug are
    validated HERE, before ``_write_stream_sync`` touches disk, so a rejected
    request leaves no blob, no row, and no ``attachment_added`` event (PH-311 AC5).
    """
    settings = get_settings()
    normalized_type = _normalize_content_type(content_type)
    if normalized_type not in settings.attachment_allowed_types_set:
        raise UnsupportedMediaType(content_type=normalized_type)
    phase = _validate_phase(phase)  # pre-write gate: invalid phase → 422, no side effect

    attachment_id = uuid.uuid4()
    storage_key = _storage_key_for(attachment_id)
    dest = _resolve_storage_key(storage_key)

    checksum, size = await asyncio.to_thread(
        _write_stream_sync, open_stream, dest, settings.attachment_max_bytes, close_after
    )

    attachment = Attachment(
        id=attachment_id,
        ticket_id=ticket.id,
        author_id=actor.id,
        filename=filename,
        content_type=normalized_type,
        size_bytes=size,
        checksum_sha256=checksum,
        storage_key=storage_key,
        kind=kind,
        source=source,
        run_id=run_id,
        phase=phase,
    )
    try:
        session.add(attachment)
        await session.flush()
        history = await write_history(
            session,
            ticket_id=ticket.id,
            actor_id=actor.id,
            event_type="attachment_added",
            new_value={"attachment_id": str(attachment.id), "filename": filename},
        )
        await session.commit()
    except BaseException:
        # DB write failed after the blob landed — remove the orphan file.
        await asyncio.to_thread(dest.unlink, True)
        raise

    # Publish after commit (mirrors add_comment: reload ticket for the event).
    ticket_for_event = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket_for_event, actor)
    await session.commit()

    result = await session.execute(
        select(Attachment)
        .where(Attachment.id == attachment.id)
        .options(selectinload(Attachment.author))
    )
    return result.scalar_one()


async def create_attachment(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    filename: str,
    content_type: str,
    open_stream: StreamFactory,
    close_after: bool = True,
    kind: str = "other",
    source: str = "human",
    run_id: str | None = None,
    phase: str | None = None,
) -> Attachment:
    """Create an attachment from a caller-provided byte stream (REST multipart)."""
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_ADD, resource=ticket)
    return await _persist_attachment(
        session,
        actor=actor,
        ticket=ticket,
        filename=filename,
        content_type=content_type,
        open_stream=open_stream,
        close_after=close_after,
        kind=kind,
        source=source,
        run_id=run_id,
        phase=phase,
    )


async def ingest_from_source_path(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    source_path: str,
    kind: str = "other",
    source: str = "agent",
    run_id: str | None = None,
    phase: str | None = None,
    filename: str | None = None,
) -> Attachment:
    """Zero-copy ingest a file already visible under the read-only /repos mount.

    Order is deliberate: authorize FIRST (don't leak filesystem existence to an
    unauthorized caller), THEN validate the path, size-gate via ``stat``, and
    delegate the streaming persist to :func:`_persist_attachment`.
    """
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_ADD, resource=ticket)

    resolved = _resolve_source_under_repos(source_path)
    settings = get_settings()
    size = await asyncio.to_thread(lambda: resolved.stat().st_size)
    if size > settings.attachment_max_bytes:
        raise PayloadTooLarge(limit=settings.attachment_max_bytes)

    resolved_name = filename or resolved.name
    guessed_type, _ = mimetypes.guess_type(resolved_name)
    return await _persist_attachment(
        session,
        actor=actor,
        ticket=ticket,
        filename=resolved_name,
        content_type=guessed_type or _DEFAULT_CONTENT_TYPE,
        open_stream=lambda: open(resolved, "rb"),
        close_after=True,
        kind=kind,
        source=source,
        run_id=run_id,
        phase=phase,
    )


async def list_attachments(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
) -> list[Attachment]:
    """List a ticket's attachments (metadata), oldest → newest. Gated on ticket.read."""
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_TICKET_READ, resource=ticket)
    result = await session.execute(
        select(Attachment)
        .where(Attachment.ticket_id == ticket.id)
        .options(selectinload(Attachment.author))
        .order_by(Attachment.created_at.asc())
    )
    return list(result.scalars())


async def get_attachment(
    session: AsyncSession,
    *,
    actor: Actor,
    attachment_id: str,
) -> Attachment:
    """Fetch one attachment (with author + ticket + board) gated on ticket.read."""
    attachment = (
        await session.execute(
            select(Attachment)
            .where(Attachment.id == parse_uuid(attachment_id))
            .options(
                selectinload(Attachment.author),
                selectinload(Attachment.ticket).selectinload(Ticket.board),
            )
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFound("attachment")
    require_permission(
        actor, attachment.ticket.board, _PERM_TICKET_READ, resource=attachment.ticket
    )
    return attachment
