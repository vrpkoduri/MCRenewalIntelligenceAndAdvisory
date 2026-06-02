# Sprint 3 Plan — Rung Classifier + State Machine + Event Log

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture block 4 (`CLAUDE.md` §3): *Rules-Based Rung Classifier + State Machine + Event Log — Appendix B*. Goal: take the live, daily-recomputed clock (S2) and **classify every funded merchant** — first onto a **lifecycle state** (the Step-0 gate), then, for active merchants, onto a **health rung 1–5** via the Appendix B waterfall — producing an honest, confidence-scored, direction-of-travel-aware label for every merchant, plus the **append-only event log** that becomes the floor queue's ordering signal and Phase-6 learning data.

**This is the first sprint that produces a merchant-facing verdict.** It is rules-only — **no ML** (S6). Everything is deterministic, auditable, and reconciles. The classifier reads the spine; it never recomputes it.

---

## 1. Objective & scope

**Objective:** implement Appendix B **literally** as a two-stage deterministic engine — (1) a **lifecycle gate** that routes Defaulted / Dormant / New-establishing / Active merchants, and (2) a **rung waterfall** (Distressed → Serial → Disciplined → Growth → Graduate, first-match-wins with a stress override that pulls down) for active merchants — emitting the Framework's output object `{ rung, confidence, missing_signals[], direction_of_travel }` per merchant. Publish lifecycle + rung as **point-in-time gold outputs** keyed `(merchant_id, classify_run_date)`, and stand up the **event log** as the append-only spine for the daily queue and future learning.

**In scope (Appendix B + Framework §4 literally):**

- **Step 0 — lifecycle gate** (`common/rung`, runs before the waterfall; B.2):
  - **Defaulted** → determine sub-type and route: true-default → Distressed/exit; early-payoff or clawback → win-back; restructured → impaired-managed; **undetermined default → do-not-fund + flag `default_subtype=unknown`** (Starr).
  - **Dormant** → `time_since_last_active > 2 × merchant's own median renewal gap` (or `2 × book-median` when the merchant has no history) → win-back.
  - **New / establishing** → single recently-funded position, no renewal history → **healthy clock-running but NOT labeled Disciplined** until a clean renewal completes (Tom Snell).
  - **Active** → proceed to the rung waterfall.
- **Step 1 — rung waterfall** (`common/rung`, first match wins, stress override pulls down; B.3):
  - **Rung 1 Distressed** (OR): stress event (NSF / default note), OR `burden_ratio > ~0.30`, OR worsening-factor + shrinking-net, OR rapid re-up into worse terms.
  - **Rung 2 Serial / Multi-position** (`rapid_reup_flag` PRIMARY, OR Position-field ≥ 2, OR parsed concurrent positions ≥ 2) **AND not Distressed**; discriminator vs Distressed = payment health + burden (Wolf).
  - **Rung 3 Disciplined Renewer** (AND): single position AND healthy-paydown renewal ≥ 50% AND clean payments AND ≥ 1 prior clean renewal.
  - **Rung 4 Growth Borrower** (AND): Disciplined-or-better AND advance rising while **relative** burden falls.
  - **Rung 5 Graduate** (AND): Growth-or-better AND qualifies for cheaper products.
  - **Unclassified**: key signals missing → logged to `missing_signals[]`; counted as an explicit pile, never force-fit.
