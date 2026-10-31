"""Run the golden set against the agent and score it.

    python evals/run_evals.py                                   # enforced in code, 1 run per row
    python evals/run_evals.py --repeat 3                        # also report pass^3
    python evals/run_evals.py --prompt-only-checks --label v1-prompt-only
    python evals/run_evals.py --no-phoenix --rows GS03,GS15
    python evals/run_evals.py --set adversarial --repeat 3   # tries to skip check_user
    python evals/run_evals.py --compromised                  # scripted bad model, adversarial set

Each row starts from a fresh seed. Any approval is answered with the row's
human_response. Results go to evals/results/<label>.csv first, then to Phoenix.
"""

import argparse
import csv
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import agent, store  # noqa: E402
from evals.scorers import score  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
# The golden set is the main check; the adversarial set pushes the agent to skip check_user.
EVAL_SETS = {"golden": EVALS_DIR / "golden_set.csv", "adversarial": EVALS_DIR / "adversarial_set.csv"}
SCORERS = ("router", "tool", "trajectory")
APPROVER = "golden-set"


def load_rows(eval_set: str, only: str | None) -> list[dict]:
    with open(EVAL_SETS[eval_set], newline="") as f:
        rows = list(csv.DictReader(f))
    if only:
        wanted = set(only.split(","))
        rows = [r for r in rows if r["id"] in wanted]
    return rows


# BOOK: Prove
def run_row(row: dict, repeat: int, enforce: bool, label: str, client=None) -> dict:
    """One run of one row: fresh seed, run the agent, auto-answer approvals, score."""
    store.reset_data(row.get("seed") or "seed")
    try:
        run = agent.start(row["request"], client=client, enforce_check_user_first=enforce,
                          enforce_scope=enforce, requester="eval", session_id=f"eval:{label}",
                          tags={"eval.row_id": row["id"], "eval.label": label, "eval.repeat": repeat})
        while run.pending:
            run = agent.resume(run, row["human_response"] or "Deny", approver=APPROVER)
        steps, decision, writes, blocked, error = run.steps, run.decision, run.writes, run.blocked, ""
        out_of_scope = run.out_of_scope
        model_decision, summary = run.model_decision or "", run.summary
    except Exception as err:  # an API error fails the row; it never crashes the whole run
        steps, decision, writes, blocked, error = [], "ERROR", [], 0, f"{type(err).__name__}: {err}"
        out_of_scope = 0
        model_decision, summary = "", ""
    return {
        "label": label, "id": row["id"], "repeat": repeat, "request": row["request"],
        "expected_first_tool": row["expected_first_tool"],
        "actual_first_tool": steps[0] if steps else "none",
        "expected_decision": row["expected_decision"], "actual_decision": decision,
        "expected_order": row["expected_order"], "actual_order": ">".join(steps) or "none",
        **score(row, steps, decision, writes),
        "blocked": blocked, "out_of_scope": out_of_scope, "model_decision": model_decision, "summary": summary,
        "writes": ";".join(f"{t}:{u}:{r}" for t, u, r in writes), "error": error,
    }


def save_csv(results: list[dict], label: str) -> Path:
    path = EVALS_DIR / "results" / f"{label}.csv"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    return path


def print_report(rows: list[dict], results: list[dict], repeat: int) -> None:
    mark = lambda ok: "✓" if ok else "✗"
    by_row = {r["id"]: [x for x in results if x["id"] == r["id"]] for r in rows}

    print(f"\n{'id':5} {'first tool':22} {'decision':24} {'order':58} "
          f"{'R':>3} {'T':>3} {'J':>3} {'blocked':>7} {'oos':>4}" + (f" {'pass^' + str(repeat):>7}" if repeat > 1 else ""))
    for row in rows:
        runs = by_row[row["id"]]
        first = runs[0]
        counts = [sum(x[s] for x in runs) for s in SCORERS]
        cells = [mark(c == repeat) if repeat == 1 else f"{c}/{repeat}" for c in counts]
        all_pass = all(all(x[s] for s in SCORERS) for x in runs)
        line = (f"{row['id']:5} {first['actual_first_tool']:22} {first['actual_decision']:24} "
                f"{first['actual_order']:58} {cells[0]:>3} {cells[1]:>3} {cells[2]:>3} "
                f"{sum(x['blocked'] for x in runs):>7} {sum(x['out_of_scope'] for x in runs):>4}")
        print(line + (f" {mark(all_pass):>7}" if repeat > 1 else ""))

    n = len(results)
    print(f"\nSummary ({len(rows)} rows x {repeat} run(s) = {n} runs)")
    for s in SCORERS:
        single = sum(x[s] for x in results) / n
        line = f"  {s:11} pass rate {single:6.1%}"
        if repeat > 1:
            pass_n = sum(all(x[s] for x in by_row[r["id"]]) for r in rows) / len(rows)
            line += f"   pass^{repeat} {pass_n:6.1%}"
        print(line)
    all_three = sum(all(x[s] for s in SCORERS) for x in results) / n
    line = f"  {'all three':11} pass rate {all_three:6.1%}"
    if repeat > 1:
        pass_n = sum(all(all(x[s] for s in SCORERS) for x in by_row[r["id"]]) for r in rows) / len(rows)
        line += f"   pass^{repeat} {pass_n:6.1%}"
    print(line)
    print(f"  blocked attempts: {sum(x['blocked'] for x in results)}")
    print(f"  out-of-scope proposals: {sum(x['out_of_scope'] for x in results)}")

    failures = [x for x in results if not all(x[s] for s in SCORERS)]
    print(f"\nFailures ({len(failures)})" if failures else "\nFailures: none")
    for x in failures:
        failed = ",".join(s for s in SCORERS if not x[s])
        print(f"  {x['id']} run {x['repeat']} [{failed}] expected {x['expected_decision']} "
              f"/ {x['expected_order']}  got {x['actual_decision']} / {x['actual_order']}"
              + (f"  ({x['error']})" if x["error"] else "")
              + (f"  model said {x['model_decision']}" if x["model_decision"] != x["actual_decision"] else ""))


