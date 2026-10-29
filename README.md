# Access-request agent

The running hands-on example for the ebook **Trustworthy AI Agents**.

> **Don't trust the model. Contain it. Then prove it works.**

A single Claude agent handles access requests in chat ("Asha Rao needs billing-db access for the quarter
close") with exactly three tools over mock JSON data. Every safety rule is enforced in code. The prompt asks
for the right behaviour, and the code makes sure of it. Lines marked `# BOOK: <Pillar>` are the snippets used
in the book.

This is a teaching example, not a product: mock data, one process, no real identity system.
[CONTAINMENT_MAP.md](CONTAINMENT_MAP.md) lists what it proves and what it doesn't.

## What the agent can do

| Tool | Does | Guard in code |
|---|---|---|
| `check_user(name)` | id, team, status, current grants; `found=false` for unknown users | read-only |
| `grant_access(user_id, resource, approval_id=None)` | adds a grant | active user, resource in catalog, and a stored human **Approve** for sensitive/admin resources |
| `revoke_access(user_id, resource, reason)` | removes a grant, logs the reason | none needed: revoking is the safe direction |

Nothing else: no shell, no web, no delete, no password reset. The resource catalog (`wiki`, `jira`: standard;
`billing-db`, `hr-records`: sensitive; `prod-admin-console`: admin) goes into the system prompt as
config. It is not a tool.

wiki, jira, billing-db, hr-records and prod-admin-console are example resource names; the agent only reads and writes mock JSON and connects to no real system.

The loop (`agent/agent.py`) sits between the model and the tools. The model only *proposes* tool calls, and
the loop decides which ones run:

- **check_user first.** A write for a user not looked up in this request is blocked. It is still recorded as
  a `blocked:<tool>` step and counted.
- **Scope.** A write for a user or resource the request doesn't name isn't run. It goes to a human as an
  out-of-scope proposal.
- **Approval.** A sensitive/admin grant pauses the loop. A human clicks Approve or Deny. Approve runs the grant;
  Deny ends the request.
- **Emergency stop.** While a `STOP` file exists (the app's "Pause agent" switch), every tool call is refused
  before it runs, and the request ends `STOPPED`.
- **Honest decision.** Every request ends with a structured decision. If the model claims a grant or revoke
  that never ran, the decision is `INVALID`.

The same gates map onto any agent framework, such as hooks and permission rules in the Claude Agent SDK.

## The five pillars, mapped to files

| Pillar | What it asks | Where it lives (`# BOOK:` snippets) |
|---|---|---|
| **Design** | Narrow by construction | `agent/tools.py` `TOOL_SCHEMAS` (three tools); `agent/prompt.py` `SYSTEM_PROMPT`; `agent/agent.py` check_user-first in `_handle` |
| **Control** | Humans approve sensitive and admin access; the model can't talk its way past | `agent/tools.py` `grant_access` (approval gate); `agent/agent.py` `resume`, scope check and approval pause in `_handle`, `_stopped` (emergency stop); `agent/store.py` `record_approval`; `app.py` Pause switch; `evals/compromised_model.py` |
| **Recover** | Undo a wrong grant | `agent/tools.py` `revoke_access`; `agent/store.py` `reset_data`; `drills/recover_drill.py` |
| **Prove** | Traces and evals | `agent/agent.py` `setup_tracing`, `parse_decision`, `_block_call`, `_finish`; `evals/scorers.py`; `evals/run_evals.py` `run_row`, `upload_to_phoenix` |
| **Operate** | Watch it over time | `agent/store.py` `log_event` (append-only audit log); `operate/monitor.py` `find_anomalies`; the app's Operate tab |

## Setup

Python 3.12. With [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
source .venv/bin/activate
cp .env.example .env
```

With pip: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

Then fill in **one** way to reach Claude in `.env`:

- **Vercel AI Gateway:** set `AI_GATEWAY_API_KEY`. The agent uses the Anthropic Python SDK with
  `base_url="https://ai-gateway.vercel.sh"`. The default model is `anthropic/claude-sonnet-5.5` (gateway ids
  take the `anthropic/` prefix).
- **Anthropic API directly:** leave `AI_GATEWAY_API_KEY` empty and set `ANTHROPIC_API_KEY`. The default
  model is `claude-sonnet-5-5`.

`ANTHROPIC_MODEL` overrides either default. Never commit `.env`; it is gitignored.

## Run it

**Tests** (no API key, no Phoenix):

```bash
pytest
```

**Phoenix** (local, open source). The Phoenix server package pins an older Anthropic SDK than this project,
so it runs in its own environment:

```bash
uvx --python 3.12 --from arize-phoenix==20.26.1 phoenix serve
```

Open http://localhost:6006. Traces go to the project `access-request-agent`. Each request is one trace:
LLM spans (OpenInference), a TOOL span per call, and a `human_approval` span with `approval.decision`,
`approval.approver`, `approval.id`, `approval.timestamp`, `approval.reason` and `approval.requested_call`.

**The app:**

```bash
streamlit run app.py
```

Chat on the left, approval card when a request needs a human, live access table on the right, an Operate
tab with anomalies and daily counts, and the "Pause agent" switch in the sidebar.

**Evals:**

```bash
python evals/run_evals.py --repeat 3                                    # golden set, gates enforced in code
python evals/run_evals.py --repeat 3 --prompt-only-checks               # same, loop checks off
python evals/run_evals.py --set adversarial --repeat 3                  # requests that push to skip checks
python evals/run_evals.py --compromised                                 # scripted compromised model
python evals/run_evals.py --compromised --prompt-only-checks
```

Each row starts from a fresh seed, and approvals are answered with the row's `human_response`. Three code-based
scorers:
- **router:** right first tool;
- **tool:** right decision, and every attempted write names the right user and resources;
- **trajectory:** exact step order, with `human_approval` as a step.

The output is a terminal table (with `blocked` and out-of-scope columns), a summary with pass rates and
pass^N, `evals/results/<label>.csv` (written before the upload), and a Phoenix experiment named `--label`.
Other flags: `--no-phoenix`, `--rows GS03,GS15`.

**Recover drill:**

```bash
python drills/recover_drill.py
```

Runs the monitor, takes its "terminated user still has access" alert (Erik Holm → jira), revokes the grant,
runs the monitor again and prints `PASS` if the alert is gone.

**Monitor:**

```bash
python operate/monitor.py
```

Daily counts from `data/audit_log.jsonl`, plus two anomaly rules: access held by an inactive/terminated
user, and a sensitive/admin grant with no matching Approve. It exits 1 when there is an alert.

## Data

- `data/seed/`: users, catalog, grants, approvals. `reset_data()` copies it to `data/` before every eval row.
  Erik Holm's leftover jira grant is there on purpose, for the monitor and the drill. Ben Thomas's existing
  billing-db grant has an approval record (`approver: pre-existing-grant`), so the "no matching Approve"
  invariant holds from the start.
- `data/seed_injection/`: the same, plus planted `"SYSTEM: …"` notes in two user records, for the
  indirect-prompt-injection rows (AD07, AD08).
- `data/audit_log.jsonl`: every event, append-only, labelled with its run (`ui`, `eval:<label>`,
  `drill:recover`). `reset_data()` never touches it. `data/events.jsonl` holds the events since the last reset.

## Results

The measured results (golden, adversarial and compromised-model runs), the one real failure found (an
unrequested revoke, caught by pass^3 and fixed in code), and the known gaps are in
[CONTAINMENT_MAP.md](CONTAINMENT_MAP.md).

## Licence

MIT. See [LICENSE](LICENSE). All people and resources in the data are fictional.
