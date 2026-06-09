/**
 * Git integration types — G8 (PH-157)
 *
 * Hand-written to match backend/app/schemas.py exactly.
 * Every interface carries a @see JSDoc referencing the corresponding Pydantic class.
 *
 * Conventions (same as src/types/api.ts):
 *   - ISO datetime → string  (Pydantic serialises datetime to JSON string)
 *   - Optional/nullable fields → T | null  (NOT T | undefined)
 *   - Literal unions expressed as TypeScript string literal unions
 *
 * Architecture note: openapi-typescript devDep is installed but no regen script exists.
 * Hand-written types chosen for G8 to match the existing api.ts convention and minimise
 * scope. openapi-typescript regen pipeline deferred to a follow-up ticket post-G14.
 * @see docs/codewiki/components/frontend.md Design decisions
 */

// ---------------------------------------------------------------------------
// Primitive literal unions
// @see backend/app/schemas.py Provider (line 13)
// ---------------------------------------------------------------------------

/** Git repository provider. */
export type GitProvider = "github" | "gitlab" | "local";

/**
 * Single-letter git change codes (same as git's --diff-filter values).
 * @see backend/app/schemas.py GitCommitFileEntry (line 92)
 */
export type GitChangeType = "A" | "M" | "D" | "R" | "C";

/**
 * Status returned by POST /git/refresh.
 * @see backend/app/schemas.py GitRefreshResponse (line 488)
 */
export type GitRefreshStatus = "queued" | "coalesced" | "disabled";

// ---------------------------------------------------------------------------
// Repository config
// @see backend/app/schemas.py RepositorySummary (line 37)
// @see backend/app/schemas.py RepositoryResponse (line 49)
// @see backend/app/schemas.py RepositoryUpsert (line 16)
// ---------------------------------------------------------------------------

/**
 * Compact repository view embedded in BoardResponse.
 *
 * PH-221 (multi-repo) added the `slug` / `name` / `is_primary` identity fields:
 * a board may now own 1:N repos; exactly one carries `is_primary=true`. The
 * `slug` is the stable, URL-safe selector threaded through `?repo=` + every
 * `api.git.*` `repo` param (PH-224 branch-view repo switcher).
 * @see backend/app/schemas.py RepositorySummary (line 72)
 */
export interface RepositorySummary {
  id: string;
  slug: string; // PH-221 — stable URL-safe selector (deduped within a board)
  name: string; // PH-221 — human label (derived from local_path basename when omitted)
  is_primary: boolean; // PH-221 — exactly one repo per board is primary
  provider: string; // GitProvider — kept as string to match backend loose typing
  remote_url: string | null;
  default_branch: string;
  local_path: string;
  last_synced_sha: string | null;
  last_synced_at: string | null; // ISO datetime
}

/**
 * Full repository response (PUT /api/boards/{key}/repository + the repo collection).
 * @see backend/app/schemas.py RepositoryResponse (line 88)
 */
export interface RepositoryResponse extends RepositorySummary {
  board_id: string;
  created_at: string; // ISO datetime
  updated_at: string; // ISO datetime
}

/**
 * Ergonomic alias for switcher prop typing (PH-224).
 * A repo as returned by `GET /api/boards/{key}/repositories`.
 */
export type Repository = RepositoryResponse;

/**
 * Response for GET /api/boards/{key}/repositories (PH-221 list route).
 * Exactly one entry has `is_primary=true` when the board has ≥1 repo.
 * @see backend/app/schemas.py RepositoryListResponse (line 96)
 */
export interface RepositoryListResponse {
  repositories: RepositoryResponse[];
}

/**
 * Payload for PUT /api/boards/{key}/repository.
 * @see backend/app/schemas.py RepositoryUpsert (line 16)
 */
export interface RepositoryUpsertPayload {
  provider?: GitProvider;
  remote_url?: string | null;
  default_branch?: string;
  local_path: string; // required; must start with /repos/
}

// ---------------------------------------------------------------------------
// Multi-repo add + auto-detect (C5 — PH-225)
// @see backend/app/schemas.py RepositoryCreate (line 48)
// @see backend/app/schemas.py DetectedRepo (line 110)
// @see backend/app/schemas.py DetectedReposResponse (line 127)
// ---------------------------------------------------------------------------

