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

| Permission | admin | manager | backend_dev | frontend_dev | qa |
|---|---|---|---|---|---|
| `*` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `ticket.create` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ticket.delete` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `ticket.assign` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `ticket.claim` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ticket.update_field` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `ticket.update_field:if_assignee` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `state.transition:*` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `state.transition:if_assignee` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `comment.add` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `epic.manage` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `git.create_branch` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `workflow.edit` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `board.edit` | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## Scoped Permission Examples

### Board-Scoped Permission Check

```python
# backend/app/core/permissions.py
from app.core.permissions import has_permission

# Check if actor can update ticket field
allowed = await has_permission(
    session,
    actor=current_actor,
    board_id=board.id,
    permission="ticket.update_field",
)
```

### Field-Level Scope

```python
# Only allow updating specific fields
allowed = await has_permission(
    session,
    actor=current_actor,
    board_id=board.id,
    permission="ticket.update_field:technical_depth,impact_analysis",
)
```

### Conditional Scope (Assignee Only)

```python
# Only assignee can update
if ticket.assignee_id == actor.id:
    allowed = await has_permission(
        session,
        actor=current_actor,
        board_id=board.id,
        permission="ticket.update_field:if_assignee",
    )
```

---

## Permission Check Flow

```
1. Request comes in with (actor, board_id, permission)
2. Load BoardMembership for (actor, board_id)
3. Get role_permissions from Role table
4. Iterate permissions:
   a. Check exact match: permission == required
   b. Check wildcard: permission == "*"
   c. Check scoped: permission.startswith(required.split(":")[0])
5. Return True if any match, else False
6. If False → raise PermissionDenied
```

---

## Error Response

```json
{
  "error": "permission_denied",
  "detail": "Actor lacks 'ticket.delete' on board PH",
  "permission": "ticket.delete",
  "board_id": "c1592aad-9466-4be9-9c0b-75f41ac3efac"
}
```
