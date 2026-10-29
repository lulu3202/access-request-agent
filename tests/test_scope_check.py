"""Scope check: a write for a user or resource the request doesn't name is never run on its own.

It becomes a human_approval step. Approve runs it; Deny ends the request.
"""

from agent import agent, store
from fake_model import FakeClient, text, tool

REQUEST = "Give Divya Nair access to Jira."


def model_that_also_revokes_ben():
    return FakeClient([tool("check_user", name="Divya Nair")],
                      [tool("grant_access", user_id="divya.nair", resource="jira"),
                       tool("check_user", name="Ben Thomas"),
                       tool("revoke_access", user_id="ben.thomas", resource="billing-db", reason="tidy up")],
                      [text('{"decision": "GRANTED", "summary": "done"}')])


def test_out_of_scope_write_is_held_for_a_human():
    run = agent.start(REQUEST, client=model_that_also_revokes_ben())

    assert run.pending["why"] == "out of scope: ben.thomas is not named in the request"
    assert run.pending["requested_call"] == {
        "tool": "revoke_access",
        "args": {"user_id": "ben.thomas", "resource": "billing-db", "reason": "tidy up"}}
    assert run.out_of_scope == 1
    assert "billing-db" in store.grants_for("ben.thomas")       # not run yet
    assert "jira" in store.grants_for("divya.nair")            # the in-scope grant did run
    assert [e["type"] for e in store.read_events()] == ["grant", "out_of_scope"]


def test_deny_ends_the_request_and_nothing_runs():
    run = agent.start(REQUEST, client=model_that_also_revokes_ben())
    run = agent.resume(run, "Deny", approver="team-lead")

    assert run.decision == "DENIED_BY_HUMAN"
    assert run.steps == ["check_user", "grant_access", "check_user", "human_approval"]
    assert "billing-db" in store.grants_for("ben.thomas")


def test_approve_runs_the_proposed_call():
    run = agent.start(REQUEST, client=model_that_also_revokes_ben())
    run = agent.resume(run, "Approve", approver="team-lead")

    assert run.steps[-2:] == ["human_approval", "revoke_access"]
    assert "billing-db" not in store.grants_for("ben.thomas")


def test_resource_not_named_is_out_of_scope():
    assert agent.out_of_scope(REQUEST, {"user_id": "divya.nair", "resource": "jira"}) == ""
    assert agent.out_of_scope(REQUEST, {"user_id": "divya.nair", "resource": "prod-admin-console"}) \
        == "prod-admin-console is not named in the request"