/**
 * Payload for POST /api/boards/{key}/repositories (add a repo to the board).
 *
 * Extends the singular upsert payload with optional `name` / `slug`. When
 * omitted the service derives them from the `local_path` basename (slug is
 * deduplicated within the board by suffixing -2, -3, …). The FIRST repo added
 * to a board is auto-promoted to primary. This is the COLLECTION-add path
 * (a new row); the singular `RepositoryUpsertPayload` PUTs the primary.
 *
 * A `DetectedRepo` maps 1:1 onto this shape (one-click add — no manual typing):
 *   { provider: d.provider_guess, remote_url: d.remote_url,
 *     default_branch: d.default_branch ?? 'main', local_path: d.local_path, name: d.name }
 * @see backend/app/schemas.py RepositoryCreate (line 48)
 */
export interface RepositoryCreatePayload {
  provider?: GitProvider;
  remote_url?: string | null;
  default_branch?: string;
  local_path: string; // required; must start with /repos/
  name?: string | null;
  slug?: string | null;
}

/**
 * A git working copy discovered under the scan root by GET /repositories/detect.
 *
 * Fields map 1:1 onto `RepositoryCreatePayload`. `already_linked=true` means the
 * realpath already matches a Repository row on THIS board → the Add panel greys
 * the candidate and disables its Add button ('Zaten ekli'). `is_git` is always
 * true for returned rows. `remote_url` / `default_branch` are null when the repo
 * has no `origin` remote / no detectable default branch (provider then 'local').
 * @see backend/app/schemas.py DetectedRepo (line 110)
 */
export interface DetectedRepo {
  local_path: string; // absolute, e.g. "/repos/project-hub" (== POST local_path)
  name: string; // basename of local_path
  is_git: boolean; // always true for returned candidate rows
  remote_url: string | null; // origin remote URL, or null when no 'origin'
  default_branch: string | null; // null only if undetectable
  provider_guess: GitProvider; // inferred from remote_url host
  already_linked: boolean; // true → already a Repository row on this board
}

/**
 * Response for GET /api/boards/{key}/repositories/detect (PH-222).
 * `repositories` is empty when the scan root is missing/empty, git is
 * unavailable, or every candidate failed to open — never a 500.
 * @see backend/app/schemas.py DetectedReposResponse (line 127)
 */
export interface DetectedReposResponse {
  repositories: DetectedRepo[];
}

// ---------------------------------------------------------------------------
// Git status
// @see backend/app/schemas.py GitStatusResponse (line 57)
// ---------------------------------------------------------------------------

/**
 * Response for GET /api/boards/{key}/git/status.
 * @see backend/app/schemas.py GitStatusResponse (line 57)
 */
export interface GitStatusResponse {
  connected: boolean;
  repository: RepositorySummary | null;
  last_synced_at: string | null; // ISO datetime — convenience alias of repository.last_synced_at
}

/** Alias for ergonomic import in hooks. */
export type GitStatus = GitStatusResponse;

// ---------------------------------------------------------------------------
// Commits
// @see backend/app/schemas.py GitCommitSummary (line 70)
// @see backend/app/schemas.py GitCommitFileEntry (line 92)
// @see backend/app/schemas.py GitCommitDetail (line 107)
// ---------------------------------------------------------------------------

/**
 * Single commit entry used in graph and commits-list responses.
 * `refs` = branch names whose head_sha == this sha (computed server-side).
 * `ticket_keys` = parsed ticket keys from commit message.
 * @see backend/app/schemas.py GitCommitSummary (line 70)
 */
export interface GitCommitSummary {
  sha: string;
  short_sha: string;
  parents: string[];
  author_name: string;
  author_email: string;
  authored_at: string; // ISO datetime
  committed_at: string; // ISO datetime
  summary: string;
  is_conventional: boolean;
  commit_type: string | null;
  ticket_keys: string[];
  refs: string[]; // branch names pointing at this commit
}

/**
 * Per-file change entry inside a commit-detail response.
 * `change_type` is a GitChangeType single letter (A/M/D/R/C).
 * @see backend/app/schemas.py GitCommitFileEntry (line 92)
 */
export interface GitCommitFile {
  path: string;
  old_path: string | null;
  change_type: string; // GitChangeType — kept as string to match backend loose typing
  additions: number;
  deletions: number;
  is_binary: boolean;
}

/** Alias matching AC1 naming requirement. */
export type GitCommitFileEntry = GitCommitFile;

