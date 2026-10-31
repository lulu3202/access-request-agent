"""Rule 3: a grant that did not go through the agent loop is an anomaly."""

from agent import agent, store
from agent.tools import grant_access, revoke_access
from fake_model import FakeClient, text, tool
from operate.monitor import find_anomalies

OUTSIDE = "grant made outside the agent (no requester on record)"


def outside_alerts():
    return [(a["user_id"], a["resource"]) for a in find_anomalies() if a["rule"] == OUTSIDE]


def test_seed_grants_are_not_flagged():
    assert outside_alerts() == []


def test_direct_tool_call_is_flagged():
    grant_access("divya.nair", "jira")                   # a script, not the agent
    assert outside_alerts() == [("divya.nair", "jira")]


def test_grant_through_the_agent_is_not_flagged():
    model = FakeClient([tool("check_user", name="Divya Nair")],
                       [tool("grant_access", user_id="divya.nair", resource="jira")],
                       [text('{"decision": "GRANTED", "summary": "done"}')])
    run = agent.start("Give Divya Nair access to Jira.", client=model)  # no requester given
    assert run.decision == "GRANTED"
    assert outside_alerts() == []
    assert store.read_events()[-1]["requester"] == "unknown"


def test_alert_clears_once_the_grant_is_revoked():
    grant_access("divya.nair", "jira")
    revoke_access("divya.nair", "jira", reason="made outside the agent")
    assert outside_alerts() == []
