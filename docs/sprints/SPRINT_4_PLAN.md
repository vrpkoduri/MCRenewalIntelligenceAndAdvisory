# Sprint 4 Plan — Activation to Salesforce + Book Health

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture block 5
(`CLAUDE.md` §3): *Activation — reverse ETL to Salesforce + Book Health analytics*. Goal
(Build Plan §6, Sprint 4): **put the intelligence in front of reps and management** — turn
the S3 rung/lifecycle labels + the S2 clock into an operational **state + named play + next
action** per merchant, surface a **daily queue**, push the rep-facing fields back to
**Salesforce**, and stand up the read-only **Book Health** scoreboard over gold + the
event-log transition history.

**This is the first sprint that writes back to Salesforce.** Reverse-ETL is outward-facing
and hard to reverse — it is **gated** (Rule 5/6): we build + verify the activation dataset
and the write-back field map, dry-run to a **Salesforce sandbox / `gold_test`** first, and
the **prod Salesforce write requires explicit approval**. Still **no ML** (S6), **no
outbound merchant comms** (S8), **no merchant app** (S9+). Everything deterministic,
auditable, reconciles.

---

## 1. Objective & scope

**Objective:** make every merchant's standing **actionable on the floor** and **visible to
management**. Two deliverables (Build Plan §6 DoD): (a) reps see each merchant's **rung +
direction + current state + next action + play** inside Salesforce, consuming a **daily
queue**; (b) management sees the **book-health scoreboard** with metrics that trend.

