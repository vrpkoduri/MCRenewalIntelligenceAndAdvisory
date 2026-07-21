# Session Handoff — Morgan Cash MRI

**Last updated:** 2026-07-20 (S8 Advisory layer complete, in PROD `gold`; branch `s8-advisory-cloud` ready to merge). This is the fast "where are we, what's next" page. Authoritative detail lives in `SPRINT_TRACKER.md` (status), `DECISIONS.md` (the C-/D-/FU- ledger), `SHARED_COMPONENTS.md`, `TESTING_FRAMEWORK.md`, `RUNBOOK.md`.

---

## Where we are (2026-07-20)

S0–S8 are **in PROD `gold`**. The deterministic spine is complete, **both §5.9 extraction agents (Data Steward + Statement Analyst) are live, and the S8 Advisory layer — the Composer + Structure Advisor behind the first-class compliance gate — is now in PROD.** The next merchant-facing step (actually delivering advisories) is S9+.

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
| S7 Phase-2 Statement Analyst | ✅ PROD `gold` + merged to `main` — D-711 positions 10/10 (C-025…C-030) |
| **S8 Advisory (Composer + Structure Advisor + compliance gate)** | ✅ **PROD `gold`** — 412 merchants, D-807 8/8 (C-031/C-032/C-033); branch `s8-advisory-cloud` **ready to merge** |
| Activation surface | ✅ read-only Lakeview dashboard over PROD `gold` (C-021); dashboard_id `01f160f949351871b77829f9bf12c942` |
| **S9+ Merchant delivery / app** | **next major sprint** (S8 composes + gates; it does NOT send) |

**Tests:** 355 tier-1 green (`python -m pytest -q`). Tier-2 recons run on Databricks (`tests/tier2/`), `gold_test` first then PROD with `allow_prod=True` (RUNBOOK).

## What just shipped — S8 Advisory layer (C-031 / C-032 / C-033)
The **Advisory Composer + Structure Advisor** turn the spine's computed facts (clock paydown/balance, positions, the S5 renewal-vs-buyout math, predictions) into **honest, grounded, merchant-facing advisories**, and **every output passes a first-class deterministic compliance gate** (advice-vs-specific-offer + state-aware disclosure). Framework §2.3 (grounded-only, "don't take money" is valid) / §2.4 (gate is real architecture) / §5.9 (agents articulate, deterministic code owns every fact + the gate verdict). **S8 COMPOSES + GATES; it does NOT send** — no outbound comms, no SF write, no spine change; a BLOCKED/ungrounded advisory is stored + auditable but never marked deliverable.
- **Pipeline:** gold `_current` views (clock/rung/predictions + most-recent active position) → deterministic **fact pack** → LLM **Composer** draft → **grounding validator** (0 invented numbers) → **compliance gate** (PASS/BLOCKED) → `gold.merchant_advisory` (+`_current`) + `advisory_composed`/`compliance_checked` events.
- **PROD (C-033):** 412 active merchants → **412 compliance PASS / 0 blocked / 0 rejected; 394 applied / 18 review**; D-807 labeled accuracy **1.0 (8/8)**; gate integrity 0 blocked-but-deliverable / 0 applied-not-pass. 279 advice / 133 factual-summary / 0 specific-offer.
- **Honest behavior verified:** barely-paid merchants (e.g. Bruno's $132k / 0% paid, Wolf) → *"wait and pay down"* with real double-dip cost; near-paid-off → *renewal-eligible*; thin/ambiguous data → held for REVIEW, never guessed. The v1 run caught benign reformatting → Composer **v2**; the full-book run caught nonsensical near-payoff buyouts + uncomputed consolidated payments → **C-032** (S5 near-payoff ceiling) + Composer **v3**.
- **Key files:** `common/compliance/{classify,disclosure,gate}.py`, `common/advisory/{factpack,structure_advisor,composer}.py`, `transform/gold_advisory.py`, `tests/tier2/recon_advisory.py`. Full arc in DECISIONS C-031…C-033 + [`SPRINT_8_PLAN`](sprints/SPRINT_8_PLAN.md).

## Recommended next steps
1. **Merge `s8-advisory-cloud` → `main`** (PR ready) and, optionally, **surface `merchant_advisory_current` on the dashboard** (read-only Merchant 360 tile) — quick, no new cloud build.
2. **FU-501 — the offer handoff with the routing team** (the natural unlock): it lights up the **specific-offer** half of the advisory layer (currently 0 specific-offers — the Composer gives advice/structure only until real matched-funder terms exist), AND unblocks the S5 offer cloud build. **Suggested primary next.**
3. **Pre-delivery polish (for S9+):** clean money/percent **display formatting** at the fact-pack layer (grounded numbers currently print as raw floats, e.g. `51499.4999`), before any advisory is shown to a merchant.
4. **S9+ — merchant delivery / app:** the actual outbound surface (the first thing that *sends* an advisory) — its own sprint, gated, with delivery-time compliance (the S8 gate is the foundation).
5. **Operational maturity — FU-707:** schedule the spine + agents as DAB jobs (today only S0 bronze CDC is automated). **Other open follow-ups:** FU-703 (rotate SF secret), FU-602 (PyMC upgrade), FU-401 (governed SF write-back), FU-704/705/706.

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
