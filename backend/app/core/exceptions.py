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
