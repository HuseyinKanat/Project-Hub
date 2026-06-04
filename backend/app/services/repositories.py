"""Repository service — G1 config layer (connect / detach / status).

G2-G6 will add reader/sync/diff on top of these helpers.
All operations are config-only; no git I/O is performed here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFound
from app.db.models import Board, Repository
from app.schemas import RepositoryResponse, RepositorySummary, RepositoryUpsert


def repository_summary(repo: Repository) -> RepositorySummary:
    """Serialize a Repository ORM row to the compact summary schema."""
    return RepositorySummary(
        id=repo.id,
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
        provider=repo.provider,
        remote_url=repo.remote_url,
        default_branch=repo.default_branch,
        local_path=repo.local_path,
        last_synced_sha=repo.last_synced_sha,
        last_synced_at=repo.last_synced_at,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
    )


async def get_repository(session: AsyncSession, board: Board) -> Repository | None:
    """Return the Repository row for a board, or None if not configured."""
    return (
        await session.execute(
            select(Repository).where(Repository.board_id == board.id)
        )
    ).scalar_one_or_none()


async def upsert_repository(
    session: AsyncSession,
    board: Board,
    payload: RepositoryUpsert,
) -> Repository:
    """Create or update the repository configuration for a board.

    Idempotent: calling with the same payload twice returns the same row
    (updated_at changes on UPDATE, id remains stable).
    """
    existing = await get_repository(session, board)
    if existing is None:
        repo = Repository(
            id=uuid.uuid4(),
            board_id=board.id,
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
    """Remove the repository configuration for a board.

    Raises NotFound if no repository is attached — callers should handle 404.
    """
    repo = await get_repository(session, board)
    if repo is None:
        raise NotFound("repository")
    await session.delete(repo)
    await session.flush()
