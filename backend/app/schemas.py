"""Pydantic request and response schemas."""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Reused field descriptions (avoid duplicated string literals across schemas).
_DESC_WORKFLOW_UUID = "Workflow UUID"

# ---------------------------------------------------------------------------
# PH-150: Git repository schemas (G1 — config only; reader/sync G2-G6)
# ---------------------------------------------------------------------------

Provider = Literal["github", "gitlab", "local"]


def _validate_local_path(local_path: str) -> None:
    """Shared local_path guard (allowlist prefix + traversal) for repo payloads.

    PH-221: factored out of ``RepositoryUpsert`` so ``RepositoryCreate`` reuses
    the identical ``/repos/`` allowlist + ``..`` traversal check.
    """
    if not local_path.startswith("/repos/"):
        raise ValueError("local_path must start with '/repos/'")
    if ".." in local_path.split("/"):
        raise ValueError("local_path must not contain '..'")


class RepositoryUpsert(BaseModel):
    """Payload for PUT /api/boards/{key}/repository (connect/update primary)."""

    provider: Provider = "local"
    remote_url: str | None = Field(default=None, max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=120)
    local_path: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_constraints(self) -> Self:
        _validate_local_path(self.local_path)
        # Non-local providers require a remote URL.
        if self.provider in ("github", "gitlab") and not self.remote_url:
            raise ValueError(f"remote_url is required when provider='{self.provider}'")
        return self


class RepositoryCreate(BaseModel):
    """Payload for POST /api/boards/{key}/repositories (add a repo to a board).

    PH-221: extends the upsert payload with optional ``name`` / ``slug``. When
    omitted the service derives them from the ``local_path`` basename (slug is
    deduplicated within the board by suffixing -2, -3, ...). The first repo added
    to a board is auto-promoted to primary.
    """

    provider: Provider = "local"
    remote_url: str | None = Field(default=None, max_length=500)
    default_branch: str = Field(default="main", min_length=1, max_length=120)
    local_path: str = Field(..., min_length=1, max_length=500)
    name: str | None = Field(default=None, max_length=160)
    slug: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _validate_constraints(self) -> Self:
        _validate_local_path(self.local_path)
        if self.provider in ("github", "gitlab") and not self.remote_url:
            raise ValueError(f"remote_url is required when provider='{self.provider}'")
        return self


class RepositorySummary(BaseModel):
    """Compact repository view embedded in BoardResponse."""

    id: UUID
    # PH-221: additive multi-repo identity fields.
    slug: str
    name: str
    is_primary: bool
    provider: str
    remote_url: str | None
    default_branch: str
    local_path: str
    last_synced_sha: str | None
    last_synced_at: datetime | None


class RepositoryResponse(RepositorySummary):
    """Full repository response (repo collection + singular primary routes)."""

    board_id: UUID
    created_at: datetime
    updated_at: datetime


class RepositoryListResponse(BaseModel):
    """Response for GET /api/boards/{key}/repositories (PH-221).

    Exactly one entry has ``is_primary=true`` when the board has ≥1 repo.
    """

    repositories: list[RepositoryResponse]


# ---------------------------------------------------------------------------
# PH-222: Git filesystem auto-detection (C2 — read-only candidate scan)
# ---------------------------------------------------------------------------


class DetectedRepo(BaseModel):
    """A git working copy discovered under the scan root (frozen contract for PH-225).

    Fields map 1:1 onto ``RepositoryCreate`` (``local_path``/``name``/``remote_url``/
    ``default_branch``/``provider_guess``→``provider``) so the frontend can one-click
    add a detected repo by copying these fields into ``POST /repositories``.
    """

    local_path: str  # absolute, e.g. "/repos/project-hub" (== the value to POST as local_path)
    name: str  # basename of local_path (e.g. "project-hub")
    is_git: bool  # True if the hardened reader opened it as a real repo (always True for rows)
    remote_url: str | None  # origin remote URL, or None when there is no 'origin' remote
    default_branch: str | None  # reader._detect_default_branch(repo); None only if undetectable
    provider_guess: Provider  # "github" | "gitlab" | "local" inferred from remote_url host
    already_linked: bool  # True when local_path (realpath) matches a Repository row on THIS board


