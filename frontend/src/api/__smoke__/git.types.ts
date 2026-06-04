/**
 * G8 smoke test — PH-157
 *
 * Type-only module: imports all new api.git.* methods and git types to verify
 * type compatibility at tsc compile time. No runtime code — never executed.
 *
 * eslint-disable used below because these are type-only re-exports that
 * reference every symbol to prove they compile; they are intentionally unused
 * at runtime.
 */

/* eslint-disable @typescript-eslint/no-unused-vars */

import { api } from "@/api/client";
import type {
  CommitDiff,
  DiffResponse,
  FileDiff,
  GitBranch,
  GitBranchEntry,
  GitBranchesListResponse,
  GitChangeType,
  GitCommitDetail,
  GitCommitFile,
  GitCommitFileEntry,
  GitCommitsListResponse,
  GitCommitSummary,
  GitGraph,
  GitGraphResponse,
  GitProvider,
  GitRefreshResponse,
  GitRefreshStatus,
  GitStatus,
  GitStatusResponse,
  GitSyncedPayload,
  RangeDiff,
  RangeDiffResponse,
  RepositoryResponse,
  RepositorySummary,
  RepositoryUpsertPayload,
  TicketCommitEntry,
  TicketCommitsResponse,
} from "@/types/git";
import { isGitSyncedMessage } from "@/hooks/useWebSocket";

// ---------------------------------------------------------------------------
// Literal union smoke
// ---------------------------------------------------------------------------

type _GitProviderCheck = GitProvider; // "github" | "gitlab" | "local"
type _GitChangeTypeCheck = GitChangeType; // "A" | "M" | "D" | "R" | "C"
type _GitRefreshStatusCheck = GitRefreshStatus; // "queued" | "coalesced" | "disabled"

// ---------------------------------------------------------------------------
// api.git.* call-site signatures (type-level, never actually called)
// ---------------------------------------------------------------------------

// getGraph — both overloads
type _GetGraphFull = typeof api.git.getGraph;
const _getGraphCall: ReturnType<_GetGraphFull> = api.git.getGraph("PH", { limit: 200, branches: ["main"] });
const _getGraphBare: ReturnType<_GetGraphFull> = api.git.getGraph("PH");

// getBranches
const _getBranches: Promise<GitBranchesListResponse> = api.git.getBranches("PH");

// listCommits — both overloads
const _listCommitsFull: Promise<GitCommitsListResponse> = api.git.listCommits("PH", {
  branch: "main",
  path: "frontend/src",
  limit: 50,
  before: "abc1234",
});
const _listCommitsBare: Promise<GitCommitsListResponse> = api.git.listCommits("PH");

// getCommit
const _getCommit: Promise<GitCommitDetail> = api.git.getCommit("PH", "abc1234abcd1234a");

// getCommitDiff
const _getCommitDiff: Promise<CommitDiff> = api.git.getCommitDiff("PH", "abc1234abcd1234a", {
  path: "frontend/src/api/client.ts",
  context: 3,
});
const _getCommitDiffBare: Promise<CommitDiff> = api.git.getCommitDiff("PH", "abc1234abcd1234a");

// getRangeDiff
const _getRangeDiff: Promise<RangeDiff> = api.git.getRangeDiff("PH", {
  base: "main",
  head: "ph-157-g8-frontend-git-api-client-tipler",
  context: 5,
});

// getStatus
const _getStatus: Promise<GitStatus> = api.git.getStatus("PH");

// refresh — G13 hybrid: omit opts for bearer (admin UI), or pass refreshToken for shared-secret hooks
const _refresh: Promise<GitRefreshResponse> = api.git.refresh("PH", { useBearer: true });
const _refreshSecret: Promise<GitRefreshResponse> = api.git.refresh("PH", { refreshToken: "secret-token-here" });

// getTicketCommits
const _getTicketCommits: Promise<TicketCommitsResponse> = api.git.getTicketCommits("PH-157");

// ---------------------------------------------------------------------------
// admin top-level
// ---------------------------------------------------------------------------

const _setRepo: Promise<RepositoryResponse> = api.setRepository("PH", {
  local_path: "/repos/project-hub",
  provider: "local",
  default_branch: "main",
});

const _detachRepo: Promise<void> = api.detachRepository("PH");

// ---------------------------------------------------------------------------
// Type shape verification — assign literal-compatible objects to interfaces
// ---------------------------------------------------------------------------

const _repoSummary: RepositorySummary = {
  id: "uuid",
  provider: "local",
  remote_url: null,
  default_branch: "main",
  local_path: "/repos/project-hub",
  last_synced_sha: "abc1234",
  last_synced_at: "2026-06-04T20:00:00Z",
};

const _repoResponse: RepositoryResponse = {
  ..._repoSummary,
  board_id: "uuid-board",
  created_at: "2026-06-04T20:00:00Z",
  updated_at: "2026-06-04T20:00:00Z",
};

