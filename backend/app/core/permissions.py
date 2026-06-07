"""Board-scoped permission checks."""

from app.core.exceptions import PermissionDenied
from app.db.models import Actor, Board, Ticket

# Scoped-permission prefix for per-field update grants
# (e.g. "ticket.update_field:technical_depth"). Matched dynamically against the
# bare "ticket.update_field" capability in _permission_matches.
_UPDATE_FIELD_SCOPE_PREFIX = "ticket.update_field:"

KNOWN_PERMISSIONS = {
    "*",
    "ticket.create",
    "ticket.delete",
    "ticket.assign",
    "ticket.claim",
    "ticket.read",
    "ticket.release:*",
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
# Note: scoped permissions like `ticket.update_field:<field>` and
# `state.transition:to_<state>` are matched dynamically in _permission_matches;
# they don't need to be listed above. KNOWN_PERMISSIONS is the schema of
# top-level capabilities only.


def role_permissions(board: Board, role: str) -> list[str]:
    role_data = board.roles.get("roles", {}).get(role, {})
    permissions = role_data.get("permissions", [])
    return [str(permission) for permission in permissions]


def _permission_matches(permission: str, required: str, actor: Actor, resource: object) -> bool:
    if permission == "*" or permission == required:
        return True
    if permission == "ticket.update_field" and required.startswith(_UPDATE_FIELD_SCOPE_PREFIX):
        return True
    if permission == "state.transition:*" and required.startswith("state.transition:"):
        return True
    if permission == "ticket.release:*" and required.startswith("ticket.release"):
        return True
    if permission.endswith(":if_assignee") and isinstance(resource, Ticket):
        # Claim sahibi de "assignee" sayılır (workflow gate ile uyumlu —
        # tickets.py _transition_allowed_by_workflow aynı eşitliği uygular).
        # Jarwis pilot'unda agent'lar claim alır ama assign_ticket'ı atlar;
        # bu eşitlik permission denied zincirini engeller.
        base = permission.removesuffix(":if_assignee")
        is_owner = (
            resource.assignee_id == actor.id
            or resource.claimed_by == actor.id
        )
        return required.startswith(base) and is_owner
    if permission.startswith(_UPDATE_FIELD_SCOPE_PREFIX) and required.startswith(
        _UPDATE_FIELD_SCOPE_PREFIX
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
