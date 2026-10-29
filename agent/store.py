"""Mock data store: users, resource catalog, grants, approvals and two event logs.

Everything is plain JSON on disk so readers can open the files and see the state.
Live files live in data/; the seed in data/seed/ is never written to.

- events.jsonl     events since the last reset (state, cleared by reset_data)
- audit_log.jsonl  every event ever, append-only, never touched by reset_data
"""

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# DATA_DIR can be overridden (tests point it at a temp folder).
DATA_DIR = Path(os.getenv("ACCESS_AGENT_DATA_DIR", PROJECT_ROOT / "data"))
SEED_DIR = PROJECT_ROOT / "data" / "seed"

USERS = "users.json"
RESOURCES = "resources.json"
GRANTS = "grants.json"
APPROVALS = "approvals.json"
EVENTS = "events.jsonl"
AUDIT_LOG = "audit_log.jsonl"

# Who is writing events: "ui", "eval:<label>", "drill:recover", ... Set by each entry point.
RUN_LABEL = os.getenv("ACCESS_AGENT_RUN_LABEL", "manual")


def set_run_label(label: str) -> None:
    global RUN_LABEL
    RUN_LABEL = label


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(name: str):
    return json.loads((DATA_DIR / name).read_text())


def _write(name: str, value) -> None:
    (DATA_DIR / name).write_text(json.dumps(value, indent=2) + "\n")


# BOOK: Recover
def reset_data(seed: str = "seed") -> None:
    """Restore a seed: users, catalog, grants and approvals. Clears events.jsonl.

    Never touches audit_log.jsonl: resetting state must not erase history.

    Every eval row starts from this exact state. seed="seed_injection" loads the
    adversarial seed, where one user record carries a planted "SYSTEM:" note.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in (USERS, RESOURCES, GRANTS, APPROVALS):
        shutil.copyfile(SEED_DIR.parent / seed / name, DATA_DIR / name)
    (DATA_DIR / EVENTS).write_text("")


def _ensure_data() -> None:
    if not (DATA_DIR / GRANTS).exists():
        reset_data()


# --- Users and catalog (read-only for the agent) ---------------------------

def find_user(name_or_id: str) -> dict | None:
    _ensure_data()
    key = name_or_id.strip().lower()
    for user in _read(USERS):
        if key in (user["user_id"], user["name"].lower()):
            return user
    return None


def all_users() -> list[dict]:
    _ensure_data()
    return _read(USERS)


def get_user(user_id: str) -> dict | None:
    _ensure_data()
    return next((u for u in _read(USERS) if u["user_id"] == user_id), None)


def catalog() -> dict[str, str]:
    """resource -> sensitivity (standard / sensitive / admin)."""
    _ensure_data()
    return {r["resource"]: r["sensitivity"] for r in _read(RESOURCES)}


# --- Grants ----------------------------------------------------------------

def all_grants() -> dict[str, list[str]]:
    _ensure_data()
    return _read(GRANTS)


def grants_for(user_id: str) -> list[str]:
    return list(all_grants().get(user_id, []))


def save_grants(grants: dict[str, list[str]]) -> None:
    _write(GRANTS, grants)


# --- Approvals (written only by a human decision, never by the model) ------

# BOOK: Control
def record_approval(user_id: str, resource: str, reason: str,
                    decision: str, approver: str) -> dict:
    """Store a human decision. Returns the approval record with its id."""
    if decision not in ("Approve", "Deny"):
        raise ValueError(f"decision must be Approve or Deny, got {decision!r}")
    _ensure_data()
    approvals = _read(APPROVALS)
    approval = {
        "approval_id": f"apr-{uuid.uuid4().hex[:8]}",
        "user_id": user_id,
        "resource": resource,
        "reason": reason,
        "decision": decision,
        "approver": approver,
        "timestamp": now_iso(),
    }
    approvals[approval["approval_id"]] = approval
    _write(APPROVALS, approvals)
    return approval


def get_approval(approval_id: str | None) -> dict | None:
    if not approval_id:
        return None
    _ensure_data()
    return _read(APPROVALS).get(approval_id)


def all_approvals() -> dict[str, dict]:
    _ensure_data()
    return _read(APPROVALS)


# --- Event log -------------------------------------------------------------

# BOOK: Operate
def log_event(event_type: str, user_id: str | None = None,
              resource: str | None = None, **detail) -> dict:
    """Append one event (grant, revoke, approval, denial, out_of_scope, stop).

    It goes to events.jsonl (this state) and to audit_log.jsonl (all history).
    """
    _ensure_data()
    event = {"ts": now_iso(), "run_label": RUN_LABEL, "type": event_type,
             "user_id": user_id, "resource": resource, **detail}
    line = json.dumps(event) + "\n"
    for name in (EVENTS, AUDIT_LOG):
        with open(DATA_DIR / name, "a") as f:  # append only, never rewritten
            f.write(line)
    return event


def _read_jsonl(name: str) -> list[dict]:
    path = DATA_DIR / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_events() -> list[dict]:
    """Events since the last reset."""
    _ensure_data()
    return _read_jsonl(EVENTS)


def read_audit_log() -> list[dict]:
    """Every event ever written, across resets, evals, drills and the UI."""
    return _read_jsonl(AUDIT_LOG)
