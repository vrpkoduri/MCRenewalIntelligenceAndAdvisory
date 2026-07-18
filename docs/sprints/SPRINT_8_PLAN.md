# Sprint 8 Plan — Advisory Layer (Advisory Composer + Structure Advisor + Compliance Gate)

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture: the
**Agentic Layer** (`CLAUDE.md` §3, Framework §5.9) + the **first-class Compliance Gate**
(`CLAUDE.md` §2.4, Framework §2.4/§5.9). Goal (Build Plan §7, Sprint 8): turn the computed
facts the spine already produces — rung, clock, offers, extractions, predictions — into
**honest, grounded, merchant-facing advisory guidance**, and make **every merchant-facing
output pass a real compliance gate** before it can ever be delivered.

**The governing principles.** Two, and they are in tension by design — the sprint exists to
resolve that tension in architecture, not prose:

1. **Honesty as an engineering constraint (Framework §2.3).** Any merchant-facing output is
   grounded **ONLY in computed numbers, never invented.** Advice may legitimately say *"don't
   take money"* or *"wait and pay down."* The Offer Engine (S5) **proposes**; the advisory +
   compliance layers **dispose.** A matchable buyout that is a double-dip must not be pitched.
2. **The compliance gate is real architecture (Framework §2.4), not an afterthought.**
   State-aware disclosure and **advice-vs-specific-offer** gating are a **first-class,
   deterministic block** every merchant-facing artifact must pass. This sprint **realizes the
   `compliance_gate_hook` interface reserved in S5** (`common/offer/suitability.py`, D-508).

**Where the agents sit (Framework §5.9): agents ARTICULATE + ORCHESTRATE; they never compute
the spine and never self-certify compliance.** The Advisory Composer and Structure Advisor
turn already-computed facts into language. Everything that must be **correct/auditable** stays
a deterministic tool: the **fact pack** the agent is allowed to speak from, the **grounding
validator** (no number that isn't in the pack), the **compliance gate** (advice/offer
classification + state disclosure), and the S5 **suitability/structure math**. "The rules
fired, and the gate passed" must remain the answer to *"why did the merchant see this?"*

This sprint produces **merchant-facing content** for the first time, so it is heavily gated.
**This first pass builds OFFLINE deterministic modules + the pure agent layer only** (the
S7 pattern: pure `common/` + tier-1 tests + a Spark-free agent half with an injected
`predict_fn`). The LLM/cloud transform, any real FM calls, and **any delivery** are designed
here but **deferred behind explicit approval** (Rule 5). **S8 composes and gates; it does not
send.** No outbound comms in this sprint.

---

## 1. Objective & scope

**Objective:** stand up the advisory layer whose **grounded, compliance-gated outputs** are
the honest merchant-facing articulation of the spine's computed facts:

- **Advisory Composer** — assemble a merchant's computed facts (rung, lifecycle/state, clock,
  offers, extractions, predictions) into a **grounded advisory record** (headline + rationale +
  recommended action + cited numbers). It may only speak numbers present in a deterministic
  **fact pack**; a grounding validator rejects anything invented.
- **Structure Advisor** — **articulate the S5 renewal-vs-buyout / wait-and-pay-down decision**
  (double-dip cost, paydown %) that `common/offer/structure.py` already computes, into honest
  merchant-facing language. It **explains** the math; it never recomputes it, and it **cannot
  un-suppress** an offer the S5 suitability gate suppressed.
- **Compliance Gate** — a **deterministic first-class block** every merchant-facing artifact
  passes: classify **advice vs. specific-offer**, apply **state-aware disclosure** rules, and
  **hard-BLOCK** (never deliver) anything ungrounded, unsuitable-but-pitched, or missing a
  required disclosure. Realizes the S5 `compliance_gate_hook` (D-508).

**In scope (Build Plan §7 + Framework §2.3/§2.4/§5.9 — OFFLINE this pass):**
- **`common/compliance/` (pure, deterministic — the correctness-critical gate; tier-1):**
  `classify.py` (advice / specific-offer / factual-summary), `disclosure.py` (state-aware
  disclosure regime lookup), `gate.py` (the composed hard gate → PASS / BLOCKED + required
  disclosures + machine-readable reasons). This is the block that makes §2.4 real.
