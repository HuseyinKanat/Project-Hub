import { getStoredToken, useAuth } from "@/stores/auth";
import type {
  ActorListResponse,
  ApiError,
  BoardListResponse,
  BoardResponse,
  CommentResponse,
  FieldGates,
  HistoryEntry,
  MeResponse,
  MembershipListResponse,
  MembershipResponse,
  NotificationListResponse,
  NotificationResponse,
  SonarIssuesResponse,
  SonarIssueType,
  SonarSetupRequest,
  SonarSetupStatus,
  TicketCreatePayload,
  TicketListResponse,
  TicketResponse,
  TicketUpdatePayload,
  WorkflowResponse,
} from "@/types/api";
import type {
  CommitDiff,
  DetectedReposResponse,
  GitBranchesListResponse,
  GitCommitDetail,
  GitCommitsListResponse,
  GitGraph,
  GitRefreshResponse,
  GitStatus,
  RangeDiff,
  RepositoryCreatePayload,
  RepositoryListResponse,
  RepositoryResponse,
  RepositoryUpsertPayload,
  RotateRefreshSecretResponse,
  TicketCommitsResponse,
} from "@/types/git";

const BASE = "/api";

// ---------------------------------------------------------------------------
// MCP helper — wraps POST /mcp/call/{toolName}
// Response shape from backend: ToolCallResponse { tool: string; result: T }
// ---------------------------------------------------------------------------
export async function mcpCall<T = unknown>(
  toolName: string,
  payload: Record<string, unknown>,
): Promise<T> {
  const token = getStoredToken();
  const res = await fetch(`/mcp/call/${toolName}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (res.status === 401) {
    useAuth.getState().logout();
  }
  if (!res.ok) {
    let body: ApiError | null = null;
    try {
      body = (await res.json()) as ApiError;
    } catch {
      body = null;
    }
    const message =
      body?.message ?? body?.error ?? body?.detail ?? `HTTP ${res.status}`;
    throw new ApiRequestError(res.status, body, message);
  }
  const json = await res.json();
  // ToolCallResponse envelope: { tool: string; result: T }
  return (json.result ?? json) as T;
}

export class ApiRequestError extends Error {
  status: number;
  body: ApiError | null;
  constructor(status: number, body: ApiError | null, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });

  if (res.status === 401) {
    useAuth.getState().logout();
  }

  if (!res.ok) {
    let body: ApiError | null = null;
    try {
      body = (await res.json()) as ApiError;
    } catch {
      body = null;
    }
    const message =
      body?.message ?? body?.error ?? body?.detail ?? `HTTP ${res.status}`;
    throw new ApiRequestError(res.status, body, message);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function jsonBody(body: unknown): RequestInit {
  return { body: JSON.stringify(body) };
}

export const api = {
  listBoards: () => request<BoardListResponse>("/boards"),
  getBoard: (id: string) => request<BoardResponse>(`/boards/${id}`),
  // PH-230: `repos_path` is the editable HOST filesystem root (drives git
  // auto-detect + SonarQube key). Empty string clears it; an invalid non-empty
  // path is rejected server-side with 422 (surfaced via ApiRequestError). Callers
  // that edit it must invalidate BOTH ['board', key] AND
  // ['repositories', key, 'detect'] so the detect panel re-scans the new path.
  updateBoard: (
    id: string,
    payload: {
      name?: string;
      description?: string;
      project_type?: string;
      roles?: Record<string, unknown>;
      repos_path?: string | null;
    },
  ) =>
    request<BoardResponse>(`/boards/${id}`, { method: "PATCH", ...jsonBody(payload) }),
  listTickets: (params: { board_id?: string; state?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.board_id) qs.set("board_id", params.board_id);
    if (params.state) qs.set("state", params.state);
    if (params.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    const suffix = q ? `?${q}` : "";
    return request<TicketListResponse>(`/tickets${suffix}`);
  },
  getTicket: (key: string) => request<TicketResponse>(`/tickets/${key}`),
  createTicket: (payload: TicketCreatePayload) =>
    request<TicketResponse>("/tickets", { method: "POST", ...jsonBody(payload) }),
  updateTicket: (key: string, payload: TicketUpdatePayload) =>
    request<TicketResponse>(`/tickets/${key}`, { method: "PATCH", ...jsonBody(payload) }),
  transitionTicket: (key: string, toState: string) =>
    request<TicketResponse>(`/tickets/${key}/transition/${toState}`, { method: "POST" }),
  claimTicket: (key: string) =>
    request<TicketResponse>(`/tickets/${key}/claim`, { method: "POST" }),
  releaseTicket: (key: string) =>
    request<TicketResponse>(`/tickets/${key}/release`, { method: "POST" }),
  assignTicket: (key: string, assigneeId: string | null) =>
    request<TicketResponse>(`/tickets/${key}/assign`, {
      method: "POST",
      ...jsonBody({ assignee_id: assigneeId }),
    }),
  addComment: (key: string, body: string) =>
    request<CommentResponse>(`/tickets/${key}/comments`, {
      method: "POST",
      ...jsonBody({ body }),
    }),
  listComments: (key: string) => request<CommentResponse[]>(`/tickets/${key}/comments`),
  listHistory: (key: string) => request<HistoryEntry[]>(`/tickets/${key}/history`),
  ping: () => request<{ status: string }>("/../health"),
  listNotifications: (params?: { unread_only?: boolean; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.unread_only) qs.set("unread_only", "true");
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    const suffix = q ? `?${q}` : "";
    return request<NotificationListResponse>(`/notifications${suffix}`);
  },
  markNotificationRead: (id: string) =>
    request<NotificationResponse>(`/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () =>
    request<{ marked_read: number }>("/notifications/read-all", { method: "POST" }),

  /**
   * @deprecated These REST endpoints do not exist in the backend (PH-21 era, will 404).
   * Use mcpCall-backed helpers below (listWorkflows, addTransition, etc.) instead.
   */
  addWorkflowState: (boardId: string, state: { name: string; color?: string; is_initial?: boolean; is_terminal?: boolean }) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/states`, { method: "POST", ...jsonBody(state) }),
  /** @deprecated See addWorkflowState note above */
  deleteWorkflowState: (boardId: string, stateName: string) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/states/${stateName}`, { method: "DELETE" }),
  /** @deprecated See addWorkflowState note above */
  updateWorkflowStates: (boardId: string, states: { name: string; color?: string; is_initial?: boolean; is_terminal?: boolean; order?: number }[]) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/states`, { method: "PUT", ...jsonBody({ states }) }),
  /** @deprecated See addWorkflowState note above */
  updateWorkflowTransitions: (boardId: string, transitions: { from: string; to: string; allowed_roles?: string[] }[]) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/transitions`, { method: "PUT", ...jsonBody({ transitions }) }),

  // ---------------------------------------------------------------------------
  // Workflow MCP helpers — all use POST /mcp/call/{tool} (PH-36 backend ready)
  // ---------------------------------------------------------------------------
  listWorkflows: (boardId: string) =>
    mcpCall<WorkflowResponse[]>("list_workflows", { board_id: boardId }),
  createWorkflow: (payload: { name: string; states?: unknown[]; transitions?: unknown[]; is_default?: boolean; board_id?: string }) => {
    const { board_id, ...workflowPayload } = payload;
    return mcpCall<WorkflowResponse>("create_workflow", {
      workflow: workflowPayload,
      ...(board_id !== undefined ? { board_id } : {}),
    });
  },
  updateWorkflow: (workflowId: string, fields: { name?: string; states?: unknown[]; transitions?: unknown[]; is_default?: boolean }, boardId?: string) =>
    mcpCall<WorkflowResponse>("update_workflow", {
      workflow_id: workflowId,
      fields,
      ...(boardId !== undefined ? { board_id: boardId } : {}),
    }),
  addTransition: (workflowId: string, fromState: string, toState: string, allowedRoles?: string[], fieldGates?: FieldGates, boardId?: string) =>
    mcpCall<WorkflowResponse>("add_transition", {
      workflow_id: workflowId,
      from_state: fromState,
      to_state: toState,
      ...(allowedRoles !== undefined ? { allowed_roles: allowedRoles } : {}),
      ...(fieldGates !== undefined ? { field_gates: fieldGates } : {}),
      ...(boardId !== undefined ? { board_id: boardId } : {}),
    }),
  deleteTransition: (workflowId: string, fromState: string, toState: string, boardId?: string) =>
    mcpCall<WorkflowResponse>("delete_transition", {
      workflow_id: workflowId,
      from_state: fromState,
      to_state: toState,
      ...(boardId !== undefined ? { board_id: boardId } : {}),
    }),
  setFieldGates: (workflowId: string, fromState: string, toState: string, fieldGates: FieldGates, boardId?: string) =>
    mcpCall<WorkflowResponse>("set_field_gates", {
      workflow_id: workflowId,
      from_state: fromState,
      to_state: toState,
      field_gates: fieldGates,
      ...(boardId !== undefined ? { board_id: boardId } : {}),
    }),
  activateWorkflow: (boardId: string, workflowId: string) =>
    mcpCall<{ status: string }>("activate_workflow", { board_id: boardId, workflow_id: workflowId }),
  // PH-97: ensure the board has its own private workflow (clone if shared)
  ensureBoardWorkflow: (boardId: string) =>
    mcpCall<{ workflow: WorkflowResponse; cloned: boolean }>("ensure_board_workflow", { board_id: boardId }),
  deactivateWorkflow: (boardId: string) =>
    mcpCall<{ status: string }>("deactivate_workflow", { board_id: boardId }),
  // PH-102: Delete a workflow with backend guard enforcement
  deleteWorkflow: (workflowId: string, boardId?: string) =>
    mcpCall<{ deleted: boolean; id: string }>("delete_workflow", {
      workflow_id: workflowId,
      ...(boardId !== undefined ? { board_id: boardId } : {}),
    }),
  // PH-106: Delete a state with backend guard enforcement (tickets_exist + last_state)
  deleteState: (workflowId: string, stateName: string, boardId?: string) =>
    mcpCall<{ deleted: boolean; state_name: string; removed_transitions: number }>(
      "delete_state",
      {
        workflow_id: workflowId,
        state_name: stateName,
        ...(boardId !== undefined ? { board_id: boardId } : {}),
      },
    ),
  getMe: () => request<MeResponse>("/auth/me"),

  /**
   * SonarQube issue drill-down for one issue type (PH-204 / PH-203 endpoint).
   * Graceful-200: a non-`ok` `status` is NOT an HTTP error — callers read
   * `data.status` for the human reason; the promise only rejects on a real
   * network failure (→ `ApiRequestError`).
   * GET /api/boards/{boardKey}/sonarqube/issues?type=&severity=&page=&page_size=
   * @see backend/app/api/boards.py (PH-203) → SonarIssuesResponse
   */
  getSonarIssues: (
    boardKey: string,
    params: {
      type: SonarIssueType;
      severity?: string;
      page?: number;
      page_size?: number;
    },
  ): Promise<SonarIssuesResponse> => {
    const qs = new URLSearchParams();
    qs.set("type", params.type);
    if (params.severity) qs.set("severity", params.severity);
    if (params.page) qs.set("page", String(params.page));
    if (params.page_size) qs.set("page_size", String(params.page_size));
    return request<SonarIssuesResponse>(
      `/boards/${boardKey}/sonarqube/issues?${qs.toString()}`,
    );
  },

  // PH-39: Board membership management REST endpoints
  listBoardMembers: (boardId: string) =>
    request<MembershipListResponse>(`/boards/${boardId}/members`),
  addBoardMember: (boardId: string, actorId: string, role: string) =>
    request<MembershipResponse>(`/boards/${boardId}/members`, {
      method: "POST",
      ...jsonBody({ actor_id: actorId, role }),
    }),
  updateBoardMember: (boardId: string, actorId: string, role: string) =>
    request<MembershipResponse>(`/boards/${boardId}/members/${actorId}`, {
      method: "PATCH",
      ...jsonBody({ role }),
    }),
  removeBoardMember: (boardId: string, actorId: string) =>
    request<void>(`/boards/${boardId}/members/${actorId}`, { method: "DELETE" }),
  listActors: () => request<ActorListResponse>("/actors"),
  deleteTicket: (ticketKey: string, reason: string) =>
    request<void>(`/tickets/${ticketKey}`, { method: "DELETE", ...jsonBody({ reason }) }),

  // ---------------------------------------------------------------------------
  // PH-157: G8 — git API client (repository admin — top-level, mirrors api.updateBoard level)
  // ---------------------------------------------------------------------------

  /**
   * Connect or update a git repository for a board (admin only).
   * PUT /api/boards/{boardKey}/repository → RepositoryResponse
   * @see backend/app/api/repositories.py api_upsert_repository
   */
  setRepository: (boardKey: string, payload: RepositoryUpsertPayload) =>
    request<RepositoryResponse>(`/boards/${boardKey}/repository`, {
      method: "PUT",
      ...jsonBody(payload),
    }),

  /**
   * Detach (remove) the repository configuration from a board (admin only).
   * DELETE /api/boards/{boardKey}/repository → 204 No Content
   * @see backend/app/api/repositories.py api_detach_repository
   */
  detachRepository: (boardKey: string) =>
    request<void>(`/boards/${boardKey}/repository`, { method: "DELETE" }),

  /**
   * Rotate the board's git refresh secret (admin only).
   * Returns the new plaintext secret ONCE — caller must display and store.
   * POST /api/boards/{boardKey}/repository/rotate-refresh-secret → RotateRefreshSecretResponse
   * @see backend/app/api/repositories.py api_rotate_refresh_secret (G13 PH-162)
   */
  rotateRefreshSecret: (boardKey: string) =>
    request<RotateRefreshSecretResponse>(
      `/boards/${boardKey}/repository/rotate-refresh-secret`,
      { method: "POST" },
    ),

  // ---------------------------------------------------------------------------
  // PH-157: G8 — git.* namespace
  // All methods under api.git.* use the existing request<T> helper (auth + error
  // normalisation via ApiRequestError) except api.git.refresh which opens its own
  // fetch to avoid setting the Bearer header (shared-secret auth only).
  // ---------------------------------------------------------------------------

  git: {
    /**
     * List ALL repositories linked to a board (PH-221 multi-repo).
     * Exactly one entry has `is_primary=true` when the board has ≥1 repo.
     * Used by the branch-view repo switcher (PH-224) to decide visibility
     * (>1 repo) + the default selection (the primary's slug).
     * GET /api/boards/{boardKey}/repositories → RepositoryListResponse
     * @see backend/app/api/repositories.py api_list_repositories
     */
    listRepositories: (boardKey: string): Promise<RepositoryListResponse> =>
      request<RepositoryListResponse>(`/boards/${boardKey}/repositories`),

    /**
     * Add a NEW repository to the board's collection (PH-225 / C5, admin auth).
     * POST /api/boards/{boardKey}/repositories → RepositoryResponse (201).
     * The FIRST repo added is auto-promoted to primary. Distinct from the
     * singular `setRepository` PUT (which upserts the PRIMARY); use THIS for a
     * manual add or a one-click add of a detected candidate.
     * Non-admin → 403 PermissionDenied (caller surfaces 'admin yetkisi gerekli').
     * @see backend/app/api/repositories.py api_add_repository
     */
    addRepository: (
      boardKey: string,
      payload: RepositoryCreatePayload,
    ): Promise<RepositoryResponse> =>
      request<RepositoryResponse>(`/boards/${boardKey}/repositories`, {
        method: "POST",
        ...jsonBody(payload),
      }),

    /**
     * Remove a repository from the board's collection (PH-225 / C5, admin auth).
     * DELETE /api/boards/{boardKey}/repositories/{selector} → 204 No Content.
     * `selector` is the repo SLUG (URL-safe, human-stable). Removing the primary
     * auto-promotes the oldest remaining repo server-side → caller must REFETCH
     * (the badge moves; the client cannot predict the promotion).
     * Non-admin → 403.
     * @see backend/app/api/repositories.py api_remove_repository
     */
    removeRepository: (boardKey: string, selector: string): Promise<void> =>
      request<void>(`/boards/${boardKey}/repositories/${selector}`, {
        method: "DELETE",
      }),

    /**
     * Mark a repository primary (PH-225 / C5, admin auth).
     * POST /api/boards/{boardKey}/repositories/{selector}/set-primary → 200.
     * The previously-primary repo loses its flag server-side → caller REFETCHES.
     * Non-admin → 403.
     * @see backend/app/api/repositories.py api_set_primary_repository
     */
    setPrimaryRepository: (
      boardKey: string,
      selector: string,
    ): Promise<RepositoryResponse> =>
      request<RepositoryResponse>(
        `/boards/${boardKey}/repositories/${selector}/set-primary`,
        { method: "POST" },
      ),

    /**
     * Auto-detect git working copies under the scan root (PH-225 / C5, member auth).
     * GET /api/boards/{boardKey}/repositories/detect → DetectedReposResponse.
     * Member auth → the dev (frontend_dev) role CAN read it. Never 500s: returns
     * `{repositories: []}` when the scan root is missing/empty or git is unavailable.
     * Each `DetectedRepo` maps 1:1 onto `RepositoryCreatePayload` for one-click add.
     * @see backend/app/api/repositories.py api_detect_repositories
     */
    detectRepositories: (boardKey: string): Promise<DetectedReposResponse> =>
      request<DetectedReposResponse>(
        `/boards/${boardKey}/repositories/detect`,
      ),

    /**
     * DAG payload for the commit graph renderer.
     * GET /api/boards/{boardKey}/git/graph?limit=&branches=<csv>&repo=<slug>
     *
     * `params.branches` string[] is joined to CSV because the backend
     * expects a `branches=main,feature` query parameter.
     * `params.repo` (PH-224) selects which of a multi-repo board's repos to
     * read; omitted → backend resolves the primary (byte-identical single-repo).
     * @see backend/app/api/repositories.py api_git_graph
     */
    getGraph: (
      boardKey: string,
      params?: { limit?: number; branches?: string[]; repo?: string },
    ): Promise<GitGraph> => {
      const qs = new URLSearchParams();
      if (params?.limit !== undefined) qs.set("limit", String(params.limit));
      if (params?.branches && params.branches.length > 0) {
        qs.set("branches", params.branches.join(","));
      }
      if (params?.repo) qs.set("repo", params.repo);
      const q = qs.toString();
      const suffix = q ? `?${q}` : "";
      return request<GitGraph>(`/boards/${boardKey}/git/graph${suffix}`);
    },

    /**
     * Branch list with ahead/behind counts against the default branch.
     * `ahead`/`behind` are null when BFS exceeds git_backfill_limit (deep divergence).
     * `params.repo` (PH-224) selects a non-primary repo; omitted → primary.
     * GET /api/boards/{boardKey}/git/branches?repo=<slug>
     * @see backend/app/api/repositories.py api_git_branches
     */
    getBranches: (
      boardKey: string,
      params?: { repo?: string },
    ): Promise<GitBranchesListResponse> => {
      const qs = new URLSearchParams();
      if (params?.repo) qs.set("repo", params.repo);
      const q = qs.toString();
      const suffix = q ? `?${q}` : "";
      return request<GitBranchesListResponse>(
        `/boards/${boardKey}/git/branches${suffix}`,
      );
    },

    /**
     * Paginated commit log (newest-first).
     * Use `params.before=<sha>` as a cursor for the next page.
     * `params.repo` (PH-224) selects a non-primary repo; omitted → primary.
     * GET /api/boards/{boardKey}/git/commits?branch=&path=&limit=&before=<sha>&repo=<slug>
     * @see backend/app/api/repositories.py api_git_commits
     */
    listCommits: (
      boardKey: string,
      params?: {
        branch?: string;
        path?: string;
        limit?: number;
        before?: string;
        repo?: string;
      },
    ): Promise<GitCommitsListResponse> => {
      const qs = new URLSearchParams();
      if (params?.branch) qs.set("branch", params.branch);
      if (params?.path) qs.set("path", params.path);
      if (params?.limit !== undefined) qs.set("limit", String(params.limit));
      if (params?.before) qs.set("before", params.before);
      if (params?.repo) qs.set("repo", params.repo);
      const q = qs.toString();
      const suffix = q ? `?${q}` : "";
      return request<GitCommitsListResponse>(`/boards/${boardKey}/git/commits${suffix}`);
    },

    /**
     * Full commit detail including per-file numstat.
     * `sha` may be full 40-hex or a short unambiguous prefix (≥7 chars).
     * `params.repo` (PH-224) selects a non-primary repo; omitted → primary.
     * GET /api/boards/{boardKey}/git/commits/{sha}?repo=<slug>
     * @see backend/app/api/repositories.py api_git_commit_detail
     */
    getCommit: (
      boardKey: string,
      sha: string,
      params?: { repo?: string },
    ): Promise<GitCommitDetail> => {
      const qs = new URLSearchParams();
      if (params?.repo) qs.set("repo", params.repo);
      const q = qs.toString();
      const suffix = q ? `?${q}` : "";
      return request<GitCommitDetail>(
        `/boards/${boardKey}/git/commits/${sha}${suffix}`,
      );
    },

    /**
     * Unified diff of one commit versus its first parent.
     * Binary files appear with `is_binary=true` and `patch=null`.
     * `params.repo` (PH-224) selects a non-primary repo; omitted → primary.
     * GET /api/boards/{boardKey}/git/commits/{sha}/diff?path=&context=&repo=<slug>
     * @see backend/app/api/repositories.py api_git_commit_diff
     */
    getCommitDiff: (
      boardKey: string,
      sha: string,
      params?: { path?: string; context?: number; repo?: string },
    ): Promise<CommitDiff> => {
      const qs = new URLSearchParams();
      if (params?.path) qs.set("path", params.path);
      if (params?.context !== undefined) qs.set("context", String(params.context));
      if (params?.repo) qs.set("repo", params.repo);
      const q = qs.toString();
      const suffix = q ? `?${q}` : "";
      return request<CommitDiff>(`/boards/${boardKey}/git/commits/${sha}/diff${suffix}`);
    },

    /**
     * Three-dot merge-base range diff (base...head).
     * Matches GitHub/GitLab PR-diff semantics.
     * `params.repo` (PH-224) selects a non-primary repo; omitted → primary.
     * GET /api/boards/{boardKey}/git/diff?base=&head=&path=&context=&repo=<slug>
     * @see backend/app/api/repositories.py api_git_range_diff
     */
    getRangeDiff: (
      boardKey: string,
      params: {
        base: string;
        head: string;
        path?: string;
        context?: number;
        repo?: string;
      },
    ): Promise<RangeDiff> => {
      const qs = new URLSearchParams();
      qs.set("base", params.base);
      qs.set("head", params.head);
      if (params.path) qs.set("path", params.path);
      if (params.context !== undefined) qs.set("context", String(params.context));
      if (params.repo) qs.set("repo", params.repo);
      return request<RangeDiff>(`/boards/${boardKey}/git/diff?${qs.toString()}`);
    },

    /**
     * Git connection status for a board.
     * `params.repo` (PH-224) reports the SELECTED repo's connection state;
     * omitted → primary.
     * GET /api/boards/{boardKey}/git/status?repo=<slug>
     * @see backend/app/api/repositories.py api_git_status
     */
    getStatus: (
      boardKey: string,
      params?: { repo?: string },
    ): Promise<GitStatus> => {
      const qs = new URLSearchParams();
      if (params?.repo) qs.set("repo", params.repo);
      const q = qs.toString();
      const suffix = q ? `?${q}` : "";
      return request<GitStatus>(`/boards/${boardKey}/git/status${suffix}`);
    },

    /**
     * Trigger a live sync for this board's repository.
     *
     * G8 (original): auth via X-Git-Refresh-Token shared secret — NOT Bearer token.
     * G13 hybrid (PH-162): if `opts.useBearer` is true, sends Authorization: Bearer
     * instead of X-Git-Refresh-Token (admin alt-auth path). When omitted, defaults
     * to bearer-auth path for UI-triggered refreshes (AC-F5).
     *
     * POST /api/boards/{boardKey}/git/refresh
     * Possible responses: 202 {queued|coalesced|disabled}, 401, 403, 503, 409.
     * @see backend/app/api/repositories.py api_git_refresh
     */
    refresh: async (
      boardKey: string,
      opts?: { refreshToken?: string; useBearer?: boolean },
    ): Promise<GitRefreshResponse> => {
      const token = getStoredToken();
      const useBearer = opts?.useBearer ?? !opts?.refreshToken;
      const headers: Record<string, string> = {
        Accept: "application/json",
      };
      if (useBearer && token) {
        headers["Authorization"] = `Bearer ${token}`;
      } else if (opts?.refreshToken) {
        headers["X-Git-Refresh-Token"] = opts.refreshToken;
      }
      const res = await fetch(`${BASE}/boards/${boardKey}/git/refresh`, {
        method: "POST",
        headers,
      });
      if (!res.ok) {
        let body: ApiError | null = null;
        try {
          body = (await res.json()) as ApiError;
        } catch {
          body = null;
        }
        const message =
          body?.message ?? body?.error ?? body?.detail ?? `HTTP ${res.status}`;
        throw new ApiRequestError(res.status, body, message);
      }
      return (await res.json()) as GitRefreshResponse;
    },

    /**
     * Commits linked to a specific ticket (cache-only, no patch text).
     * GET /api/tickets/{ticketKey}/commits
     * @see backend/app/api/tickets.py api_ticket_commits
     */
    getTicketCommits: (ticketKey: string): Promise<TicketCommitsResponse> =>
      request<TicketCommitsResponse>(`/tickets/${ticketKey}/commits`),
  },

  // ---------------------------------------------------------------------------
  // PH-226: C6 — SonarQube board-settings setup/sync/status namespace.
  // Mirrors the api.git.* nested convention (PH-224/225). All three use the
  // shared request<T> helper (auth + ApiRequestError normalisation) — do NOT
  // hand-roll fetch. status is member-level (no 403); setup/sync are admin-only
  // → a 403 surfaces as ApiRequestError(status=403) for the inline "Admin role
  // required" branch in BoardSettings. All return SonarSetupStatus 200 on the
  // happy path (never-500/never-hang backend contract, PH-223).
  // @see backend/app/api/boards.py (setup/sync/status), components/sonarqube.md
  // ---------------------------------------------------------------------------
  sonarqube: {
    /**
     * Read the board's SonarQube linkage view (member auth — no 403).
     * GET /api/boards/{boardKey}/sonarqube/status → SonarSetupStatus.
     */
    getStatus: (boardKey: string): Promise<SonarSetupStatus> =>
      request<SonarSetupStatus>(`/boards/${boardKey}/sonarqube/status`),

    /**
     * One-click setup (admin auth). Empty body → backend derives the default key
     * (PH → `project-hub`); a supplied `project_key` overrides it. Idempotent.
     * POST /api/boards/{boardKey}/sonarqube/setup → SonarSetupStatus.
     * Non-admin → ApiRequestError(status=403).
     */
    setup: (
      boardKey: string,
      body: SonarSetupRequest = {},
    ): Promise<SonarSetupStatus> =>
      request<SonarSetupStatus>(`/boards/${boardKey}/sonarqube/setup`, {
        method: "POST",
        ...jsonBody(body),
      }),

    /**
     * Re-poll cached metrics from SonarQube (admin auth). Degrades gracefully —
     * the returned status' message reflects unreachable rather than throwing.
     * POST /api/boards/{boardKey}/sonarqube/sync → SonarSetupStatus.
     * Non-admin → ApiRequestError(status=403).
     */
    sync: (boardKey: string): Promise<SonarSetupStatus> =>
      request<SonarSetupStatus>(`/boards/${boardKey}/sonarqube/sync`, {
        method: "POST",
      }),
  },
};

export async function verifyToken(token: string): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/boards`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return res.ok;
  } catch {
    return false;
  }
}
