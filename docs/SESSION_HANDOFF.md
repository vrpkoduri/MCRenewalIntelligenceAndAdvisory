# Session Handoff — Morgan Cash MRI

**Last updated:** 2026-07-14 (S7 Phase-2 Statement Analyst complete, in PROD, merged to `main`). This is the fast "where are we, what's next" page. Authoritative detail lives in `SPRINT_TRACKER.md` (status), `DECISIONS.md` (the C-/D-/FU- ledger), `SHARED_COMPONENTS.md`, `TESTING_FRAMEWORK.md`, `RUNBOOK.md`.

---

## Where we are (2026-07-14)

S0–S7 are **in PROD `gold`**. The deterministic spine is complete and **both §5.9 extraction agents (Data Steward + Statement Analyst) are live.** S8 (advisory comms + compliance) is the next major sprint.

| Sprint | State |
|---|---|
| S0 Ingestion → silver | ✅ PROD |
| S1 Identity + Deal table | ✅ PROD `gold` |
| S2 Features + amortization clock (Appendix A) | ✅ PROD `gold` |
| S3 Rung classifier + state machine + event log (Appendix B) | ✅ PROD `gold` |
| S4 Activation + Book Health + daily queue | ✅ PROD `gold` |
| S5 Offer Engine | ◑ offline modules built + tier-1 green; **MRI-side cloud build gated on FU-501** |
| S6 Prediction | ✅ PROD `gold` (lifetimes BG/NBD + Gamma-Gamma + lifelines Cox; **FU-602** PyMC upgrade open) |
| S7 Phase-1 Data Steward | ✅ PROD `gold` — D-706 6/6 (C-024) |
| **S7 Phase-2 Statement Analyst** | ✅ **PROD `gold` + merged to `main`** — D-711 positions 10/10 (C-025…C-030) |
| Activation surface | ✅ read-only Lakeview dashboard over PROD `gold` (C-021); dashboard_id `01f160f949351871b77829f9bf12c942` |
| **S8 Advisory comms + Composer/Structure Advisor + compliance** | **next major sprint** |

**Tests:** 324 tier-1 green (`python -m pytest -q`). Tier-2 recons run on Databricks (`tests/tier2/`), `gold_test` first then PROD with `allow_prod=True` (RUNBOOK).