const _repoUpsert: RepositoryUpsertPayload = {
  local_path: "/repos/project-hub",
};

const _branchEntry: GitBranchEntry = {
  name: "main",
  head_sha: "abc1234",
  is_default: true,
  ticket_key: null,
  ahead: 0,
  behind: 0,
};
const _branchAlias: GitBranch = _branchEntry; // alias check

const _commitSummary: GitCommitSummary = {
  sha: "abc1234abcd1234aabc1234abcd1234aabc12340",
  short_sha: "abc1234",
  parents: [],
  author_name: "Dev",
  author_email: "dev@example.com",
  authored_at: "2026-06-04T20:00:00Z",
  committed_at: "2026-06-04T20:00:00Z",
  summary: "feat(PH-157): add git api client + types",
  is_conventional: true,
  commit_type: "feat",
  ticket_keys: ["PH-157"],
  refs: ["main"],
};

const _commitFile: GitCommitFile = {
  path: "frontend/src/types/git.ts",
  old_path: null,
  change_type: "A",
  additions: 150,
  deletions: 0,
  is_binary: false,
};
const _commitFileAlias: GitCommitFileEntry = _commitFile; // alias check

const _commitDetail: GitCommitDetail = {
  ..._commitSummary,
  committer_name: "Dev",
  committer_email: "dev@example.com",
  body: "",
  files: [_commitFile],
};

const _fileDiff: FileDiff = {
  path: "frontend/src/types/git.ts",
  old_path: null,
  change_type: "A",
  additions: 150,
  deletions: 0,
  is_binary: false,
  patch: "@@ -0,0 +1 @@\n+export interface GitCommitSummary {",
  truncated: false,
};

const _diffResponse: DiffResponse = {
  sha: "abc1234abcd1234aabc1234abcd1234aabc12340",
  files: [_fileDiff],
  truncated: false,
};
const _commitDiffAlias: CommitDiff = _diffResponse; // alias check

const _rangeDiffResponse: RangeDiffResponse = {
  base: "main",
  head: "ph-157-g8-frontend-git-api-client-tipler",
  files: [],
  truncated: false,
};
const _rangeDiffAlias: RangeDiff = _rangeDiffResponse; // alias check

const _graph: GitGraphResponse = {
  commits: [_commitSummary],
  branches: [_branchEntry],
  tags: [],
};
const _graphAlias: GitGraph = _graph; // alias check

const _gitStatus: GitStatusResponse = {
  connected: true,
  repository: _repoSummary,
  last_synced_at: "2026-06-04T20:00:00Z",
};
const _gitStatusAlias: GitStatus = _gitStatus; // alias check

const _refreshResponse: GitRefreshResponse = {
  ok: true,
  status: "queued",
  last_sync_at: null,
};

const _ticketCommitEntry: TicketCommitEntry = {
  sha: "abc1234abcd1234aabc1234abcd1234aabc12340",
  short_sha: "abc1234",
  summary: "feat(PH-157): add git api client + types",
  authored_at: "2026-06-04T20:00:00Z",
  committed_at: "2026-06-04T20:00:00Z",
  author_name: "Dev",
  additions: 150,
  deletions: 0,
  files_changed: 3,
};

const _ticketCommitsResponse: TicketCommitsResponse = {
  branch_name: "ph-157-g8-frontend-git-api-client-tipler",
  commits: [_ticketCommitEntry],
};

const _gitSyncedPayload: GitSyncedPayload = {
  new_commit_shas: ["abc1234abcd1234aabc1234abcd1234aabc12340"],
  updated_branches: ["main"],
  deleted_branches: [],
  commits_count: 1,
  last_synced_sha: "abc1234abcd1234aabc1234abcd1234aabc12340",
  last_synced_at: "2026-06-04T20:00:00Z",
};

// ---------------------------------------------------------------------------
// isGitSyncedMessage type narrowing smoke
// ---------------------------------------------------------------------------

// Verify the narrowing function compiles correctly
type _NarrowingFnType = typeof isGitSyncedMessage;
// The function should accept WebSocketMessage and return the narrowed type predicate
const _narrowingWorks: _NarrowingFnType = isGitSyncedMessage;

// ---------------------------------------------------------------------------
// Suppress "declared but never read" for Promise variables (type-check only)
// ---------------------------------------------------------------------------

void _getGraphCall;
void _getGraphBare;
void _getBranches;
void _listCommitsFull;
void _listCommitsBare;
void _getCommit;
void _getCommitDiff;
void _getCommitDiffBare;
void _getRangeDiff;
void _getStatus;
void _refresh;
void _getTicketCommits;
void _setRepo;
void _detachRepo;
void _repoResponse;
void _repoUpsert;
void _branchAlias;
void _commitFileAlias;
void _commitDiffAlias;
void _rangeDiffAlias;
void _graphAlias;
void _gitStatusAlias;
void _refreshResponse;
void _ticketCommitsResponse;
void _gitSyncedPayload;
void _narrowingWorks;
