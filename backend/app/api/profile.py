"""User profile + per-owner project-path REST endpoints (PH-322).

GET  /api/profile                — the caller's own owner_slug + resolved owner
PUT  /api/profile                — set the caller's human owner_slug (human-only)
GET  /api/profile/project-paths  — the caller's own local path on a board (?board=)
PUT  /api/profile/project-paths  — upsert (or remove) the caller's path on a board

Every route authenticates with ANY Bearer token (a human OR a jarwis role token —
so ``jarwis-init`` can curl the project-paths write under the @suffix-derived owner,
no human owner_slug needed). Owner is ALWAYS derived from the token, never trusted
from a payload; a payload ``owner`` that names someone else is a 403.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.exceptions import PermissionDenied
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import (
    ProfileResponse,
    ProfileUpdate,
    ProjectPathResponse,
    ProjectPathUpsert,
)
from app.services.boards import get_board
from app.services.owners import resolve_owner_slug, set_owner_slug
from app.services.project_paths import get_project_path, set_project_path
from app.services.serializers import project_path_response

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _profile_response(actor: Actor) -> ProfileResponse:
    return ProfileResponse(
        actor_id=actor.id,
        display_name=actor.display_name,
        kind=actor.kind,
        owner_slug=actor.owner_slug,
        owner=resolve_owner_slug(actor),
    )


@router.get("", response_model=ProfileResponse)
async def api_get_profile(
    actor: Annotated[Actor, Depends(current_actor)],
) -> ProfileResponse:
    """Return the caller's own profile (raw owner_slug column + effective owner)."""
    return _profile_response(actor)


@router.put("", response_model=ProfileResponse)
async def api_set_profile(
    payload: ProfileUpdate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProfileResponse:
    """Set the caller's human ``owner_slug``.

    Human-only (an agent token → 403; agents derive owner from the display_name
    suffix). A slug failing ``^[a-z0-9][a-z0-9-]{0,19}$`` → 422; a slug already held
    by another actor → 409.
    """
    updated = await set_owner_slug(session, actor, payload.owner_slug)
    return _profile_response(updated)


@router.get("/project-paths", response_model=ProjectPathResponse)
async def api_get_project_path(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    board: Annotated[str, Query(description="Board KEY or UUID")],
) -> ProjectPathResponse:
    """Return the caller's OWN local path on ``board`` (422 if no owner, 403 if not a member)."""
    board_obj = await get_board(session, board)
    owner, row = await get_project_path(session, actor, board_obj)
    return project_path_response(board_obj.key, owner, row)


@router.put("/project-paths", response_model=ProjectPathResponse)
async def api_set_project_path(
    payload: ProjectPathUpsert,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectPathResponse:
    """Upsert (or remove, when ``local_path`` is empty) the caller's path on a board.

    The path is written under the TOKEN-derived owner. A payload ``owner`` that names
    a DIFFERENT resolved owner → 403 (self-write guard, never a cross-owner write).
    """
    board_obj = await get_board(session, payload.board)
    token_owner = resolve_owner_slug(actor)
    if payload.owner is not None and token_owner is not None and payload.owner != token_owner:
        raise PermissionDenied(required="project_path.owner:self", have=[])
    owner, row = await set_project_path(session, actor, board_obj, payload.local_path)
    return project_path_response(board_obj.key, owner, row)
