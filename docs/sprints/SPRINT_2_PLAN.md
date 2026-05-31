# Sprint 2 Plan — Feature Layer & Amortization Clock

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture block 3 (`CLAUDE.md` §3): *derived features; balance/paydown/eligible-date (Appendix A)*. Goal: turn the static, identity-resolved Deal Table into a **live, daily-recomputed** view of where every deal and merchant actually stands — the **core value the system adds over Salesforce** (`CLAUDE.md` 2.1). **This is the THE-core-principle sprint:** we recompute everything time-dependent ourselves and never trust SF's frozen `Remaining Balance` / `Percentage Paid` / `Estimated Renewal Date`.

---

## 1. Objective & scope

**Objective:** implement the **Amortization Clock** exactly as Appendix A specifies, producing per-deal and per-merchant time-dependent fields, and publish them into the gold layer as **point-in-time, daily-recomputed** outputs keyed on `deal_id` / `merchant_id`. Extend `gold.merchants` from the S1 identity+profile seed into the **"Position & burden (the clock)"** section of the Merchant Gold Table contract.

**In scope (Appendix A literally):**
- **Per-deal clock outputs** (extend `gold.deals` or a `gold.deal_clock` companion — see D-201):
  - `rtr` = `funded_amount × factor_rate` (total owed, never changes; validated against payback_amount checkpoint)
  - `elapsed_payments` — counted per **A.3** (daily = business days M–F excl. holidays ~21.7/mo; weekly = calendar weeks), **capped at `num_payments`** so it never overcounts past payoff
  - `amount_paid` = `payment_amount × elapsed_payments`
  - `est_current_balance` = `max(0, rtr − amount_paid)`
  - `est_paydown_pct` = `amount_paid ÷ rtr`
  - `est_renewal_eligible_date` (**A.4**) — solve for the payment number where paydown crosses the renewal threshold (funder lookup, **default 55%**), map back to a business-day/weekly calendar date
  - `is_eligible_now` — `est_paydown_pct ≥ threshold`
  - `balance_source` enum (`actual` / `estimated`) (**A.3**) — `actual` when servicing feed exists, else `estimated`
  - `closure_status` enum (**A.5b**) — `active` / `closed_clean` / `closed_default`; **computed**, since SF has no closure status. `paydown ≥ 100%` alone is NEVER `closed_clean` — the Notes default-cause separates clean from default
- **Per-merchant roll-up** (**A.5**) into `gold.merchants`:
  - `active_position_cnt` (= count of deals with `closure_status = active` — itself an inference)
  - `total_weekly_debit` = Σ active-position payments (frequency-normalized to weekly)
  - `est_current_balance`, `est_paydown_pct` (primary-position paydown drives merchant eligibility), `est_renewal_eligible_date`
  - `burden_ratio` = `total_weekly_debit ÷ est_weekly_revenue` (compute where revenue available; else null + flag)
  - `balance_source` rolls up as the **weakest** across positions (any `estimated` → merchant `estimated`)
  - `tenure_days` = `today − first_funded_date` (time-dependent → recomputed here, not S1)
