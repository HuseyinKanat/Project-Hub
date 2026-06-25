"""Default workflow and role templates from the project plan."""

# Permission identifier constants — referenced across many role definitions in
# DEFAULT_WEB_ROLES. Extracted to module-level to avoid literal duplication.
_PERM_TICKET_CREATE = "ticket.create"
_PERM_TICKET_ASSIGN = "ticket.assign"
_PERM_TICKET_READ = "ticket.read"
_PERM_TICKET_CLAIM = "ticket.claim"
_PERM_COMMENT_ADD = "comment.add"
_PERM_GIT_CREATE_BRANCH = "git.create_branch"
_PERM_UPDATE_FIELD_IF_ASSIGNEE = "ticket.update_field:if_assignee"
_PERM_STATE_TRANSITION_IF_ASSIGNEE = "state.transition:if_assignee"
# PH-281: the PH-273 tag.read / tag.assign / tag.manage caps were removed with the
# ConceptTag user-facing surface. graph/search read-auth now uses the EXISTING
# ticket.read cap (global gate via require_global_permission). No new cap added.

# Implementer roles (backend/frontend/unity/native) share an identical
# permission shape; defined once and reused to keep the registry DRY.
_IMPLEMENTER_PERMISSIONS: list[str] = [
    _PERM_TICKET_READ,
    _PERM_UPDATE_FIELD_IF_ASSIGNEE,
    _PERM_STATE_TRANSITION_IF_ASSIGNEE,
    _PERM_TICKET_ASSIGN,
    _PERM_COMMENT_ADD,
    _PERM_GIT_CREATE_BRANCH,
    _PERM_TICKET_CLAIM,
]

DEFAULT_STATES: list[dict[str, object]] = [
    {
        "name": "backlog",
        "category": "new",
        "color": "gray",
        "is_initial": True,
        "is_terminal": False,
    },
    {
        "name": "to_do",
        "category": "new",
        "color": "blue",
        "is_initial": False,
        "is_terminal": False,
    },
    {
        "name": "in_progress",
        "category": "active",
        "color": "yellow",
        "is_initial": False,
        "is_terminal": False,
    },
    {
        "name": "blocked",
        "category": "active",
        "color": "red",
        "is_initial": False,
        "is_terminal": False,
    },
    {
        "name": "in_review",
        "category": "active",
        "color": "purple",
        "is_initial": False,
        "is_terminal": False,
    },
    {
        "name": "in_test",
        "category": "active",
        "color": "orange",
        "is_initial": False,
        "is_terminal": False,
    },
    {
        "name": "done",
        "category": "done",
        "color": "green",
        "is_initial": False,
        "is_terminal": True,
    },
]

DEFAULT_TRANSITIONS: list[dict[str, object]] = [
    {"from": "backlog", "to": "to_do", "allowed_roles": ["pm", "architect"]},
    {"from": "to_do", "to": "in_progress", "allowed_roles": ["assignee", "pm"]},
    {"from": "in_progress", "to": "blocked", "allowed_roles": ["assignee", "pm"]},
    {"from": "blocked", "to": "in_progress", "allowed_roles": ["assignee", "pm"]},
    {
        "from": "in_progress",
        "to": "in_review",
        "allowed_roles": ["assignee", "pm"],
        "field_gates": {
            "required_fields": ["technical_depth", "acceptance_criteria"],
            "exempt_ticket_types": ["epic"]
        }
    },
    {"from": "in_review", "to": "in_progress", "allowed_roles": ["reviewer", "pm"]},
    {
        "from": "in_review",
        "to": "in_test",
        "allowed_roles": ["assignee", "pm", "qa", "reviewer"],
        "field_gates": {
            "required_fields": ["test_plan"],
            "exempt_ticket_types": ["epic"]
        }
    },
    {"from": "in_test", "to": "in_progress", "allowed_roles": ["qa", "pm"]},
    {
        "from": "in_test",
        "to": "done",
        "allowed_roles": ["qa", "pm"],
        "field_gates": {
            "required_fields": ["impact_analysis"],
            "exempt_ticket_types": ["epic"]
        }
    },
    {"from": "*", "to": "done", "allowed_roles": ["pm", "admin"]},
]

DEFAULT_WEB_ROLES: dict[str, object] = {
    "roles": {
        "admin": {"permissions": ["*"]},
        "pm": {
            "permissions": [
                _PERM_TICKET_CREATE,
                _PERM_TICKET_ASSIGN,
                "ticket.delete",
                "ticket.update_field",
                "epic.manage",
                _PERM_COMMENT_ADD,
                "state.transition:*",
                "ticket.release:*",
            ]
        },
        "architect": {
            "permissions": [
                _PERM_TICKET_CREATE,
                _PERM_TICKET_READ,
                "ticket.update_field",
                _PERM_TICKET_ASSIGN,
                _PERM_COMMENT_ADD,
                "state.transition:to_to_do",
                "state.transition:to_in_review",
            ]
        },
        "frontend_dev": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        "backend_dev": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        "reviewer": {
            "permissions": [
                _PERM_TICKET_READ,
                "ticket.update_field:technical_depth",
                "state.transition:to_in_test",
                "state.transition:to_in_progress",
                _PERM_TICKET_ASSIGN,
                _PERM_COMMENT_ADD,
            ]
        },
        "qa": {
            "permissions": [
                _PERM_TICKET_READ,
                "ticket.update_field:impact_analysis,test_plan,branch_name",
                "state.transition:to_done",
                "state.transition:to_in_progress",
                "state.transition:to_in_review",
                "state.transition:to_in_test",
                _PERM_TICKET_ASSIGN,
                _PERM_TICKET_CLAIM,
                _PERM_COMMENT_ADD,
                _PERM_GIT_CREATE_BRANCH,
            ]
        },
        # --- Unity mode roles (Jarwis modes/unity.md activates these instead
        # of backend_dev + frontend_dev). Same implementer permission shape;
        # the split exists so audit trail tells you whether a change came
        # from gameplay/C# code (unity_dev) or scene/prefab/asset wiring
        # (unity_scene_manager).
        "unity_dev": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        "unity_scene_manager": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        # unity_platform: mobile build pipeline + native-SDK integration +
        # platform config (Jarwis modes/unity.md). Same implementer shape.
        "unity_platform": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        # --- Native SDK/bridge mode roles (Jarwis modes/android.md + modes/ios.md).
        # Single implementer per platform repo; same implementer permission shape
        # as backend_dev/unity_dev. Audit trail tells you the change came from the
        # Android (android_dev) or iOS (ios_dev) native bridge source.
        "android_dev": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        "ios_dev": {
            "permissions": list(_IMPLEMENTER_PERMISSIONS),
        },
        "orchestrator": {
            "permissions": [
                _PERM_TICKET_CREATE,
                _PERM_TICKET_ASSIGN,
                _PERM_COMMENT_ADD,
            ]
        },
    }
}


def initial_state(states: list[dict[str, object]]) -> str:
    for state in states:
        if state.get("is_initial") is True:
            return str(state["name"])
    return "backlog"
