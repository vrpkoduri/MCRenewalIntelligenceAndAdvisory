# FU-501 — Offer-Engine Profile Handoff Contract (MRI ↔ Routing Engine)

**Status: DRAFT for routing-team sign-off.** Defines the batch interface by which MRI reuses
the existing `mca_funders` routing engine to score the **funded book** proactively (Sprint 5,
decision **C-019 / D-501**). Integration contract only — **no rebuild of the routing engine or
the funder-criteria dataset** (CLAUDE.md §6). Once agreed, MRI builds `transform/gold_offers.py`
(profile export + results consumer) against it.

**Schema re-verified live 2026-07-20** against `mca_funders` (`v_funder_input`,
`routing_program_evaluations`, `routing_decisions`) — the column names below are real.

## Why this matters now — it unblocks TWO shipped layers

FU-501 is no longer just "finish S5." Both of these are built and gated on it:
1. **S5 Offer Engine** — `transform/gold_offers.py` (the MRI side is offline-built + 270 tier-1
   green; only the cloud handoff is missing).
2. **S8 Advisory — the specific-offer path.** The Advisory Composer (in PROD, C-033) currently
   emits **0 specific-offers** — it can only give *advice / structure* guidance because it has
   no grounded offer amount to speak. `gold.merchant_advisory`'s specific-offer branch reads
   `merchant_offers_current`; the moment offers land, the advisory layer starts composing
   grounded, compliance-gated **specific offers** (amount + factor), disclosure-checked per state.
   The plumbing already exists (the driver reads `merchant_offers_current` if present).

So one integration lights up a whole offer capability across two sprints.

## Why a handoff at all (the spike result)

Read-only spike (2026-06-02): the engine's existing outputs cover only the **new-deal /
submission** population — **121 merchants** — and the MRI funded book has **ZERO id-overlap**
with it (MRI `azure_merchant_id` ≈ `1.0M`/`HIST-…` vs funder `merchant_id` `3.4–5.2M`;
`v_funder_input` carries no tax_id bridge). MRI cannot reuse existing evaluations; the engine
must be **run against MRI-built profiles**, and MRI's own `merchant_id` must **round-trip**
through the run so results map back.

## Population & cadence (MRI-side proposal)