- **`common/advisory/` (pure — the grounding + orchestration; tier-1):** `factpack.py`
  (assemble the grounded fact pack from the gold `_current` views — the ONLY numbers the
  Composer may use, each carrying its source field + run_date), grounding validation (reuse
  `common/agents/grounding.py` pattern — no numeric token outside the pack; ungrounded →
  rejected), `structure_advisor.py` (deterministic articulation helpers that wrap
  `common/offer/structure.py` — the Structure Advisor's correctness-critical half), and the
  pure `compose_advisory` orchestration (fact pack → agent → ground → **gate** → advisory
  record; Spark-free, injected `predict_fn` — tier-1 testable exactly like S7's
  `data_steward.py` / `statement_analyst.py`).
- **`agents/` (the LLM-using layer — pure prompt-builders + tolerant parsers built offline;
  real FM calls gated):** the Advisory Composer + Structure Advisor prompts/tools calling the
  deterministic tools above. Platform **reuses S7** (Databricks Foundation Model
  `databricks-claude-sonnet-4-5` via the `databricks-sdk` serving client, temperature 0,
  batch) — no new platform decision.
- **Schema/maps/constants:** `schemas/gold.merchant_advisory_schema()`;
  `field_maps.MERCHANT_ADVISORY_MAP`; `constants` `AdvisoryType` / `ComplianceStatus` /
  `DisclosureRegime` enums + `EventType.ADVISORY_COMPOSED` + `EventType.COMPLIANCE_CHECKED` +
  `GoldTable.MERCHANT_ADVISORY`(+`_CURRENT`) (reuse `AGENT_CONFIDENCE_REVIEW_MIN`).
- **Grounding + audit:** every advisory carries its fact-pack citations + confidence +
  model_version + **compliance_status** + review_status; every compose/gate action logs an
  `advisory_composed` / `compliance_checked` event.
- **Accuracy/quality bar (D-807):** a labeled sample of merchant-facing advisories (four
  merchants central) scored on grounding (0 invented numbers — hard), compliance-gate
  correctness, and honesty (recommends *wait / don't take money* where the math says so — Wolf
  → wait-and-paydown). Permanent tier-2 gate, mirrors D-706/D-711.
- Tier-1 for every deterministic module + the pure agent orchestration; the four validation
  merchants as canonical fixtures.

**Out of scope (do NOT build in S8):**
- **No outbound / delivery of any kind** — no email/SMS/portal push, no Salesforce write, no
  merchant app. S8 **composes and gates**; the artifact is stored, not sent. Delivery is a
  later, separately-gated step (S9+ / FU-401 write-back).
- **No agent computes the spine or the gate verdict** — clock, rung, burden/eligibility,
  suitability/double-dip math, and the compliance classification/disclosure rules stay
  deterministic (Framework §5.9). The agent proposes language + an *intent label*; the
  deterministic gate decides.
- **No LLM/cloud run this pass** — the FM calls + `transform/gold_advisory.py` Spark driver +
  the `gold_test` run are designed but gated (Rule 5), same as S7's offline-first phase.
- **No legal drafting** — the gate flags *which disclosure regime applies and that a
  disclosure block is required*; it does **not** author binding legal disclosure language
  (that needs counsel — D-805). No personalized financial/investment advice.
- **No re-scope of the spine** — S8 reads `_current` gold views; it writes only its own
  `gold.merchant_advisory` + its events.
- **Internal rep guidance is untouched** — S4 `next_tactical_action` / `next_strategic_nudge`
  are **internal, not merchant-facing**, and stay as-is (the gate is for merchant-facing output
  only — see D-801 scope).

## 2. Definition of Ready

| Gate | State |
|---|---|
| S0–S7 in PROD `gold`; the spine's computed facts exist to articulate | ✅ |
| Rung / lifecycle / clock facts available | ✅ `merchant_rung_current`, `merchant_clock_current`, `merchant_activation_current` |
| Extraction facts (default-cause, statement positions/burden) available | ✅ `merchant_extraction_current` (S7 Ph-1+2, advisory-only) |
| Prediction facts available | ✅ `merchant_predictions_current` (S6 v1) |
| Offer / structure facts available | ◑ `common/offer` built + tier-1 green; `gold.merchant_offers` **gated on FU-501** (cloud). Offline structure math (double-dip / recommend_structure) is available now for the Structure Advisor. |
| S5 `compliance_gate_hook` reserved interface | ✅ `common/offer/suitability.py` (D-508) — S8 realizes it |
| LLM / agent platform | ✅ **reuse S7** (`databricks-claude-sonnet-4-5`, `databricks-sdk` serving client) |
| **State-aware disclosure rules** (which states, which regimes) | ⏳ **D-805** — needs confirmation (legal-adjacent); v1 = flag regime + require disclosure block, do not draft language |
| **Labeled advisory quality sample** | ⏳ **D-807** — to be created (four merchants + N more) |
| Framework §2.3/§2.4/§5.9 guardrails (grounded-only, gate-is-real, articulate-not-compute) | ✅ — encoded as the review gate |
| D-801…D-807 signed | ⏳ **awaiting sign-off (this plan)** |

**Dependency note:** the Structure Advisor's cloud path over *matched-funder* offers depends on
`gold.merchant_offers` (FU-501). The **offline** structure math (`common/offer/structure.py`,
double-dip / wait-vs-buyout — Wolf case) exists today, so S8's offline build and its Structure
Advisor tier-1 tests are **not** blocked by FU-501; only the eventual end-to-end cloud run over
live matched offers is.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate §2.3 (grounded-only, "don't take money" is valid advice), §2.4
   (compliance gate is first-class), §5.9 (agents articulate/orchestrate, never compute the
   spine/gate). Define the fact-pack contract, the grounding rule (no numeric token outside the
   pack), the advice-vs-specific-offer classification, the state-disclosure rule set, the hard
   BLOCK semantics, and the quality bar.
2. **Design**
   - **`common/compliance/` (pure, deterministic):** `classify.py` — `classify_output_type`
     (advice / specific-offer / factual-summary from the advisory record's content + whether it
     names concrete offer terms); `disclosure.py` — `disclosure_regime(governing_state)` +
     `required_disclosures(...)` from a config-driven rule table (D-805); `gate.py` —
     `compliance_gate(advisory, governing_state, suitability_verdict)` → `{status: PASS/BLOCKED,
     output_type, required_disclosures[], reasons[]}` (hard-BLOCK on ungrounded, on a
     specific-offer that is SUPPRESS/WAIT per S5 suitability, or on a missing required
     disclosure).
   - **`common/advisory/` (pure):** `factpack.py` — `build_fact_pack(merchant signals)` → a
     typed, cited fact pack (each number = value + source gold field + run_date); grounding via
     the `common/agents/grounding.py` validator (reject any advisory numeric token not in the
     pack — ungrounded → REJECTED); `structure_advisor.py` — deterministic helpers wrapping
     `common/offer/structure.py` (`structure_evaluation`, `double_dip_cost`,
     `recommend_structure`) into the facts the Structure Advisor articulates (it never
     recomputes); `compose.py` — pure `compose_advisory(fact_pack, predict_fn, gate_fn)` →
     agent draft → ground → **gate** → advisory record (Spark-free, injected `predict_fn`).
   - **Agent layer** (`agents/advisory_composer.py`, `agents/structure_advisor.py` — pure
     prompt-builders + tolerant/defensive parsers, built offline; FM caller reuses S7's serving
     client, gated for cloud).
   - **Layer/shape (D-804):** separate point-in-time `gold.merchant_advisory`
     (merchant_id/advisory_run_date/advisory_type/headline/rationale/recommended_action/
     grounded_refs/confidence/model_version/**compliance_status**/required_disclosures/
     review_status) + `_current` view. Stored, **not delivered**.
3. **Definition of Ready** — D-801…D-807 signed; D-805 disclosure rules confirmed; labeled
   quality sample created (D-807).
4. **Build (OFFLINE this pass)** — the pure `common/compliance/` + `common/advisory/` modules +
   the pure agent orchestration + `constants` / `schemas/gold.merchant_advisory_schema()` /
   `field_maps`. DQ: every advisory carries grounded_refs + confidence + model_version +
   compliance_status; **ungrounded → REJECTED; a specific-offer that fails suitability or lacks a
   required disclosure → BLOCKED; the agent writes no spine column and no gate verdict.**
5. **Test** — tier-1 (below) + the **quality evaluation** on the labeled sample (D-807) — run
   at cloud time. **Full suite green after every build piece** (Rule 4).
6. **Review** — self-review + `code-review`; **§2.3/§2.4/§5.9 guardrail gate:** confirm the
   agent only articulates + supplies an intent label, the deterministic gate decides, no invented
   numbers survive grounding, no merchant-facing artifact bypasses the gate, PII/source handling
   per the Data Contract compliance codes.
7. **Documentation** — advisory/compliance field docs; the §2.4 gate contract; update tracker,
   SHARED_COMPONENTS (`common/compliance`, `common/advisory`), TESTING_FRAMEWORK (+ the quality
   register), RUNBOOK (advisory batch + gate), DECISIONS (D-801…D-807).
8. **Definition of Done** — §9.
9. **Deploy/Activate (GATED — not this pass)** — `transform/gold_advisory.py` + real FM calls +
   the `gold_test` run are **deferred behind explicit approval**; `gold_test` first, PROD needs
   `allow_prod=True` + approval; **any delivery is a separate later gated step** (Rule 5).

## 4. Shared components created/changed

- **New** `common/compliance/`: `classify.py`, `disclosure.py`, `gate.py` (pure, deterministic —
  the first-class compliance block; tier-1). **Realizes the S5 `compliance_gate_hook` (D-508).**
- **New** `common/advisory/`: `factpack.py`, `structure_advisor.py`, `compose.py` (pure — the
  grounding + articulation + orchestration; tier-1).
- **New** `agents/`: `advisory_composer.py` + `structure_advisor.py` (prompt-builders +
  parsers; the LLM layer — reuses the S7 FM caller, cloud gated).
- **Changed** `common/schemas/gold.py`: `merchant_advisory_schema()`.
- **Changed** `common/field_maps.py`: `MERCHANT_ADVISORY_MAP` (grounded fields +
  `compliance_status` / `required_disclosures` provenance; no `_sf_stored_*`).
- **Changed** `common/constants.py`: `AdvisoryType` / `ComplianceStatus` / `DisclosureRegime`
  enums, `EventType.ADVISORY_COMPOSED` + `EventType.COMPLIANCE_CHECKED`,
  `GoldTable.MERCHANT_ADVISORY`(+`_CURRENT`) (reuse `AGENT_CONFIDENCE_REVIEW_MIN`; no duplicate
  thresholds — Rule 3).
- **New (gated)** `transform/gold_advisory.py` (Spark driver — reuses the S7 FM caller + write
  helpers + `_create_current_view`) + `tests/tier2/recon_advisory.py`.
- **Reuse:** `common/offer/{structure,suitability}` (the Structure Advisor articulates these,
  never recomputes; the gate reads `suitability_verdict`), `common/agents/grounding.py` (the
  grounding validator), the gold `_current` views (fact sources), the event log, `io.guards`
  (no-surface), the four validation merchants.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local, deterministic):**
  - **Compliance gate** — `classify_output_type` (advice vs specific-offer vs factual-summary
    on hand-worked records); `disclosure_regime` per state (CA/NY/UT/VA/… → regime; else none);
    `compliance_gate` **hard-BLOCKs** (a) an ungrounded advisory, (b) a **specific-offer whose
    S5 suitability is SUPPRESS/WAIT** (the double-dip that must not be pitched), (c) a
    specific-offer in a disclosure state with no disclosure block; **PASSes** honest advice
    ("wait and pay down") and a grounded, suitable, disclosed offer.
  - **Advisory grounding** — the grounding validator rejects any advisory numeric token absent
    from the fact pack (no invented numbers); a well-grounded advisory validates.
  - **Structure Advisor** — articulates `structure_evaluation` faithfully (Wolf →
    wait-and-paydown, $20,880 rollover cost surfaced as an *honest reason to wait*, not a pitch);
    it never emits a number the S5 math didn't produce; a suppressed buyout stays suppressed.
  - **Compose orchestration** — `compose_advisory` with a stubbed `predict_fn` runs
    factpack→agent→ground→gate; a draft that invents a number → REJECTED; a specific-offer
    draft failing the gate → BLOCKED; the four merchants produce sane advisory types.
  - **Invariants** — the advisory map carries **no `_sf_stored_*`** and **no spine-math column**;
    schema == `merchant_advisory_schema()`.
