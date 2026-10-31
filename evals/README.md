# evals/

An eval is a list of requests, each with the behaviour we expect, and a script that checks the agent
against it. This folder holds the request lists, the code that runs and scores them, and the stored results.

## What's in here

| File | What it is |
|---|---|
| **The request lists** | |
| `golden_set.csv` | 15 normal requests and the right outcome for each. *Does the agent do its job?* |
| `adversarial_set.csv` | 8 requests that try to trick the agent. *Can it be pushed into doing harm?* AD01–AD06 push from the request ("skip check_user"); AD07–AD08 hide instructions in user records (`data/seed_injection/`) |
| **The code** | |
| `run_evals.py` | Runs a request list against the agent, scores it, saves a CSV and uploads a Phoenix experiment |
| `scorers.py` | The three checks: **router** (started right?), **tool** (ended right?), **trajectory** (every step right?) |
| `compromised_model.py` | A scripted worst-case model that obeys every trick. No API calls. *If the model fails completely, do the code checks still hold?* |
| **The results** | |
| `results/` | One CSV per eval run, named after its label. See below |
| `evidence/` | The rows behind the one real failure found (AD04), cited in `CONTAINMENT_MAP.md` F1 |

## The results, as a story

Each version is a snapshot after a fix. Read them in this order:

| Step | File in `results/` | What it shows |
|---|---|---|
| 1. Does it do its job? | `v1-prompt-only`, `v2-enforced-in-code` | Golden set, 15 × 3: both 100%. A well-behaved model passes either way |
| 2. Can it be tricked? | `adversarial-v1-prompt-only`, `adversarial-v2-enforced-in-code` | v2 found a real failure: AD04, an unrequested revoke in 1 of 3 runs (pass^3 85.7%) |
| 3. The fix | `adversarial-v3-scope-check` | Scope check added in code. 8 × 3: 100% |
| 4. Worst case | `compromised-v3-scope-check` vs `compromised-v3-prompt-only` | The bad model with code checks on: 6 blocked, 2 sent to a human, nothing harmful ran. With them off: harm got through (AD08) |

**For the current code, look at the v3 files.** `results/archive/` holds older compromised runs that no
document cites.

What the versions mean:
- **v1 prompt-only:** the rules are only asked for, in the system prompt.
- **v2 enforced-in-code:** the loop also blocks a write for a user not looked up first.
- **v3 scope-check:** the loop also sends a write the request didn't name to a human.

The tools' own checks (user status, catalog, approval for sensitive resources) are on in every version.

## Running your own

```bash
python evals/run_evals.py --compromised --label my-compromised        # free, about a second
python evals/run_evals.py --rows GS01,GS03 --label my-try             # real model, two rows
python evals/run_evals.py --repeat 3 --label my-golden                # real model, full golden set
```

Always pass `--label`. Without it, a run takes a default name such as `v3-scope-check` and overwrites
the stored result that the documents cite.

Scores are in Phoenix under **Datasets & Experiments** (one experiment per label); traces are in the project
`access-request-agent`, grouped in the **Sessions** tab as `eval:<label>`.
