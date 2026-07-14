# Testing Framework (Master)

The single source for test strategy, types, fixtures, and the per-sprint register (GENERAL_INSTRUCTIONS Rule 4). One consolidated suite, appended to as we build, **run in full after every build piece**. A build piece is not "done" until its tests exist and the whole suite is green.

## Two-tier strategy (driven by local tooling)

Local machine has Python 3.12 + pytest but **no Spark/Java**. So:

- **Tier 1 — local, fast (pure Python).** Constants, field-map integrity, contract↔code consistency, DQ rule *semantics* (`dq/predicates.py`), no-surface guard, fixture validity. Run on every change: `python3 -m pytest -q`.
- **Tier 2 — Databricks (Spark).** Transforms, schema, reconciliation, integration, E2E — run on the workspace against **`_test` mirror schemas** (house convention), or locally if `pyspark`+JDK are installed (uncomment in `requirements-dev.txt`). Marked `@pytest.mark.spark`.

Tier-1 pins the semantics; Tier-2 verifies the Spark implementation mirrors them.

## Test types maintained

| Type | Tier | What it covers | Status |
|---|---|---|---|
| Unit | 1 | Pure functions: DQ predicates, constants, field maps | ✅ S0 |
| Data-quality | 1/2 | 0/blank-as-missing, date-sanity, RTR cross-check produce correct flags | ✅ S0 (tier-1); tier-2 pending data |
| Data-integrity | 2 | Keys/grain/uniqueness, deal→merchant FK, **no-surface** of `_sf_stored_*` | ◑ guard ✅ (tier-1); table-level pending data |
| Reconciliation | 2 | `count(silver.deals where stage=Funded)` == SF funded count (± explained) | ⏳ needs bronze |
| Scenario | 1/2 | Four validation merchants → expected outcomes | ✅ S0 (static/DQ level) |
| Integration | 2 | bronze→silver via the contract (synthetic bronze fixture) | ⏳ needs Spark/bronze |
| E2E | 2 | Full pipeline on a sample book | ⏳ |
| Regression | 1/2 | Entire accumulated suite re-run every cycle | ✅ ongoing |

(Plus, in later sprints: point-in-time correctness for the Feature Store (S2), model backtest sanity (S6), performance.)

## How to run

```bash
# Tier 1 (local)
python3 -m pytest -q

# Tier 2 (Databricks) — run on the workspace against *_test schemas
python3 -m pytest -q -m spark        # requires pyspark+JDK locally, or run as a Databricks job
```

## Fixtures

- `tests/fixtures/validation_merchants.py` — the four canonical merchants (Starr, One Big Promotion, Tom Snell, Wolf), reused by **all** tiers/types. Synthetic until G1; real records swap in without changing tests.

## Per-sprint test register

### Sprint 0
Added: `test_constants`, `test_field_maps`, `test_contract_consistency`, `test_dq_predicates`, `test_no_surface_guard`, `test_validation_merchants` (+ `test_offers_field_history_maps`).
**Result (2026-05-29): 32 → later 41 passed (tier-1).** Tier-2: silver recon PASSED on `silver_test` then promoted to prod (`recon_silver_deals`, `recon_aux`).

### Sprint 1 (identity + gold)
Added: `test_identity_normalize` (37, ported AATM cases), `test_identity_match`, `test_identity_keys`, `test_gold_maps`. **106 tier-1 green.** Tier-2 `recon_gold` PASSED on `gold_test` → prod.

### Sprint 2 (amortization clock, Appendix A)
Added: `test_clock` (+59) — A.2 arithmetic, A.3/A.4 calendar + inverse-solve, A.5b closure (Starr default-note invariant), A.5 roll-up, four merchants vs `expected_clock`, no-surface. **165 tier-1 green.** Tier-2 `recon_clock` PASSED on `gold_test` → prod.

