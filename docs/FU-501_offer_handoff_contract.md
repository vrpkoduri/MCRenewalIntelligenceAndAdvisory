# FU-501 — Offer-Engine Profile Handoff Contract (MRI ↔ Routing Engine)

**Status: DRAFT for routing-team sign-off.** Defines the batch interface by which MRI reuses
the existing `mca_funders` routing engine to score the **funded book** proactively (Sprint 5,
decision **C-019 / D-501**). This is an integration contract only — **no rebuild of the
routing engine or the funder-criteria dataset** (CLAUDE.md §6). Once agreed, MRI builds
`transform/gold_offers.py` (profile export + results consumer) against it.

## Why a handoff (the spike result)

Read-only spike (2026-06-02): the routing engine's existing outputs cover only the **new-deal
/ submission** population — **121 distinct merchants**, latest 2026-06-01 — and the MRI funded
book has **ZERO id-overlap** with it (MRI `azure_merchant_id` ≈ `1.0M`/`HIST-…` vs funder
`3.4–5.2M`; `v_funder_input` carries no tax_id bridge). So MRI cannot reuse existing
evaluations; the engine must be **run against MRI-built profiles**, and MRI's own
`merchant_id` must **round-trip** through the run so results map back.

## The interface (three parts)

### A. MRI → routing: profile landing (MRI writes)

MRI lands one row per funded merchant per scan, in the **`gold.v_funder_input` shape** plus
control columns. MRI populates only the fields it has; the rest are NULL by design so the
engine's **own missing-data handling** applies (never faked — CLAUDE.md §2.5).

| Column | Source in MRI | Notes |
|---|---|---|
| `mri_merchant_id` | `gold.merchants.merchant_id` | **The round-trip key** — must come back on results |
| `batch_id` | MRI scan id | one per proactive run |
| `profile_run_date` | run date | the tap-early cadence marker |
| `business_state` | `merchants.governing_state` | |
| `state_of_incorporation` | `merchants.governing_state` / deal | |
| `industry_code` | `merchants.industry` | raw; routing maps via its `industry_taxonomy` |
| `tib_months` | `business_start_date` → months (else `months_in_business`) | 0/blank = missing |
| `fico` | `merchants.fico` | 0/blank = missing |
| `max_existing_positions_observed` | `merchant_clock_current.active_position_cnt` | |
| `monthly_revenue` / `avg_daily_balance` / `nsf_per_month` / bankruptcy / lien / judgment / … | **NULL (no bank/credit feed in v1)** | engine → `missing_data_categories`; folds in with FU-301 |

**Open:** exact landing table (catalog/schema/name), MRI write access, and whether these are
ingested as synthetic "submissions" or via a dedicated proactive-profile path.

### B. routing → MRI: results (routing writes, MRI reads)

The engine evaluates each profile against the funder boxes and writes results **keyed to
`mri_merchant_id`**. Shape = a subset of `routing_program_evaluations` + a per-merchant
summary:

| Column | Notes |
|---|---|
| `mri_merchant_id` | the round-trip key |
| `batch_id`, `evaluated_at` | run lineage |
| `routing_engine_version`, `box_version_id` | reproducibility / lineage (MRI records these) |
| per program: `program_id`, `funder_id`, `verdict` (pass / fail / case-by-case), `failing_rule_categories`, `missing_data_categories`, `rank`, `estimated_funding_amount`, `estimated_factor_rate`, `overall_fit`, `confidence_overall` | MRI maps passing programs → `matched_funders`; estimates → offer summary / capacity |

**MRI consumes:** passing/case-by-case programs → `matched_funders` + `eligible_offer_types`;
estimated amounts → `best_offer_summary` and a `max_sustainable_advance` input (still revenue-
gated, D-505). MRI then applies its **suitability gate** (renewal-vs-buyout, double-dip
suppression) before anything is surfaced.

### C. Cadence

Proactive scan on a schedule (proposal: **weekly**) so newly-eligible merchants are caught
before competitors (Framework §5.7 "tap-early"). Trigger: MRI lands profiles → routing job
picks up the `batch_id`, or a coordinated shared schedule. **Open:** frequency + trigger
mechanism + SLA for results.

## Decisions to agree with the routing team

1. **Landing table** location + schema + MRI write grant (or a routing-owned ingestion path).
2. **Results table** location + schema + MRI read grant; confirm it is keyed by `mri_merchant_id`.
3. **Ad-hoc profile ingestion** — can the engine evaluate synthetic, non-submission profiles
   keyed by an external id? (It runs on submissions today.)
4. **Partial-profile behavior** — confirm missing revenue/NSF/bankruptcy yields `case-by-case`
   / `missing_data`, not a hard fail (so the funded book still gets credible verdicts).
5. **Cadence + trigger** + box-version pinning per `batch_id` (reproducibility).
6. **Volume** — ~2,125 profiles per scan; confirm acceptable for the engine/job.

## What MRI builds once this is signed (gated)

`transform/gold_offers.py` = (1) **profile export** (MRI gold → the §A shape, landed) +
(2) **results consumer** (read §B → map to `eligible_offer_types` / `matched_funders` /
`max_sustainable_advance` / `best_offer_summary`, apply the suitability gate) → write
point-in-time `gold.merchant_offers` (+ `_current`) + `offer_computed` events. Then tier-2 on
`gold_test`, reconciling a sample to the engine's own results (prove reuse, not reinvent);
prod gated (Rule 5).

## Until then

The offline `common/offer` engine (renewal-vs-buyout suitability, offer-type rules, profile
assembly) is built + tier-1-green (270 tests) and committed — it is the MRI side of this
contract and is ready to wire in as soon as §A/§B locations are agreed.
