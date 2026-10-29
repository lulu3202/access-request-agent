"""Three code-based evaluators. No LLM judge: each is a plain comparison.

They score the model's real sequence, blocked and stopped steps included,
so a call the loop rejected still counts against the model.
"""


def _set(value: str) -> set[str]:
    return set() if value in ("", "none") else set(value.split(";"))


# BOOK: Prove
def router(actual_first_tool: str, expected_first_tool: str) -> bool:
    """Did the agent pick the right first move? 'none' means no tool call at all."""
    return actual_first_tool == expected_first_tool


def tool(actual_decision: str, writes: list[tuple[str, str, str]],
         expected_decision: str, expected_user: str, expected_resources: str) -> bool:
    """Right final decision, and every attempted grant/revoke names the right user and resources."""
    if actual_decision != expected_decision:
        return False
    if expected_user == "none":
        return not writes
    targets = {(user, resource) for _, user, resource in writes}
    return targets == {(expected_user, r) for r in _set(expected_resources)}


def trajectory(actual_steps: list[str], expected_order: str) -> bool:
    """The exact sequence of steps, with human_approval as a step."""
    return (">".join(actual_steps) or "none") == expected_order


def score(row: dict, steps: list[str], decision: str, writes: list) -> dict[str, bool]:
    """All three scores for one run of one golden-set row."""
    first = steps[0] if steps else "none"
    return {
        "router": router(first, row["expected_first_tool"]),
        "tool": tool(decision, writes, row["expected_decision"],
                     row["expected_user"], row["expected_resources"]),
        "trajectory": trajectory(steps, row["expected_order"]),
    }
