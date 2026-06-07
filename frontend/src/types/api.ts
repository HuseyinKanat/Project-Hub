export type Priority = "low" | "medium" | "high" | "urgent";
export type TicketType = "feature" | "bug" | "task" | "epic";
export type AgentPhase =
  | "planning"
  | "analyzing"
  | "coding"
  | "testing"
  | "reviewing"
  | "idle";

export interface ActorSummary {
  id: string;
  kind: "human" | "agent";
  display_name: string;
  agent_id: string | null;
  agent_role_hint: string | null;
}

export interface AgentPhaseInfo {
  agent_id: string;
  phase: AgentPhase;
  message: string;
  started_at: string;
  last_heartbeat_at: string;
}

export interface WorkflowState {
  name: string;
  category: "new" | "active" | "done";
  color?: string;
  is_initial?: boolean;
  is_terminal?: boolean;
  // Client-side position storage for visual editor
  position?: { x: number; y: number };
}

export interface FieldGates {
  required_fields?: string[];
  exempt_ticket_types?: string[];
}

export interface WorkflowTransition {
  from: string;
  to: string;
  allowed_roles?: string[];
  field_gates?: FieldGates;
}

export interface WorkflowResponse {
  id: string;
  name: string;
  states: WorkflowState[];
  transitions: WorkflowTransition[];
  is_default: boolean;
  /** Populated by list_workflows MCP tool (BoardWorkflow join) */
  is_active?: boolean;
}

/**
 * SonarQube board-health snapshot — mirrors backend `BoardHealth`
 * (backend/app/schemas.py:263-277, PH-193) verbatim. Null on `BoardResponse`
 * when no SonarQube metric row exists yet (mirrors the `repository` field).
 * Carries metrics only — never the SonarQube token or any secret.
 */
export interface BoardHealth {
  quality_gate_status: string | null; // 'OK' | 'ERROR' | 'WARN' | null
  bugs: number | null;
  vulnerabilities: number | null;
  code_smells: number | null;
  coverage: number | null; // percent 0..100
  duplicated_lines_density: number | null; // percent 0..100
  ncloc: number | null;
  fetched_at: string; // ISO datetime — non-null when a metric row exists
}

/**
 * SonarQube issue drill-down (PH-204 / epic PH-201, D2). Mirrors PH-203's
 * backend `SonarIssueItem` + `SonarIssuesResponse` (backend/app/schemas.py)
 * verbatim. Consumed lazily by `useSonarIssues` when a SonarHealthPanel tile
 * is clicked. `component` is already a RELATIVE path (backend stripped the
 * project prefix); `dashboard_url` is already host-facing — use both verbatim.
 */
export type SonarIssueType = "BUG" | "VULNERABILITY" | "CODE_SMELL";
export type SonarIssueSeverity =
  | "BLOCKER"
  | "CRITICAL"
  | "MAJOR"
  | "MINOR"
  | "INFO";

/**
 * "Any other string" branch of a literal-union-preserving string type.
 *
 * The classic `string & {}` idiom keeps a union's literal autocomplete hints
 * while still accepting arbitrary backend strings (avoids the union collapsing
 * to bare `string` — typescript:S6571). But `string & {}` intersects with an
 * empty-member object type, which SonarQube flags as a no-op intersection
 * (typescript:S4335). Intersecting with a phantom-optional brand instead keeps
 * the exact same widening behaviour — any string is still assignable both ways
 * and the literal hints survive — without an empty-member intersection.
 */
export type LooseString = string & { readonly __brand?: never };
export type SonarIssuesStatus =
  | "ok"
  | "unreachable"
  | "no_project_key"
  | "not_configured";

export interface SonarIssue {
  key: string;
  rule: string;
  /**
   * SonarQube canonical severity; widen-safe — badge map falls back on unknown.
   * `LooseString` preserves the literal autocomplete hints while still accepting
   * any backend value, without collapsing the union to `string` (S6571).
   */
  severity: SonarIssueSeverity | LooseString;
  type: SonarIssueType;
  /** RELATIVE file path — backend already stripped the project prefix. */
  component: string;
  line: number | null;
  message: string;
}

