# Shared Components Catalogue

Reusable libraries with stable contracts, centralized so they change in one place (GENERAL_INSTRUCTIONS Rule 3). No block reaches into another's internals; data flows via the gold-table contract.

## Built (Sprint 0)

| Component | Path | Contract / purpose |
|---|---|---|
| `constants` | `src/common/constants.py` | Catalog/schema/table names, SF object names, enums (DealType, PaymentFrequency, BalanceSource, ClosureStatus, Verdict; **S3: LifecycleState, RungState, DefaultSubtype, LifecycleRoute, DirectionOfTravel, EventType; S4: CurrentState, Play, BookHealthView; S5: OfferType, OfferStructure, SuitabilityVerdict, FunderCatalog; S6: EventType.PREDICTION, CLV/insufficient-history/Cox-covariate config**), no-surface set, RTR tolerance, `DEFAULT_NOTE_KEYWORDS`, `RAPID_REUP_MAX_GAP_DAYS`, `APPROACHING_WINDOW_DAYS`/`RENEWED_WINDOW_DAYS`, `PLAY_SLA_BUSINESS_DAYS`, Appendix A/B `Thresholds` (single calibration home). Pure Python. |
| `field_maps` | `src/common/field_maps.py` | SPRINT_0 bronze→silver maps as `FieldSpec` data (deals, field_history) + DQ-derived columns. The rename/typing spec the transform reads. |
| `contract` | `src/common/contract.py` | Loads the authoritative Data Contract xlsx; exposes Deal/Merchant-Gold field→verdict maps for drift tests. |
| `dq.predicates` | `src/common/dq/predicates.py` | Pure-Python DQ semantics: missing-implausible-zero, date-sanity, RTR check. Tier-1 testable; the canonical spec. |
| `dq.rules` | `src/common/dq/rules.py` | Native Spark column expressions mirroring the predicates (no UDFs). |
| `schemas.silver` | `src/common/schemas/silver.py` | Spark schemas for `silver.deals` / `silver.field_history`, derived from `field_maps` (single source). |
| `io.guards` | `src/common/io/guards.py` | No-surface guard — makes CLAUDE.md §2.1 executable (`assert_no_surface`). |

## Built (Sprint 1 — identity + gold)

| Component | Path | Contract / purpose |
|---|---|---|
| `identity.normalize` | `src/common/identity/normalize.py` | Pure normalizers (business name/suffixes, tax_id, phone, state) ported from AATM. Tier-1 testable. |
| `identity.match` | `src/common/identity/match.py` | 4-tier `resolve_merchant` priority chain (master-record → tax_id auto-merge → phone/name+state candidates). |
| `identity.keys` | `src/common/identity/keys.py` | Deterministic `merchant_id` minting + match-key builders. |
| `schemas.gold` | `src/common/schemas/gold.py` | Spark schemas for `gold.deals` / `merchants` / `merchant_crosswalk` (S1) **and** `deal_clock` / `merchant_clock` (S2), derived from the gold field maps. |

## Built (Sprint 2 — amortization clock, Appendix A)

| Component | Path | Contract / purpose |
|---|---|---|
| `clock.amortization` | `src/common/clock/amortization.py` | A.2 scalars: `rtr`, `amount_paid`, `est_current_balance` (floored ≥0), `est_paydown_pct` (capped ≤1). |
| `clock.calendar` | `src/common/clock/calendar.py` | A.3/A.4: `elapsed_payments` (business-day/weekly, term-capped), `business_days_between` + `nth_business_day_after` (inverse), `payments_to_threshold`, `eligible_date`. Holiday-set hook (D-204). |
| `clock.closure` | `src/common/clock/closure.py` | A.5b three-state closure: `has_default_note`, `closure_status` (default note dominates ~100% paydown → `closed_default`), `is_active`. |
| `clock.rollup` | `src/common/clock/rollup.py` | A.5 merchant roll-up: active-position count, weekly debit, burden ratio (null when revenue missing), weakest `balance_source`, primary-position eligibility, tenure. |

