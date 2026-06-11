"""GitHub webhook receiver endpoint."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_board_by_key
from app.db.models import Board
from app.db.session import get_db_session
from app.git.webhook import handle_branch_delete, handle_pull_request, handle_push, verify_github_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/boards/{board_key}/webhook", tags=["git"])


@router.post("/github")
async def github_webhook(
    board_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    board: Annotated[Board, Depends(get_board_by_key)],
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    body = await request.body()
    webhook_secret: str = board.roles.get("webhook_secret", "") if isinstance(board.roles, dict) else ""

    if webhook_secret:
        if not verify_github_signature(x_hub_signature_256, body, webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = x_github_event or "unknown"
    logger.info("GitHub webhook: board=%s event=%s", board.key, event)

    if event == "push":
        linked = await handle_push(session, board, payload)
        return {"ok": True, "event": event, "linked_tickets": linked}

    if event == "pull_request":
        linked = await handle_pull_request(session, board, payload)
        return {"ok": True, "event": event, "linked_tickets": linked}

    if event == "delete":
        linked = await handle_branch_delete(session, board, payload)
        return {"ok": True, "event": event, "linked_tickets": linked}

    return {"ok": True, "event": event, "linked_tickets": []}
