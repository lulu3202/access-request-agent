"""Watch the agent over time: daily event counts and two anomaly rules.

    python operate/monitor.py

Reads the append-only data/audit_log.jsonl (history across resets) and the
live grants, users and approvals. It exits 1 if it
finds an anomaly, so it can run on a schedule and alert.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import store  # noqa: E402
from agent.tools import NEEDS_APPROVAL  # noqa: E402

EVENT_TYPES = ["grant", "revoke", "approval", "denial", "out_of_scope", "stop"]


def daily_counts() -> dict[str, Counter]:
    """day -> Counter of event types, from the audit log (survives resets)."""
    days: dict[str, Counter] = {}
    for event in store.read_audit_log():
        days.setdefault(event["ts"][:10], Counter())[event["type"]] += 1
    return dict(sorted(days.items()))


def counts_by_run_label() -> Counter:
    return Counter(e.get("run_label", "?") for e in store.read_audit_log())


# BOOK: Operate
def find_anomalies() -> list[dict]:
    """Two readable rules over the live state. Each alert names the user and resource."""
    alerts = []
    catalog = store.catalog()
    approved = {(a["user_id"], a["resource"]) for a in store.all_approvals().values()
                if a["decision"] == "Approve"}

    for user_id, resources in store.all_grants().items():
        user = store.get_user(user_id) or {"status": "unknown"}
        for resource in resources:
            # Rule 1: access held by someone who should have none.
            if user["status"] != "active":
                alerts.append({"rule": f"{user['status']} user still has access",
                               "user_id": user_id, "resource": resource})
            # Rule 2 (invariant): sensitive/admin access needs a human Approve on record.
            if catalog.get(resource) in NEEDS_APPROVAL and (user_id, resource) not in approved:
                alerts.append({"rule": f"{catalog[resource]} grant with no matching Approve",
                               "user_id": user_id, "resource": resource})
    return alerts


def print_report() -> list[dict]:
    days = daily_counts()
    print("Events per day (audit_log.jsonl)")
    print(f"  {'day':10} " + " ".join(f"{t:>12}" for t in EVENT_TYPES))
    for day, counts in days.items():
        print(f"  {day:10} " + " ".join(f"{counts.get(t, 0):>12}" for t in EVENT_TYPES))
    if not days:
        print("  (no events yet)")
    labels = counts_by_run_label()
    if labels:
        print("\nEvents by run label")
        for label, n in labels.most_common():
            print(f"  {label:40} {n:>6}")

    alerts = find_anomalies()
    print(f"\nAnomalies ({len(alerts)})" if alerts else "\nAnomalies: none")
    for a in alerts:
        print(f"  ALERT  {a['rule']}: {a['user_id']} -> {a['resource']}")
    return alerts


if __name__ == "__main__":
    sys.exit(1 if print_report() else 0)
