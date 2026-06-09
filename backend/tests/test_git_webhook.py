"""Tests for app.git.webhook — PH-166 idempotent (commit, ticket) linkage.

Focus: the webhook-after-sync double-write of ``git_commit_linked`` history.

Background (G3/PH-152 discovered debt):
  ``sync.py`` gates ``git_commit_linked`` history writes on the
  ``git_commit_tickets`` unique constraint (first-observation wins). The
  GitHub webpath (``handle_push``) historically wrote history unconditionally,
  so a webhook arriving AFTER sync (same commit) produced a SECOND
  ``git_commit_linked`` row — an activity-feed duplicate.

  PH-166 routes ``handle_push`` through the same shared dedupe gate
  (``app/git/_linkage.ensure_commit_ticket_link``). Whichever path observes the
  (commit, ticket) pair first writes history; the other is a no-op.

Fixtures reuse the in-memory SQLite + on-disk git repo primitives from
test_git_sync.py (copied here to keep the file self-contained).
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import (
    Actor,
    Board,
    GitCommit,
    GitCommitFile,
    GitCommitTicket,
    Repository,
    Ticket,
    TicketHistory,
    Workflow,
)
from app.git.sync import sync_repo
from app.git.webhook import handle_push
from app.services.defaults import DEFAULT_STATES, DEFAULT_TRANSITIONS, DEFAULT_WEB_ROLES

# ---------------------------------------------------------------------------
# Git repo primitives (mirrors test_git_sync.py)
# ---------------------------------------------------------------------------

_GIT = ["git", "-c", "user.email=t@t.local", "-c", "user.name=Test"]


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)


def _write_and_commit(repo_path: Path, files: dict[str, str], msg: str) -> str:
    for name, content in files.items():
        target = repo_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", name], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        [*_GIT, "commit", "-m", msg], cwd=str(repo_path), check=True, capture_output=True, text=True
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# DB session fixture (function-scoped, in-memory)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def wh_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


def _make_captured_publish() -> tuple[AsyncMock, list[Any]]:
    captured: list[Any] = []

    async def _mock_publish(envelope: Any) -> None:
        captured.append(envelope)

    return AsyncMock(side_effect=_mock_publish), captured


# ---------------------------------------------------------------------------
# Scenario builder
# ---------------------------------------------------------------------------


class WebhookFixture:
    session: AsyncSession
    board: Board
    ticket: Ticket
    repo_row: Repository | None
    repo_path: Path
    commit_sha: str


async def _build(
    session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_repo: bool,
) -> WebhookFixture:
    root = tmp_path_factory.mktemp("wh_repos")
    repo_path = root / "wh-repo"
    repo_path.mkdir()
    _git_init(repo_path)
    monkeypatch.setattr(get_settings(), "repos_root", str(root))

    workflow = Workflow(
        name="Default", states=DEFAULT_STATES, transitions=DEFAULT_TRANSITIONS, is_default=True
    )
    session.add(workflow)
    await session.flush()

    admin = Actor(kind="human", display_name="Admin", token_hash="x", is_active=True)
    session.add(admin)
    await session.flush()

    board = Board(
        key="PH",
        name="ProjectHub",
        description="",
        project_type="web_app",
        workflow_id=workflow.id,
        roles=DEFAULT_WEB_ROLES,
        created_by=admin.id,
    )
    session.add(board)
    await session.flush()

    ticket = Ticket(
        key="PH-1",
        board_id=board.id,
        type="task",
        title="Ticket one",
        description="",
        state="to_do",
        reporter_id=admin.id,
        priority="medium",
        labels=[],
    )
    session.add(ticket)
    await session.flush()

    repo_row: Repository | None = None
    if with_repo:
        repo_row = Repository(
            id=uuid.uuid4(),
            board_id=board.id,
            slug="repo",
            name="repo",
            is_primary=True,
            local_path=str(repo_path),
            provider="local",
            default_branch="main",
        )
        session.add(repo_row)
    await session.commit()

    commit_sha = _write_and_commit(repo_path, {"a.txt": "init\n"}, "feat(PH-1): first")

    board = (
        await session.execute(
            select(Board).where(Board.id == board.id).options(selectinload(Board.repositories))
        )
    ).scalar_one()

    fx = WebhookFixture()
    fx.session = session
    fx.board = board
    fx.ticket = ticket
    fx.repo_row = repo_row
    fx.repo_path = repo_path
    fx.commit_sha = commit_sha
    return fx


def _push_payload(sha: str) -> dict[str, Any]:
    """Minimal GitHub push payload referencing one PH-1 commit."""
    return {
        "ref": "refs/heads/main",
        "commits": [
            {
                "id": sha,
                "message": "feat(PH-1): first",
                "author": {"name": "Dev", "email": "dev@example.com"},
                "url": f"https://github.com/x/y/commit/{sha}",
                "timestamp": "2026-06-05T00:00:00+00:00",
            }
        ],
    }


async def _count_linked_history(session: AsyncSession, ticket_id: Any) -> int:
    rows = (
        await session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket_id,
                TicketHistory.event_type == "git_commit_linked",
            )
        )
    ).scalars().all()
    return len(rows)


async def _count_linked_history_for_sha(
    session: AsyncSession, ticket_id: Any, sha: str
) -> int:
    """Count git_commit_linked history rows for a SPECIFIC (ticket, commit sha).

    The double-write invariant is per (commit, ticket) pair — two distinct
    commits referencing the same ticket legitimately yield two rows.
    """
    rows = (
        await session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket_id,
                TicketHistory.event_type == "git_commit_linked",
            )
        )
    ).scalars().all()
    return sum(1 for r in rows if (r.event_metadata or {}).get("sha") == sha)


# ---------------------------------------------------------------------------
# PH-166 — core fix: webhook AFTER sync must NOT double-write history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_after_sync_no_double_write(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync links the commit first; a later webhook for the SAME commit is a no-op."""
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=True)

    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result = await sync_repo(fx.session, fx.board)
    assert result.linked_tickets == 1
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1

    # Webhook arrives AFTER sync for the same commit → must skip history.
    with patch("app.git.webhook.publish_ticket_event", new=AsyncMock()):
        linked = await handle_push(fx.session, fx.board, _push_payload(fx.commit_sha))

    # No second git_commit_linked row.
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1, (
        "webhook after sync must not write a duplicate git_commit_linked row"
    )
    # handle_push reports nothing newly linked.
    assert linked == []
    # Exactly one junction row.
    junctions = (
        await fx.session.execute(
            select(GitCommitTicket).where(GitCommitTicket.ticket_id == fx.ticket.id)
        )
    ).scalars().all()
    assert len(junctions) == 1


