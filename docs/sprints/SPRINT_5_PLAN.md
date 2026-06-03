# Sprint 5 Plan — Offer Engine (Proactive)

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture: the
**Offer Engine** (`CLAUDE.md` §3, Framework §5.7) — *reuse the existing funder-criteria
dataset + routing engine, run proactively against the funded book*. Goal (Build Plan §6,
Sprint 5): answer **"what can we credibly offer this merchant right now?"** for every funded
merchant — `eligible_offer_types`, `matched_funders`, `max_sustainable_advance` — with the
**suitability** gate wired (engine *proposes*, advisory/compliance *dispose*).

**This sprint reuses existing IP — it does NOT build a routing engine or funder dataset**
(CLAUDE.md §6 guardrail: "Do NOT rebuild the funder-criteria dataset or routing engine").
The funder-criteria framework + routing engine already exist in the **`mca_funders`** catalog
(structured boxes `funder_programs`/`funder_program_versions`/`funder_industries`/
`funder_states`/`funder_operations`; routing outputs `routing_decisions`/
`routing_program_evaluations`/`routing_rule_results`; input contract `gold.v_funder_input`).
S5 = the **integration layer** only (Data Contract "Offer Engine Integration" sheet). Still
**no ML** (S6), **no outbound delivery / comms** (S8), **no compliance block** (S8), **no
merchant app** (S9+). Deterministic, auditable, reconciles.

---

## 1. Objective & scope

**Objective:** for each funded merchant, produce a **current, gated set of credible offer
options** by reusing the existing routing engine against an MRI-derived merchant profile, and
surface the **honest structure decision** (renewal vs buyout vs wait) for serial/rapid-reup
merchants. Deliverable (Build Plan §6 DoD): every eligible merchant carries offer options that
**match the existing routing engine's results**, and the **suitability gate demonstrably
blocks unsuitable offers** (e.g. a double-dip buyout).

**In scope (Build Plan §6 + Framework §5.7 + Data Contract "Offer Engine Integration"):**

- **Merchant profile assembly** (`transform`): build the routing engine's input profile
  (the `mca_funders.gold.v_funder_input` shape — vertical/industry, TIB, FICO, revenue,
  positions, state, …) from MRI gold (`gold.merchants` + `gold.deals` + `gold.merchant_clock_current`
  + `gold.merchant_rung_current`). Where MRI lacks a field (revenue, NSF, bankruptcies — no
  bank feed v1), leave it absent so the engine's **own missing-data handling**
  (`missing_data_categories`) produces honest `case-by-case`/`missing-data` verdicts — never fake.
- **Routing-engine reuse** (`common/offer` adapter, mechanism per **D-501**): run the
  **existing** routing logic against the funded book on a cadence and obtain per-funder
  verdicts (`routing_program_evaluations`: verdict / failing categories / rank / estimated
  funding-factor-commission / confidence). **Reuse, not rebuild.**
- **Offer outputs** (`transform/gold_offers.py`) → point-in-time `gold.merchant_offers`
  (D-507): `eligible_offer_types` (renewal / buyout / larger-advance / none-yet — D-504, from
  clock eligibility + rung/state + routing pass), `matched_funders` (funders whose box the
  merchant fits), `max_sustainable_advance` (capacity headroom — **D-505**), `best_offer_summary`
  (plain-language), `offer_refresh_date` (the tap-early cadence marker).
- **Suitability gate v1 — the renewal-vs-buyout structure decision** (`common/offer`, D-506):
  for serial/rapid-reup merchants, **deterministically compute both structures and the
  double-dip delta** (reusing the S2 clock) and recommend the honest option — renewal / buyout
  / **"wait and pay down first"**. This is the Framework's headline honesty feature and serves
  Wolf. The engine *proposes*; this gate *disposes* (suppresses a matchable-but-unsuitable offer).
- **Compliance gate — interface only** (D-508): a documented hook (`offer_vs_advice` /
  state-disclosure) that the offer flows through; the **full compliance block is S8** — S5
  must not design around it but does not build it.
