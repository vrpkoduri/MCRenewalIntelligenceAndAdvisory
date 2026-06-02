# Sprint Tracker

Fast-moving operational log of sprint status, decisions, and test results. The formal
narrative roadmap is `Morgan_Cash_Build_Plan_Sprint_Roadmap.docx`; this is the living index (GENERAL_INSTRUCTIONS Rule 1).

## Status at a glance

| Sprint | Block | Status | Plan | Notes |
|---|---|---|---|---|
| **S0** | Ingestion → silver | **Complete** | [SPRINT_0_PLAN](sprints/SPRINT_0_PLAN.md) | Bronze **live** (4 SF objects, G1 ✅); **all 3 silver tables in PROD 2026-05-31** — `deals` (3,959), `offers` (57,586), `field_history` (220,172); every table reconciles to bronze, schema + no-surface green. Remaining: FU-001/FU-002 (deferred, non-blocking) |
| S1 | Identity + Deal table | **Complete — in PROD `gold` (2026-05-31)** | [SPRINT_1_PLAN](sprints/SPRINT_1_PLAN.md) | **D-101…D-105 signed; C-013 (PORT AATM) + C-014 (own `merchant_id` + carry `azure_merchant_id`).** `common/identity/{normalize,match,keys}` + `schemas/gold.py` + gold field maps + transforms `gold_{merchants,deals}.py`. **106 tier-1 green.** **Tier-2 recon PASSED on `gold_test` then PROMOTED TO PROD (`failures: []` both):** `gold.deals` **3,959**, `gold.merchants` **2,125**, `gold.merchant_crosswalk` **2,167**; 0 null merchant_id, collapse 1.0198, azure fill **81.4%**, gaps null+flagged, FU-002 Wolf=1. |
| S2 | Features + clock (Appendix A) | **Complete — in PROD `gold` (2026-05-31)** | [SPRINT_2_PLAN](sprints/SPRINT_2_PLAN.md) | THE-core-principle sprint: daily live-recompute of balance/paydown/eligible-date/closure; never trust SF stored balances. **Decisions:** separate point-in-time `gold.deal_clock`/`merchant_clock` (+`_current` views); estimated-path-only (no feed); plain M–F v1 (holiday hook); 55% default threshold (FU-201 to wire per-funder). Built `common/clock/{amortization,calendar,closure,rollup}` + `transform/gold_clock.py` + clock field maps/schemas. **165 tier-1 green** (+59 clock). **Tier-2 recon PASSED on `gold_test` then PROMOTED TO PROD (`failures: []` both, run_date 2026-05-31):** `gold.deal_clock` **3,959** (= gold.deals, 1 row/deal) + `gold.merchant_clock` **2,125** (= distinct merchants) + `*_current` views; closure closed_clean 3,059 / active 894 / **closed_default 6** (Starr default-note invariant held — 0 default-note-but-clean); paydown∈[0,1] + balance≥0 + balance_source 100% estimated + revenue/burden 100% null+flagged all clean. Diagnostics: clock_inputs_missing 234, rtr_checkpoint>tol 21 (FU-202), elapsed-at-term-cap 3,826 (run_date future vs real book). |
| S3 | Rung classifier + state machine + event log (Appendix B) | **Decisions signed (C-017) — building offline pure modules (cloud gated)** | [SPRINT_3_PLAN](sprints/SPRINT_3_PLAN.md) | Two-stage deterministic engine: Step-0 lifecycle gate → 5-rung waterfall (first-match-wins + stress override), `{rung, confidence, missing_signals[], direction_of_travel}` per merchant; append-only event log; daily queue read. **No ML** (S6). Reads S2 clock only — never recomputes the spine. New modules `common/rung` + `common/eventlog` (homes reserved). **D-301…D-306 signed 2026-06-01 (C-017):** v1 stress = default-note+burden+terms (NSF→FU-301); `rapid_reup_flag` in `common/rung` (prior <50% paid & still active, OR ≤45-day gap); trust `Type=Renewal` (503 unlinkable = data gap, FU-302); separate `gold.merchant_rung` + append-only `gold.merchant_event_log`; one wide event log; borderline-driven confidence (missing data never penalized). Exit: whole book classified, Unclassified pile quantified, reconciles, 4-merchant B.5 labels match. |
| S4 | Activation + Book Health | Not started | — | |
| S5 | Offer Engine | Not started | — | `mca_funders` = reuse dataset |
| S6 | Prediction | Not started | — | |
| S7 | Agentic extraction (Statement Analyst + Data Steward) | Not started | — | agents extract only; never compute the spine |
| S8 | Advisory comms + agents (Composer, Structure Advisor) + compliance | Not started | — | compliance gate is first-class |
| S9+ | Merchant app | Not started | — | renderer over Lakebase |

