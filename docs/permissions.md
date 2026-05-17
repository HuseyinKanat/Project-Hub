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

## Resolution Order

1. `*` grants access.
2. Actor must have a membership on the board.
3. Exact permission matches first.
4. Scoped permissions are evaluated against the resource.
5. If nothing matches, the call fails with `permission_denied`.

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

```python
# Implementer can update mutable fields only while they hold the ticket.
require_permission(actor, board, "ticket.update_field:title", resource=ticket)
# Passes only when ticket.assignee_id == actor.id, given role
# permission "ticket.update_field:if_assignee".
```

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

```json
{
  "error": "permission_denied",
  "message": "Permission denied",
  "required": "ticket.delete",
  "have": ["comment.add", "ticket.update_field:if_assignee"]
}
```