- **Book Health unlock:** the deferred "offer acceptance" metric input lands (`eligible_offer_types`),
  to be aggregated when acceptance/comms exist (S8).
- Tier-1 + tier-2 tests; UC governance/lineage; reconciliation; the four-merchant scenarios
  (esp. Wolf → renewal-vs-buyout eval; Starr → none-yet/do-not-fund).

**Out of scope (do NOT build):**

- **No rebuild of the funder-criteria dataset or the routing engine** — reuse the `mca_funders`
  IP only (CLAUDE.md §6). We read its boxes / invoke its logic / consume its evaluations.
- **No outbound delivery, no merchant comms, no offer *sending*** — S8.
- **No compliance block / disclosure engine / regulated-language generation** — S8 (interface only).
- **No ML / pricing models** — S6; we reuse the routing engine's own estimates, we don't model.
- **No new clock/rung/identity/activation logic** — S5 READS S1–S4 gold; never recomputes the
  spine (CLAUDE.md §2.1); never reads SF `_sf_stored_*`.
- **No writes to the `mca_funders` catalog** — MRI reads it / invokes it read-only; all MRI
  outputs land in `mca_mri.gold`.

## 2. Definition of Ready

| Gate | State |
|---|---|
| S1–S4 in prod (`gold.merchants`/`deals`/`merchant_clock_current`/`merchant_rung_current`/`merchant_activation_current`) | ✅ 2026-06-02 |
| Existing funder-criteria dataset located | ✅ `mca_funders` catalog (silver boxes + gold routing outputs + `v_funder_input` contract) |
| Existing routing engine located | ✅ workspace `mca-funder-2A-3` / Azure DevOps `DataManagement` repo (versioned engine; `routing_engine_version`, `box_version_id`) |
| **Routing-engine invocation path** (callable job / entrypoint that accepts an ad-hoc profile batch) | ⏳ **D-501 spike** — confirm how to run the engine against MRI profiles |
| **MRI ↔ `mca_funders` merchant identity join** (so evaluations map back; likely via `azure_merchant_id` / tax_id) | ⏳ **D-502 spike** — confirm the join key + overlap |
| Read access to `mca_funders` from the MRI workspace | ⏳ confirm UC grants (read-only) |
| D-501…D-508 signed | ⏳ **awaiting sign-off (this plan)** |

Offline `common/offer` pure functions (eligible-offer-type rules, renewal-vs-buyout math,
suitability gate) + schemas can be built in parallel; the cloud build waits on D-501/D-502
(the two short investigation spikes) and the decisions.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate the integration contract (READS / USES / WRITES / GATED-BY /
   CADENCE from the Data Contract sheet) as explicit rules; enumerate the `v_funder_input`
   fields MRI can populate vs must leave missing; define the `eligible_offer_types` mapping and
   the renewal-vs-buyout decision rule.
2. **Design**
   - **Investigation spikes (D-501/D-502)** — confirm the engine's batch entrypoint + the
     MRI↔funder id join (read-only); record findings in the tracker. Mirrors S1's C-013 (the
     PORT-vs-call decision for AATM).
   - **`common/offer/` pure module** (no Spark at import — tier-1 testable; mirrors `common/rung`):
     `profile.py` (MRI gold → `v_funder_input` profile dict + missing-field flags),
     `offer_types.py` (`eligible_offer_types(clock, rung, routing)` rules), `structure.py`
     (renewal-vs-buyout math + double-dip delta + the honest recommendation, reusing
     `common.clock`), `suitability.py` (the gate: suppress unsuitable; compliance-hook stub).
   - **`transform/gold_offers.py`** (Spark): assemble profiles → invoke/reuse the engine
     (D-501) → map verdicts → write point-in-time `gold.merchant_offers` (+`_current` view) →
     emit `offer_computed` events to `gold.merchant_event_log` (new `EventType`).
   - **Layer/shape (D-507):** separate point-in-time `gold.merchant_offers` keyed
     `(merchant_id, offer_run_date)`, append-only + `_current` view (mirrors S2/S3/S4).
