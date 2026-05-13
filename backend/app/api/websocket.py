"""WebSocket gateway for real-time ticket updates (PH-7).

Endpoints:
    /ws/boards/{board_id} - Subscribe to board-level events
    /ws/tickets/{ticket_id} - Subscribe to ticket-level events

Auth:
    Token passed via query param: ?token=xxx or subprotocol header
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db_session
from app.events.bus import EventBus, EventEnvelope
from app.services.actors import get_actor_from_token

logger = get_logger(__name__)
router = APIRouter()


def _get_token_from_websocket(websocket: WebSocket) -> str | None:
    """Extract bearer token from query param or subprotocol header."""
    # Try query param first: ?token=xxx
    token = websocket.query_params.get("token")
    if token:
        return token

    # Try Sec-WebSocket-Protocol header (common pattern for WS auth)
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    for proto in protocols.split(","):
        proto = proto.strip()
        if proto.startswith("token,"):
            return proto.split(",", 1)[1].strip()
        if proto.startswith("bearer,"):
            return proto.split(",", 1)[1].strip()

    # Try Authorization header (some clients support this)
    auth = websocket.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]

    return None


async def _authenticate_websocket(
    websocket: WebSocket,
    session: AsyncSession,
) -> tuple[Any, str | None]:
    """Authenticate WebSocket connection and return actor + token."""
    token = _get_token_from_websocket(websocket)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)

    # Validate token against database
    actor = await get_actor_from_token(session, token)
    if not actor:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)

    return actor, token


@router.websocket("/ws/boards/{board_id}")
async def websocket_board_endpoint(websocket: WebSocket, board_id: str) -> None:
    """WebSocket endpoint for board-level event streaming.

    Events streamed:
        - All ticket lifecycle events on this board (created, updated,
          state_changed, claimed, released, phase_updated, comment_added, etc.)

    Auth:
        Pass token via query: /ws/boards/PH?token=change-me-on-first-login
        Or via Sec-WebSocket-Protocol: token,change-me-on-first-login

    Protocol:
        - Binary/text messages from client are ignored (no client->server commands)
        - Server pushes JSON event envelopes
        - Connection closes on auth failure or server shutdown
    """
    await websocket.accept()

    async for session in get_db_session():
        try:
            actor, _token = await _authenticate_websocket(websocket, session)
        except WebSocketDisconnect:
            return

        # Check board membership (optional but good for security)
        from app.services.boards import get_board

        try:
            board = await get_board(session, board_id)
            # Verify actor has at least read access to this board
            from app.core.permissions import require_permission

            require_permission(actor, board, "ticket.read")
        except Exception as e:
            logger.warning("ws_board_access_denied: board_id=%s error=%s", board_id, str(e))
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Access denied to board",
            )
            return

    # Subscribe to board channel
    channel = f"board:{board.id}"
    logger.info("ws_connected: channel=%s actor_id=%s", channel, str(actor.id))

    try:
        async for envelope in EventBus.subscribe(channel):
            try:
                await websocket.send_text(envelope.to_json())
            except Exception as e:
                logger.warning("ws_send_failed: %s (channel=%s)", str(e), channel)
                break
    except WebSocketDisconnect:
        logger.info("ws_disconnected: channel=%s actor_id=%s", channel, str(actor.id))
    except Exception as e:
        logger.warning("ws_error: %s (channel=%s)", str(e), channel)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws/tickets/{ticket_id}")
async def websocket_ticket_endpoint(websocket: WebSocket, ticket_id: str) -> None:
    """WebSocket endpoint for ticket-level event streaming.

    More focused stream than board-level; only events for a specific ticket.

    Auth: Same as board endpoint (token query param or subprotocol).
    """
    await websocket.accept()

    async for session in get_db_session():
        try:
            actor, _token = await _authenticate_websocket(websocket, session)
        except WebSocketDisconnect:
            return

        # Verify ticket exists and actor has access
        from app.services.tickets import get_ticket

        try:
            ticket = await get_ticket(session, ticket_id)
            from app.core.permissions import require_permission

            require_permission(actor, ticket.board, "ticket.read")
            channel = f"ticket:{ticket.id}"
        except Exception as e:
            logger.warning("ws_ticket_access_denied: ticket_id=%s error=%s", ticket_id, str(e))
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Access denied to ticket",
            )
            return

    logger.info("ws_connected: channel=%s actor_id=%s", channel, str(actor.id))

    try:
        async for envelope in EventBus.subscribe(channel):
            try:
                await websocket.send_text(envelope.to_json())
            except Exception as e:
                logger.warning("ws_send_failed: %s (channel=%s)", str(e), channel)
                break
    except WebSocketDisconnect:
        logger.info("ws_disconnected: channel=%s actor_id=%s", channel, str(actor.id))
    except Exception as e:
        logger.warning("ws_error: %s (channel=%s)", str(e), channel)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
