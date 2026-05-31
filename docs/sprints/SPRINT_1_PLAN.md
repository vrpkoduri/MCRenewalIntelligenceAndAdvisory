# Sprint 1 Plan — Identity Resolution & Canonical Deal Table

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture block 2 (`CLAUDE.md` §3): *canonical merchant identity; deal grain*. Goal: turn the governed silver layer into the **canonical, identity-resolved Deal Table** every later sprint reads from. **Still no live recompute** (balance/paydown/clock are S2).

---

## 1. Objective & scope

**Objective:** resolve every funded deal to a **canonical merchant** (`merchant_id`) and publish the conformed **Deal Table** (Data Contract "Deal Table" sheet) at one-row-per-advance grain, plus the seed **merchant dimension** that S2+ will extend into the gold merchant-feature table.

**In scope:**
- **Identity resolution** — collapse Salesforce `Account` duplicates into canonical merchants; assign a **stable `merchant_id`**; emit a `merchant_sf_id → merchant_id` crosswalk + static profile (business name, principal, governing state, tax id, business-start, industry).
- **Deal Table** — promote `silver.deals` to the contract Deal Table schema with `merchant_id` attached; derive the S1-available `Derive` fields: `is_renewal_of` (renewal chain), `prior_factor_rate`, `term_months` (static, Appendix A.3 calendar conversion), deal `status` (from `silver.field_history` Stage transitions). Carry `Must-capture` gaps as null + `*_is_missing` flags.
- **FU-002** — resolve the four validation merchants (CLAUDE.md §8) by business name via the Account join; close the deferred S0 follow-up.
- Tier-1 + tier-2 tests; UC governance/lineage on the new tables; reconciliation.

**Out of scope (do NOT build):**
- Any **live recompute** — `est_current_balance`, `est_paydown_pct`, `est_renewal_eligible_date`, `burden_ratio`, tenure/feature math (all **S2**, Appendix A).
- Rung/lifecycle/state machine/event log (**S3**), activation (**S4**), offers/prediction/comms/app (**S5+**).
- Capturing the `Must-capture` gaps themselves (positions, disclosed balance, net funded, PG, EIN where absent) — S1 only **exposes** them as flagged nulls; sourcing is a later data-acquisition workstream.
- The full 66-field Merchant Gold Table — S1 seeds only the **identity + static-profile** subset; features accrete S2→S7.

## 2. Definition of Ready

| Gate | State |
|---|---|
| S0 complete (silver `deals`/`offers`/`field_history` in prod, reconciled) | ✅ 2026-05-31 |
| `bronze.account` profiled for identity keys | ◑ keys identified (Tax_ID, DBA, phone, MasterRecordId, name, state) — **profile populations in build step** |
| AATM IP located (`lakebase_aatm_*`) | ⏳ confirm which artifact + whether to port logic or call it (D-105) |
| Identity strategy decisions | ⏳ D-101…D-104 (below) need your sign-off before build |
| G2/G3 (clock/rung) | ✅ not needed until S2/S3 |

Build cannot start until **D-101…D-105** are decided (identity is irreversible-ish once `merchant_id`s are published downstream). Scaffolding of `common/identity` helpers + schemas can proceed in parallel.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate from Data Contract *Deal Table* (24 fields) + identity/profile subset of *Merchant Gold Table*; confirm grain (deal = funded Opportunity; merchant = canonical Account cluster).
2. **Design**
   - Identity pipeline: **normalize → block → match → cluster → assign id**. Normalizers (`common/identity`): `normalize_tax_id`, `normalize_phone`, `normalize_name` (case/punct/suffix-fold), `normalize_state`. Blocking/match keys in priority order: (1) SF `MasterRecordId` merge chains; (2) exact normalized **Tax ID** (`Key_Reference_Tax_Id__c`/`Tax_ID__c`); (3) exact normalized **phone**; (4) normalized **name|DBA + governing state**. Confidence tiering per D-102.
   - `merchant_id` minting per D-101 (recommend persisted crosswalk for stability across daily refresh).
   - Deal Table derivations: renewal chain ordering per merchant by `funded_date` → `is_renewal_of` / `prior_factor_rate` (D-103); `term_months` static conversion (Appendix A.3: daily ÷21.7, weekly ÷4.33 payments/month); `status` from `field_history` StageName transitions (latest stage + terminal flags).
   - Layer placement (D-104): recommend **gold** — `mca_mri.gold.deals` + `mca_mri.gold.merchants` (with `gold_test` mirror).