class DetectedReposResponse(BaseModel):
    """Response for GET /api/boards/{key}/repositories/detect (PH-222).

    ``repositories`` is empty when the scan root is missing/empty, git is
    unavailable, or every candidate failed to open — never a 500.
    """

    repositories: list[DetectedRepo]


class GitStatusResponse(BaseModel):
    """Response for GET /api/boards/{key}/git/status."""

    connected: bool
    repository: RepositorySummary | None
    last_synced_at: datetime | None  # convenience alias of repository.last_synced_at


# ---------------------------------------------------------------------------
# PH-153: Git read API schemas (G4 — graph / branches / commits / commit-detail)
# ---------------------------------------------------------------------------


class GitCommitSummary(BaseModel):
    """Single commit entry used in graph and commits-list responses.

    ``refs`` is computed server-side (branch names whose head_sha == this sha).
    ``ticket_keys`` comes from ``git_commits.ticket_keys`` (JSON column set by
    the sync runner — G3).
    """

    sha: str
    short_sha: str
    parents: list[str]
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    summary: str
    is_conventional: bool
    commit_type: str | None
    ticket_keys: list[str]
    refs: list[str]  # branch names pointing at this commit


class GitCommitFileEntry(BaseModel):
    """Per-file change entry inside a commit-detail response.

    ``change_type`` is one of A(dded), M(odified), D(eleted), R(enamed),
    C(opied) — matching git's single-letter codes stored in the cache.
    """

    path: str
    old_path: str | None
    change_type: str
    additions: int
    deletions: int
    is_binary: bool


class GitCommitDetail(GitCommitSummary):
    """Full commit payload including per-file numstat.

    Extends ``GitCommitSummary`` with fields only needed for the detail view
    so the commits-list response stays lean.
    """

    committer_name: str
    committer_email: str
    body: str
    files: list[GitCommitFileEntry]


class GitBranchEntry(BaseModel):
    """Single branch entry from GET /git/branches.

    ``ahead`` / ``behind`` are relative to the repo's default branch.
    Both are ``null`` when the BFS walk exceeds ``git_backfill_limit`` (deep
    divergence) — G8 should render "..." for null values.
    For the default branch itself both are always 0.
    """

    name: str
    head_sha: str
    is_default: bool
    ticket_key: str | None
    ahead: int | None
    behind: int | None


class GitGraphResponse(BaseModel):
    """Response for GET /git/graph.

    ``tags`` is always an empty list in G4 (no cache table yet; G14 follow-up).
    """

    commits: list[GitCommitSummary]
    branches: list[GitBranchEntry]
    tags: list[object]


class GitCommitsListResponse(BaseModel):
    """Response for GET /git/commits (paginated list)."""

    commits: list[GitCommitSummary]


class GitBranchesListResponse(BaseModel):
    """Response for GET /git/branches."""

    branches: list[GitBranchEntry]


# ---------------------------------------------------------------------------
# PH-154: G5 — Diff API schemas
# ---------------------------------------------------------------------------


class FileDiff(BaseModel):
    """Per-file diff entry in a unified diff response.

    ``patch`` is None when the file is binary or when the byte-cap has been
    reached (truncated=True on this entry).  ``old_path`` is populated only for
    renames/copies.
    """

    path: str
    old_path: str | None
    change_type: str  # A|M|D|R|C
    additions: int
    deletions: int
    is_binary: bool
    patch: str | None  # null when binary or omitted after byte-cap
    truncated: bool = False  # True when this specific file's patch was sliced


class DiffResponse(BaseModel):
    """Response for GET /api/boards/{key}/git/commits/{sha}/diff."""

    sha: str  # full 40-hex sha
    files: list[FileDiff]
    truncated: bool  # top-level cap hit anywhere in this response


