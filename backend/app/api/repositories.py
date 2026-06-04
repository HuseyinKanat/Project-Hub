"""Repository config REST endpoints — G1 (PH-150), G4 (PH-153), G5 (PH-154), G6 (PH-155).

PUT    /api/boards/{board_key}/repository             — upsert repo config (board admin)
DELETE /api/boards/{board_key}/repository             — detach repo config (board admin)
GET    /api/boards/{board_key}/git/status             — connection status (any board member)
GET    /api/boards/{board_key}/git/graph              — DAG payload (G4)
GET    /api/boards/{board_key}/git/branches           — branch list with ahead/behind (G4)
GET    /api/boards/{board_key}/git/commits            — paginated commit log (G4)
GET    /api/boards/{board_key}/git/commits/{sha}      — commit detail + files (G4)
GET    /api/boards/{board_key}/git/commits/{sha}/diff — unified diff of one commit (G5)
GET    /api/boards/{board_key}/git/diff               — three-dot range diff (G5)
POST   /api/boards/{board_key}/git/refresh            — trigger sync, shared-secret auth (G6)
"""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

import git as gitpython
from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.config import get_settings
from app.core.exceptions import NotFound, PermissionDenied, RepoNotConfigured
from app.core.logging import get_logger
from app.db.models import Actor, Board, BoardMembership
from app.db.session import get_db_session
from app.git.reader import adiff_text, aopen_repo, arange_diff
from app.git.refresh import _locked_sync_repo, registry
from app.schemas import (
    DiffResponse,
    FileDiff,
    GitBranchesListResponse,
    GitCommitDetail,
    GitCommitsListResponse,
    GitGraphResponse,
    GitRefreshResponse,
    GitStatusResponse,
    RangeDiffResponse,
    RepositoryResponse,
    RepositoryUpsert,
)
from app.services.background_tasks import run_in_background
from app.services.boards import get_board
from app.services.git_queries import (
    branches_payload,
    commit_detail,
    commits_payload,
    graph_payload,
    resolve_sha,
)
from app.services.repositories import (
    detach_repository,
    get_repository,
    repository_response,
    repository_summary,
    upsert_repository,
)

logger = get_logger(__name__)

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


# ---------------------------------------------------------------------------
# G5 — diff API (PH-154)
# ---------------------------------------------------------------------------


@router.get(
    "/git/commits/{sha}/diff",
    response_model=DiffResponse,
    operation_id="api_git_commit_diff",
)
async def api_git_commit_diff(
    sha: str,
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    path: Annotated[str | None, Query(description="Restrict diff to this file path")] = None,
    context: Annotated[int, Query(ge=0, le=10, description="Unified context lines (0-10)")] = 3,
) -> DiffResponse:
    """Unified diff of one commit versus its first parent (live reader).

    ``sha`` may be a full 40-hex sha or a short unambiguous prefix (>=7 chars).
    ``path`` restricts the diff to a single file (404 if not in this commit).
    ``context`` controls unified-diff context lines (clamped to 0-10 by FastAPI).

    Returns 404 for unknown sha, 409 if no repo configured, 403 if not a member.
    Binary files appear with ``is_binary=true`` and ``patch=null``.
    When total patch size exceeds the server limit, ``truncated=true`` is set.
    """
    from app.core.exceptions import RepoNotConfigured

    repo_row = await get_repository(session, board)
    if repo_row is None:
        raise RepoNotConfigured()

    # Resolve sha via cache (404 if absent) before hitting the FS
    full_sha = await resolve_sha(session, repo_row.id, sha)

    # Open hardened repo handle
    repo = await aopen_repo(repo_row.local_path)

    settings = get_settings()
    try:
        result = await adiff_text(
            repo,
            full_sha,
            path,
            context=context,
            max_bytes=settings.git_diff_max_bytes,
        )
    except gitpython.BadName as exc:
        raise NotFound("commit") from exc

    # If path filter was requested and no files returned, surface 404
    if path is not None and len(result.files) == 0:
        raise NotFound("commit_path")

    return DiffResponse(
        sha=full_sha,
        files=[
            FileDiff(
                path=f.path,
                old_path=f.old_path,
                change_type=f.change_type,
                additions=f.additions,
                deletions=f.deletions,
                is_binary=f.is_binary,
                patch=f.patch,
                truncated=f.truncated,
            )
            for f in result.files
        ],
        truncated=result.truncated,
    )


