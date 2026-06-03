# Shared Components Catalogue

Reusable libraries with stable contracts, centralized so they change in one place (GENERAL_INSTRUCTIONS Rule 3). No block reaches into another's internals; data flows via the gold-table contract.

## Built (Sprint 0)

| Component | Path | Contract / purpose |
|---|---|---|
| `constants` | `src/common/constants.py` | Catalog/schema/table names, SF object names, enums (DealType, PaymentFrequency, BalanceSource, ClosureStatus, Verdict; **S3: LifecycleState, RungState, DefaultSubtype, LifecycleRoute, DirectionOfTravel, EventType; S4: CurrentState, Play, BookHealthView**), no-surface set, RTR tolerance, `DEFAULT_NOTE_KEYWORDS`, `RAPID_REUP_MAX_GAP_DAYS`, `APPROACHING_WINDOW_DAYS`/`RENEWED_WINDOW_DAYS`, `PLAY_SLA_BUSINESS_DAYS`, Appendix A/B `Thresholds` (single calibration home). Pure Python. |
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

## Principles

- **Schema is derived from / validated against the Data Contract xlsx** — never a parallel hand-maintained copy.
- **Thresholds live once** in `constants.Thresholds` (calibration in one place).
- **The four validation merchants are canonical fixtures**, reused everywhere.
- A utility used in ≥2 places is promoted to `src/common/` immediately.
