"""Background task service for async processing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BackgroundTaskRunner:
    """Simple background task runner for async operations."""

    def __init__(self):
        self._tasks: set[asyncio.Task] = set()

    def submit(self, coro: Callable[[], Any], name: str = "background_task") -> None:
        """Submit a coroutine to run in the background."""
        task = asyncio.create_task(coro(), name=name)
        self._tasks.add(task)

        # Clean up completed tasks
        task.add_done_callback(self._tasks.discard)

        # Log any exceptions
        task.add_done_callback(self._log_exceptions)

    def _log_exceptions(self, task: asyncio.Task) -> None:
        """Log exceptions from completed tasks."""
        try:
            exc = task.exception()
            if exc:
                logger.error(f"Background task {task.get_name()} failed: {exc}", exc_info=exc)
        except asyncio.CancelledError:
            logger.debug(f"Background task {task.get_name()} was cancelled")


# Global instance
background_runner = BackgroundTaskRunner()


def run_in_background(coro: Callable[[], Any], name: str = "background_task") -> None:
    """Submit a coroutine to run in the background."""
    background_runner.submit(coro, name)


async def send_notification_emails_background(
    recipients: list[dict[str, Any]],  # [{"email": str, "actor_id": UUID}, ...]
    subject: str,
    body: str,
) -> None:
    """Background task for sending notification emails."""
    from app.services.email import send_notification_emails

    if not recipients:
        logger.debug("No email recipients, skipping background email task")
        return

    logger.info(f"Sending notification emails to {len(recipients)} recipients: {subject}")

    try:
        results = await send_notification_emails(recipients, subject, body)

        # Log results
        successful = sum(1 for r in results if r.get("success", False))
        failed = len(results) - successful

        if failed > 0:
            logger.warning(f"Email notification results: {successful} successful, {failed} failed")
        else:
            logger.info(f"All {successful} notification emails sent successfully")

    except Exception as e:
        logger.error(f"Background email task failed: {e}", exc_info=e)