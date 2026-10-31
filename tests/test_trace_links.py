"""Who asked and which trace explains it: on the root span and on every audit line."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import agent, store
from fake_model import FakeClient, text, tool


def test_requester_session_and_trace_id_are_linked(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(agent, "tracer", provider.get_tracer("test"))

    model = FakeClient([tool("check_user", name="Asha Rao")],
                       [tool("grant_access", user_id="asha.rao", resource="billing-db")],
                       [text('{"decision": "GRANTED_AFTER_APPROVAL", "summary": "done"}')])
    run = agent.start("Asha Rao needs billing-db access for the quarter close.", client=model,
                      requester="devi", session_id="ui-test")
    run = agent.resume(run, "Approve", approver="team-lead")
    assert run.decision == "GRANTED_AFTER_APPROVAL"

    (root,) = [s for s in exporter.get_finished_spans() if s.name == "access_request"]
    assert root.attributes["user.id"] == "devi"
    assert root.attributes["session.id"] == "ui-test"

    trace_id = format(root.context.trace_id, "032x")
    events = store.read_audit_log()
    assert [e["type"] for e in events] == ["approval", "grant"]
    assert all(e["requester"] == "devi" and e["trace_id"] == trace_id for e in events)


def test_untraced_events_stay_as_before():
    store.log_event("revoke", "erik.holm", "jira", reason="drill")
    (event,) = store.read_audit_log()
    assert "requester" not in event and "trace_id" not in event