@router.get(
    "/git/diff",
    response_model=RangeDiffResponse,
    operation_id="api_git_range_diff",
)
async def api_git_range_diff(
    board: Annotated[Board, Depends(_require_board_member)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    base: Annotated[str, Query(min_length=1, max_length=200, description="Base ref")],
    head: Annotated[str, Query(min_length=1, max_length=200, description="Head ref")],
    path: Annotated[str | None, Query(description="Restrict diff to this file path")] = None,
    context: Annotated[int, Query(ge=0, le=10, description="Unified context lines (0-10)")] = 3,
) -> RangeDiffResponse:
    """Three-dot merge-base range diff (``base...head``).

    This matches GitHub/GitLab PR-diff semantics: the diff is anchored at the
    merge base of ``base`` and ``head``.  ``base`` and ``head`` may be branch
    names, tag names, or full/short SHAs.

    Returns 404 when ``base`` or ``head`` cannot be resolved in the repo.
    Returns 409 if no repo is configured for this board.
    """
    from app.core.exceptions import RepoNotConfigured

    repo_row = await get_repository(session, board)
    if repo_row is None:
        raise RepoNotConfigured()

    repo = await aopen_repo(repo_row.local_path)

    settings = get_settings()
    try:
        result = await arange_diff(
            repo,
            base,
            head,
            path,
            context=context,
            max_bytes=settings.git_diff_max_bytes,
        )
    except gitpython.GitCommandError as exc:
        raise NotFound("ref") from exc

    return RangeDiffResponse(
        base=base,
        head=head,
        files=[
            FileDiff(
                path=f.path,
                old_path=f.old_path,
                change_type=f.change_type,
                additions=f.additions,
                deletions=f.deletions,
                is_binary=f.is_binary,
                patch=f.patch,
                truncated=f.truncated,
            )
            for f in result.files
        ],
        truncated=result.truncated,
    )


# ---------------------------------------------------------------------------
# G6 — Live refresh endpoint (PH-155)
# ---------------------------------------------------------------------------


@router.post(
    "/git/refresh",
    response_model=GitRefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="api_git_refresh",
)
async def api_git_refresh(
    board_key: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_git_refresh_token: Annotated[str | None, Header()] = None,
) -> GitRefreshResponse:
    """Trigger a live sync for this board's repository.

    Auth: shared-secret via ``X-Git-Refresh-Token`` header matched against
    ``board.roles["refresh_secret"]`` using constant-time hmac.compare_digest.

    - 403 when ``refresh_secret`` is not configured on the board (fails closed).
    - 401 when the secret IS configured but the token does not match.
    - 503 when ``git_refresh_enabled=False`` in settings (master kill switch).
    - 409 when no Repository row is attached to the board.
    - 202 {status:"queued"} when a sync was dispatched to the background.
    - 202 {status:"coalesced"} when another sync is already in-flight / just ran
      within the debounce window.
    """
    settings = get_settings()

    # Master kill switch: 503 and no sync.
    if not settings.git_refresh_enabled:
        return GitRefreshResponse(
            ok=False,
            status="disabled",
            last_sync_at=None,
        )

    board = await get_board(session, board_key)

    # Auth — shared-secret only (no actor token; hook is fire-and-forget).
    roles_dict: dict[str, object] = board.roles if isinstance(board.roles, dict) else {}
    refresh_secret: str | None = roles_dict.get("refresh_secret")  # type: ignore[assignment]
    if not refresh_secret:
        # Secret not configured → fail closed (403).
        logger.info(
            "git_refresh: board=%s rejected (refresh_secret not set)", board.key
        )
        return Response(  # type: ignore[return-value]
            status_code=status.HTTP_403_FORBIDDEN,
            content='{"error":"refresh_disabled","detail":"set board.roles.refresh_secret first"}',
            media_type="application/json",
        )

    # Constant-time comparison.
    supplied = x_git_refresh_token or ""
    if not hmac.compare_digest(supplied, refresh_secret):
        logger.info(
            "git_refresh: board=%s rejected (token mismatch)", board.key
        )
        return Response(  # type: ignore[return-value]
            status_code=status.HTTP_401_UNAUTHORIZED,
            content='{"error":"unauthorized"}',
            media_type="application/json",
        )

    # Repo must be configured.
    repo_row = await get_repository(session, board)
    if repo_row is None:
        raise RepoNotConfigured()

    last_sync_at = repo_row.last_synced_at

    # Debounce: if a sync was dispatched within the debounce window, coalesce.
    if registry.should_coalesce(repo_row.id, settings.git_refresh_debounce_seconds):
        logger.debug(
            "git_refresh: board=%s coalesced (within debounce window)", board.key
        )
        return GitRefreshResponse(
            ok=True,
            status="coalesced",
            last_sync_at=last_sync_at,
        )

    # Mark dispatched before submitting so rapid concurrent calls coalesce.
    registry.mark_dispatched(repo_row.id)
    board_id: uuid.UUID = board.id

    run_in_background(
        lambda: _locked_sync_repo(board_id),
        name=f"git_refresh_{board.key}",
    )
    logger.info("git_refresh: board=%s queued", board.key)

    return GitRefreshResponse(
        ok=True,
        status="queued",
        last_sync_at=last_sync_at,
    )
