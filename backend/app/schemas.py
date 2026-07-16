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
    # PH-268: True when this commit's sha is reachable from the default-branch
    # head over the FULL repo cache (i.e. it is merged into the default branch),
    # or IS the default head itself.  graph-only signal: lets the frontend
    # branch-graph classify merge-ness authoritatively instead of inferring it
    # from the (incomplete) windowed parent DAG, which misses merge commits on
    # non-zero lanes and misclassifies stash/octopus 2nd-parent edges.  Defaults
    # to False so the commit-detail payload (which never computes it) and any
    # legacy/un-upgraded consumer stay back-compatible.
    merged_into_default: bool = False


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
    """Single commit linked to a ticket (cache-only, no patch text).

    PH-247: a ticket's linked commits aggregate across ALL of a board's repos
    (``ticket_commits_payload`` joins ``git_commit_tickets`` by ``ticket_id``
    only). Each entry therefore carries its OWN source-repo identity so the FE
    can render a repo badge per row and thread ``repo_slug`` into the per-repo
    commit-detail / diff fetch (``?repo=`` selector). Non-optional: a real
    commit row always has a NOT-NULL ``repo_id`` and its ``Repository.slug`` /
    ``Repository.name`` are NOT NULL — mirrors PH-246 ``RepoHealth`` naming
    (RepoHealth's fields are Optional only because of its board-level aggregate
    row; a commit has no aggregate case).
    """

    sha: str
    short_sha: str
    summary: str
    authored_at: datetime
    committed_at: datetime
    author_name: str
    additions: int  # sum over git_commit_files
    deletions: int  # sum over git_commit_files
    files_changed: int  # COUNT(*) from git_commit_files
    repo_id: UUID  # PH-247: source repo (git_commits.repo_id, NOT NULL)
    repo_slug: str  # PH-247: stable per-board repo slug (Repository.slug)
    repo_name: str  # PH-247: repo display name (Repository.name)


class TicketBranchEntry(BaseModel):
    """Single git branch attributed to a ticket, scoped to ONE linked repo.

    PH-250: surfaces the per-repo branch identity that already lives in
    ``git_branches`` (each row carries ``repo_id`` (NOT NULL) + ``ticket_key``,
    the latter derived from the branch NAME by ``git.sync`` on every sync for
    every repo). A ticket worked in a non-primary repo therefore already has a
    branch row with the CORRECT ``repo_id`` — this entry just exposes it on the
    per-ticket read path, mirroring the PH-247 ``TicketCommitEntry`` repo-tag
    pattern (``repo_id``/``repo_slug``/``repo_name``).

    Matching is by ``git_branches.ticket_key == ticket.key`` (the KEY STRING,
    e.g. "PH-250"), NOT by ``ticket_id`` — that is the join key sync writes.
    Contrast ``TicketCommitEntry`` whose source rows join by ``ticket_id`` (the
    UUID) via ``git_commit_tickets``. Both are correct for their own source.

    Non-optional repo fields: a real branch row always has a NOT-NULL
    ``repo_id`` whose ``Repository.slug`` / ``Repository.name`` are NOT NULL
    (1:1 join, cannot multiply rows). ``last_commit_at`` is nullable on the
    source column, so it is ``datetime | None`` here.
    """

    name: str
    head_sha: str
    is_default: bool
    last_commit_at: datetime | None
    repo_id: UUID  # PH-250: source repo (git_branches.repo_id, NOT NULL)
    repo_slug: str  # PH-250: stable per-board repo slug (Repository.slug)
    repo_name: str  # PH-250: repo display name (Repository.name)


class TicketCommitsResponse(BaseModel):
    """Response for GET /api/tickets/{key}/commits.

    PH-250: ``branches`` is ADDITIVE — the existing ``branch_name`` (legacy
    single repo-agnostic ``<key>-<slug>`` pointer) and ``commits`` keep their
    exact shape/semantics (frozen contract for the FE ``git.ts`` consumer).
    ``branches`` aggregates a ticket's branches across ALL linked repos, each
    tagged with its own repo (``[]`` when none — 200, never 404).
    """

    branch_name: str | None
    branches: list[TicketBranchEntry]  # PH-250: repo-attributed; [] when none
    commits: list[TicketCommitEntry]  # newest-first by committed_at


