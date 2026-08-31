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
import base64
import binascii
import hashlib
import io
import logging
import mimetypes
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AttachmentContentInvalid,
    AttachmentMetadataInvalid,
    AttachmentPhaseInvalid,
    AttachmentSourceInvalid,
    AttachmentUploadInvalid,
    NotFound,
    PayloadTooLarge,
    UnsupportedMediaType,
)
from app.core.permissions import require_permission
from app.db.models import Actor, Attachment, AttachmentUploadSession, Ticket
from app.events import publish_ticket_event
from app.services.boards import parse_uuid
from app.services.history import write_history
from app.services.repo_paths import RepoPathError, to_container_path
from app.services.tickets import get_ticket

_PERM_ATTACHMENT_ADD = "attachment.add"
_PERM_ATTACHMENT_UPDATE = "attachment.update"  # PH-313
_PERM_ATTACHMENT_DELETE = "attachment.delete"  # PH-313
_PERM_TICKET_READ = "ticket.read"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_CHUNK_SIZE = 1024 * 1024  # 1 MiB — streamed hash + size accounting granularity

# PH-341: chunked-upload staging area — a subdir on the SAME attachments volume as
# the final blobs. Final storage_key shards are 2-hex-char dirs ({id[:2]}/{id}); a
# leading-dot ".uploads" is NOT a hex shard, so a staging path can never collide
# with (or be resolved by the REST content route into) a committed attachment.
_STAGING_SUBDIR = ".uploads"

# PH-313: metadata-update field constraints. Only these three columns are
# mutable; the caps mirror the REAL column widths (kind VARCHAR(20),
# run_id VARCHAR(120)) so an overflow is a clean 422 (AttachmentMetadataInvalid)
# instead of a DB-level 500. ``phase`` reuses the shared _validate_phase gate.
_UPDATABLE_FIELDS = frozenset({"phase", "kind", "run_id"})
_KIND_MAX_LEN = 20
_RUN_ID_MAX_LEN = 120

logger = logging.getLogger(__name__)

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


def _validate_metadata_lengths(kind: str | None, run_id: str | None) -> None:
    """Shared kind/run_id length gate — raises 422 (never a DB-level 500) on overflow.

    The single source of truth for the ``kind`` VARCHAR(20) / ``run_id`` VARCHAR(120)
    column-width caps (PH-315). Both the add-path (:func:`_persist_attachment`) and
    the update-path (:func:`_validate_update_fields`) call it, so the cap lives in
    EXACTLY one place. ``None`` is a passthrough (absent/nullable) — callers pass the
    single field they are checking (e.g. ``_validate_metadata_lengths(kind, None)``)
    so the update-path per-field error precedence is preserved. ``kind`` is checked
    before ``run_id`` (matching the update-path field order). Raises
    :class:`AttachmentMetadataInvalid` with ``.field`` naming the offending column.
    """
    if kind is not None and len(kind) > _KIND_MAX_LEN:
        raise AttachmentMetadataInvalid(
            f"kind must be <= {_KIND_MAX_LEN} chars (got {len(kind)})", field="kind"
        )
    if run_id is not None and len(run_id) > _RUN_ID_MAX_LEN:
        raise AttachmentMetadataInvalid(
            f"run_id must be <= {_RUN_ID_MAX_LEN} chars (got {len(run_id)})",
            field="run_id",
        )


