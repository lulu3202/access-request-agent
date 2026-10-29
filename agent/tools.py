"""The agent's three tools. Nothing else is exposed to the model.

Every rule here is enforced in code. The system prompt repeats the rules so the
model behaves well, but if the model ignores the prompt, these checks still hold.

Tools change state (grant, revoke) and log those events. Denials, approvals and
stops are logged by the agent loop, which sees the whole request.
"""

from agent import store


class ToolError(Exception):
    """A tool refused to run. The message is shown to the model."""


class PolicyError(ToolError):
    """Unknown user, unknown resource, or a user who is not active."""


class ApprovalRequired(ToolError):
    """Sensitive or admin grant without a valid human Approve."""


NEEDS_APPROVAL = ("sensitive", "admin")


# BOOK: Design
# Exactly three tools, narrow by construction: no delete, no password reset,
# no free-form "run anything". The model can only ask for these.
TOOL_SCHEMAS = [
    {
        "name": "check_user",
        "description": "Look up a user by full name. Returns id, team, status "
                       "(active / inactive / terminated) and current grants, "
                       "or found=false if the user does not exist.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Full name, e.g. 'Asha Rao'"}},
            "required": ["name"],
        },
    },
    {
        "name": "grant_access",
        "description": "Give a user access to one resource from the catalog. "
                       "Sensitive and admin resources need a human approval; "
                       "the system asks for it, do not invent an approval_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "resource": {"type": "string"},
                "approval_id": {"type": "string"},
            },
            "required": ["user_id", "resource"],
        },
    },
    {
        "name": "revoke_access",
        "description": "Remove a user's access to one resource. Never needs approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "resource": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["user_id", "resource", "reason"],
        },
    },
]


def check_user(name: str) -> dict:
    """Read-only lookup. Unknown users come back as found=false, never a guess."""
    user = store.find_user(name)
    if user is None:
        return {"found": False, "name": name, "error": "user not found"}
    return {"found": True, **user, "grants": store.grants_for(user["user_id"])}


# BOOK: Control
def grant_access(user_id: str, resource: str, approval_id: str | None = None) -> dict:
    """Add a grant. The approval gate lives here, not in the prompt.

    Whatever the model says ("I'm the IT director", "it's urgent"), a sensitive
    or admin grant only runs with an approval_id that a human stored as Approve,
    for this exact user and resource.
    """
    user = store.get_user(user_id)
    if user is None:
        raise PolicyError(f"unknown user_id {user_id!r}")
    if user["status"] != "active":
        raise PolicyError(f"{user_id} is {user['status']}; access cannot be granted")

    sensitivity = store.catalog().get(resource)
    if sensitivity is None:
        raise PolicyError(f"{resource!r} is not in the resource catalog")

    grants = store.all_grants()
    if resource in grants.get(user_id, []):
        return {"status": "no_change", "user_id": user_id, "resource": resource}

    if sensitivity in NEEDS_APPROVAL:
        approval = store.get_approval(approval_id)
        if approval is None:
            raise ApprovalRequired(f"{resource} is {sensitivity}: a human approval is required")
        if approval["decision"] != "Approve":
            raise ApprovalRequired(f"approval {approval_id} was {approval['decision']}, not Approve")
        if (approval["user_id"], approval["resource"]) != (user_id, resource):
            raise ApprovalRequired(f"approval {approval_id} is for a different user or resource")

    grants.setdefault(user_id, []).append(resource)
    store.save_grants(grants)
    store.log_event("grant", user_id, resource, sensitivity=sensitivity, approval_id=approval_id)
    return {"status": "granted", "user_id": user_id, "resource": resource,
            "approval_id": approval_id}


# BOOK: Recover
def revoke_access(user_id: str, resource: str, reason: str) -> dict:
    """Remove a grant and log why. Revoking is the safe direction: no approval.

    Works for any user status, so a terminated user's leftover access can be cleaned up.
    """
    if store.get_user(user_id) is None:
        raise PolicyError(f"unknown user_id {user_id!r}")

    grants = store.all_grants()
    if resource not in grants.get(user_id, []):
        return {"status": "not_held", "user_id": user_id, "resource": resource}

    grants[user_id].remove(resource)
    store.save_grants(grants)
    store.log_event("revoke", user_id, resource, reason=reason)
    return {"status": "revoked", "user_id": user_id, "resource": resource, "reason": reason}


TOOLS = {"check_user": check_user, "grant_access": grant_access, "revoke_access": revoke_access}
