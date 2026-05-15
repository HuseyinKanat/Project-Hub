"""GitHub webhook event handlers."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Board, Ticket
from app.events import publish_ticket_event
from app.git.parser import ParsedCommit, expected_branch_name, parse_commit
from app.services.history import write_history

logger = logging.getLogger(__name__)

GITHUB_BOT_ACTOR_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def verify_github_signature(signature_header: str | None, body: bytes, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def _find_ticket_by_key(session: AsyncSession, key: str, board_id: Any) -> Ticket | None:
    return (
        await session.execute(
            select(Ticket).where(
                Ticket.key == key.upper(),
                Ticket.board_id == board_id,
                Ticket.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _get_system_actor_id(session: AsyncSession, board: Board) -> Any:
    return board.created_by


async def handle_push(
    session: AsyncSession, board: Board, payload: dict[str, Any]
) -> list[str]:
    """Process push event; link commits to tickets via history. Returns list of linked ticket keys."""
    commits: list[dict[str, Any]] = payload.get("commits", [])
    linked: list[str] = []

    actor_id = await _get_system_actor_id(session, board)

    for raw in commits:
        sha: str = raw.get("id", "")
        message: str = raw.get("message", "")
        author: str = raw.get("author", {}).get("name", "unknown")
        url: str = raw.get("url", "")

        pc = parse_commit(sha=sha, message=message, author=author, url=url)

        for key in pc.ticket_keys:
            ticket = await _find_ticket_by_key(session, key, board.id)
            if ticket is None:
                logger.debug("commit %s references unknown ticket %s on board %s", sha[:8], key, board.key)
                continue

            history = await write_history(
                session,
                ticket_id=ticket.id,
                actor_id=actor_id,
                event_type="git_commit_linked",
                metadata={
                    "sha": sha,
                    "sha_short": sha[:8],
                    "message": pc.message,
                    "author": pc.author,
                    "url": pc.url,
                    "commit_type": pc.commit_type,
                    "is_conventional": pc.is_conventional,
                    "branch": payload.get("ref", "").removeprefix("refs/heads/"),
                },
            )
            await session.flush()
            await publish_ticket_event(history, ticket, None)  # type: ignore[arg-type]
            linked.append(key)

            if not pc.is_conventional:
                warn_history = await write_history(
                    session,
                    ticket_id=ticket.id,
                    actor_id=actor_id,
                    event_type="git_commit_invalid_format",
                    metadata={
                        "sha": sha[:8],
                        "message": pc.message,
                        "expected_format": "feat(PH-14): describe change",
                    },
                )
                await session.flush()
                await publish_ticket_event(warn_history, ticket, None)  # type: ignore[arg-type]
                logger.warning(
                    "Non-conventional commit %s on ticket %s: %r", sha[:8], key, pc.message
                )

            if ticket.branch_name is None:
                ticket.branch_name = expected_branch_name(ticket.key, ticket.title)
                await session.flush()

    await session.commit()
    return linked


async def handle_branch_delete(
    session: AsyncSession, board: Board, payload: dict[str, Any]
) -> list[str]:
    """Process GitHub 'delete' event (branch/tag deletion)."""
    ref_type: str = payload.get("ref_type", "")
    if ref_type != "branch":
        return []

    branch: str = payload.get("ref", "")
    if not branch:
        return []

    from app.git.parser import TICKET_KEY_RE
    keys = TICKET_KEY_RE.findall(branch.upper())
    if not keys:
        return []

    actor_id = await _get_system_actor_id(session, board)
    linked: list[str] = []

    for key in keys:
        ticket = await _find_ticket_by_key(session, key, board.id)
        if ticket is None:
            continue

        history = await write_history(
            session,
            ticket_id=ticket.id,
            actor_id=actor_id,
            event_type="git_branch_deleted",
            metadata={"branch": branch, "reason": "github_delete_event"},
        )
        await session.flush()
        await publish_ticket_event(history, ticket, None)  # type: ignore[arg-type]

        if ticket.branch_name == branch:
            ticket.branch_name = None
            await session.flush()
            logger.info("Cleared branch_name for ticket %s (delete event)", key)

        linked.append(key)

    await session.commit()
    return linked


async def handle_pull_request(
    session: AsyncSession, board: Board, payload: dict[str, Any]
) -> list[str]:
    """Process pull_request event; link PR to ticket via history."""
    action: str = payload.get("action", "")
    if action not in ("opened", "closed", "reopened"):
        return []

    pr = payload.get("pull_request", {})
    pr_number: int = pr.get("number", 0)
    pr_title: str = pr.get("title", "")
    pr_url: str = pr.get("html_url", "")
    pr_state: str = pr.get("state", "")
    merged: bool = pr.get("merged", False)
    head_branch: str = pr.get("head", {}).get("ref", "")

    from app.git.parser import TICKET_KEY_RE
    keys_from_title = TICKET_KEY_RE.findall(pr_title)
    keys_from_branch = TICKET_KEY_RE.findall(head_branch.upper())
    all_keys = list(dict.fromkeys(keys_from_title + keys_from_branch))

    if not all_keys:
        return []

    actor_id = await _get_system_actor_id(session, board)
    linked: list[str] = []

    for key in all_keys:
        ticket = await _find_ticket_by_key(session, key, board.id)
        if ticket is None:
            continue

        if action == "opened":
            event_type = "git_pr_linked"
        elif action == "closed" and merged:
            event_type = "git_pr_merged"
        elif action == "closed" and not merged:
            event_type = "git_pr_closed"
        else:
            event_type = "git_pr_updated"

        not_ready = action == "closed" and merged and ticket.state not in (
            "in_review", "in_test", "done"
        )
        merge_metadata: dict[str, Any] = {
            "pr_number": pr_number,
            "pr_title": pr_title,
            "pr_url": pr_url,
            "pr_state": pr_state,
            "merged": merged,
            "branch": head_branch,
        }
        if not_ready:
            merge_metadata["warning"] = (
                f"Ticket was in state '{ticket.state}' when PR was merged. "
                "Expected: in_review or in_test."
            )
            logger.warning(
                "PR merged for ticket %s in unexpected state %s", key, ticket.state
            )

        history = await write_history(
            session,
            ticket_id=ticket.id,
            actor_id=actor_id,
            event_type=event_type,
            metadata=merge_metadata,
        )
        await session.flush()
        await publish_ticket_event(history, ticket, None)  # type: ignore[arg-type]
        linked.append(key)

        if action == "closed" and merged:
            delete_branch: bool = payload.get("pull_request", {}).get(
                "head", {}
            ).get("repo", {}).get("delete_branch_on_merge", False)
            branch_deleted: bool = delete_branch or payload.get("delete_branch_on_merge", False)

            if branch_deleted or ticket.branch_name == head_branch:
                branch_history = await write_history(
                    session,
                    ticket_id=ticket.id,
                    actor_id=actor_id,
                    event_type="git_branch_deleted",
                    metadata={
                        "branch": head_branch,
                        "reason": "merged_and_deleted",
                    },
                )
                await session.flush()
                await publish_ticket_event(branch_history, ticket, None)  # type: ignore[arg-type]

                if ticket.branch_name == head_branch:
                    ticket.branch_name = None
                    await session.flush()
                    logger.info("Cleared branch_name for ticket %s after merge", key)

    await session.commit()
    return linked
