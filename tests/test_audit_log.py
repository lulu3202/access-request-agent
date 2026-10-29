"""Resetting state must never erase history."""

from agent import store


def test_reset_data_leaves_audit_log_untouched(fresh_data):
    store.set_run_label("test:audit")
    store.log_event("grant", "divya.nair", "jira")
    store.log_event("revoke", "erik.holm", "jira", reason="test")
    audit = fresh_data / store.AUDIT_LOG
    before = audit.read_bytes()

    store.reset_data()
    store.reset_data("seed_injection")

    assert audit.read_bytes() == before                  # byte for byte
    assert store.read_events() == []                     # state log is cleared
    assert [e["type"] for e in store.read_audit_log()] == ["grant", "revoke"]


def test_every_line_has_timestamp_and_run_label():
    store.set_run_label("test:labels")
    store.log_event("stop", "asha.rao", "wiki")
    (event,) = store.read_audit_log()
    assert event["run_label"] == "test:labels" and event["ts"]
