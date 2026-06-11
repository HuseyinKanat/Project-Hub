"""Test WebSocket permission fixes for PH-40."""

import pytest

from app.core.permissions import KNOWN_PERMISSIONS
from app.services.defaults import DEFAULT_WEB_ROLES


def test_ticket_read_permission_in_known_permissions():
    """Test that ticket.read is properly added to KNOWN_PERMISSIONS."""
    assert "ticket.read" in KNOWN_PERMISSIONS


def test_reviewer_has_ticket_read_permission():
    """Test that reviewer role has ticket.read permission for WebSocket access."""
    reviewer_role = DEFAULT_WEB_ROLES["roles"]["reviewer"]
    assert "ticket.read" in reviewer_role["permissions"]


def test_all_roles_have_ticket_read_for_websocket():
    """Test that all roles that might need WebSocket access have ticket.read."""
    roles_needing_websocket = ["architect", "frontend_dev", "backend_dev", "reviewer", "qa", "unity_dev", "unity_scene_manager"]

    for role_name in roles_needing_websocket:
        role = DEFAULT_WEB_ROLES["roles"][role_name]
        assert "ticket.read" in role["permissions"], f"Role {role_name} missing ticket.read permission"


def test_pm_and_admin_have_access_through_wildcard():
    """Test that PM and admin still have access through existing permissions."""
    pm_role = DEFAULT_WEB_ROLES["roles"]["pm"]
    admin_role = DEFAULT_WEB_ROLES["roles"]["admin"]

    # PM has ticket.* permissions via other permissions and state.transition:*
    assert "ticket.create" in pm_role["permissions"]
    assert "ticket.update_field" in pm_role["permissions"]

    # Admin has wildcard
    assert "*" in admin_role["permissions"]