def _validate_update_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a PH-313 metadata-update map, returning the columns to assign.

    Key-PRESENCE is load-bearing (the caller strips absent keys via
    ``exclude_unset`` on REST / passes the dict verbatim on MCP): a key present
    with ``None`` CLEARS the column (phase / run_id are nullable) — ``kind`` is
    NOT nullable so ``kind=None`` is a 422. An EMPTY map (nothing to change) or
    an UNKNOWN key is a 422. Length caps mirror the real column widths so an
    overflow surfaces as a clean 422, never a DB-level 500. Runs BEFORE any row
    mutation, so a rejected update has no side effect. ``phase`` is delegated to
    the shared :func:`_validate_phase` (its own AttachmentPhaseInvalid on a bad
    slug); the other fields raise :class:`AttachmentMetadataInvalid`.
    """
    if not fields:
        raise AttachmentMetadataInvalid("no metadata fields provided to update", field=None)
    unknown = sorted(set(fields) - _UPDATABLE_FIELDS)
    if unknown:
        raise AttachmentMetadataInvalid(
            f"unknown metadata field(s): {', '.join(unknown)} "
            f"(updatable: {', '.join(sorted(_UPDATABLE_FIELDS))})",
            field=unknown[0],
        )

    to_assign: dict[str, Any] = {}
    if "phase" in fields:
        # None clears the phase (AC11); a bad slug → AttachmentPhaseInvalid (AC3).
        to_assign["phase"] = _validate_phase(fields["phase"])
    if "kind" in fields:
        kind = fields["kind"]
        if not isinstance(kind, str):  # column is NOT nullable
            raise AttachmentMetadataInvalid("kind must be a non-null string", field="kind")
        _validate_metadata_lengths(kind, None)  # shared length cap (PH-315)
        to_assign["kind"] = kind
    if "run_id" in fields:
        run_id = fields["run_id"]
        if run_id is not None:
            if not isinstance(run_id, str):
                raise AttachmentMetadataInvalid(
                    "run_id must be a string or null", field="run_id"
                )
            _validate_metadata_lengths(None, run_id)  # shared length cap (PH-315)
        to_assign["run_id"] = run_id  # None clears (AC11)
    return to_assign


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
    """Shared persist core: validate type+phase+lengths → stream to disk → row + history + event.

    Assumes the caller already loaded ``ticket`` (with board) and enforced
    ``attachment.add``. The content-type allowlist, the ``phase`` slug, AND the
    ``kind``/``run_id`` length caps are validated HERE, before ``_write_stream_sync``
    touches disk, so a rejected request leaves no blob, no row, and no
    ``attachment_added`` event (PH-311 AC5 / PH-315 add/update parity).
    """
    settings = get_settings()
    normalized_type = _normalize_content_type(content_type)
    if normalized_type not in settings.attachment_allowed_types_set:
        raise UnsupportedMediaType(content_type=normalized_type)
    phase = _validate_phase(phase)  # pre-write gate: invalid phase → 422, no side effect
    _validate_metadata_lengths(kind, run_id)  # pre-write gate: overflow → 422, no side effect

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


async def ingest_from_content(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    filename: str,
    content_b64: str,
    kind: str = "other",
    source: str = "agent",
    run_id: str | None = None,
    phase: str | None = None,
) -> Attachment:
    """Ingest an attachment from INLINE base64 content — PH-318 (byte-carrying MCP path).

    Sibling of :func:`ingest_from_source_path` for a REMOTE agent whose evidence is
    NOT visible under the read-only ``/repos`` mount, so it ships the bytes inline as
    base64. Order mirrors the ingest path — authorize FIRST (a missing-cap caller
    never triggers a decode and cannot probe the ticket), THEN a two-stage size gate
    around a *validated* decode, THEN delegate to the SAME :func:`_persist_attachment`
    core (allowlist / phase / kind+run_id gates + row + a single ``attachment_added``
    history + event come for free — zero divergent write path).

    Dedicated cap ``attachment_mcp_max_bytes`` (8 MiB), distinct from the 25 MiB disk
    cap: the JSON-RPC body is fully buffered by ``request.json()`` before dispatch, so
    a low cap bounds transient memory (rationale: PH-318 technical_depth). Gate order
    (403 precedes any decode/persist):

      1. ``get_ticket`` (404) → ``require_permission(attachment.add)`` (403) — BEFORE
         any decode/persist, so an unauthorized caller's bytes are never decoded.
      2. Pre-decode fast-fail: the base64 encoding of *N* bytes is exactly
         ``4*ceil(N/3)`` chars, so any string longer than the ceiling for ``cap``
         bytes MUST decode to more than ``cap`` — reject 413 WITHOUT decoding (no
         decoded buffer allocated).
      3. ``base64.b64decode(validate=True)`` — a malformed payload (bad padding,
         non-alphabet char, embedded whitespace/newline, non-ASCII string) →
         :class:`AttachmentContentInvalid` (422, never a 500).
      4. Post-decode authoritative cap on the REAL decoded byte length → 413.
      5. content-type guessed from ``filename`` (consistent with ``add_attachment``;
         the caller passes no content_type), then :func:`_persist_attachment`.
    """
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_ADD, resource=ticket)

    cap = get_settings().attachment_mcp_max_bytes
    # Pre-decode fast-fail (AC3): reject an over-ceiling string before allocating the
    # decoded buffer — the encoding of N bytes is exactly 4*ceil(N/3) chars, so a
    # longer string cannot possibly decode to <= cap. Loose upper bound on purpose;
    # the authoritative gate is the post-decode len(raw) check below.
    max_encoded = 4 * ((cap + 2) // 3)
    if len(content_b64) > max_encoded:
        raise PayloadTooLarge(limit=cap)

    try:
        raw = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        # binascii.Error is a ValueError subclass; both are caught so malformed
        # base64 (incl. a non-ASCII str) is a clean 422, never a 500. Agents MUST
        # send UNWRAPPED base64 — validate=True rejects embedded whitespace/newlines.
        raise AttachmentContentInvalid(f"content_b64 is not valid base64: {exc}") from exc

    if len(raw) > cap:  # authoritative cap on the real decoded length (AC2)
        raise PayloadTooLarge(limit=cap)

    guessed_type, _ = mimetypes.guess_type(filename)
    return await _persist_attachment(
        session,
        actor=actor,
        ticket=ticket,
        filename=filename,
        content_type=guessed_type or _DEFAULT_CONTENT_TYPE,
        open_stream=lambda: io.BytesIO(raw),
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


async def _load_ticket_scoped_attachment(
    session: AsyncSession, *, ticket: Ticket, attachment_id: str
) -> Attachment:
    """Load an attachment by ``id AND ticket_id`` or raise ``NotFound('attachment')``.

    Ticket-scoping (not a bare id lookup) is what makes a cross-ticket mutation a
    404 instead of touching another ticket's evidence (PH-313 AC10). A malformed
    ``attachment_id`` parses to ``None`` → the ``id == None`` predicate matches no
    row → the same 404 (no 500). Callers MUST have already authorized, so the
    absence of a row never leaks existence to an unauthorized caller (AC6).
    """
    attachment = (
        await session.execute(
            select(Attachment).where(
                Attachment.id == parse_uuid(attachment_id),
                Attachment.ticket_id == ticket.id,
            )
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise NotFound("attachment")
    return attachment


async def update_attachment(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    attachment_id: str,
    fields: Mapping[str, Any],
) -> Attachment:
    """Update ONLY an attachment's metadata (phase / kind / run_id) — PH-313.

    The blob is immutable: ``storage_key`` / ``filename`` / ``content_type`` /
    ``size_bytes`` / ``checksum_sha256`` are NEVER read or written (AC2). Order
    mirrors ``create_attachment``: authorize on ``attachment.update`` BEFORE the
    attachment lookup (a missing-cap caller cannot probe existence — AC6), then
    ticket-scoped load (wrong ticket → 404, AC10), then validate ``fields``
    BEFORE any assignment (invalid → 422 with no side effect, AC3), then
    assign-only-provided-columns → ``attachment_updated`` history (old→new) →
    commit → publish → commit, returning the reloaded row.
    """
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_UPDATE, resource=ticket)

    to_assign = _validate_update_fields(fields)  # pre-mutation gate (422 → no side effect)
    attachment = await _load_ticket_scoped_attachment(
        session, ticket=ticket, attachment_id=attachment_id
    )

    old_meta = {
        "phase": attachment.phase,
        "kind": attachment.kind,
        "run_id": attachment.run_id,
    }
    for column, value in to_assign.items():
        setattr(attachment, column, value)  # ONLY metadata columns — blob untouched

    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="attachment_updated",
        old_value=old_meta,
        new_value={
            "attachment_id": str(attachment.id),
            "filename": attachment.filename,
            "phase": attachment.phase,
            "kind": attachment.kind,
            "run_id": attachment.run_id,
        },
    )
    await session.commit()

    ticket_for_event = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket_for_event, actor)
    await session.commit()

    result = await session.execute(
        select(Attachment)
        .where(Attachment.id == attachment.id)
        .options(selectinload(Attachment.author))
    )
    return result.scalar_one()


async def delete_attachment(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Hard-delete an attachment (row + blob) — PH-313. NOT idempotent.

    DAR RBAC: only ``attachment.delete`` (qa + pm) may call this, checked BEFORE
    the attachment lookup (no existence leak — AC6). Ticket-scoped load → a
    wrong-ticket or already-deleted id is a 404 (AC5 second call, AC10). Tx order
    is deliberate: snapshot metadata + resolve the blob path BEFORE the row goes,
    then ``session.delete`` + ``attachment_deleted`` history → commit → publish;
    the blob is unlinked POST-commit as best-effort — an unlink failure is logged
    and NEVER raised (the row is already gone; an orphan blob is a GC-cron
    concern), so a filesystem hiccup can never turn a committed delete into a 500.
    """
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_DELETE, resource=ticket)

    attachment = await _load_ticket_scoped_attachment(
        session, ticket=ticket, attachment_id=attachment_id
    )

    # Snapshot audit metadata + resolve the blob path BEFORE the row is deleted.
    attachment_id_str = str(attachment.id)
    snapshot = {
        "attachment_id": attachment_id_str,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size_bytes": attachment.size_bytes,
        "checksum_sha256": attachment.checksum_sha256,
        "storage_key": attachment.storage_key,
        "kind": attachment.kind,
        "source": attachment.source,
        "run_id": attachment.run_id,
        "phase": attachment.phase,
    }
    blob_path = resolve_attachment_path(attachment)

    await session.delete(attachment)
    history = await write_history(
        session,
        ticket_id=ticket.id,
        actor_id=actor.id,
        event_type="attachment_deleted",
        old_value=snapshot,
    )
    await session.commit()

    ticket_for_event = await get_ticket(session, ticket.key)
    await publish_ticket_event(history, ticket_for_event, actor)
    await session.commit()

    # POST-commit best-effort blob unlink. Row is already gone → never surface a
    # filesystem error as a 500; log and move on (orphan swept by a GC cron).
    try:
        await asyncio.to_thread(blob_path.unlink, True)
    except OSError as exc:
        logger.warning("attachment %s blob unlink failed: %s", attachment_id_str, exc)

    return {"deleted": True, "id": attachment_id_str}


