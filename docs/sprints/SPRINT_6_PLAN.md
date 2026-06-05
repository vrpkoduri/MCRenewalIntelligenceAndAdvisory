# Sprint 6 Plan — Prediction Models

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture:
**Prediction** (`CLAUDE.md` §3, Framework §11.2). Goal (Build Plan §6, Sprint 6): add the
**demand-clock and defection signals** on top of the validated feature table — *when will this
merchant next need capital, and are they slipping away?* — using a **named, explainable,
adopted toolkit**, with **uncertainty surfaced** so advice is framed honestly.

**First ML sprint. We ADOPT, we do not build** (CLAUDE.md §4): PyMC-Marketing (BG/NBD +
Gamma-Gamma + CLV) and lifelines (Cox PH + Kaplan-Meier). Models are part of the deterministic,
auditable spine (MLflow-versioned, inference logged) — NOT the agentic layer (S7). The toolkit
is chosen for the **sparse-data case** (Framework §11.2): BTYD works "from minimal data" and
Cox treats not-yet-renewed merchants as **censored, not missing**. Still **no comms** (S8), **no
agents** (S7), **no merchant app** (S9+). Reads S1–S4 gold + the event log; writes prediction
fields back to gold. Distressed timing stays **signal-driven, not model-driven** (Build Plan §6).

---

## 1. Objective & scope

**Objective:** write the Merchant-Gold **prediction outputs** for every merchant —
`rfm_recency/frequency/T/monetary`, `p_alive`, `p_defection`, `predicted_next_event_date`,
`predicted_clv`, `prediction_confidence` — and let the **daily queue prioritize by them**,
with confidence (posterior width) governing how firmly advice is framed. Deliverable (Build
Plan §6 DoD): models **trained on real renewal history**; **backtest produces sane, explainable
results**; `prediction_confidence` surfaced and used.

**In scope (Build Plan §6 + Framework §11.2 + Data Contract "Prediction Mapping"):**

- **RFM feature derivation** (`common/prediction`, pure/deterministic): from the deal history +
  event log, per the contract — `rfm_frequency` (repeat-advance count), `rfm_recency` (time of
  last advance since first), `rfm_T` (observation-window age), `rfm_monetary` (avg advance
  value). Tier-1 testable; the single source the models consume.
- **BTYD / CLV (PyMC-Marketing):** BG/NBD → `p_alive` (→ `p_defection = 1 − p_alive`, adjusted)
  + expected next-event count; Gamma-Gamma + CLV → `predicted_clv` (NPV over a configured
  horizon + discount rate, D-606). **Hierarchical / book-level priors** so thin-history
  merchants borrow strength (D-603).
- **Survival (lifelines):** Cox PH → `predicted_next_event_date` (time-to-next-advance) with
  covariates, treating not-yet-renewed merchants as **censored**; Kaplan-Meier baseline curve
  per cohort/rung. Covariate set per D-607 (drop covariates that are null book-wide in v1, e.g.
  `burden_ratio`).
- **`prediction_confidence`** = Bayesian posterior width / model uncertainty (D-603) — high for
  rich-history merchants, low (wide) for thin; an explicit **`insufficient_history`** bucket
  (mirrors the Unclassified pile) where only book-level priors apply. Honesty constraint.
- **MLflow + inference logging** (D-605): experiments/params/metrics tracked in MLflow; the
  fitted models registered + versioned; **batch inference** writes the prediction fields to
  gold; inference rows double as **event-log `prediction` events + audit** (D-305 reserved the
  type). Real-time Model Serving endpoint **deferred** unless needed.
- **Queue integration:** the S4 daily queue + state machine consume `p_defection` /
  `predicted_next_event_date` (in-market-now / win-back triggers). Prediction *refines* the
  ordering; it does not replace the deterministic gates.
- Tier-1 (RFM/labeling math) + tier-2 (fit + **backtest** + write) tests; UC governance;
  the four-merchant scenarios sanity-checked.

**Out of scope (do NOT build):**

- **No new feature engineering beyond the contract** (Build Plan §6) — RFM + the listed
  covariates only.
- **Distressed timing stays signal-driven** — the rung classifier (S3) owns distress; models do
  not drive it.
