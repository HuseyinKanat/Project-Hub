# Permission Grammar

Permissions are board-scoped and resolved from `BoardMembership.role`.

Format:

```text
<resource>.<action>[:<scope>]
```

## Known Permissions

| Permission | Meaning |
|---|---|
| `*` | Full admin access |
| `ticket.create` | Create tickets |
| `ticket.delete` | Soft-delete tickets |
| `ticket.assign` | Assign or unassign tickets |
| `ticket.claim` | Claim ticket locks and update agent phase |
| `ticket.update_field` | Update all mutable ticket fields |
| `ticket.update_field:<field>` | Update one field |
| `ticket.update_field:<f1>,<f2>` | Update only listed fields |
| `ticket.update_field:if_assignee` | Update mutable fields only when assignee |
| `state.transition:*` | Perform any workflow transition |
| `state.transition:to_<state>` | Transition into one target state |
| `state.transition:if_assignee` | Transition only when assignee |
| `comment.add` | Add ticket comments |
| `epic.manage` | Manage epics |
| `git.create_branch` | Create a branch for a ticket |
| `git.link_commit` | Manually link git activity |
| `workflow.edit` | Edit workflow definitions |
| `board.edit` | Edit board settings |

> **PH-281**: the PH-273 `tag.read` / `tag.manage` / `tag.assign` caps were removed
> with the ConceptTag user-facing surface. The cross-board graph (`/api/graph`) +
> search (`/api/search`) now read the inline `Ticket.labels` free-text ARRAY and gate
> on the EXISTING global `ticket.read` cap (see below).

## Resolution Order

1. `*` grants access.
2. Actor must have a membership on the board.
3. Exact permission matches first.
4. Scoped permissions are evaluated against the resource.
5. If nothing matches, the call fails with `permission_denied`.

## Global (cross-board) read gate — `/api/graph` + `/api/search` + `related_tickets` (PH-281, PH-287)

The cross-board concept graph (`/api/graph`), search (`/api/search`), and the
`related_tickets` MCP tool (PH-287) expose cross-board ticket key/title/state, so
they are a **cross-board ticket read**. They are NOT board-scoped (there is no single
board to gate against), so they use
`require_global_permission(actor, "ticket.read", memberships_with_boards)`:

- passes iff the actor holds `ticket.read` (or `*`) under the role of **ANY** of their
  board memberships ("holds `ticket.read` on at least one board"). The caller loads the
  actor's memberships **with their boards' roles** eager-loaded (`current_actor` only
  selectinloads `Actor.memberships`, not `membership.board`), keeping the gate
  self-contained + async-safe without broadening `current_actor`.
- **PH-287: `pm` + `orchestrator` now hold `ticket.read`** (granted in
  `DEFAULT_WEB_ROLES`) so the Coordinator's `jarwis-pm` channel can call
  `related_tickets` (and graph/search). Reading is the least-dangerous cap and pm
  already holds the write caps, so withholding read was an accident, not a security
  boundary. A board-less actor with NO membership is **still denied** — the
  stranger-denied invariant (least-privilege) holds.
- ⚠️ **Propagation**: `DEFAULT_WEB_ROLES` is a TEMPLATE applied at board creation;
  EXISTING boards keep their stored `roles` JSON. The grant is **inert** on live
  boards until `projecthub update_board_roles` re-applies the template. New boards
  get it automatically.