## What just shipped — S7 Phase-2 Statement Analyst (C-025…C-030)
The **Statement Analyst** (Framework §5.9 — *agents EXTRACT, deterministic tools COUNT, the spine COMPUTES*): OCR'd bank statements → an LLM agent classifies transaction lines → deterministic tools count **true concurrent MCA positions** (incl. other funders Salesforce can't see) + weekly debit burden + operating revenue → grounded/gated/logged into `gold.merchant_extraction` (+ `statement_extraction_audit`). **Advisory-only (C-026 #1): it surfaces burden/positions but never changes a rung.**
- **Pipeline:** SF Files → headless Client-Credentials fetch → UC Volume `bronze.statements_raw` (324 PDFs) → `silver.statement_text` (pdfplumber OCR, 317/324 text-OK) → `statement_analyst` agent (prompt **v2** — excludes leases / term-loans / vendor bills / cards) + `positions` counter → `gold.merchant_extraction`.
- **Coverage (honest):** 79 funded deals (2.0% of book; 28.4% of 2025+ fundings) / ~35 active merchants — a **go-forward** capability that grows with new fundings, not a retroactive book-wide fix.
- **Accuracy:** D-711 positions gate **10/10** on operator-confirmed labels (±1); permanent PROD gate. Revenue deferred (FU-704, advisory/soft).
- **PROD (C-030):** 237 rows (14 applied / 223 review) + audit (79) + 237 `agent_extraction` events; `_current` verified to hold BOTH streams (the C-029 fix kept the Data Steward's `default_subtype` intact — applied 5, Starr).
- **Key files:** `common/agents/{positions,statement_analyst}.py`, `transform/{silver_statement_text,gold_statement_extraction}.py`, `ingestion/statement_fetch.py`, `tests/tier2/recon_statement_analyst.py`. Full arc in DECISIONS C-025…C-030 + [`D-702 spike + plan`](D-702_statement_ingestion_spike.md).

## Recommended next steps
1. **S8 — Advisory comms + Composer + Structure Advisor + compliance gate** (the next major sprint). The spine + both extraction agents give grounded facts to articulate; the compliance gate (state-aware disclosure, advice-vs-specific-offer) is first-class architecture (Framework §2.4). Open decisions **D-801…** first. **Suggested primary next.**
2. **Realize + close the Phase-2 tail (quick parallel cleanup):** **FU-703** (rotate the Consumer Secret — near-term security); **dashboard surfacing** of statement burden/positions on Merchant 360 (`burden_source=statement`, `as_of_date`); then **FU-705** (funder registry), **FU-704** (revenue-outlier guard), **FU-706** (routing-team alignment).
3. **Operational maturity — FU-707:** schedule the spine as DAB jobs (silver → clock → rung → activation → agents). Today **only S0 bronze ingestion is automated** (6h CDC); everything else is run as gated manual promotions.
4. **Other open follow-ups:** FU-501 (offer handoff → unblock S5 cloud), FU-602 (PyMC upgrade), FU-401 (governed SF write-back), FU-301 (NSF signal — now partly served by statement metrics).

## Open follow-ups (ledger)
FU-301 (NSF/servicing signals), FU-401 (SF write-back), FU-501 (S5 handoff contract), FU-602 (PyMC Bayesian), FU-702 (upstream ops to grow statement coverage), **FU-703 (rotate Consumer Secret)**, FU-704 (revenue-outlier guard), FU-705 (funder registry), FU-706 (routing-team shared registry/extraction), FU-707 (daily DAB scheduling). Detail in `DECISIONS.md`.

## Binding working rules (do not drift — see `GENERAL_INSTRUCTIONS.md`)
- **Plan first** (no code without an approved plan); SDLC stages; **ASK before any cloud build/deploy** — build on `*_test` first, PROD needs explicit approval + `allow_prod=True` (Rule 5).
- Shared logic lives **once** in `common/` (Rule 3 — no duplicate numbers/logic); maintain + run tier-1/tier-2 tests every cycle; thorough docs in the same change.
- **Never surface/trust** SF stored `Remaining Balance` / `Percentage Paid` / `Estimated Renewal Date` (`_sf_stored_*` no-surface guard). Recompute everything time-dependent daily (Appendix A).
- **Secrets never touch chat/commits/notebooks/shell.** Git: commit/push only when asked; branch first if on default; commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Databricks CLI via **PowerShell** (Git Bash mangles `/Workspace` paths + uses a different `gh`/HOME); `--json @file` written UTF-8-no-BOM; Foundation Models via the `databricks-sdk` serving client on serverless. **OAuth token expires ~hourly — re-auth with `databricks auth login --profile DEFAULT` when CLI calls fail.**

## Environment quick-reference
- Catalog `mca_mri`; schemas `bronze`/`silver`/`gold` (+ `*_test` mirrors). Point-in-time gold tables keyed `(merchant_id, <run>_date)`, append-only, idempotent via `replaceWhere`, with `_current` views. **`merchant_extraction._current` is per-(merchant,deal,type) latest** (multi-stream: Data Steward + Statement Analyst; C-029).
- Tier-2 staging: stage `src/` + recon as Workspace files, submit a serverless notebook job, delete after (RUNBOOK). Starter Warehouse id `526a06bbae2df35b`. FM endpoint `databricks-claude-sonnet-4-5`.
- Statement fetch: SF **Client Credentials** app (Run-As `integrations@morgancash.com`, read-only), secret scope **`mri-salesforce-api`**; PDFs in UC Volume `bronze.statements_raw` (owner-scoped).
- Four validation merchants (Framework §8): Starr (true_default→distressed-exit), One Big Promotion (dormant→win-back), Tom Snell (new/establishing), Wolf (serial rapid re-up).
