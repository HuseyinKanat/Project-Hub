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
}

export interface WorkflowResponse {
  id: string;
  name: string;
  states: WorkflowState[];
  transitions: { from: string; to: string; allowed_roles?: string[] }[];
  is_default: boolean;
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
