"""ProjectHub exception hierarchy and FastAPI handler."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ProjectHubError(Exception):
    """Base application error."""

    code = "internal_error"
    status = 500

    def __init__(self, message: str = "Internal error") -> None:
        super().__init__(message)
        self.message = message


class PermissionDenied(ProjectHubError):
    code = "permission_denied"
    status = 403

    def __init__(self, required: str, have: list[str]) -> None:
        super().__init__("Permission denied")
        self.required = required
        self.have = have


class NotFound(ProjectHubError):
    code = "not_found"
    status = 404

    def __init__(self, resource: str) -> None:
        super().__init__(f"{resource} not found")
        self.resource = resource


class InvalidTransition(ProjectHubError):
    code = "invalid_transition"
    status = 422

    def __init__(self, from_state: str, to_state: str, allowed: list[str]) -> None:
        super().__init__("Invalid workflow transition")
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed


class AlreadyClaimed(ProjectHubError):
    code = "already_claimed"
    status = 409

    def __init__(self, claimed_by: str, since: str) -> None:
        super().__init__("Ticket already claimed")
        self.claimed_by = claimed_by
        self.since = since


class FieldGateNotMet(ProjectHubError):
    code = "field_gate_not_met"
    status = 422

    def __init__(self, transition: str, missing_fields: list[str]) -> None:
        super().__init__("Required fields missing for transition")
        self.transition = transition
        self.missing_fields = missing_fields


class WorkflowDeletionBlocked(ProjectHubError):
    """Raised when a workflow cannot be deleted due to a safety guard."""

    code = "workflow_deletion_blocked"
    status = 400

    def __init__(self, reason: str, workflow_id: str, detail: str = "") -> None:
        super().__init__(detail or f"Cannot delete workflow: {reason}")
        self.reason = reason
        self.workflow_id = workflow_id


class StateDeletionBlocked(ProjectHubError):
    """Raised when a workflow state cannot be deleted due to a safety guard.

    PH-106: reasons are 'tickets_exist' or 'last_state'.
    """

    code = "state_deletion_blocked"
    status = 400

    def __init__(
        self,
        reason: str,
        state_name: str,
        ticket_count: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(detail or f"Cannot delete state: {reason}")
        self.reason = reason
        self.state_name = state_name
        self.ticket_count = ticket_count


class UnknownRoleError(ProjectHubError):
    """Raised when a membership role is not defined in board.roles.

    PH-39: maps to 422.
    """

    code = "unknown_role"
    status = 422

    def __init__(self, role: str) -> None:
        super().__init__(f"role '{role}' not in board.roles")
        self.role = role


class LastAdminError(ProjectHubError):
    """Raised when an operation would remove/demote the last admin on a board.

    PH-39: maps to 409.
    """

    code = "last_admin"
    status = 409

    def __init__(self) -> None:
        super().__init__("cannot remove/demote the last admin")


class DuplicateMembershipError(ProjectHubError):
    """Raised when a (board, actor) membership pair already exists.

    PH-39: maps to 409.
    """

    code = "duplicate_membership"
    status = 409

    def __init__(self) -> None:
        super().__init__("actor is already a member of this board")


class RepoNotConfigured(ProjectHubError):
    """Raised when a board exists but has no Repository row attached.

    G4: read endpoints require a configured repo to serve data; returning
    empty arrays would be ambiguous (sync not yet run vs. no repo at all).
    The 409 signals a configuration prerequisite rather than a conflict,
    mirroring ``AlreadyClaimed`` which also uses 409 for a state mismatch.
    """

    code = "repo_not_configured"
    status = 409

    def __init__(self) -> None:
        super().__init__("No repository configured for this board")


class Conflict(ProjectHubError):
    """Raised when a write would collide with existing state — maps to 409.

    PH-254: a per-repo SonarQube setup whose effective ``project_key`` is already
    held by ANOTHER repo of the SAME board is rejected (two repos pointing at one
    Sonar project would produce two cards backed by one project_key on the
    ``(board_id, repo_id)`` metric model). ``conflicting_repo`` names the sibling
    so the caller's 409 message is actionable. Cross-board reuse is NOT a conflict
    (boards are independent Sonar surfaces).
    """

    code = "conflict"
    status = 409

    def __init__(self, message: str, conflicting_repo: str | None = None) -> None:
        super().__init__(message)
        self.conflicting_repo = conflicting_repo


class InvalidConceptLink(ProjectHubError):
    """Raised when a concept-tag link violates an app-layer guard — maps to 422.

    PH-273: the directed ``ConceptTagLink`` self-loop guard (``source == target``)
    is enforced in the service layer, NOT the DB (core.py:612-613 deferred it from
    PH-272). A duplicate ``(source, target)`` pair is a ``Conflict`` (409) instead;
    this 422 is reserved for the structurally-invalid self-loop.
    """

    code = "invalid_concept_link"
    status = 422

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _error_payload(exc: ProjectHubError) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": exc.code, "message": exc.message}
    for attr in (
        "required",
        "have",
        "allowed",
        "from_state",
        "to_state",
        "claimed_by",
        "since",
        "resource",
        "transition",
        "missing_fields",
        "reason",
        "workflow_id",
        "state_name",
        "ticket_count",
        "role",
        "conflicting_repo",
    ):
        if hasattr(exc, attr):
            payload[attr] = getattr(exc, attr)
    return payload


async def projecthub_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, ProjectHubError):
        return JSONResponse(status_code=exc.status, content=_error_payload(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Internal error"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProjectHubError, projecthub_error_handler)