# ---------------------------------------------------------------------------
# PH-341: chunked MCP upload (add_attachment_begin / _chunk / _commit).
#
# Lets a REMOTE MCP-only agent (off the hub host, so add_attachment's /repos
# source_path is unavailable) stream evidence LARGER than the 8 MiB inline base64
# cap (add_attachment_content) up to the 25 MiB disk cap WITHOUT a raw REST call
# (mcp-discipline §2.9). Every byte stays inside an authenticated MCP tool call;
# the finalize re-streams the staged file through the SAME _persist_attachment
# core, so size/type/checksum/RBAC/row/history/event are shared verbatim — zero
# divergent write path. Design: ADR-0002 / PH-341 technical_depth.
# ---------------------------------------------------------------------------


def _staging_key_for(session_id: uuid.UUID) -> str:
    """Staging blob key: ``.uploads/{id[:2]}/{id}`` — server-generated, disjoint from
    the 2-hex-char final shards (so a staging path never collides with a committed
    attachment nor resolves via the REST content route)."""
    key = str(session_id)
    return f"{_STAGING_SUBDIR}/{key[:2]}/{key}"


def _staging_path_for(staging_key: str) -> Path:
    """Absolute staging path for a session, asserting it stays under attachments_root
    (reuses the same escape guard as final blobs)."""
    return _resolve_storage_key(staging_key)