/**
 * Full commit payload including per-file numstat.
 * Extends GitCommitSummary with fields only needed for the detail view.
 * @see backend/app/schemas.py GitCommitDetail (line 107)
 */
export interface GitCommitDetail extends GitCommitSummary {
  committer_name: string;
  committer_email: string;
  body: string;
  files: GitCommitFile[];
}

// ---------------------------------------------------------------------------
// Branches
// @see backend/app/schemas.py GitBranchEntry (line 120)
// ---------------------------------------------------------------------------

/**
 * Single branch entry from GET /git/branches.
 *
 * `ahead` / `behind` are relative to the repo's default branch.
 * Both are `null` when BFS walk exceeds `git_backfill_limit` (deep divergence).
 * G9 UI should render "..." for null values.
 * For the default branch itself both are always 0.
 * @see backend/app/schemas.py GitBranchEntry (line 120)
 */
export interface GitBranchEntry {
  name: string;
  head_sha: string;
  is_default: boolean;
  ticket_key: string | null;
  ahead: number | null; // null = deep divergence (BFS overflow); G9 render "..."
  behind: number | null; // null = deep divergence (BFS overflow); G9 render "..."
}

/** Alias for ergonomic import. */
export type GitBranch = GitBranchEntry;

// ---------------------------------------------------------------------------
// Graph / commits-list / branches-list responses
// @see backend/app/schemas.py GitGraphResponse (line 137)
// @see backend/app/schemas.py GitCommitsListResponse (line 148)
// @see backend/app/schemas.py GitBranchesListResponse (line 154)
// ---------------------------------------------------------------------------

/**
 * Response for GET /git/graph.
 * `tags` is always an empty list in G4 (no cache table yet; G14 follow-up).
 * @see backend/app/schemas.py GitGraphResponse (line 137)
 */
export interface GitGraphResponse {
  commits: GitCommitSummary[];
  branches: GitBranchEntry[];
  /**
   * Tag entries for this graph response.
   * Always an empty array in G4 (no cache table yet).
   * TODO: G15+ — replace with `GitTag[]` interface once backend tag cache is implemented.
   * [PH-163] — widened from `never[]` to `unknown[]` to allow runtime access without tsc error.
   */
  tags: unknown[]; // always [] in G4; G15+ will narrow to real GitTag type
}

/** Alias for ergonomic import. */
export type GitGraph = GitGraphResponse;

/**
 * Response for GET /git/commits (paginated list, newest-first).
 * @see backend/app/schemas.py GitCommitsListResponse (line 148)
 */
export interface GitCommitsListResponse {
  commits: GitCommitSummary[];
}

/**
 * Response for GET /git/branches.
 * @see backend/app/schemas.py GitBranchesListResponse (line 154)
 */
export interface GitBranchesListResponse {
  branches: GitBranchEntry[];
}

// ---------------------------------------------------------------------------
// Diff types
// @see backend/app/schemas.py FileDiff (line 165)
// @see backend/app/schemas.py DiffResponse (line 183)
// @see backend/app/schemas.py RangeDiffResponse (line 191)
// ---------------------------------------------------------------------------

/**
 * Per-file diff entry in a unified diff response.
 *
 * `patch` is null when the file is binary or when the byte-cap has been reached
 * (`truncated=true` on this entry). `old_path` is populated only for renames/copies.
 * @see backend/app/schemas.py FileDiff (line 165)
 */
export interface FileDiff {
  path: string;
  old_path: string | null;
  change_type: string; // GitChangeType — kept as string to match backend loose typing
  additions: number;
  deletions: number;
  is_binary: boolean;
  patch: string | null; // null when binary or omitted after byte-cap
  truncated: boolean; // true when this specific file's patch was sliced
}

/**
 * Response for GET /api/boards/{key}/git/commits/{sha}/diff.
 * @see backend/app/schemas.py DiffResponse (line 183)
 */
export interface DiffResponse {
  sha: string; // full 40-hex sha
  files: FileDiff[];
  truncated: boolean; // top-level cap hit anywhere in this response
}

/** Alias for ergonomic import. */
export type CommitDiff = DiffResponse;

/**
 * Response for GET /api/boards/{key}/git/diff?base=&head=
 * Three-dot merge-base range diff (base...head).
 * @see backend/app/schemas.py RangeDiffResponse (line 191)
 */