class RangeDiffResponse(BaseModel):
    """Response for GET /api/boards/{key}/git/diff?base=&head=."""

    base: str  # as supplied by caller (branch name or sha)
    head: str
    files: list[FileDiff]
    truncated: bool


class TicketCommitEntry(BaseModel):
    """Single commit linked to a ticket (cache-only, no patch text)."""

    sha: str
    short_sha: str
    summary: str
    authored_at: datetime
    committed_at: datetime
    author_name: str
    additions: int  # sum over git_commit_files
    deletions: int  # sum over git_commit_files
    files_changed: int  # COUNT(*) from git_commit_files


class TicketCommitsResponse(BaseModel):
    """Response for GET /api/tickets/{key}/commits."""

    branch_name: str | None
    commits: list[TicketCommitEntry]  # newest-first by committed_at


TicketType = Literal["feature", "bug", "task", "epic"]
Priority = Literal["low", "medium", "high", "urgent"]


class ActorSummary(BaseModel):
    id: UUID
    kind: str
    display_name: str
    agent_id: str | None = None
    agent_role_hint: str | None = None


class MembershipSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    board_id: UUID
    board_key: str
    role: str


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor: ActorSummary
    memberships: list[MembershipSummary]


class WorkflowResponse(BaseModel):
    id: UUID
    name: str
    states: list[dict[str, object]]
    transitions: list[dict[str, object]]
    is_default: bool


class BoardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    project_type: str | None = None
    roles: dict[str, object] | None = None
    # PH-230: editable HOST filesystem root (drives git auto-detect + sonar key).
    # Empty string clears the path (board with no path); a non-empty value is
    # validated at the API boundary via repo_paths.to_container_path → 422.
    repos_path: str | None = Field(default=None, max_length=500)


class BoardHealth(BaseModel):
    """PH-193: latest SonarQube quality snapshot embedded in BoardResponse.

    Null on a board with no persisted metric (mirrors the ``repository`` field).
    Carries metrics only — never the SonarQube token or any secret.
    """

    quality_gate_status: str | None
    bugs: int | None
    vulnerabilities: int | None
    code_smells: int | None
    coverage: float | None  # percent 0..100
    duplicated_lines_density: float | None  # percent 0..100
    ncloc: int | None
    fetched_at: datetime


class SonarIssueItem(BaseModel):
    """PH-203: one SonarQube issue, component mapped to a relative file path.

    Carries issue metadata only — never the SonarQube token or any secret.
    """

    key: str
    rule: str
    severity: str | None
    type: str | None
    component: str  # relative file path (the "<projectKey>:" prefix stripped)
    line: int | None  # SonarQube omits line for project/file-level issues
    message: str
    hash: str | None


class SonarIssuesResponse(BaseModel):
    """PH-203: proxied SonarQube issue-search result for a board.

    Always 200 from the endpoint: ``status`` distinguishes a live fetch ("ok") from
    the graceful-degradation paths ("unreachable" | "not_configured" |
    "no_project_key"). ``dashboard_url`` is a HOST-facing deep-link base (built from
    ``sonarqube_scan_url``, never the compose-internal ``sonarqube_url``); null when
    there is no resolvable projectKey / sonar is not configured. Never emits the token.
    """

    status: str  # "ok" | "unreachable" | "not_configured" | "no_project_key"
    total: int
    page: int
    page_size: int
    issues: list[SonarIssueItem]
    dashboard_url: str | None


class SonarSetupRequest(BaseModel):
    """PH-223: body for ``POST .../sonarqube/setup``.

    ``project_key`` is optional — omit (or send ``{}``) to derive the default from
    the board key (PH → ``project-hub`` to match ``sonar-project.properties``; else
    the board key lowercased). A supplied key overrides the derived default.
    """

    project_key: str | None = None


