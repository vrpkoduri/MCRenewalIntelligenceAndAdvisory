# Shared Components Catalogue

Reusable libraries with stable contracts, centralized so they change in one place (GENERAL_INSTRUCTIONS Rule 3). No block reaches into another's internals; data flows via the gold-table contract.

## Built (Sprint 0)

| Component | Path | Contract / purpose |
|---|---|---|
| `constants` | `src/common/constants.py` | Catalog/schema/table names, SF object names, enums (DealType, PaymentFrequency, BalanceSource, Verdict), no-surface set, RTR tolerance, reserved Appendix A/B thresholds. Pure Python. |
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

## Planned (Sprint 3 — designed + decisions signed C-017; build pending)

Appendix B engine, decisions signed 2026-06-01 ([SPRINT_3_PLAN](sprints/SPRINT_3_PLAN.md), DECISIONS C-017). Reserved homes `src/common/rung/` + `src/common/eventlog/` exist (empty `__init__.py`). Pure / Spark-free-at-import, mirroring `common/clock`.

| Component | Path | Contract / purpose |
|---|---|---|
| `rung.lifecycle` | `src/common/rung/lifecycle.py` | B.2 Step-0 gate: `default_subtype` (unknown→do-not-fund), `is_dormant` (>2× median renewal gap, book-median fallback), `is_new_establishing`, `lifecycle_state` route. |
| `rung.waterfall` | `src/common/rung/waterfall.py` | B.3 first-match-wins waterfall + stress-override-pulls-down: `is_distressed`/`is_serial`/`is_disciplined`/`is_growth`/`is_graduate`, `rung_of`. Includes `rapid_reup_flag` (D-302: prior still-active & <50% paid at new funding, OR ≤45-day gap `RAPID_REUP_MAX_GAP_DAYS`). |
| `rung.confidence` | `src/common/rung/confidence.py` | D-306 borderline-driven `confidence` (missing data NOT penalized) + `direction_of_travel` (climbing/holding/sliding). |
| `rung.classify` | `src/common/rung/classify.py` | Composes gate→waterfall→output object `{lifecycle_state, rung, confidence, missing_signals[], direction_of_travel}`. Single pure entry point. |
| `eventlog.events` | `src/common/eventlog/events.py` | D-305 append-only event builders + schema; v1 = classification + transition events, one wide table keyed `(merchant_id, event_type, event_ts)`. |

Reuses the existing `Thresholds` (no duplicate numbers — Rule 3) + `Type=Renewal` trust (D-303). Consumed by `transform/gold_rung.py` (the Spark driver, build pending) writing point-in-time `gold.merchant_rung` (+`_current` view) + append-only `gold.merchant_event_log` (D-304).

## Principles

- **Schema is derived from / validated against the Data Contract xlsx** — never a parallel hand-maintained copy.
- **Thresholds live once** in `constants.Thresholds` (calibration in one place).
- **The four validation merchants are canonical fixtures**, reused everywhere.
- A utility used in ≥2 places is promoted to `src/common/` immediately.
