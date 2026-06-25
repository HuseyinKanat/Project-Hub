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
 * Per-repo SonarQube quality snapshot — mirrors backend `RepoHealth`
 * (backend/app/schemas.py RepoHealth, PH-246) VERBATIM. One entry per linked
 * repository in `BoardResponse.repo_health`; the FE (PH-249) renders these as a
 * card grid in the `#quality` tab. Carries the same 7 `BoardHealth` metrics PLUS
 * the repo identity + its own host-facing dashboard deep link. SECRET-FREE —
 * never the token nor the compose-internal sonarqube_url.
 *
 * `repo_id` / `repo_slug` / `repo_name` are nullable: a `null` identity is the
 * BOARD-LEVEL AGGREGATE row (a single-repo board with no per-repo Repository
 * row, e.g. KIM, emits one aggregate row). The card keys its title off
 * `repo_name ?? "Board total"` and must never crash on a null identity.
 */
export interface RepoHealth {
  repo_id: string | null; // null = board-level aggregate row
  repo_slug: string | null; // null = aggregate
  repo_name: string | null; // null = aggregate
  is_primary: boolean; // PH-251: drives the per-repo `primary` badge (un-gated surface); aggregate row → false
  project_key: string; // the SonarQube projectKey this repo scanned under
  quality_gate_status: string | null; // 'OK' | 'ERROR' | 'WARN' | null (never scanned)
  bugs: number | null;
  vulnerabilities: number | null;
  code_smells: number | null;
  coverage: number | null; // percent 0..100
  duplicated_lines_density: number | null; // percent 0..100
  ncloc: number | null;
  fetched_at: string | null; // ISO datetime — poll wall-clock; null = linked repo with no metric yet (PH-252 "No analysis yet")
  dashboard_url: string | null; // HOST-facing deep link for this repo's project
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

/**
 * PH-235 — the HONEST SonarQube setup-status discriminator (mirrors backend
 * SONAR_STATUS_*). The frontend keys its setup/header messaging off THIS, never
 * off the boolean trio (which conflated "no analysis yet" with "unreachable").
 *   disabled      server kill switch off
 *   unconfigured  enabled, no project key
 *   no_analysis   key set, no metric, server NOT known down → "run a scan"
 *   ok            metric present / live poll succeeded
 *   unreachable   a REAL live sync attempt failed (genuine outage)
 */
export type SonarSetupState =
  | "disabled"
  | "unconfigured"
  | "no_analysis"
  | "ok"
  | "unreachable";

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

/**
 * SonarQube board-settings setup/sync/status view (PH-226 / epic PH-220, C6).
 * Mirrors backend `SonarSetupStatus` (backend/app/schemas.py:405-431, PH-223)
 * verbatim (datetime → ISO string). SECRET-FREE: never the sonarqube_token nor
 * the compose-internal sonarqube_url — only the resolved key + cached freshness
 * + a HOST-facing dashboard deep link. Returned 200 by all three of
 * setup/sync/status (never-500/never-hang contract).
 */
export interface SonarSetupStatus {
  /**
   * PH-235 — the load-bearing honest discriminator. Key all messaging off this,
   * NOT off `reachable` (which no longer means "metric exists"). `LooseString`
   * keeps the literal hints without collapsing to `string` (S6571).
   */
  status: SonarSetupState | LooseString;
  /**
   * PH-235 — a cached SonarQubeMetric row exists. The truthful "we have data"
   * signal split out of the old `reachable` overload.
   */
  has_analysis: boolean;
  /** settings.sonarqube_enabled — the server-level kill switch. */
  enabled: boolean;
  /**
   * True only when a live poll just succeeded. On the pure read path this is a
   * best-effort optimistic value — NEVER a false "unreachable". PH-235: ignore it
   * for messaging when `status === "no_analysis"`; key off `status` instead.
   */
  reachable: boolean;
  /** The board has a resolvable project key. */
  configured: boolean;
  /** The resolved key (or null when not linked yet). */
  project_key: string | null;
  /** Cached SonarQubeMetric.fetched_at — ISO datetime; null when no metric row. */
  last_metric_fetched_at: string | null;
  /** Latest cached gate: 'OK' | 'ERROR' | 'WARN' | 'NONE' | null. */
  quality_gate_status: string | null;
  /** HOST-facing deep link (scan_url + /dashboard?id=key); null when not linked. */
  dashboard_url: string | null;
  /** Human-readable state summary. */
  message: string;
}

/**
 * Body for `POST .../sonarqube/setup`. `project_key` is optional — omit (or send
 * `{}`) for the one-click default (backend derives `project-hub` for the PH
 * board; else the board key lowercased). A supplied key overrides the default.
 */
export interface SonarSetupRequest {
  project_key?: string | null;
}

/**
 * PH-237 / PH-257 — the `scan_status` enum returned by `POST .../sonarqube/scan`
 * (mirrors backend SonarScanResponse.scan_status, schemas.py). "Scan now"
 * ENQUEUES a new HOST-side analysis run — it is NOT instant.
 *   queued              intent enqueued; the host runner picks it up (async)
 *   running             a scan is already in flight
 *   unsupported         the HONEST CE gate — a language Community Edition cannot analyze
 *   needs_dotnet_setup  PH-257 — a csharp (Unity) board IS analyzable via the host .NET
 *                       pipeline, but the host prerequisite (dotnet SDK +
 *                       dotnet-sonarscanner + SONAR_DOTNET_ENABLED=true) is not ready, so
 *                       NO job is enqueued (honest, never a fake "queued")
 *   disabled            the server kill switch is off
 *   unconfigured        no project key — run Setup first
 *   error               a graceful failure; show the message (never a fake "queued")
 */
export type SonarScanStatus =
  | "queued"
  | "running"
  | "unsupported"
  | "needs_dotnet_setup"
  | "disabled"
  | "unconfigured"
  | "error";

/**
 * Result of `POST .../sonarqube/scan` ("Scan now"). Mirrors backend
 * `SonarScanResponse` (backend/app/schemas.py:452) VERBATIM — the wire field is
 * `scan_status` (snake), so the TS field name matches 1:1 (request<T> does NO key
 * remapping; naming it `status` would silently read `undefined`). SECRET-FREE.
 *
 * `LooseString` keeps the literal autocomplete hints without collapsing the union
 * to bare `string` if the backend ever adds a seventh value (S6571 / Risk R5).
 */
export interface SonarScanResult {
  /** The load-bearing six-value enum — keyed for the honest async/unsupported UX. */
  scan_status: SonarScanStatus | LooseString;
  /** Resolved projectKey (or null when unconfigured). */
  project_key: string | null;
  /** Detected primary language label (or null). */
  language: string | null;
  /** In-container `/repos/<rel>` sources path (or null). */
  container_source: string | null;
  /** Human-readable outcome (+ the host command on `queued`). */
  message: string;
}

/**
 * PH-256 (epic PH-253, child 3/3) — result of the PER-REPO scan endpoint
 * `POST /api/boards/{boardKey}/repositories/{selector}/sonarqube/scan`. Mirrors
 * backend `SonarRepoScanResponse` (backend/app/schemas.py, PH-255) VERBATIM.
 *
 * DELIBERATELY DISTINCT from the board-level `SonarScanResult` (Risk R3): the
 * per-repo response carries `repo_slug` + a nullable `job_id` (the queued
 * SonarScanJob UUID, null when NOT queued) and has NO `container_source`. Reusing
 * `SonarScanResult` would read `undefined` on `job_id`, so this is its own type.
 * Reuses the SAME `SonarScanStatus` union — the honest async/unsupported UX is
 * keyed off `scan_status` exactly as the board-level Scan-now is. SECRET-FREE
 * (never the token nor the compose-internal sonarqube_url).
 */
export interface SonarRepoScanResult {
  /** The scanned repo's slug (the selector this scan targeted). */
  repo_slug: string;
  /** Resolved per-repo projectKey (or null when unconfigured). */
  project_key: string | null;
  /** Detected primary language label (or null). */
  language: string | null;
  /** The load-bearing scan_status enum — keyed for the honest async/unsupported UX. */
  scan_status: SonarScanStatus | LooseString;
  /**
   * The enqueued SonarScanJob UUID — NON-null ONLY when `scan_status` is queued
   * (scannable). null ⇒ non-scannable (e.g. C# in CE / unconfigured / disabled);
   * the card shows an honest non-queued message and NEVER a fake queued state.
   */
  job_id: string | null;
  /** Human-readable outcome (the honest reason on a non-queued status). */
  message: string;
}

/**
 * The FROZEN per-board scan plan from `GET .../sonarqube/scan-plan` (admin-only,
 * never-500). Mirrors backend `SonarScanPlanResponse` (backend/app/schemas.py:479)
 * VERBATIM. SECRET-FREE (no token, no compose-internal sonarqube_url). PH-237
 * consumes `language` + `supported` + `reason` to annotate/disable "Scan now"
 * BEFORE the click, so an unsupported board is honest up-front.
 */
export interface SonarScanPlan {
  /** Resolved projectKey (or null when unconfigured). */
  project_key: string | null;
  /** In-container `/repos/<rel>` sources path the scanner reads (or null). */
  container_source: string | null;
  /** The HOST `repos_path` (informational). */
  host_source: string | null;
  /** Detected primary language label (or null). */
  language: string | null;
  /** True ⇒ Community Edition can analyze this board ⇒ "Scan now" enabled. */
  supported: boolean;
  /** Why — especially when `supported` is false (the honest annotation). */
  reason: string;
  /** `sonar.exclusions` glob the runner passes (or null). */
  exclusions: string | null;
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
  /**
   * PH-246 — per-repo SonarQube breakdown (one entry per linked repository).
   * Additive: the backend ALWAYS emits this (empty list when no per-repo metric
   * row), so it is non-nullable. Consumed by `SonarRepoHealthCards` (PH-249) in
   * the `#quality` tab; `health` above stays the primary-repo back-compat field.
   */
  repo_health: RepoHealth[];
  /**
   * HOST filesystem root for this board (PH-228 backfilled, PH-230 editable).
   * Drives git auto-detection + SonarQube key derivation. Null/empty → detection
   * disabled. Editable via PATCH /api/boards/{key} (`repos_path`).
   */
  repos_path: string | null;
  /**
   * PH-235 — the resolved SonarQube project key (additive nullable; null when
   * unconfigured). Lets the board-header SonarHealthPanel distinguish "no key"
   * (connect a key) from "key set, no analysis yet" (linked — run a scan).
   */
  sonarqube_project_key: string | null;
}

export interface BoardListResponse {
  boards: BoardResponse[];
}

// ---------------------------------------------------------------------------
// PH-276 / epic PH-271: concept tags (cross-board taxonomy)
// ---------------------------------------------------------------------------

/**
 * Compact concept-tag projection EMBEDDED in `TicketResponse.concept_tags`.
 * Mirrors backend `ConceptTagSummary` (services/serializers.py:concept_tag_summary)
 * — EXACTLY these 4 identity fields, NO `description` (kept narrow so listing a
 * cross-board tag never leaks the other board's ticket content). `color` is
 * nullable; a null falls back to a deterministic `var(--lane-*)` token in the chip.
 */
export interface ConceptTag {
  id: string; // UUID serialized as string
  slug: string;
  name: string;
  color: string | null;
}

/** One directed tag→tag link (mirrors backend `ConceptTagLinkResponse`). */
export interface ConceptTagLinkSummary {
  id: string;
  source_tag_id: string;
  target_tag_id: string;
  relation: string | null;
}

/**
 * The RICHER tag shape returned by `GET /api/concept-tags` (mirrors backend
 * `ConceptTagResponse`). Used ONLY by the editor's picker — the chip needs just
 * the 4 `ConceptTag` summary fields, all of which are present here.
 */
export interface ConceptTagDetail extends ConceptTag {
  description: string | null;
  created_at: string;
  updated_at: string;
  ticket_count: number;
  links: ConceptTagLinkSummary[];
}

export interface ConceptTagListResponse {
  tags: ConceptTagDetail[];
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
  // PH-276: concept tags are a SEPARATE field from `labels` (never overloaded
  // into the free-text array). Mirrors backend `ConceptTagSummary` — 4 fields,
  // attached/detached via their own endpoint (not a PATCH on this payload).
  concept_tags: ConceptTag[];
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

// ---------------------------------------------------------------------------
// Cross-board concept graph (PH-274 backend / PH-277 frontend, epic PH-271)
// ---------------------------------------------------------------------------
//
// Mirrors backend `schemas.py` GraphNode / GraphEdge / GraphResponse EXACTLY
// (the STABLE topology contract — do NOT rename). Bipartite: ticket nodes + tag
// nodes, disambiguated by a TYPE-PREFIXED id ("ticket:<uuid>" / "tag:<uuid>").
// Every node ALSO carries a redundant `type` discriminator so we style/filter
// without string-parsing the id. Topology only — NO coordinates (PH-277 runs a
// d3-force layout client-side). Ticket-only fields (board/board_id/key/state/
// title) are null on tag nodes; tag-only fields (slug/color) are null on ticket
// nodes. `color` is nullable; null falls back to a deterministic var(--lane-*).
export interface GraphNode {
  id: string; // "ticket:<uuid>" | "tag:<uuid>"
  type: "ticket" | "tag";
  label: string; // ticket → key (e.g. "PH-274"); tag → name
  // ticket-only (null for tag nodes):
  board: string | null; // board KEY (e.g. "PH") for grouping/color-by-board
  board_id: string | null;
  key: string | null; // ticket key (label duplicate, explicit for routing)
  state: string | null;
  title: string | null;
  // tag-only (null for ticket nodes):
  slug: string | null;
  color: string | null; // hex e.g. #7dd3fc
}

export interface GraphEdge {
  id: string; // stable, deterministic — see backend services/graph derivation
  source: string; // prefixed node id
  target: string; // prefixed node id
  type: "has_tag" | "tag_link" | "epic";
  relation: string | null; // ONLY for tag_link (carries ConceptTagLink.relation)
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