# chore/refactor/hotfix: Jarwis workflow vocabulary (contracts/ticket-fields.md) —
# the hotfix/refactor flows open tickets with these types; DB column is String(20),
# so no migration (PH-290, R12 reconciliation).
TicketType = Literal["feature", "bug", "task", "epic", "chore", "refactor", "hotfix"]
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


class RepoHealth(BaseModel):
    """PH-246: per-repo SonarQube quality snapshot in ``BoardResponse.repo_health``.

    One entry per linked repository (the FE / PH-249 renders these as cards). Carries the
    same 7 ``BoardHealth`` metrics PLUS the repo identity + its own dashboard deep link.
    SECRET-FREE — never the token or the compose-internal sonarqube_url.

      repo_id           the linked Repository this snapshot belongs to (null = board-level
                        aggregate row, see ``BoardResponse.repo_health`` note)
      repo_slug         the repo's stable per-board slug (null = aggregate)
      repo_name         the repo's display name (null = aggregate)
      is_primary        whether this repo is the board's primary/default repo (PH-251 —
                        drives the FE's per-repo `primary` badge off this un-gated surface
                        instead of the member-gated /repositories call; null-repo aggregate
                        row → False)
      project_key       the SonarQube projectKey this repo scanned under (always non-null —
                        derivable even before a scan via derive_repo_project_key)
      <7 metrics>       quality_gate_status / bugs / vulnerabilities / code_smells /
                        coverage / duplicated_lines_density / ncloc (same as BoardHealth;
                        all null for a linked-but-never-scanned repo — PH-252)
      fetched_at        poll wall-clock (freshness); NULL for a linked repo with no metric
                        row yet (PH-252 — "No analysis yet" card). BoardHealth.fetched_at
                        stays non-null; only this per-repo field widened required→optional.
      dashboard_url     HOST-facing deep link for this repo's project (null = no link;
                        always null for an unscanned repo — PH-252 null-until-first-scan)
    """

    repo_id: UUID | None
    repo_slug: str | None
    repo_name: str | None
    is_primary: bool
    project_key: str
    quality_gate_status: str | None
    bugs: int | None
    vulnerabilities: int | None
    code_smells: int | None
    coverage: float | None
    duplicated_lines_density: float | None
    ncloc: int | None
    fetched_at: datetime | None
    dashboard_url: str | None


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


class RepoSonarSetupRequest(BaseModel):
    """PH-254: body for ``POST .../repositories/{selector}/sonarqube/setup``.

    ``project_key`` is optional — omit (or send ``{}``) to derive + persist the
    default per-repo key (``derive_repo_project_key``: primary inherits the board
    key; a sibling gets ``<primaryKey>-<slug>``). A supplied key overrides the
    derived default. A whitespace-only key is rejected server-side (422) since
    Pydantic ``min_length`` does not trim. A dedicated class (not a reuse of
    ``SonarSetupRequest``) keeps the per-repo docstring + room for future
    divergence clean.
    """

    project_key: str | None = None


class SonarSetupStatus(BaseModel):
    """PH-223: setup/sync/status view of a board's SonarQube linkage (SECRET-FREE).

    Returned 200 by all three of setup/sync/status — the never-500/never-hang
    contract (mirrors PH-203). Carries only the resolved key + cached metric
    freshness + a HOST-facing dashboard deep link; NEVER the ``sonarqube_token`` or
    the compose-internal ``sonarqube_url``.

      status                  PH-235: honest discriminator —
                              disabled|unconfigured|no_analysis|ok|unreachable.
                              The load-bearing field the UI keys its messaging off
                              (no inference from the boolean trio).
      has_analysis            PH-235: a cached SonarQubeMetric row exists. The honest
                              "we have data" signal that USED to be smuggled inside
                              ``reachable`` — split out so absence-of-metric no longer
                              masquerades as a false "unreachable".
      enabled                 settings.sonarqube_enabled (server-level kill switch)
      reachable               True only when a live poll just succeeded; for the
                              pure read path it is best-effort/optimistic — NEVER a
                              false "unreachable" (the read path NEVER probes). Keyed
                              off ``status``, not ``reachable``, for messaging.
      configured              the board has a resolvable project key
      project_key             the resolved key (or null)
      last_metric_fetched_at  cached SonarQubeMetric.fetched_at (freshness)
      quality_gate_status     latest cached gate (OK|ERROR|WARN|NONE) or null
      dashboard_url           HOST-facing deep link (sonarqube_scan_url + /dashboard?id=key)
      message                 human-readable state summary
    """

    # PH-235: honest status discriminator + the split-out has_analysis signal.
    # ADDITIVE — every pre-existing field below is unchanged (backward-compatible).
    status: str
    has_analysis: bool
    enabled: bool
    reachable: bool
    configured: bool
    project_key: str | None
    last_metric_fetched_at: datetime | None
    quality_gate_status: str | None
    dashboard_url: str | None
    message: str