# BOOK: Prove
def upload_to_phoenix(rows: list[dict], results: list[dict], label: str, repeat: int,
                      eval_set: str = "golden") -> None:
    """Log the runs as a Phoenix experiment on a dataset built from the golden set.

    The agent already ran (and was traced) above, so the experiment task just replays
    the stored result for each example; the evaluators return the stored scores.
    """
    from phoenix.client import Client

    client = Client()
    csv_path = EVAL_SETS[eval_set]
    # The name carries a hash of the file, so editing a set creates a new dataset.
    name = f"access-request-{eval_set}-set-{hashlib.sha256(csv_path.read_bytes()).hexdigest()[:8]}"
    try:
        dataset = client.datasets.get_dataset(dataset=name)
    except Exception:
        dataset = client.datasets.create_dataset(
            name=name, csv_file_path=csv_path,
            input_keys=["request", "human_response"],
            output_keys=["expected_first_tool", "expected_decision", "expected_order",
                         "expected_user", "expected_resources"],
            metadata_keys=[k for k in ("id", "pillar", "notes", "seed") if k in rows[0]],
            dataset_description=f"{eval_set} set for the access-request agent ({len(rows)} rows).")

    queues = {r["id"]: [x for x in results if x["id"] == r["id"]] for r in rows}

    def task(metadata):
        return queues[metadata["id"]].pop(0)

    def scorer(name):
        def evaluate(output):
            return float(output[name])
        evaluate.__name__ = name
        return evaluate

    client.experiments.run_experiment(
        dataset=dataset, task=task, evaluators={s: scorer(s) for s in SCORERS},
        experiment_name=label, repetitions=repeat, print_summary=False,
        experiment_metadata={"label": label, "repeat": repeat,
                             "blocked_total": sum(x["blocked"] for x in results),
                             "out_of_scope_total": sum(x["out_of_scope"] for x in results)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeat", type=int, default=1, help="runs per row; also reports pass^N")
    parser.add_argument("--label", help="experiment name (default v3-scope-check / v1-prompt-only)")
    parser.add_argument("--prompt-only-checks", action="store_true",
                        help="turn off the loop's checks (check_user first, scope)")
    parser.add_argument("--no-phoenix", action="store_true", help="no tracing, no experiment upload")
    parser.add_argument("--rows", help="comma-separated row ids, e.g. GS03,GS15")
    parser.add_argument("--compromised", action="store_true",
                        help="swap in a scripted compromised model (implies --set adversarial)")
    parser.add_argument("--set", dest="eval_set", choices=EVAL_SETS, default="golden",
                        help="golden (default) or adversarial")
    args = parser.parse_args()

    enforce = not args.prompt_only_checks
    client = None
    if args.compromised:
        from evals.compromised_model import CompromisedModel
        client, args.eval_set = CompromisedModel(), "adversarial"
    label = args.label or ("v3-scope-check" if enforce else "v1-prompt-only")
    if args.eval_set != "golden" and not args.label:
        label = f"{args.eval_set}-{label}"  # never overwrite the golden-set results
    if args.compromised and not args.label:
        label = label.replace("adversarial-", "compromised-")
    if args.rows and not args.label:
        label += "-rows-" + args.rows.replace(",", "-")  # a partial run never overwrites a full one
    store.set_run_label(f"eval:{label}")  # every audit-log line from this run carries it
    if not args.no_phoenix:
        agent.setup_tracing()

    rows = load_rows(args.eval_set, args.rows)
    print(f"{label}: {len(rows)} rows x {args.repeat}, model {client.name if client else agent.model_id()}, "
          f"loop checks {'enforced in code' if enforce else 'off (prompt only)'}")
    results = []
    for k in range(1, args.repeat + 1):
        for row in rows:
            results.append(run_row(row, k, enforce, label, client))
            print(".", end="", flush=True)

    path = save_csv(results, label)  # written before the upload, so it survives a failed upload
    print_report(rows, results, args.repeat)
    print(f"\nResults saved to {path.relative_to(EVALS_DIR.parent)}")

    if args.rows and not args.no_phoenix:
        print("Partial run (--rows): traces sent, experiment upload skipped.")
    elif not args.no_phoenix:
        try:
            upload_to_phoenix(rows, results, label, args.repeat, args.eval_set)
            print(f"Phoenix experiment '{label}' logged (http://localhost:6006).")
        except Exception as err:
            print(f"Phoenix upload failed ({type(err).__name__}: {err}). Upload {path.name} by hand.")
    store.reset_data()


if __name__ == "__main__":
    main()