All clock functions are pure / Spark-free-at-import (tier-1 testable) and reused inside the Spark columns/UDFs of `transform/gold_clock.py` — the same `dq.predicates ↔ dq.rules` mirror pattern.

## Built (Sprint 3 — rung classifier + event log, Appendix B)

Appendix B engine, decisions signed 2026-06-01 ([SPRINT_3_PLAN](sprints/SPRINT_3_PLAN.md), DECISIONS C-017); **pure modules built + tier-1 green 2026-06-02 (217 tier-1; cloud `transform/gold_rung.py` gated, Rule 5).** Pure / Spark-free-at-import, mirroring `common/clock`.

| Component | Path | Contract / purpose |
|---|---|---|
| `rung.lifecycle` | `src/common/rung/lifecycle.py` | B.2 Step-0 gate: `default_subtype` (unknown→do-not-fund), `is_dormant` (>2× median renewal gap, book-median fallback), `is_new_establishing`, `lifecycle_state` route. |
| `rung.waterfall` | `src/common/rung/waterfall.py` | B.3 first-match-wins waterfall + stress-override-pulls-down: `is_distressed`/`is_serial`/`is_disciplined`/`is_growth`/`is_graduate`, `rung_of`. Includes `rapid_reup_flag` (D-302: prior still-active & <50% paid at new funding, OR ≤45-day gap `RAPID_REUP_MAX_GAP_DAYS`). |
| `rung.confidence` | `src/common/rung/confidence.py` | D-306 borderline-driven `confidence` (missing data NOT penalized) + `direction_of_travel` (climbing/holding/sliding). |
| `rung.classify` | `src/common/rung/classify.py` | Composes gate→waterfall→output object `{lifecycle_state, rung, confidence, missing_signals[], direction_of_travel}`. Single pure entry point. |
| `eventlog.events` | `src/common/eventlog/events.py` | D-305 append-only event builders + schema; v1 = classification + transition events, one wide table keyed `(merchant_id, event_type, event_ts)`. |

Reuses the existing `Thresholds` (no duplicate numbers — Rule 3) + `Type=Renewal` trust (D-303). Consumed by **`transform/gold_rung.py`** (the Spark driver — **built; tier-2 PASSED + promoted to PROD `gold` 2026-06-02, `failures: []`**) which applies the pure engine via UDFs and writes point-in-time `gold.merchant_rung` (+`_current` view) + append-only `gold.merchant_event_log` (D-304/D-305).

## Built (Sprint 4 — activation: state machine + plays + Book Health, §6/5.8)

Decisions signed 2026-06-02 (C-018); **pure modules built + tier-1 green 2026-06-02 (246 tier-1; Spark transforms + serving gated, Rule 5).** Pure / Spark-free-at-import, mirroring `common/rung`. NO Salesforce write in S4 (serving-layer-only, D-403; SF write-back is FU-401). NO merchant comms (S8).

| Component | Path | Contract / purpose |
|---|---|---|
| `activation.state_machine` | `src/common/activation/state_machine.py` | D-401 `current_state` (clock-running/approaching/in-market/renewed/lost-winback) from the S2 clock + S3 rung/lifecycle; `state_changed` (state_transition event). |
| `activation.plays` | `src/common/activation/plays.py` | D-402 `active_play` priority matrix; `play_sla_due` (reuses `clock.calendar.nth_business_day_after`; SLA tiers 2/5/10 business days); `play_owner`; grounded `next_tactical_action`/`next_strategic_nudge` templates. |
| `activation` (`activate_merchant`) | `src/common/activation/__init__.py` | Composed entry point → `{current_state, active_play, play_sla_due, play_owner, next_tactical_action, next_strategic_nudge}`. |
| `bookhealth.metrics` | `src/common/bookhealth/metrics.py` | Framework 5.8 metric registry (v1-available vs deferred S5/S6/S8) + None-safe `pct`/`ratio`/`net_drift`/`distribution`. |
| `schemas.gold` (+S4) | `src/common/schemas/gold.py` | `merchant_activation_schema()`, `book_health_schema()` (tall point-in-time). |