## Sprint 2 — progress detail

- **D-201…D-205 signed (C-016, 2026-05-31):** separate point-in-time `gold.deal_clock` / `gold.merchant_clock` (+ `*_current` views); estimated-path-only v1 (no servicing feed, D-203); plain M–F business-day counting v1 with a `holidays` hook (D-204); 55% default renewal threshold reusing `Thresholds.DEFAULT_RENEWAL_PAYDOWN` (D-205; per-funder lookup deferred to **FU-201**).
- **Shared clock logic** in `src/common/clock/{amortization,calendar,closure,rollup}.py` — pure, Spark-free-at-import (mirrors `common/identity`), reused inside the Spark UDFs/columns of `transform/gold_clock.py` (the dq.predicates ↔ dq.rules pattern). Appendix A.2 (rtr/amount_paid/balance floor/paydown cap), A.3 (business-day & weekly elapsed, term-capped), A.4 (eligible-date inverse-solve), A.5b (three-state closure; **default note dominates ~100% paydown → closed_default, never closed_clean**), A.5 (merchant roll-up: active count, weekly debit, burden, weakest balance_source, primary-position eligibility).
- **Tier-1: 165 green** (+59 clock) — hand-worked math, daily/weekly term-cap, nth-business-day inverse, the Starr defaulted-but-100% case, roll-up, all four validation merchants end-to-end vs `expected_clock`, no-surface + Merchant-Gold-contract-subset invariants.
- **Tier-2 recon PASSED on `gold_test` (2026-05-31, run_date pinned 2026-05-31, `failures: []`):** built `gold_test.deal_clock` / `merchant_clock` (+`_current`) via the same `build_gold_clock` driver staged to a fresh `mri_tier2` workspace dir, serverless job, staging cleaned up after. Hard checks green — coverage `deal_clock`=**3,959** (= gold.deals, (deal_id,run_date) unique), `merchant_clock`=**2,125** (= distinct merchants); `est_paydown_pct`∈[0,1]; `est_current_balance`≥0; closure ∈ 3 valid states; **0 default-note-but-closed_clean** (Starr invariant); `balance_source` 100% `estimated` (D-203); `est_weekly_revenue`/`burden_ratio` 100% null with `*_is_missing` 100% true; `*_current` views resolve to the single latest run_date. Diagnostics: closure closed_clean **3,059** / active **894** / **closed_default 6**; `clock_inputs_missing` 234; `rtr_checkpoint_delta` max 372,500 with **21** > tolerance (terms-contradiction flags, diagnostic only); `elapsed_at_term_cap` 3,826 and `is_eligible_now` 3,558 (both inflated because run_date 2026-05-31 sits ahead of the real funded book — correct recompute given that "today"). Reference-by-name found Wolf only (2 active positions, low paydown) — same Account-vs-Opportunity-name nuance as **FU-002**, diagnostic not a failure.
- **PROD `gold` promotion DONE (2026-05-31, approved):** re-ran the same verified driver with `schema=gold, allow_prod=true` — **recon identical (`failures: []`)**. PROD objects: `mca_mri.gold.deal_clock` (managed, 3,959), `gold.merchant_clock` (managed, 2,125), `gold.deal_clock_current` + `gold.merchant_clock_current` (views). Workspace staging cleaned up. **S2 amortization-clock path COMPLETE.**

