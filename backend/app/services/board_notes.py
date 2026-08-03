"""Board-scoped notes / guardrails store (PH-336) — CRUD service.

The round-1 "recurring-mistake / warning notes" surface: a net-new board-scoped,
DB-persisted, MCP-queryable store (explicitly NOT a CLAUDE.md mirror — there is no
CLAUDE.md I/O anywhere). A note is ``body`` + author + timestamp + ``board_id`` (no
severity/tag — cut in round-1). Humans WRITE via the BoardSettings panel; agents PULL
read-only via the MCP ``get_board_notes`` tool.

Auth ordering mirrors ``services/progress.py`` + the ``sonar_pr_issues`` MCP tool and
is applied to ALL ops (list/create/delete): unknown board -> 404 FIRST (``get_board``),
resolved-but-non-member -> 403 SECOND (``require_board_member``). Round-1 gates on
MEMBERSHIP (not admin) — every board-scoped precedent does, and every jarwis role is a
member so agents can PULL guardrails; ``require_board_admin`` is the forward-compat
tightening if note-spam becomes real.

Services own their transaction boundary (``get_db_session`` does NOT commit) — create
and delete commit; list is read-only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import schemas
from app.core.exceptions import NotFound
from app.core.permissions import require_board_member
from app.db.models import Actor, BoardNote
from app.services.boards import get_board


def _to_schema(note: BoardNote) -> schemas.BoardNote:
    """Serialize a ``BoardNote`` ORM row; resolves the author's display_name.

    Requires ``note.author`` eager-loaded (``selectinload``) OR a freshly-created row
    whose author is passed explicitly by the caller. A NULL ``created_by`` / a
    deleted author -> ``created_by_name=None`` (UI shows "unknown"; never a 500).
    """
    author = note.author
    return schemas.BoardNote(
        id=note.id,
        board_id=note.board_id,
        body=note.body,
        created_by=note.created_by,
        created_by_name=author.display_name if author is not None else None,
        created_at=note.created_at,
    )


async def list_notes(
    session: AsyncSession, *, actor: Actor, board_id: str
) -> schemas.BoardNoteListResponse:
    """List a board's notes newest-first (body + author display_name + created_at)."""
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_board_member(actor, board)  # non-member -> 403 SECOND
    result = await session.execute(
        select(BoardNote)
        .where(BoardNote.board_id == board.id)
        # created_at DESC = newest-first; id DESC breaks ties deterministically for
        # rows sharing a server_default timestamp (bulk inserts in a single tick).
        .order_by(BoardNote.created_at.desc(), BoardNote.id.desc())
        .options(selectinload(BoardNote.author))
    )
    notes = list(result.scalars())
    return schemas.BoardNoteListResponse(notes=[_to_schema(note) for note in notes])


async def create_note(
    session: AsyncSession, *, actor: Actor, board_id: str, body: str
) -> schemas.BoardNote:
    """Persist a note authored by ``actor``; returns the created row (201 at the API).

    ``body`` is stripped; a blank/whitespace-only body raises ``ValueError`` (UC E1 —
    no partial row). The REST schema (``BoardNoteCreate``) already rejects blank at
    parse (422), so this branch only guards a direct/service caller.
    """
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_board_member(actor, board)  # non-member -> 403 SECOND
    cleaned = body.strip()
    if not cleaned:
        raise ValueError("body must not be empty or whitespace-only")
    note = BoardNote(board_id=board.id, body=cleaned, created_by=actor.id)
    session.add(note)
    await session.commit()
    # The author is the authenticated ``actor`` — resolve its name directly (no reload).
    return schemas.BoardNote(
        id=note.id,
        board_id=note.board_id,
        body=note.body,
        created_by=note.created_by,
        created_by_name=actor.display_name,
        created_at=note.created_at,
    )


async def delete_note(
    session: AsyncSession, *, actor: Actor, board_id: str, note_id: UUID
) -> None:
    """Delete a note that belongs to ``board_id``.

    The ``board_id`` predicate is load-bearing: a note of ANOTHER board -> 404 (never a
    cross-board delete), same as an entirely unknown note id.
    """
    board = await get_board(session, board_id)  # unknown board -> 404 FIRST
    require_board_member(actor, board)  # non-member -> 403 SECOND
    result = await session.execute(
        select(BoardNote).where(
            BoardNote.id == note_id, BoardNote.board_id == board.id
        )
    )
    note = result.scalar_one_or_none()
    if note is None:
        raise NotFound("note")
    await session.delete(note)
    await session.commit()