Reuses existing `Thresholds` + the clock business-day calendar (no duplicate numbers — Rule 3) + the S3 rung output / event log (extended with S4 builders `state_transition_event`/`play_fired_event` + 4 nullable cols). Consumed by `transform/gold_activation.py` + `transform/gold_book_health.py` (**Spark drivers — built; tier-2 PASSED + promoted to PROD `gold` 2026-06-02 `failures: []`**) writing point-in-time `gold.merchant_activation` (+`_current`), the `gold.daily_queue` view (sliding-first `queue_rank`), and the `gold.book_health` family (+ 3 `_current` views); `field_maps.SF_WRITEBACK_REFERENCE` documents the FU-401 Salesforce write-back (dedicated `MRI__*` fields).

## Built (Sprint 5 — Offer Engine integration: structure + suitability + offer types, §6/5.7)

D-503…D-508 signed 2026-06-02; **pure modules built + tier-1 green (270 tier-1).** REUSES the existing `mca_funders` routing engine — NO routing/criteria rebuild (CLAUDE.md §6), NO comms (S8), no writes to `mca_funders`. The cloud reuse step (`transform/gold_offers.py`) is **blocked on D-501** (the spike showed reuse-by-id is non-viable — 0 overlap). Pure / Spark-free-at-import.

| Component | Path | Contract / purpose |
|---|---|---|
| `offer.structure` | `src/common/offer/structure.py` | D-506 renewal-vs-buyout: `double_dip_cost` (balance×(factor−1)), `recommend_structure` (wait-and-paydown / buyout / renewal), `structure_evaluation`. Reuses the 50% paydown threshold. |
| `offer.suitability` | `src/common/offer/suitability.py` | The gate (engine proposes / advisory disposes): `suitability_verdict` (surface / suppress double-dip / wait), `is_suitable`, `compliance_gate_hook` (S8 interface only, D-508). |
| `offer.offer_types` | `src/common/offer/offer_types.py` | D-504 `candidate_offer_types` from clock/rung/state (renewal / buyout / larger-advance / none-yet). |
| `offer.profile` | `src/common/offer/profile.py` | D-503 `build_funder_profile`: MRI gold → the engine's `v_funder_input` shape + honest missing-field flags; `tib_months`. |
| `schemas.gold` (+S5) | `src/common/schemas/gold.py` | `merchant_offers_schema()` (point-in-time). |

Reuses existing `Thresholds` + the S2/S3/S4 outputs (no duplicate numbers — Rule 3). `constants.FunderCatalog` holds the `mca_funders` fq names (read-only reuse target). Consumed by `transform/gold_offers.py` (**build pending — gated on D-501**) writing point-in-time `gold.merchant_offers` (+`_current`); `matched_funders` is sourced by REUSING the routing engine, never rebuilt.

## Built (Sprint 6 — Prediction feature/label derivation, §6/11.2)

D-601…D-609 signed 2026-06-02 (C-020); **pure modules built + tier-1 green (284 tier-1).** First ML sprint — the MODELS (PyMC-Marketing BG/NBD+Gamma-Gamma+CLV; lifelines Cox+KM) are ADOPTED and fit on Databricks (`transform/gold_predictions.py`, **build gated on D-602**); MRI owns only feature/label derivation + orchestration (CLAUDE.md §4). Pure / Spark+ML-free at import. Distress stays signal-driven (S3).

| Component | Path | Contract / purpose |
|---|---|---|
| `prediction.rfm` | `src/common/prediction/rfm.py` | D-601 `rfm_features`: frequency (deal_count−1) / recency / T / monetary for BG/NBD + Gamma-Gamma. |
| `prediction.survival` | `src/common/prediction/survival.py` | D-607 `inter_advance_intervals` / `censored_duration` / `survival_rows`: observed intervals (event=1) + censored tail (event=0) for lifelines Cox/KM — not-yet-renewed = censored, never dropped. |
| `prediction.confidence` | `src/common/prediction/confidence.py` | D-603 `prediction_confidence` (posterior-width else history), `is_insufficient_history` (thin merchants → prior + wide confidence). |
| `schemas.gold` (+S6) | `src/common/schemas/gold.py` | `merchant_predictions_schema()` (point-in-time). |