3. **Definition of Ready** — D-501…D-508 signed; spikes done; checklist in tracker.
4. **Build** — `common/offer/` (pure); `constants` (`OfferType`, `OfferStructure`,
   `SuitabilityVerdict` enums, `GoldTable.MERCHANT_OFFERS`/`_CURRENT`, extend `EventType`
   `offer_computed` — reuse existing `Thresholds`, no duplicate numbers, Rule 3);
   `schemas/gold.py` (`merchant_offers_schema()`); `field_maps` (offer field map + the
   `v_funder_input` profile map); `transform/gold_offers.py`. DQ: every merchant gets an offer
   row; `eligible_offer_types` ∈ enum; matched_funders ⊆ known funders; suitability gate
   applied; missing-profile fields flagged not faked.
5. **Test** — tier-1 (offer-type rules; **renewal-vs-buyout math + double-dip delta** on
   hand-worked vectors incl. Wolf; suitability suppression of an unsuitable buyout;
   profile-assembly missing-field flags; schema == contract; no `_sf_stored_*`) + tier-2
   (`gold_test`: build offers, **reconcile to the routing engine's own results** for a sample,
   coverage, suitability-gate demonstrably blocks, event append integrity, no-surface).
6. **Review** — self-review + `code-review`; verify the four merchants; confirm **no routing
   rebuild** (we reuse), **no spine recompute**, **no offer *sent*** (proposes only), **no
   writes to `mca_funders`**.
