"""WebSocket gateway for real-time ticket updates (PH-7).

Endpoints:
    /ws/boards/{board_id} - Subscribe to board-level events
    /ws/tickets/{ticket_id} - Subscribe to ticket-level events

Auth:
    Token passed via query param: ?token=xxx or subprotocol header
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.websocket_manager import websocket_manager
from app.db.session import SessionLocal
from app.events.bus import EventBus
from app.services.actors import get_actor_from_token

logger = get_logger(__name__)
router = APIRouter()

# Client-facing message for unexpected handler failures (reused for both the
# JSON error envelope and the WebSocket close reason).
_INTERNAL_SERVER_ERROR = "Internal server error"


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
        logger.warning("ws_auth_failed: missing_token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)

    # Validate token against database
    actor = await get_actor_from_token(session, token)
    if not actor:
        # Enhanced logging for debugging Jarwis authentication issues
        token_preview = token[:8] + "..." if len(token) > 8 else token
        logger.warning(
            "ws_auth_failed: invalid_token token_preview=%s token_length=%d",
            token_preview,
            len(token)
        )

        # Check if this looks like a Jarwis token (hex format, length 48)
        if len(token) == 48 and all(c in '0123456789abcdef' for c in token.lower()):
            logger.error(
                "ws_auth_jarwis_token_failed: %s possible_jarwis_auth_bug=True",
                token_preview,
            )
        elif token == "change-me-on-first-login":
            logger.error(
                "ws_auth_admin_token_failed: admin_token_not_working "
                "possible_admin_deactivated=True"
            )
        else:
            logger.warning("ws_auth_unknown_token_format: token_preview=%s", token_preview)

        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)

    logger.info(
        "ws_auth_success: actor=%s actor_id=%s token_preview=%s",
        actor.display_name,
        actor.id,
        token[:8] + "..." if len(token) > 8 else token
    )
    return actor, token


async def _handle_client_message(connection_id: str, websocket: WebSocket, message: str) -> None:
    """Handle incoming message from client (mainly ping-pong)."""
    try:
        data = json.loads(message)
        msg_type = data.get("type")

        if msg_type == "ping":
            # Handle ping and send pong
            await websocket_manager.handle_ping(connection_id, websocket)
        else:
            logger.debug(
                "unknown_client_message: type=%s connection_id=%s", msg_type, connection_id
            )

    except json.JSONDecodeError:
        logger.warning(
            "invalid_json_from_client: connection_id=%s message=%s", connection_id, message[:100]
        )
    except Exception as e:
        logger.warning("client_message_error: connection_id=%s error=%s", connection_id, str(e))


@router.websocket("/ws/boards/{board_id}")
async def websocket_board_endpoint(websocket: WebSocket, board_id: str) -> None:
    """WebSocket endpoint for board-level event streaming with stability fixes.

    Events streamed:
        - All ticket lifecycle events on this board (created, updated,
          state_changed, claimed, released, phase_updated, comment_added, etc.)

    Auth:
        Pass token via query: /ws/boards/PH?token=change-me-on-first-login
        Or via Sec-WebSocket-Protocol: token,change-me-on-first-login

    Protocol:
        - Client can send ping messages: {"type": "ping"}
        - Server responds with pong: {"type": "pong", "timestamp": ..., "connection_id": ...}
        - Server pushes JSON event envelopes
        - Connection closes on auth failure, timeout, or server shutdown

    Stability improvements (PH-41):
        - Long-lived DB session throughout connection lifecycle
        - Ping-pong heartbeat mechanism
        - Connection health monitoring
        - Redis retry with exponential backoff
        - Graceful error handling with structured responses
    """
    connection_id = None
    session = None

    try:
        await websocket.accept()

        # Create long-lived session for entire connection duration
        # Use direct session creation to avoid context manager conflicts
        try:
            session = SessionLocal()
        except Exception as e:
            logger.error("ws_session_creation_failed: board_id=%s error=%s", board_id, str(e))
            await websocket.close(
                code=1011,
                reason=f"Database connection failed: {e!s}"
            )
            return

        try:
            actor, _token = await _authenticate_websocket(websocket, session)
        except WebSocketDisconnect:
            return
        except Exception as e:
            logger.warning("ws_auth_error: board_id=%s error=%s", board_id, str(e))
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"Authentication failed: {e!s}"
            )
            return

        # Check board membership
        from app.services.boards import get_board

        try:
            board = await get_board(session, board_id)
            # Verify actor has at least read access to this board
            from app.core.permissions import require_permission

            require_permission(actor, board, "ticket.read")
        except Exception as e:
            logger.warning("ws_board_access_denied: board_id=%s error=%s", board_id, str(e))

            # Structured error response for client
            error_response = {
                "error": "access_denied",
                "message": f"Access denied to board {board_id}",
                "retry_allowed": False
            }

            try:
                await websocket.send_text(json.dumps(error_response))
            except Exception:
                pass

            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Access denied to board",
            )
            return

        # Register connection with manager
        channel = f"board:{board.id}"
        conn_info = websocket_manager.register_connection(
            websocket=websocket,
            session=session,
            actor_id=str(actor.id),
            channel=channel
        )
        connection_id = conn_info.connection_id

        logger.info(
            "ws_connected: connection_id=%s channel=%s actor_id=%s",
            connection_id,
            channel,
            str(actor.id)
        )

        # Run bidirectional communication until one side completes.
        await _run_connection_tasks(connection_id, websocket, channel)

    except WebSocketDisconnect:
        logger.info("ws_disconnected: connection_id=%s", connection_id)
    except Exception as e:
        logger.error(
            "ws_fatal_error: connection_id=%s board_id=%s error=%s",
            connection_id,
            board_id,
            str(e)
        )

        # Try to send structured error to client
        if websocket.client_state.name != "DISCONNECTED":
            error_response = {
                "error": "server_error",
                "message": _INTERNAL_SERVER_ERROR,
                "retry_allowed": True
            }
            try:
                await websocket.send_text(json.dumps(error_response))
                await websocket.close(code=1011, reason=_INTERNAL_SERVER_ERROR)
            except Exception:
                pass

    finally:
        # Cleanup connection and session
        if connection_id:
            websocket_manager.unregister_connection(connection_id)
        elif session:
            # Fallback session cleanup if connection wasn't registered
            try:
                if session.is_active:
                    await session.close()
                    logger.debug("fallback_session_closed: board_id=%s", board_id)
            except Exception as e:
                logger.warning("session_cleanup_error: board_id=%s error=%s", board_id, str(e))


async def _run_connection_tasks(
    connection_id: str, websocket: WebSocket, channel: str
) -> None:
    """Run the event-stream + client-message tasks until one completes, then drain.

    Spawns both bidirectional tasks, waits for the FIRST to finish (connection
    close, error, etc.), cancels the remaining one, and absorbs its
    CancelledError via ``gather(return_exceptions=True)``. Behavior is identical
    across the board and ticket endpoints; extracted to keep each endpoint's
    cognitive complexity in check (PH-215).
    """
    event_task = asyncio.create_task(_handle_event_stream(connection_id, websocket, channel))
    client_task = asyncio.create_task(_handle_client_messages(connection_id, websocket))

    # Wait for either task to complete (connection closes, error, etc.)
    try:
        _done, pending = await asyncio.wait(
            [event_task, client_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks. gather(return_exceptions=True) absorbs the
        # deliberately-cancelled tasks' CancelledError without a broad
        # `except CancelledError` that would swallow our own cancellation.
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        logger.warning("ws_error: connection_id=%s error=%s", connection_id, str(e))


async def _handle_event_stream(connection_id: str, websocket: WebSocket, channel: str) -> None:
    """Handle Redis event stream for WebSocket connection."""
    logger.info("event_stream_start: connection_id=%s channel=%s", connection_id, channel)
    try:
        logger.debug("event_stream_subscribing: connection_id=%s", connection_id)
        subscription_iter = EventBus.subscribe(channel)
        logger.debug("event_stream_subscription_created: connection_id=%s", connection_id)

        async for envelope in subscription_iter:
            logger.debug("event_stream_received_envelope: connection_id=%s", connection_id)
            try:
                await websocket.send_text(envelope.to_json())
                websocket_manager.update_message_count(connection_id)
                logger.debug("event_sent: connection_id=%s event_type=%s", connection_id, envelope.type)

            except Exception as e:
                logger.warning(
                    "event_send_failed: connection_id=%s error=%s", connection_id, str(e)
                )
                break

    except Exception as e:
        logger.error("event_stream_error: connection_id=%s channel=%s error=%s",
                    connection_id, channel, str(e), exc_info=True)
    finally:
        logger.info("event_stream_end: connection_id=%s channel=%s", connection_id, channel)


async def _handle_client_messages(connection_id: str, websocket: WebSocket) -> None:
    """Handle incoming messages from client."""
    try:
        while True:
            message = await websocket.receive_text()
            await _handle_client_message(connection_id, websocket, message)

    except WebSocketDisconnect:
        # Normal client disconnect
        pass
    except Exception as e:
        logger.warning(
            "client_message_handler_error: connection_id=%s error=%s", connection_id, str(e)
        )


@router.websocket("/ws/tickets/{ticket_id}")
async def websocket_ticket_endpoint(websocket: WebSocket, ticket_id: str) -> None:
    """WebSocket endpoint for ticket-level event streaming with stability fixes.

    More focused stream than board-level; only events for a specific ticket.

    Auth: Same as board endpoint (token query param or subprotocol).

    Stability improvements (PH-41):
        - Same fixes as board endpoint: long-lived session, ping-pong, etc.
    """
    connection_id = None
    session = None

    try:
        await websocket.accept()

        # Create long-lived session for entire connection duration
        # Use direct session creation to avoid context manager conflicts
        try:
            session = SessionLocal()
        except Exception as e:
            logger.error("ws_session_creation_failed: ticket_id=%s error=%s", ticket_id, str(e))
            await websocket.close(
                code=1011,
                reason=f"Database connection failed: {e!s}"
            )
            return

        try:
            actor, _token = await _authenticate_websocket(websocket, session)
        except WebSocketDisconnect:
            return
        except Exception as e:
            logger.warning("ws_auth_error: ticket_id=%s error=%s", ticket_id, str(e))
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"Authentication failed: {e!s}"
            )
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

            # Structured error response for client
            error_response = {
                "error": "access_denied",
                "message": f"Access denied to ticket {ticket_id}",
                "retry_allowed": False
            }

            try:
                await websocket.send_text(json.dumps(error_response))
            except Exception:
                pass

            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Access denied to ticket",
            )
            return

        # Register connection with manager
        conn_info = websocket_manager.register_connection(
            websocket=websocket,
            session=session,
            actor_id=str(actor.id),
            channel=channel
        )
        connection_id = conn_info.connection_id

        logger.info(
            "ws_connected: connection_id=%s channel=%s actor_id=%s",
            connection_id,
            channel,
            str(actor.id)
        )

        # Run bidirectional communication until one side completes.
        await _run_connection_tasks(connection_id, websocket, channel)

    except WebSocketDisconnect:
        logger.info("ws_disconnected: connection_id=%s", connection_id)
    except Exception as e:
        logger.error(
            "ws_fatal_error: connection_id=%s ticket_id=%s error=%s",
            connection_id,
            ticket_id,
            str(e)
        )

        # Try to send structured error to client
        if websocket.client_state.name != "DISCONNECTED":
            error_response = {
                "error": "server_error",
                "message": _INTERNAL_SERVER_ERROR,
                "retry_allowed": True
            }
            try:
                await websocket.send_text(json.dumps(error_response))
                await websocket.close(code=1011, reason=_INTERNAL_SERVER_ERROR)
            except Exception:
                pass

    finally:
        # Cleanup connection and session
        if connection_id:
            websocket_manager.unregister_connection(connection_id)
        elif session:
            # Fallback session cleanup if connection wasn't registered
            try:
                if session.is_active:
                    await session.close()
                    logger.debug("fallback_session_closed: ticket_id=%s", ticket_id)
            except Exception as e:
                logger.warning("session_cleanup_error: ticket_id=%s error=%s", ticket_id, str(e))