- **Point-in-time semantics** — a `clock_run_date` (= run's "today") stamped on outputs so the recompute is reproducible/auditable.
- **Day-one checkpoint validation** — reconcile day-one computed balance against SF stored values *as a validation checkpoint only* (never surfaced), per A.0.
- Tier-1 + tier-2 tests; UC governance/lineage; reconciliation against the four validation merchants.

**Out of scope (do NOT build):**
- **Rung / lifecycle / state machine / event log** (**S3**, Appendix B) — even though `closure_status` *feeds* the lifecycle gate, S2 stops at computing it; the gate logic is S3.
- Behavioral/RFM, prediction, rung, offer, compliance, advisory, app fields of the Merchant Gold Table (**S3+**).
- **True-split / percentage-holdback** servicing (A.6 fallback) — **off by default**; build only if the audit confirms split deals exist (D-202). `holdback_pct` stays deferred (per S1 `test_holdback_pct_deferred_to_s2_not_trusted`).
- Sourcing a real **servicing/bank feed** — if no feed exists, the whole book runs the **estimated path**; the `actual` path is wired but inert until a feed lands (D-203).
- Activation / reverse-ETL of clock outputs to Salesforce (**S4**).

## 2. Definition of Ready

| Gate | State |
|---|---|
| S1 complete (`gold.deals` / `gold.merchants` / `gold.merchant_crosswalk` in prod, reconciled) | ✅ 2026-05-31 |
| G2 clock math (Appendix A) RESOLVED | ✅ — Appendix A is the spec, transcribed literally |
| Static terms present on `gold.deals` (funded_amount, factor_rate, payment_amount, frequency, num_payments, funded_date, payback_amount) | ✅ S1 Deal Table carries them; **confirm null-rates in build step** |
| Business-day calendar / holiday source decided | ⏳ D-204 (US Federal holiday set vs business-day-only) |
| Funder renewal-threshold lookup | ⏳ D-205 — `mca_funders` dataset location + per-funder threshold field; default 55% until wired |
| Servicing/bank feed availability | ⏳ D-203 — confirm none today → estimated-path-only for v1 |
| D-201…D-205 signed | ⏳ need sign-off before build (clock outputs are the join surface for S3) |

Scaffolding of `common/clock/` pure functions + schemas can proceed in parallel; the prod build waits on the decisions.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate Appendix A (A.0–A.6) + the Merchant Gold Table "Position & burden (the clock)" contract fields as an explicit field list with formulas and quality flags.
2. **Design**
   - **`common/clock/` pure module** (no Spark at import — tier-1 testable, mirrors `common/identity`):
     - `amortization.py`: `rtr()`, `amount_paid()`, `est_current_balance()`, `est_paydown_pct()` — pure arithmetic on scalars (A.2).
     - `calendar.py`: `elapsed_payments(funded_date, today, frequency, num_payments, holidays)` (business-day vs weekly counting, term cap — A.3); `eligible_date(...)` inverse solve (A.4).
     - `closure.py`: `closure_status(paydown_pct, has_default_note, on_schedule)` (A.5b three-state logic).
     - `rollup.py`: pure merchant roll-up helpers (weakest `balance_source`, weekly-debit normalization, active-position count).
   - **Spark transform** `transform/gold_clock.py`: reads `gold.deals` (static terms) + `gold.merchants` + optional servicing feed; applies the pure functions as UDFs/columns; stamps `clock_run_date`; writes per-deal + per-merchant clock outputs.
   - **Layer/shape decision (D-201):** per-deal clock columns appended to `gold.deals` vs a separate point-in-time `gold.deal_clock` table; merchant clock fields update `gold.merchants` in place vs a `gold.merchant_clock` snapshot. Recommend a **separate point-in-time table** keyed `(deal_id/merchant_id, clock_run_date)` so daily recomputes are append-only and auditable, with a `current`-view for the live read.
   - **Day-one checkpoint:** read `_sf_stored_*` checkpoint columns (validation only, never propagated) to assert day-one math; emit a `clock_checkpoint_delta` diagnostic.
3. **Definition of Ready** — D-201…D-205 signed; checklist in tracker.
4. **Build**
   - `common/clock/` pure functions (shared component).
   - `common/schemas/gold.py`: `deal_clock_schema()` / extend `merchant_schema()` for the clock section; new DQ columns (`balance_source`, `*_is_missing`, `clock_run_date`).
   - `common/constants.py`: clock constants already seeded (`BUSINESS_DAYS_PER_MONTH=21.7`, `WEEKS_PER_MONTH=4.33`); add `DEFAULT_RENEWAL_THRESHOLD=0.55`, holiday set / calendar config.
   - `transform/gold_clock.py`.
   - DQ: paydown bounds [0,1], balance ≥ 0, elapsed ≤ num_payments, eligible_date ≥ funded_date, balance_source populated 100%.
5. **Test** — tier-1 (pure clock math on hand-worked examples incl. the four merchants; term-cap; daily vs weekly; closure three-state; roll-up weakest-source) + tier-2 (build on `gold_test`, reconcile, day-one checkpoint, four-merchant expected balances). Full suite after each build piece.
6. **Review** — self-review + `code-review`; verify against **real funded-deal screens** (A.0 example: a defaulted deal computing ~100% must land `closed_default`, never `closed_clean`).
7. **Documentation** — clock field docs; update tracker, SHARED_COMPONENTS (`common/clock`), runbook (daily recompute job), DECISIONS (D-201…D-205).
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` only on your approval** (Rule 5). Daily recompute job is **defined** but scheduling/activation is gated.

## 4. Shared components created/changed

- **New** `common/clock/`: `amortization.py`, `calendar.py`, `closure.py`, `rollup.py` (all pure, Spark-free at import — tier-1 testable; the home `src/common/clock` was reserved in the tracker).
- **Changed** `common/schemas/gold.py`: `deal_clock_schema()` (or extend `deal_table_schema()`), clock section of `merchant_schema()`.
- **Changed** `common/field_maps.py`: clock-output field maps (source = "Amortization clock (Appendix A) — NOT SF stored").
- **Changed** `common/constants.py`: `DEFAULT_RENEWAL_THRESHOLD`, calendar/holiday config, `GoldTable.DEAL_CLOCK`/`MERCHANT_CLOCK` if D-201 → separate tables.
- **Changed** `common/dq`: clock DQ predicates (bounds, monotonicity, source populated).
- **Reuse:** `io.guards` (no-surface: `_sf_stored_*` stay checkpoint-only, never surfaced), `dq.predicates` (missing/zero flags), the four-merchant fixtures.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local):** pure clock math — `rtr`, `amount_paid`, `est_current_balance` (incl. `max(0, …)` floor), `est_paydown_pct`; `elapsed_payments` for daily (business-day) and weekly with **term cap**; `eligible_date` inverse-solve crosses threshold on the right period; `closure_status` three-state incl. the **defaulted-but-100%** case → `closed_default`; roll-up weakest-`balance_source` + weekly-debit normalization + active-position count; schema **== contract** (extend `test_contract_consistency` / `test_gold_maps`); `balance_source` enum domain; no `_sf_stored_*` surfaced.
- **Tier-2 (Databricks `gold_test`):** build clock outputs; **reconciliations** — (a) `rtr ≈ payback_amount` within tolerance on the funded book (day-one checkpoint); (b) `est_paydown_pct ∈ [0,1]`, `est_current_balance ≥ 0`, `elapsed_payments ≤ num_payments` for 100% of rows; (c) `balance_source` 100% populated, all `estimated` if no feed (D-203); (d) `est_renewal_eligible_date ≥ funded_date`; (e) merchant `active_position_cnt` = count of active deals, `balance_source` = weakest across positions; (f) day-one `clock_checkpoint_delta` within tolerance, contradictions flagged not hidden; (g) no-surface guard clean.
- **Four-merchant scenario (carry from S1):** Starr (defaulted) → `closed_default` (NOT clean despite ~100%); One Big Promotion (paid 100%, quiet since 2020) → `closed_clean`; Tom Snell (1 fresh deal) → `active`, clock running, low paydown; Wolf (renewed ~14 days in, $30k→$40k) → `active` with serial/rapid-reup-relevant balances. Hand-compute expected balances as fixtures.

## 6. Data contracts touched

- **Reads:** `mca_mri.gold.deals` (static terms + `_sf_stored_*` checkpoint cols, validation-only), `mca_mri.gold.merchants` (identity/profile + `first_funded_date`), `mca_funders` (renewal thresholds — D-205), optional servicing/bank feed (D-203). Data Contract xlsx governs the clock field names + "NOT SF stored" verdicts.
- **Writes:** clock outputs per D-201 — either appended to `gold.deals` + `gold.merchants`, or new `gold.deal_clock` + `gold.merchant_clock` point-in-time tables (+ `gold_test` mirrors).
- **Stable interface:** `closure_status`, `est_paydown_pct`, `is_eligible_now`, `est_renewal_eligible_date`, `active_position_cnt`, `burden_ratio` become the inputs S3's lifecycle gate + rung waterfall and S4's queue depend on — clock accuracy affects **classification**, not just displayed balances (A.5b).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Estimated path drift** vs reality (no servicing feed) | Term-cap bounds it; `balance_source = estimated` carried through to advisory tone (softer language); day-one checkpoint validates the starting point |
| **Defaulted deal computes ~100%** → mislabeled `closed_clean` | A.5b: `paydown ≥ 100%` alone never = clean; require Notes default-cause to separate; defaulted-but-100% is an explicit tier-1 test (Starr) |
| **Business-day / holiday calendar wrong** → wrong elapsed count | Single sourced holiday set (D-204); tier-1 tests on known date spans; ~21.7/mo only as a *derived* checkpoint, not the counting method |
| **Renewal threshold assumption** (default 55%) wrong per funder | Funder lookup (D-205); default documented + flagged; eligible-date recomputes when a funder-specific value lands |
| **Active-position count is an inference** → propagates into burden + Serial test | Treat as computed, carry `balance_source`; surface estimated merchants for review; document that classification depends on clock accuracy |
| **Point-in-time confusion** (which "today"?) | Stamp `clock_run_date` on every output; reproducible recompute; never mutate prior runs (append-only if D-201 → snapshot table) |
| **Surfacing SF stored balances** by accident | `_sf_stored_*` used only inside the day-one checkpoint; no-surface guard test on all clock outputs |
| Building S3 logic early | S2 computes `closure_status` and stops; the lifecycle gate + rung waterfall (Appendix B) stay S3 |
| Revenue/burden gaps mistaken for zeros | `burden_ratio` / `est_weekly_revenue` null + `*_is_missing` where no feed; never 0 (CLAUDE.md 2.5) |

## 8. Open decisions (need your sign-off before build)

- **D-201 — Clock output shape/placement.** (a) **Separate point-in-time tables** `gold.deal_clock` + `gold.merchant_clock` keyed `(id, clock_run_date)`, append-only, with a `current` view *(recommended — auditable daily recompute, clean lineage, no destructive overwrite)*; (b) append clock columns onto `gold.deals` / `gold.merchants` in place (simpler reads, but overwrites each run and loses point-in-time history). Confirm.
- **D-202 — True-split / holdback handling.** Recommend: **off by default** (fixed-ACH is the confirmed common case, A.6); build the estimated-via-revenue split path only if the audit finds split deals. Confirm we defer, and confirm whether the audit already answered this.
- **D-203 — Servicing/bank feed.** Recommend: confirm **no feed exists today** → v1 runs the **estimated path** for the whole book; the `actual` path is wired but inert. Need confirmation + a pointer if a feed actually exists.
- **D-204 — Business-day calendar / holidays.** Recommend a single **US Federal holiday** set for the daily business-day count (M–F minus holidays). Confirm holiday source / whether plain M–F (no holidays) is acceptable for v1.
- **D-205 — Funder renewal-threshold lookup.** Where is the per-funder threshold in `mca_funders`, and is **55% default** acceptable until wired? Need the dataset pointer + field.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/clock/` pure functions implement A.2–A.5b **literally**; hand-worked examples (incl. the four merchants) pass — *tier-1 math tests*.
- [ ] Clock outputs published to `gold` (per D-201) at the right grain with `clock_run_date` stamped; `rtr ≈ payback_amount` day-one checkpoint within tolerance — *reconciliation + schema test*.
- [ ] `est_paydown_pct ∈ [0,1]`, `est_current_balance ≥ 0`, `elapsed_payments ≤ num_payments`, `est_renewal_eligible_date ≥ funded_date` for **100%** of rows — *bounds DQ test*.
- [ ] `balance_source` populated 100% (all `estimated` if no feed); merchant source = weakest across positions — *DQ + roll-up test*.
- [ ] `closure_status` computed for every deal; a **defaulted deal computing ~100% is `closed_default`, never `closed_clean`** (Starr) — *scenario test*.
- [ ] Merchant roll-up (`active_position_cnt`, `total_weekly_debit`, `burden_ratio`, paydown/eligible-date) correct; burden/revenue gaps null + flagged, never 0 — *roll-up + DQ test*.
- [ ] **No SF stored balances surfaced** anywhere in clock outputs (`_sf_stored_*` checkpoint-only) — *no-surface test*.
- [ ] Four-merchant expected balances match hand-computed fixtures — *scenario test + manual spot-check vs real screens*.
- [ ] Daily recompute job **defined as code** (DAB); scheduling/activation gated on approval — *job definition in repo*.
- [ ] Unity Catalog governs the new clock tables with lineage from `gold.deals`/`gold.merchants` — *manual UC check + tracker note*.
- [ ] Tier-1 + tier-2 suites green; results logged — *suite output in tracker*.