export interface SonarIssuesResponse {
  /**
   * 'ok' carries issues; anything else is a graceful-200 error reason.
   * `LooseString` keeps the literal hints without collapsing to `string` (S6571).
   */
  status: SonarIssuesStatus | LooseString;
  total: number;
  page: number;
  page_size: number;
  issues: SonarIssue[];
  /** Host-facing SonarQube deep link; null on no_project_key / not_configured. */
  dashboard_url: string | null;
}

export interface BoardResponse {
  id: string;
  key: string;
  name: string;
  description: string | null;
  project_type: string;
  roles: Record<string, { permissions: string[] }>;
  workflow: WorkflowResponse;
  created_at: string;
  updated_at: string;
  /** Optional repository summary — null when not yet configured. PH-150 / G1. */
  repository: import("@/types/git").RepositorySummary | null;
  /** Optional SonarQube health snapshot — null when no scan yet. PH-196 / PH-193. */
  health: BoardHealth | null;
}

export interface BoardListResponse {
  boards: BoardResponse[];
}

export interface TicketResponse {
  id: string;
  key: string;
  board_id: string;
  type: TicketType;
  title: string;
  description: string;
  state: string;
  agent_phase: AgentPhaseInfo | null;
  assignee: ActorSummary | null;
  reporter: ActorSummary;
  priority: Priority;
  epic_id: string | null;
  labels: string[];
  acceptance_criteria: string | null;
  technical_depth: string | null;
  impact_analysis: string | null;
  test_plan: string | null;
  steps_to_reproduce: string | null;
  expected_behavior: string | null;
  actual_behavior: string | null;
  branch_name: string | null;
  story_points: number | null;
  due_date: string | null;
  claimed_by: string | null;
  claimed_at: string | null;
  created_at: string;
  updated_at: string;
  _links: Record<string, string>;
}

export interface TicketListResponse {
  tickets: TicketResponse[];
}

export interface TicketCreatePayload {
  board_id: string;
  type: TicketType;
  title: string;
  description?: string;
  priority?: Priority;
  epic_id?: string | null;
  labels?: string[];
  acceptance_criteria?: string | null;
  technical_depth?: string | null;
  steps_to_reproduce?: string | null;
  expected_behavior?: string | null;
  actual_behavior?: string | null;
  story_points?: number | null;
  due_date?: string | null;
}

export interface TicketUpdatePayload {
  title?: string;
  description?: string;
  priority?: Priority;
  epic_id?: string | null;
  labels?: string[];
  acceptance_criteria?: string | null;
  technical_depth?: string | null;
  impact_analysis?: string | null;
  test_plan?: string | null;
  steps_to_reproduce?: string | null;
  expected_behavior?: string | null;
  actual_behavior?: string | null;
  branch_name?: string | null;
  story_points?: number | null;
  due_date?: string | null;
}

export interface CommentResponse {
  id: string;
  ticket_id: string;
  author: ActorSummary;
  body: string;
  created_at: string;
  edited_at: string | null;
}

export interface HistoryEntry {
  id: string;
  actor: ActorSummary | null;
  event_type: string;
  field: string | null;
  old_value: unknown;
  new_value: unknown;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface NotificationResponse {
  id: string;
  ticket_id: string;
  ticket_key: string;
  event_type: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

export interface NotificationListResponse {
  notifications: NotificationResponse[];
  unread_count: number;
}

export interface ApiError {
  error: string;
  message?: string;
  required?: string;
  have?: string[];
  detail?: string;
  transition?: string;
  missing_fields?: string[];
  from_state?: string;
  to_state?: string;
  allowed?: string[];
  claimed_by?: string;
  since?: string;
}

export interface MembershipSummary {
  board_id: string;
  board_key: string;
  role: string; // "admin" | "pm" | "editor" | "viewer" | role-id-from-board
}

export interface MeResponse {
  actor: {
    id: string;
    kind: "human" | "agent";
    display_name: string;
    agent_id: string | null;
    agent_role_hint: string | null;
  };
  memberships: MembershipSummary[];
}

// PH-39: Board membership management types

export interface MembershipResponse {
  id: string;
  actor: ActorSummary;
  role: string;
  created_at: string;
  updated_at: string;
}

export interface MembershipListResponse {
  members: MembershipResponse[];
}

export interface ActorListResponse {
  actors: ActorSummary[];
}