7. **Documentation** — offer field docs; the reuse decision (D-501) recorded like C-013;
   update tracker, SHARED_COMPONENTS (`common/offer`), TESTING_FRAMEWORK, RUNBOOK (offer scan +
   the reuse path), DECISIONS (D-501…D-508).
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` on approval**; the proactive scan's
   scheduling + any cross-system engine invocation are approval-gated (Rule 5).

## 4. Shared components created/changed

- **New** `common/offer/`: `profile.py`, `offer_types.py`, `structure.py`, `suitability.py`
  (pure, Spark-free at import).
- **Changed** `common/schemas/gold.py`: `merchant_offers_schema()`.
- **Changed** `common/field_maps.py`: offer field map; the `v_funder_input` profile map
  (MRI gold → engine input; source = "offer — derived from S1–S4 gold + funder reuse").
- **Changed** `common/constants.py`: `OfferType` / `OfferStructure` / `SuitabilityVerdict`
  enums, `GoldTable.MERCHANT_OFFERS`/`_CURRENT`, extend `EventType` (`offer_computed`),
  `FunderCatalog` config (the `mca_funders` fq names) — reuse existing `Thresholds`.
- **Changed** `common/dq`: offer DQ predicates (coverage, enum domains, matched-funder validity).
- **Reuse:** the entire `mca_funders` IP (read-only / invoke), the S2 clock (renewal-vs-buyout
  math), `io.guards`, the four-merchant fixtures.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local):** `eligible_offer_types` rules for each (clock × rung × state) combo;
  **renewal-vs-buyout**: rolled-balance + double-dip delta arithmetic on hand-worked vectors
  (incl. Wolf renewing days-in → buyout-or-wait recommended, never auto-renew); the
  **suitability gate** suppresses a matchable-but-unsuitable buyout and can return "wait and
  pay down"; profile assembly emits missing-field flags (revenue/NSF absent v1); schema ==
  contract; no `_sf_stored_*`.
- **Tier-2 (`gold_test`):** build `gold.merchant_offers`; **reconcile a sample of merchants to
  the routing engine's own `routing_program_evaluations`** (our matched_funders == the engine's
  passing programs for the same profile — proves "reuse, not reinvent"); whole-book coverage;
  `eligible_offer_types` ∈ enum 100%; suitability-gate suppression count; offer-event append
  integrity; no-surface.
- **Four-merchant scenario:** Wolf → renewal-vs-buyout structure eval (buyout/wait surfaced
  with the double-dip delta); Starr → none-yet / do-not-fund (defaulted); Snell → none-yet
  (new, clock running); a disciplined in-market merchant → renewal/larger-advance matched.

## 6. Data contracts touched

- **Reads (MRI gold):** `gold.merchants` (vertical/industry, governing_state, tenure),
  `gold.deals` (terms, factor trajectory, position history), `gold.merchant_clock_current`
  (paydown, balance, eligibility, est_weekly_revenue/burden — null v1), `gold.merchant_rung_current`
  (rung/state), `gold.merchant_activation_current` (current_state).
- **Reads / invokes (EXISTING IP, read-only):** `mca_funders` — the funder boxes
  (`silver.funder_programs`/`funder_program_versions`/`funder_industries`/`funder_states`/
  `funder_operations`), the input contract `gold.v_funder_input`, and the routing outputs
  `gold.routing_program_evaluations`/`routing_decisions` (mechanism per D-501).
- **Writes (MRI gold only):** `gold.merchant_offers` (+ `_current` view); new `offer_computed`
  events in `gold.merchant_event_log`. **No writes to `mca_funders`.**
- **Stable interface:** `eligible_offer_types` / `matched_funders` / `max_sustainable_advance` /
  `best_offer_summary` feed S8 advisory comms (gated) and the merchant app (S9+), and the
  "offer acceptance" Book Health metric (when acceptance lands). The suitability gate is the
  contract the advisory layer builds on.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Rebuilding the routing engine / criteria** (forbidden) | D-501 = reuse/invoke the existing engine; review-gate asserts no rule thresholds re-implemented in `common/offer` (only the offer-type mapping + structure math live in MRI) |
| **Engine has no ad-hoc batch entrypoint** | D-501 spike confirms; fallbacks ranked — (a) invoke job, (b) reuse existing `routing_program_evaluations` joined by merchant id (D-502), (c) thin criteria-match against the boxes as a last resort (flagged as partial) |
| **MRI ↔ funder merchant-id mismatch** (different id systems, as with AATM) | D-502 spike confirms the join (likely `azure_merchant_id` / normalized tax_id); unmatched merchants → `none-yet` + `offer_profile_unmatched` flag, never guessed |
| **Partial MRI profile** (no revenue/NSF/bankruptcy v1) | Feed the engine the fields we have; rely on its `missing_data_categories` → honest `case-by-case`/`missing-data` verdicts; never fabricate inputs |
| **`max_sustainable_advance` needs revenue** (null v1) | D-505: null + `max_sustainable_advance_is_missing` (capacity hook wired, inert until the revenue feed — FU-301), OR a conservative RTR-headroom proxy if you prefer; never a fabricated ceiling |
| **Engine "sell more money" failure mode** | The suitability gate (D-506) suppresses unsuitable offers + the renewal-vs-buyout honesty check; "wait and pay down" is a valid output; compliance interface reserved (S8) |
| **Cross-system coupling / stale funder boxes** | MRI reads/invokes read-only; box currency is the existing routing framework's job (Framework §5.7); we record `box_version_id`/`routing_engine_version` for lineage |
| **Cross-system invocation is outward-facing** | Any engine invocation + the proactive schedule are approval-gated (Rule 5); `gold_test` first |

## 8. Decisions (D-501…D-508 — SIGNED 2026-06-02, see DECISIONS C-019)

**D-501 resolved to the BATCH HANDOFF path** (after the spikes showed 0 id-overlap, so
reusing existing evaluations is non-viable and the engine must run on MRI profiles): MRI
builds + lands `v_funder_input` profiles → the existing routing engine evaluates on a cadence
(routing side owns it; no rebuild/port) → MRI reads results back keyed to MRI `merchant_id`
→ `gold.merchant_offers`. **FU-501** = the profile-handoff contract (landing table + id
round-trip + cadence) with the routing team. D-503…D-508 approved as written below.

### 8a. Original recommendations (for the record)

- **D-501 — Routing-engine reuse mechanism (headline, mirrors S1's C-013).** *Recommendation:*
  **reuse the existing engine, do not port** (its boxes are versioned + complex; porting =
  rebuild, forbidden). Preferred = **invoke** the engine on MRI-derived profiles on a cadence
  and read back `routing_program_evaluations`; if no batch entrypoint exists, **reuse existing
  evaluations** joined by merchant id (D-502); thin criteria-matching is a last-resort fallback
  (flagged partial). Needs the D-501 spike + your call on the mechanism.
- **D-502 — MRI ↔ `mca_funders` merchant identity join.** *Recommendation:* join on
  `gold.merchants.azure_merchant_id` → `mca_funders` merchant id (else normalized tax_id);
  unmatched → `none-yet` + flag. Confirm the key after the spike.
- **D-503 — Merchant profile assembly + missing fields.** *Recommendation:* populate the
  `v_funder_input` fields MRI has (FICO, TIB from business_start_date/months_in_business,
  active_position_cnt, governing_state, industry); leave revenue/NSF/bankruptcy/etc. absent →
  rely on the engine's missing-data verdicts; never fake. Confirm.
- **D-504 — `eligible_offer_types` derivation.** *Recommendation:* `renewal` when in-market &
  single-position disciplined; `buyout`/`larger-advance` when serial/rapid-reup & a funder box
  passes; `none-yet` when not eligible, gated (defaulted/dormant/new), or no funder match.
  Confirm the mapping.
- **D-505 — `max_sustainable_advance`.** *Recommendation:* revenue-dependent → **null +
  `_is_missing`** in v1 (no feed; hook wired, FU-301), not a fabricated ceiling. Confirm
  (vs a conservative RTR-headroom proxy).
- **D-506 — Renewal-vs-buyout suitability gate (v1).** *Recommendation:* **build it** —
  deterministically compute both structures + the double-dip delta (S2 clock) and recommend
  renewal / buyout / wait-and-pay-down with the merchant's interest as the tiebreaker. Confirm.
- **D-507 — Output shape/placement.** *Recommendation:* separate point-in-time
  `gold.merchant_offers` keyed `(merchant_id, offer_run_date)`, append-only + `_current` view
  (mirrors S2/S3/S4). Confirm.
- **D-508 — Compliance gate scope in S5.** *Recommendation:* **interface/hook only** in S5
  (offer flows through a documented `offer_vs_advice` placeholder); the full compliance block is
  S8. Confirm.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/offer/` implements offer-type rules + renewal-vs-buyout math + the suitability
  gate **literally** per D-504/D-506 — *tier-1 logic tests + four merchants*.