Reuses existing `Thresholds` + the deal history / event log (no duplicate numbers — Rule 3). `constants` adds the CLV horizon/discount + `INSUFFICIENT_HISTORY_MIN_EVENTS` + `COX_COVARIATES` (calibration home). Consumed by `transform/gold_predictions.py` (**build pending — gated**) writing point-in-time `gold.merchant_predictions` (+`_current`) + `prediction` events, MLflow-versioned (`model_version`).

## Built (Sprint 7 — agentic extraction deterministic tools, §5.9)

D-701…D-706 signed 2026-06-05 (C-022); **Phase-1 deterministic foundation built + tier-1 green (297 tier-1).** Framework §5.9 — agents EXTRACT, the spine COMPUTES; these pure tools are what the LLM agent calls (the LLM layer in `agents/` + the cloud run are gated). Pure / no-LLM-at-import.

| Component | Path | Contract / purpose |
|---|---|---|
| `agents.default_subtype` | `src/common/agents/default_subtype.py` | Data Steward tool: `normalize_subtype_label` (free-text → DefaultSubtype) + `apply_default_subtype` (confidence gate → concrete sub-type APPLIED only ≥ threshold, else conservative `unknown`+REVIEW; routes via S3 `route_for_default`). |
| `agents.grounding` | `src/common/agents/grounding.py` | The extraction contract: `make_extraction` (grounded row), `review_status` (APPLIED/REVIEW/REJECTED — ungrounded→rejected, requires model_version), `is_applicable`. |
| `agents.data_steward` | `src/common/agents/data_steward.py` | The fuzzy half (Spark-free, injected `predict_fn`): `build_messages` (grounded prompt), `parse_response` (tolerant/defensive — malformed/OOV→unknown, conf clamped), `classify_default_cause`, and `build_extraction_rows` (the pure agent→gate→ground orchestration — lives here per Rule 3; the transform is a thin wrapper). |
| `transform.gold_extraction` (S7 driver) | `src/transform/gold_extraction.py` | Thin Spark wrapper: `closed_default_deals` source, `databricks_chat_predict_fn` (Foundation Model via `databricks-sdk` serving client), writes point-in-time `gold.merchant_extraction` (+`_current`) + delete-then-append `agent_extraction` events. |
| `eventlog.events` (+S7) | `src/common/eventlog/events.py` | `agent_extraction_event` (timeline marker; full detail lives in `merchant_extraction`). |
| `rung.lifecycle` (+S7) | `src/common/rung/lifecycle.py` | `default_subtype`/`lifecycle_state` now honor a `resolved_default_subtype` signal (agent extracts, gate routes) — backward-compatible. |
| `schemas.gold` (+S7) | `src/common/schemas/gold.py` | `merchant_extraction_schema()` (point-in-time, grounded). |

Reuses S3 `route_for_default` (no duplicate routing — Rule 3). `constants` adds `ExtractionType`/`ReviewStatus`/`EventType.AGENT_EXTRACTION`/`AGENT_CONFIDENCE_REVIEW_MIN`. Consumed by the **Data Steward LLM agent + `transform/gold_extraction.py`** (build gated) writing `gold.merchant_extraction`; the spine re-run reads APPLIED extractions as optional enrichment — the agent never writes spine tables.

## Principles

- **Schema is derived from / validated against the Data Contract xlsx** — never a parallel hand-maintained copy.
- **Thresholds live once** in `constants.Thresholds` (calibration in one place).
- **The four validation merchants are canonical fixtures**, reused everywhere.
- A utility used in ≥2 places is promoted to `src/common/` immediately.
