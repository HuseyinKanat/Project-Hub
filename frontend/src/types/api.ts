export type Priority = "low" | "medium" | "high" | "urgent";
export type TicketType =
  | "feature"
  | "bug"
  | "task"
  | "epic"
  | "chore"
  | "refactor"
  | "hotfix";
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

/**
 * The board role→capability map AS IT ACTUALLY ARRIVES ON THE WIRE (PH-297).
 *
 * AUTHORITATIVE SHAPE IS NESTED: the role→permissions map lives under a
 * top-level `roles` key — the backend gates EVERY capability on
 * `board.roles["roles"][role].permissions` (core/permissions.py
 * `role_permissions`), and both `defaults.DEFAULT_WEB_ROLES` and the PH-296
 * migration seed it that way. The OLD `roles: Record<string, {permissions}>`
 * type LIED (modelled it flat), so `TicketDetail`'s inline `board.roles[role]`
 * upload check read `undefined` and the evidence upload form NEVER rendered for
 * authorized roles (qa_failed iter-1). `mask_webhook_secret`
 * (services/serializers.py) rides optional, already-masked ("*****") webhook
 * secrets alongside `roles`; typed here so the shape is complete — never read by
 * the client. Consume the map via `canUploadAttachment` / `board.roles.roles`.
 */
export interface BoardRoles {
  /** role name → its capability grant; admin's grant is the `["*"]` wildcard. */
  roles: Record<string, { permissions: string[] }>;
  /** Serializer-masked to "*****" when a webhook is configured; never consumed. */
  webhook_secret?: string | null;
  refresh_secret?: string | null;
  /**
   * The backend column is a heterogeneous `dict[str, object]` (services/boards.py
   * `mask_webhook_secret`), so extra webhook-config keys (url, events, …) may ride
   * alongside `roles`. This index mirrors that bag faithfully and keeps the
   * existing nested-aware `BoardSettings` casts compiling. Access the load-bearing
   * map via the explicit `roles` field above (it wins over this index).
   */
  [key: string]: unknown;
}