- [ ] **Reuse proven**: a sample of MRI merchants' `matched_funders` == the existing routing
  engine's passing programs for the same profile (no reinvented rules) — *tier-2 reconcile to
  `routing_program_evaluations`*.
- [ ] **Whole book gets an offer row**; `eligible_offer_types` ∈ enum 100%; matched_funders
  valid; unmatched merchants flagged `none-yet` — *coverage + enum DQ*.
- [ ] **Suitability gate demonstrably blocks** a matchable-but-unsuitable (double-dip) buyout;
  "wait and pay down" is a reachable output — *tier-1 + tier-2 suppression test*.
- [ ] **No routing rebuild, no spine recompute, no `_sf_stored_*`, no offer sent, no writes to
  `mca_funders`** — *review gate + no-surface test*.
- [ ] Four-merchant outcomes: Wolf → renewal-vs-buyout eval; Starr → none-yet/do-not-fund;
  Snell → none-yet; an in-market disciplined merchant → renewal/larger-advance — *scenario test*.
- [ ] Offer scan job **defined as code** (DAB); scheduling + engine invocation approval-gated —
  *job definition in repo*.
- [ ] Unity Catalog governs `gold.merchant_offers` with lineage from MRI gold + `mca_funders` —
  *UC check + tracker note*.
- [ ] Tier-1 + tier-2 suites green; results logged — *suite output in tracker*.
