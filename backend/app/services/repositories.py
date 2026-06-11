"""Repository service — G1 config layer + PH-221 multi-repo management.

PH-221: a board owns 0..N repositories; exactly one is the primary/default.
Functions cover list / get_primary / resolve / add / remove / set_primary, plus
the back-compat primary-aliases (``upsert_repository`` / ``detach_repository``)
that keep the singular ``PUT/DELETE /repository`` routes working until the
frontend migrates (PH-224/PH-225).

All operations are config-only; no git I/O is performed here.
G13 adds rotate_refresh_secret helper.
"""

from __future__ import annotations

import re
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.exceptions import NotFound, RepoNotConfigured
from app.db.models import Board, Repository
from app.schemas import (
    RepositoryCreate,
    RepositoryResponse,
    RepositorySummary,
    RepositoryUpsert,
)

# Slug normalisation: lowercase, non-[a-z0-9_-] runs → '-', trim leading/trailing '-'.
_SLUG_INVALID = re.compile(r"[^a-z0-9_-]+")


def _slugify(value: str) -> str:
    """Normalise an arbitrary string to a URL-safe slug ([a-z0-9_-])."""
    slug = _SLUG_INVALID.sub("-", value.strip().lower()).strip("-")
    return slug or "repo"


def _basename(local_path: str) -> str:
    """Return the last non-empty path segment of a /repos/ mount path."""
    parts = [p for p in local_path.split("/") if p]
    return parts[-1] if parts else "repo"


def repository_summary(repo: Repository) -> RepositorySummary:
    """Serialize a Repository ORM row to the compact summary schema."""
    return RepositorySummary(
        id=repo.id,
        slug=repo.slug,
        name=repo.name,
        is_primary=repo.is_primary,
        provider=repo.provider,
        remote_url=repo.remote_url,
        default_branch=repo.default_branch,
        local_path=repo.local_path,
        last_synced_sha=repo.last_synced_sha,
        last_synced_at=repo.last_synced_at,
    )