- **Quality evaluation (the §9 bar, D-807 — at cloud time):** the labeled advisory sample
  (four merchants + N more) scored on grounding (**0 invented numbers — hard 100%**),
  compliance-gate correctness (output-type + disclosure vs labels), and honesty (recommends
  *wait/don't take money* where the math says so). Reported; the sample versioned; a permanent
  tier-2 gate + regression guards (never pitch a suppressed double-dip; never emit an ungrounded
  number).
- **Tier-2 (`gold_test`, gated):** run the Composer/Structure Advisor over a sample/book →
  `gold.merchant_advisory` + `advisory_composed`/`compliance_checked` events; assert every
  stored artifact is grounded + gated (compliance_status ∈ {PASS, BLOCKED}), no BLOCKED artifact
  is marked deliverable, grounding/audit integrity (every advisory has grounded_refs +
  model_version), no spine column written by the agent path.
- **Four-merchant scenario:** Wolf → Structure Advisor "wait and pay down" (suppress the
  double-dip); Starr → distressed-exit advisory tone, no new-money pitch; Tom Snell →
  new/establishing nurture; One Big Promotion → dormant win-back — each grounded + gated.

## 6. Data contracts touched

- **Reads (all `_current` gold views — the computed facts to articulate):**
  `merchant_rung_current`, `merchant_clock_current`, `merchant_activation_current`,
  `merchant_extraction_current`, `merchant_predictions_current`, and (when FU-501 lands)
  `merchant_offers_current`; `gold.merchants` for `governing_state`. **No spine recompute.**
- **Writes:** `gold.merchant_advisory` (+ `_current`); `advisory_composed` /
  `compliance_checked` events in `gold.merchant_event_log`. **Nothing outbound; no SF write.**
- **Stable interface:** `gold.merchant_advisory` is a new consumer-facing point-in-time table
  (like extraction/predictions); it changes no existing contract. The S5 `compliance_gate_hook`
  now returns a real verdict (S8), a backward-compatible fill of the reserved interface.
- **PII / no-surface:** advisories carry only derived, grounded aggregates + the merchant's own
  situation; the `_sf_stored_*` no-surface guard and the S7 statement no-surface guard (raw
  account numbers / running balances) both hold — nothing regulated leaks into an advisory.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Agent invents a number** (honesty breach, §2.3) | The Composer may speak **only** the deterministic fact pack; a grounding validator **rejects any numeric token not in the pack** (ungrounded → REJECTED, never stored as deliverable). Numbers are cited to a gold field + run_date. |
| **Agent self-certifies compliance / drifts into deciding the gate** (§2.4/§5.9 loss) | The agent supplies language + an **intent label only**; the **deterministic `common/compliance` gate decides** PASS/BLOCK, output-type, and required disclosures. The gate is pure, tier-1, auditable — never an LLM call. |
| **A suppressed double-dip gets pitched** (the exact §2.3 failure) | The gate hard-**BLOCKs** any specific-offer whose S5 `suitability_verdict` is SUPPRESS/WAIT; the Structure Advisor cannot un-suppress; regression guard in the quality gate. |
| **Missing / wrong state disclosure** (regulatory) | State-aware `disclosure.py` rule table (D-805); a specific-offer in a disclosure state with no disclosure block is BLOCKED; v1 **flags the regime + requires the block, does not draft legal language** (counsel owns wording). |
| **Advice-vs-offer misclassification** | Deterministic `classify_output_type` on content + concrete-terms presence; specific-offer path is strictly gated; labeled quality sample scores classification accuracy. |
| **Over-trusting a thin quality sample** | Version the labeled set; report honestly; grounding is a **hard 100%** gate; expand the sample; degrade to REVIEW/BLOCK when uncertain (never auto-deliver). |
| **Accidental delivery** | S8 has **no delivery path at all** — artifacts are stored with a `compliance_status`; delivery is a separate later gated step (Rule 5). |
| **Scope creep into delivery / app** | S8 = compose + gate only; outbound comms, SF write-back (FU-401), and the merchant app (S9+) stay out. |
| **PII in merchant-facing text** | Grounded aggregates only; `_sf_stored_*` + statement no-surface guards hold; PII per the Data Contract compliance codes. |

## 8. Open decisions requiring your sign-off (D-801…D-807)

- **D-801 — Compliance gate: placement, shape, and hard-vs-soft.** *Rec:* a **pure,
  deterministic `common/compliance/` block** (classify + disclosure + gate), tier-1 tested,
  realizing the S5 `compliance_gate_hook` (D-508). It is a **HARD gate**: any merchant-facing
  artifact that is ungrounded, a suppressed/unsuitable offer pitched as a specific offer, or
  missing a required disclosure is **BLOCKED** (stored, never marked deliverable) — not merely
  annotated. **Scope = merchant-facing outputs only** (S4 internal rep guidance is untouched).
  Confirm: deterministic block + hard BLOCK + merchant-facing-only scope.
- **D-802 — Advisory Composer grounding contract.** *Rec:* the Composer speaks **only** from a
  deterministic **fact pack** (each number = value + source gold field + run_date); a grounding
  validator **rejects any numeric token not in the pack**; output is a **structured advisory
  record** (headline / rationale / recommended_action / grounded_refs), not free prose. It never
  computes the spine. Confirm the fact-pack + no-invented-numbers contract.
- **D-803 — Structure Advisor scope.** *Rec:* it **articulates only** the existing S5
  `structure_evaluation` / `suitability_verdict` (renewal / buyout / wait-and-pay-down + the
  double-dip cost `common/offer/structure.py` already computes); it **never recomputes** the math
  and **cannot un-suppress** a suppressed offer. It explains *why wait / why renew* honestly
  (serves Wolf). Confirm articulate-not-recompute + honors suppression.
- **D-804 — Advisory output shape/placement + review/gating.** *Rec:* separate point-in-time
  `gold.merchant_advisory` (+`_current`) keyed `(merchant_id, advisory_run_date)`, append-only,
  carrying grounded_refs / confidence / model_version / **compliance_status** /
  required_disclosures / review_status; **stored, not delivered** (no outbound in S8). Mirrors
  D-704/D-604/D-507. Confirm shape + store-not-send.
- **D-805 — State-aware disclosure rule source + v1 scope.** *Rec:* a **config-driven
  `disclosure_rules` table keyed by `governing_state`**, seeded with the commercial-financing
  disclosure regimes we can confirm (e.g. **CA** commercial financing disclosure, **NY** CFDL,
  **UT**, **VA**, others as researched); **v1 flags *which regime applies* + *requires a
  disclosure block be present*, and does NOT draft binding legal language** (counsel owns
  wording). This is legal-adjacent — **please confirm the state list + that "flag + require,
  don't draft" is the right v1 line, and whether counsel should review before any cloud run.**
- **D-806 — Advice-vs-specific-offer classification rule.** *Rec:* a **deterministic**
  `classify_output_type` — *specific-offer* = the artifact names concrete offer terms (amount /
  factor / payment); *advice* = general guidance / eligibility / paydown coaching with no
  concrete terms; *factual-summary* = the merchant's own computed situation. Specific-offer
  triggers the strict path (disclosure required + suitability must be SURFACE). The **agent
  labels intent; the deterministic gate enforces.** Confirm the boundary.
- **D-807 — Advisory quality bar + labeled sample.** *Rec:* hand-label a sample of
  merchant-facing advisories (the four merchants + N more) scored on **grounding (0 invented
  numbers — hard 100%)**, **compliance-gate correctness** (output-type + disclosure vs labels),
  and **honesty** (recommends *wait / don't take money* where the math says so). A permanent
  tier-2 gate + regression guards (never pitch a suppressed double-dip; never emit an ungrounded
  number), calibrated on the first sample. Mirrors D-706/D-711. Confirm the approach.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/compliance/` (classify + disclosure + gate) and `common/advisory/` (factpack +
  structure_advisor + compose) implemented + tier-1 tested incl. the four merchants — *logic tests*.
- [ ] **Advisory Composer** produces grounded advisory records that speak **only** the fact
  pack; an invented number is **rejected by grounding** — *grounding tests*.
- [ ] **Structure Advisor** articulates the S5 structure decision faithfully (Wolf → wait-and-
  pay-down), recomputes nothing, and cannot un-suppress a suppressed offer — *structure tests*.
- [ ] **Compliance gate** hard-BLOCKs the ungrounded / suppressed-offer-pitched / missing-
  disclosure cases and PASSes honest advice + a grounded-suitable-disclosed offer;
  **realizes the S5 hook (D-508)** — *gate tests*.
- [ ] Advisories land in `gold.merchant_advisory` with grounded_refs + confidence +
  model_version + `compliance_status`; `advisory_composed`/`compliance_checked` events logged;
  **no BLOCKED artifact marked deliverable**; **no spine column / gate verdict written by the
  agent path** — *DQ + review gate* (at cloud time).
- [ ] **Quality evaluation** meets the D-807 bar (grounding 100%, gate correctness, honesty) on
  the labeled sample — *quality eval* (at cloud time).
- [ ] **Agents never compute the spine or the gate verdict** (clock/classifier/burden/
  eligibility/suitability-math/compliance-decision stay deterministic) — *§5.9/§2.4 review gate*.
- [ ] **No outbound / delivery** exists in S8 (compose + gate only) — *scope check*.
- [ ] Advisory batch + gate **defined as code** (DAB); FM spend + the `gold_test`/PROD runs +
  any future delivery are approval-gated — *job definitions in repo (gated)*.
- [ ] Unity Catalog governs `gold.merchant_advisory` with lineage — *UC check* (at cloud time).
- [ ] Tier-1 suite green + results logged; the cloud tier-2 + quality gates staged behind
  approval — *suite output in tracker*.
