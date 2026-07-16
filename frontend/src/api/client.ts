import { getStoredToken, useAuth } from "@/stores/auth";
import type {
  ActorListResponse,
  ApiError,
  AttachmentResponse,
  BoardListResponse,
  BoardResponse,
  CommentResponse,
  FieldGates,
  GraphResponse,
  HistoryEntry,
  MeResponse,
  MembershipListResponse,
  MembershipResponse,
  NotificationListResponse,
  NotificationResponse,
  ProfileResponse,
  ProjectPathResponse,
  RepoHealth,
  SearchResponse,
  SonarIssuesResponse,
  SonarIssueType,
  SonarRepoScanResult,
  SonarScanPlan,
  SonarScanResult,
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

  // ---------------------------------------------------------------------------
  // PH-297 (frontend of PH-296): ticket evidence ATTACHMENTS.
  //   GET  /api/tickets/{key}/attachments                         → metadata list
  //   GET  /api/tickets/{key}/attachments/{id}/content?token=&download=1 → bytes
  //   POST /api/tickets/{key}/attachments  (multipart)            → 201 metadata
  // list uses the shared request<T> (auth + ApiRequestError). The CONTENT url is a
  // pure builder (no fetch) — `<img>`/`<video>` tags cannot set an Authorization
  // header, so the token rides as `?token=` (backend `_actor_for_content` accepts
  // header OR query param; FileResponse serves Range/206 natively for mp4 seeking).
  // UPLOAD is a DEDICATED XHR (NOT request<T>): a manually-set JSON Content-Type
  // would clobber the multipart boundary the browser must generate, and fetch()
  // exposes no upload-progress events. It re-implements request<T>'s 401→logout +
  // ApiRequestError normalisation verbatim so callers get the same error surface.
  // ---------------------------------------------------------------------------
  listAttachments: (key: string) =>
    request<AttachmentResponse[]>(`/tickets/${key}/attachments`),

  attachmentContentUrl: (
    key: string,
    id: string,
    opts: { download?: boolean } = {},
  ): string => {
    const token = getStoredToken() ?? "";
    const dl = opts.download ? "&download=1" : "";
    return `${BASE}/tickets/${key}/attachments/${id}/content?token=${encodeURIComponent(token)}${dl}`;
  },

  // PH-300: fetch an attachment's bytes AS TEXT for the inline JSON/log viewer.
  // Reuses the same `?token=` content URL the <img>/<video> tags use (backend
  // `_actor_for_content` accepts the query param, so no Authorization header is
  // needed). Callers MUST gate on the metadata `size_bytes` FIRST and skip this
  // entirely when over cap — `maxBytes` here is a defensive SECOND belt that
  // truncates an unexpectedly-long body, NOT the primary gate. Throws
  // ApiRequestError on 401/403/404/… so the UI can render an inline error.
  // (URL is built inline, like uploadAttachment, to stay self-contained.)
  fetchAttachmentText: async (
    key: string,
    id: string,
    opts: { maxBytes?: number } = {},
  ): Promise<string> => {
    const token = getStoredToken() ?? "";
    const url = `${BASE}/tickets/${key}/attachments/${id}/content?token=${encodeURIComponent(token)}`;
    const res = await fetch(url, { headers: { Accept: "text/plain, */*" } });
    if (res.status === 401) useAuth.getState().logout();
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
    const text = await res.text();
    // `maxBytes` is compared against char length (an ASCII-log proxy; the
    // authoritative byte gate is the caller's size_bytes check, so a multibyte
    // body only ever truncates LATER than the cap — safe).
    return opts.maxBytes != null && text.length > opts.maxBytes
      ? text.slice(0, opts.maxBytes)
      : text;
  },

  uploadAttachment: (
    key: string,
    file: File,
    opts: {
      kind?: string;
      runId?: string | null;
      /** PH-314: story phase slug (composePhaseSlug output). Omitted/empty → phaseless. */
      phase?: string | null;
      onProgress?: (percent: number) => void;
    } = {},
  ): Promise<AttachmentResponse> =>
    new Promise<AttachmentResponse>((resolve, reject) => {
      const token = getStoredToken();
      const form = new FormData();
      form.append("file", file);
      if (opts.kind) form.append("kind", opts.kind);
      if (opts.runId && opts.runId.trim()) form.append("run_id", opts.runId.trim());
      // PH-314: only append a non-empty phase — an omitted field uploads phaseless
      // (AC3, back-compat); the composed slug is already validated by the backend.
      if (opts.phase) form.append("phase", opts.phase);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/tickets/${key}/attachments`);
      xhr.setRequestHeader("Accept", "application/json");
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      // NOTE: deliberately DO NOT set Content-Type — XHR derives the multipart
      // boundary from the FormData body; setting it by hand breaks the parse.

      if (opts.onProgress) {
        xhr.upload.onprogress = (e: ProgressEvent) => {
          if (e.lengthComputable) {
            opts.onProgress?.(Math.round((e.loaded / e.total) * 100));
          }
        };
      }

      const fail = (status: number, raw: string) => {
        if (status === 401) useAuth.getState().logout();
        let body: ApiError | null = null;
        try {
          body = JSON.parse(raw) as ApiError;
        } catch {
          body = null;
        }
        const message =
          body?.message ?? body?.error ?? body?.detail ?? `HTTP ${status}`;
        reject(new ApiRequestError(status, body, message));
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as AttachmentResponse);
          } catch {
            reject(new ApiRequestError(xhr.status, null, "Geçersiz sunucu yanıtı"));
          }
          return;
        }
        fail(xhr.status, xhr.responseText);
      };
      xhr.onerror = () =>
        reject(new ApiRequestError(0, null, "Ağ hatası — yükleme başarısız"));
      xhr.onabort = () =>
        reject(new ApiRequestError(0, null, "Yükleme iptal edildi"));

      xhr.send(form);
    }),

  // PH-314 (consumes PH-313): edit an attachment's METADATA (phase/kind/run_id) —
  // the blob is immutable. Key-presence semantics on the wire (backend
  // `exclude_unset`): a field sent as `null` CLEARS it (phase/run_id are nullable),
  // an OMITTED field is left untouched. So `updateAttachment(k, id, {phase: null})`
  // removes the phase, while `{phase: "iter-1-fail"}` sets it. 422 (empty body /
  // invalid slug) / 403 (missing `attachment.update`) / 404 (wrong ticket) surface
  // as ApiRequestError. Uses the shared request<T> (auth + error normalisation).
  updateAttachment: (
    key: string,
    id: string,
    fields: { phase?: string | null; kind?: string | null; run_id?: string | null },
  ): Promise<AttachmentResponse> =>
    request<AttachmentResponse>(`/tickets/${key}/attachments/${id}`, {
      method: "PATCH",
      ...jsonBody(fields),
    }),

  // PH-314 (consumes PH-313): hard-delete an attachment (row + blob) → 204 No
  // Content. `request<void>` already returns `undefined` on 204 (see line ~129), so
  // there is NO JSON-parse-on-empty-body bug (AC11). NOT idempotent server-side: a
  // second delete → 404. 403 (missing `attachment.delete`, pm/qa-only) surfaces as
  // ApiRequestError so the caller can show a non-destructive inline error (AC8).
  deleteAttachment: (key: string, id: string): Promise<void> =>
    request<void>(`/tickets/${key}/attachments/${id}`, { method: "DELETE" }),

  // ---------------------------------------------------------------------------
  // PH-280 / epic PH-271: cross-board concept GRAPH (the /space view).
  // GET /api/graph → bipartite GraphResponse {nodes, edges}. PH-281 re-pointed the
  // backend at inline `Ticket.labels` and moved the read gate to the GLOBAL
  // `ticket.read` cap (held under ANY board membership). The /space page surfaces
  // a live 403 honestly via its error branch rather than crashing.
  // NAMED `getConceptGraph` (NOT `getGraph` — that already exists for the
  // board-scoped git DAG; reusing it would collide). Query params are
  // server-side filters: `label` (SINGULAR, raw value) returns the 1-hop
  // neighbourhood of tickets carrying that label. The global /space sends NONE.
  //
  // PH-279 graph-v2 `scope`: `scope=global` (default) = the detailed cross-board
  // topology; `scope=board` REQUIRES `board=<key>` and COLLAPSES every cross-board
  // connection into one `board:<key>` node per neighbour (the per-board "Space"
  // tab). ⚠️ LOAD-BEARING: sending `board` ALONE (no scope) hits the OLD board-
  // FILTER (drop-dangling, no collapse) — the board tab MUST send `scope:"board"`.
  // `scope=board` without `board` → 422.
  // ---------------------------------------------------------------------------
  /** GET /api/graph → bipartite concept graph (global `ticket.read`). PH-281 labels. */
  getConceptGraph: (params?: {
    scope?: "global" | "board";
    board?: string;
    /** PH-281: 1-hop filter by RAW label value (SINGULAR `?label=`, was `?tag=`). */
    label?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params?.scope) qs.set("scope", params.scope); // board-collapse mode
    if (params?.board) qs.set("board", params.board);
    if (params?.label) qs.set("label", params.label);
    const q = qs.toString();
    return request<GraphResponse>(`/graph${q ? `?${q}` : ""}`);
  },
  // ---------------------------------------------------------------------------
  // PH-280 / epic PH-271: cross-board unified SEARCH (the /api/search surface).
  // PH-281: GET /api/search?q=&labels= → grouped {tickets, labels:string[]}. Gated
  // by the GLOBAL `ticket.read` cap; a live 403 surfaces via ApiRequestError and
  // the result panel renders an error branch (never throws silently). `labels`
  // (optional CSV, was `tags`) narrows by EXACT label value (AND-intersection);
  // the global search box sends only `q`. Backend short-circuits a blank `q` to
  // ([],[]), so the caller gates the query on a non-empty trimmed term.
  // ---------------------------------------------------------------------------
  /** GET /api/search?q=&labels= → grouped cross-board hits (global `ticket.read`). */
  searchAll: (q: string, labels?: string[]) => {
    const qs = new URLSearchParams();
    qs.set("q", q);
    if (labels && labels.length > 0) qs.set("labels", labels.join(","));
    return request<SearchResponse>(`/search?${qs.toString()}`);
  },
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
  // PH-322 (consumed by PH-323): user profile + per-owner project-path registry.
  //   GET  /api/profile                     → the caller's owner_slug + resolved owner
  //   PUT  /api/profile {owner_slug}         → set the human owner_slug (409 dup / 422 regex / 403 agent)
  //   GET  /api/profile/project-paths?board= → the caller's OWN path on ONE board
  //   PUT  /api/profile/project-paths        → upsert; local_path:"" REMOVES (no DELETE endpoint)
  // All use the shared request<T> (Bearer auth + ApiRequestError normalisation).
  // The owner is ALWAYS derived from the token server-side — never sent in a body.
  // ---------------------------------------------------------------------------

  /** GET /api/profile → the caller's own owner_slug + effective resolved owner. */
  getProfile: () => request<ProfileResponse>("/profile"),

  /**
   * PUT /api/profile — set the caller's human owner_slug. Human-only (agent → 403);
   * a slug failing `^[a-z0-9][a-z0-9-]{0,19}$` → 422, a taken slug → 409. Both
   * surface via ApiRequestError so the Profile page can show the server message
   * without a crash (the 409 case).
   */
  updateProfile: (ownerSlug: string) =>
    request<ProfileResponse>("/profile", {
      method: "PUT",
      ...jsonBody({ owner_slug: ownerSlug }),
    }),

  /**
   * GET /api/profile/project-paths?board=<KEY> — the caller's OWN local path on one
   * board. `board` is REQUIRED (there is no list-all route — the Profile page fans
   * out one call per membership). Returns `local_path: null` (200, NOT an error)
   * when no row exists yet — an ADD affordance. 422 when the caller has no resolved
   * owner (the page gates the whole path form on `profile.owner` to avoid this).
   */
  getProjectPath: (board: string) =>
    request<ProjectPathResponse>(
      `/profile/project-paths?board=${encodeURIComponent(board)}`,
    ),

  /**
   * PUT /api/profile/project-paths — upsert the caller's path on a board. Passing
   * `localPath: ""` REMOVES the row (remove-on-empty contract — there is no DELETE
   * endpoint), and the response then carries `local_path: null`. A >255-char path →
   * 422; the owner is token-derived (never sent). Callers invalidate BOTH
   * ['project-paths', boardKey] AND ['members', boardKey] on settle.
   */
  putProjectPath: (params: { board: string; localPath: string }) =>
    request<ProjectPathResponse>("/profile/project-paths", {
      method: "PUT",
      ...jsonBody({ board: params.board, local_path: params.localPath }),
    }),

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

    /**
     * PH-237: ENQUEUE a NEW per-board analysis run (admin auth). DISTINCT from
     * `sync` (which re-polls the EXISTING analysis) — the actual scanner runs
     * HOST-side via `scripts/sonar-scan-board.sh`, ASYNC, NOT instant. Never-500:
     * the result's `scan_status` (queued|running|unsupported|disabled|
     * unconfigured|error) carries the honest outcome rather than throwing.
     * POST /api/boards/{boardKey}/sonarqube/scan → SonarScanResult.
     * Non-admin → ApiRequestError(status=403).
     * @see backend/app/api/boards.py (PH-236 scan), components/sonarqube.md
     */
    scan: (boardKey: string): Promise<SonarScanResult> =>
      request<SonarScanResult>(`/boards/${boardKey}/sonarqube/scan`, {
        method: "POST",
      }),

    /**
     * PH-237: the FROZEN per-board scan plan (admin auth, never-500). The frontend
     * reads `supported` + `reason` + `language` to annotate/disable "Scan now"
     * BEFORE the click (so an unsupported board — e.g. C#/Unity in CE — is honest
     * up-front). Fetched lazily AND gated on isAdmin (admin-only → 403 for
     * non-admin; do NOT fire it for them).
     * GET /api/boards/{boardKey}/sonarqube/scan-plan → SonarScanPlan.
     * Non-admin → ApiRequestError(status=403).
     * @see backend/app/api/boards.py (PH-236 scan-plan), components/sonarqube.md
     */
    getScanPlan: (boardKey: string): Promise<SonarScanPlan> =>
      request<SonarScanPlan>(`/boards/${boardKey}/sonarqube/scan-plan`),

    // -------------------------------------------------------------------------
    // PH-256 (epic PH-253, child 3/3) — PER-REPO setup/scan/sync, the single-repo
    // counterparts of the board-level setup/sync/scan above. `selector` = the
    // repo slug (the card has `repo.repo_slug`; the backend accepts slug-OR-id).
    // All admin-gated (non-admin → ApiRequestError(status=403)) and never-500:
    // setup/sync return RepoHealth, scan returns SonarRepoScanResult (with a
    // nullable job_id — null when non-scannable). The FE RepoCardActions only
    // mounts these for admins, so no 403-spam fires for non-members.
    // @see backend/app/api/boards.py (PH-254 setup, PH-255 scan+sync)
    // -------------------------------------------------------------------------

    /**
     * Persist (or re-affirm) a per-repo project key (admin auth). Empty body →
     * backend persists the derived default key. 422 (blank/bad key) / 409
     * (intra-board dup) / 403 (non-admin) surface as ApiRequestError for the
     * inline per-card error region.
     * POST /api/boards/{boardKey}/repositories/{selector}/sonarqube/setup → RepoHealth.
     */
    setupRepo: (
      boardKey: string,
      selector: string,
      body: SonarSetupRequest = {},
    ): Promise<RepoHealth> =>
      request<RepoHealth>(
        `/boards/${boardKey}/repositories/${selector}/sonarqube/setup`,
        { method: "POST", ...jsonBody(body) },
      ),

    /**
     * ENQUEUE a NEW per-repo analysis run (admin auth). Async/HOST-side — NOT
     * instant. Never-500: `scan_status` + a nullable `job_id` carry the honest
     * outcome (queued ⇒ job_id set; non-scannable ⇒ job_id null) rather than
     * throwing.
     * POST /api/boards/{boardKey}/repositories/{selector}/sonarqube/scan → SonarRepoScanResult.
     */
    scanRepo: (
      boardKey: string,
      selector: string,
    ): Promise<SonarRepoScanResult> =>
      request<SonarRepoScanResult>(
        `/boards/${boardKey}/repositories/${selector}/sonarqube/scan`,
        { method: "POST" },
      ),

    /**
     * Re-poll a single repo's cached metrics from SonarQube (admin auth). A
     * never-scanned repo returns a 200 unscanned RepoHealth card (the bridge out
     * of "No analysis yet"); a scanned repo returns fresh metrics. Degrades
     * gracefully — never throws on unreachable.
     * POST /api/boards/{boardKey}/repositories/{selector}/sonarqube/sync → RepoHealth.
     */
    syncRepo: (boardKey: string, selector: string): Promise<RepoHealth> =>
      request<RepoHealth>(
        `/boards/${boardKey}/repositories/${selector}/sonarqube/sync`,
        { method: "POST" },
      ),
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
