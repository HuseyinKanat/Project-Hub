"""Board REST endpoints."""

import uuid as _uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
    RepoHealth,
    RepoSonarSetupRequest,
    SonarIssueItem,
    SonarIssuesResponse,
    SonarRepoScanItem,
    SonarRepoScanResponse,
    SonarScanPlanResponse,
    SonarScanResponse,
    SonarSetupRequest,
    SonarSetupStatus,
)
from app.services import repo_paths
from app.services.boards import get_board, list_boards, update_board
from app.services.memberships import (
    add_member,
    list_members,
    remove_member,
    update_member_role,
)
from app.services.repositories import resolve_repository
from app.services.serializers import (
    board_response,
    membership_response,
    repo_health,
    unscanned_repo_health,
)
from app.services.sonarqube import (
    SonarRepoScanResult,
    SonarScanPlan,
    SonarScanResult,
    SonarSetupStatusData,
    build_scan_plan,
    build_scan_plans,
    build_setup_status,
    fetch_issues,
    request_board_scan,
    request_repo_scan,
    resolve_project_key,
    setup_board_project,
    setup_repo_project,
    sync_board_now,
    sync_repo_now,
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
        # PH-235: the honest discriminator + the split-out has_analysis signal.
        status=data.status,
        has_analysis=data.has_analysis,
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


@router.post(
    "/{board_id}/repositories/{selector}/sonarqube/setup",
    response_model=RepoHealth,
)
async def api_repo_sonarqube_setup(
    board_id: str,
    selector: str,
    payload: RepoSonarSetupRequest,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RepoHealth:
    """Assign/override ONE repo's SonarQube project key (admin only, PH-254).

    The per-repo successor to the board-level ``/sonarqube/setup`` — it persists
    ``Repository.sonarqube_project_key`` for a single (typically secondary) repo so
    the per-repo Quality dashboard can configure repos beyond the primary. Mirrors
    the board-level contract: persist-only (NO live Sonar poll — provisioning is
    scan-time auto-create, decision a), never-500, SECRET-FREE.

    Resolution + status codes:
      - ``{board_id}`` KEY or UUID + admin membership via ``require_board_admin``
        (unknown board → 404, non-admin → 403).
      - ``{selector}`` repo id OR slug via ``resolve_repository`` (no match → 404).
      - blank/whitespace ``project_key`` → 422 (``setup_repo_project`` raises
        ``ValueError``, FastAPI maps to 422); omit/``{}`` derives the default.
      - a ``project_key`` already held by ANOTHER repo of THIS board → 409
        (``Conflict``; decision b — cross-board reuse is allowed).
      - sonar disabled → still 200 + persists (config allowed offline).

    Returns the just-configured repo's ``RepoHealth`` (so the FE re-renders the one
    card in place): the existing metric row when present, else an honest unscanned
    card (null metrics) carrying the freshly-stored ``project_key``.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    repo = await resolve_repository(session, board, selector)  # 404 on no match
    try:
        await setup_repo_project(session, board, repo, payload.project_key)
    except ValueError as exc:
        # Blank/whitespace key — surface as 422 (never 500), mirroring the
        # never-500 contract; the message is operator-actionable.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # Build RepoHealth for the just-configured repo: the cached metric row when one
    # exists (eager-loaded on `board.sonarqube_metrics` by get_board, async-safe),
    # else an honest unscanned card surfacing the freshly-stored project_key.
    metric = next(
        (m for m in board.sonarqube_metrics if m.repo_id == repo.id), None
    )
    if metric is not None:
        return repo_health(metric)
    return unscanned_repo_health(board, repo)


# ---------------------------------------------------------------------------
# PH-255 (C2): per-repo "Scan now" + "Sync now" — the single-repo successors of
# the board-level /sonarqube/{scan,sync}. Both on the boards router (reuse
# require_board_admin verbatim + resolve_repository for {selector}), admin-gated,
# graceful-200, never-500, SECRET-FREE.
# ---------------------------------------------------------------------------


def _repo_scan_response(result: SonarRepoScanResult) -> SonarRepoScanResponse:
    """Map the service ``SonarRepoScanResult`` dataclass → the response schema."""
    return SonarRepoScanResponse(
        repo_slug=result.repo_slug,
        project_key=result.project_key,
        language=result.language,
        scan_status=result.scan_status,
        job_id=result.job_id,
        message=result.message,
    )


@router.post(
    "/{board_id}/repositories/{selector}/sonarqube/scan",
    response_model=SonarRepoScanResponse,
)
async def api_repo_sonarqube_scan(
    board_id: str,
    selector: str,
    admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarRepoScanResponse:
    """Enqueue an on-demand scan for ONE repo ("Scan now", admin only, PH-255).

    The per-repo successor of the board-level ``/sonarqube/scan`` — it targets a single
    (typically secondary) repo so the per-repo Quality card's "Scan now" button works for
    repos beyond the primary. DISTINCT from ``sync`` (sync re-polls the EXISTING analysis;
    scan ENQUEUES a NEW analysis run). Cheap + NON-blocking + never-500: the backend can't
    ``docker compose run`` so it only enqueues a ``SonarScanJob`` (idempotent per
    ``(board_id, repo_id)``); the host watcher (``scripts/sonar-scan-watcher.sh``) drains it.

    Resolution + status codes:
      - ``{board_id}`` KEY or UUID + admin membership via ``require_board_admin``
        (unknown board → 404, non-admin → 403).
      - ``{selector}`` repo id OR slug via ``resolve_repository`` (no match → 404).
      - A non-scannable repo (csharp-needs-setup / unsupported / no key / no path / sonar
        disabled) returns 200 with the HONEST ``scan_status`` ∈
        {unsupported,needs_dotnet_setup,unconfigured,error,disabled} and ``job_id=null``,
        NO job enqueued (decision A — NOT a 409/422). The FE renders it as a UX state, not
        a client error.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    repo = await resolve_repository(session, board, selector)  # 404 on no match
    return _repo_scan_response(
        await request_repo_scan(session, board, repo, requested_by=admin.id)
    )


@router.post(
    "/{board_id}/repositories/{selector}/sonarqube/sync",
    response_model=RepoHealth,
)
async def api_repo_sonarqube_sync(
    board_id: str,
    selector: str,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RepoHealth:
    """Re-poll ONE repo's existing analysis → fresh ``RepoHealth`` (admin only, PH-255).

    The per-repo successor of the board-level ``/sonarqube/sync`` — re-poll, NOT re-scan:
    it reads SonarQube's *existing* analysis for THIS repo's derived project key (fast,
    bounded by the 10s client timeout) and upserts its single ``(board_id, repo_id)``
    ``SonarQubeMetric`` row, then returns the fresh card so the FE re-renders the one repo
    in place. Graceful-200, never-500, never-hang.

    Resolution + status codes:
      - ``{board_id}`` KEY or UUID + admin membership via ``require_board_admin``
        (unknown board → 404, non-admin → 403).
      - ``{selector}`` repo id OR slug via ``resolve_repository`` (no match → 404).
      - A NEVER-scanned repo (Sonar has no analysis yet) returns 200 with the honest
        unscanned ``RepoHealth`` (null gate / null metrics / null ``fetched_at`` / derived
        ``project_key`` / ``dashboard_url=null``), NOT a 404 (decision B). Reachable + has
        analysis → the metric row is upserted and live values are returned.
      - sonar disabled → 200, NO live attempt (current cached card or unscanned), never 5xx.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    repo = await resolve_repository(session, board, selector)  # 404 on no match
    return await sync_repo_now(session, board, repo)


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


# ---------------------------------------------------------------------------
# PH-236 (C2): per-board "Scan now" enqueue + scan-plan for the host runner.
#
# `scan` ENQUEUES a NEW analysis run (distinct from `sync`, which re-polls the
# EXISTING analysis). The backend can't `docker compose run`, so the actual scanner
# runs HOST-side via scripts/sonar-scan-board.sh, which curls `scan-plan`. Both are
# admin-gated (require_board_admin, key-or-uuid PH-233), graceful-200, never-500,
# SECRET-FREE (no token, no compose-internal sonarqube_url).
# ---------------------------------------------------------------------------


def _scan_result_response(result: SonarScanResult) -> SonarScanResponse:
    """Map the service ``SonarScanResult`` dataclass → the response schema."""
    return SonarScanResponse(
        scan_status=result.scan_status,
        project_key=result.project_key,
        language=result.language,
        container_source=result.container_source,
        message=result.message,
        # PH-246: additive per-repo breakdown.
        repos=[
            SonarRepoScanItem(
                repo_slug=r.repo_slug,
                project_key=r.project_key,
                scan_status=r.scan_status,
                language=r.language,
            )
            for r in result.repos
        ],
    )


def _scan_plan_response(plan: SonarScanPlan) -> SonarScanPlanResponse:
    """Map the service ``SonarScanPlan`` dataclass → the response schema (7 frozen + PH-246)."""
    return SonarScanPlanResponse(
        project_key=plan.project_key,
        container_source=plan.container_source,
        host_source=plan.host_source,
        language=plan.language,
        supported=plan.supported,
        reason=plan.reason,
        exclusions=plan.exclusions,
        # PH-246 additive — self-describing per-repo element.
        repo_id=plan.repo_id,
        repo_slug=plan.repo_slug,
    )


@router.post("/{board_id}/sonarqube/scan", response_model=SonarScanResponse)
async def api_board_sonarqube_scan(
    board_id: str,
    admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarScanResponse:
    """Enqueue an on-demand per-board SonarQube scan ("Scan now", admin only).

    DISTINCT from ``sync``: ``sync`` re-polls a board's EXISTING analysis; ``scan``
    plans a NEW analysis run. Cheap + NON-blocking + never-500 — it does NOT launch the
    scanner (the backend can't ``docker compose run``); it resolves project_key + the
    container source path + language support and returns a ``scan_status`` ∈
    ``queued|running|unsupported|needs_dotnet_setup|disabled|unconfigured|error``. The
    actual analysis runs HOST-side via ``scripts/sonar-scan-board.sh <board-key>`` (which
    curls ``scan-plan``), then the next poll/sync ingests the fresh measures. A Unity/C#
    board returns ``needs_dotnet_setup`` honestly until the host .NET prerequisite is
    declared ready (SONAR_DOTNET_ENABLED=true) — NOT a fake ``queued`` (PH-257).
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    return _scan_result_response(
        await request_board_scan(session, board, requested_by=admin.id)
    )


@router.get("/{board_id}/sonarqube/scan-plan", response_model=SonarScanPlanResponse)
async def api_board_sonarqube_scan_plan(
    board_id: str,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarScanPlanResponse:
    """Return a board's FROZEN scan plan for the HOST runner (admin only).

    The seam that lets the host script stay dumb: ``scripts/sonar-scan-board.sh`` curls
    this ONE endpoint and gets everything it needs —
    ``{ project_key, container_source, host_source, language, supported, reason,
    exclusions }``. When ``supported`` it runs the scanner with
    ``-Dsonar.projectKey=<project_key> -Dsonar.sources=<container_source>``; when not it
    logs + exits 0. Pure resolution (no network, no scanner), never-500, SECRET-FREE
    (no token, no compose-internal ``sonarqube_url``). The JSON shape is FROZEN — PH-237
    (frontend C3) + the host script depend on these field names.
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    return _scan_plan_response(build_scan_plan(board))


@router.get(
    "/{board_id}/sonarqube/scan-plans",
    response_model=list[SonarScanPlanResponse],
)
async def api_board_sonarqube_scan_plans(
    board_id: str,
    _admin: Annotated[Actor, Depends(require_board_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[SonarScanPlanResponse]:
    """Return a board's PER-REPO scan plans for the HOST runner (admin only, PH-246).

    The multi-repo successor to the single-object ``/scan-plan`` (which is KEPT for
    back-compat — it returns the primary-repo plan the deployed host script still
    object-parses). This returns ``list[SonarScanPlanResponse]`` — one element per linked
    repository, each carrying the 7 FROZEN fields (PH-236/244) plus the additive
    ``repo_id``/``repo_slug`` so the watcher (PH-248) can iterate and scan each repo under
    its own ``project_key`` (primary inherits the board key; siblings ``<base>-<slug>``).
    A repo with no scannable source is an UNSCANNABLE element (``supported=False``) rather
    than dropped (the runner skips it; the others still scan). A board with no linked
    repos returns exactly ONE board-level plan = today's ``/scan-plan`` output. Pure
    resolution (no network, no scanner), never-500, SECRET-FREE (no token, no
    compose-internal ``sonarqube_url``).
    """
    board = await get_board(session, board_id)  # 404 on a truly missing board
    return [_scan_plan_response(plan) for plan in build_scan_plans(board)]


@router.get("/{board_id}/sonarqube/status", response_model=SonarSetupStatus)
async def api_board_sonarqube_status(
    board_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SonarSetupStatus:
    """Read a board's current SonarQube setup state (any board member).

    Pure read: assembles ``SonarSetupStatus`` from settings + the cached metric.
    Makes NO live probe (a read must not hang on a down server). PH-235: metric
    presence drives ``has_analysis`` / the ``status`` enum (``no_analysis`` vs
    ``ok``) — it is NEVER reported as a false ``unreachable``; ``unreachable`` is
    reserved for a genuine failed live sync. No ``/api/system/status`` probe.
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
    # PH-230: validate a non-empty repos_path at the API boundary using the same
    # rules as detect/sonar resolution (absolute, no '..', under HOST_HOME). An
    # empty string is allowed — it clears the path. RepoPathError → 422 (it is a
    # ValueError subclass authored for exactly this mapping).
    if payload.repos_path is not None and payload.repos_path.strip():
        try:
            repo_paths.to_container_path(payload.repos_path.strip())
        except repo_paths.RepoPathError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    await update_board(
        session,
        board,
        name=payload.name,
        description=payload.description,
        project_type=payload.project_type,
        roles=payload.roles,
        repos_path=payload.repos_path,
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
