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
