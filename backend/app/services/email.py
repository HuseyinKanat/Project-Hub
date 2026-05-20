"""Email service for sending notification emails."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def send_email_async(
    to_email: str,
    subject: str,
    body: str,
    *,
    from_email: str | None = None,
) -> bool:
    """Send an email asynchronously. Returns True if successful."""
    settings = get_settings()

    if not settings.email_enabled:
        logger.debug(f"Email disabled, skipping email to {to_email}")
        return True

    from_email = from_email or settings.email_from

    try:
        # Run the synchronous SMTP code in a thread pool
        await asyncio.get_event_loop().run_in_executor(
            None,
            _send_email_sync,
            to_email,
            subject,
            body,
            from_email
        )
        logger.info(f"Email sent successfully to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False


def _send_email_sync(to_email: str, subject: str, body: str, from_email: str) -> None:
    """Synchronous email sending using SMTP."""
    settings = get_settings()

    # Create message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    # Add plain text part
    text_part = MIMEText(body, "plain")
    msg.attach(text_part)

    # Connect to SMTP server and send
    if settings.smtp_use_ssl:
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
        if settings.smtp_use_tls:
            context = ssl.create_default_context()
            server.starttls(context=context)

    try:
        if settings.smtp_user and settings.smtp_password:
            server.login(settings.smtp_user, settings.smtp_password)

        server.send_message(msg)

    finally:
        server.quit()


async def send_notification_emails(
    recipients: list[dict[str, Any]],  # [{"email": str, "actor_id": UUID}, ...]
    subject: str,
    body: str,
) -> list[dict[str, Any]]:
    """
    Send notification emails to multiple recipients.

    Returns list of results: [{"email": str, "success": bool, "actor_id": UUID}, ...]
    """
    if not recipients:
        return []

    results = []

    # Send emails concurrently
    tasks = []
    for recipient in recipients:
        email = recipient["email"]
        actor_id = recipient["actor_id"]

        task = asyncio.create_task(
            _send_to_recipient(email, subject, body, actor_id)
        )
        tasks.append(task)

    # Wait for all emails to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Convert exceptions to failure results
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Email task failed for {recipients[i]['email']}: {result}")
            results[i] = {
                "email": recipients[i]["email"],
                "success": False,
                "actor_id": recipients[i]["actor_id"],
            }

    return results


async def _send_to_recipient(email: str, subject: str, body: str, actor_id: Any) -> dict[str, Any]:
    """Send email to a single recipient and return result."""
    success = await send_email_async(email, subject, body)
    return {
        "email": email,
        "success": success,
        "actor_id": actor_id,
    }