export interface BoardResponse {
  id: string;
  key: string;
  name: string;
  description: string | null;
  project_type: string;
  roles: BoardRoles;
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
// PH-280 / epic PH-271: cross-board SEARCH (labels pivot)
// ---------------------------------------------------------------------------

/**
 * Mirrors backend `schemas.py` TicketSearchHit (PH-275, epic PH-271). One row in
 * the cross-board `/api/search` Tickets group — IDENTITY-ONLY (no description
 * snippet in v1; description is matched server-side but never returned). `board`
 * is the board KEY (e.g. "PH"), mirroring `GraphNode.board`, so a hit navigates
 * via the KEY-based route `/boards/:boardKey/tickets/:ticketKey` (NOT board_id).
 */
export interface TicketSearchHit {
  id: string;
  key: string;
  title: string;
  board: string; // board KEY (NOT id) — drives the cross-board nav URL
  board_id: string;
  state: string;
}

/**
 * Mirrors backend `schemas.py` SearchResponse (PH-281 labels pivot). GROUPED by
 * type — the two arrays are NEVER mixed into one list (the result panel renders
 * them as two separated groups: Tickets / Labels). `labels` is a plain
 * `list[str]` of distinct matching label strings (no separate entity) — the
 * frontend hashes each string for a deterministic chip color.
 */
export interface SearchResponse {
  tickets: TicketSearchHit[];
  labels: string[];
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

// ---------------------------------------------------------------------------
// PH-297 / epic — ticket evidence ATTACHMENTS (frontend of PH-296 backend).
// Mirrors backend `schemas.py` AttachmentResponse (PH-296) VERBATIM (UUID → string,
// datetime → ISO string). Metadata only — NEVER the blob bytes (served separately
// via GET .../attachments/{id}/content). `kind` is a free-text backend Form field
// (default "other"); the enumerated union carries the load-bearing values the UI
// badges/groups by, and `LooseString` keeps the literal hints without collapsing
// to bare `string` if the backend ever emits a new kind (S6571). `source` is a
// CLOSED backend-controlled set: "human" (REST multipart) | "agent" (MCP zero-copy).
// ---------------------------------------------------------------------------
export type AttachmentKind =
  | "screenshot"
  | "recording"
  | "video"
  | "log"
  | "report"
  | "other";
export type AttachmentSource = "human" | "agent";

export interface AttachmentResponse {
  id: string;
  ticket_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  checksum_sha256: string;
  /** Free-text on the wire; union + LooseString keeps hints, accepts any value. */
  kind: AttachmentKind | LooseString;
  source: AttachmentSource;
  /** Groups evidence by test/agent run; null → ungrouped ("Diğer"). */
  run_id: string | null;
  /**
   * Story phase slug (PH-311/312) — mirrors backend `AttachmentResponse.phase`.
   * Conventional values: `repro` | `before` | `iter-<N>-fail` | `iter-<N>-pass` |
   * `after`; any other non-empty string is a valid unknown slug; null → phaseless.
   */
  phase: string | null;
  author: ActorSummary;
  created_at: string;
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
// Cross-board concept graph (PH-281 backend / PH-280 frontend, epic PH-271)
// ---------------------------------------------------------------------------
//
// Mirrors backend `schemas.py` GraphNode / GraphEdge / GraphResponse EXACTLY
// (the STABLE topology contract — do NOT rename). Bipartite: ticket nodes + LABEL
// nodes, disambiguated by a TYPE-PREFIXED id ("ticket:<uuid>" / "label:<value>").
// Every node ALSO carries a redundant `type` discriminator so we style/filter
// without string-parsing the id. Topology only — NO coordinates (PH-280 runs a
// d3-force layout client-side). Ticket-only fields (board/board_id/key/state/
// title) are null on label nodes.
//
// PH-281 PIVOT — the graph is driven by inline `Ticket.labels` (free-text array),
// NOT a separate ConceptTag entity. A `label` node has `id="label:<rawValue>"`
// (the raw, un-slugified label STRING after the FIRST `label:` prefix — a value
// may itself contain ':', so strip ONLY the first prefix, never `split(':')`),
// `label`=the raw value, and `color` is ALWAYS null (labels carry no stored hue →
// the frontend hashes the string deterministically). The board node type
// (collapsed neighbour, `scope=board`) is UNCHANGED from PH-279.
export interface GraphNode {
  id: string; // "ticket:<uuid>" | "label:<rawValue>" | "board:<KEY>"
  type: "ticket" | "label" | "board"; // "board" = collapsed neighbour (scope=board)
  label: string; // ticket → key (e.g. "PH-274"); label → raw value; board → name/key
  // ticket-only (null for label nodes); ALSO carried by a board node:
  board: string | null; // board KEY (e.g. "PH") for grouping/color-by-board
  board_id: string | null;
  key: string | null; // ticket key (label duplicate, explicit for routing)
  state: string | null;
  title: string | null;
  // PH-281: kept for shape stability with PH-274 consumers; ALWAYS null for a
  // `label` node (labels have no stored color — the frontend hashes the string).
  color?: string | null;
}

export interface GraphEdge {
  id: string; // stable, deterministic — see backend services/graph derivation
  source: string; // prefixed node id
  target: string; // prefixed node id
  // PH-281 PIVOT — `has_tag`/`tag_link` are GONE; ticket→label edges are now
  // `has_label`. `epic` (parent→child), `reference` (ticket→ticket key-mention),
  // and `board` (collapsed cross-board) are unchanged. There is NO `relation`
  // field anymore (only the removed `tag_link` used it).
  type: "has_label" | "epic" | "reference" | "board";
  // A human-labelable per-edge context string the BACKEND populates for EVERY
  // edge (has_label→"has-label", epic→"epic", reference→"ticket-reference",
  // board→"cross-board"). The frontend READS this for the edge label (with a
  // type-keyed fallback); it never computes it.
  context: string | null;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
