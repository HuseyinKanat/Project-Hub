"""Default workflow and role templates from the project plan."""

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
    {"from": "in_progress", "to": "in_review", "allowed_roles": ["assignee"]},
    {"from": "in_review", "to": "in_progress", "allowed_roles": ["reviewer", "pm"]},
    {"from": "in_review", "to": "in_test", "allowed_roles": ["assignee", "pm", "qa"]},
    {"from": "in_test", "to": "in_progress", "allowed_roles": ["qa", "pm"]},
    {"from": "in_test", "to": "done", "allowed_roles": ["qa", "pm"]},
    {"from": "*", "to": "done", "allowed_roles": ["pm", "admin"]},
]

DEFAULT_WEB_ROLES: dict[str, object] = {
    "roles": {
        "admin": {"permissions": ["*"]},
        "pm": {
            "permissions": [
                "ticket.create",
                "ticket.assign",
                "ticket.delete",
                "epic.manage",
                "comment.add",
                "state.transition:*",
            ]
        },
        "architect": {
            "permissions": [
                "ticket.create",
                "ticket.update_field",
                "comment.add",
                "state.transition:to_in_review",
            ]
        },
        "frontend_dev": {
            "permissions": [
                "ticket.update_field:if_assignee",
                "state.transition:if_assignee",
                "comment.add",
                "git.create_branch",
                "ticket.claim",
            ]
        },
        "backend_dev": {
            "permissions": [
                "ticket.update_field:if_assignee",
                "state.transition:if_assignee",
                "comment.add",
                "git.create_branch",
                "ticket.claim",
            ]
        },
        "qa": {
            "permissions": [
                "ticket.update_field:impact_analysis,test_plan",
                "state.transition:to_done",
                "comment.add",
                "ticket.claim",
            ]
        },
        "orchestrator": {"permissions": ["ticket.create", "ticket.assign", "comment.add"]},
    }
}


def initial_state(states: list[dict[str, object]]) -> str:
    for state in states:
        if state.get("is_initial") is True:
            return str(state["name"])
    return "backlog"