### Sprint 3 (rung classifier + event log, Appendix B)
Added: `test_rung` (42), `test_eventlog` (10). Covers the Step-0 gate routes + order + dormancy boundary; waterfall first-match-wins + **stress-override-pulls-down**; Disciplined AND-conditions; D-303 unlinkable-renewal not a disqualifier; Growth/Graduate gating; `rapid_reup_flag` paydown-primary/day-gap-fallback/worsening-terms (D-302); borderline confidence + missing-data-never-lowers + Unclassified-floor (D-306); `direction_of_travel`; the **four B.5 merchants** end-to-end; output-object shape; rung-map column/verdict/no-surface invariants; event-log append-only/no-mutation/transition-only-on-change/keying (D-305).
**Result (2026-06-02): 52 new → 217 passed, 0 failed (tier-1).**
**Tier-2 (`recon_rung` + `run_tier2_rung`) PASSED on `gold_test` (2026-06-02, `failures: []`):** built `gold_test.merchant_rung` (+`_current`) + `merchant_event_log` via `build_gold_rung` (serverless one-time job, staging cleaned up). Green: whole-book coverage **2,125/2,125** (key unique), schema contracts, enum/bounds/route domains, gated-null-rung + `is_gated` consistency, **Unclassified bucket** `is_unclassified ⇔ (active ∧ rung null)`, no-surface on both tables, one classification event/merchant + unique `(merchant_id,event_type,event_ts)`, `_current` = latest run, **two-run state machine** (prior partition unchanged/append-only, `_current` advanced). **Unclassified pile quantified:** 141 (top missing `disclosed_positions_cnt`/`est_weekly_revenue`/`burden_ratio`/`est_paydown_pct`). Diagnostics: dormant 1,458 / active 422 / new-establishing 239 / defaulted 6; Serial 169 / Distressed 65 / Disciplined 47; rapid_reup 287; renewal_chain_incomplete 503; Wolf→Serial (B.5).
**PROD `gold` promotion PASSED 2026-06-02** (`schema=gold, allow_prod=True, second_run=False` — clean single partition; recon identical, `failures: []`): `mca_mri.gold.merchant_rung` (2,125) + `merchant_rung_current` (view) + `merchant_event_log` (2,125 events).

### Sprint 4 (activation: state machine + plays + Book Health, §6/5.8)
Added: `test_activation` (21), `test_bookhealth` (8). Covers the state machine (clock×rung×lifecycle incl. boundaries + the four merchants); the play priority matrix (distressed-beats-all, slide-before-posture, rung/timing plays, unclassified); SLA reuses the business-day calendar + every play has a tier; grounded next-actions; composed `activate_merchant`; Book Health metric v1/deferred split + None-safe math; activation/book-health/SF-writeback no-surface + floor/dual-audience-only invariants.
Later added: 4 S4 event-log builder tests (`test_eventlog`) + 1 Book Health deferral test (`test_bookhealth`). **Result (2026-06-02): 35 new total → 251 passed, 0 failed (tier-1).**
**Tier-2 (`recon_activation` + `run_tier2_activation`) PASSED on `gold_test` (2026-06-02, `failures: []`):** built `gold_test.merchant_activation` (+`_current`) + `daily_queue` + `book_health` (+ 3 `_current` views) via `build_gold_activation`/`build_gold_book_health` (serverless, staging cleaned up). Green: whole-book coverage **2,125/2,125** (key unique), schema contracts, `current_state`/`active_play` enums + `play_sla_due` non-null + `play_owner_is_missing` 100%, no-surface on all three tables, `daily_queue` unique `queue_rank` over the book, Book Health Σ rung_distribution = 2,125, the two v1 views populate (`renewal_performance` intentionally empty — deferred), **two-run state machine** (prior partition + S3 classification events untouched, `_current` advanced). Wolf → renewed / serial-renewal-vs-buyout.
**PROD `gold` promotion PASSED 2026-06-02** (`schema=gold, allow_prod=True, second_run=False` — clean single partition; recon identical, `failures: []`): `mca_mri.gold.merchant_activation` (2,125) + `merchant_activation_current` + `daily_queue` + `book_health` (22 rows) + 3 `_current` views. FU-401 (Salesforce write-back + Lakebase serving) remains gated.

### Sprint 5 (Offer Engine integration — reuse the routing engine, §6/5.7)
Added: `test_offer` (19). Covers the renewal-vs-buyout double-dip math + structure recommendation (Wolf → wait-and-paydown), the suitability gate (suppress a double-dip buyout / wait), candidate `eligible_offer_types`, profile assembly with honest missing-field flags, and map/no-surface/Reuse invariants.
**Result (2026-06-02): 19 new → 270 passed, 0 failed (tier-1).**
Pending: **D-501 reuse-mechanism decision** before `transform/gold_offers.py`; then tier-2 on `gold_test` — reconcile a sample to the existing engine's `routing_program_evaluations` (prove reuse, not reinvent), whole-book coverage, suitability-gate suppression, offer-event append integrity. (Spike: existing evaluations have 0 id-overlap with the funded book → the engine must run on MRI profiles.)

