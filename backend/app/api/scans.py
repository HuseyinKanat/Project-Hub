"""PH-239 — SonarQube scan-job lifecycle endpoints (the host-watcher seam).

These three endpoints are NOT board-scoped (a scan job is addressed by its own id),
so they live on their own ``/api/scans`` router rather than under ``/api/boards``.
They are consumed by the host-side watcher (``scripts/sonar-scan-watcher.sh``):

  GET  /api/scans/pending           → the queue the watcher long-polls
  POST /api/scans/{job_id}/claim    → mark a job running (double-run guarded → 409)
  POST /api/scans/{job_id}/complete → report outcome + IMMEDIATE ingest on success

Auth: an authenticated actor (the watcher sends the same admin bearer the host
scripts already use via ``SONAR_API_TOKEN``). SECRET-FREE — no token, no
compose-internal ``sonarqube_url`` ever crosses these (PH-203/223/235 contract).

never-500 posture: the SonarQube-degradation paths inside ``complete_scan_job`` are
already error-isolated (``poll_board`` never raises for sonar errors). The only
non-200s here are DELIBERATE: 404 (unknown job id) and 409 (illegal lifecycle
transition — the double-run guard) — both honest client-error signals, not crashes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor, SonarScanJob
from app.db.session import get_db_session
from app.schemas import (
    PendingScanItem,
    ScanCompleteRequest,
    ScanJobResponse,
)
from app.services.sonarqube import (
    ScanJobConflict,
    ScanJobNotFound,
    claim_scan_job,
    complete_scan_job,
    list_pending_scans,
)

router = APIRouter(prefix="/api/scans", tags=["scans"])

# S1192: the 404 detail string is shared by _parse_job_id() and both
# ScanJobNotFound handlers — hoisted to a constant so the literal lives once.
_JOB_NOT_FOUND = "scan job not found"


def _job_response(job: SonarScanJob) -> ScanJobResponse:
    """Map the ORM job → the secret-free response schema."""
    return ScanJobResponse(
        job_id=job.id,
        board_id=job.board_id,
        project_key=job.project_key,
        state=job.state,
        detail=job.detail,
    )


def _parse_job_id(job_id: str) -> uuid.UUID:
    """Coerce a path job_id string → UUID, mapping a malformed id to 404 (not 422).

    Keeps the watcher's error handling simple: an unknown OR malformed job id both
    surface as 404 rather than a 422 validation error.
    """
    try:
        return uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_JOB_NOT_FOUND
        ) from exc


@router.get("/pending", response_model=list[PendingScanItem])
async def api_scans_pending(
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[PendingScanItem]:
    """List queued scan jobs for the host watcher (authenticated). SECRET-FREE.

    Returns ``[{job_id, board_key, project_key}]`` for every ``queued`` job (oldest
    first), or ``[]`` when idle. Cheap (indexed on ``state``) — safe to long-poll.
    """
    pending = await list_pending_scans(session)
    return [
        PendingScanItem(
            job_id=p.job_id,
            board_key=p.board_key,
            project_key=p.project_key,
            repo_slug=p.repo_slug,  # PH-246: which repo this job targets
        )
        for p in pending
    ]


@router.post("/{job_id}/claim", response_model=ScanJobResponse)
async def api_scan_claim(
    job_id: str,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanJobResponse:
    """Claim a queued job → running (authenticated). Double-run guarded.

    ``queued`` → ``running`` (sets ``started_at``). A second claim on the same job
    (already running/done/failed) → 409 Conflict (R2: prevents two watcher instances
    double-running). Unknown / malformed job id → 404.
    """
    parsed = _parse_job_id(job_id)
    try:
        job = await claim_scan_job(session, parsed)
    except ScanJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_JOB_NOT_FOUND
        ) from exc
    except ScanJobConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return _job_response(job)


@router.post("/{job_id}/complete", response_model=ScanJobResponse)
async def api_scan_complete(
    job_id: str,
    payload: ScanCompleteRequest,
    _actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanJobResponse:
    """Report a running job's outcome → done|failed (authenticated).

    ``success=true`` → ``done`` + an IMMEDIATE ``poll_board`` ingest of the fresh
    analysis (the 300s cron is the backstop for the SonarQube async-indexing race).
    ``success=false`` → ``failed`` with ``detail`` persisted and NO ingest. Completing
    a job not in ``running`` → 409; unknown / malformed id → 404.
    """
    parsed = _parse_job_id(job_id)
    try:
        job = await complete_scan_job(
            session,
            parsed,
            success=payload.success,
            detail=payload.detail,
        )
    except ScanJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_JOB_NOT_FOUND
        ) from exc
    except ScanJobConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return _job_response(job)