def _append_chunk_sync(dest: Path, data: bytes) -> None:
    """Append one decoded chunk to the staging blob (creates the shard on first write).
    Runs inside a worker thread (blocking IO)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "ab") as out:
        out.write(data)


def _sha256_of_file_sync(path: Path) -> str:
    """Stream a file → hex sha256 (blocking; called via to_thread). Used for the
    OPTIONAL client-supplied integrity check at commit."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_expired(expires_at: datetime) -> bool:
    """True once ``expires_at`` is in the past. Tolerates a naive datetime (the SQLite
    test DB drops tz on a ``DateTime(timezone=True)`` column) by treating it as UTC,
    so the same guard is correct on Postgres (aware) and SQLite (naive)."""
    exp = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    return exp <= datetime.now(UTC)


async def unlink_staging_blob(path: Path) -> None:
    """Best-effort staging-blob unlink — logs (never raises) an OS error, mirroring
    ``delete_attachment``'s post-commit unlink. Shared by ``_abort_session`` and the
    GC cron so an abandoned/aborted session never outlives its staging bytes."""
    try:
        await asyncio.to_thread(path.unlink, True)  # missing_ok — begin-only session has no blob
    except OSError as exc:
        logger.warning("upload staging blob unlink failed: %s (%s)", path, exc)


