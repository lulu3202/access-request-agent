"""Recover drill: start from a real monitor alert, fix it, prove the alert is gone.

    python drills/recover_drill.py

1. Reset to the seed and run the monitor. It flags Erik Holm (terminated) -> jira.
2. Revoke that grant with revoke_access; the reason is logged.
3. Run the monitor again. PASS if the alert is gone and nothing else changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import store  # noqa: E402
from agent.tools import revoke_access  # noqa: E402
from operate.monitor import find_anomalies  # noqa: E402

TARGET_RULE = "terminated user still has access"


def show_grants(title: str) -> None:
    print(title)
    for user_id, resources in store.all_grants().items():
        status = store.get_user(user_id)["status"]
        print(f"  {user_id:11} {status:10} {', '.join(resources) or '-'}")


# BOOK: Recover
def main() -> bool:
    store.set_run_label("drill:recover")
    store.reset_data()

    # 1. Detect: the monitor, not a person, finds the problem.
    alerts = find_anomalies()
    alert = next((a for a in alerts if a["rule"] == TARGET_RULE), None)
    if alert is None:
        print(f"FAIL: monitor did not raise '{TARGET_RULE}'")
        return False
    print(f"Monitor alert: {alert['rule']}: {alert['user_id']} -> {alert['resource']}\n")
    before = store.all_grants()
    show_grants("Before")

    # 2. Recover: revoke is the safe direction, so no approval is needed.
    result = revoke_access(alert["user_id"], alert["resource"],
                           reason=f"recover drill: monitor alert '{alert['rule']}'")
    print(f"\nrevoke_access -> {result['status']} (reason logged: {result['reason']!r})\n")
    show_grants("After")

    # 3. Verify: the same detector, run again.
    still_there = [a for a in find_anomalies() if a == alert]
    expected = {u: [r for r in rs if (u, r) != (alert["user_id"], alert["resource"])]
                for u, rs in before.items()}
    only_that_changed = store.all_grants() == expected
    logged = any(e["type"] == "revoke" and e["user_id"] == alert["user_id"] for e in store.read_events())

    ok = not still_there and only_that_changed and logged
    print(f"\nalert gone: {not still_there}   only that grant changed: {only_that_changed}   "
          f"revoke logged: {logged}")
    print("PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    ok = main()
    store.reset_data()  # leave the live data at the seed for the next run
    sys.exit(0 if ok else 1)