class SonarSetupStatus(BaseModel):
    """PH-223: setup/sync/status view of a board's SonarQube linkage (SECRET-FREE).

    Returned 200 by all three of setup/sync/status — the never-500/never-hang
    contract (mirrors PH-203). Carries only the resolved key + cached metric
    freshness + a HOST-facing dashboard deep link; NEVER the ``sonarqube_token`` or
    the compose-internal ``sonarqube_url``.

      enabled                 settings.sonarqube_enabled (server-level kill switch)
      reachable               True only when a live poll just succeeded; for the
                              pure read path it stays conservative (no blocking probe)
      configured              the board has a resolvable project key
      project_key             the resolved key (or null)
      last_metric_fetched_at  cached SonarQubeMetric.fetched_at (freshness)
      quality_gate_status     latest cached gate (OK|ERROR|WARN|NONE) or null
      dashboard_url           HOST-facing deep link (sonarqube_scan_url + /dashboard?id=key)
      message                 human-readable state summary
    """

    enabled: bool
    reachable: bool
    configured: bool
    project_key: str | None
    last_metric_fetched_at: datetime | None
    quality_gate_status: str | None
    dashboard_url: str | None
    message: str


class BoardResponse(BaseModel):
    id: UUID
    key: str
    name: str
    description: str
    project_type: str
    roles: dict[str, object]
    workflow: WorkflowResponse
    created_at: datetime
    updated_at: datetime
    # PH-150: optional repository summary — null when not yet configured.
    repository: RepositorySummary | None = None
    # PH-193: optional SonarQube board-health snapshot — null when no metric row.
    health: BoardHealth | None = None
    # PH-228: per-board HOST filesystem path (read-only here; backfilled for the 6
    # known boards, null otherwise). PATCH editability is deferred to PH-230 —
    # BoardUpdate is intentionally NOT extended in PH-228.
    repos_path: str | None = None


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    states: list[dict[str, object]] = Field(..., min_length=1)
    transitions: list[dict[str, object]] = Field(default_factory=list)
    is_default: bool = Field(default=False)


class WorkflowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    states: list[dict[str, object]] | None = None
    transitions: list[dict[str, object]] | None = None
    is_default: bool | None = None
    board_id: str | None = None  # PH-97: optional board context for clone-guard


class TransitionCreate(BaseModel):
    workflow_id: str = Field(..., description=_DESC_WORKFLOW_UUID)
    from_state: str = Field(..., min_length=1)
    to_state: str = Field(..., min_length=1)
    # PH-99: None = absent (preserve existing), [] = explicit "all roles" (remove key),
    # [...] = replace with supplied list.  Matches upsert semantics in add_transition().
    allowed_roles: list[str] | None = None
    field_gates: dict[str, object] | None = None
    board_id: str | None = None  # PH-97: optional board context for clone-guard


class TransitionUpdate(BaseModel):
    allowed_roles: list[str] | None = None
    field_gates: dict[str, object] | None = None
    board_id: str | None = None  # PH-97: optional board context for clone-guard


class FieldGatesUpdate(BaseModel):
    workflow_id: str = Field(..., description=_DESC_WORKFLOW_UUID)
    from_state: str = Field(..., min_length=1)
    to_state: str = Field(..., min_length=1)
    field_gates: dict[str, object] = Field(..., description="Field requirements for transition")
    board_id: str | None = None  # PH-97: optional board context for clone-guard


class EnsureBoardWorkflowInput(BaseModel):
    """PH-97: Ensure a board has its own private workflow copy (clone if shared)."""

    board_id: str = Field(..., description="Board UUID")


class EnsureBoardWorkflowResponse(BaseModel):
    """PH-97: Result of ensure_board_workflow — returns current (possibly cloned) workflow."""

    workflow: WorkflowResponse
    cloned: bool


class WorkflowActivation(BaseModel):
    board_id: str = Field(..., description="Board UUID or key")
    workflow_id: str = Field(..., description=_DESC_WORKFLOW_UUID)


class BoardListResponse(BaseModel):
    boards: list[BoardResponse]