async def _load_owned_session(
    session: AsyncSession,
    *,
    actor: Actor,
    upload_id: str,
    for_update: bool = False,
) -> AttachmentUploadSession:
    """Load a session by ``id AND author_id`` or raise ``NotFound('upload_session')``.

    Authorize-before-existence (PH-313 idiom): a wrong actor, a malformed id (parses
    to ``None`` → matches no row), or an unknown id all collapse to the SAME 404 — no
    existence leak, no ownership probe. Expiry is intentionally NOT filtered here so an
    expired-but-present session can be actively ABORTED by the caller (row + staging
    swept), matching the belt-and-suspenders GC. ``for_update`` serializes concurrent
    chunk writers on Postgres (no-op on SQLite); the ``seq == next_seq`` guard makes a
    duplicate/reordered chunk a clean 409 regardless.
    """
    stmt = select(AttachmentUploadSession).where(
        AttachmentUploadSession.id == parse_uuid(upload_id),
        AttachmentUploadSession.author_id == actor.id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound("upload_session")
    return row


async def _abort_session(session: AsyncSession, row: AttachmentUploadSession) -> None:
    """Drop a session row + unlink its staging blob (cumulative-cap breach / expired)."""
    staging_path = _staging_path_for(row.staging_key)
    await session.delete(row)
    await session.commit()
    await unlink_staging_blob(staging_path)


async def begin_upload_session(
    session: AsyncSession,
    *,
    actor: Actor,
    ticket_id: str,
    filename: str,
    kind: str = "other",
    run_id: str | None = None,
    phase: str | None = None,
    declared_size: int | None = None,
) -> AttachmentUploadSession:
    """Open an authorized chunked-upload session (``add_attachment_begin``).

    Gate order mirrors the other ingest paths — authorize FIRST (an unauthorized
    caller never creates a row nor probes the ticket), THEN the same content-type
    allowlist / phase / kind+run_id / declared-size validations that would run at
    commit, so a rejected ``begin`` leaves NO session row and NO staging blob. The
    ``content_type`` guessed here from ``filename`` is stored and replayed into
    ``_persist_attachment`` at commit (which re-derives + re-checks it authoritatively).
    """
    ticket = await get_ticket(session, ticket_id)
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_ADD, resource=ticket)

    settings = get_settings()
    guessed_type, _ = mimetypes.guess_type(filename)
    normalized_type = _normalize_content_type(guessed_type or _DEFAULT_CONTENT_TYPE)
    if normalized_type not in settings.attachment_allowed_types_set:
        raise UnsupportedMediaType(content_type=normalized_type)
    phase = _validate_phase(phase)  # pre-row gate: invalid phase → 422, no side effect
    _validate_metadata_lengths(kind, run_id)  # pre-row gate: overflow → 422, no side effect
    if declared_size is not None and declared_size > settings.attachment_max_bytes:
        raise PayloadTooLarge(limit=settings.attachment_max_bytes)

    session_id = uuid.uuid4()
    upload = AttachmentUploadSession(
        id=session_id,
        ticket_id=ticket.id,
        author_id=actor.id,
        filename=filename,
        content_type=normalized_type,
        kind=kind,
        run_id=run_id,
        phase=phase,
        staging_key=_staging_key_for(session_id),
        bytes_received=0,
        next_seq=0,
        declared_size=declared_size,
        expires_at=datetime.now(UTC)
        + timedelta(seconds=settings.attachment_upload_ttl_seconds),
    )
    session.add(upload)
    await session.commit()
    return upload


