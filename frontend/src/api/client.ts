import { getStoredToken, useAuth } from "@/stores/auth";
import type {
  ApiError,
  BoardListResponse,
  BoardResponse,
  CommentResponse,
  HistoryEntry,
  MeResponse,
  NotificationListResponse,
  NotificationResponse,
  TicketCreatePayload,
  TicketListResponse,
  TicketResponse,
  TicketUpdatePayload,
} from "@/types/api";

const BASE = "/api";

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

  addWorkflowState: (boardId: string, state: { name: string; color?: string; is_initial?: boolean; is_terminal?: boolean }) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/states`, { method: "POST", ...jsonBody(state) }),
  deleteWorkflowState: (boardId: string, stateName: string) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/states/${stateName}`, { method: "DELETE" }),
  updateWorkflowStates: (boardId: string, states: { name: string; color?: string; is_initial?: boolean; is_terminal?: boolean; order?: number }[]) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/states`, { method: "PUT", ...jsonBody({ states }) }),
  updateWorkflowTransitions: (boardId: string, transitions: { from: string; to: string; allowed_roles?: string[] }[]) =>
    request<BoardResponse>(`/boards/${boardId}/workflow/transitions`, { method: "PUT", ...jsonBody({ transitions }) }),
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