class SonarRepoScanItem(BaseModel):
    """PH-246: per-repo outcome inside a ``SonarScanResponse``. SECRET-FREE, additive.

    One per linked repo the board scan considered — lets the FE (PH-249) show which
    repos queued vs were skipped (unsupported/error) without a separate call.
    """

    repo_slug: str | None
    project_key: str | None
    scan_status: str
    language: str | None


class SonarScanResponse(BaseModel):
    """PH-236: result of ``POST .../sonarqube/scan`` ("Scan now"). Always 200, SECRET-FREE.

    ``scan`` is DISTINCT from ``sync``: sync re-polls the EXISTING analysis; scan
    ENQUEUES a NEW per-board analysis run (the backend can't ``docker compose run``, so
    the actual scanner runs HOST-side via ``scripts/sonar-scan-board.sh``). The endpoint
    is cheap + NON-blocking + never-500.

      scan_status       the load-bearing enum —
                        queued|running|unsupported|needs_dotnet_setup|disabled|
                        unconfigured|error.
                        PH-257: ``needs_dotnet_setup`` is the HONEST csharp (Unity) gate —
                        C# IS analyzable (via the host .NET pipeline) but the host
                        prerequisite (dotnet SDK + dotnet-sonarscanner +
                        SONAR_DOTNET_ENABLED=true) is not declared ready, so NO job is
                        enqueued (not a silent fail, not a fake ``queued``).
                        PH-246: now a per-board AGGREGATE (queued if ≥1 repo queued).
      project_key       the resolved projectKey (PRIMARY repo, back-compat; or null)
      language          detected primary-repo language label (or null)
      container_source  the PRIMARY repo's in-container ``/repos/<rel>`` path (or null)
      message           human-readable outcome (+ the host command on ``queued``)
      repos             PH-246 ADDITIVE: per-repo outcomes (empty for a repo-less board)

    NEVER carries the ``sonarqube_token`` or the compose-internal ``sonarqube_url``.
    """

    scan_status: str
    project_key: str | None
    language: str | None
    container_source: str | None
    message: str
    # PH-246 additive — per-repo breakdown; defaults to [] so existing consumers reading
    # only the top-level fields are unaffected.
    repos: list[SonarRepoScanItem] = Field(default_factory=list)


class SonarRepoScanResponse(BaseModel):
    """PH-255: result of per-repo ``POST .../sonarqube/scan``. Always 200, SECRET-FREE.

    The per-REPO sibling of ``SonarScanResponse`` — it targets ONE repo (not the whole
    board), so there is no ``repos[]`` aggregate / no top-level board fields. It carries
    the single repo's identity (``repo_slug``) + the resolved ``project_key`` / detected
    ``language`` + the load-bearing ``scan_status`` and, on ``queued`` ONLY, the enqueued
    ``job_id`` the FE (PH-256) can poll/await.

      repo_slug     the targeted repo's stable per-board slug (or null — board-level)
      project_key   the resolved per-repo projectKey (``derive_repo_project_key``; or null)
      language      detected language label for THIS repo (or null)
      scan_status   the load-bearing enum —
                    queued|running|unsupported|needs_dotnet_setup|disabled|unconfigured|
                    error. A non-scannable repo (csharp-needs-setup / genuinely
                    unsupported / no key / no path / disabled) returns the HONEST status
                    with NO job — NOT a 409/422 (decision A).
      job_id        the enqueued ``SonarScanJob`` id when ``scan_status=="queued"``, else
                    null (no job is created for a non-queued outcome).
      message       human-readable outcome (+ the host command on ``queued``)

    NEVER carries the ``sonarqube_token`` or the compose-internal ``sonarqube_url``.
    """

    repo_slug: str | None
    project_key: str | None
    language: str | None
    scan_status: str
    job_id: UUID | None
    message: str


