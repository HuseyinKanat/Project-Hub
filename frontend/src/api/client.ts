import { getStoredToken, useAuth } from "@/stores/auth";
import type {
  ApiError,
  BoardListResponse,
  BoardResponse,
  CommentResponse,
  FieldGates,
  HistoryEntry,
  MeResponse,
  NotificationListResponse,
  NotificationResponse,
  TicketCreatePayload,
  TicketListResponse,
  TicketResponse,
  TicketUpdatePayload,
  WorkflowResponse,
} from "@/types/api";

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
  getMe: () => request<MeResponse>("/auth/me"),
  deleteTicket: (ticketKey: string, reason: string) =>
    request<void>(`/tickets/${ticketKey}`, { method: "DELETE", ...jsonBody({ reason }) }),
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