def repository_response(repo: Repository) -> RepositoryResponse:
    """Serialize a Repository ORM row to the full response schema."""
    return RepositoryResponse(
        id=repo.id,
        board_id=repo.board_id,
        slug=repo.slug,
        name=repo.name,
        is_primary=repo.is_primary,
        provider=repo.provider,
        remote_url=repo.remote_url,
        default_branch=repo.default_branch,
        local_path=repo.local_path,
        last_synced_sha=repo.last_synced_sha,
        last_synced_at=repo.last_synced_at,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_repositories(session: AsyncSession, board: Board) -> list[Repository]:
    """Return all repositories for a board, primary first then by slug."""
    return list(
        (
            await session.execute(
                select(Repository)
                .where(Repository.board_id == board.id)
                .order_by(Repository.is_primary.desc(), Repository.slug)
            )
        )
        .scalars()
        .all()
    )


async def get_primary_repository(
    session: AsyncSession, board: Board
) -> Repository | None:
    """Return the board's primary repository, or None if no primary is set."""
    return (
        await session.execute(
            select(Repository).where(
                Repository.board_id == board.id,
                Repository.is_primary.is_(True),
            )
        )
    ).scalar_one_or_none()


async def get_repository(session: AsyncSession, board: Board) -> Repository | None:
    """Return the board's PRIMARY repository, or None if not configured.

    Back-compat: pre-PH-221 callers (``sync.py``, ``cli.py``, git status,
    diff endpoints) used this as "the board's one repo". It now resolves the
    primary, preserving single-repo behaviour with zero changes at call sites.
    """
    return await get_primary_repository(session, board)


async def resolve_repository(
    session: AsyncSession, board: Board, selector: str | None
) -> Repository:
    """Resolve a repo selector to a Repository row — THE shared `/git/*` resolver.

    - ``selector`` None  → the primary repo (raises ``RepoNotConfigured`` (409)
      when the board has no primary, preserving the legacy "no repo" status).
    - ``selector`` set   → match by row id (UUID parse first; id wins on the rare
      collision) OR by slug; raises ``NotFound("repository")`` (404) when nothing
      matches.
    """
    if selector is None:
        primary = await get_primary_repository(session, board)
        if primary is None:
            raise RepoNotConfigured()
        return primary

    # Try id match first (id precedence on the theoretical slug/uuid collision).
    try:
        repo_id = uuid.UUID(selector)
    except ValueError:
        repo_id = None

    if repo_id is not None:
        by_id = (
            await session.execute(
                select(Repository).where(
                    Repository.board_id == board.id,
                    Repository.id == repo_id,
                )
            )
        ).scalar_one_or_none()
        if by_id is not None:
            return by_id

    by_slug = (
        await session.execute(
            select(Repository).where(
                Repository.board_id == board.id,
                Repository.slug == selector,
            )
        )
    ).scalar_one_or_none()
    if by_slug is None:
        raise NotFound("repository")
    return by_slug


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


async def _unique_slug(
    session: AsyncSession, board: Board, base_slug: str
) -> str:
    """Return ``base_slug`` deduplicated within the board (suffix -2, -3, ...)."""
    existing = set(
        (
            await session.execute(
                select(Repository.slug).where(Repository.board_id == board.id)
            )
        )
        .scalars()
        .all()
    )
    if base_slug not in existing:
        return base_slug
    suffix = 2
    while f"{base_slug}-{suffix}" in existing:
        suffix += 1
    return f"{base_slug}-{suffix}"


async def add_repository(
    session: AsyncSession, board: Board, payload: RepositoryCreate
) -> Repository:
    """Add a repository to a board (PH-221).

    The slug is taken from ``payload.slug`` (slugified) or derived from the
    ``local_path`` basename, then deduplicated within the board. The first repo
    added to a board is auto-promoted to primary; subsequent repos are non-primary.
    """
    basename = _basename(payload.local_path)
    base_slug = _slugify(payload.slug or basename)
    slug = await _unique_slug(session, board, base_slug)
    name = payload.name or basename

    # First repo on the board becomes the primary.
    has_any = (
        await session.execute(
            select(Repository.id).where(Repository.board_id == board.id).limit(1)
        )
    ).scalar_one_or_none() is not None

    repo = Repository(
        id=uuid.uuid4(),
        board_id=board.id,
        slug=slug,
        name=name,
        is_primary=not has_any,
        provider=payload.provider,
        remote_url=payload.remote_url,
        default_branch=payload.default_branch,
        local_path=payload.local_path,
    )
    session.add(repo)
    await session.flush()
    await session.refresh(repo)
    return repo


async def remove_repository(
    session: AsyncSession, board: Board, selector: str
) -> None:
    """Remove a repository by selector (id or slug); 404 if unmatched.

    If the removed repo was the primary AND other repos remain, the oldest
    remaining repo (``created_at`` ASC) is auto-promoted to primary so the board
    is never left with repos-but-no-primary.
    """
    repo = await resolve_repository(session, board, selector)
    was_primary = repo.is_primary
    await session.delete(repo)
    await session.flush()

    if was_primary:
        next_primary = (
            await session.execute(
                select(Repository)
                .where(Repository.board_id == board.id)
                .order_by(Repository.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if next_primary is not None:
            next_primary.is_primary = True
            await session.flush()


async def set_primary(
    session: AsyncSession, board: Board, selector: str
) -> Repository:
    """Make the selected repo the board's primary (demote → flush → promote).

    The demote-then-flush-then-promote ordering avoids tripping the partial
    unique index (``uq_repository_one_primary``) with a transient two-primary
    window inside a single flush.
    """
    target = await resolve_repository(session, board, selector)
    if target.is_primary:
        return target

    current = await get_primary_repository(session, board)
    if current is not None:
        current.is_primary = False
        await session.flush()

    target.is_primary = True
    await session.flush()
    await session.refresh(target)
    return target


async def upsert_repository(
    session: AsyncSession,
    board: Board,
    payload: RepositoryUpsert,
) -> Repository:
    """Upsert the board's PRIMARY repository (back-compat alias for PUT /repository).

    PH-221 reinterpretation of the legacy 1:1 upsert: if a primary exists it is
    updated in place (idempotent, stable id); otherwise a new repo is created and
    marked primary. The singular route + ``cli.connect_repository`` keep working
    unchanged.
    """
    existing = await get_primary_repository(session, board)
    if existing is None:
        basename = _basename(payload.local_path)
        slug = await _unique_slug(session, board, _slugify(basename))
        repo = Repository(
            id=uuid.uuid4(),
            board_id=board.id,
            slug=slug,
            name=basename,
            is_primary=True,
            provider=payload.provider,
            remote_url=payload.remote_url,
            default_branch=payload.default_branch,
            local_path=payload.local_path,
        )
        session.add(repo)
    else:
        existing.provider = payload.provider
        existing.remote_url = payload.remote_url
        existing.default_branch = payload.default_branch
        existing.local_path = payload.local_path
        repo = existing

    await session.flush()
    await session.refresh(repo)
    return repo


async def detach_repository(session: AsyncSession, board: Board) -> None:
    """Remove the board's PRIMARY repository (back-compat alias for DELETE /repository).

    Raises NotFound if no primary repository is attached — callers should handle
    404. If other repos remain, the oldest is auto-promoted (via remove_repository).
    """
    primary = await get_primary_repository(session, board)
    if primary is None:
        raise NotFound("repository")
    await remove_repository(session, board, str(primary.id))


async def rotate_refresh_secret(session: AsyncSession, board: Board) -> str:
    """Mint a new 48-hex refresh secret, persist it in board.roles, and return the plaintext.

    Caller MUST commit the session after this call.  The plaintext is returned
    once; subsequent GET /boards/{key} will show the secret masked ('*****') via
    mask_webhook_secret in the board serialiser.

    G13 (PH-162) — one-shot reveal endpoint: POST /repository/rotate-refresh-secret.
    """
    new_secret = secrets.token_hex(24)  # 48 hex chars
    roles_dict: dict[str, object] = dict(board.roles) if board.roles else {}
    roles_dict["refresh_secret"] = new_secret
    board.roles = roles_dict
    flag_modified(board, "roles")
    await session.flush()
    return new_secret