class SonarScanPlanResponse(BaseModel):
    """PH-236: the per-repo scan plan the HOST runner + frontend C3 consume.

    Returned 200 by ``GET .../sonarqube/scan-plan`` (single — primary repo, back-compat)
    and as elements of ``GET .../sonarqube/scan-plans`` (PH-246 — the list). The host
    script (``scripts/sonar-scan-board.sh``) curls this and, when ``supported``, runs the
    scanner with ``-Dsonar.projectKey=<project_key> -Dsonar.sources=<container_source>``.
    SECRET-FREE (no token, no compose-internal ``sonarqube_url``).

    The 7 FROZEN fields (PH-236/244) — NEVER renamed/removed:
      project_key       the resolved projectKey (or null when unconfigured)
      container_source  the in-container ``/repos/<rel>`` sources path the scanner reads
                        (or null on no/invalid repos_path)
      host_source       the HOST path (informational)
      language          detected primary language label (or null)
      supported         True ⇒ CE can analyze ⇒ the runner should scan; False ⇒ skip
      reason            why (esp. when ``supported`` is False)
      exclusions        ``sonar.exclusions`` glob the runner passes (or null)

    PH-246 ADDITIVE — self-describing for per-repo job + health keying (null on the
    board-level single-object endpoint when no primary repo drove the plan):
      repo_id           the linked Repository this plan targets (or null)
      repo_slug         the repo's stable per-board slug (or null)
    """

    project_key: str | None
    container_source: str | None
    host_source: str | None
    language: str | None
    supported: bool
    reason: str
    exclusions: str | None
    # PH-246 additive — null on a board-level (repo-less) plan element.
    repo_id: UUID | None = None
    repo_slug: str | None = None


# ---------------------------------------------------------------------------
# PH-239: scan-job lifecycle schemas — the host-watcher seam.
# ---------------------------------------------------------------------------


class PendingScanItem(BaseModel):
    """PH-239: one queued scan job the host watcher should run. SECRET-FREE.

    Returned in the list from ``GET /api/scans/pending``. Carries only the job id +
    board key + project key (+ PH-246 ``repo_slug``) — NEVER the token or the
    compose-internal sonarqube_url.

    PH-246: ``repo_slug`` tells the watcher which repo each job targets (so the
    ``/complete`` ingest re-polls the right project). Nullable for a legacy board-level
    job; additive (an old watcher ignoring it still works).
    """

    job_id: UUID
    board_key: str
    project_key: str
    repo_slug: str | None = None


class ScanJobResponse(BaseModel):
    """PH-239: a scan job's lifecycle state. SECRET-FREE.

    Returned by ``POST /api/scans/{job_id}/claim`` and ``.../complete``.

      job_id        the scan job uuid
      board_id      the owning board
      project_key   the resolved projectKey snapshot at enqueue
      state         queued | running | done | failed
      detail        failure reason / ingest note (or null)
    """

    job_id: UUID
    board_id: UUID
    project_key: str
    state: str
    detail: str | None


class ScanCompleteRequest(BaseModel):
    """PH-239: body for ``POST /api/scans/{job_id}/complete``.

    ``success`` is the scanner's REAL outcome (the watcher reads the scanner RC, NOT
    the always-exit-0 wrapper script's exit code — R4). ``detail`` is an optional
    secret-free note (failure reason / RC).
    """

    success: bool
    detail: str | None = None


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
    # PH-246: KEPT as the PRIMARY repo's metric (back-compat — single-repo boards +
    # existing FE render identically). The per-repo breakdown lives in ``repo_health``.
    health: BoardHealth | None = None
    # PH-246: per-repo SonarQube breakdown — one ``RepoHealth`` per linked repo that has
    # a metric (additive; empty list for boards with no per-repo metrics). PH-249 reads
    # this to render per-repo cards; old consumers reading only ``health`` are unaffected.
    repo_health: list[RepoHealth] = Field(default_factory=list)
    # PH-228: per-board HOST filesystem path (read-only here; backfilled for the 6
    # known boards, null otherwise). PATCH editability is deferred to PH-230 —
    # BoardUpdate is intentionally NOT extended in PH-228.
    repos_path: str | None = None
    # PH-235: the resolved SonarQube project key (additive nullable). Lets the
    # board-header SonarHealthPanel distinguish "no key" (connect a key) from
    # "key set, no scan yet" (linked — no analysis) without a separate status call.
    sonarqube_project_key: str | None = None


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
    # PH-294: PM can declare dependencies at decompose time; Architect fills
    # files_touched_globs at approve (Jarwis planning/parallel flows).
    files_touched_globs: list[str] | None = None
    blocked_by: list[str] | None = None


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
    # PH-294: structured parallel-independence fields (Jarwis contracts/parallel.md §1)
    files_touched_globs: list[str] | None = None
    blocked_by: list[str] | None = None

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


