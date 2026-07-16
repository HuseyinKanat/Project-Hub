"""ORM to API response mappers."""

from app.db.models import (
    Actor,
    Attachment,
    Board,
    BoardMembership,
    Comment,
    Repository,
    SonarQubeMetric,
    Ticket,
    TicketHistory,
    Workflow,
)
from app.schemas import (
    ActorSummary,
    AttachmentResponse,
    BoardHealth,
    BoardResponse,
    CommentResponse,
    HistoryResponse,
    MembershipResponse,
    RepoHealth,
    TicketResponse,
    TicketSearchHit,
    WorkflowResponse,
)
from app.services.boards import mask_webhook_secret
from app.services.repositories import repository_summary
from app.services.sonarqube import _dashboard_url, derive_repo_project_key


def board_health(metric: SonarQubeMetric) -> BoardHealth:
    """Serialize a SonarQubeMetric ORM row to the compact health schema (PH-193).

    Numeric (Decimal) percentages are coerced to float for JSON. Token/secret is
    never read here — only the persisted metric columns are exposed.
    """
    return BoardHealth(
        quality_gate_status=metric.quality_gate_status,
        bugs=metric.bugs,
        vulnerabilities=metric.vulnerabilities,
        code_smells=metric.code_smells,
        coverage=float(metric.coverage) if metric.coverage is not None else None,
        duplicated_lines_density=(
            float(metric.duplicated_lines_density)
            if metric.duplicated_lines_density is not None
            else None
        ),
        ncloc=metric.ncloc,
        fetched_at=metric.fetched_at,
    )


def repo_health(metric: SonarQubeMetric) -> RepoHealth:
    """Serialize a SonarQubeMetric ORM row → the per-repo RepoHealth schema (PH-246).

    Reads the eager-loaded ``metric.repository`` for the repo identity (slug/name) — a
    legacy board-level row (``repo_id IS NULL`` / no repository) degrades to null repo
    fields. ``is_primary`` (PH-251) is read from the same eager-loaded ``repository.is_primary``
    (non-null Boolean, core.py:379) so the FE's per-repo `primary` badge is sourced from this
    un-gated surface instead of the member-gated /repositories call; a null-repo aggregate
    row → ``False``. ``dashboard_url`` is the HOST-facing deep link for THIS repo's project_key
    (secret-free; built from ``sonarqube_scan_url``, never the token / internal url).
    """
    repo = metric.repository
    return RepoHealth(
        repo_id=metric.repo_id,
        repo_slug=repo.slug if repo is not None else None,
        repo_name=repo.name if repo is not None else None,
        is_primary=repo.is_primary if repo is not None else False,
        project_key=metric.project_key,
        quality_gate_status=metric.quality_gate_status,
        bugs=metric.bugs,
        vulnerabilities=metric.vulnerabilities,
        code_smells=metric.code_smells,
        coverage=float(metric.coverage) if metric.coverage is not None else None,
        duplicated_lines_density=(
            float(metric.duplicated_lines_density)
            if metric.duplicated_lines_density is not None
            else None
        ),
        ncloc=metric.ncloc,
        fetched_at=metric.fetched_at,
        dashboard_url=_dashboard_url(metric.project_key),
    )


def unscanned_repo_health(board: Board, repo: Repository) -> RepoHealth:
    """RepoHealth for a LINKED repo that has no metric row yet (PH-252).

    A repo is linked to a multi-repo board but has never been scanned (Sonar off / no
    watcher / queued-but-undrained ``SonarScanJob``). It still gets an honest "No analysis
    yet" card: real identity (``repo_id``/``repo_slug``/``repo_name``/``is_primary``) +
    derived non-null ``project_key`` (``derive_repo_project_key`` is total — a slug is
    always present), but null ``quality_gate_status`` (the single canonical "never scanned"
    signal the FE keys on) + null 7 metrics + null ``fetched_at``.

    ``dashboard_url`` is ``None`` — NOT a derived deep link (PH-252 DECISION: null until
    first scan). The Sonar project is auto-created on first analysis; a derived URL before
    that 404s/empties. The FE already omits the "Open in SonarQube" anchor when
    ``dashboard_url`` is null, so this is the honest UX. Once scanned, ``repo_health(metric)``
    supplies the real URL. Secret-free — only ORM identity columns + a derived key are read.
    """
    return RepoHealth(
        repo_id=repo.id,
        repo_slug=repo.slug,
        repo_name=repo.name,
        is_primary=repo.is_primary,
        project_key=derive_repo_project_key(board, repo),
        quality_gate_status=None,
        bugs=None,
        vulnerabilities=None,
        code_smells=None,
        coverage=None,
        duplicated_lines_density=None,
        ncloc=None,
        fetched_at=None,
        dashboard_url=None,
    )