> Cross-board read is intentional but scoped: the graph/search endpoints return ticket
> identity (key/title/state/board) + label STRINGS only — no board-B ticket bodies
> leak (ticket detail stays behind the ticket's own board read).

---

## Role x Permission Matrix

Default roles seeded by `app.services.defaults.DEFAULT_WEB_ROLES`. Run
`projecthub update_board_roles --board <KEY>` (or omit `--board` for all
boards) to refresh existing boards after this matrix changes.

| Permission | admin | pm | architect | backend_dev | frontend_dev | reviewer | qa | orchestrator |
|---|---|---|---|---|---|---|---|---|
| `*` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `ticket.create` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `ticket.delete` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `ticket.assign` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ticket.claim` | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |
| `ticket.update_field` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `ticket.update_field:<scoped>` | ✅ | ✅ | ✅ | `if_assignee` | `if_assignee` | `technical_depth` | `impact_analysis,test_plan` | ❌ |
| `state.transition:*` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `state.transition:to_in_review` | ✅ | ✅ | ✅ | via `if_assignee` | via `if_assignee` | ❌ | ✅ | ❌ |
| `state.transition:to_in_progress` | ✅ | ✅ | ❌ | via `if_assignee` | via `if_assignee` | ✅ | ✅ | ❌ |
| `state.transition:to_in_test` | ✅ | ✅ | ❌ | via `if_assignee` | via `if_assignee` | ✅ | ❌ | ❌ |
| `state.transition:to_done` | ✅ | ✅ | ❌ | via `if_assignee` | via `if_assignee` | ❌ | ✅ | ❌ |
| `state.transition:if_assignee` | ✅ | via `*` | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `comment.add` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `epic.manage` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `git.create_branch` | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |
| `workflow.edit` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `board.edit` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `ticket.read` (graph/search/related_tickets global gate — PH-281, PH-287) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Role intent — one-line summary

- **admin** — humans operating the board (full access).
- **pm** — opens, triages, decomposes; can transition anywhere.
- **architect** — fills `technical_depth` + mermaid; hands off to implementers (no state transitions in normal flow).
- **backend_dev / frontend_dev** — claim, branch, implement; transition only while assignee; can re-assign for handoff.
- **reviewer** — review-only; can amend `technical_depth` on approve, transition to `in_test` (approve) or back to `in_progress` (reject).
- **qa** — bug reproduce (claim + branch + failing test), verify, regression; transitions `to_in_progress` (qa_failed) and `to_done` (pass).
- **orchestrator** — programmatic agent that opens tickets and routes them; minimal write surface.

---

## Scoped Permission Examples

### Field-level scope

```python
# Reviewer is allowed to amend only technical_depth on approve.
# role = "reviewer", permission = "ticket.update_field:technical_depth"
require_permission(actor, board, "ticket.update_field:technical_depth", resource=ticket)
```

### Targeted state transition

```python
# QA can transition a failing test back to in_progress.
require_permission(actor, board, "state.transition:to_in_progress", resource=ticket)
```

### Conditional scope (`if_assignee`)

Ownership for an `:if_assignee` grant is `ticket.assignee_id == actor.id` **OR**
`ticket.claimed_by == actor.id` — the claim owner counts as the assignee, so a Jarwis
agent that `claim`s a ticket but skips `assign` still passes. Both the field-update
(`ticket.update_field:if_assignee`) and the transition (`state.transition:if_assignee`)
grants evaluate this same equality.

```python
# Implementer can update mutable fields only while it owns the ticket
# (it is the assignee OR the current claim owner).
require_permission(actor, board, "ticket.update_field:title", resource=ticket)
# Passes when ticket.assignee_id == actor.id OR ticket.claimed_by == actor.id,
# given role permission "ticket.update_field:if_assignee".
```

**Stale-claim interaction + assignee backfill (PH-340).** A claim with no heartbeat for
`CLAIM_TIMEOUT_SECONDS` (300s) is auto-released by the stale-claim cron, which sets
`claimed_by = NULL`. If `assignee_id` was ALSO null, the actor would lose its
`if_assignee` write authority mid-work — e.g. during a build/test that outlasts the
timeout. To prevent that silent loss, the release **backfills** `assignee_id` with the
expiring claim owner **only when `assignee_id IS NULL`** (same transaction, before
`claimed_by` is cleared); a non-null assignee is NEVER overwritten (the Coordinator's
rotation authority is preserved). The `released` history event records the pin
(`new_value.assignee_id` alongside `reason: "stale_claim_timeout"`). Rationale + the
rejected grace-window alternative: [ADR-0001](adr/0001-stale-claim-assignee-backfill.md).

**`not_owner` denial (PH-340).** When an actor DOES hold a base-matching `:if_assignee`
grant yet is neither the assignee nor the claim owner, the 403 is enriched with
`reason: "not_owner"` plus `actor_id` / `assignee_id` / `claimed_by`, so the reader does
not misread the `have` list (which shows the grant) and looks at ownership instead. A
denial with NO matching `:if_assignee` grant keeps the generic shape (no `reason`) — see
[Error Response](#error-response).

---

## Permission Check Flow

```
1. Request comes in with (actor, board, required)
2. Load BoardMembership rows for (actor, board)
3. Collect role permissions from board.roles JSON
4. For each permission, _permission_matches(...) evaluates:
   - exact match
   - wildcards ("*", "ticket.update_field" matching scoped form,
     "state.transition:*" matching any to_<state>)
   - field-list match for "ticket.update_field:<f1>,<f2>"
   - "if_assignee" check against ticket.assignee_id
5. If any match → allow; else raise PermissionDenied
```

---

## Error Response

Generic denial (missing capability) — unchanged shape:

```json
{
  "error": "permission_denied",
  "message": "Permission denied",
  "required": "ticket.delete",
  "have": ["comment.add", "ticket.update_field:if_assignee"]
}
```

Ownership denial (PH-340) — the actor holds a base-matching `:if_assignee` grant but is
neither the assignee nor the claim owner. The REST body ADDS `reason` + `actor_id` +
`assignee_id` + `claimed_by` (each id field may be `null`):

```json
{
  "error": "permission_denied",
  "message": "Permission denied",
  "required": "ticket.update_field:impact_analysis",
  "have": ["comment.add", "ticket.update_field:if_assignee"],
  "reason": "not_owner",
  "actor_id": "9e28d810-2f3a-4c6b-8b1a-1c2d3e4f5a6b",
  "assignee_id": null,
  "claimed_by": null
}
```

Over MCP the tool-error detail carries `reason` + `claimed_by` (the two fields already in
the MCP error allowlist); `actor_id` / `assignee_id` are REST-only. ONLY the `not_owner`
branch sets these fields — every other 403 stays byte-identical to the generic body above.

---

## Git integration endpoints

Git endpoints added in G1–G13 (PH-149 epic) use existing permission gates — no new permission strings were introduced. All git endpoints are board-scoped.

| Endpoint | Auth gate | Equivalent permission |
|---|---|---|
| `PUT /api/boards/{key}/repository` | `_require_board_admin` | `board.edit` |
| `DELETE /api/boards/{key}/repository` | `_require_board_admin` | `board.edit` |
| `GET /api/boards/{key}/git/status` | `_require_board_member` | board member (read) |
| `GET /api/boards/{key}/git/graph` | `_require_board_member` | board member (read) |
| `GET /api/boards/{key}/git/branches` | `_require_board_member` | board member (read) |
| `GET /api/boards/{key}/git/commits` | `_require_board_member` | board member (read) |
| `GET /api/boards/{key}/git/commits/{sha}` | `_require_board_member` | board member (read) |
| `GET /api/boards/{key}/git/commits/{sha}/diff` | `_require_board_member` | board member (read) |
| `GET /api/boards/{key}/git/diff` | `_require_board_member` | board member (read) |
| `POST /api/boards/{key}/git/refresh` | Hybrid: Bearer admin **or** shared-secret (`X-Git-Refresh-Token`) | `board.edit` (Bearer path) / secret-based (hook path) |
| `POST /api/boards/{key}/repository/rotate-refresh-secret` | `_require_board_admin` | `board.edit` |
| `GET /api/tickets/{key}/commits` | `_require_board_member` (via ticket→board) | board member (read) |

### Auth grammar notes

- **Board admin** (`_require_board_admin`): the actor must have a `BoardMembership` row with a role that includes `board.edit` or `*`. By default: only `admin` role.
- **Board member** (`_require_board_member`): any actor with a `BoardMembership` row on the board, regardless of role. Equivalent to authenticated read access — analogous to dashboard read.
- **`POST /git/refresh` hybrid auth**: the endpoint accepts either (a) a Bearer token identifying a board admin (checked via `_resolve_actor_from_bearer` + admin membership check) **or** (b) a shared-secret via `X-Git-Refresh-Token` header (compared via `hmac.compare_digest` against `board.roles["refresh_secret"]`). The Bearer path is checked first; if no Bearer header is present or it does not match an admin, the shared-secret path is evaluated. If both fail, the request is rejected.
- **`refresh_secret` is NOT tied to `BoardMembership.role`**: it lives in the `board.roles` JSON as a separate key (`board.roles["refresh_secret"]`). It is a standalone shared secret class — minted/rotated via the `rotate-refresh-secret` endpoint (admin only) or the `connect_repository` CLI command. Holding the secret grants only the right to trigger a git refresh; it grants no ticket/state/field permissions.

---

## Claude Code tool whitelist (non-board permissions)

These entries gate what a Claude Code **sub-agent / Coordinator** may invoke — they
are enforced by the agent tool whitelist (`.claude/agents/*.md` frontmatter and
`.claude/settings.json`), **NOT** by `BoardMembership.role`. They introduce **no new
board permission strings** (the board permission grammar above is unchanged), so the
two permission systems are kept distinct: the matrix above governs project-hub API
calls; the entries below govern Claude Code tool access and local filesystem edits.

### Reviewer SonarQube MCP tools (PH-195)

PH-195 added the official SonarSource MCP server (`sonarqube` entry in `.mcp.json`)
to the reviewer's tool whitelist (`.claude/agents/reviewer.md` frontmatter). These
tools target the **external** SonarSource MCP server, not the project-hub board, so
they are Claude Code tool-whitelist entries — analogous to the git section's "no new
permission strings".

| Tool | Use |
|---|---|
| `mcp__sonarqube__analyze_code_snippet` | Mandatory per-diff snippet analysis during review; a BLOCKER/CRITICAL finding alone is grounds for `needs_revision`. |
| `mcp__sonarqube__get_project_quality_gate_status` | Situational helper — read the project quality gate. |
| `mcp__sonarqube__search_sonar_issues_in_projects` | Situational helper — search existing issues. |
| `mcp__sonarqube__get_component_measures` | Situational helper — read component measures. |

Notes:

- Gated by the **Claude Code tool whitelist** (the reviewer sub-agent frontmatter),
  not by `BoardMembership.role`.
- They require the `sonarqube` MCP server to be connected — see the
  [SonarQube setup runbook](./sonarqube-setup.md) for the token bootstrap and the
  required Claude Code session restart.
- If the server is not connected (session not restarted / SonarQube down / no token),
  the reviewer **gracefully skips** snippet analysis; that skip alone is not a reject.

### `Edit(.claude/agents/**)` allow rule (PH-195)

`.claude/settings.json` `permissions.allow` includes:

```json
"Edit(.claude/agents/**)"
```

This is a **Claude Code filesystem-edit pre-approval** — it lets the
Coordinator/agents edit sub-agent definitions under `.claude/agents/` without a
per-edit prompt (PH-195 added the reviewer's `mcp__sonarqube__*` tools to
`reviewer.md`). It is a Claude Code permission, **distinct** from the board
permission grammar; it grants no project-hub API access.
