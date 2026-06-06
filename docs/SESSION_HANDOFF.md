# Session Handoff — Morgan Cash MRI

**Last updated:** 2026-06-05 (end of the S7 Phase-1 session). This is the fast "where are we, what's next" page for the next session. Authoritative detail lives in `SPRINT_TRACKER.md` (status), `DECISIONS.md` (the C-/D-/FU- ledger), `SHARED_COMPONENTS.md`, `TESTING_FRAMEWORK.md`, `RUNBOOK.md`.

---

## Where we are (2026-06-05)

S0–S7 Phase-1 are **in PROD `gold`** (`mca_mri.gold`). The spine is complete and the first agentic layer is live.

| Sprint | State |
|---|---|
| S0 Ingestion → silver | ✅ PROD |
| S1 Identity + Deal table | ✅ PROD `gold` |
| S2 Features + amortization clock (Appendix A) | ✅ PROD `gold` |
| S3 Rung classifier + state machine + event log (Appendix B) | ✅ PROD `gold` |
| S4 Activation + Book Health + daily queue | ✅ PROD `gold` |
| S5 Offer Engine | ◑ offline modules built + tier-1 green; **MRI-side cloud build gated on FU-501** (handoff contract with the routing team) |
| S6 Prediction | ✅ PROD `gold` (lifetimes BG/NBD + Gamma-Gamma + lifelines Cox; **FU-602** PyMC Bayesian upgrade open) |
| **S7 Phase-1 Data Steward (agentic extraction)** | ✅ **PROD `gold`** — D-706 labeled accuracy **6/6**, `failures: []` (C-024) |
| Activation surface | ✅ read-only Lakeview dashboard over PROD `gold` (C-021); dashboard_id `01f160f949351871b77829f9bf12c942` |

**Tests:** 302 tier-1 green (`python -m pytest -q`). Tier-2 recons run on Databricks (`tests/tier2/`), `gold_test` first then PROD with `allow_prod=True` (RUNBOOK).

## What just shipped (this session — S7 Phase-1)
The **Data Steward** (Framework §5.9 — *agents EXTRACT, the spine COMPUTES*): a Databricks Foundation Model (`databricks-claude-sonnet-4-5`) reads each defaulted deal's free-text `silver.deals.notes`, proposes a default-cause label; deterministic tier-1-tested tools (`common/agents/`) gate + ground it; the S3 classifier re-runs and routes on the APPLIED result. The agent never writes a spine table — only `gold.merchant_extraction` (+`_current`) + `agent_extraction` events.
- **Outcome:** 5 of 6 previously-`unknown` defaults resolved → distressed-exit (incl. **Starr**, whose "$250 clawback" on a defaulted deal = `true_default`, NOT early-payoff); 1 (Zeek, no cause signal) correctly abstained → human review.
- **D-706 accuracy gate** is permanent in `tests/tier2/recon_extraction.py` (`DEFAULT_SUBTYPE_LABELS`, `ACCURACY_BAR=0.80` + a regression guard) — it guards PROD on every future run. Operator-confirmed labeling policy (C-024): an explicit "defaulted" note = `true_default`; a clawback on a defaulted deal = `true_default`; no cause signal = abstain (`unknown`).
- **FU-701** fixed en route: `gold_rung.compute_event_log` now null-fills missing wide-log columns before the schema projection.

## Key files (S7)
- `src/common/agents/{default_subtype,grounding,data_steward}.py` — deterministic tools + the fuzzy half (prompt + parser + pure `build_extraction_rows` orchestration; injected `predict_fn`, tier-1 testable).
- `src/transform/gold_extraction.py` — Spark driver + `databricks_chat_predict_fn` (FM via `databricks-sdk` serving client — serverless has no `mlflow`).
- `tests/tier2/{recon_extraction,run_tier2_extraction}.py` — tier-2 recon + D-706 gate.
- `src/transform/gold_rung.py` — reads APPLIED `resolved_default_subtype` (backward-compatible).

---

## Recommended next steps (pick one)

1. **S8 — Advisory comms + the remaining agents (Composer, Structure Advisor) + compliance gate.** The next major sprint per the roadmap. The spine + Data Steward give it grounded facts to articulate; the compliance gate (state-aware disclosure, advice-vs-specific-offer) is first-class architecture (Framework §2.4). Decisions to open first (D-801…).
2. **S7 Phase-2 — Statement Analyst.** Highest-value remaining *extraction* work: OCR + transaction-line classification of bank-statement PDFs → true concurrent positions + total weekly burden → feed `active_position_cnt`/`burden_ratio`/`est_weekly_revenue` (advances FU-301). **Gated on D-702** — the statement-ingestion spike (statements confirmed to live in SF Documents/attachments; needs an ingestion path + a labeled sample, mirroring the D-706 pattern that worked well for the Data Steward).
3. **Close an open follow-up:** FU-501 (offer handoff contract with the routing team → unblocks the S5 cloud build), FU-602 (PyMC Bayesian upgrade — needs interactive ML-runtime debugging; lifetimes v1 is in prod), FU-401 (governed Salesforce write-back, sandbox-first).

**Suggested default:** kick off the **D-702 statement-ingestion spike** to scope S7 Phase-2 — it's the natural continuation of the agentic extraction work and reuses the now-proven grounding + labeled-sample + accuracy-gate pattern. If you'd rather build breadth than depth, **S8** is the bigger roadmap step.

## Binding working rules (do not drift — see `GENERAL_INSTRUCTIONS.md`)
- **Plan first** (no code without an approved plan); SDLC stages; **ASK before any cloud build/deploy** — build on `*_test` first, PROD needs explicit approval + `allow_prod=True` (Rule 5).
- Shared logic lives **once** in `common/` (Rule 3 — no duplicate numbers/logic); maintain + run tier-1/tier-2 tests every cycle; thorough docs in the same change.
- **Never surface/trust** SF stored `Remaining Balance` / `Percentage Paid` / `Estimated Renewal Date` (`_sf_stored_*` no-surface guard). Recompute everything time-dependent daily (Appendix A).
- **Secrets never touch chat/commits/notebooks/shell.** Git: commit/push only when asked; branch first if on default; commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Databricks CLI via **PowerShell** (Git Bash mangles `/Workspace` paths); `--json @file` written UTF-8-no-BOM; Foundation Models via the `databricks-sdk` serving client on serverless.

## Environment quick-reference
- Catalog `mca_mri`; schemas `bronze`/`silver`/`gold` (+ `gold_test` mirror). Point-in-time gold tables keyed `(merchant_id, <run>_date)`, append-only, idempotent via `replaceWhere`, with `_current` views.
- Tier-2 staging dir: `/Workspace/Users/venkat@morgancash.com/mri_tier2` (deleted after each run). Starter Warehouse id `526a06bbae2df35b`. FM endpoint `databricks-claude-sonnet-4-5` (verify: `databricks serving-endpoints list`).
- Four validation merchants (Framework §8): Starr (now true_default→distressed-exit), One Big Promotion (dormant→win-back), Tom Snell (new/establishing), Wolf (serial rapid re-up).
