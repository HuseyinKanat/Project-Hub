"""Board REST endpoints."""

import uuid as _uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor, require_board_admin
from app.core.config import get_settings
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import (
    BoardListResponse,
    BoardResponse,
    BoardUpdate,
    MembershipCreate,
    MembershipListResponse,
    MembershipResponse,
    MembershipUpdate,
    SonarIssueItem,
    SonarIssuesResponse,
    SonarSetupRequest,
    SonarSetupStatus,
)
from app.services.boards import get_board, list_boards, update_board
from app.services.memberships import (
    add_member,
    list_members,
    remove_member,
    update_member_role,
)
from app.services.serializers import board_response, membership_response
from app.services.sonarqube import (
    SonarSetupStatusData,
    build_setup_status,
    fetch_issues,
    resolve_project_key,
    setup_board_project,
    sync_board_now,
)

router = APIRouter(prefix="/api/boards", tags=["boards"])


@router.get("", response_model=BoardListResponse)
async def api_list_boards(
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardListResponse:
    boards = await list_boards(session)
    return BoardListResponse(boards=[board_response(board) for board in boards])


@router.get("/{board_id}", response_model=BoardResponse)
async def api_get_board(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardResponse:
    return board_response(await get_board(session, board_id))


@router.get("/{board_id}/sonarqube/issues", response_model=SonarIssuesResponse)
async def api_board_sonarqube_issues(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    type: Literal["BUG", "CODE_SMELL", "VULNERABILITY"] | None = None,
    severity: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> SonarIssuesResponse:
    """PH-203: proxy SonarQube ``/api/issues/search`` for a board's projectKey.

    A genuinely missing board IS a legitimate 404 (``get_board`` → NotFound). The
    never-500 rule applies only to SonarQube degradation: an unresolvable projectKey,
    sonar disabled, or SonarQube unreachable all return HTTP 200 with a ``status``
    flag and an empty issue list. ``dashboard_url`` is a HOST-facing deep-link base
    built from ``sonarqube_scan_url`` (never the compose-internal ``sonarqube_url``,
    never the token); null when there is no projectKey / sonar is not configured.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    settings = get_settings()

    project_key = resolve_project_key(board)
    if project_key is None:
        return SonarIssuesResponse(
            status="no_project_key",
            total=0,
            page=page,
            page_size=page_size,
            issues=[],
            dashboard_url=None,
        )
    if not settings.sonarqube_enabled:
        return SonarIssuesResponse(
            status="not_configured",
            total=0,
            page=page,
            page_size=page_size,
            issues=[],
            dashboard_url=None,
        )

    result = await fetch_issues(
        project_key,
        types=type,
        severities=severity,
        page=page,
        page_size=page_size,
    )
    dashboard_url = (
        f"{settings.sonarqube_scan_url.rstrip('/')}/project/issues?id={project_key}"
    )
    return SonarIssuesResponse(
        status=result.status,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        issues=[
            SonarIssueItem(
                key=i.key,
                rule=i.rule,
                severity=i.severity,
                type=i.type,
                component=i.component,
                line=i.line,
                message=i.message,
                hash=i.hash,
            )
            for i in result.issues
        ],
        dashboard_url=dashboard_url,
    )


# ---------------------------------------------------------------------------
# PH-223: SonarQube one-click setup + sync-now + status.
#
# All three are graceful-200 (mirror /sonarqube/issues): a genuinely missing
# board is a legit 404 (get_board → NotFound), but every SonarQube degradation
# (disabled / no key / unreachable) returns 200 with SonarSetupStatus flags +
# message — NEVER 500, NEVER a blocking probe on the read path. The status object
# is SECRET-FREE: no token, no compose-internal sonarqube_url; dashboard_url is a
# HOST-facing link derived from sonarqube_scan_url only.
# ---------------------------------------------------------------------------


def _setup_status_response(data: SonarSetupStatusData) -> SonarSetupStatus:
    """Map the service dataclass → the SonarSetupStatus response schema."""
    return SonarSetupStatus(
        enabled=data.enabled,
        reachable=data.reachable,
        configured=data.configured,
        project_key=data.project_key,
        last_metric_fetched_at=data.last_metric_fetched_at,
        quality_gate_status=data.quality_gate_status,
        dashboard_url=data.dashboard_url,
        message=data.message,
    )


@router.post("/{board_id}/sonarqube/setup", response_model=SonarSetupStatus)
async def api_board_sonarqube_setup(
    board_id: str,
    payload: SonarSetupRequest,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarSetupStatus:
    """One-click link a board to its SonarQube project (admin only).

    Persists ``Board.sonarqube_project_key`` to the supplied key or the derived
    default (PH → ``project-hub``; else board key lowercased). Idempotent: re-running
    with the same effective key is a clean no-op. Returns 200 ``SonarSetupStatus``.

    Provisioning = scan-time auto-create — this does NOT call the SonarQube admin
    project-create API (no admin token provisioned; out of scope). The key is
    persisted even when ``sonarqube_enabled=false`` (config allowed offline); the
    status then reports ``enabled=false`` so the UI shows "linked, but disabled".
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    await setup_board_project(session, board, payload.project_key)
    return _setup_status_response(await build_setup_status(session, board))


@router.post("/{board_id}/sonarqube/sync", response_model=SonarSetupStatus)
async def api_board_sonarqube_sync(
    board_id: str,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarSetupStatus:
    """Trigger an immediate metric re-poll for a board (admin only).

    Re-poll, NOT re-scan: reads SonarQube's *existing* analysis (fast, bounded by
    the 10s client timeout) and upserts the metric cache — it does NOT kick a full
    scanner run (scans stay post-merge). Graceful-200: disabled / no key /
    unreachable all return ``SonarSetupStatus`` flags, never 500, never hang.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    return _setup_status_response(await sync_board_now(session, board))


@router.get("/{board_id}/sonarqube/status", response_model=SonarSetupStatus)
async def api_board_sonarqube_status(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarSetupStatus:
    """Read a board's current SonarQube setup state (any board member).

    Pure read: assembles ``SonarSetupStatus`` from settings + the cached metric.
    Makes NO live probe (a read must not hang on a down server) — reachability is
    derived from cached-metric freshness, not a blocking ``/api/system/status`` call.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    return _setup_status_response(await build_setup_status(session, board))


@router.patch("/{board_id}", response_model=BoardResponse)
async def api_update_board(
    board_id: str,
    payload: BoardUpdate,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BoardResponse:
    board = await get_board(session, board_id)
    await update_board(
        session,
        board,
        name=payload.name,
        description=payload.description,
        project_type=payload.project_type,
        roles=payload.roles,
    )
    await session.commit()
    # Re-fetch to ensure relationships are loaded for serialization
    updated_board = await get_board(session, str(board.id))
    return board_response(updated_board)


# ---------------------------------------------------------------------------
# PH-39: Board membership endpoints
# ---------------------------------------------------------------------------


@router.get("/{board_id}/members", response_model=MembershipListResponse)
async def api_list_members(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipListResponse:
    """List all members of a board (any authenticated actor)."""
    board = await get_board(session, board_id)
    members = await list_members(session, board)
    return MembershipListResponse(members=[membership_response(m) for m in members])


@router.post(
    "/{board_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_add_member(
    board_id: str,
    payload: MembershipCreate,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipResponse:
    """Add an actor to a board (admin only).

    Returns 201 with the new membership.
    Raises 422 if the role is unknown, 409 if the actor is already a member.
    """
    board = await get_board(session, board_id)
    membership = await add_member(session, board, payload.actor_id, payload.role)
    await session.commit()
    return membership_response(membership)


@router.patch("/{board_id}/members/{actor_id}", response_model=MembershipResponse)
async def api_update_member(
    board_id: str,
    actor_id: _uuid.UUID,
    payload: MembershipUpdate,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MembershipResponse:
    """Update the role of a board member (admin only).

    Raises 422 on unknown role, 409 on last-admin demotion, 404 if not a member.
    """
    board = await get_board(session, board_id)
    membership = await update_member_role(session, board, actor_id, payload.role)
    await session.commit()
    return membership_response(membership)


@router.delete(
    "/{board_id}/members/{actor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def api_remove_member(
    board_id: str,
    actor_id: _uuid.UUID,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Remove a member from a board (admin only).

    Raises 409 if this is the last admin, 404 if not a member.
    """
    board = await get_board(session, board_id)
    await remove_member(session, board, actor_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
