"""Board-scoped permission checks."""

from app.core.exceptions import PermissionDenied
from app.db.models import Actor, Board, Ticket

KNOWN_PERMISSIONS = {
    "*",
    "ticket.create",
    "ticket.delete",
    "ticket.assign",
    "ticket.claim",
    "ticket.update_field",
    "state.transition:*",
    "state.transition:if_assignee",
    "comment.add",
    "epic.manage",
    "git.create_branch",
    "git.link_commit",
    "workflow.edit",
    "board.edit",
}


def role_permissions(board: Board, role: str) -> list[str]:
    role_data = board.roles.get("roles", {}).get(role, {})
    permissions = role_data.get("permissions", [])
    return [str(permission) for permission in permissions]


def _permission_matches(permission: str, required: str, actor: Actor, resource: object) -> bool:
    if permission == "*" or permission == required:
        return True
    if permission == "ticket.update_field" and required.startswith("ticket.update_field:"):
        return True
    if permission == "state.transition:*" and required.startswith("state.transition:"):
        return True
    if permission.endswith(":if_assignee") and isinstance(resource, Ticket):
        base = permission.removesuffix(":if_assignee")
        return required.startswith(base) and resource.assignee_id == actor.id
    if permission.startswith("ticket.update_field:") and required.startswith(
        "ticket.update_field:"
    ):
        allowed_fields = set(permission.split(":", 1)[1].split(","))
        requested_field = required.split(":", 1)[1]
        return requested_field in allowed_fields
    return False


def require_permission(
    actor: Actor,
    board: Board,
    required: str,
    resource: object | None = None,
) -> None:
    """Raise if actor does not have a required permission on a board."""

    memberships = [
        membership for membership in actor.memberships if membership.board_id == board.id
    ]
    have: list[str] = []
    for membership in memberships:
        have.extend(role_permissions(board, membership.role))

    target = resource if resource is not None else board
    if any(_permission_matches(permission, required, actor, target) for permission in have):
        return

    raise PermissionDenied(required=required, have=sorted(set(have)))