class TicketCreate(BaseModel):
    board_id: str = Field(..., description="Board UUID or key")
    type: TicketType
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    priority: Priority = "medium"
    epic_id: UUID | None = None
    labels: list[str] = Field(default_factory=list)
    acceptance_criteria: str | None = None
    technical_depth: str | None = None
    steps_to_reproduce: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    story_points: int | None = None
    due_date: date | None = None


class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    priority: Priority | None = None
    epic_id: UUID | None = None
    labels: list[str] | None = None
    acceptance_criteria: str | None = None
    technical_depth: str | None = None
    impact_analysis: str | None = None
    test_plan: str | None = None
    steps_to_reproduce: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    branch_name: str | None = None
    story_points: int | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def prevent_required_field_nulls(self) -> Self:
        required_when_present = {"title", "description", "priority", "labels"}
        for field_name in required_when_present & self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class AssignTicket(BaseModel):
    assignee_id: str | None = Field(default=None, description="Actor UUID or agent_id")


class AgentPhaseUpdate(BaseModel):
    phase: Literal["planning", "analyzing", "coding", "testing", "reviewing", "idle"]
    message: str = Field(default="", max_length=500)


class DeleteTicket(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class TransitionState(BaseModel):
    to_state: str
    comment: str | None = None


class TicketResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    key: str
    board_id: UUID
    type: str
    title: str
    description: str
    state: str
    agent_phase: dict[str, object] | None
    assignee: ActorSummary | None
    reporter: ActorSummary
    priority: str
    epic_id: UUID | None
    labels: list[str]
    acceptance_criteria: str | None
    technical_depth: str | None
    impact_analysis: str | None
    test_plan: str | None
    steps_to_reproduce: str | None
    expected_behavior: str | None
    actual_behavior: str | None
    story_points: int | None
    due_date: date | None
    branch_name: str | None
    claimed_by: UUID | None
    claimed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    links: dict[str, str] = Field(alias="_links")


class TicketListResponse(BaseModel):
    tickets: list[TicketResponse]


class LinkPR(BaseModel):
    ticket_id: str
    pr_url: str = Field(..., min_length=1)
    pr_number: int | None = None
    pr_title: str = ""


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1)


class CommentResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    author: ActorSummary
    body: str
    created_at: datetime
    edited_at: datetime | None


class HistoryResponse(BaseModel):
    id: UUID
    event_type: str
    field: str | None
    old_value: object | None
    new_value: object | None
    metadata: dict[str, object]
    actor: ActorSummary
    created_at: datetime


class NotificationResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_key: str
    event_type: str
    message: str
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    unread_count: int


# ---------------------------------------------------------------------------
# PH-155: G6 — git refresh response schema
# ---------------------------------------------------------------------------


class GitRefreshResponse(BaseModel):
    """Response for POST /api/boards/{key}/git/refresh (G6)."""

    ok: bool
    status: Literal["queued", "coalesced", "disabled"]
    last_sync_at: datetime | None = None


# ---------------------------------------------------------------------------
# PH-162: G13 — rotate refresh secret response schema
# ---------------------------------------------------------------------------


class RotateRefreshSecretResponse(BaseModel):
    """Response for POST /api/boards/{key}/repository/rotate-refresh-secret (G13).

    Returns the new plaintext secret ONCE.  Subsequent GET /boards/{key}
    will show the secret masked as '*****'.
    ``hook_install_command`` is a ready-to-paste shell snippet.
    """

    refresh_secret: str = Field(..., min_length=48, max_length=48)
    hook_install_command: str


# ---------------------------------------------------------------------------
# PH-39: Membership management schemas
# ---------------------------------------------------------------------------


class MembershipResponse(BaseModel):
    id: UUID
    actor: ActorSummary
    role: str
    created_at: datetime
    updated_at: datetime


class MembershipListResponse(BaseModel):
    members: list[MembershipResponse]


class MembershipCreate(BaseModel):
    actor_id: UUID
    role: str = Field(..., min_length=1, max_length=80)


class MembershipUpdate(BaseModel):
    role: str = Field(..., min_length=1, max_length=80)


class ActorListResponse(BaseModel):
    actors: list[ActorSummary]
