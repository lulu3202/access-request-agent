"""Emergency stop: with the STOP file present, no tool call runs."""

from agent import agent, store
from fake_model import FakeClient, text, tool


def test_stop_file_refuses_every_tool_call(tmp_path, monkeypatch):
    stop_file = tmp_path / "STOP"
    stop_file.touch()
    monkeypatch.setattr(agent, "STOP_FILE", stop_file)

    model = FakeClient([tool("check_user", name="Divya Nair"),
                        tool("grant_access", user_id="divya.nair", resource="jira")],
                       [text('{"decision": "GRANTED", "summary": "never reached"}')])
    run = agent.start("Give Divya Nair access to Jira.", client=model)

    assert run.decision == "STOPPED"
    assert run.steps == ["stopped"]                      # nothing ran, not even check_user
    assert store.grants_for("divya.nair") == ["wiki"]    # state untouched
    assert [e["type"] for e in store.read_events()] == ["stop"]


def test_stop_mid_task_after_approval(tmp_path, monkeypatch):
    """Paused for a human, an operator hits stop, then the human approves: the grant still never runs."""
    stop_file = tmp_path / "STOP"
    monkeypatch.setattr(agent, "STOP_FILE", stop_file)

    model = FakeClient([tool("check_user", name="Asha Rao")],
                       [tool("grant_access", user_id="asha.rao", resource="billing-db")])
    run = agent.start("Asha Rao needs billing-db access for the quarter close.", client=model)
    assert run.pending is not None

    stop_file.touch()                                     # operator stops the agent mid-task
    run = agent.resume(run, "Approve", approver="team-lead")

    assert run.decision == "STOPPED"
    assert run.steps == ["check_user", "human_approval", "stopped"]
    assert "billing-db" not in store.grants_for("asha.rao")