async def append_chunk(
    session: AsyncSession,
    *,
    actor: Actor,
    upload_id: str,
    seq: int,
    data_b64: str,
) -> AttachmentUploadSession:
    """Append one ``<= 8 MiB`` base64 chunk to a session (``add_attachment_chunk``).

    Gates (each rejecting BEFORE the staging file grows): owned+present (404, and an
    EXPIRED session is aborted → 404) → ``seq == next_seq`` (else 409 with
    ``expected_seq``) → validated base64 decode (422) → decoded ``<= attachment_mcp_max_bytes``
    (413) → cumulative ``<= attachment_max_bytes`` (413, and the session is ABORTED so no
    ``> 25 MiB`` staging can ever accumulate). Only then are the bytes appended and the
    ``next_seq``/``bytes_received`` accounting advanced.
    """
    settings = get_settings()
    row = await _load_owned_session(
        session, actor=actor, upload_id=upload_id, for_update=True
    )
    if _is_expired(row.expires_at):
        await _abort_session(session, row)
        raise NotFound("upload_session")

    if seq != row.next_seq:
        raise AttachmentUploadInvalid(
            f"out-of-order chunk: expected seq {row.next_seq}, got {seq}",
            expected_seq=row.next_seq,
        )

    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        # UNWRAPPED base64 required — validate=True rejects embedded whitespace/newlines.
        raise AttachmentContentInvalid(f"data_b64 is not valid base64: {exc}") from exc

    if len(raw) > settings.attachment_mcp_max_bytes:  # per-chunk peak-RAM bound
        raise PayloadTooLarge(limit=settings.attachment_mcp_max_bytes)

    if row.bytes_received + len(raw) > settings.attachment_max_bytes:
        # Cumulative breach — abort so a rogue/looping uploader can never stage > 25 MiB.
        await _abort_session(session, row)
        raise PayloadTooLarge(limit=settings.attachment_max_bytes)

    staging_path = _staging_path_for(row.staging_key)
    await asyncio.to_thread(_append_chunk_sync, staging_path, raw)
    row.next_seq += 1
    row.bytes_received += len(raw)
    await session.commit()
    return row


async def commit_upload_session(
    session: AsyncSession,
    *,
    actor: Actor,
    upload_id: str,
    sha256: str | None = None,
) -> Attachment:
    """Finalize a chunked upload (``add_attachment_commit``) → one Attachment.

    Loads the owned session (expired → abort+404), rejects an empty session (no
    chunks → 409), re-checks ``attachment.add`` (403), OPTIONALLY verifies a
    client-supplied ``sha256`` against the staged bytes BEFORE persisting (mismatch →
    409, session PRESERVED so the agent can inspect/retry — and no bogus attachment is
    created), then re-streams the staging file through the shared ``_persist_attachment``
    (25 MiB + allowlist + checksum + row + ``attachment_added`` history + event). On
    success the session row is dropped and the staging blob unlinked. Ordering is
    crash-safe: the attachment commits FIRST, so a crash before cleanup leaves only an
    orphan session row + staging blob (reaped by the GC cron) — evidence is never lost.
    """
    row = await _load_owned_session(
        session, actor=actor, upload_id=upload_id, for_update=True
    )
    if _is_expired(row.expires_at):
        await _abort_session(session, row)
        raise NotFound("upload_session")
    if row.next_seq <= 0:  # no chunk ever appended
        raise AttachmentUploadInvalid("upload session has no chunks to commit")

    ticket = await get_ticket(session, str(row.ticket_id))
    require_permission(actor, ticket.board, _PERM_ATTACHMENT_ADD, resource=ticket)

    staging_path = _staging_path_for(row.staging_key)
    if sha256 is not None:
        staged_digest = await asyncio.to_thread(_sha256_of_file_sync, staging_path)
        if staged_digest != sha256.strip().lower():
            raise AttachmentUploadInvalid(
                f"sha256 mismatch: client-supplied {sha256!r} != staged {staged_digest}"
            )

    attachment = await _persist_attachment(
        session,
        actor=actor,
        ticket=ticket,
        filename=row.filename,
        content_type=row.content_type,
        open_stream=lambda: open(staging_path, "rb"),
        close_after=True,
        kind=row.kind,
        source="agent",
        run_id=row.run_id,
        phase=row.phase,
    )

    await session.delete(row)
    await session.commit()
    await unlink_staging_blob(staging_path)
    return attachment
