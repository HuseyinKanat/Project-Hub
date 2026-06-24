"""Concept-tag REST endpoints (PH-273, epic PH-271 child 2/7).

GLOBAL router (no board scope) for the cross-board concept-tag taxonomy. Permission
checks live in the SERVICE layer (mirroring ``services/tickets.py``), NOT the
router: ``tag.read`` (global) gates reads, ``tag.manage`` (admin-on-any-board)
gates writes + link manage. Ticket attach/detach (board-scoped ``tag.assign``)
lives on the tickets router so it inherits ``/api/tickets``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import (
    ConceptTagCreate,
    ConceptTagLinkCreate,
    ConceptTagLinkResponse,
    ConceptTagListResponse,
    ConceptTagResponse,
    ConceptTagUpdate,
)
from app.services.concept_tags import (
    create_concept_tag,
    create_link,
    delete_concept_tag,
    delete_link,
    list_concept_tags,
    read_concept_tag,
    update_concept_tag,
)
from app.services.serializers import concept_tag_link_response, concept_tag_response

router = APIRouter(prefix="/api/concept-tags", tags=["concept-tags"])


@router.get("", response_model=ConceptTagListResponse)
async def api_list_concept_tags(
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConceptTagListResponse:
    pairs = await list_concept_tags(session, actor)
    return ConceptTagListResponse(
        tags=[concept_tag_response(tag, links) for tag, links in pairs]
    )


@router.post("", response_model=ConceptTagResponse, status_code=201)
async def api_create_concept_tag(
    payload: ConceptTagCreate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConceptTagResponse:
    tag, links = await create_concept_tag(session, actor=actor, payload=payload)
    return concept_tag_response(tag, links)


@router.get("/{tag_id}", response_model=ConceptTagResponse)
async def api_get_concept_tag(
    tag_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConceptTagResponse:
    tag, links = await read_concept_tag(session, actor, tag_id)
    return concept_tag_response(tag, links)


@router.patch("/{tag_id}", response_model=ConceptTagResponse)
async def api_update_concept_tag(
    tag_id: str,
    payload: ConceptTagUpdate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConceptTagResponse:
    tag, links = await update_concept_tag(
        session, actor=actor, tag_id=tag_id, payload=payload
    )
    return concept_tag_response(tag, links)


@router.delete("/{tag_id}", status_code=204)
async def api_delete_concept_tag(
    tag_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await delete_concept_tag(session, actor=actor, tag_id=tag_id)
    return Response(status_code=204)


@router.post(
    "/{tag_id}/links", response_model=ConceptTagLinkResponse, status_code=201
)
async def api_create_concept_tag_link(
    tag_id: str,
    payload: ConceptTagLinkCreate,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConceptTagLinkResponse:
    link = await create_link(session, actor=actor, tag_id=tag_id, payload=payload)
    return concept_tag_link_response(link)


@router.delete("/{tag_id}/links/{target_tag_id}", status_code=204)
async def api_delete_concept_tag_link(
    tag_id: str,
    target_tag_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await delete_link(
        session, actor=actor, tag_id=tag_id, target_tag_id=target_tag_id
    )
    return Response(status_code=204)