**S2 exit criteria met.** Follow-ups (non-blocking): **FU-201** wire per-funder renewal threshold from `mca_funders`; **FU-202** review the 21 `rtr_checkpoint_delta` > tolerance deals (terms contradictions); daily recompute DAB job scheduling/activation remains approval-gated.

## Sprint 1 — progress detail

**Decisions:** D-101…D-104 approved 2026-05-31; D-105 resolved via **C-013** (PORT the AATM matching IP); **C-014** (mint own `merchant_id`, carry AATM `azure_merchant_id` via the normalized-tax_id bridge).

**Done (code, 2026-05-31):**
- **Identity IP** (`src/common/identity/`): `normalize.py` (tax_id/business_name/phone/state — ported from AATM + `normalize_state`); `match.py` (`AccountKeys`, union-find `cluster_accounts` — MasterRecordId + exact tax_id AUTO-merge; phone + name/state flagged candidates per D-102; `account_match_keys` Spark adapter); `keys.py` (`assign_merchant_ids` persisted-crosswalk stability D-101 + `match_reason_by_merchant`).
- **Gold schemas** (`src/common/schemas/gold.py`) + **field maps** (`DEAL_TABLE_MAP` = the 24 contract Deal-Table fields in order, `MERCHANT_MAP`, `MERCHANT_CROSSWALK_MAP`, gold DQ columns) + `GoldTable`/`Identity` constants + `Thresholds.WEEKS_PER_MONTH=4.33`.
- **Transforms**: `src/transform/gold_merchants.py` (crosswalk + merchant dimension + AATM `azure_merchant_id` enrichment, optional/read-only) and `src/transform/gold_deals.py` (24-field Deal Table; `term_months` Appendix A.3; renewal chain D-103; `status` from field_history; Must-capture gaps null + `*_is_missing`).
- **Tests: 106 tier-1 green** (normalize 37, match 9, keys 8, gold maps 11, + existing).

**Done (cloud, 2026-05-31) — tier-2 gold reconciliation PASSED (`gold_test`):**
- Ran `build_gold_merchants` + `build_gold_deals` into `mca_mri.gold_test.*` on **serverless** (one-time job submit; src + `recon_gold` staged as Workspace files, `run_tier2_gold` as a notebook — no bundle deploy; staging cleaned up after). Test code in `tests/tier2/recon_gold.py`.
- **Hard checks all green (`failures: []`):** `gold.deals` = **3,959 == silver.deals 3,959**; `deal_id` unique; **0 deals with null `merchant_id`**; crosswalk covers every funded Account; schemas == `deal_table_schema()`/`merchant_schema()`/`merchant_crosswalk_schema()`; **no-surface guard clean** on `gold.deals`; all 4 Must-capture gaps 100% null with `*_is_missing` 100% true.
- **Identity rollups:** **2,125 merchants** from ~2,167 funded Accounts (collapse ratio **1.0198**); merge tiers = 18 `tax_id` clusters + 2,107 singletons (no MasterRecordId merges on the funded book). **`azure_merchant_id` fill = 81.4%** (matches the C-014 ~84% tax_id-bridge estimate). **FU-002:** "Wolf Corporation" resolves to exactly 1 merchant_id (other 3 reference names live on `Opportunity.Name`, not `Account.Name` — surfaced, not failed).
- **Derivation rollups (honest, no faking):** `term_months` 3,947 populated / 12 flagged missing; renewals 1,854 → 1,351 linked + 503 `renewal_unlinkable`; `prior_factor_rate` 1,276; gap columns 0 non-null.
- **Prod `gold` promotion DONE (2026-05-31, approved):** re-ran the same verified driver with `schema=gold, allow_prod=true` — **recon identical (`failures: []`)**. Prod row counts: `mca_mri.gold.deals` **3,959**, `gold.merchants` **2,125**, `gold.merchant_crosswalk` **2,167**. Workspace staging cleaned up. **S1 identity + Deal-Table path COMPLETE.**

**S1 exit criteria met.** Follow-ups (non-blocking): calibrate the renewal-chain vs any SF link field (503 `renewal_unlinkable` to investigate in S2); FU-002 fully closeable once principal/Account-name resolution lands; `azure_merchant_id` 81.4% vs C-014's profiled 84% — small gap to reconcile (test-compute tax_id normalization parity).

