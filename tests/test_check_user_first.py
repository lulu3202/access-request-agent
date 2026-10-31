"""check_user first: a write for a user not looked up in this request never runs."""

from agent import agent, store
from fake_model import FakeClient, text, tool


def test_write_without_lookup_is_blocked():
    model = FakeClient([tool("grant_access", user_id="divya.nair", resource="jira")],
                       [text('{"decision": "GRANTED", "summary": "done"}')])
    run = agent.start("Give Divya Nair access to Jira.", client=model)

    assert run.steps == ["blocked:grant_access"]
    assert run.blocked == 1
    assert run.decision == "INVALID"                     # claimed a grant that never ran
    assert store.grants_for("divya.nair") == ["wiki"]


def test_write_after_lookup_runs():
    model = FakeClient([tool("check_user", name="Divya Nair")],
                       [tool("grant_access", user_id="divya.nair", resource="jira")],
                       [text('{"decision": "GRANTED", "summary": "done"}')])
    run = agent.start("Give Divya Nair access to Jira.", client=model)

    assert run.steps == ["check_user", "grant_access"]
    assert run.blocked == 0
    assert run.decision == "GRANTED"
    assert "jira" in store.grants_for("divya.nair")
