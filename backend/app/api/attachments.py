"""Ticket evidence attachment REST endpoints (PH-296).

Routes live under the ``/api/tickets`` prefix (alongside the tickets router):

    POST   /api/tickets/{ticket_id}/attachments                     (multipart)
    GET    /api/tickets/{ticket_id}/attachments                     (list metadata)
    GET    /api/tickets/{ticket_id}/attachments/{attachment_id}     (one metadata)
    GET    /api/tickets/{ticket_id}/attachments/{attachment_id}/content

The content route is the only one that streams bytes. It carries a bespoke
authenticator (``_actor_for_content``) that accepts the token via the standard
``Authorization: Bearer`` header OR a ``?token=`` query param — browsers/`<img>`/
`<video>` tags cannot set headers, so range-served media needs query-param auth
(mirrors the WebSocket gateway's token resolution). Starlette's ``FileResponse``
handles ``Range`` (206 + Content-Range) and ``Accept-Ranges`` natively.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_actor
from app.core.exceptions import NotFound, PermissionDenied
from app.db.models import Actor
from app.db.session import get_db_session
from app.schemas import AttachmentResponse
from app.services.actors import get_actor_from_token
from app.services.attachments import (
    create_attachment,
    get_attachment,
    list_attachments,
    resolve_attachment_path,
)
from app.services.serializers import attachment_response

router = APIRouter(prefix="/api/tickets", tags=["attachments"])

# Header stripped from FileResponse serving to stop content-type sniffing of
# user-supplied blobs (e.g. an uploaded .txt being interpreted as HTML/JS).
_NOSNIFF_HEADERS = {"X-Content-Type-Options": "nosniff"}


async def _actor_for_content(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    token: Annotated[str | None, Query()] = None,
) -> Actor:
    """Resolve the actor for the content route from a bearer header OR ?token=."""
    raw: str | None = None
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        raw = auth[7:]
    elif token:
        raw = token
    if not raw:
        raise PermissionDenied(required="authenticated_actor", have=[])
    actor = await get_actor_from_token(session, raw)
    if actor is None:
        raise PermissionDenied(required="valid_bearer_token", have=[])
    return actor


@router.post(
    "/{ticket_id}/attachments",
    response_model=AttachmentResponse,
    status_code=201,
)
async def api_create_attachment(
    ticket_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "other",
    run_id: Annotated[str | None, Form()] = None,
) -> AttachmentResponse:
    """Upload an evidence file (human/REST path). Streams the multipart body to
    storage while enforcing the size cap + content-type allowlist."""
    file.file.seek(0)
    attachment = await create_attachment(
        session,
        actor=actor,
        ticket_id=ticket_id,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        open_stream=lambda: file.file,
        close_after=False,  # FastAPI owns the UploadFile handle lifecycle
        kind=kind,
        source="human",
        run_id=run_id,
    )
    return attachment_response(attachment)


@router.get(
    "/{ticket_id}/attachments",
    response_model=list[AttachmentResponse],
)
async def api_list_attachments(
    ticket_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[AttachmentResponse]:
    attachments = await list_attachments(session, actor=actor, ticket_id=ticket_id)
    return [attachment_response(a) for a in attachments]


@router.get(
    "/{ticket_id}/attachments/{attachment_id}",
    response_model=AttachmentResponse,
)
async def api_get_attachment(
    ticket_id: str,
    attachment_id: str,
    actor: Annotated[Actor, Depends(current_actor)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AttachmentResponse:
    attachment = await get_attachment(session, actor=actor, attachment_id=attachment_id)
    return attachment_response(attachment)


@router.get("/{ticket_id}/attachments/{attachment_id}/content")
async def api_get_attachment_content(
    ticket_id: str,
    attachment_id: str,
    actor: Annotated[Actor, Depends(_actor_for_content)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    download: Annotated[int, Query()] = 0,
) -> FileResponse:
    """Serve an attachment's raw bytes. ``?download=1`` forces an attachment
    disposition with the original filename; otherwise it is served inline."""
    attachment = await get_attachment(session, actor=actor, attachment_id=attachment_id)
    path = resolve_attachment_path(attachment)
    if not await asyncio.to_thread(path.is_file):
        raise NotFound("attachment content")
    return FileResponse(
        path,
        media_type=attachment.content_type,
        headers=dict(_NOSNIFF_HEADERS),
        filename=attachment.filename,
        content_disposition_type="attachment" if download else "inline",
    )