## Sprint 0 — progress detail

**Gates:** G2 ✅ (Appendix A) · G3 ✅ (Appendix B) · **G1 ✅** (data audit vs live SF — bronze landed 2026-05-31; D-002 closed via C-011/C-012) · G4 ✅ (env + data rights, C-001).

**Done (offline, no cloud resources):**
- Repo scaffold: DAB (`databricks.yml`, dev/prod), `src/{ingestion,transform,common}`, `resources/`, `tests/`. `databricks bundle validate` → OK.
- Shared components (Rule 3): `constants`, `field_maps` (SPRINT_0 map as data), `dq` (predicates + Spark rules), `schemas/silver`, `io/guards` (no-surface), reserved homes for clock/identity/rung/eventlog.
- Fixtures: four validation merchants. Tests: **32 tier-1 tests passing** (constants, field-map integrity, contract consistency, DQ predicates, no-surface guard, scenario).

**Done (cloud, approved 2026-05-29):**
- `mca_mri` catalog created (managed, metastore `morgancash_uc_prd` family); schemas `bronze`/`silver`/`gold` + `_test` mirrors created and verified.
- Salesforce **External Client App** "Databricks Ingestion" created (OAuth: `api`, `refresh_token offline_access`; IP relaxed; self-authorize — FU-001 to harden pre-go-live).
- UC connection **`mri_salesforce`** created and **ACTIVE** (type SALESFORCE, OAUTH_U2M, instance `mcabrokerage.my.salesforce.com`, prod). No separate secret scope (C-009).

**Decisions confirmed 2026-05-29:** C-005 (web/auth-code OAuth), C-007 (date-sanity flags either direction), C-008 (root CLAUDE.md pointer), C-009 (no secret scope; creds in UC connection), **C-010 (all-managed tables, bronze/silver/gold)**. (C-006 superseded.)

**Done (cloud, 2026-05-31) — bronze ingestion live:**
- Lakeflow Connect ingestion pipeline **`mri_sf_ingest_bronze`** created + first run **COMPLETED** (4m 18s, 0 errors). Connection `mri_salesforce`; managed streaming tables in `mca_mri.bronze`; serverless; schedule every 6h; failure-email to venkat@. Pipeline ID `0b6d521b-…`.
- Landed: `account` 333K · `opportunity` 426K · `offer__c` 115K · `opportunityfieldhistory` 220K. Object scope = the four SPRINT_0 objects (excluded `Offer__Feed`/`Offer__History`); all columns + future-columns ON.
- **G1 field-level audit done** (profiled via Starter Warehouse, read-only). Funded book = **3,959** opps (`StageName='Funded'`). **C-011** (object names) + **C-012** (field source-of-truth) logged; **D-002 closed**.
  - Economics from **selected offer** (`Offer__c` where `Select_Offer__c=true`): `factor_rate`/`payback_amount`/`payment_amount` use the **underscored** Offer fields; `funded_amount` from `Opportunity.Funded_Amount__c`; merchant link = `Opportunity.AccountId`; FICO from `FICO__c` (string).
  - DQ found: ~28 funded deals have >1 selected offer (dedup on latest `LastModifiedDate`); 2 have none (fallback + flag). `field_maps.DEALS_MAP` updated to real API names; **32 tier-1 tests green**.

**Done (code, 2026-05-31) — post-G1 build:**
- **Pipeline-as-code**: `resources/ingestion_pipeline.yml` authored (pipeline + 6h job), mirrors live resources; `databricks bundle validate -t dev` → OK. Bind-before-deploy note included (avoids duplicate pipeline).
- **bronze→silver `deals` transform** (`src/transform/silver_deals.py`): implements C-012 — `resolve_selected_offer` (filter `Select_Offer__c=true`, dedup latest `LastModifiedDate`, `multi_selected_offer` flag); `select_rename_deals` (funded book, left-join selected offer, economics-from-offer with Opportunity fallback, `selected_offer_missing` flag, FICO string→int); `build_silver_deals` writes managed `mca_mri.silver.deals`.
- **C-007 now implemented**: `date_sanity_flag` rewritten to flag a large funded/created gap in **either** direction (threshold `DATE_SANITY_GAP_DAYS=365`, calibratable) across pure-Python + Spark mirrors; the old literal `funded>created` rule both over-flagged latency and missed the migration artifact. Added `BronzeTable` constants. **34 tier-1 tests green.**