**In scope (Build Plan §6 + Framework 5.8 + Data Contract "rung & state" / "Book Health
Metrics" / "Event Log"):**

- **State machine** (`common/activation`, deterministic): derive `current_state` ∈
  {`clock-running`, `approaching`, `in-market`, `renewed`, `lost-winback`} from the S2 clock
  (`is_eligible_now`, `est_renewal_eligible_date`), the S3 `lifecycle_state` / `rung` /
  `direction_of_travel`, and recency — runs over the active book; gated lifecycle states map
  to their operational state (dormant→lost-winback, etc.). **Detect state transitions** vs
  the prior run and emit `play_fired` / `rung_transition` events into the existing
  `gold.merchant_event_log` (extending S3's append-only log — D-305 reserved the type).
- **Named plays + SLA + next actions** (`common/activation`): from (rung × current_state)
  assign `active_play`, `play_owner`, `play_sla_due`, and the grounded, templated
  `next_tactical_action` (floor script) + `next_strategic_nudge` (rung-climbing move). These
  are **internal rep guidance, not merchant comms** (comms is S8; the offer/advice
  compliance gate is S8). Strings are deterministic templates grounded only in computed
  numbers (honesty constraint, CLAUDE.md §2.3) — never invented.
- **Daily queue (read surface)**: `gold.daily_queue` view ordering the active book by
  `direction_of_travel` (sliding first) → `current_state` (in-market/approaching) →
  `play_sla_due` → `confidence` — the floor consumption surface (carried from the S3 plan §1).
- **Serving layer (read-only) — D-403 SIGNED (C-018): NO Salesforce write in S4.** An
  **activation projection** (`gold.merchant_activation`, point-in-time + `_current` view) of
  the floor-facing fields is the floor's read surface (queried directly or via the
  `daily_queue` view). A **write-back field map** (gold field → SF target object.field) is
  authored as documentation of the *future* delivery, and the governed **Salesforce write-back**
  (dedicated MRI fields only, sandbox-first, `allow_sf_write`-gated) + a Lakebase serving sync
  are **deferred to their own gated step → FU-401**. S4 ships the intelligence with zero
  outward-facing risk.
- **Portfolio Analytics / Book Health** (`transform/gold_book_health.py`, read-only): the
  three Framework 5.8 views as point-in-time gold tables (+ `_current` views) over the
  Merchant Gold table + the event-log transition history — **book health** (rung distribution
  + drift, default/restructure trend, renewal-capture rate), **renewal performance**
  (time-to-contact, play-SLA adherence), **leading indicators** (sliding count, approaching
  pipeline, concentration risk by funder/vertical/rung). v1 computes only the metrics whose
  **inputs exist today**; LTV/defection/offer-acceptance/value-to-ask are **deferred** (their
  inputs are S5/S6/S8 — explicit, honest, documented).
- Tier-1 + tier-2 tests; UC governance/lineage; reconciliation; the four-merchant scenarios
  carried through to their state + play.

**Out of scope (do NOT build):**

- **No merchant-facing surface** — the app is S9+ (no auth, no bank-linking, no merchant UI).
- **No automated outbound comms / no merchant messages** — S8. S4 produces internal rep
  guidance text only; it does not send anything to a merchant.
- **No compliance/disclosure gate, no offer_vs_advice classification of live comms** — S8
  (the gate is real architecture; S4 must not design around it, but does not build it).
- **No ML / predictions** — S6. Book Health v1 omits `predicted_clv` / `p_defection` /
  `predicted_next_event_date`-driven metrics until S6 writes them.
- **No Offer Engine fields** — S5. Book Health "offer acceptance" / `eligible_offer_types`
  metrics wait for S5.
- **No new rung/clock/identity logic** — S4 READS S1/S2/S3 gold outputs; it never recomputes
  the spine (CLAUDE.md §2.1) and never reads SF `_sf_stored_*`.
- **No rebuild of the floor's tool** — we deliver INTO Salesforce (their existing tool).

## 2. Definition of Ready

| Gate | State |
|---|---|
| S3 complete (`gold.merchant_rung` / `merchant_rung_current` + `gold.merchant_event_log` in prod, reconciled) | ✅ 2026-06-02 (`failures: []`) |
| S2 clock in prod (`merchant_clock_current`: `is_eligible_now`, `est_renewal_eligible_date`, `est_paydown_pct`) | ✅ |
| Data Contract "rung & state" + "Book Health Metrics" + "Event Log" sections | ✅ transcribed here (the field set + metric defs) |
| Event-log extensibility for new event types (`play_fired`, `rung_transition`, `state → touch`) | ✅ D-305 one-wide-table reserved S4 event types |
| Salesforce write-back target (object + dedicated MRI fields) + a sandbox to dry-run against | ⏳ **D-403** — needs your decision + SF-side field creation |
| `play_owner` source (rep assignment) | ⏳ **D-402** — `iso_rep` is a gap (FU-101); SF Opportunity/Account OwnerId is the candidate |
| Reverse-ETL mechanism (Lakebase mirror vs governed SF API write vs reverse-ETL tool) | ⏳ **D-403** |
| D-401…D-406 signed | ⏳ **awaiting sign-off (this plan)** |

Offline `common/activation` + `common/bookhealth` pure functions + schemas can be built in
parallel; the cloud build (and absolutely the SF write) waits on the decisions.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate, as explicit rules: the 5 `current_state` definitions +
   transition conditions (from clock/rung/lifecycle/recency); the (rung × state) → play
   matrix with SLA + next-action templates; the daily-queue ordering; the write-back field
   map (which Merchant-Gold fields, which SF target, audiences F/D only); the Book Health
   metric set with v1-available vs deferred inputs.
2. **Design**
   - **`common/activation/` pure module** (no Spark at import — tier-1 testable; mirrors
     `common/rung`): `state_machine.py` (`current_state(signals)`, `state_transition(prev,curr)`),
     `plays.py` (`active_play(rung, state)`, `play_sla(play)`, `next_actions(rung, state)` →
     templated strings, `play_owner(...)`). Pure, grounded, deterministic.
   - **`common/bookhealth/` pure module**: `metrics.py` — the metric definitions as pure
     functions over already-aggregated inputs (the Spark aggregation mirrors them — the
     `dq.predicates ↔ dq.rules` pattern); marks each metric available/deferred.
   - **`transform/gold_activation.py`** (Spark): read `merchant_rung_current` +
     `merchant_clock_current` + `gold.deals` (owner) → apply the pure state machine + plays →
     write **`gold.merchant_activation`** (point-in-time `(merchant_id, activation_run_date)`,
     append-only + `_current` view, mirrors S2/S3) → append `play_fired` / state-transition
     events to `gold.merchant_event_log` → (re)create the **`gold.daily_queue`** view.
   - **`transform/gold_book_health.py`** (Spark): read-only aggregation over
     `merchant_rung_current` / `merchant_clock_current` / `merchant_event_log` → write the
     point-in-time Book Health tables (+ `_current` views).
   - **`transform/reverse_etl_salesforce.py`** (gated): build the SF write-back payload from
     `merchant_activation_current`; a Lakebase sync for the serving mirror; the SF writer
     (dedicated MRI fields only) — **dry-run / sandbox first; prod write approval-gated**.
   - **Layer/shape (D-404):** separate point-in-time `gold.merchant_activation` + Book Health
     tables (+ `_current` views), append-only — mirrors the S2 clock / S3 rung pattern.
3. **Definition of Ready** — D-401…D-406 signed; checklist in the tracker.
4. **Build** — `common/activation` + `common/bookhealth` (shared, pure); `constants`
   (`CurrentState`, `Play`, SLA durations, `APPROACHING_WINDOW_DAYS`, `GoldTable` names — reuse
   existing `Thresholds`, no new duplicate numbers, Rule 3); `schemas/gold.py`
   (`merchant_activation_schema()`, `book_health_*_schema()`); `field_maps` (activation map +
   the **SF write-back map** + book-health maps); the transforms; the gated reverse-ETL writer.
   DQ: every active merchant gets a state + play; state ∈ enum; SLA present for every play;
   queue covers the active book; write-back touches only F/D-audience fields.
5. **Test** — tier-1 (pure state machine on hand-worked signal vectors incl. the four
   merchants → their state + play; transition detection; SLA assignment; next-action template
   grounding; book-health metric math; write-back map touches only floor/dual audiences +
   never an `_sf_stored_*` field) + tier-2 (`gold_test`: build activation + book-health,
   reconcile whole-book state coverage, event append integrity, queue ordering, book-health
   totals reconcile to the rung distribution; **reverse-ETL DRY-RUN** payload validates, no
   prod SF write). Full suite each cycle.
6. **Review** — self-review + `code-review`; verify the four merchants' state+play; confirm
   **no spine recompute**, **no `_sf_stored_*`**, **no merchant-facing comms**, and the SF
   writer touches only dedicated MRI fields.
7. **Documentation** — activation/book-health/queue field docs; SF write-back map; update
   tracker, SHARED_COMPONENTS (`common/activation`, `common/bookhealth`), TESTING_FRAMEWORK,
   RUNBOOK (activation job + book-health + the gated reverse-ETL), DECISIONS (D-401…D-406).
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` on approval**; the **Salesforce
   write is separately, explicitly approval-gated** and dry-run to a sandbox first (Rule 5/6).

## 4. Shared components created/changed

- **New** `common/activation/`: `state_machine.py`, `plays.py` (pure, Spark-free at import).
- **New** `common/bookhealth/`: `metrics.py` (pure metric definitions).
- **Changed** `common/schemas/gold.py`: `merchant_activation_schema()`, `book_health_*_schema()`.
- **Changed** `common/field_maps.py`: activation field map; the **Salesforce write-back map**
  (gold field → SF object.field; source = "activation — derived from S2/S3, NOT SF"); book-health maps.
- **Changed** `common/constants.py`: `CurrentState` / `Play` enums, SLA durations,
  `APPROACHING_WINDOW_DAYS`, `GoldTable.MERCHANT_ACTIVATION` / `DAILY_QUEUE` / `BOOK_HEALTH_*`,
  extend `EventType` (`play_fired`, `state_transition`/`rung_transition`, `touch` reserved) —
  **reuse existing `Thresholds`; no new numeric duplicates** (Rule 3).
- **Changed** `common/dq`: activation DQ predicates (state coverage, enum domains, SLA presence).
- **Reuse:** `io.guards` (no-surface), the S3 `merchant_rung` + event log, the S2 clock, the
  four-merchant fixtures.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local):** `current_state` for every (clock×rung×lifecycle) combination incl. the
  four merchants (Starr→lost-winback/do-not-fund-review; OBP→lost-winback/win-back play;
  Snell→clock-running/nurture; Wolf→in-market-or-approaching/serial renewal-vs-buyout play);
  state-transition detection prev→curr; play+SLA assignment per (rung×state); next-action
  templates grounded only in computed values; daily-queue ordering key; book-health metric
  math on hand-worked rollups; the SF write-back map includes only F/D-audience fields and no
  `_sf_stored_*`; schemas == contract.
- **Tier-2 (`gold_test`):** build `merchant_activation` + book-health + the queue; **recon**
  — whole-book state coverage (every active merchant has a state+play; gated have their
  operational state); enum/SLA DQ 100%; event append integrity (`play_fired`/transition rows
  appended, prior runs untouched); queue covers the active book and orders correctly;
  book-health rung-distribution totals reconcile to `merchant_rung_current`; **reverse-ETL
  dry-run** produces a valid SF payload (row count, field set, audience filter) **without
  writing to Salesforce**; no-surface guard clean.
- **Four-merchant scenario:** carried from S0–S3 to their S4 state + play + queue position.

## 6. Data contracts touched

- **Reads:** `gold.merchant_rung_current` (rung, lifecycle_state, direction_of_travel,
  confidence, route, default_subtype), `gold.merchant_clock_current` (is_eligible_now,
  est_renewal_eligible_date, est_paydown_pct, active_position_cnt), `gold.merchant_event_log`
  (transition history for Book Health), `gold.deals` (owner / iso_rep), `gold.merchants`
  (governing_state, industry for concentration). **No SF stored balances.**
- **Writes:** `gold.merchant_activation` (+ `_current`), `gold.daily_queue` (view),
  `gold.book_health_*` (+ `_current`), new event rows in `gold.merchant_event_log`; and —
  **gated** — a Lakebase serving mirror + a **Salesforce write-back** to dedicated MRI fields.
- **Stable interface:** `current_state`, `active_play`, `play_sla_due`, `next_tactical_action`,
  `next_strategic_nudge`, the queue, and the Book Health metrics feed the floor (Salesforce),
  management, and later S5 (offer suitability surfaced in the play) / S8 (comms consume the
  next-action + the offer/advice gate). The event log keeps accruing the learning/audit spine.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Reverse-ETL overwrites or corrupts Salesforce data** (irreversible, outward-facing) | Write ONLY to dedicated MRI fields (never SF's own); dry-run to a sandbox / `gold_test`; **prod SF write needs explicit approval + an `allow_sf_write` guard** (mirrors `allow_prod`); idempotent upsert keyed on a stable id |
| **Merchant-facing comms leak in via "next action" text** | next_tactical/strategic are INTERNAL rep guidance only; no send path built; comms + the offer/advice gate stay S8; review-gate asserts no delivery |
| **Book Health metrics imply data we don't have yet** (LTV, defection, offers) | v1 computes only metrics with existing inputs; deferred metrics are explicitly listed + omitted (not faked/zeroed); honesty constraint |
| **`play_owner` unavailable** (`iso_rep` is a gap, FU-101) | D-402: source owner from SF Opportunity/Account OwnerId (ingest) or leave null + `play_owner_is_missing` until a rep map lands — never fabricate accountability |
| **State/play flapping across runs churns the queue** | Reuse `direction_of_travel` smoothing; point-in-time history; consider hysteresis only if tier-2 shows churn (defer unless observed) — same stance as S3 |
| **Scope creep into S5–S8** (offers, comms, app) | Hard out-of-scope list (§1); S4 stops at internal activation + read-only analytics |
| **Recomputing the spine in activation** | S4 reads S1/S2/S3 gold outputs only; review-gate asserts no clock/rung math in `common/activation` |
| **Build-Plan vs signed-plan scope drift** (state machine + plays listed under S3) | **D-405** confirms S4 absorbs the state machine + plays + SLA that the S3 plan (C-017) deferred to activation |

## 8. Decisions (D-401…D-406 — SIGNED 2026-06-02, see DECISIONS C-018)

**D-403 resolved to the serving-layer-only path: NO Salesforce write in S4** (the governed SF
write-back is deferred to **FU-401**). The recommendations below were approved as written
(D-401 states + 30-day windows; D-402 play matrix + 2/5/10-day SLA tiers + owner from SF
OwnerId; D-404 point-in-time Book Health family + v1/deferred split; D-405 S4 owns the state
machine + plays; D-406 `daily_queue` view).

### 8a. Original recommendations (for the record)

- **D-401 — State machine definitions + thresholds.** Define `current_state` ∈
  {clock-running, approaching, in-market, renewed, lost-winback} and the transition rules.
  *Recommendation:* `in-market` = active & `is_eligible_now`; `approaching` = active & not
  eligible & `est_renewal_eligible_date` within **`APPROACHING_WINDOW_DAYS`** (recommend **30**,
  calibratable) of today; `clock-running` = active, paying down, not yet approaching;
  `renewed` = a new same-merchant advance funded within a recent window (reuse the S3 renewal
  signal); `lost-winback` = lifecycle dormant/defaulted (win-back or do-not-fund-review). Need
  sign-off on the states + the 30-day window.
- **D-402 — Named plays catalogue + SLA + `play_owner` source.** *Recommendation:* a small
  deterministic (rung × state) → play matrix (e.g. in-market-renewal, slide-intervention,
  win-back, new-establishing-nurture, distressed-stabilize, do-not-fund-review), each with an
  SLA (e.g. sliding/distressed = **2 business days**, in-market = **5**, others = **10**;
  calibratable constants) and templated next-actions grounded in the merchant's computed
  numbers. `play_owner` ← **SF Opportunity OwnerId** (ingest in S4) if available, else null +
  `play_owner_is_missing` (FU-101). Need sign-off on the play list + SLA numbers + owner source.
- **D-403 — Reverse-ETL mechanism + Salesforce target (headline).** *Recommendation:* build
  the **activation gold table + the SF write-back field map + a Lakebase serving mirror** now,
  implement the **Salesforce writer against a sandbox / `gold_test` first**, writing ONLY to
  **dedicated MRI fields** on the chosen SF object (recommend a small set on `Opportunity` or a
  custom `MRI__c` block — needs SF-side field creation), and **gate the prod SF write on
  explicit approval** (`allow_sf_write=True`, mirroring `allow_prod`). Need: the SF target
  object + confirmation MRI fields can be created + a sandbox, and whether S4 performs the SF
  write or stops at the Lakebase/gold serving layer for v1.
- **D-404 — Book Health output shape + v1 metric scope.** *Recommendation:* separate
  point-in-time `gold.book_health_*` tables keyed by `report_date` (+ `_current` views),
  append-only (mirrors the clock/rung pattern); v1 computes **rung distribution, rung drift
  (event log), default/restructure trend, sliding count, approaching pipeline, concentration
  risk, renewal-capture (partial)**; **defer** aggregate-LTV, defection rate/destination,
  offer-acceptance, value-to-ask, time-to-contact/play-SLA-adherence-by-comms until their
  inputs (S5/S6/S8) exist. Need sign-off on shape + the v1/deferred split.
- **D-405 — Scope boundary confirmation.** The Build Plan listed the state machine
  (current_state) + named plays + SLA under **Sprint 3's** in-scope; the signed S3 plan
  (C-017) deliberately built lifecycle_state + rung + direction + event log and **deferred
  activation to S4**. *Recommendation:* **S4 absorbs the state machine + plays + SLA + next
  actions** (they belong with activation). Confirm.
- **D-406 — Daily queue surface.** *Recommendation:* `gold.daily_queue` as a **view** over
  `merchant_activation_current` + `merchant_clock_current` ordered sliding-first → in-market/
  approaching → `play_sla_due` → confidence (carried from the S3 plan §1). Confirm.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/activation` implements the 5-state machine + (rung×state) plays + SLA + grounded
  next-actions **literally** per the agreed D-401/D-402 — *tier-1 logic tests + four merchants*.
- [ ] **Whole active book gets a state + play**; gated merchants carry their operational state;
  state ∈ enum, SLA present 100% — *coverage + enum/SLA DQ recon*.
- [ ] **Daily queue** orders the active book (sliding-first …) and covers it — *queue recon*.
- [ ] **State/play transitions** append `play_fired` / transition events without mutating prior
  runs — *event append-integrity test (two-run)*.
- [ ] **Book Health** v1 views populate and **reconcile** (rung-distribution totals ==
  `merchant_rung_current`); deferred metrics explicitly omitted, not faked — *tier-2 recon*.
- [ ] **Reverse-ETL**: write-back field map touches only F/D-audience fields and no
  `_sf_stored_*`; a **dry-run** produces a valid SF payload on `gold_test`/sandbox; **no prod
  Salesforce write without explicit approval** (`allow_sf_write`) — *map test + dry-run recon + gate*.
- [ ] **No spine recompute, no `_sf_stored_*`, no merchant-facing comms** in S4 — *review gate + no-surface test*.
- [ ] Four-merchant state+play match expectations (Starr→do-not-fund-review; OBP→win-back;
  Snell→nurture/clock-running; Wolf→serial renewal-vs-buyout) — *scenario test*.
- [ ] Activation + book-health + reverse-ETL jobs **defined as code** (DAB); scheduling +
  the SF write are approval-gated — *job definitions in repo*.
- [ ] Unity Catalog governs the new tables with lineage from `merchant_rung` / `merchant_clock`
  / `merchant_event_log` — *UC check + tracker note*.
- [ ] Tier-1 + tier-2 suites green; results logged — *suite output in tracker*.
