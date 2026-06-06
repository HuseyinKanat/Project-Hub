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
  TicketCreatePayload,
  TicketListResponse,
  TicketResponse,
  TicketUpdatePayload,
  WorkflowResponse,
} from "@/types/api";
import type {
  CommitDiff,
  GitBranchesListResponse,
  GitCommitDetail,
  GitCommitsListResponse,
  GitGraph,
  GitRefreshResponse,
  GitStatus,
  RangeDiff,
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
  updateBoard: (id: string, payload: { name?: string; description?: string; project_type?: string; roles?: Record<string, unknown> }) =>
    request<BoardResponse>(`/boards/${id}`, { method: "PATCH", ...jsonBody(payload) }),
  listTickets: (params: { board_id?: string; state?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.board_id) qs.set("board_id", params.board_id);
    if (params.state) qs.set("state", params.state);
    if (params.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return request<TicketListResponse>(`/tickets${q ? `?${q}` : ""}`);
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
    return request<NotificationListResponse>(`/notifications${q ? `?${q}` : ""}`);
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
     * DAG payload for the commit graph renderer.
     * GET /api/boards/{boardKey}/git/graph?limit=&branches=<csv>
     *
     * `params.branches` string[] is joined to CSV because the backend
     * expects a `branches=main,feature` query parameter.
     * @see backend/app/api/repositories.py api_git_graph
     */
    getGraph: (
      boardKey: string,
      params?: { limit?: number; branches?: string[] },
    ): Promise<GitGraph> => {
      const qs = new URLSearchParams();
      if (params?.limit !== undefined) qs.set("limit", String(params.limit));
      if (params?.branches && params.branches.length > 0) {
        qs.set("branches", params.branches.join(","));
      }
      const q = qs.toString();
      return request<GitGraph>(`/boards/${boardKey}/git/graph${q ? `?${q}` : ""}`);
    },

    /**
     * Branch list with ahead/behind counts against the default branch.
     * `ahead`/`behind` are null when BFS exceeds git_backfill_limit (deep divergence).
     * GET /api/boards/{boardKey}/git/branches
     * @see backend/app/api/repositories.py api_git_branches
     */
    getBranches: (boardKey: string): Promise<GitBranchesListResponse> =>
      request<GitBranchesListResponse>(`/boards/${boardKey}/git/branches`),

    /**
     * Paginated commit log (newest-first).
     * Use `params.before=<sha>` as a cursor for the next page.
     * GET /api/boards/{boardKey}/git/commits?branch=&path=&limit=&before=<sha>
     * @see backend/app/api/repositories.py api_git_commits
     */
    listCommits: (
      boardKey: string,
      params?: { branch?: string; path?: string; limit?: number; before?: string },
    ): Promise<GitCommitsListResponse> => {
      const qs = new URLSearchParams();
      if (params?.branch) qs.set("branch", params.branch);
      if (params?.path) qs.set("path", params.path);
      if (params?.limit !== undefined) qs.set("limit", String(params.limit));
      if (params?.before) qs.set("before", params.before);
      const q = qs.toString();
      return request<GitCommitsListResponse>(`/boards/${boardKey}/git/commits${q ? `?${q}` : ""}`);
    },

    /**
     * Full commit detail including per-file numstat.
     * `sha` may be full 40-hex or a short unambiguous prefix (≥7 chars).
     * GET /api/boards/{boardKey}/git/commits/{sha}
     * @see backend/app/api/repositories.py api_git_commit_detail
     */
    getCommit: (boardKey: string, sha: string): Promise<GitCommitDetail> =>
      request<GitCommitDetail>(`/boards/${boardKey}/git/commits/${sha}`),

    /**
     * Unified diff of one commit versus its first parent.
     * Binary files appear with `is_binary=true` and `patch=null`.
     * GET /api/boards/{boardKey}/git/commits/{sha}/diff?path=&context=
     * @see backend/app/api/repositories.py api_git_commit_diff
     */
    getCommitDiff: (
      boardKey: string,
      sha: string,
      params?: { path?: string; context?: number },
    ): Promise<CommitDiff> => {
      const qs = new URLSearchParams();
      if (params?.path) qs.set("path", params.path);
      if (params?.context !== undefined) qs.set("context", String(params.context));
      const q = qs.toString();
      return request<CommitDiff>(`/boards/${boardKey}/git/commits/${sha}/diff${q ? `?${q}` : ""}`);
    },

    /**
     * Three-dot merge-base range diff (base...head).
     * Matches GitHub/GitLab PR-diff semantics.
     * GET /api/boards/{boardKey}/git/diff?base=&head=&path=&context=
     * @see backend/app/api/repositories.py api_git_range_diff
     */
    getRangeDiff: (
      boardKey: string,
      params: { base: string; head: string; path?: string; context?: number },
    ): Promise<RangeDiff> => {
      const qs = new URLSearchParams();
      qs.set("base", params.base);
      qs.set("head", params.head);
      if (params.path) qs.set("path", params.path);
      if (params.context !== undefined) qs.set("context", String(params.context));
      return request<RangeDiff>(`/boards/${boardKey}/git/diff?${qs.toString()}`);
    },

    /**
     * Git connection status for a board.
     * GET /api/boards/{boardKey}/git/status
     * @see backend/app/api/repositories.py api_git_status
     */
    getStatus: (boardKey: string): Promise<GitStatus> =>
      request<GitStatus>(`/boards/${boardKey}/git/status`),

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