3. **Definition of Ready** — D-101…D-105 signed; checklist in tracker.
4. **Build**
   - `common/identity/` normalizers + matcher + id minting (shared component).
   - `common/schemas`: `deal_table_schema()`, `merchant_schema()` derived from contract.
   - `field_maps`: `MERCHANT_MAP` (Account→merchant dimension), `DEAL_TABLE_MAP` (silver.deals + derivations→contract Deal Table).
   - `transform/gold_merchants.py`, `transform/gold_deals.py`.
   - DQ: identity metrics (collapse ratio, unmatched residual, multi-merchant-per-deal guard).
5. **Test** — tier-1 (normalizers, map integrity, schema vs contract, renewal-chain logic on fixtures) + tier-2 (build on `gold_test`, reconciliation, FU-002 by name). Full suite after each build piece.
6. **Review** — self-review + `code-review`; verify matching behavior on real edge cases (shared phone across distinct merchants, blank tax id), not just types.
7. **Documentation** — schema docs for `gold.deals`/`gold.merchants`; update tracker, SHARED_COMPONENTS, runbook; record identity decisions in DECISIONS.
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` only on your approval** (Rule 5).

## 4. Shared components created/changed

- **New** `common/identity/`: `normalize.py` (tax id / phone / name / state), `match.py` (blocking + clustering + confidence tiers), `keys.py` (`merchant_id` minting / crosswalk).
- **Changed** `common/schemas/silver.py`→ add gold schemas (or new `common/schemas/gold.py`): `deal_table_schema()`, `merchant_schema()`.
- **Changed** `common/field_maps.py`: `MERCHANT_MAP`, `DEAL_TABLE_MAP` (+ helper column lists).
- **Changed** `common/constants.py`: `class GoldTable` (`DEALS`, `MERCHANTS`); identity thresholds (match confidence, suffix stop-words) under a new `Identity` group.
- **Changed** `common/dq`: identity DQ predicates (`is_blank_tax_id`, duplicate-cluster metrics).
- Reuse: existing `io.guards` (no-surface still applies — `_sf_stored_*` never propagate into gold), `dq.predicates` (missing/zero flags for the gaps).

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local):** normalizers (tax-id strip, phone E.164-ish, name suffix-fold, state map); `is_renewal_of`/`prior_factor_rate` chain logic on synthetic per-merchant deal sequences (incl. Wolf rapid re-up); `term_months` conversion (daily/weekly); Deal Table + merchant schema **== contract** (extend `test_contract_consistency`); map integrity (PKs, no dup columns, gaps flagged Must-capture).
- **Tier-2 (Databricks `gold_test`):** build `gold.merchants` + `gold.deals`; **reconciliations** — (a) every `silver.deals.opportunity_id` present in `gold.deals` with non-null `merchant_id` (100% — `AccountId` was 100% set on funded book); (b) `deal_count == 3,959`; (c) `distinct merchant_id ≤ distinct merchant_sf_id` (report **collapse ratio**); (d) **no deal → >1 merchant_id**; (e) every Renewal/Buyout either links to an earlier same-merchant deal or is flagged `renewal_unlinkable`; (f) no-surface guard clean on gold. **FU-002:** the four validation merchants resolve and are found by `business_name`.
- Reuse the four-merchant fixtures; extend `expected` with merchant-clustering expectations (Wolf = serial/multi-deal; OBP = single deal).

## 6. Data contracts touched

- **Reads:** `mca_mri.silver.deals`, `mca_mri.silver.field_history` (deal status), `mca_mri.bronze.account` (merchant attributes/dedup keys). Data Contract xlsx governs Deal-Table/Merchant naming + verdicts.
- **Writes:** `mca_mri.gold.merchants` (identity dimension + static profile), `mca_mri.gold.deals` (canonical Deal Table). `gold_test` mirrors first.
- **Stable interface:** `merchant_id` becomes the join key every later sprint depends on — hence the stability decision (D-101) matters most.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Over-merging** distinct merchants (shared phone/ISO, family businesses) | Tiered confidence: exact Tax ID auto-merges; weaker keys produce *candidates* flagged for review, **not** auto-merged in v1 (conservative); record match reason per cluster |
| **Under-merging** (same merchant, blank/typo tax id) | Multi-key blocking + name+state+phone fallback; surface unmatched-residual metric; manual review queue |
| **`merchant_id` instability** across refreshes breaks downstream joins | Persisted crosswalk (D-101 rec) so merges don't re-key; deterministic fallback documented |
| Sparse/dirty dedup keys (Tax ID blank, DBA vs legal name) | `*_is_missing` flags; never treat blank as a match key; profile populations before trusting a key |
| `is_renewal_of` wrong on migrated records (bad created/funded dates) | Order by `funded_date`; cross-check against any SF renewal link + `date_sanity_flag`; flag unlinkable rather than guess |
| Building S2 features early | `gold.merchants` ships **identity+static profile only**; clock/feature columns stay null/absent with reserved homes |
| Must-capture gaps mistaken for real zeros | Carry as null + flag; never 0 (CLAUDE.md 2.5) |

## 8. Open decisions (need your sign-off before build)

- **D-101 — `merchant_id` minting & stability.** Options: (a) **persisted crosswalk** table, upserted each refresh so ids never change on re-merge *(recommended — stable join key downstream)*; (b) deterministic hash of canonical natural key (simpler, but re-keys if the canonical key changes). 
- **D-102 — Match confidence / auto-merge policy.** Recommend: exact normalized **Tax ID** → auto-merge (high); **phone** or **name+state** alone → candidate cluster, flagged, **not** auto-merged in v1. Confirm thresholds + whether to stand up a review queue now or defer.
- **D-103 — `is_renewal_of` definition.** Recommend: per-merchant deals ordered by `funded_date`; a `Renewal`/`Buyout` deal links to the immediately prior same-merchant deal; validate against any SF renewal/legacy link field if one exists (to confirm during build). Confirm.
- **D-104 — Layer placement.** Recommend **gold** (`gold.deals`, `gold.merchants`) since these are conformed/curated and are the single source of truth for S2+. Alternative: keep Deal Table in `silver`. Confirm.
- **D-105 — AATM reuse.** Which `lakebase_aatm_*` artifact is the entity-matching IP, and do we **port** its logic into `common/identity` (preferred for testability/lineage) or **call** the existing service? Need a pointer.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `mca_mri.gold.deals` exists at deal grain; **every** funded deal (3,959) present with a **non-null `merchant_id`**; schema conforms to contract Deal Table — *reconciliation + schema test*.
- [ ] `mca_mri.gold.merchants` exists; `merchant_id` is unique PK; crosswalk maps every `merchant_sf_id` on the funded book; **collapse ratio reported** — *uniqueness + coverage test + tracker note*.
- [ ] **No deal maps to >1 merchant**; identity matches carry a recorded reason/confidence — *integrity test*.
- [ ] `is_renewal_of`/`prior_factor_rate`/`term_months`/`status` populated where derivable; unlinkable renewals flagged — *derivation tests + DQ counts*.
- [ ] `Must-capture` gaps present as null + `*_is_missing` flags (no fake zeros) — *DQ test*.
- [ ] No-surface guard clean on gold (no `_sf_stored_*`) — *no-surface test*.
- [ ] **FU-002 closed:** the four validation merchants resolve and are found by business name; clustering matches expectations (Wolf multi-deal serial; OBP single) — *scenario test + manual spot-check*.
- [ ] Unity Catalog governs `gold` with lineage from silver visible — *manual UC check + tracker screenshot*.
- [ ] Tier-1 + tier-2 suites green; results logged — *suite output in tracker*.
