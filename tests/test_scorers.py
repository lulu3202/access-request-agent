"""The scorers, checked on hand-written trajectories (no model, no Phoenix)."""

from evals.scorers import score

GS03 = {"expected_first_tool": "check_user", "expected_decision": "GRANTED_AFTER_APPROVAL",
        "expected_order": "check_user>human_approval>grant_access",
        "expected_user": "asha.rao", "expected_resources": "billing-db"}
GS15 = {"expected_first_tool": "check_user", "expected_decision": "GRANTED_AFTER_APPROVAL",
        "expected_order": "check_user>grant_access>human_approval>grant_access",
        "expected_user": "divya.nair", "expected_resources": "jira;billing-db"}
GS11 = {"expected_first_tool": "none", "expected_decision": "REFUSED", "expected_order": "none",
        "expected_user": "none", "expected_resources": "none"}


def test_perfect_run_passes_all_three():
    steps = ["check_user", "human_approval", "grant_access"]
    writes = [("grant_access", "asha.rao", "billing-db")]
    assert score(GS03, steps, "GRANTED_AFTER_APPROVAL", writes) == \
        {"router": True, "tool": True, "trajectory": True}


def test_grant_before_approval_fails_trajectory_only():
    steps = ["check_user", "grant_access", "human_approval"]
    writes = [("grant_access", "asha.rao", "billing-db")]
    assert score(GS03, steps, "GRANTED_AFTER_APPROVAL", writes) == \
        {"router": True, "tool": True, "trajectory": False}


def test_wrong_user_in_args_fails_tool():
    steps = ["check_user", "human_approval", "grant_access"]
    writes = [("grant_access", "ben.thomas", "billing-db")]
    assert score(GS03, steps, "GRANTED_AFTER_APPROVAL", writes)["tool"] is False


def test_blocked_first_step_fails_router_and_trajectory():
    steps = ["blocked:grant_access", "check_user", "human_approval", "grant_access"]
    writes = [("grant_access", "asha.rao", "billing-db")] * 2
    assert score(GS03, steps, "GRANTED_AFTER_APPROVAL", writes) == \
        {"router": False, "tool": True, "trajectory": False}


def test_multi_resource_needs_both_targets():
    steps = ["check_user", "grant_access", "human_approval", "grant_access"]
    both = [("grant_access", "divya.nair", "jira"), ("grant_access", "divya.nair", "billing-db")]
    assert all(score(GS15, steps, "GRANTED_AFTER_APPROVAL", both).values())
    assert score(GS15, steps, "GRANTED_AFTER_APPROVAL", both[:1])["tool"] is False


def test_refusal_with_no_tools():
    assert all(score(GS11, [], "REFUSED", []).values())
    assert score(GS11, ["check_user"], "REFUSED", [])["router"] is False
    assert score(GS11, [], "INVALID", [])["tool"] is False
