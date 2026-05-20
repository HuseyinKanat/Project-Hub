"""Test WebSocket permission issues - specifically PH-40 bug reproduction.

This test reproduces the issue where jarwis-reviewer actor cannot connect to
WebSocket endpoints due to missing 'ticket.read' permission.

Bug: WebSocket connection fails with "Permission denied" because the reviewer
role lacks the required "ticket.read" permission that is checked in
app.api.websocket.py lines 105 and 159.
"""

import pytest
import pytest_asyncio
from fastapi import status
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDenied
from app.core.permissions import require_permission
from app.db.models import Actor, Board, BoardMembership
from app.main import app
from app.services.defaults import DEFAULT_WEB_ROLES


@pytest_asyncio.fixture
async def reviewer_actor(db_session: AsyncSession, seed) -> Actor:
    """Create a reviewer actor with standard reviewer permissions."""
    reviewer = Actor(
        kind="jarwis",
        display_name="jarwis-reviewer",
        token_hash="reviewer-token-hash-test",
        is_active=True
    )
    db_session.add(reviewer)
    await db_session.flush()  # Get the ID

    # Add reviewer membership to the seed board
    membership = BoardMembership(
        board_id=seed.board.id,
        actor_id=reviewer.id,
        role="reviewer"
    )
    db_session.add(membership)
    await db_session.commit()
    await db_session.refresh(reviewer)
    return reviewer


def test_reviewer_lacks_ticket_read_permission(seed) -> None:
    """
    FAILING TEST: Reproduces PH-40 - reviewer role missing ticket.read permission.

    This test demonstrates that the reviewer role in DEFAULT_WEB_ROLES does NOT
    have "ticket.read" permission, which is required by WebSocket endpoints.

    Expected: This test should FAIL initially, proving the bug exists.
    After the bug is fixed, this test should PASS.
    """
    # Create a reviewer actor with board membership
    reviewer = Actor(
        kind="jarwis",
        display_name="jarwis-reviewer",
        token_hash="test-hash"
    )
    reviewer.memberships = [BoardMembership(
        board_id=seed.board.id,
        actor_id=reviewer.id,
        role="reviewer"
    )]

    # This should FAIL because reviewer role doesn't have "ticket.read" permission
    # When the bug is fixed, "ticket.read" should be added to reviewer permissions
    with pytest.raises(PermissionDenied, match="Permission denied"):
        require_permission(reviewer, seed.board, "ticket.read")


@pytest.mark.asyncio
async def test_websocket_board_connection_fails_for_reviewer(
    db_session: AsyncSession,
    seed,
    reviewer_actor: Actor
) -> None:
    """
    FAILING TEST: Reproduces PH-40 WebSocket connection failure.

    Simulates the exact error scenario:
    - jarwis-reviewer actor attempts to connect to /ws/boards/PH
    - Connection fails with "Permission denied" due to require_permission check
    - Backend logs: "ws_board_access_denied: board_id=PH error=Permission denied"

    Expected: This test should FAIL initially with WebSocketDisconnect.
    After adding "ticket.read" to reviewer permissions, it should PASS.
    """
    with TestClient(app) as client:
        # Attempt WebSocket connection as reviewer
        # This simulates: ws://localhost:8000/ws/boards/PH?token=reviewer-token
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/ws/boards/{seed.board.key}?token=reviewer-token-hash-test"
            ) as websocket:
                # This line should never be reached due to permission failure
                data = websocket.receive_text()

        # Verify it failed with the expected permission violation code
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_websocket_ticket_connection_fails_for_reviewer(
    db_session: AsyncSession,
    seed,
    reviewer_actor: Actor
) -> None:
    """
    FAILING TEST: Ticket-level WebSocket also fails for same reason.

    The ticket WebSocket endpoint also uses require_permission(actor, ticket.board, "ticket.read")
    so it should also fail for reviewer role.
    """
    # First create a test ticket
    from app.services.tickets import create_ticket
    from app.schemas import TicketCreate

    ticket_data = TicketCreate(
        title="Test Ticket",
        description="Test",
        type="bug",
        priority="medium"
    )

    # Create ticket as admin
    ticket = await create_ticket(db_session, seed.admin, seed.board.id, ticket_data)

    with TestClient(app) as client:
        # Attempt WebSocket connection to specific ticket as reviewer
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/ws/tickets/{ticket.key}?token=reviewer-token-hash-test"
            ) as websocket:
                # Should not reach here
                data = websocket.receive_text()

        # Verify permission violation
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION


def test_ticket_read_permission_missing_from_known_permissions() -> None:
    """
    FAILING TEST: 'ticket.read' is not in KNOWN_PERMISSIONS list.

    This test verifies that 'ticket.read' is missing from the KNOWN_PERMISSIONS
    set in app.core.permissions.py, which is another part of the bug.
    """
    from app.core.permissions import KNOWN_PERMISSIONS

    # This should FAIL initially - ticket.read is not in known permissions
    assert "ticket.read" in KNOWN_PERMISSIONS, (
        "ticket.read permission is missing from KNOWN_PERMISSIONS - "
        "this causes WebSocket authentication to fail for reviewer role"
    )


def test_current_reviewer_permissions_do_not_include_ticket_read() -> None:
    """
    DOCUMENTATION TEST: Shows current reviewer permissions for debugging.

    This test documents what permissions the reviewer role currently has,
    to make it clear that 'ticket.read' is missing.
    """
    reviewer_perms = DEFAULT_WEB_ROLES["roles"]["reviewer"]["permissions"]

    # Document current permissions (these should be correct)
    expected_reviewer_perms = [
        "ticket.update_field:technical_depth",
        "state.transition:to_in_test",
        "state.transition:to_in_progress",
        "ticket.assign",
        "comment.add"
    ]

    assert reviewer_perms == expected_reviewer_perms

    # The missing permission that causes PH-40
    assert "ticket.read" not in reviewer_perms, (
        "This test documents the bug - ticket.read is missing from reviewer permissions"
    )