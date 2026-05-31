# Sprint Tracker

Fast-moving operational log of sprint status, decisions, and test results. The formal
narrative roadmap is `Morgan_Cash_Build_Plan_Sprint_Roadmap.docx`; this is the living index (GENERAL_INSTRUCTIONS Rule 1).

## Status at a glance

| Sprint | Block | Status | Plan | Notes |
|---|---|---|---|---|
| **S0** | Ingestion → silver | **Complete** | [SPRINT_0_PLAN](sprints/SPRINT_0_PLAN.md) | Bronze **live** (4 SF objects, G1 ✅); **all 3 silver tables in PROD 2026-05-31** — `deals` (3,959), `offers` (57,586), `field_history` (220,172); every table reconciles to bronze, schema + no-surface green. Remaining: FU-001/FU-002 (deferred, non-blocking) |
| S1 | Identity + Deal table | Not started | — | AATM IP exists (`lakebase_aatm_*`) for reuse |
| S2 | Features + clock (Appendix A) | Not started | — | `src/common/clock` home reserved |
| S3 | Rung classifier + state machine + event log (Appendix B) | Not started | — | `src/common/rung`, `eventlog` reserved |
| S4 | Activation + Book Health | Not started | — | |
| S5 | Offer Engine | Not started | — | `mca_funders` = reuse dataset |
| S6 | Prediction | Not started | — | |
| S7 | Advisory comms + compliance | Not started | — | |
| S8+ | Merchant app | Not started | — | |

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
