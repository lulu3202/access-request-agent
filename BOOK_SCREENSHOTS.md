# Book screenshots

Ten screenshots, each with the command that reproduces it. Run everything from the repo root with the venv
active. Start Phoenix first for shots 5 and 6:

```bash
uvx --python 3.12 --from arize-phoenix==20.26.1 phoenix serve
```

Tip: use a browser window at least 1280 px wide, so the access table doesn't wrap.

| # | Pillar | Screenshot | How to reproduce |
|---|---|---|---|
| 1 | Control | **Approval card**: the purple card with who / what / "why a human: sensitive resource" and the proposed call as JSON | `streamlit run app.py`, click "Reset demo data", type `Asha Rao needs billing-db access for the quarter close.` |
| 2 | Control | **After Approve**: reply with `GRANTED_AFTER_APPROVAL`, and the access table showing Asha with billing-db | Shot 1, then click **Approve** |
| 3 | Control | **Deny**: `DENIED_BY_HUMAN`, Divya's access unchanged | In the app: `Give Divya Nair admin on prod-admin-console.` then **Deny** |
| 4 | Control | **Emergency stop**: "Pause agent" on, red banner, request ends `STOPPED` | Turn on **Pause agent** in the sidebar, type `Give Divya Nair access to Jira.` (turn it off afterwards) |
| 5 | Prove | **Phoenix trace with the human decision inside it**: `access_request` → `check_user` → `human_approval` → `grant_access`, with the `human_approval` span's attributes open (`approval.decision = Approve`, `approval.approver`, `approval.id`, `approval.timestamp`, `approval.requested_call`) | `python evals/run_evals.py --rows GS03`, then in Phoenix open project `access-request-agent`, open the newest trace and click the `human_approval` span |
| 6 | Prove | **Phoenix experiments side by side**: `compromised-v3-scope-check` vs `compromised-v3-prompt-only` (or `v2-enforced-in-code` vs `v1-prompt-only`) | `python evals/run_evals.py --compromised --label compromised-v3-scope-check` and `python evals/run_evals.py --compromised --prompt-only-checks --label compromised-v3-prompt-only`, then Phoenix → Datasets → `access-request-adversarial-set-…` → Experiments → compare |
| 7 | Prove | **Eval summary**: golden-set table with R / T / J, `blocked`, `oos`, pass^3, and the summary block | `python evals/run_evals.py --repeat 3` |
| 8 | Prove / Control | **Compromised model vs the gates**: terminal tables for both runs; `blocked attempts: 6` and `out-of-scope proposals: 2` with checks on, versus AD08's revoke going through with checks off | `python evals/run_evals.py --compromised --no-phoenix` and `python evals/run_evals.py --compromised --prompt-only-checks --no-phoenix` |
| 9 | Recover | **Drill output**: monitor alert, before and after grants, the revoke reason, `PASS` | `python drills/recover_drill.py` |
| 10 | Operate | **Monitor and anomalies**: events per day and by run label, `ALERT terminated user still has access: erik.holm -> jira` (or the app's Operate tab) | `python operate/monitor.py` (or the **Operate** tab in the app) |

Optional, for the "real finding" story in CONTAINMENT_MAP.md: open
`evals/evidence/ad04-unrequested-revoke__adversarial-v2-enforced-in-code.csv`, and in Phoenix the trace
`19395f50c0b59a454eb04b618dd6d6fd` (AD04, run 3: `check_user > revoke_access`).

Shots 2 and 3 change the live data. Click "Reset demo data" or run the drill (it resets at the end)
before reshooting. The audit log keeps everything either way.