def repo_health_list(board: Board) -> list[RepoHealth]:
    """Per-repo SonarQube breakdown for ``BoardResponse.repo_health`` (PH-246/PH-252).

    Enumerates the board's LINKED repositories (``board.repositories``) LEFT-JOINed (in
    Python, over already-eager-loaded collections) to each repo's latest metric — one
    ``RepoHealth`` per linked repo, ordered primary-first then siblings by slug (mirrors
    ``build_scan_plans`` sonarqube.py:1376). A linked repo with NO metric row → an honest
    ``unscanned_repo_health`` card (PH-252: previously such repos were silently dropped,
    so a 3-repo board with 1 metric rendered only 1 card).

    Legacy repo-less boards (a ``repo_id IS NULL`` aggregate metric, no ``Repository`` rows)
    keep today's behavior via the ``not board.repositories`` fallback: ``[repo_health(m)
    ...]`` freshest-first (the aggregate row is preserved).

    Requires the board fetched with ``selectinload(Board.repositories)`` +
    ``selectinload(Board.sonarqube_metrics).selectinload(SonarQubeMetric.repository)``
    (get_board / list_boards do this — services/boards.py) so this is async-safe (no
    lazy-load). The ``UniqueConstraint(board_id, repo_id)`` (core.py:562) guarantees ≤1
    metric per repo, so ``metric_by_repo`` is unambiguous.
    """
    if not board.repositories:
        # Legacy repo-less aggregate fallback — preserve today's behavior exactly.
        metrics = sorted(
            board.sonarqube_metrics, key=lambda m: m.fetched_at, reverse=True
        )
        return [repo_health(m) for m in metrics]

    metric_by_repo = {
        m.repo_id: m for m in board.sonarqube_metrics if m.repo_id is not None
    }
    repos = sorted(board.repositories, key=lambda r: (not r.is_primary, r.slug))
    return [
        repo_health(metric_by_repo[r.id])
        if r.id in metric_by_repo
        else unscanned_repo_health(board, r)
        for r in repos
    ]


def actor_summary(actor: Actor) -> ActorSummary:
    return ActorSummary(
        id=actor.id,
        kind=actor.kind,
        display_name=actor.display_name,
        agent_id=actor.agent_id,
        agent_role_hint=actor.agent_role_hint,
    )


def workflow_response(workflow: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=workflow.id,
        name=workflow.name,
        states=workflow.states,
        transitions=workflow.transitions,
        is_default=workflow.is_default,
    )


def board_response(board: Board) -> BoardResponse:
    # PH-150/PH-221: include the PRIMARY repository summary.
    # Board is fetched with selectinload(Board.repositories) in get_board/list_boards,
    # so the primary_repository property iterates the loaded collection safely in
    # async context (no lazy-load triggered).
    repo_orm = board.primary_repository
    repo_summary = repository_summary(repo_orm) if repo_orm is not None else None

    # PH-193/PH-246: include SonarQube health. ``health`` stays the PRIMARY repo's
    # metric (back-compat — single-repo boards + existing FE render identically) via
    # the ``primary_sonarqube_metric`` accessor; ``repo_health`` carries the per-repo
    # breakdown. Board is fetched with selectinload(Board.sonarqube_metrics) +
    # Board.repositories in get_board/list_boards, so both accesses are async-safe.
    metric_orm = board.primary_sonarqube_metric
    health = board_health(metric_orm) if metric_orm is not None else None

    return BoardResponse(
        id=board.id,
        key=board.key,
        name=board.name,
        description=board.description,
        project_type=board.project_type,
        roles=mask_webhook_secret(board.roles),
        workflow=workflow_response(board.workflow),
        created_at=board.created_at,
        updated_at=board.updated_at,
        repository=repo_summary,
        health=health,
        repo_health=repo_health_list(board),
        # PH-228: expose the per-board HOST filesystem path (read surface only).
        repos_path=board.repos_path,
        # PH-235: expose the resolved SonarQube key so the board-header panel can
        # tell "no key" apart from "key set, not analyzed yet" (additive nullable).
        sonarqube_project_key=board.sonarqube_project_key,
    )


