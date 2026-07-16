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
    # PH-296: evidence attachment ingest (REST multipart + MCP zero-copy).
    "attachment.add",
    # PH-313: metadata-only update (implementers + qa + pm) and hard delete
    # (qa + pm ONLY — the curators who may destroy evidence).
    "attachment.update",
    "attachment.delete",
    "epic.manage",
    "git.create_branch",
    "git.link_commit",
    "workflow.edit",
    "board.edit",
    # PH-281: the PH-273 tag.read/tag.manage/tag.assign caps were removed with the
    # ConceptTag user-facing surface. graph/search read-auth now uses the EXISTING
    # global ticket.read gate (require_global_permission(actor, "ticket.read", ...))
    # — "holds ticket.read under ANY board membership".
    # PH-287: the same global ticket.read gate now also fronts related_tickets (the
    # cross-board relationship tool). pm + orchestrator were GRANTED ticket.read in
    # defaults.DEFAULT_WEB_ROLES so the Coordinator's pm channel can call it; the
    # stranger-denied invariant is preserved (a board-less actor still lacks the cap
    # under any membership). Existing boards need `update_board_roles` to take it up.
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


def require_global_permission(
    actor: Actor,
    required: str,
    memberships_with_boards: list[tuple[str, Board]],
) -> None:
    """Raise unless ``actor`` holds ``required`` under ANY board membership.

    Some surfaces are cross-board (no single ``board_id`` to gate against), so the
    board-scoped ``require_permission`` cannot guard them. There is no global-admin
    concept in the codebase (admin is always a per-board ``BoardMembership.role``).
    The gate semantics are "holds ``required`` (or the ``*`` wildcard) under the
    role of at least one of the actor's board memberships". PH-281 uses this to
    gate ``/api/graph`` + ``/api/search`` on global ``ticket.read`` (those expose
    cross-board ticket identity); it previously gated the removed ``tag.read``.

    The caller MUST pass ``(role, board)`` pairs with ``board.roles`` already
    eager-loaded (``current_actor`` only selectinloads ``Actor.memberships``, NOT
    ``membership.board``, so a service that needs this gate first loads the
    actor's memberships WITH their boards — see ``services/graph`` /
    ``services/search``). This keeps the gate self-contained and async-safe
    without broadening the blast radius of ``current_actor``.
    """

    have: list[str] = []
    for role, board in memberships_with_boards:
        have.extend(role_permissions(board, role))

    if any(_permission_matches(permission, required, actor, None) for permission in have):
        return

    raise PermissionDenied(required=required, have=sorted(set(have)))