**Done (cloud, 2026-05-31) — tier-2 reconciliation PASSED (`silver_test`):**
- Ran `build_silver_deals` into `mca_mri.silver_test.deals` on **serverless** (one-time job submit; src + `recon_silver_deals` staged as Workspace **files**, driver `run_tier2` as a notebook — no bundle deploy, so the live pipeline is untouched/not duplicated). Test code lives in `tests/tier2/`.
- **Hard checks all green:** silver count **3,959 == bronze funded 3,959** (exact); schema == `deals_schema()` (column names + order); **no-surface guard clean** on the consumed projection (the 3 `_sf_stored_*` checkpoint cols stay on the base table only); selected-offer anomalies within G1 band — `selected_offer_missing=2` (=G1), `multi_selected_offer=26` (G1~28).
- **Diagnostic DQ rollups (informational, never drop rows):** `date_sanity_flag=1686` (~43% — large funded/created gaps, consistent with the SF migration stamping `CreatedDate`; C-007 working as designed, **threshold calibration to revisit**); `months_in_business_is_missing=3750` (~95%, matches G1 sparsity); `fico_is_missing=382`; `rtr_check_flag=21`.
- **Reference-merchant-by-name check = 0/4 (expected, not a failure):** `Opportunity.Name` is the auto-generated opportunity label, not the merchant business name (that lives on `Account.Name`). Validating the four CLAUDE.md §8 merchants needs the Account join → **deferred to S1 identity resolution**. Logged as **FU-002**.

**Done (cloud, 2026-05-31) — prod `silver.deals` promotion:**
- Ran the same verified driver against **`schema=silver, allow_prod=true`** (serverless one-time job). **`mca_mri.silver.deals` built — recon identical to `silver_test`:** 3,959 == bronze funded, schema == `deals_schema()`, no-surface clean, `selected_offer_missing=2` / `multi_selected_offer=26`.

**Done (code + cloud, 2026-05-31) — optional silver tables `offers` + `field_history`:**
- **`src/transform/silver_offers.py`** (raw Offer__c catalogue, all offers — underscored economics per C-012, `is_deleted` carried not filtered) + **`src/transform/silver_field_history.py`** (OpportunityFieldHistory event source for S3 renewal-cadence reconstruction). Added `OFFERS_MAP`, extended `FIELD_HISTORY_MAP` (+`history_id`, `data_type`), `offers_schema()`, tier-1 tests. **41 tier-1 tests green.**
- **Built + reconciled on `silver_test`, then PROMOTED TO PROD** (serverless; `tests/tier2/recon_aux.py` + `run_tier2_aux`). Both exact to bronze, schema-matched, **0 FK orphans**: `silver.offers` = **57,586** (5,143 selected), `silver.field_history` = **220,172** (13,705 StageName events). Workspace staging cleaned up.

**Next (all deferred / non-blocking):**
- FU-001 (pre-go-live): lock External Client App pre-auth to the integration user; switch pipeline run-as to a service principal.
- FU-002 (S1): validate the four reference merchants via Account join once identity resolution lands.
- Calibrate `DATE_SANITY_GAP_DAYS` (currently 365 → ~43% flagged from the SF migration; diagnostic only).
- Bind `resources/ingestion_pipeline.yml` to the live pipeline before any `bundle deploy` (avoids duplicate pipeline).

**Exit criteria:** [SPRINT_0_PLAN](sprints/SPRINT_0_PLAN.md) §9 — bronze landed + **all three silver tables (`deals`, `offers`, `field_history`) built, reconciled to bronze, and in prod `silver`** ✅. **S0 ingestion→silver path COMPLETE.**