def ticket_search_hit(ticket: Ticket) -> TicketSearchHit:
    """Compact ticket projection for the cross-board search response (PH-275).

    Identity-only — mirrors the PH-274 GraphNode ticket fields so PH-278 renders
    search hits and graph nodes with one shape. Reads ``ticket.board.key``, which
    the search ticket query eager-loads (selectinload(Ticket.board)); reached via
    a lazy access this would raise MissingGreenlet (PH-272 rule). Description is
    matched server-side but deliberately NOT surfaced here.
    """
    return TicketSearchHit(
        id=ticket.id,
        key=ticket.key,
        title=ticket.title,
        board=ticket.board.key,
        board_id=ticket.board_id,
        state=ticket.state,
    )


def ticket_response(ticket: Ticket) -> TicketResponse:
    return TicketResponse.model_validate(
        {
            "id": ticket.id,
            "key": ticket.key,
            "board_id": ticket.board_id,
            "type": ticket.type,
            "title": ticket.title,
            "description": ticket.description,
            "state": ticket.state,
            "agent_phase": ticket.agent_phase,
            "assignee": actor_summary(ticket.assignee) if ticket.assignee else None,
            "reporter": actor_summary(ticket.reporter),
            "priority": ticket.priority,
            "epic_id": ticket.epic_id,
            "labels": ticket.labels,
            "acceptance_criteria": ticket.acceptance_criteria,
            "technical_depth": ticket.technical_depth,
            "impact_analysis": ticket.impact_analysis,
            "test_plan": ticket.test_plan,
            "steps_to_reproduce": ticket.steps_to_reproduce,
            "expected_behavior": ticket.expected_behavior,
            "actual_behavior": ticket.actual_behavior,
            "story_points": ticket.story_points,
            "due_date": ticket.due_date,
            "branch_name": ticket.branch_name,
            "files_touched_globs": ticket.files_touched_globs,
            "blocked_by": ticket.blocked_by,
            "claimed_by": ticket.claimed_by,
            "claimed_at": ticket.claimed_at,
            "created_at": ticket.created_at,
            "updated_at": ticket.updated_at,
            "_links": {
                "self": f"/api/tickets/{ticket.key}",
                "claim": f"/api/tickets/{ticket.key}/claim",
                "comments": f"/api/tickets/{ticket.key}/comments",
            },
        },
    )


def comment_response(comment: Comment) -> CommentResponse:
    return CommentResponse(
        id=comment.id,
        ticket_id=comment.ticket_id,
        author=actor_summary(comment.author),
        body=comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
    )


def attachment_response(attachment: Attachment) -> AttachmentResponse:
    """Serialize an Attachment ORM row. Requires ``attachment.author`` eager-loaded."""
    return AttachmentResponse(
        id=attachment.id,
        ticket_id=attachment.ticket_id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        checksum_sha256=attachment.checksum_sha256,
        kind=attachment.kind,
        source=attachment.source,
        run_id=attachment.run_id,
        phase=attachment.phase,
        author=actor_summary(attachment.author),
        created_at=attachment.created_at,
    )


def history_response(history: TicketHistory) -> HistoryResponse:
    return HistoryResponse(
        id=history.id,
        event_type=history.event_type,
        field=history.field,
        old_value=history.old_value,
        new_value=history.new_value,
        metadata=history.event_metadata,
        actor=actor_summary(history.actor),
        created_at=history.created_at,
    )


def membership_response(membership: BoardMembership) -> MembershipResponse:
    """Serialize a BoardMembership ORM instance to MembershipResponse.

    Requires ``membership.actor`` to be eagerly loaded.
    """
    return MembershipResponse(
        id=membership.id,
        actor=actor_summary(membership.actor),
        role=membership.role,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )
