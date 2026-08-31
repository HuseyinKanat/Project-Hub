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

    def __init__(
        self,
        required: str,
        have: list[str],
        *,
        reason: str | None = None,
        actor_id: str | None = None,
        assignee_id: str | None = None,
        claimed_by: str | None = None,
    ) -> None:
        super().__init__("Permission denied")
        self.required = required
        self.have = have
        # PH-340 (AC-1): ownership-failure diagnostics. Populated ONLY when the
        # denial is an if_assignee ownership miss (require_permission passes
        # reason="not_owner"). For every OTHER 403 these attributes stay UNSET, so
        # hasattr(exc, "reason") is False and the REST/MCP payload is byte-identical
        # to the pre-PH-340 shape (backward compat — see _error_payload /
        # mcp/server.py:_domain_error_detail, which already allowlist reason+claimed_by).
        if reason is not None:
            self.reason = reason
            self.actor_id = actor_id
            self.assignee_id = assignee_id
            self.claimed_by = claimed_by


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


class PayloadTooLarge(ProjectHubError):
    """Raised when an attachment exceeds the configured byte cap — maps to 413.

    PH-296: streamed writes count bytes as they arrive; the moment the running
    total passes ``attachment_max_bytes`` the partial file is deleted and this is
    raised. ``limit`` echoes the cap so the caller's message is actionable.
    """

    code = "payload_too_large"
    status = 413

    def __init__(self, limit: int) -> None:
        super().__init__(f"Attachment exceeds the maximum size of {limit} bytes")
        self.limit = limit


class UnsupportedMediaType(ProjectHubError):
    """Raised when an attachment's content type is not in the allowlist — 415.

    PH-296: the normalized content type must be a member of
    ``attachment_allowed_types``; ``content_type`` names the rejected value.
    """

    code = "unsupported_media_type"
    status = 415

    def __init__(self, content_type: str) -> None:
        super().__init__(f"Content type '{content_type}' is not allowed")
        self.content_type = content_type


class AttachmentSourceInvalid(ProjectHubError):
    """Raised when an MCP ingest ``source_path`` is unusable — maps to 422.

    PH-296: the zero-copy ingest path validates the host ``source_path`` against
    the read-only ``/repos`` mount (absolute, no ``..`` traversal, under the
    mount root, resolves to a real file). Any failure surfaces here so agents get
    a structured 422 instead of a 500.
    """

    code = "attachment_source_invalid"
    status = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class AttachmentPhaseInvalid(ProjectHubError):
    """Raised when an attachment ``phase`` tag is not a valid slug — maps to 422.

    PH-311: ``phase`` labels the workflow phase/iteration an evidence file belongs
    to. Only its SHAPE is enforced (like ``kind`` — a free slug, not a closed
    enum): ``^[a-z0-9]+(?:-[a-z0-9]+)*$`` with ``<= 40`` chars. ``None``/omitted is
    valid (passthrough). Validation runs at the top of ``_persist_attachment``
    BEFORE any blob/row/event is created, so a rejected phase never leaves a
    side effect. ``phase`` echoes the rejected value so the caller's 422 (REST) /
    isError (MCP) detail is actionable.
    """

    code = "attachment_phase_invalid"
    status = 422

    def __init__(self, detail: str, phase: str | None = None) -> None:
        super().__init__(detail)
        self.phase = phase


class AttachmentMetadataInvalid(ProjectHubError):
    """Raised when an attachment metadata update field is invalid — maps to 422.

    PH-313: ``update_attachment`` mutates ONLY the metadata columns (phase / kind
    / run_id); the blob bytes are immutable. This guards the non-phase fields
    (``phase`` keeps its own :class:`AttachmentPhaseInvalid` via the shared
    ``_validate_phase``): an EMPTY ``fields`` map, an UNKNOWN key, a non-string
    ``kind`` (the column is NOT nullable), or a ``kind`` / ``run_id`` value wider
    than its column (``kind`` VARCHAR(20), ``run_id`` VARCHAR(120)) is rejected
    with a clean 422 BEFORE the row is touched — never a DB-level 500. Validation
    runs before any mutation, so a rejected update leaves no side effect.
    ``field`` names the offending key (``None`` for an empty map) so the caller's
    422 (REST) / isError (MCP) detail is actionable.
    """

    code = "attachment_metadata_invalid"
    status = 422

    def __init__(self, detail: str, field: str | None = None) -> None:
        super().__init__(detail)
        self.field = field


