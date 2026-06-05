"""Internal linkage helpers shared by webhook.py and sync.py.

Both the GitHub webhook path and the local sync path need to look up a ticket
by key within a board, and both need the system actor ID for history writes.
Centralising here avoids duplication and ensures consistent query semantics
(board-scoped, soft-delete aware).

G-PH-166 additionally centralises the **(commit, ticket) dedupe gate** so that
both paths converge on the ``git_commit_tickets`` unique constraint before
writing a ``git_commit_linked`` history row. This removes the
webhook-after-sync double-write asymmetry: whichever path observes the
(commit, ticket) pair first writes history; the second is a no-op.

Underscore prefix = internal; callers outside app/git/ should not import this.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Board, GitCommit, GitCommitTicket, Ticket


async def find_ticket_by_key(
    session: AsyncSession, key: str, board_id: Any
) -> Ticket | None:
    """Return the Ticket for ``key`` on ``board_id``, or None if absent/deleted."""
    return (
        await session.execute(
            select(Ticket).where(
                Ticket.key == key.upper(),
                Ticket.board_id == board_id,
                Ticket.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_system_actor_id(session: AsyncSession, board: Board) -> Any:
    """Return the actor ID used for system-generated history rows.

    Currently delegates to ``board.created_by`` (the board creator acts as the
    system actor — consistent with webhook.py).  G6 may introduce a dedicated
    system actor; swap the implementation here.
    """
    return board.created_by


# ---------------------------------------------------------------------------
# Dialect-aware "insert if absent" helper (shared by sync + webhook)
# ---------------------------------------------------------------------------


def _dialect_name(session: AsyncSession) -> str:
    """Return the SQLAlchemy dialect name for the current session."""
    try:
        bind = session.get_bind()
        if bind is not None:
            name: str = bind.dialect.name
            return name
    except Exception:
        pass
    # Fallback: inspect engine via session.bind
    bind_attr = getattr(session, "bind", None)
    dialect_attr = getattr(bind_attr, "dialect", None)
    return str(getattr(dialect_attr, "name", "sqlite"))


async def insert_ignore(
    session: AsyncSession,
    table: type[Any],
    values: dict[str, Any],
    conflict_cols: list[str],
) -> bool:
    """INSERT a row; return True if the row was new (not a conflict).

    Uses dialect-aware ON CONFLICT DO NOTHING so we can tell whether the row
    was actually inserted (returned a row) or was a no-op (conflict).

    Works on both PostgreSQL (RETURNING) and SQLite (pre-check + insert).
    """
    try:
        dialect = _dialect_name(session)
    except Exception:
        dialect = "sqlite"

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_cols)
            .returning(table.id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None
    else:
        # SQLite (and generic fallback): pre-check then insert.
        filters = [getattr(table, col) == values[col] for col in conflict_cols]
        existing = (await session.execute(select(table).where(*filters))).scalar_one_or_none()
        if existing is not None:
            return False
        obj = table(**values)
        session.add(obj)
        await session.flush()
        return True


# ---------------------------------------------------------------------------
# (commit, ticket) link gate — single source of truth for both paths
# ---------------------------------------------------------------------------


async def ensure_commit_ticket_link(
    session: AsyncSession,
    *,
    repo_id: Any,
    commit_factory: dict[str, Any],
    ticket_id: Any,
) -> bool:
    """Idempotently ensure a ``git_commit_tickets`` row for (commit, ticket).

    The ``git_commit_tickets`` unique constraint on ``(commit_id, ticket_id)``
    is the dedupe gate shared by the sync path and the GitHub webhook path.
    Because ``commit_id`` is a FK to ``git_commits.id`` (keyed on SHA per repo),
    this helper first resolves — creating if absent — the ``git_commits`` row for
    the SHA, then performs a dedupe-gated insert into the junction table.

    Args:
        session: active AsyncSession.
        repo_id: the ``Repository.id`` the commit belongs to.
        commit_factory: a fully-populated ``git_commits`` value dict to use **iff**
            no row yet exists for ``(repo_id, sha)``. MUST contain ``sha`` and the
            non-nullable commit columns. The ``id`` is honoured if present; a fresh
            UUID is assigned otherwise.
        ticket_id: the ``Ticket.id`` to link.

    Returns:
        True if the (commit, ticket) link was freshly created (caller SHOULD write
        the ``git_commit_linked`` history row); False if the link already existed
        (caller MUST skip history to avoid the double-write).
    """
    sha = commit_factory["sha"]

    # Resolve the git_commits row for (repo_id, sha); create a minimal row if the
    # webhook is the first observer (sync has not cached this commit yet).
    commit_row = (
        await session.execute(
            select(GitCommit).where(
                GitCommit.repo_id == repo_id,
                GitCommit.sha == sha,
            )
        )
    ).scalar_one_or_none()

    if commit_row is None:
        values = dict(commit_factory)
        values.setdefault("id", uuid.uuid4())
        values["repo_id"] = repo_id
        commit_row = GitCommit(**values)
        session.add(commit_row)
        await session.flush()

    commit_id = commit_row.id

    return await insert_ignore(
        session,
        GitCommitTicket,
        {"id": uuid.uuid4(), "commit_id": commit_id, "ticket_id": ticket_id},
        ["commit_id", "ticket_id"],
    )
