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

from app.db.models import Board, GitCommit, GitCommitFile, GitCommitTicket, Ticket


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


# ---------------------------------------------------------------------------
# Stub enrichment — repair webhook-first git_commits rows during sync
# ---------------------------------------------------------------------------

# Columns sync owns authoritatively. When the webhook is the first observer it
# mints a minimal git_commits row (``parents=[]``, ``body=''``,
# ``committer_name``=author, no git_commit_files). These columns are the ones
# sync can fully populate from a real git read and must overwrite on enrichment.
_ENRICHABLE_COLUMNS: tuple[str, ...] = (
    "parents",
    "author_name",
    "author_email",
    "authored_at",
    "committer_name",
    "committer_email",
    "committed_at",
    "summary",
    "body",
    "is_conventional",
    "commit_type",
    "ticket_keys",
)


async def enrich_commit_row(
    session: AsyncSession,
    *,
    repo_id: Any,
    commit_values: dict[str, Any],
) -> GitCommit | None:
    """Enrich an existing webhook-stub ``git_commits`` row with full sync data.

    The webhook may become the first observer of a commit and mint a minimal
    stub (``parents=[]``, no ``git_commit_files`` rows, ``committer``=author,
    empty ``body``). A subsequent sync sees the row already exists and — prior to
    PH-166 — ``continue``d past file fetching, so the stub stayed permanently
    impoverished (0 files, ``parents=[]``), corrupting commit-detail views and
    the branch-graph ahead/behind BFS.

    This helper detects such a stub and upserts the authoritative columns in
    place (the row's identity / ``id`` / ``created_at`` are preserved so existing
    ``git_commit_tickets`` FKs stay valid). It returns the row **iff** file rows
    still need to be inserted (stub had none), so the caller can fetch and add
    ``git_commit_files``. Returns ``None`` when the row is already enriched
    (has file rows) — no work needed.

    A row is considered a stub-to-enrich when it has **zero** ``git_commit_files``
    rows. ``parents`` alone is insufficient (a real root commit also has empty
    parents); the absence of file rows is the authoritative "sync never ran"
    signal, since sync always attempts file insertion for every commit it caches.

    Args:
        session: active AsyncSession.
        repo_id: the ``Repository.id`` the commit belongs to.
        commit_values: a fully-populated git_commits value dict from a real git
            read (the same dict sync builds for a fresh insert). MUST contain
            ``sha`` and the enrichable columns.

    Returns:
        The enriched ``GitCommit`` row if file rows must still be inserted by the
        caller; ``None`` if the row was already complete (has file rows) or does
        not exist.
    """
    sha = commit_values["sha"]
    commit_row = (
        await session.execute(
            select(GitCommit).where(
                GitCommit.repo_id == repo_id,
                GitCommit.sha == sha,
            )
        )
    ).scalar_one_or_none()

    if commit_row is None:
        return None

    has_files = (
        await session.execute(
            select(GitCommitFile.id)
            .where(GitCommitFile.commit_id == commit_row.id)
            .limit(1)
        )
    ).scalar_one_or_none() is not None

    if has_files:
        # Already enriched by a prior sync — nothing to repair.
        return None

    # Stub: overwrite the authoritative columns with the real git read. The
    # row id / created_at are untouched so the junction FK stays valid.
    for col in _ENRICHABLE_COLUMNS:
        if col in commit_values:
            setattr(commit_row, col, commit_values[col])
    await session.flush()
    return commit_row
