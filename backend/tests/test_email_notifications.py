"""Tests for email notification functionality."""

import pytest
from unittest.mock import patch, AsyncMock

from app.services.email import send_email_async
from app.services.email_templates import state_change_email_template, comment_added_email_template
from app.services.user_preferences import set_user_preference, get_user_email, get_email_notification_enabled


def test_state_change_email_template():
    """Test state change email template generation."""
    # Mock ticket object
    class MockBoard:
        name = "Test Board"

    class MockTicket:
        key = "PH-42"
        title = "Test ticket"
        board = MockBoard()
        board_id = "board-123"

    ticket = MockTicket()
    subject, body = state_change_email_template(ticket, "in_progress", "in_review", "John Doe")

    assert "PH-42" in subject
    assert "in_review" in subject
    assert "Test Board" in subject

    assert "PH-42" in body
    assert "in_progress → in_review" in body
    assert "John Doe" in body
    assert "Test ticket" in body


def test_comment_added_email_template():
    """Test comment added email template generation."""
    # Mock ticket object
    class MockBoard:
        name = "Test Board"

    class MockTicket:
        key = "PH-42"
        title = "Test ticket"
        board = MockBoard()
        board_id = "board-123"

    ticket = MockTicket()
    comment_text = "This is a test comment"
    subject, body = comment_added_email_template(ticket, "Jane Smith", comment_text)

    assert "PH-42" in subject
    assert "Jane Smith" in subject
    assert "Test Board" in subject

    assert "PH-42" in body
    assert comment_text in body
    assert "Jane Smith" in body


@pytest.mark.anyio
async def test_send_email_disabled():
    """Test that email sending is skipped when disabled."""
    with patch("app.services.email.get_settings") as mock_settings:
        mock_settings.return_value.email_enabled = False

        result = await send_email_async(
            "test@example.com",
            "Test Subject",
            "Test Body"
        )

        assert result is True  # Returns True even when disabled


@pytest.mark.anyio
async def test_send_email_with_smtp_error():
    """Test email sending with SMTP error."""
    with patch("app.services.email.get_settings") as mock_settings:
        mock_settings.return_value.email_enabled = True
        mock_settings.return_value.smtp_host = "invalid-host"
        mock_settings.return_value.smtp_port = 587
        mock_settings.return_value.email_from = "noreply@test.com"
        mock_settings.return_value.smtp_use_tls = False
        mock_settings.return_value.smtp_use_ssl = False
        mock_settings.return_value.smtp_user = ""
        mock_settings.return_value.smtp_password = ""

        result = await send_email_async(
            "test@example.com",
            "Test Subject",
            "Test Body"
        )

        assert result is False  # Should fail with invalid host