- **Classification output object** (Framework §4) per merchant: `rung` (1–5 or null when gated/unclassified), `lifecycle_state`, `confidence` (continuous score), `missing_signals[]`, `direction_of_travel` (climbing / holding / sliding — used to prioritize the queue).
- **State machine**: persist current lifecycle_state + rung and detect **transitions** between `classify_run_date`s; emit a transition event when either changes.
- **Event log** (`common/eventlog`, append-only; "capture every signal and touch as events from the start"): rung/lifecycle assignments + transitions + the underlying signal snapshot, keyed by merchant + timestamp; this is the Phase-6 learning dataset and the app activity feed. v1 emits **classification + transition** events; touch/comms events arrive in S4/S8.
- **Daily queue (read view)**: order active book by `direction_of_travel` (sliding first) + confidence, surfacing who needs attention — no activation/reverse-ETL (that's S4).
- Tier-1 + tier-2 tests; UC governance/lineage; reconciliation + the four-merchant validation (B.5).

**Out of scope (do NOT build):**

- **Any ML / prediction** (S6) — rung is rules-only; `confidence` is a deterministic rules score, not a model probability.
- **Activation / reverse-ETL to Salesforce, Book-Health analytics dashboards** (S4) — S3 produces the labels + queue *view*; pushing them anywhere is S4.
- **Offer engine, advisory comms, compliance gate, app** (S5+).
- **Bank-statement parsing for concurrent positions** (S7 agentic extraction) — B.4 stages position detection: v1 uses **rapid_reup_flag (day-one) + Position-field**; parsed/live-feed positions land later. Do not build statement parsing here.
- **Recomputing any clock/balance/eligibility** — those are S2 spine reads; S3 consumes `closure_status`, `est_paydown_pct`, `is_eligible_now`, `burden_ratio`, `active_position_cnt`, `rapid_reup_flag` and never recalculates them.
- **Agents** — no agents before/around the spine; the classifier is deterministic code (CLAUDE.md §3).

## 2. Definition of Ready

| Gate | State |
|---|---|
| S2 complete (`gold.deal_clock` / `gold.merchant_clock` + `_current` views in prod, reconciled) | ✅ 2026-05-31 |
| G3 rung rules (Appendix B) RESOLVED | ✅ — Appendix B is the spec, transcribed literally above |
| Clock signals present on `gold.merchant_clock` (`closure_status`, `est_paydown_pct`, `is_eligible_now`, `burden_ratio`, `active_position_cnt`, `balance_source`) | ✅ S2 carries them; **confirm null-rates feeding `missing_signals`** |
| Renewal-chain / prior-clean-renewal signal available | ✅ D-303 SIGNED — trust `Type=Renewal`; 503 unlinkable = data gap, flagged `renewal_chain_incomplete`, not a disqualifier (FU-302) |
| `rapid_reup_flag` definition (PRIMARY Serial signal) | ✅ D-302 SIGNED — owned in `common/rung`; prior still-active & <50% paid down at new funding, OR ≤45-day gap (`RAPID_REUP_MAX_GAP_DAYS`) |
| Stress-event source (NSF / default note) | ✅ D-301 SIGNED — v1 = `has_default_note` + burden + worsening-terms; NSF deferred to feed/S7 (FU-301) |
| Position count for Serial | ✅ `active_position_cnt` (clock) + Position-field (`Positions__c`, 0%-populated per C-012) — B.4 v1 = rapid_reup + active_position_cnt |
| D-301…D-306 signed | ✅ 2026-06-01 — see DECISIONS C-017 |

Scaffolding of `common/rung` + `common/eventlog` pure functions + schemas can proceed in parallel; the prod build waits on the decisions.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate Appendix B (B.1–B.5) + Framework §4 as an explicit, ordered rule list: the Step-0 lifecycle gate (4 routes + default sub-typing), the 5-rung waterfall (first-match-wins + stress override), the Unclassified rule, and the output object fields with their exact source signals and the threshold constants each uses (already seeded in `constants.Thresholds`).
2. **Design**
   - **`common/rung/` pure module** (no Spark at import — tier-1 testable, mirrors `common/clock` / `common/identity`):
     - `lifecycle.py`: `default_subtype(...)`, `is_dormant(time_since_last_active, median_gap, book_median)`, `is_new_establishing(...)`, `lifecycle_state(...)` → the Step-0 gate returning the route.
     - `waterfall.py`: `is_distressed(...)`, `is_serial(...)`, `is_disciplined(...)`, `is_growth(...)`, `is_graduate(...)`, and `rung_of(signals)` applying first-match-wins + stress-override-pulls-down.
     - `confidence.py`: deterministic `confidence(signals, missing_signals)` + `direction_of_travel(prev_signals, curr_signals)` (climbing / holding / sliding).
     - `classify.py`: `classify_merchant(signals) -> {lifecycle_state, rung, confidence, missing_signals[], direction_of_travel}` — composes gate → waterfall → output object; the single pure entry point.
   - **`common/eventlog/` pure module**: `events.py` — event constructors (`classification_event`, `transition_event`) + the event schema; append-only, no mutation. Pure builders; the Spark writer lives in the transform.
   - **Spark transform** `transform/gold_rung.py`: reads `gold.merchant_clock_current` (+ `gold.merchants`, `gold.deals` renewal chain for prior-clean-renewal); applies the pure `classify_merchant` as UDFs/columns; stamps `classify_run_date`; writes the rung output + (diffing against the prior run) the transition events into the event log.
   - **Layer/shape decision (D-30x → likely a new D for output shape):** recommend **separate point-in-time `gold.merchant_rung`** keyed `(merchant_id, classify_run_date)`, append-only with a `merchant_rung_current` view (mirrors the S2 clock pattern — auditable, no destructive overwrite), and an append-only `gold.merchant_event_log`. Confirm.
   - **State machine** = the diff between consecutive `classify_run_date`s; no separate mutable state table (the point-in-time table *is* the history; `_current` is the live read).
3. **Definition of Ready** — D-301…D-30x signed; checklist in tracker.
4. **Build**
   - `common/rung/` + `common/eventlog/` pure functions (shared components; reserved homes already exist).
   - `common/schemas/gold.py`: `merchant_rung_schema()`, `event_log_schema()`; DQ columns (`missing_signals`, `confidence`, `lifecycle_state`, `rung`, `direction_of_travel`, `classify_run_date`).
   - `common/constants.py`: reuse the **already-seeded** `Thresholds` (BURDEN_DISTRESS_CEILING=0.30, BURDEN_SERIAL_BAND=(0.15,0.30), DISCIPLINED_BURDEN_MAX=0.15, DISCIPLINED_RENEWAL_PAYDOWN_MIN=0.50, DORMANCY_MULTIPLIER=2.0, SERIAL_POSITION_MIN=2, DEFAULT_RENEWAL_PAYDOWN=0.55) — **no new duplicate constants** (Rule 3); add only `RungState` / `LifecycleState` / `DirectionOfTravel` enums + any event-type constants.
   - `transform/gold_rung.py`.
   - DQ: every active merchant gets a rung or is counted Unclassified; lifecycle_state ∈ enum; rung ∈ {1..5, null}; confidence ∈ [0,1]; `missing_signals` populated whenever rung is null-by-missing; gated merchants have null rung + a lifecycle route.
5. **Test** — tier-1 (pure gate + waterfall + confidence + direction-of-travel on hand-worked signal vectors incl. the four merchants; first-match-wins ordering; stress-override-pulls-down; Unclassified when signals missing; dormancy multiplier; default sub-typing incl. `unknown`→do-not-fund) + tier-2 (build on `gold_test`, reconcile whole-book coverage, quantify the Unclassified pile, event-log append integrity, four-merchant expected labels). Full suite after each build piece.
6. **Review** — self-review + `code-review`; verify the four B.5 outcomes against the real merchant screens; confirm **no spine recompute** leaked into the classifier and **no SF stored balances** are read (only S2 clock outputs).
7. **Documentation** — rung/lifecycle/event-log field docs; update tracker, SHARED_COMPONENTS (`common/rung`, `common/eventlog`), runbook (daily classify job + queue read), DECISIONS (D-301…D-30x).
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` only on your approval** (Rule 5). Daily classify job is **defined** as code but scheduling/activation is gated.

## 4. Shared components created/changed

- **New** `common/rung/`: `lifecycle.py`, `waterfall.py`, `confidence.py`, `classify.py` (all pure, Spark-free at import — tier-1 testable; the home `src/common/rung` was reserved in the tracker).
- **New** `common/eventlog/`: `events.py` (pure append-only event builders + event schema; the home `src/common/eventlog` was reserved).
- **Changed** `common/schemas/gold.py`: `merchant_rung_schema()`, `event_log_schema()`.
- **Changed** `common/field_maps.py`: rung/lifecycle/event field maps (source = "Rung classifier (Appendix B) — derived from clock signals, NOT SF").
- **Changed** `common/constants.py`: `LifecycleState` / `RungState` / `DirectionOfTravel` / `EventType` enums + `GoldTable.MERCHANT_RUNG` / `MERCHANT_EVENT_LOG` (reuse existing `Thresholds` values — no new numeric duplicates).
- **Changed** `common/dq`: rung DQ predicates (coverage, enum domains, confidence bounds, missing_signals presence).
- **Reuse:** `io.guards` (no-surface), `dq.predicates`, the four-merchant fixtures, the entire S2 clock output (read-only).

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local):** pure classification logic — the Step-0 gate routes (Defaulted sub-types incl. `unknown`→do-not-fund; dormancy at exactly `2 × median`; new/establishing stays clock-running not Disciplined); the waterfall first-match-wins ordering; **stress override pulls down** (a Serial-looking merchant with NSF/burden>0.30 lands Distressed); Disciplined AND-conditions all required; Growth requires rising-advance + **falling relative** burden; Unclassified when a required signal is missing (→ `missing_signals[]`); `confidence` monotonic in signal completeness; `direction_of_travel` climbing/holding/sliding from prev→curr; schema **== contract**; event builders produce append-only, well-keyed rows; no `_sf_stored_*` referenced.
- **Tier-2 (Databricks `gold_test`):** build rung + event-log outputs; **reconciliations** — (a) **whole-book coverage**: every merchant in `gold.merchant_clock_current` gets exactly one row in `merchant_rung` for the run; (b) **Unclassified pile quantified** (count + top missing_signals) and is an explicit bucket, not silent; (c) lifecycle_state ∈ enum 100%, rung ∈ {1..5,null} 100%, confidence ∈ [0,1] 100%; (d) gated merchants (Defaulted/Dormant/New) have null rung + a route; (e) **state-machine**: re-run for a second `classify_run_date` and assert transitions are detected + event rows appended (no mutation of prior run); (f) event-log append integrity (keys unique per (merchant, event, ts); prior runs untouched); (g) no-surface guard clean.
- **Four-merchant scenario (carry from S0/S1/S2 — B.5):** **Starr** → Defaulted / `default_subtype=unknown` → do-not-fund + review (NOT a rung); **One Big Promotion** → Dormant → win-back; **Tom Snell** → New/establishing → healthy clock-running, **not yet Disciplined**; **Wolf** → Active → `rapid_reup_flag` → **Serial** → renewal-vs-buyout eval (and NOT Distressed, given payment health). Hand-build the signal vectors as fixtures and assert the full output object.

## 6. Data contracts touched

- **Reads:** `mca_mri.gold.merchant_clock_current` (closure, paydown, eligibility, burden, active_position_cnt, balance_source), `gold.merchants` (identity/profile, first_funded_date, renewal chain `is_renewal_of`), `gold.deals` (per-position terms, factor trajectory, `rapid_reup_flag` source). Data Contract xlsx governs the rung/lifecycle/event field names + audiences. **No SF stored balances** (S2 already isolated `_sf_stored_*`).
- **Writes:** `gold.merchant_rung` (point-in-time, `(merchant_id, classify_run_date)`) + `gold.merchant_rung_current` view; `gold.merchant_event_log` (append-only) (+ `gold_test` mirrors). Shape per the D-30x output decision.
- **Stable interface:** `lifecycle_state`, `rung`, `confidence`, `direction_of_travel`, `missing_signals[]`, and the event stream become the inputs to **S4 activation + Book Health** (reverse-ETL these labels; queue depth analytics), **S5 offer-engine** suitability gating, and **S6 prediction** (event log = learning data). Classification stability matters: a flapping rung churns the floor queue.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Over-classifying** — force-fitting a rung when signals are missing | Explicit **Unclassified** bucket + `missing_signals[]`; tier-2 quantifies the pile; never silently default to a rung (Framework §4 "honest, explicit unclassified pile") |
| **Stress override missed** → a distressed merchant labeled Serial/Disciplined | Waterfall applies stress-override **after** first-match (pulls down to Distressed); explicit tier-1 test for a Serial-looking + NSF/burden case |
| **`rapid_reup_flag` ownership unclear** (Rule 3) | Single owner decided in D-302; if derived upstream, S3 only reads it; if here, it lives once in `common` and S2/S3 share it |
| **"Prior clean renewal" depends on the renewal chain** (503 `renewal_unlinkable` from S1) | D-303: unlinkable renewals → treat as missing-signal (Unclassified-leaning) not as "no prior renewal"; never assert Disciplined on incomplete history |
| **Dormancy needs renewal-gap history** many merchants lack | Fall back to `2 × book-median` when no merchant history (B.2); flag the fallback in `missing_signals` |
| **Default sub-type often undetermined** | `unknown` → do-not-fund + `default_subtype=unknown` flag (Starr) — the honest, safe route; never guess true-default vs clawback |
| **Rung flapping** across daily runs churns the queue | Point-in-time history + `direction_of_travel` smooths reads; consider hysteresis only if tier-2 shows churn (defer unless observed) |
| **Confidence mistaken for an ML probability** | It's a deterministic rules-completeness score; documented as such; ML is S6 |
| **Recomputing the spine** in the classifier | S3 reads clock outputs only; review gate asserts no balance/eligibility math in `common/rung`; clock stays the single owner (CLAUDE.md §2.1) |
| Building S4+ early | S3 produces labels + a queue *view*; reverse-ETL, dashboards, offers, comms stay later sprints |

## 8. Decisions (D-301…D-306 — SIGNED 2026-06-01, see DECISIONS C-017)

- **D-301 — Stress-event source for Distressed. SIGNED.** No servicing/bank feed today (confirmed). **v1 stress = `has_default_note` + `burden_ratio > 0.30` + worsening-terms.** NSF / returned-payment is a **deferred signal** added when a feed lands → **FU-301** (Framework to absorb it + the other statement-derived signals).
- **D-302 — `rapid_reup_flag` ownership + definition. SIGNED.** Nothing computes it upstream today → **owned in `common/rung`**. **Definition:** TRUE when a new same-merchant advance funds while a prior position is still `active` AND the prior's `est_paydown_pct` (S2 clock, evaluated at the new `funded_date`) **< 50%**; **fallback** when prior paydown can't be computed: consecutive `funded_date`s **≤ 45 days** apart. Paydown-based is PRIMARY (more honest than raw days). Constant `RAPID_REUP_MAX_GAP_DAYS = 45` (calibratable once the book's gap distribution is seen). Richer concurrent-position detection (other funders' ACH debits) is **S7 statement parsing** — B.4 staging.
- **D-303 — "≥1 prior clean renewal" + the 503 `renewal_unlinkable`. SIGNED (revised).** **Trust `Type=Renewal` as a true renewal** (CLAUDE.md §2.5 — `Type` is reliable). The 503 unlinkable renewals are a **data-linkage gap, not evidence the renewal didn't happen** → counted as real renewals, flagged `renewal_chain_incomplete`, tracked + resolved forward (**FU-302**). **No merchant is demoted to Unclassified for a linking gap.** "Clean" prior renewal = where the chain *is* linked, the prior deal reached `closed_clean` (not `closed_default`) with healthy paydown; where unlinked, lean on the merchant's current clean signals + the incomplete flag rather than asserting or denying.
- **D-304 — Output shape/placement. SIGNED.** **Separate point-in-time `gold.merchant_rung`** keyed `(merchant_id, classify_run_date)`, append-only + `merchant_rung_current` view, and an append-only `gold.merchant_event_log` (mirrors the S2 clock pattern).
- **D-305 — Event-log scope for v1. SIGNED.** v1 emits **classification + transition** events in **one wide append-only table** keyed `(merchant_id, event_type, event_ts)`; touch/comms/offer events are added by S4/S5/S8 writing to the same log.
- **D-306 — Confidence scoring. SIGNED.** Deterministic, **borderline-driven**: confidence is high when a merchant's signals sit comfortably inside their rung's band and falls as values approach a threshold boundary. **Missing data does NOT lower confidence** — extend benefit of the doubt, treat the merchant as good and keep advisory comms flowing until a signal says otherwise. Missing *key* signals still route to **Unclassified** / `missing_signals[]`, but a *classified* merchant is never penalized for absent peripheral data. Exact formula: `confidence = min over the rung's decisive thresholds of a margin function (1.0 deep inside the band → 0.5 at the boundary)`, with absent non-key signals omitted from the calc (not scored as 0).

### 8b. Deferred signals — upgrade path (document for the Framework, FU-301)

These signals are **not available in v1** (no servicing/bank feed; no statement parsing yet) and the classifier runs without them. When the servicing feed / **S7 Statement Analyst** lands, fold them in — each strengthens an existing rule, none changes the rung definitions:

| Signal | Source when available | Strengthens |
|---|---|---|
| **NSF / returned ACH** | servicing/bank feed | Distressed stress condition (D-301) — adds the real-time payment-failure trip |
| **Concurrent positions (other funders)** | S7 bank-statement parsing (other lenders' debits) | Serial position detection (B.4) — beyond rapid-re-up + our own `active_position_cnt` |
| **True deposits / revenue** | S7 statement parsing / feed | `burden_ratio` denominator (currently null+flagged) → real burden, sharpens Distressed + Growth |
| **Real payment cadence / on-schedule** | servicing feed | `closure_status` `actual` path + "clean payments" evidence for Disciplined |

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/rung/` implements the Step-0 gate + 5-rung waterfall + Unclassified rule **literally** per Appendix B; first-match-wins + stress-override + AND/OR conditions verified on hand-worked vectors — *tier-1 logic tests*.
- [ ] `common/eventlog/` emits append-only, well-keyed classification + transition events — *tier-1 + tier-2 append-integrity tests*.
- [ ] **Whole book classified**: every merchant in `gold.merchant_clock_current` gets exactly one `merchant_rung` row per `classify_run_date` — *coverage reconciliation*.
- [ ] **Unclassified pile quantified** (count + top `missing_signals`) and surfaced as an explicit bucket — *tier-2 rollup* (roadmap exit criterion).
- [ ] lifecycle_state ∈ enum, rung ∈ {1..5, null}, confidence ∈ [0,1] for **100%** of rows; gated merchants have null rung + a route — *enum/bounds DQ test*.
- [ ] **State machine**: a second-day re-run detects lifecycle/rung transitions and appends events without mutating the prior run — *tier-2 two-run test*.
- [ ] **No spine recompute** and **no SF stored balances** in the classifier (reads S2 clock outputs only) — *review gate + no-surface test*.
- [ ] Four-merchant labels match B.5: Starr→Defaulted/unknown→do-not-fund; OBP→Dormant→win-back; Snell→New/establishing (not Disciplined); Wolf→Serial — *scenario test + manual spot-check vs real screens*.
- [ ] Daily classify job **defined as code** (DAB); scheduling/activation gated on approval — *job definition in repo*.
- [ ] Unity Catalog governs the new rung + event-log tables with lineage from `gold.merchant_clock` — *manual UC check + tracker note*.
- [ ] Tier-1 + tier-2 suites green; results logged — *suite output in tracker*.
