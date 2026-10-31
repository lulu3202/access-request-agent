"""The agent loop: config, the model call, the code-level gates, and tracing.

    run = start("Asha Rao needs billing-db access.")
    if run.pending:                      # paused for a human
        run = resume(run, "Approve", approver="team-lead")
    print(run.decision, run.steps)       # GRANTED_AFTER_APPROVAL  ['check_user', 'human_approval', 'grant_access']

The model only proposes tool calls. This loop decides which ones actually run.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from agent import store
from agent.prompt import DECISIONS, build_system_prompt
from agent.tools import NEEDS_APPROVAL, TOOL_SCHEMAS, TOOLS, PolicyError, ToolError

# --- Config ----------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

GATEWAY_URL = "https://ai-gateway.vercel.sh"
STOP_FILE = PROJECT_ROOT / "STOP"
MAX_TURNS = 8
MAX_TOKENS = 1024


def make_client():
    """Vercel AI Gateway if AI_GATEWAY_API_KEY is set, otherwise Anthropic directly."""
    import anthropic

    # open_telemetry=False: the SDK's own spans would duplicate OpenInference's.
    if os.getenv("AI_GATEWAY_API_KEY"):
        return anthropic.Anthropic(api_key=os.environ["AI_GATEWAY_API_KEY"],
                                   base_url=GATEWAY_URL, open_telemetry=False)
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), open_telemetry=False)


def model_id() -> str:
    default = "anthropic/claude-sonnet-5.5" if os.getenv("AI_GATEWAY_API_KEY") else "claude-sonnet-5-5"
    return os.getenv("ANTHROPIC_MODEL") or default


# --- Tracing (Phoenix via OpenInference) -----------------------------------

tracer = trace.get_tracer("access-request-agent")  # no-op until setup_tracing() runs


# BOOK: Prove
def setup_tracing(project_name: str = "access-request-agent"):
    """Send traces to the local Phoenix server; auto-instrument the Anthropic SDK."""
    from phoenix.otel import register

    return register(project_name=project_name, auto_instrument=True, batch=False)


def _attrs(kind: str, **values) -> dict:
    attrs = {"openinference.span.kind": kind}
    for key, value in values.items():
        attrs[key] = value if isinstance(value, (str, bool, int, float)) else json.dumps(value)
    return attrs


# --- Decisions -------------------------------------------------------------

# Most restrictive first. A multi-resource request reports the first one that occurred.
RESTRICTIVENESS = ["STOPPED", "DENIED_BY_HUMAN", "DENIED_POLICY", "GRANTED_AFTER_APPROVAL",
                   "GRANTED", "REVOKED", "NO_CHANGE", "REFUSED", "INFO"]


def most_restrictive(decisions) -> str:
    return min(decisions, key=RESTRICTIVENESS.index)


# BOOK: Prove
def parse_decision(text: str) -> tuple[str, str]:
    """Read the final JSON line. No retry: anything missing or invalid is INVALID."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip() and not l.strip().startswith("```")]
    if not lines:
        return "INVALID", "no final decision line"
    try:
        final = json.loads(lines[-1])
    except json.JSONDecodeError:
        return "INVALID", "last line is not JSON"
    decision = final.get("decision") if isinstance(final, dict) else None
    if decision not in DECISIONS:
        return "INVALID", f"unknown decision {decision!r}"
    return decision, str(final.get("summary", ""))


# --- One request -----------------------------------------------------------

@dataclass
class Run:
    request: str
    requester: str | None = None                   # who asked (not the user getting access)
    session_id: str | None = None                  # groups one conversation's traces in Phoenix
    enforce_check_user_first: bool = True
    enforce_scope: bool = True
    messages: list = field(default_factory=list)
    steps: list = field(default_factory=list)      # the real trajectory, blocked steps included
    writes: list = field(default_factory=list)     # every attempted grant/revoke: (tool, user, resource)
    outcomes: list = field(default_factory=list)   # what code saw happen, e.g. GRANTED, DENIED_POLICY
    checked_users: set = field(default_factory=set)
    blocked: int = 0
    out_of_scope: int = 0                          # writes the loop sent to a human as out of scope
    pending: dict | None = None                    # set while waiting for a human
    decision: str | None = None                    # final, after code facts are applied
    model_decision: str | None = None              # what the model said
    summary: str = ""
    reply: str = ""
    _queue: list = field(default_factory=list)     # tool calls still to run after a pause
    _results: list = field(default_factory=list)   # tool results collected for this turn
    _span: object = None
    _client: object = None

    @property
    def done(self) -> bool:
        return self.decision is not None

    @property
    def first_tool(self) -> str:
        return self.steps[0] if self.steps else "none"


def start(request: str, client=None, enforce_check_user_first: bool = True,
          enforce_scope: bool = True, tags: dict | None = None,
          requester: str | None = None, session_id: str | None = None) -> Run:
    """tags are extra root-span attributes, e.g. {"eval.row_id": "GS03"}.

    requester and session_id become the OpenInference user.id and session.id attributes,
    so Phoenix can filter by who asked and group a conversation in its Sessions tab.
    Events from the loop always carry a requester ("unknown" if none is given), so the
    monitor can tell them apart from writes made outside the agent.
    """
    requester = requester or "unknown"
    run = Run(request=request, requester=requester, session_id=session_id,
              enforce_check_user_first=enforce_check_user_first, enforce_scope=enforce_scope)
    run._client = client or make_client()
    run.messages.append({"role": "user", "content": request})
    who = {k: v for k, v in (("user.id", requester), ("session.id", session_id)) if v}
    run._span = tracer.start_span("access_request", attributes=_attrs(
        "AGENT", **{"input.value": request, "enforce_check_user_first": enforce_check_user_first,
                    "enforce_scope": enforce_scope, **who, **(tags or {})}))
    with trace.use_span(run._span, end_on_exit=False), store.acting_for(requester):
        _drive(run)
    return run


# BOOK: Control
def resume(run: Run, decision: str, approver: str) -> Run:
    """A human answered the pending approval. Approve runs the proposed call; Deny ends the request."""
    pending, run.pending = run.pending, None
    tool, proposed = pending["requested_call"]["tool"], pending["requested_call"]["args"]
    with trace.use_span(run._span, end_on_exit=False), store.acting_for(run.requester):
        approval = store.record_approval(pending["user_id"], pending["resource"],
                                         f"{pending['why']}: {run.request}", decision, approver)
        store.log_event("approval", pending["user_id"], pending["resource"],
                        decision=decision, approver=approver, approval_id=approval["approval_id"],
                        why=pending["why"])
        # The human decision sits inside the trace, next to the call the model tried to make.
        with tracer.start_as_current_span("human_approval", attributes=_attrs(
                "CHAIN", **{"approval.decision": decision, "approval.approver": approver,
                            "approval.id": approval["approval_id"],
                            "approval.timestamp": approval["timestamp"],
                            "approval.reason": pending["why"],
                            "approval.requested_call": pending["requested_call"]})):
            run.steps.append("human_approval")

        if decision != "Approve":
            run.outcomes.append("DENIED_BY_HUMAN")
            _finish(run, "DENIED_BY_HUMAN",
                    f"{approver} denied {tool} {pending['resource']} for {pending['user_id']}.")
            return run

        if _stopped(run, tool, proposed):
            return run
        args = {**proposed, "approval_id": approval["approval_id"]} if tool == "grant_access" else proposed
        result = _execute(run, tool, args)
        if result.get("status") == "granted":
            run.outcomes[-1] = "GRANTED_AFTER_APPROVAL"
        run._results.append(_tool_result(pending["tool_use_id"], {"approved_by": approver, **result}))
        if not _run_queue(run):
            _drive(run)
    return run


def _drive(run: Run) -> None:
    """Call the model until it stops asking for tools, or the loop pauses or stops."""
    while not run.done and not run.pending:
        if len([m for m in run.messages if m["role"] == "assistant"]) >= MAX_TURNS:
            _finish(run, "INVALID", "too many turns")
            return
        response = run._client.messages.create(
            model=model_id(), max_tokens=MAX_TOKENS, system=build_system_prompt(),
            tools=TOOL_SCHEMAS, messages=run.messages)
        content = [_block(b) for b in response.content if b.type in ("text", "tool_use")]
        run.messages.append({"role": "assistant", "content": content})

        calls = [b for b in content if b["type"] == "tool_use"]
        if not calls:
            run.reply = "\n".join(b["text"] for b in content if b["type"] == "text")
            run.model_decision, run.summary = parse_decision(run.reply)
            _finish(run, run.model_decision, run.summary)
            return
        run._queue, run._results = calls, []
        if _run_queue(run):
            return  # paused or stopped


def _run_queue(run: Run) -> bool:
    """Run queued tool calls in order. Returns True if the loop paused or stopped."""
    while run._queue:
        call = run._queue.pop(0)
        if _stopped(run, call["name"], call["input"]):
            return True
        result = _handle(run, call)
        if run.pending:
            return True
        run._results.append(_tool_result(call["id"], result))
    run.messages.append({"role": "user", "content": run._results})
    run._results = []
    return False


def _handle(run: Run, call: dict) -> dict:
    name, args = call["name"], call["input"]
    if name not in TOOLS:
        return _block_call(run, name, args, f"there is no tool named {name}")

    if name in ("grant_access", "revoke_access"):
        run.writes.append((name, args.get("user_id"), args.get("resource")))
        # BOOK: Design
        # check_user must come first. Enforced here, not trusted to the prompt.
        if run.enforce_check_user_first and args.get("user_id") not in run.checked_users:
            return _block_call(run, name, args, "call check_user for this user first")

        # BOOK: Control
        # Scope check: a write must be about the user (and resource) the request names.
        # Anything else is not run; it goes to a human as a proposal.
        why = run.enforce_scope and out_of_scope(run.request, args)
        if why:
            run.out_of_scope += 1
            store.log_event("out_of_scope", args.get("user_id"), args.get("resource"),
                            tool=name, why=why, request=run.request)
            run.pending = _proposal(call, f"out of scope: {why}")
            return {}

    # BOOK: Control
    # A sensitive/admin grant without a valid Approve pauses the loop for a human.
    # Policy failures (inactive user, unknown resource) go straight to the tool, which denies them.
    if name == "grant_access" and _needs_human(args):
        run.pending = _proposal(call, f"{store.catalog()[args['resource']]} resource")
        return {}

    return _execute(run, name, args)


def _proposal(call: dict, why: str) -> dict:
    """A pending approval: the call the model proposed, and why a human must decide."""
    args = {k: v for k, v in call["input"].items() if k != "approval_id"}
    return {"user_id": args.get("user_id"), "resource": args.get("resource"), "why": why,
            "tool_use_id": call["id"], "requested_call": {"tool": call["name"], "args": args}}


def out_of_scope(request: str, args: dict) -> str:
    """Why a write is out of scope, or "" if it is in scope.

    In scope means the request names the user (full name or user_id) and, when the
    request names any catalog resource, this resource is one of them.
    """
    text = request.lower()
    user_id, resource = args.get("user_id") or "", args.get("resource") or ""
    user = store.get_user(user_id)
    names = {user_id.lower()} | ({user["name"].lower()} if user else set())
    if not any(n and n in text for n in names):
        return f"{user_id} is not named in the request"
    named = [r for r in store.catalog() if r in text]
    if named and resource not in named:
        return f"{resource} is not named in the request"
    return ""


def _needs_human(args: dict) -> bool:
    user = store.get_user(args.get("user_id", ""))
    resource = args.get("resource")
    approval = store.get_approval(args.get("approval_id"))
    return (store.catalog().get(resource) in NEEDS_APPROVAL
            and user is not None and user["status"] == "active"
            and resource not in store.grants_for(user["user_id"])
            and not (approval and approval["decision"] == "Approve"))


def _execute(run: Run, name: str, args: dict) -> dict:
    """Run a real tool inside a TOOL span and record what happened."""
    run.steps.append(name)
    with tracer.start_as_current_span(name, attributes=_attrs(
            "TOOL", **{"tool.name": name, "input.value": args})) as span:
        try:
            result = TOOLS[name](**args)
        except ToolError as err:
            span.set_status(Status(StatusCode.ERROR, str(err)))
            if isinstance(err, PolicyError):
                run.outcomes.append("DENIED_POLICY")
            return {"error": str(err)}
        except TypeError as err:  # wrong arguments from the model
            span.set_status(Status(StatusCode.ERROR, str(err)))
            return {"error": f"bad arguments: {err}"}
        span.set_attribute("output.value", json.dumps(result))

    if name == "check_user" and result.get("found"):
        run.checked_users.add(result["user_id"])
    run.outcomes.append({"granted": "GRANTED", "revoked": "REVOKED",
                         "no_change": "NO_CHANGE", "not_held": "NO_CHANGE"}.get(result.get("status"), "INFO"))
    return result


# BOOK: Prove
def _block_call(run: Run, name: str, args: dict, reason: str) -> dict:
    """A rejected call is never hidden: it is a step, a span and a count."""
    run.steps.append(f"blocked:{name}")
    run.blocked += 1
    with tracer.start_as_current_span(f"blocked:{name}", attributes=_attrs(
            "TOOL", **{"tool.name": name, "input.value": args, "blocked": True, "blocked.reason": reason})):
        pass
    return {"error": f"blocked: {reason}"}


# BOOK: Control
def _stopped(run: Run, name: str, args: dict) -> bool:
    """Emergency stop. If the STOP file exists, refuse the call before it runs."""
    if not STOP_FILE.exists():
        return False
    with tracer.start_as_current_span("stopped", attributes=_attrs(
            "CHAIN", **{"tool.name": name, "input.value": args, "stopped": True})):
        pass
    run.steps.append("stopped")
    store.log_event("stop", args.get("user_id"), args.get("resource"), tool=name, request=run.request)
    run._queue = []
    _finish(run, "STOPPED", "Agent is paused by an operator; no action was taken.")
    return True


# A decision that claims a change needs that change to have actually run.
CLAIMS_NEED_EVIDENCE = {"GRANTED", "GRANTED_AFTER_APPROVAL", "REVOKED"}


def _finish(run: Run, decision: str, summary: str) -> None:
    # BOOK: Prove
    # The model's label is checked against what code saw happen:
    # - claiming a grant or revoke that never ran is INVALID (the row fails);
    # - a human Deny or a policy denial cannot be reported as something milder.
    if decision in CLAIMS_NEED_EVIDENCE and decision not in run.outcomes:
        decision, summary = "INVALID", f"model claimed {decision} but no such action ran"
    if decision not in ("INVALID", "STOPPED") and run.outcomes:
        decision = most_restrictive([decision, *[o for o in run.outcomes if o != "INFO"]])
    run.decision, run.summary = decision, summary
    if decision in ("DENIED_POLICY", "DENIED_BY_HUMAN"):
        target = run.writes[-1] if run.writes else (None, None, None)
        store.log_event("denial", target[1], target[2], decision=decision, request=run.request)
    if run._span is not None:
        run._span.set_attributes({"output.value": json.dumps({"decision": decision, "summary": summary}),
                                  "decision": decision, "blocked_count": run.blocked,
                                  "trajectory": ">".join(run.steps) or "none"})
        run._span.end()


def _block(b) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}


def _tool_result(tool_use_id: str, result: dict) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": json.dumps(result), "is_error": "error" in result}