### Sprint 6 (Prediction — feature/label derivation; adopted models, §6/11.2)
Added: `test_prediction` (14). Covers RFM features (single-deal → frequency 0; multi-deal recency/frequency/T/monetary; missing amount not faked), survival labeling + **censoring** (single-deal = censored-not-dropped), confidence (posterior-width else monotonic history; insufficient-history threshold), and map/no-surface/`model_version` invariants.
**Result (2026-06-02): 14 new → 284 passed, 0 failed (tier-1).**
Readiness spike (read-only): 796/2,125 repeat merchants (training pop; 358 active) vs 1,329 single-deal (insufficient_history); 1,834 intervals, median 81 days → real Cox signal.

### Sprint 7 (agentic extraction — Data Steward, §5.9)
Added: `test_agents` (17 total = 12 deterministic-tool + 5 LLM-agent). The deterministic tools cover the default-subtype mapper (synonyms → enum; junk → unknown, never guess), the confidence gate (low-conf → unknown+review+do-not-fund; concrete+confident → applied+route), the grounding contract (ungrounded → rejected & not-applicable, model_version + known type required), the lifecycle gate honoring a resolved sub-type (agent EXTRACTS / gate ROUTES; unresolved unchanged), and map/no-surface/event-type/threshold invariants. The LLM-agent tests (no network — injected `predict_fn`) cover `parse_response` (clean + prose-wrapped JSON; **defensive**: malformed/OOV-label → unknown, confidence clamped to [0,1]; every ALLOWED label round-trips), `classify_default_cause` (calls the model with the grounded system prompt; blank/None Notes short-circuit — no call), and `build_extraction_rows` (the full offline agent→gate→ground path: APPLIED+routes, low-conf→REVIEW, no-notes→ungrounded→REJECTED).
**Result (2026-06-05): 17 new (12 deterministic + 5 LLM-agent) → 302 passed, 0 failed (tier-1).**

**Tier-2 PASSED on `gold_test` (2026-06-05, `failures: []`):** `build_gold_extraction` (`recon_extraction.py` + `run_tier2_extraction.py`) via the Databricks Foundation Model **`databricks-claude-sonnet-4-5`** (SDK serving client, temperature 0) over all **6 `closed_default`** deals, then an S3 re-run. Green: schema == `merchant_extraction_schema()`; coverage **6/6** deals (key `(merchant_id,deal_id,extraction_run_date,extraction_type)` unique); grounding (D-705) — every APPLIED row grounded + ≥ threshold, no REJECTED row has a source_ref; confidence ∈ [0,1]; model_version 100%; extraction_type == default_subtype; review_status ∈ ReviewStatus.ALL; no-surface; `_current` = run_date; **6 `agent_extraction` events** emitted (S3/S4 rows intact). Fixed **FU-701** en route (gold_rung event-log schema projection).