# ---------------------------------------------------------------------------
# PH-166 — symmetric: webhook FIRST then sync also stays single
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_after_webhook_no_double_write(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """webhook links first; a later sync for the same commit must not re-write."""
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=True)

    with patch("app.git.webhook.publish_ticket_event", new=AsyncMock()):
        linked = await handle_push(fx.session, fx.board, _push_payload(fx.commit_sha))
    assert linked == ["PH-1"]
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1

    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        result = await sync_repo(fx.session, fx.board)

    # Sync sees the junction already exists → no new link, no new history.
    assert result.linked_tickets == 0
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1, (
        "sync after webhook must not write a duplicate git_commit_linked row"
    )


# ---------------------------------------------------------------------------
# PH-166 — webhook redelivery (idempotent on its own)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_redelivery_is_idempotent(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two identical webhook deliveries → only one git_commit_linked row."""
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=True)

    with patch("app.git.webhook.publish_ticket_event", new=AsyncMock()):
        first = await handle_push(fx.session, fx.board, _push_payload(fx.commit_sha))
        second = await handle_push(fx.session, fx.board, _push_payload(fx.commit_sha))

    assert first == ["PH-1"]
    assert second == [], "redelivery must not re-link"
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1


# ---------------------------------------------------------------------------
# Regression — fresh commit via webhook still writes history (with repo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_fresh_commit_writes_history_with_repo(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-ever observation of a commit (repo configured, never synced)
    → one git_commit_linked row + a git_commits + git_commit_tickets row."""
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=True)

    with patch("app.git.webhook.publish_ticket_event", new=AsyncMock()):
        linked = await handle_push(fx.session, fx.board, _push_payload(fx.commit_sha))

    assert linked == ["PH-1"]
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1

    # The webhook created the git_commits row (it was the first observer).
    commits = (
        await fx.session.execute(
            select(GitCommit).where(GitCommit.sha == fx.commit_sha)
        )
    ).scalars().all()
    assert len(commits) == 1
    junctions = (
        await fx.session.execute(
            select(GitCommitTicket).where(GitCommitTicket.ticket_id == fx.ticket.id)
        )
    ).scalars().all()
    assert len(junctions) == 1

    # PH-247: the git_commit_linked event metadata carries source-repo identity
    # at the webhook write site (repo_row threaded from handle_push).
    assert fx.repo_row is not None
    history = (
        await fx.session.execute(
            select(TicketHistory).where(
                TicketHistory.ticket_id == fx.ticket.id,
                TicketHistory.event_type == "git_commit_linked",
            )
        )
    ).scalars().all()
    assert len(history) == 1
    meta = history[0].event_metadata
    assert meta["repo_id"] == str(fx.repo_row.id)
    assert meta["repo_slug"] == "repo"
    assert meta["repo_name"] == "repo"


# ---------------------------------------------------------------------------
# Regression — no repository configured → legacy direct write preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_no_repo_legacy_behavior(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Board without a Repository row: webhook still writes git_commit_linked
    (legacy path — cannot key the junction without a git_commits row)."""
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=False)

    with patch("app.git.webhook.publish_ticket_event", new=AsyncMock()):
        linked = await handle_push(fx.session, fx.board, _push_payload(fx.commit_sha))

    assert linked == ["PH-1"]
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1
    # No git_commits / junction rows are fabricated when there's no repo.
    commits = (await fx.session.execute(select(GitCommit))).scalars().all()
    assert commits == []


