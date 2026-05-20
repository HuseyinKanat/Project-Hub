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
    {"from": "in_review", "to": "in_test", "allowed_roles": ["assignee", "pm", "qa", "reviewer"]},
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
                "ticket.update_field",
                "epic.manage",
                "comment.add",
                "state.transition:*",
            ]
        },
        "architect": {
            "permissions": [
                "ticket.create",
                "ticket.read",
                "ticket.update_field",
                "ticket.assign",
                "comment.add",
                "state.transition:to_to_do",
                "state.transition:to_in_review",
            ]
        },
        "frontend_dev": {
            "permissions": [
                "ticket.read",
                "ticket.update_field:if_assignee",
                "state.transition:if_assignee",
                "ticket.assign",
                "comment.add",
                "git.create_branch",
                "ticket.claim",
            ]
        },
        "backend_dev": {
            "permissions": [
                "ticket.read",
                "ticket.update_field:if_assignee",
                "state.transition:if_assignee",
                "ticket.assign",
                "comment.add",
                "git.create_branch",
                "ticket.claim",
            ]
        },
        "reviewer": {
            "permissions": [
                "ticket.read",
                "ticket.update_field:technical_depth",
                "state.transition:to_in_test",
                "state.transition:to_in_progress",
                "ticket.assign",
                "comment.add",
            ]
        },
        "qa": {
            "permissions": [
                "ticket.read",
                "ticket.update_field:impact_analysis,test_plan,branch_name",
                "state.transition:to_done",
                "state.transition:to_in_progress",
                "state.transition:to_in_review",
                "state.transition:to_in_test",
                "ticket.assign",
                "ticket.claim",
                "comment.add",
                "git.create_branch",
            ]
        },
        # --- Unity mode roles (Jarwis modes/unity.md activates these instead
        # of backend_dev + frontend_dev). Same implementer permission shape;
        # the split exists so audit trail tells you whether a change came
        # from gameplay/C# code (unity_dev) or scene/prefab/asset wiring
        # (unity_scene_manager).
        "unity_dev": {
            "permissions": [
                "ticket.read",
                "ticket.update_field:if_assignee",
                "state.transition:if_assignee",
                "ticket.assign",
                "comment.add",
                "git.create_branch",
                "ticket.claim",
            ]
        },
        "unity_scene_manager": {
            "permissions": [
                "ticket.read",
                "ticket.update_field:if_assignee",
                "state.transition:if_assignee",
                "ticket.assign",
                "comment.add",
                "git.create_branch",
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