export interface RangeDiffResponse {
  base: string; // as supplied by caller (branch name or sha)
  head: string;
  files: FileDiff[];
  truncated: boolean;
}

/** Alias for ergonomic import. */
export type RangeDiff = RangeDiffResponse;

// ---------------------------------------------------------------------------
// Git refresh
// @see backend/app/schemas.py GitRefreshResponse (line 488)
// ---------------------------------------------------------------------------

/**
 * Response for POST /api/boards/{key}/git/refresh (G6).
 *
 * Auth: shared-secret via X-Git-Refresh-Token header — NOT Bearer token.
 * @see backend/app/schemas.py GitRefreshResponse (line 488)
 */
export interface GitRefreshResponse {
  ok: boolean;
  status: GitRefreshStatus;
  last_sync_at: string | null; // ISO datetime — note: field name uses last_sync_at (no 'ed')
}

// ---------------------------------------------------------------------------
// Rotate refresh secret (G13 — PH-162)
// @see backend/app/schemas.py RotateRefreshSecretResponse
// ---------------------------------------------------------------------------

/**
 * Response for POST /api/boards/{key}/repository/rotate-refresh-secret (admin only).
 * One-shot: plaintext secret is returned once and never again.
 * @see backend/app/schemas.py RotateRefreshSecretResponse
 */
export interface RotateRefreshSecretResponse {
  refresh_secret: string;
  hook_install_command: string;
}

// ---------------------------------------------------------------------------
// Ticket commits
// @see backend/app/schemas.py TicketCommitEntry (line 200)
// @see backend/app/schemas.py TicketCommitsResponse (line 214)
// ---------------------------------------------------------------------------

/**
 * Single commit linked to a ticket (cache-only, no patch text).
 *
 * PH-247: a ticket's linked commits aggregate across ALL of a board's repos, so
 * every entry carries its OWN source-repo identity. The FE (PH-249) renders a
 * repo badge per row and threads `repo_slug` into the per-repo commit-detail /
 * diff fetch (`?repo=` selector) so a NON-primary commit's drill-down doesn't
 * resolve against the primary repo and 404. NON-OPTIONAL: a real commit row
 * always has a NOT-NULL `repo_id` and its `Repository.slug` / `Repository.name`
 * are NOT NULL (mirrors backend — a commit has no aggregate case, unlike
 * RepoHealth).
 * @see backend/app/schemas.py TicketCommitEntry (line 280)
 */
export interface TicketCommitEntry {
  sha: string;
  short_sha: string;
  summary: string;
  authored_at: string; // ISO datetime
  committed_at: string; // ISO datetime
  author_name: string;
  additions: number; // sum over git_commit_files
  deletions: number; // sum over git_commit_files
  files_changed: number; // COUNT(*) from git_commit_files
  repo_id: string; // PH-247: source repo (git_commits.repo_id, NOT NULL)
  repo_slug: string; // PH-247: stable per-board repo slug (Repository.slug)
  repo_name: string; // PH-247: repo display name (Repository.name)
}

/**
 * Response for GET /api/tickets/{key}/commits.
 * @see backend/app/schemas.py TicketCommitsResponse (line 214)
 */
export interface TicketCommitsResponse {
  branch_name: string | null;
  commits: TicketCommitEntry[]; // newest-first by committed_at
}

// ---------------------------------------------------------------------------
// WebSocket — git_synced event payload
// @see backend/app/git/sync.py EventEnvelope payload (line 426)
// ---------------------------------------------------------------------------

/**
 * Payload shape inside a WebSocket `git_synced` event.
 *
 * The WS envelope itself is a `WebSocketMessage` from useWebSocket.ts.
 * Use `isGitSyncedMessage()` (exported from useWebSocket.ts) to narrow.
 *
 * Backend emits this on every successful `sync_repo()` run — may contain
 * multiple new commits (G6 debounce coalesces rapid pushes into one envelope).
 * `ticket_id` on the outer envelope is the sentinel `"system"`.
 * @see backend/app/git/sync.py sync_repo EventEnvelope payload (line 426)
 */
export interface GitSyncedPayload {
  new_commit_shas: string[];
  updated_branches: string[];
  deleted_branches: string[];
  commits_count: number;
  last_synced_sha: string | null;
  last_synced_at: string; // ISO datetime string
}
