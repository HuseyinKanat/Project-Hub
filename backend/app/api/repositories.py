"""Repository config REST endpoints (PH-150 G1) and git read API (PH-153 G4).

PUT    /api/boards/{board_key}/repository          — upsert repo config (board admin)
DELETE /api/boards/{board_key}/repository          — detach repo config (board admin)
GET    /api/boards/{board_key}/git/status          — connection status (any board member)
GET    /api/boards/{board_key}/git/graph           — DAG payload (G4)
GET    /api/boards/{board_key}/git/branches        — branch list with ahead/behind (G4)
GET    /api/boards/{board_key}/git/commits         — paginated commit log (G4)
GET    /api/boards/{board_key}/git/commits/{sha}   — commit detail + files (G4)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.exceptions import PermissionDenied
from app.db.models import Actor, Board, BoardMembership
from app.db.session import get_db_session
from app.schemas import (
    GitBranchesListResponse,
    GitCommitDetail,
    GitCommitsListResponse,
    GitGraphResponse,
    GitStatusResponse,
    RepositoryResponse,
    RepositoryUpsert,
)
from app.services.boards import get_board
from app.services.git_queries import (
    branches_payload,
    commit_detail,
    commits_payload,
    graph_payload,
)
from app.services.repositories import (
    detach_repository,
    get_repository,
    repository_response,
    repository_summary,
    upsert_repository,
)

router = APIRouter(prefix="/api/boards/{board_key}", tags=["repositories"])


# ---------------------------------------------------------------------------
# Local dependency helpers
# ---------------------------------------------------------------------------


async def _require_board_member(
    board_key: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Board:
    """Return the Board after verifying actor is a member (any role).

    Raises 404 if board not found, 403 if not a member.
    """
    board = await get_board(session, board_key)
    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise PermissionDenied(required="board.member", have=[])
    return board


async def _require_board_admin(
    board_key: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Board:
    """Return the Board after verifying actor is a board admin.

    Raises 404 if board not found, 403 if actor is not an admin.
    """
    board = await get_board(session, board_key)
    membership = (
        await session.execute(
            select(BoardMembership).where(
                BoardMembership.board_id == board.id,
                BoardMembership.actor_id == actor.id,
                BoardMembership.role == "admin",
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise PermissionDenied(required="board.admin", have=[])
    return board


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.put("/repository", response_model=RepositoryResponse)
async def api_upsert_repository(
    payload: RepositoryUpsert,
    board: Annotated[Board, Depends(_require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RepositoryResponse:
    """Connect or update a git repository for a board (idempotent upsert).

    Returns 200 in both the create and update cases so callers need not
    distinguish between them (config-first contract).
    """
    repo = await upsert_repository(session, board, payload)
    await session.commit()
    await session.refresh(repo)
    return repository_response(repo)


@router.delete("/repository", status_code=status.HTTP_204_NO_CONTENT)
async def api_detach_repository(
    board: Annotated[Board, Depends(_require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Detach (remove) the repository configuration from a board.

    Returns 204 on success, 404 if no repository is configured.
    """
    await detach_repository(session, board)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/git/status", response_model=GitStatusResponse)
async def api_git_status(
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GitStatusResponse:
    """Return the git connection status for a board.

    Any board member may call this endpoint (read-only, no admin required).
    G1 reports config connectivity only; physical reachability is G6.
    """
    repo = await get_repository(session, board)
    if repo is None:
        return GitStatusResponse(
            connected=False,
            repository=None,
            last_synced_at=None,
        )
    return GitStatusResponse(
        connected=True,
        repository=repository_summary(repo),
        last_synced_at=repo.last_synced_at,
    )


# ---------------------------------------------------------------------------
# G4 — git read API (PH-153)
# ---------------------------------------------------------------------------


@router.get(
    "/git/graph",
    response_model=GitGraphResponse,
    operation_id="api_git_graph",
)
async def api_git_graph(
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    branches: Annotated[str | None, Query()] = None,
) -> GitGraphResponse:
    """DAG payload for the SourceTree-style lane renderer.

    Returns cached commits + branch heads.  Tags array is always empty in G4.
    ``branches`` is a CSV of branch names to filter on; omit for all branches.
    """
    branch_filter = [b.strip() for b in branches.split(",") if b.strip()] if branches else None
    return await graph_payload(session, board, limit=limit, branch_filter=branch_filter)


@router.get(
    "/git/branches",
    response_model=GitBranchesListResponse,
    operation_id="api_git_branches",
)
async def api_git_branches(
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GitBranchesListResponse:
    """Branch list with ahead/behind against the default branch.

    ``ahead``/``behind`` are null when BFS exceeds the backfill limit
    (deep divergence); G8 should render "..." for null values.
    """
    return await branches_payload(session, board)


@router.get(
    "/git/commits",
    response_model=GitCommitsListResponse,
    operation_id="api_git_commits",
)
async def api_git_commits(
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    branch: Annotated[str | None, Query()] = None,
    path: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[str | None, Query()] = None,
) -> GitCommitsListResponse:
    """Paginated commit log (newest-first).

    Use ``before=<sha>`` as a cursor to fetch the next page.
    ``branch`` restricts to commits reachable from that branch head.
    ``path`` restricts to commits that touched the given file path (exact).
    """
    return await commits_payload(
        session, board, branch=branch, path=path, limit=limit, before=before
    )


@router.get(
    "/git/commits/{sha}",
    response_model=GitCommitDetail,
    operation_id="api_git_commit_detail",
)
async def api_git_commit_detail(
    sha: str,
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GitCommitDetail:
    """Full commit detail including per-file numstat.

    ``sha`` may be the full 40-hex sha or any unambiguous prefix (≥7 chars).
    Returns 404 if not found or if the prefix matches multiple commits.
    """
    return await commit_detail(session, board, sha)