- **No hand-built models** — adopt PyMC-Marketing + lifelines (CLAUDE.md §4).
- **No comms / no agents / no app** (S7/S8/S9+); **no real-time serving endpoint** in v1 (batch).
- **No spine recompute** — reads S1–S4 gold + event log; never recomputes clock/rung/identity;
  never reads SF `_sf_stored_*`.

## 2. Definition of Ready

| Gate | State |
|---|---|
| S1–S4 in prod (deal history, clock, rung, activation, event log) | ✅ 2026-06-02 |
| Renewal history exists (the models' training signal) | ✅ S1: 1,854 renewals → 1,351 linked chains; event log carries rung transitions |
| Prediction Mapping (field → model) | ✅ transcribed (Data Contract sheet) |
| **Data-readiness read** — repeat-merchant count + deal-count distribution (sizes the predictable vs `insufficient_history` population) | ⏳ **DoR spike** (cheap warehouse query at build start) |
| PyMC-Marketing + lifelines available on the Databricks compute (+ MLflow) | ⏳ **D-602** — add libs / confirm runtime |
| D-601…D-609 signed | ⏳ **awaiting sign-off (this plan)** |

Offline `common/prediction` RFM/labeling (pure) + schemas + tier-1 can be built in parallel;
the model fitting/backtest waits on D-602 (libs) + the decisions and runs on Databricks.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate the Prediction Mapping as explicit feature/label definitions
   (RFM grain; the survival `duration`/`event_observed`; the Cox covariate set; the CLV horizon
   + discount config); define the `insufficient_history` rule + the confidence semantics.
2. **Design**
   - **DoR data-readiness spike** — deal-count distribution + repeat-merchant count (sizes the
     predictable population); record in the tracker (mirrors the S5 spike).
   - **`common/prediction/` pure module** (no Spark/ML at import — tier-1 testable):
     `rfm.py` (`rfm_features(deals, today)` → recency/frequency/T/monetary), `survival.py`
     (`duration_event(merchant)` → Cox `duration` + `event_observed` with censoring),
     `confidence.py` (posterior-width → `prediction_confidence`; `insufficient_history` flag).
   - **`transform/gold_predictions.py`** (Databricks, PyMC-Marketing + lifelines): assemble the
     RFM/survival inputs → fit (hierarchical BG/NBD + Gamma-Gamma + Cox) → batch-infer → write
     point-in-time `gold.merchant_predictions` (D-604) → emit `prediction` events → log to MLflow.
   - **Layer/shape (D-604):** separate point-in-time `gold.merchant_predictions` keyed
     `(merchant_id, prediction_run_date)`, append-only + `_current` view (mirrors S2/S3/S4/S5).
3. **Definition of Ready** — D-601…D-609 signed; libs confirmed; data-readiness spike done.
4. **Build** — `common/prediction/` (pure); `constants` (CLV horizon/discount config,
   `GoldTable.MERCHANT_PREDICTIONS`/`_CURRENT`, `EventType.PREDICTION`, insufficient-history
   threshold — reuse existing `Thresholds`); `schemas/gold.merchant_predictions_schema()`;
   `field_maps` (prediction field map); `transform/gold_predictions.py` (+ MLflow). DQ: every
   merchant gets a prediction row or an `insufficient_history` flag; `p_alive`/`p_defection` ∈
   [0,1]; `prediction_confidence` ∈ [0,1]; dates sane.
5. **Test** — tier-1 (RFM math; duration/event censoring labels incl. single-deal → censored;
   confidence monotonic in history; schema == contract; no `_sf_stored_*`) + tier-2
   (`gold_test`: fit on real history; **backtest** — time-split train/holdout, predicted vs
   actual next-events, KM baseline per rung; ranges/bounds; `insufficient_history` pile
   quantified; prediction-event append integrity; no-surface). Backtest acceptance = "sane +
   explainable" (calibration + ordering, not a hard accuracy bar).
6. **Review** — self-review + `code-review`; confirm **adopted libs not hand-rolled**, **no
   spine recompute**, **distress not model-driven**, confidence honestly surfaced.
7. **Documentation** — prediction field docs; the model/library decision; update tracker,
   SHARED_COMPONENTS (`common/prediction`), TESTING_FRAMEWORK, RUNBOOK (fit/infer job + MLflow),
   DECISIONS (D-601…D-609).
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` on approval**; the fit/infer schedule
   + any Model Serving endpoint are approval-gated (Rule 5).

## 4. Shared components created/changed

- **New** `common/prediction/`: `rfm.py`, `survival.py`, `confidence.py` (pure, no ML at import).
- **Changed** `common/schemas/gold.py`: `merchant_predictions_schema()`.
- **Changed** `common/field_maps.py`: prediction field map (source = "prediction — PyMC-Marketing /
  lifelines on RFM + survival inputs; NOT SF").
- **Changed** `common/constants.py`: `GoldTable.MERCHANT_PREDICTIONS`/`_CURRENT`,
  `EventType.PREDICTION`, CLV horizon/discount config, `INSUFFICIENT_HISTORY_MIN_EVENTS` — reuse
  existing `Thresholds`.
- **New (gated)** `transform/gold_predictions.py` (PyMC-Marketing + lifelines + MLflow).
- **Reuse:** the deal history / event log / rung / clock (read-only), `io.guards`, the
  four-merchant fixtures.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local, pure):** RFM features on hand-worked deal sets (single-deal → frequency 0;
  multi-deal → correct recency/frequency/T/monetary); survival `duration`/`event_observed`
  labeling incl. **censoring** for not-yet-renewed; `prediction_confidence` monotonic in history
  + `insufficient_history` flag; schema == contract; no `_sf_stored_*`.
- **Tier-2 (Databricks `gold_test`, ML):** fit hierarchical BG/NBD + Gamma-Gamma + Cox on real
  history; **backtest** (time-split: train to a cutoff, score next-event on the holdout; KM
  baseline per rung) → sane/explainable (calibration buckets + ordering); `p_alive`/`p_defection`
  ∈ [0,1], dates sane; **`insufficient_history` pile quantified**; prediction-event append
  integrity; no-surface; MLflow run logged.
- **Four-merchant sanity:** Wolf (serial, rapid re-up) → high near-term event probability;
  One Big Promotion (dormant since 2020) → low `p_alive` / high `p_defection`; Tom Snell (1
  fresh deal) → `insufficient_history` / wide confidence; Starr (defaulted) → handled by
  lifecycle, prediction framed cautiously.

## 6. Data contracts touched

- **Reads:** `gold.deals` (advance history → RFM, monetary, intervals), `gold.merchant_clock_current`
  (tenure, eligibility, burden — null v1), `gold.merchant_rung_current` (rung, factor_trend,
  payment_health, direction), `gold.merchant_event_log` (transition history → multi-state, later),
  `gold.merchants` (industry_vertical, first_funded_date). **No SF stored balances.**
- **Writes:** `gold.merchant_predictions` (+ `_current` view); `prediction` events in
  `gold.merchant_event_log`; MLflow experiment/model registry.
- **Stable interface:** the prediction fields feed the S4 queue/state machine (in-market /
  win-back timing), the deferred **Book Health** metrics (aggregate LTV, defection rate +
  destination), and S8 advisory framing (`prediction_confidence` → soft vs firm). The contract
  shape `{rung, confidence, predicted_event}` is unchanged downstream (Framework §5.4 — the model
  upgrade is invisible to consumers).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Sparse / thin post-funding history** (many single-deal, dormant merchants) | The toolkit is chosen for it (Framework §11.2): BTYD from minimal data + Cox censoring; **hierarchical/book-level priors** pool strength; an explicit **`insufficient_history`** bucket + **wide `prediction_confidence`** rather than false precision; quantify the predictable share (DoR spike) |
| **Over-trusting uncertain predictions** | `prediction_confidence` (posterior width) is first-class and governs advice framing (soft vs firm); the queue uses predictions to *refine* ordering, never to override the deterministic gates |
| **Covariates null book-wide in v1** (`burden_ratio`, real revenue) | Drop/flag null-everywhere covariates in the Cox set (D-607); they fold in with the bank feed (FU-301); document the v1 covariate set |
| **Model-driven distress creep** | Out of scope — distress stays signal-driven (S3); review-gate asserts predictions don't feed the distress rung |
| **Hand-rolling models** | Adopt PyMC-Marketing + lifelines (CLAUDE.md §4); MRI owns only feature/label derivation + the orchestration |
| **Backtest leakage / look-ahead** | Time-split (train to a cutoff, score the future holdout); censor correctly; KM baseline per cohort; acceptance = calibration + ordering, not a vanity accuracy number |
| **New heavy dependencies** (PyMC sampling cost) | D-602 confirms libs/runtime; batch fit on a sized cluster/serverless; Model Serving endpoint deferred (batch inference to gold is enough for the queue) |
| **Reproducibility / audit** (regulated, advice-giving) | MLflow tracks params/metrics/model version; inference logged as events; `model_version` stamped on outputs (Event Log contract) |

## 8. Open decisions requiring your sign-off (D-601…D-609)

- **D-601 — RFM feature definitions + grain.** *Rec:* one row/merchant; an "event" = a funded
  advance (`gold.deals.funded_date`); `frequency` = repeat advances (deal_count − 1); `recency`
  = last − first advance; `T` = run_date − first; `monetary` = avg `funded_amount`. Confirm.
- **D-602 — Model libraries + runtime.** *Rec:* adopt **PyMC-Marketing** (BG/NBD, Gamma-Gamma,
  CLV) + **lifelines** (Cox PH, KM) on a Databricks ML runtime + **MLflow**; add to
  `requirements`. Confirm the libs + that fitting runs on Databricks (not tier-1 local).
- **D-603 — Sparse-data handling + confidence.** *Rec:* **hierarchical/book-level priors**
  (pool thin merchants), Cox **censoring** for not-yet-renewed, an explicit
  **`insufficient_history`** flag when repeat events < `INSUFFICIENT_HISTORY_MIN_EVENTS` (rec **1**),
  and `prediction_confidence` = normalized posterior width. Confirm the approach + threshold.
- **D-604 — Output shape/placement.** *Rec:* separate point-in-time `gold.merchant_predictions`
  keyed `(merchant_id, prediction_run_date)`, append-only + `_current` (mirrors S2–S5). Confirm.
- **D-605 — MLflow + serving scope.** *Rec:* MLflow tracking + model registry + **batch
  inference** to gold + `prediction` events; **defer the real-time Model Serving endpoint** to
  when a live consumer needs it. Confirm (batch-only v1).
- **D-606 — CLV horizon + discount rate.** *Rec:* `future_t` = **12 months**, `discount_rate` =
  a configced annual rate (e.g. **12%**), both calibratable constants. Confirm the values.
- **D-607 — Cox covariates + event definition.** *Rec:* `event_observed` = took capital
  (renewed) vs censored (not yet); `duration` = interval to next advance (else tenure to
  censor); covariates = `factor_trend`, `active_position_cnt`, `payment_health`,
  `industry_vertical` (**drop `burden_ratio`** — null book-wide v1, FU-301). Confirm.
- **D-608 — Backtest method + acceptance.** *Rec:* time-split train/holdout; predicted-vs-actual
  next-event calibration + ordering; KM baseline per rung; accept on "sane + explainable", not a
  fixed accuracy bar. Confirm.
- **D-609 — Prediction event + queue integration.** *Rec:* emit `prediction` events; S4 queue
  refines ordering by `p_defection` + `predicted_next_event_date` (never overrides the gates).
  Confirm.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/prediction/` derives RFM + survival labels (with censoring) **per the contract** —
  *tier-1 logic tests + four merchants*.
- [ ] Models **fit on real renewal history** (PyMC-Marketing BG/NBD+Gamma-Gamma+CLV; lifelines
  Cox+KM); **backtest sane + explainable** — *tier-2 backtest*.
- [ ] **Every merchant** gets a prediction row or an `insufficient_history` flag; `p_alive`/
  `p_defection`/`prediction_confidence` ∈ [0,1]; dates sane — *coverage + bounds DQ*.
- [ ] **`insufficient_history` pile quantified** (count + share) — *tier-2 rollup*.
- [ ] **No spine recompute, distress not model-driven, no `_sf_stored_*`, models adopted not
  built** — *review gate + no-surface test*.
- [ ] **Confidence surfaced + used** (posterior width → soft/firm framing hook) — *field present + documented*.
- [ ] Prediction outputs land in gold; the queue can prioritize by them; `prediction` events
  appended without mutating prior runs — *tier-2 append + queue read*.
- [ ] MLflow run(s) logged with params/metrics/`model_version`; fit/infer job **defined as code**
  (DAB), scheduling gated — *MLflow + repo*.
- [ ] Unity Catalog governs `gold.merchant_predictions` with lineage — *UC check + tracker note*.
- [ ] Tier-1 + tier-2 suites green; results logged — *suite output in tracker*.