- **Population v1: the ~412 active merchants** (active lifecycle ∩ a live position) — the ones
  for whom a proactive offer is actionable (in-market / approaching / serial). NOT the whole
  2,125 (dormant/defaulted/paid-off don't need an offer scan). Grows/refreshes each run.
  *(Rationale: matches the S8 advisory universe exactly, so every offer maps to a live advisory;
  keeps engine volume modest. Expandable later.)*
- **Cadence: weekly** proactive scan (Framework §5.7 "tap-early"), pinned to a `box_version_id`
  per `batch_id` for reproducibility.

## The interface (three parts)

### A. MRI → routing: profile landing (MRI writes, in the `v_funder_input` shape)

One row per active merchant per scan. MRI populates only the fields it has; the rest are **NULL
by design** so the engine's **own missing-data handling** applies (never faked — CLAUDE.md §2.5).

| `v_funder_input` column | MRI source | Notes |
|---|---|---|
| `submission_id` | **a batch-scoped MRI key** | round-trip candidate #1 — see round-trip options |
| `merchant_id` | *(funder-side id — MRI has none)* | left to engine / crosswalk; NOT MRI's id |
| `business_state`, `state_of_incorporation` | `merchants.governing_state` | |
| `industry_code` | `merchants.industry` | raw; engine maps via its industry taxonomy → `industry_risk_tier` |
| `tib_months` | `business_start_date`→months (else deal `months_in_business`) | 0/blank = missing |
| `fico` | `merchants.fico` | 0/blank = missing; MRI sets `fico_source` = SF |
| `max_existing_positions_observed` | `merchant_clock_current.active_position_cnt` | + S7 statement positions where covered (`positions_source`) |
| `max_combined_balance_observed` | `merchant_clock_current.est_current_balance` | computed (Appendix A), not SF-stored |
| `days_since_newest_mca` | from most-recent `funded_date` | |
| `monthly_revenue` / `annual_revenue` / `avg_daily_balance` / `avg_monthly_deposits` / `nsf_per_month` / `negative_days_per_month` | **NULL** (no bank feed) — *except* the ~35 S7-covered merchants where statement `est_weekly_revenue` / `weekly_debit` can seed monthly figures (advisory-confidence, `financials_source=statement`) | engine → `missing_data_categories`; broadens with FU-301 |
| `has_open_bankruptcy` / `bankruptcy_*` / `has_tax_lien` / `has_judgment` / late-payment / `datamerch_default_flag` | **NULL** (no credit feed) | engine missing-data |
| `data_quality_flags` | MRI DQ (e.g. `fico_missing`, `revenue_missing`) | so the engine sees why fields are null |

**MRI does NOT set** `submission_date` semantics as a real application — these are *proactive
profiles*, not submissions (see round-trip / decision #3).

### B. routing → MRI: results (routing writes, MRI reads)

Two real tables, both already keyed by `batch_id` + `submission_id` + `merchant_id`:

**`routing_program_evaluations`** (per program per merchant) — MRI consumes:
`batch_id`, `submission_id`, `merchant_id`, `program_id`, `funder_id`, `box_version_id`,
`verdict` (pass / case-by-case / fail), `failing_rule_categories`, `case_by_case_rule_categories`,
`missing_data_categories`, `rank`, `rank_score`, `estimated_funding_amount`,
`estimated_factor_rate`, `estimated_term_months`, `estimate_confidence`, `overall_fit`,
`confidence_overall`.

**`routing_decisions`** (per-merchant summary) — MRI consumes:
`decision_id`, `batch_id`, `submission_id`, `merchant_id`, `recommended_action`,
`top_program_ids`, `programs_passed` / `programs_case_by_case` / `programs_failed`,
`merchant_data_gaps`, `routing_engine_version`, `overall_confidence`, `additional_attributes`.

**MRI maps:** passing / case-by-case programs → `matched_funders` + `eligible_offer_types`;
`estimated_funding_amount` → `best_offer_summary` + a `max_sustainable_advance` input (still
revenue-gated, D-505); records `routing_engine_version` + `box_version_id` for lineage. MRI
then applies its **suitability gate** (renewal-vs-buyout, double-dip suppression) before
anything is surfaced — and the **S8 compliance gate** before any of it becomes merchant-facing.

### C. The round-trip key (the one hard requirement)

MRI's `merchant_id` (`MRI-…`) must survive the run so results map back. Three viable options —
**routing team picks one:**
1. **`submission_id` = MRI's batch-scoped key.** MRI lands `submission_id = "<batch_id>:<mri_merchant_id>"`; results carry it back verbatim. Simplest; no schema change if the engine treats `submission_id` as opaque.
2. **`additional_attributes.mri_merchant_id`** on `routing_decisions` (JSON passthrough) — if the engine echoes input attributes.
3. **A crosswalk MRI maintains** (`batch_id`, engine `submission_id`/`merchant_id` → `mri_merchant_id`), if the engine mints its own ids and returns them deterministically per landed row.

## Decisions to agree with the routing team

1. **Landing** — table location + schema + MRI write grant, **or** a routing-owned ingestion path MRI writes to.
2. **Results read grant** on `routing_program_evaluations` + `routing_decisions`, filtered to MRI's `batch_id`.
3. **Ad-hoc / proactive profiles** — can the engine evaluate non-submission profiles (no real application) keyed by an external id? (It runs on submissions today; these are proactive scans.)
4. **Round-trip key** — pick option 1 / 2 / 3 in §C.
5. **Partial-profile behavior** — confirm missing revenue/NSF/bankruptcy → `case-by-case` / `missing_data`, not a hard fail (so the funded book still gets credible verdicts).
6. **Cadence + trigger** (proposal weekly; MRI lands `batch_id` → engine picks it up) + `box_version_id` pinning per batch.
7. **Volume** — ~412 profiles/scan v1 (active book); confirm acceptable.

## What MRI builds once signed (gated — Rule 5)

`transform/gold_offers.py` = (1) **profile export** (MRI gold → the §A `v_funder_input` shape,
landed for the batch) + (2) **results consumer** (read §B keyed by the §C round-trip key → map
to `eligible_offer_types` / `matched_funders` / `max_sustainable_advance` / `best_offer_summary`,
apply the suitability gate) → write point-in-time `gold.merchant_offers` (+`_current`) +
`offer_computed` events. Then:
- **Tier-2 on `gold_test`**, reconciling a sample to the engine's own results (prove reuse, not reinvent).
- **Re-run the S8 advisory** so `merchant_offers_current` lights up the **specific-offer path** — grounded amount/factor, suitability-gated, disclosure-checked (D-805/D-806). Expect the first non-zero `specific-offer` count, each still 100%-grounded + compliance-PASS or held.
- Prod gated (explicit approval + `allow_prod=True`).

## Until then

The offline `common/offer` engine (renewal-vs-buyout suitability, offer-type rules, profile
assembly) is built + tier-1-green (270 tests) and in `main` — it is the MRI side of this
contract, ready to wire in as soon as §A/§B locations + the §C key are agreed. The S8 advisory
already reads `merchant_offers_current` when present, so no S8 change is needed to activate the
specific-offer path — only this handoff.