**Sprint 7 Phase 2 (Statement Analyst — §5.9 / C-025 / C-026).** Two offline test files, built ahead of the gated statement ingestion (the labeled-sample accuracy gate D-711 follows once real statements are fetched).
- `test_statement_positions` (10) — the deterministic counter: `normalize_to_weekly` (Daily/Weekly reuse the S2 clock; Biweekly/Monthly via `Thresholds`; missing/unrecognized → None, never fabricated), `concurrent_position_count` (Wolf-flavored — excludes Morgan Cash's own so the clock would ADD other-funder positions; funder dedupe; unlabeled streams counted distinctly; empty → 0), `is_morgan_cash` default-false, `total_weekly_debit` (MC-inclusive total; unsizable streams contribute 0), `est_weekly_revenue` (period-normalized; None on missing/zero period — no div-by-zero), `summarize_statement`, and `statement_is_fresh` (C-026 #2 window — fresh/stale/future/unknown).
- `test_statement_analyst` (7) — the agent half (injected fake `predict_fn`, no network): tolerant/defensive `parse_response` (clean + prose-wrapped JSON; junk → empty/zero-confidence; malformed positions coerced/dropped), blank-text short-circuit (no LLM call), and `build_statement_rows` emitting the three grounded signals — with the **C-026 guardrails**: positions exclude MC / debit includes MC / revenue is period-normalized; **#2 freshness** (stale `as_of_date` → all rows REVIEW, source_ref carries `@as_of`); **#3 revenue haircut** (confidence 0.80 → positions/debit APPLY but revenue 0.68 → REVIEW); a None/unknown revenue is never APPLIED. **#1 (advisory-only) is structural** — nothing in the module feeds the rung waterfall.

- `test_statement_fetch` (4) — the pure helpers in `ingestion/statement_fetch.py`: `is_statement` (doctype tag ∪ title heuristic), `location_to_deal_map` (opp + submission → funded deal), `covered_statement` (only funded statements pass), `statement_file_row` (shape + `as_of_date` = CreatedDate proxy). Network/Volume I/O is the driver (run on Databricks).

**Result: 22 new (10 positions + 8 analyst + 4 fetch) → 324 passed, 0 failed (tier-1)** (the +1 = `build_statement_extractions` audit-trail test added with JSON persistence).

**Tier-2 D-711 positions accuracy gate — PASSED on `gold_test` then PROMOTED to PROD (2026-07-13/14, C-028/C-030):** `recon_statement_analyst.STATEMENT_LABELS` = operator-confirmed true position counts for 10 sampled deals; the recon scores the extracted count (±1, regardless of review_status). **labeled_accuracy 1.0 (10/10), `failures: []`**; mechanical gates (schema/coverage/keys/grounding/no-surface/`_current`) green. Revenue accuracy deferred (FU-704). A `_current` multi-stream bug was caught + fixed pre-PROD (C-029 — per-(merchant,deal,type) latest so the Data Steward's `default_subtype` isn't evicted; verified in PROD: both streams current). **PROD promotion:** OCR → `silver.statement_text` (324 files) + `build_gold_statement_extraction(schema=gold, allow_prod=True)` → 237 rows (14 applied / 223 review) + audit(79) + 237 events; advisory-only.

**Binary fetch RUN on Databricks (2026-06-09):** `ingestion.statement_fetch.fetch_statements` via the Client-Credentials token → **324 statement PDFs / 167.9 MB / 79 funded deals** landed in the governed UC Volume `mca_mri.bronze.statements_raw` + `bronze.statement_files`. (Read-only token+VersionData validity test passed first: 200 / 3.32 MB / no 403.) Next: silver OCR → `gold_statement_extraction` → D-711 labeled-sample accuracy gate on `gold_test`.

**D-706 labeled-sample accuracy gate (added to the recon; gates PROD).** `recon_extraction.DEFAULT_SUBTYPE_LABELS` = the 6 closed_default deals' operator-confirmed ground truth (C-024): an explicit "defaulted" note (incl. bare "defaulted") = true_default; a clawback on a defaulted deal = true_default (NOT early_payoff); no default-cause signal = unknown (abstain). `assert_recon` HARD-fails if `labeled_accuracy < ACCURACY_BAR` (0.80) or if any labeled true_default is APPLIED as early_payoff (regression guard). The **v1** prompt scored 4/6 (mis-read Starr/Sai clawbacks as early_payoff); **v2** (`model_version data-steward/claude-sonnet-4-5/v2` — "defaulted" overrides "clawback") **re-ran on `gold_test`: labeled accuracy 1.0 (6/6), `failures: []`** — 5 true_default→distressed-exit, 1 unknown→do-not-fund+review (Zeek, correct abstain). **Then PROMOTED + re-verified on PROD `gold` (2026-06-05, `schema=gold, allow_prod=True`): `failures: []`, accuracy 1.0** — the gate guards PROD too. Phase 2 (Statement Analyst) deferred (D-702).
**Tier-2 PASSED on `gold_test` (2026-06-02, `failures: []`):** `build_gold_predictions` on a single-node 14.3 LTS ML job (v1 via `lifetimes` BG/NBD+Gamma-Gamma+CLV + lifelines Cox, cluster-level libs + UC SINGLE_USER; PyMC-Marketing crashed the driver → FU-602). Green: coverage **2,125** (key unique), schema, p_alive/p_defection/confidence ∈ [0,1], next-event non-null, `model_version` stamped, **insufficient_history ⇔ rfm_frequency==0**, no-surface, `_current` = run_date. Diagnostics: insufficient_history 1,329, repeat 796, CLV non-null 796 (single-deal correctly null), Cox fitted; Wolf p_alive 0.42 / next-event 2026-08-22 / CLV ~$69k. Full time-split backtest deferred with the PyMC upgrade (FU-602). PROD `gold` promotion gated (Rule 5).