# ---------------------------------------------------------------------------
# PH-281 (epic PH-271): the ConceptTag CRUD/link/attach schemas were removed —
# the user-facing ConceptTag surface is gone; graph + search now read the inline
# `Ticket.labels` ARRAY. The ConceptTag* MODELS remain DORMANT in core.py (no
# table drop). See GraphNode / SearchResponse below.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cross-board concept graph (PH-274 / PH-279, re-pointed to labels in PH-281)
# ---------------------------------------------------------------------------
#
# Topology contract consumed by PH-277/PH-280 (frontend /space). Bipartite:
# ticket nodes + LABEL nodes (PH-281: was tag nodes), disambiguated by a
# TYPE-PREFIXED id ("ticket:<uuid>" / "label:<rawValue>") so a ticket UUID and a
# label value never collide. The label string is the disambiguator — kept RAW
# (un-slugified) after the "label:" prefix; the value is injective by
# construction and round-trips losslessly. Consumers do exact-string match on
# the id (never split on ":") — a ":" inside a label value is harmless.
# Every node ALSO carries a redundant `type` discriminator. Topology only — no
# layout/coordinates (PH-277 computes positions).
#
# PH-281 PIVOT (epic PH-271): the bipartite second axis moved from the separate
# ConceptTag entity to the inline `Ticket.labels` free-text ARRAY. GraphNode.type
# "tag" → "label"; GraphEdge "has_tag"/"tag_link" → "has_label" (no label↔label
# relation); GraphEdge.relation dropped. This is a BREAKING change vs the
# PH-274/279 ConceptTag surface — backward-compat explicitly NOT required (PH-280
# updates in lockstep). board-scope collapse + reference + epic edges UNCHANGED.


class GraphNode(BaseModel):
    id: str  # "ticket:<uuid>" | "label:<rawValue>" | "board:<key>"
    type: Literal["ticket", "label", "board"]  # PH-281: "tag" → "label"
    label: str  # ticket → key (e.g. "PH-274"); label → raw value; board → name/key
    # ticket-only (None for label/board nodes):
    board: str | None = None  # board KEY (e.g. "PH") for grouping/color-by-board
    board_id: UUID | None = None
    key: str | None = None  # ticket key (label duplicate, explicit for PH-277)
    state: str | None = None
    title: str | None = None
    # PH-281: label nodes have no slug (free-text Ticket.labels has no registry).
    # `color` kept (always None for label nodes) for shape stability with the
    # PH-274 consumer contract — the frontend (PH-280) HASHES the label string
    # client-side for color; the backend stores none.
    color: str | None = None  # hex e.g. #7dd3fc — None for label nodes


class GraphEdge(BaseModel):
    id: str  # stable, deterministic — see services/graph derivation
    source: str  # prefixed node id
    target: str  # prefixed node id
    # PH-281: "has_tag"/"tag_link" → "has_label" (labels have no label↔label
    # relation, so tag_link is dropped). "epic"/"reference"/"board" unchanged.
    # PH-288: the default graph is now ticket↔ticket only — it emits the WEIGHTED
    # top-K signal edges (`dependency`/`reference`/`epic`/`shared_label`/
    # `code_overlap`) instead of `has_label`. The Literal WIDENS additively;
    # `has_label` is KEPT (shape stability) even though the default graph no
    # longer emits it. `board` is the scope=board collapse node edge (unchanged).
    type: Literal[
        "has_label",
        "epic",
        "reference",
        "board",
        "dependency",
        "shared_label",
        "code_overlap",
    ]
    # PH-281: `relation` field dropped (only tag_link carried it). Per-edge
    # human-labelable context for EVERY edge type.
    context: str | None = None
    # PH-288 (ADDITIVE, optional — backward-compatible): every weighted edge
    # carries `strength` (the dominant signal's score contribution for THIS pair)
    # + `reason` (the canonical signal type, mirrors PH-287's RelatedReason.type).
    # Old consumers ignore them; `/space` styles/sizes edges off `strength`.
    strength: float | None = None
    reason: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# ---------------------------------------------------------------------------
