"""Streamlit UI: chat, approval card, live access table, Operate tab, emergency stop.

    streamlit run app.py
"""

import json
import uuid

import pandas as pd
import streamlit as st

from agent import agent, store
from operate.monitor import EVENT_TYPES, daily_counts, find_anomalies

st.set_page_config(page_title="Access-request agent", page_icon="🔐", layout="wide")
st.markdown("""
<style>
.approval-card {border: 2px solid #7c5cbf; background: #f4effc; border-radius: 10px; padding: 14px 18px;}
.approval-card h4 {color: #5b3f9e; margin: 0 0 6px 0;}
.decision {display: inline-block; background: #eef6ff; border-radius: 6px; padding: 2px 8px;
           font-family: monospace; font-size: 0.85em;}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def tracing():
    """Send UI traces to Phoenix if it is running; the app works without it."""
    try:
        return agent.setup_tracing("access-request-agent-ui")
    except Exception:
        return None


tracing()
store.set_run_label("ui")
state = st.session_state
state.setdefault("history", [])  # (role, text, decision)
state.setdefault("run", None)
state.setdefault("session_id", f"ui-{uuid.uuid4().hex[:8]}")  # one per browser session


def record(run):
    """Add a finished or paused run to the chat history."""
    if run.pending:
        return
    lines = [l for l in run.reply.strip().splitlines() if not l.strip().startswith("{")]
    text = "\n".join(lines).strip() or run.summary
    state.history.append(("assistant", text, run.decision))
    state.run = None


# --- Sidebar: emergency stop and approver ----------------------------------
with st.sidebar:
    st.header("Controls")
    # BOOK: Control
    paused = st.toggle("Pause agent", value=agent.STOP_FILE.exists(),
                       help="Emergency stop: every tool call is refused while this is on.")
    if paused and not agent.STOP_FILE.exists():
        agent.STOP_FILE.touch()
    if not paused and agent.STOP_FILE.exists():
        agent.STOP_FILE.unlink()
    if paused:
        st.error("Agent paused. Requests end as STOPPED.")
    requester = st.text_input("Requester (you)", value="demo-user",
                              help="Who is asking. Traced as user.id and written to the audit log.")
    approver = st.text_input("Approver name", value="team-lead")
    if st.button("Reset demo data"):
        store.reset_data()
        state.history, state.run = [], None
        state.session_id = f"ui-{uuid.uuid4().hex[:8]}"  # a fresh take is a fresh Phoenix session
        st.rerun()
    st.caption(f"Model: {agent.model_id()}")
    st.caption(f"Session: `{state.session_id}`")

agent_tab, operate_tab = st.tabs(["Agent", "Operate"])

# --- Agent tab ---------------------------------------------------------------
with agent_tab:
    chat, side = st.columns([3, 2], gap="large")

    with chat:
        st.subheader("Access requests")
        for role, text, decision in state.history:
            with st.chat_message(role):
                st.markdown(text)
                if decision:
                    st.markdown(f'<span class="decision">{decision}</span>', unsafe_allow_html=True)

        run = state.run
        if run is not None and run.pending:
            p = run.pending
            st.markdown(f"""
<div class="approval-card">
  <h4>Approval needed</h4>
  <b>Who:</b> {p['user_id']}<br>
  <b>What:</b> {p['requested_call']['tool']} → {p['resource']}<br>
  <b>Why a human:</b> {p['why']}<br>
  <b>Request:</b> {run.request}
</div>""", unsafe_allow_html=True)
            st.code(json.dumps(p["requested_call"], indent=2), language="json")
            approve, deny, _ = st.columns([1, 1, 3])
            for col, decision in ((approve, "Approve"), (deny, "Deny")):
                if col.button(decision, type="primary" if decision == "Approve" else "secondary"):
                    try:
                        record(agent.resume(run, decision, approver=approver or "unknown"))
                    except Exception as err:
                        st.error(f"{type(err).__name__}: {err}")
                    st.rerun()

        prompt = st.chat_input("e.g. Asha Rao needs billing-db access for the quarter close.",
                               disabled=run is not None and run.pending is not None)
        if prompt:
            state.history.append(("user", prompt, None))
            try:
                state.run = agent.start(prompt, requester=requester or "unknown",
                                        session_id=state.session_id)
                record(state.run)
            except Exception as err:
                state.history.append(("assistant", f"Error: {type(err).__name__}: {err}", None))
            st.rerun()

    with side:
        st.subheader("Who has access")
        catalog, grants = store.catalog(), store.all_grants()
        rows = [{"user": u["name"], "status": u["status"],
                 "access": ", ".join(f"{r} ({catalog.get(r, '?')})" for r in grants.get(u["user_id"], [])) or "-"}
                for u in store.all_users()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption("Catalog: " + ", ".join(f"{r} ({s})" for r, s in catalog.items()))

# --- Operate tab -------------------------------------------------------------
with operate_tab:
    st.subheader("Anomalies")
    alerts = find_anomalies()
    if not alerts:
        st.success("No anomalies.")
    for a in alerts:
        st.error(f"**{a['rule']}**: {a['user_id']} → {a['resource']}")
    st.caption("Rules: access held by an inactive/terminated user; sensitive/admin grant with no "
               "matching Approve; grant made outside the agent (no requester on record). "
               "Run `python drills/recover_drill.py` to fix the first one.")

    st.subheader("Recent events")
    recent = store.read_audit_log()[-20:][::-1]
    if recent:
        st.dataframe(pd.DataFrame([{"time": e["ts"], "run": e.get("run_label"), "type": e["type"],
                                    "user": e.get("user_id"), "resource": e.get("resource"),
                                    "requester": e.get("requester", "-"),
                                    "trace_id": e.get("trace_id", "-")} for e in recent]),
                     hide_index=True, use_container_width=True)
    else:
        st.info("No events yet.")
    st.caption("Newest first. requester is who asked; trace_id opens the Phoenix trace that "
               "explains the event. No requester means the change did not go through the agent.")

    st.subheader("Events per day")
    days = daily_counts()
    if days:
        st.dataframe(pd.DataFrame([{"day": d, **{t: c.get(t, 0) for t in EVENT_TYPES}}
                                   for d, c in days.items()]), hide_index=True, use_container_width=True)
    else:
        st.info("No events yet.")
    st.caption("From data/audit_log.jsonl: append-only, kept across resets, every line labelled "
               "with its run (ui, eval:<label>, drill:recover).")
