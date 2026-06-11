"""Email templates for notifications."""

from __future__ import annotations

from app.db.models import Ticket


def state_change_email_template(
    ticket: Ticket, old_state: str, new_state: str, actor_name: str
) -> tuple[str, str]:
    """Generate email subject and body for state change notifications."""
    subject = f"[{ticket.board.name}] {ticket.key}: State changed to {new_state}"

    body = f"""
Hi there,

The ticket "{ticket.title}" has been updated:

Ticket: {ticket.key}
Board: {ticket.board.name}
State: {old_state} → {new_state}
Changed by: {actor_name}

You can view the ticket here: {_get_ticket_url(ticket)}

Best regards,
Project Hub Notifications
"""
    return subject.strip(), body.strip()


def comment_added_email_template(ticket: Ticket, author_name: str, comment_text: str) -> tuple[str, str]:
    """Generate email subject and body for new comment notifications."""
    subject = f"[{ticket.board.name}] {ticket.key}: New comment by {author_name}"

    # Truncate comment if too long
    comment_preview = comment_text[:200] + "..." if len(comment_text) > 200 else comment_text

    body = f"""
Hi there,

A new comment has been added to "{ticket.title}":

Ticket: {ticket.key}
Board: {ticket.board.name}
Author: {author_name}

Comment:
{comment_preview}

You can view the full conversation here: {_get_ticket_url(ticket)}

Best regards,
Project Hub Notifications
"""
    return subject.strip(), body.strip()


def _get_ticket_url(ticket: Ticket) -> str:
    """Generate URL for ticket (placeholder for now)."""
    return f"http://localhost:5173/boards/{ticket.board_id}/tickets/{ticket.key}"