"""Core ProjectHub ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

# Dialect-aware types: native Postgres in production, generic JSON/Uuid for tests.
UUID_TYPE = Uuid(as_uuid=True)
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
STRING_ARRAY_TYPE = JSON().with_variant(ARRAY(String), "postgresql")

# Foreign-key target references — table.column strings reused across many
# mapped_column / ForeignKey definitions. Centralised to avoid literal
# duplication and to make the table-name coupling explicit.
_FK_ACTORS_ID = "actors.id"
_FK_BOARDS_ID = "boards.id"
_FK_TICKETS_ID = "tickets.id"
_FK_REPOSITORIES_ID = "repositories.id"
_FK_CONCEPT_TAGS_ID = "concept_tags.id"

# SQLAlchemy relationship cascade directive reused on every owning side.
_CASCADE_ALL_DELETE_ORPHAN = "all, delete-orphan"


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID_TYPE, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Actor(Base, TimestampMixin):
    __tablename__ = "actors"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    agent_role_hint: Mapped[str | None] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list[BoardMembership]] = relationship(back_populates="actor")


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    states: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False)
    transitions: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    boards: Mapped[list[Board]] = relationship(back_populates="workflow")
    board_workflows: Mapped[list[BoardWorkflow]] = relationship(back_populates="workflow")


class BoardWorkflow(Base, TimestampMixin):
    __tablename__ = "board_workflows"
    __table_args__ = (
        # Partial unique index: at most one active workflow per board.
        # Using a partial index instead of a full unique constraint on
        # (board_id, is_active) allows a board to have multiple inactive
        # workflows in the junction table simultaneously.
        Index(
            "uq_board_active_workflow",
            "board_id",
            unique=True,
            postgresql_where="is_active = true",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    board_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_BOARDS_ID), nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    board: Mapped[Board] = relationship(back_populates="board_workflows")
    workflow: Mapped[Workflow] = relationship(back_populates="board_workflows")


class Board(Base, TimestampMixin):
    __tablename__ = "boards"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(5), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    project_type: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    roles: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(_FK_ACTORS_ID))
    next_ticket_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # PH-193: links a PH board to its SonarQube projectKey. Nullable → additive,
    # no backfill; the SonarQube poller skips boards with no resolvable key.
    sonarqube_project_key: Mapped[str | None] = mapped_column(String(400))
    # PH-228: per-board filesystem root, stored as the HOST path (the user-meaningful
    # path matching Finder/CLI, e.g. /Users/huseyinkanat/Documents/kims) — NOT the
    # container path. Translated to the in-container /repos path on demand by
    # services.repo_paths.to_container_path (the docker mount maps $HOME → /repos).
    # Nullable → additive; PH-228 backfills the 6 known boards, later boards stay
    # null until set via PH-230's settings UI. Consumed by detect/sonar in PH-229.
    repos_path: Mapped[str | None] = mapped_column(String(500))

    workflow: Mapped[Workflow] = relationship(back_populates="boards")
    board_workflows: Mapped[list[BoardWorkflow]] = relationship(back_populates="board")
    memberships: Mapped[list[BoardMembership]] = relationship(back_populates="board")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="board")
    # PH-221: a board owns 0..N repositories; exactly one is the primary/default.
    # (Was uselist=False 1:1 in PH-150 — see `primary_repository` for back-compat.)
    repositories: Mapped[list[Repository]] = relationship(
        back_populates="board",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )
    # PH-193: latest SonarQube board-health snapshot (1 board : 0..1 metric row,
    # upsert-latest). Mirrors the `repository` 1:1 relationship.
    # PH-246: KEPT as the PRIMARY-repo back-compat accessor (see
    # ``primary_sonarqube_metric``) — the relationship below is now 1:N keyed on
    # ``(board_id, repo_id)``; this singular property selects the primary repo's row.
    sonarqube_metrics: Mapped[list[SonarQubeMetric]] = relationship(
        back_populates="board",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )
    # PH-239: lifecycle rows for per-board scan requests (queued→running→done/failed),
    # consumed by the host-side watcher. Append history (NOT a 1:1 cache).
    sonar_scan_jobs: Mapped[list[SonarScanJob]] = relationship(
        back_populates="board",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )

    @property
    def primary_repository(self) -> Repository | None:
        """The board's primary/default repository, or None if no repo is configured.

        PH-221 back-compat accessor — NOT a mapped column. Lets the singular
        consumers (``serializers.board_response``, ``BoardResponse.repository``)
        keep working after the 1:1 → 1:N relationship rename. Iterates the
        already-loaded ``repositories`` collection (callers must
        ``selectinload(Board.repositories)`` first — no lazy-load in async).
        """
        return next((r for r in self.repositories if r.is_primary), None)

    @property
    def primary_sonarqube_metric(self) -> SonarQubeMetric | None:
        """The board's PRIMARY-repo SonarQube metric, or None (back-compat).

        PH-246: ``sonarqube_metrics`` became 1:N (one row per ``(board_id, repo_id)``).
        ``BoardResponse.health`` stays the SINGLE primary-repo metric for back-compat
        (single-repo boards + existing FE render identically), so this accessor picks
        the row whose ``repo_id`` is the primary repo's. Selection order:
          1. the metric whose ``repo_id`` == the primary repo's id;
          2. else a legacy ``repo_id is None`` row (pre-migration board-level metric);
          3. else the first metric (degraded fallback — never raises).
        Iterates the already-loaded collections (callers must
        ``selectinload(Board.sonarqube_metrics, Board.repositories)`` — no lazy-load).
        """
        metrics = list(self.sonarqube_metrics)
        if not metrics:
            return None
        primary = self.primary_repository
        if primary is not None:
            for m in metrics:
                if m.repo_id == primary.id:
                    return m
        for m in metrics:
            if m.repo_id is None:
                return m
        return metrics[0]


class BoardMembership(Base, TimestampMixin):
    __tablename__ = "board_memberships"
    __table_args__ = (UniqueConstraint("board_id", "actor_id", name="uq_board_actor"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    board_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_BOARDS_ID), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_ACTORS_ID), nullable=False)
    role: Mapped[str] = mapped_column(String(80), nullable=False)

    board: Mapped[Board] = relationship(back_populates="memberships")
    actor: Mapped[Actor] = relationship(back_populates="memberships")


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("board_id", "key", name="uq_ticket_board_key"),
        Index("ix_tickets_board_state", "board_id", "state"),
        Index("ix_tickets_board_key", "board_id", "key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(24), nullable=False)
    board_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_BOARDS_ID), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_phase: Mapped[dict[str, Any] | None] = mapped_column(JSON_TYPE)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(_FK_ACTORS_ID))
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_ACTORS_ID), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    epic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(_FK_TICKETS_ID))
    labels: Mapped[list[str]] = mapped_column(STRING_ARRAY_TYPE, default=list, nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(200))
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    technical_depth: Mapped[str | None] = mapped_column(Text)
    impact_analysis: Mapped[str | None] = mapped_column(Text)
    test_plan: Mapped[str | None] = mapped_column(Text)
    steps_to_reproduce: Mapped[str | None] = mapped_column(Text)
    expected_behavior: Mapped[str | None] = mapped_column(Text)
    actual_behavior: Mapped[str | None] = mapped_column(Text)
    story_points: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(_FK_ACTORS_ID))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    board: Mapped[Board] = relationship(back_populates="tickets")
    reporter: Mapped[Actor] = relationship(foreign_keys=[reporter_id])
    assignee: Mapped[Actor | None] = relationship(foreign_keys=[assignee_id])
    epic: Mapped[Ticket | None] = relationship(remote_side=[id])
    comments: Mapped[list[Comment]] = relationship(back_populates="ticket")
    history: Mapped[list[TicketHistory]] = relationship(back_populates="ticket")
    # PH-272: concept-tag attachments. ASYNC-SAFE WARNING — this is a lazy
    # collection; any async code path that reads ``ticket.concept_tag_links``
    # (or ``.concept_tag`` through it) MUST eager-load via
    # ``selectinload(Ticket.concept_tag_links).selectinload(
    # TicketConceptTag.concept_tag)`` or async lazy-load raises MissingGreenlet.
    concept_tag_links: Mapped[list[TicketConceptTag]] = relationship(
        back_populates="ticket", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_TICKETS_ID), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_ACTORS_ID), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[Ticket] = relationship(back_populates="comments")
    author: Mapped[Actor] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_actor_read", "actor_id", "is_read"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_ACTORS_ID), nullable=False)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_TICKETS_ID), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    actor: Mapped[Actor] = relationship(foreign_keys=[actor_id])
    ticket: Mapped[Ticket] = relationship()


class TicketHistory(Base):
    __tablename__ = "ticket_history"
    __table_args__ = (Index("ix_ticket_history_ticket_created", "ticket_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_TICKETS_ID), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_ACTORS_ID), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    field: Mapped[str | None] = mapped_column(String(80))
    old_value: Mapped[dict[str, Any] | list[Any] | str | int | bool | None] = mapped_column(
        JSON_TYPE
    )
    new_value: Mapped[dict[str, Any] | list[Any] | str | int | bool | None] = mapped_column(
        JSON_TYPE
    )
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    ticket: Mapped[Ticket] = relationship(back_populates="history")
    actor: Mapped[Actor] = relationship()


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(_FK_ACTORS_ID), nullable=False)
    preference_key: Mapped[str] = mapped_column(String(80), nullable=False)
    preference_value: Mapped[str] = mapped_column(Text, nullable=False)

    # Unique constraint to prevent duplicate preferences for the same actor
    __table_args__ = (UniqueConstraint("actor_id", "preference_key", name="uk_actor_preference"),)

    actor: Mapped[Actor] = relationship()


class Repository(Base, TimestampMixin):
    """Git repository configuration attached to a Board (1 board : 0..N repos).

    PH-221: a board may own multiple repositories; exactly one is the primary
    (``is_primary=true``). Identity within a board is the stable ``slug``.
    G1 stores connection config only — no physical git operations are performed
    here.  Reader/sync/diff logic is G2-G6.
    """

    __tablename__ = "repositories"
    __table_args__ = (
        # PH-221: slug is the stable per-board identity used in URLs/selectors.
        UniqueConstraint("board_id", "slug", name="uq_repository_board_slug"),
        # PH-221: at most one primary repo per board. Partial unique index —
        # `postgresql_where` for prod, `sqlite_where` so the in-memory test DB
        # gets a TRUE partial index too (without the SQLite predicate the index
        # degrades to a full UNIQUE(board_id), which would forbid 1:N entirely).
        # The service invariant in set_primary/add_repository is the additional
        # cross-dialect backstop.
        Index(
            "uq_repository_one_primary",
            "board_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    board_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_BOARDS_ID, ondelete="CASCADE"),
        nullable=False,
    )
    # PH-221: stable per-board identity (slugified) — used in `repo` selectors
    # and `/repositories/{slug}` routes. Unique within a board.
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    # PH-221: human-readable label shown in the repo switcher UI.
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # PH-221: exactly one primary per board (partial unique index + service
    # invariant). The primary is the default target when `repo` is omitted.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # provider: 'github' | 'gitlab' | 'local'.  Stored as string; CHECK constraint
    # in migration enforces valid values (avoids Enum migration complexity).
    provider: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    remote_url: Mapped[str | None] = mapped_column(String(500))
    default_branch: Mapped[str] = mapped_column(String(120), default="main", nullable=False)
    # local_path: container-internal mount path; must start with /repos/
    # (validated at service layer, re-checked by G2 reader with Path.resolve()).
    local_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Populated by G3 sync runner — null until first sync.
    last_synced_sha: Mapped[str | None] = mapped_column(String(40))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # PH-246: per-repo SonarQube projectKey — the SOURCE OF TRUTH for scanning a
    # multi-repo board. Nullable/additive: a freshly-added repo has no key until
    # setup/scan derives one (``derive_repo_project_key``). The migration backfills
    # the PRIMARY repo's key from the legacy ``Board.sonarqube_project_key`` so
    # KIM/PH/GXA-primary keep their EXACT existing Sonar project (no rename, no
    # orphaned dashboard); non-primary repos stay NULL and derive lazily at scan time
    # (``<primaryKey>-<slug>``). ``Board.sonarqube_project_key`` is KEPT as the legacy
    # fallback + PH self-scan identity (NOT dropped — see that column's note).
    sonarqube_project_key: Mapped[str | None] = mapped_column(String(400))

    board: Mapped[Board] = relationship(back_populates="repositories")
    git_commits: Mapped[list[GitCommit]] = relationship(
        back_populates="repository",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )
    git_branches: Mapped[list[GitBranch]] = relationship(
        back_populates="repository",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )


class GitCommit(Base, TimestampMixin):
    """Cached git commit metadata — one row per (repo, sha).

    G3 sync populates this table from local git reads; G4 read endpoints
    serve directly from here without spawning git subprocesses per API call.
    """

    __tablename__ = "git_commits"
    __table_args__ = (
        UniqueConstraint("repo_id", "sha", name="uq_git_commit_repo_sha"),
        Index("ix_git_commits_repo_committed_at", "repo_id", "committed_at"),
        Index("ix_git_commits_repo_sha", "repo_id", "sha"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_REPOSITORIES_ID, ondelete="CASCADE"),
        nullable=False,
    )
    sha: Mapped[str] = mapped_column(String(40), nullable=False)
    short_sha: Mapped[str] = mapped_column(String(12), nullable=False)
    parents: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    author_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committer_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    committer_email: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_conventional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commit_type: Mapped[str | None] = mapped_column(String(20))
    ticket_keys: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)

    repository: Mapped[Repository] = relationship(back_populates="git_commits")
    files: Mapped[list[GitCommitFile]] = relationship(
        back_populates="commit",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )
    ticket_links: Mapped[list[GitCommitTicket]] = relationship(
        back_populates="commit",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
    )


class GitBranch(Base, TimestampMixin):
    """Cached git branch snapshot — one row per (repo, branch name).

    Refreshed on every sync_repo() call.  Rows for branches that no longer
    exist in the repo are deleted (deleted_branches in SyncResult).
    """

    __tablename__ = "git_branches"
    __table_args__ = (
        UniqueConstraint("repo_id", "name", name="uq_git_branch_repo_name"),
        Index("ix_git_branches_repo_id", "repo_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_REPOSITORIES_ID, ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticket_key: Mapped[str | None] = mapped_column(String(24))

    repository: Mapped[Repository] = relationship(back_populates="git_branches")


class GitCommitFile(Base):
    """Per-file change entry for a cached git commit.

    Immutable per commit — no TimestampMixin needed (parent git_commits.created_at
    is sufficient provenance).
    """

    __tablename__ = "git_commit_files"
    __table_args__ = (Index("ix_git_commit_files_commit_id", "commit_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    commit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("git_commits.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    old_path: Mapped[str | None] = mapped_column(String(1000))
    # change_type: 'A' | 'M' | 'D' | 'R' | 'C'
    change_type: Mapped[str] = mapped_column(String(1), nullable=False)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_binary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    commit: Mapped[GitCommit] = relationship(back_populates="files")


class GitCommitTicket(Base):
    """Junction table linking git commits to tickets.

    The unique constraint on (commit_id, ticket_id) acts as the dedupe gate for
    both the GitHub webhook path (write_history without consulting this table)
    and the local sync path (INSERT … ON CONFLICT DO NOTHING; skip history write
    when no rows inserted).  First-observation wins — timestamps remain anchored
    to the first time the link was established.
    """

    __tablename__ = "git_commit_tickets"
    __table_args__ = (
        UniqueConstraint("commit_id", "ticket_id", name="uq_git_commit_ticket"),
        Index("ix_git_commit_tickets_ticket_id", "ticket_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    commit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("git_commits.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_TICKETS_ID, ondelete="CASCADE"),
        nullable=False,
    )

    commit: Mapped[GitCommit] = relationship(back_populates="ticket_links")
    ticket: Mapped[Ticket] = relationship()


class ConceptTag(Base, TimestampMixin):
    """Global concept-tag (PH-272).

    A cross-board taxonomy entity (mirrors Actor/Workflow: NO ``board_id``).
    Distinct from ``Ticket.labels`` (free-text per-ticket ARRAY) — concept tags
    are first-class, globally-shared, and graph-linkable (``ConceptTagLink``).
    """

    __tablename__ = "concept_tags"
    __table_args__ = (UniqueConstraint("slug", name="uq_concept_tag_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(20))  # hex e.g. #7dd3fc

    ticket_links: Mapped[list[TicketConceptTag]] = relationship(
        back_populates="concept_tag", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )


class TicketConceptTag(Base):
    """Junction linking a ticket to a global concept tag (PH-272).

    Mirrors ``GitCommitTicket``: link rows with no timestamps. The unique
    constraint on (ticket_id, concept_tag_id) dedupes double-attach; both FKs
    CASCADE so deleting the ticket (or the tag) prunes the link, never the
    surviving parent.
    """

    __tablename__ = "ticket_concept_tags"
    __table_args__ = (
        UniqueConstraint(
            "ticket_id", "concept_tag_id", name="uq_ticket_concept_tag"
        ),
        Index("ix_ticket_concept_tags_ticket_id", "ticket_id"),
        Index("ix_ticket_concept_tags_concept_tag_id", "concept_tag_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_TICKETS_ID, ondelete="CASCADE"),
        nullable=False,
    )
    concept_tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_CONCEPT_TAGS_ID, ondelete="CASCADE"),
        nullable=False,
    )

    ticket: Mapped[Ticket] = relationship(back_populates="concept_tag_links")
    concept_tag: Mapped[ConceptTag] = relationship(back_populates="ticket_links")


class ConceptTagLink(Base):
    """Directed tag↔tag edge (PH-272).

    Self-referential M2M: ``source_tag_id`` → ``target_tag_id`` with a nullable
    ``relation`` label (``relates`` | ``parent`` | ``depends``). DIRECTED is the
    only shape carrying both asymmetric (parent/depends) and symmetric (relates)
    semantics; the graph layer (PH-275) collapses to undirected when ``relation``
    is symmetric. ``uq_concept_tag_link_source_target`` dedupes one direction;
    the reverse (target, source) is a distinct row by design. Self-loop/cycle
    guards are app-layer (PH-273), NOT enforced here.

    Both FKs target ``concept_tags.id`` → each relationship MUST set explicit
    ``foreign_keys=[...]`` or mapper config raises AmbiguousForeignKeysError.
    """

    __tablename__ = "concept_tag_links"
    __table_args__ = (
        UniqueConstraint(
            "source_tag_id",
            "target_tag_id",
            name="uq_concept_tag_link_source_target",
        ),
        Index("ix_concept_tag_links_source_tag_id", "source_tag_id"),
        Index("ix_concept_tag_links_target_tag_id", "target_tag_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_CONCEPT_TAGS_ID, ondelete="CASCADE"),
        nullable=False,
    )
    target_tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_CONCEPT_TAGS_ID, ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str | None] = mapped_column(String(40))

    source_tag: Mapped[ConceptTag] = relationship(foreign_keys=[source_tag_id])
    target_tag: Mapped[ConceptTag] = relationship(foreign_keys=[target_tag_id])


class SonarQubeMetric(Base, TimestampMixin):
    """Latest SonarQube main-branch quality snapshot, per (Board, Repository).

    PH-193: upsert-latest cache (NOT append-history). The denormalized hot columns
    (bugs/coverage/…) are what the API reads; ``raw_measures`` keeps the verbatim
    measure map for forward-compat (new metrics need no migration).

    PH-246 — per-REPO: was 1 board : 0..1 row (``UniqueConstraint(board_id)``); now
    1 board : N rows, one per scanned repository, keyed by
    ``UniqueConstraint(board_id, repo_id) = uq_sonarqube_metric_board_repo``. ``repo_id``
    is NULLABLE so the legacy board-level row migrates non-destructively (the migration
    backfills ``repo_id`` = the board's primary repo id for every existing row → the
    relaxed unique constraint is satisfied + KIM/PH health is unchanged). The board API
    ``health`` stays the PRIMARY repo's metric (``Board.primary_sonarqube_metric``);
    ``repo_health`` carries the per-repo breakdown.
    """

    __tablename__ = "sonarqube_metrics"
    __table_args__ = (
        # PH-246: relaxed from UniqueConstraint(board_id) → (board_id, repo_id) so a
        # board may carry one upsert-latest row per repo. A NULL repo_id (legacy
        # board-level row) is permitted; the migration backfills it to the primary repo.
        UniqueConstraint(
            "board_id", "repo_id", name="uq_sonarqube_metric_board_repo"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    board_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_BOARDS_ID, ondelete="CASCADE"),
        nullable=False,
    )
    # PH-246: which repository this snapshot belongs to. Nullable so the legacy
    # board-level row (pre-migration) still validates; the migration backfills it to the
    # board's primary repo, and every NEW row sets it. CASCADE so a removed repo's metric
    # is cleaned up.
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(_FK_REPOSITORIES_ID, ondelete="CASCADE"),
    )
    # The SonarQube projectKey actually queried (audit even if Board.sonarqube_project_key
    # later changes / is resolved from the project_key_map).
    project_key: Mapped[str] = mapped_column(String(400), nullable=False)
    # Quality gate: 'OK' | 'ERROR' | 'WARN' | 'NONE' (from project_status.status).
    quality_gate_status: Mapped[str | None] = mapped_column(String(20))
    bugs: Mapped[int | None] = mapped_column(Integer)
    vulnerabilities: Mapped[int | None] = mapped_column(Integer)
    code_smells: Mapped[int | None] = mapped_column(Integer)
    # Percentages 0..100 (SonarQube returns strings → parsed to Decimal).
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    duplicated_lines_density: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    ncloc: Mapped[int | None] = mapped_column(Integer)  # lines of code
    # Verbatim {metricKey: rawValue} map for forward-compat.
    raw_measures: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)
    # Poll wall-clock — the freshness signal the frontend shows.
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    board: Mapped[Board] = relationship(back_populates="sonarqube_metrics")
    # PH-246: the owning repository (None for a legacy board-level row).
    repository: Mapped[Repository | None] = relationship()


class SonarScanJob(Base, TimestampMixin):
    """PH-239 — one queued/in-flight/finished per-board SonarQube scan request.

    Unlike ``SonarQubeMetric`` (a 1:1 latest-snapshot cache), this is an append
    LIFECYCLE table: each "Scan now" click (or auto-trigger) persists a row that the
    host-side watcher (``scripts/sonar-scan-watcher.sh``) claims, runs, and reports
    back on. A board can have queue depth >1 and a history of past runs, and the
    watcher needs an atomic claim (``queued`` → ``running`` guarded against a double
    run) — none of which a single column on ``Board`` can express.

    State machine (the load-bearing ``state`` column):
        queued   — enqueued by ``request_board_scan``; the watcher polls for these.
        running  — claimed by the watcher (``started_at`` set); scanner in flight.
        done     — scanner reported success; immediate ``poll_board`` ingest fired.
        failed   — scanner reported a non-zero RC; ``detail`` records why, NO ingest.

    Enqueue idempotency (R5 mitigation, architect's recommendation): an enqueue
    RE-USES an existing ``queued`` row for the same board rather than stacking a
    second — re-clicking "Scan now" while one waits does not duplicate scanner runs.
    A board already ``running`` still enqueues a fresh ``queued`` job (a new request
    after the in-flight one starts is legitimate).

    ``project_key`` is a snapshot of the resolved key at enqueue time (audit even if
    ``Board.sonarqube_project_key`` later changes). ``requested_by`` is nullable (the
    auto-trigger / cron path has no actor).
    """

    __tablename__ = "sonar_scan_jobs"
    __table_args__ = (
        # The watcher's pending query filters on state — cheap index on the hot path.
        Index("ix_sonar_scan_jobs_state", "state"),
        Index("ix_sonar_scan_jobs_board_id", "board_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    board_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(_FK_BOARDS_ID, ondelete="CASCADE"),
        nullable=False,
    )
    # PH-246: which repository this scan job targets (None for a legacy board-level
    # row; the migration backfills it to the board's primary repo). Nullable so legacy
    # rows validate; every NEW row sets it. ``request_board_scan`` enqueues one job per
    # scannable repo, idempotent per ``(board_id, repo_id, queued)``. CASCADE on repo
    # delete. ``repo_slug`` is a snapshot so the host watcher (PH-248) knows which repo
    # a job targets without a join (audit even if the repo is later renamed/removed).
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(_FK_REPOSITORIES_ID, ondelete="CASCADE"),
    )
    repo_slug: Mapped[str | None] = mapped_column(String(120))
    # Snapshot of the resolved SonarQube projectKey at enqueue (audit).
    project_key: Mapped[str] = mapped_column(String(400), nullable=False)
    # Lifecycle: queued | running | done | failed (see class docstring).
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # Who clicked "Scan now"; nullable for an auto/cron-triggered enqueue.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(_FK_ACTORS_ID))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failure reason / scanner RC / "ingested" note — human-readable, secret-free.
    detail: Mapped[str | None] = mapped_column(Text)

    board: Mapped[Board] = relationship(back_populates="sonar_scan_jobs")