# Cross-board search (PH-275, epic PH-271 child 4/7)
# ---------------------------------------------------------------------------
#
# Result contract consumed by PH-278/PH-280 (frontend search UI). GROUPED by
# TYPE — ticket hits and LABEL hits live in separate lists, never a mixed array.
# The ticket-hit shape MIRRORS the PH-274 GraphNode ticket fields
# (id/key/title/board/board_id/state) so search hits and graph nodes render with
# one component. Identity-only — NO description snippet in v1.
#
# PH-281 PIVOT: `concept_tags: list[ConceptTagSummary]` → `labels: list[str]`
# (distinct matching label STRINGS from the inline `Ticket.labels` ARRAY). The
# frontend hashes the string for a chip color. BREAKING vs PH-275 — backward-
# compat NOT required (PH-280 lockstep).


class TicketSearchHit(BaseModel):
    id: UUID
    key: str
    title: str
    board: str  # board KEY (e.g. "PH") — mirrors GraphNode.board
    board_id: UUID
    state: str


class SearchResponse(BaseModel):
    tickets: list[TicketSearchHit]
    labels: list[str]  # PH-281: distinct matching label strings (was concept_tags)


# ---------------------------------------------------------------------------
# Relationship scoring (PH-284, epic PH-283 child A — FOUNDATION)
# ---------------------------------------------------------------------------
#
# Output contract for the `related_tickets` MCP tool + the reusable
# `services.relationships.related_tickets()` seam consumed by PH-286
# (`recall_context`). A RelatedTicket carries the related ticket identity
# (key/title/board/state) + a deterministic relevance `score` + an explainable
# `reasons` array. Each reason names WHICH labels / WHICH reference direction /
# WHICH epic, so the score is reconstructable from reasons.


class RelatedReason(BaseModel):
    # PH-287: `dependency` (explicit Depends on:/blocked by/blocks) + `code_overlap`
    # (shared touched files via git cache) are ADDITIVE Literal members — existing
    # `shared_label`/`reference`/`epic` consumers (PH-286 recall_context) are
    # unchanged; new members are tolerated as an additive enum widening.
    type: Literal["shared_label", "reference", "epic", "dependency", "code_overlap"]
    detail: str  # human-readable: which labels / direction / epic / dep / files


class RelatedTicket(BaseModel):
    key: str
    title: str
    board: str  # board KEY (e.g. "PH") — mirrors GraphNode.board / TicketSearchHit.board
    state: str
    # PH-287: weighted multi-signal score, reconstructable from `reasons`:
    #   8*dependency + 5*reference + 3*epic
    #   + min(0.6*Σ label_idf, 8) + min(0.5*Σ file_idf, 6)
    # (hub labels n>=15 contribute 0). Higher = more related.
    score: float
    reasons: list[RelatedReason]


# ---------------------------------------------------------------------------
# History-aware context recall (PH-286, epic PH-283 child C — the FINAL tool)
# ---------------------------------------------------------------------------
#
# Output contract for the `recall_context` MCP tool + the reusable
# `services.recall.recall_context()` seam. A RecalledTicket is a RelatedTicket
# (key/title/board/state/score/reasons — REUSING RelatedReason) PLUS a compact
# CONTEXT block: the `[HANDOFF...]` decision trail + a capped technical_depth /
# impact_analysis / description excerpt, so an agent recalls not just WHICH past
# tickets are relevant but WHAT was decided on them.


class RecallContextBlock(BaseModel):
    handoffs: list[str]  # capped [HANDOFF...] comment lines, newest first
    summary: str  # capped technical_depth/impact_analysis/description excerpt
    # Which field the summary came from (or "none" when all are empty).
    summary_source: Literal[
        "technical_depth", "impact_analysis", "description", "none"
    ]


class RecalledTicket(BaseModel):
    key: str
    title: str
    board: str  # board KEY, mirrors RelatedTicket.board
    state: str
    # From related_tickets (0.0 for pure search-hit fallbacks on the topic path).
    score: float
    reasons: list[RelatedReason]  # REUSED schema (empty for pure search-hit fallbacks)
    context: RecallContextBlock


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
    files_touched_globs: list[str] | None
    blocked_by: list[str] | None
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


class AttachmentResponse(BaseModel):
    """Evidence-attachment metadata (PH-296). Never carries the blob bytes — the
    content is served separately via GET .../attachments/{id}/content."""

    id: UUID
    ticket_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    kind: str
    source: str
    run_id: str | None
    phase: str | None
    author: ActorSummary
    created_at: datetime


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