class AttachmentContentInvalid(ProjectHubError):
    """Raised when inline base64 attachment content is undecodable — maps to 422.

    PH-318: ``add_attachment_content`` ships bytes INLINE as base64 (a REMOTE agent
    off the hub host, whose evidence is not under the read-only ``/repos`` mount).
    If ``base64.b64decode(..., validate=True)`` rejects the payload — bad padding, a
    non-alphabet character, embedded whitespace/newlines (agents must send UNWRAPPED
    base64), or a non-ASCII string — this surfaces as a clean 422 instead of a
    server 500. Raised AFTER the authorize + pre-decode size gate but BEFORE any
    blob/row/event, so a malformed payload leaves no side effect. Message-only (no
    extra attributes) — flows through ``_error_payload`` / ``_domain_error_detail``
    with just ``code`` + ``message``.
    """

    code = "attachment_content_invalid"
    status = 422


class AttachmentUploadInvalid(ProjectHubError):
    """Raised when a CHUNKED upload session step is out of order/state — maps to 409.

    PH-341: the ``add_attachment_begin`` / ``add_attachment_chunk`` /
    ``add_attachment_commit`` protocol streams a remote agent's large evidence
    (> the 8 MiB inline cap, up to the 25 MiB disk cap) as a sequence of chunks.
    This is the state-machine guard distinct from the size (413) / type (415) /
    base64 (422) / not-found (404) / permission (403) errors: a ``seq`` that is not
    the session's expected ``next_seq`` (a gap or a duplicate/reordered chunk), a
    ``commit`` on a session that has received NO chunks (empty), or a client-supplied
    ``sha256`` that does not match the staged bytes. ``expected_seq`` echoes the seq
    the server is waiting for so an agent can resume deterministically (``None`` for
    the empty-commit / checksum-mismatch cases). Flows through ``_error_payload`` /
    ``_domain_error_detail`` (both allowlist ``expected_seq``).
    """

    code = "attachment_upload_invalid"
    status = 409

    def __init__(self, detail: str, expected_seq: int | None = None) -> None:
        super().__init__(detail)
        self.expected_seq = expected_seq


class OwnerUnresolved(ProjectHubError):
    """Raised when a caller's owner cannot be resolved from its token — maps to 422.

    PH-322: the per-owner project-path registry is keyed on ``owner_slug``, resolved
    from the CALLING token — an agent's from its ``jarwis-<role>@<owner>`` display_name
    suffix, a human's from ``actor.owner_slug``. When NEITHER yields a slug (a human who
    has not set ``owner_slug`` yet, or an un-namespaced agent) there is no identity to
    write/read a path under, so ``set``/``get`` raise this BEFORE any row is touched —
    NOT a 403 (which means "you're not on this board"). Message-only (no extra
    attributes) — flows through ``_error_payload`` / ``_domain_error_detail`` with just
    ``code`` + ``message``.
    """

    code = "owner_unresolved"
    status = 422

    def __init__(self, message: str = "owner tanimsiz - profilden ayarla") -> None:
        super().__init__(message)


class ProfileFieldInvalid(ProjectHubError):
    """Raised when a profile write field fails validation — maps to 422.

    PH-322: guards the two profile write fields BEFORE any row is touched (so a
    rejected write leaves no side effect) — ``owner_slug`` that fails the
    ``^[a-z0-9][a-z0-9-]{0,19}$`` slug shape, or a ``local_path`` wider than its
    255-char column. ``field`` names the offending key so the caller's 422 (REST) /
    isError (MCP) detail is actionable. ``field`` is ALREADY in both
    ``_error_payload`` and ``_domain_error_detail`` attr lists (shared with
    ``AttachmentMetadataInvalid``) → no handler edits needed.
    """

    code = "profile_field_invalid"
    status = 422

    def __init__(self, detail: str, field: str | None = None) -> None:
        super().__init__(detail)
        self.field = field


# PH-281: InvalidConceptLink (422 concept-tag self-loop guard) was removed with
# the ConceptTag user-facing surface — its only raiser (services/concept_tags)
# is gone.


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
        # PH-340 (AC-1): ownership-failure diagnostics (REST full set). claimed_by is
        # already listed above; actor_id/assignee_id are intentionally REST-only —
        # MCP's _domain_error_detail keeps reason+claimed_by (technical_depth A4:
        # avoids editing mcp/server.py, which PH-341 also touches).
        "actor_id",
        "assignee_id",
        "workflow_id",
        "state_name",
        "ticket_count",
        "role",
        "conflicting_repo",
        "limit",
        "content_type",
        "phase",
        "field",
        # PH-341: chunked-upload seq-gap recovery hint (AttachmentUploadInvalid).
        "expected_seq",
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