# ---------------------------------------------------------------------------
# PH-166 BLOCKER regression — webhook-first stub MUST be enriched by later sync
# ---------------------------------------------------------------------------


async def _count_files_for_sha(session: AsyncSession, sha: str) -> int:
    """Count git_commit_files rows for the git_commits row with this sha."""
    commit = (
        await session.execute(select(GitCommit).where(GitCommit.sha == sha))
    ).scalar_one_or_none()
    if commit is None:
        return 0
    rows = (
        await session.execute(
            select(GitCommitFile).where(GitCommitFile.commit_id == commit.id)
        )
    ).scalars().all()
    return len(rows)


def _push_payload_for(sha: str, message: str) -> dict[str, Any]:
    """Webhook push payload for an arbitrary (sha, message) on main."""
    return {
        "ref": "refs/heads/main",
        "commits": [
            {
                "id": sha,
                "message": message,
                # Webhook author differs from the git committer ('Test') so we
                # can prove sync overwrites the stub's committer on enrichment.
                "author": {"name": "Dev", "email": "dev@example.com"},
                "url": f"https://github.com/x/y/commit/{sha}",
                "timestamp": "2026-06-05T00:00:00+00:00",
            }
        ],
    }


@pytest.mark.asyncio
async def test_webhook_first_then_sync_enriches_stub(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION (PH-166 blocker): when the webhook is the FIRST observer it
    mints a minimal git_commits stub (parents=[], no files, committer=author).
    A later sync MUST enrich that stub in place — populate git_commit_files,
    real parents, and the real committer — instead of skipping past it.

    Pre-fix bug: sync saw the row existed and `continue`d, leaving the commit
    permanently at 0 files / parents=[], corrupting commit-detail + branch-graph
    ahead/behind BFS for exactly the webhook+sync boards this ticket targets.
    """
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=True)

    # Make a SECOND commit so the enriched commit has a real (non-empty) parent.
    second_sha = _write_and_commit(
        fx.repo_path, {"b.txt": "second\n"}, "fix(PH-1): second"
    )

    # 1) Webhook is the first observer of the second commit → mints a stub.
    with patch("app.git.webhook.publish_ticket_event", new=AsyncMock()):
        linked = await handle_push(
            fx.session, fx.board, _push_payload_for(second_sha, "fix(PH-1): second")
        )
    assert linked == ["PH-1"]
    assert await _count_linked_history_for_sha(
        fx.session, fx.ticket.id, second_sha
    ) == 1

    # Prove it really is an impoverished stub at this point.
    stub = (
        await fx.session.execute(
            select(GitCommit).where(GitCommit.sha == second_sha)
        )
    ).scalar_one()
    stub_id = stub.id
    assert stub.parents == [], "webhook stub should have empty parents"
    assert await _count_files_for_sha(fx.session, second_sha) == 0, (
        "webhook stub should have no file rows yet"
    )
    assert stub.committer_name == "Dev", "stub committer mirrors webhook author"

    # 2) Sync runs later and MUST enrich the stub (not skip it).
    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        await sync_repo(fx.session, fx.board)

    enriched = (
        await fx.session.execute(
            select(GitCommit).where(GitCommit.sha == second_sha)
        )
    ).scalar_one()
    await fx.session.refresh(enriched)

    # Same physical row (id preserved → junction FK stays valid).
    assert enriched.id == stub_id, "enrichment must mutate the existing row in place"
    # parents now populated (second commit's parent is the first commit).
    assert enriched.parents == [fx.commit_sha], (
        "sync must enrich the stub's parents with the real git history"
    )
    # File rows now present (the core data-integrity fix).
    assert await _count_files_for_sha(fx.session, second_sha) >= 1, (
        "sync must populate git_commit_files for a webhook-first stub"
    )
    # Real committer from git ('Test'), overwriting the webhook author ('Dev').
    assert enriched.committer_name == "Test", (
        "sync must overwrite the stub committer with the real git committer"
    )
    # And the double-write invariant still holds for THIS commit: enriching the
    # stub must NOT write a second git_commit_linked row for (second_sha, PH-1).
    assert await _count_linked_history_for_sha(
        fx.session, fx.ticket.id, second_sha
    ) == 1, "stub enrichment must not write a second git_commit_linked row"
    # Sanity: the first commit (which the webhook never saw) is now linked by
    # sync in the same run — so the ticket has two rows total, one per commit.
    assert await _count_linked_history_for_sha(
        fx.session, fx.ticket.id, fx.commit_sha
    ) == 1, "sync still links commits the webhook never observed"
    assert await _count_linked_history(fx.session, fx.ticket.id) == 2
    # Exactly one junction row for the (commit, ticket) pair.
    junctions = (
        await fx.session.execute(
            select(GitCommitTicket).where(GitCommitTicket.commit_id == stub_id)
        )
    ).scalars().all()
    assert len(junctions) == 1


@pytest.mark.asyncio
async def test_sync_only_commit_is_not_re_enriched(
    wh_session: AsyncSession,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A commit cached by sync (already has file rows) is NOT re-touched on a
    second sync — enrichment only repairs file-less webhook stubs, so a normal
    re-sync remains a cheap no-op (no duplicate file rows, no history churn)."""
    fx = await _build(wh_session, tmp_path_factory, monkeypatch, with_repo=True)

    mock_pub, _ = _make_captured_publish()
    with patch("app.events.bus.EventBus.publish", mock_pub):
        await sync_repo(fx.session, fx.board)

    files_after_first = await _count_files_for_sha(fx.session, fx.commit_sha)
    assert files_after_first >= 1

    # Force a full re-walk by clearing last_synced_sha, then sync again.
    repo = (
        await fx.session.execute(select(Repository).where(Repository.board_id == fx.board.id))
    ).scalar_one()
    repo.last_synced_sha = None
    await fx.session.commit()

    with patch("app.events.bus.EventBus.publish", mock_pub):
        await sync_repo(fx.session, fx.board)

    # No duplicated file rows; still exactly one history row.
    assert await _count_files_for_sha(fx.session, fx.commit_sha) == files_after_first, (
        "re-sync must not duplicate file rows for an already-enriched commit"
    )
    assert await _count_linked_history(fx.session, fx.ticket.id) == 1
