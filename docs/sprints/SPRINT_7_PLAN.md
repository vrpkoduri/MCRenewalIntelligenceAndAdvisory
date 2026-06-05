# Sprint 7 Plan — Agentic Extraction (Statement Analyst + Data Steward)

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Architecture: the
**Agentic Layer** (`CLAUDE.md` §3, Framework §5.9). Goal (Build Plan §6, Sprint 7): introduce
the **first agents** to recover signals the deterministic spine cannot — **true concurrent
positions + total debit burden** from bank statements, and **default-cause** from free-text
Notes — so the spine classifies a sharper, more honest book.

**The governing principle (Framework §5.9): agents EXTRACT; the spine still COMPUTES.** An
agent earns its place only where a task needs judgment over an unpredictable path (reading
messy PDFs, classifying free-text). Everything that must be correct/auditable stays a
deterministic tool. Agents are **grounded strictly in source records, log every action to the
event log, call deterministic tools, and NEVER recompute the spine** (clock, classifier,
burden/eligibility math, compliance, models). "The rules fired" must remain the answer to
"why was I classified this way."

This sprint touches **new infrastructure** (an LLM/agent platform) and a **new data source**
(bank-statement PDFs not yet ingested), and carries an **accuracy bar** — so it is heavily
gated and phased.

---

## 1. Objective & scope

**Objective:** stand up two narrow extraction agents whose **structured outputs feed the
existing deterministic gold pipeline**, measurably improving classification:
- **Data Steward** — classify `default_subtype` (true-default / early-payoff / restructured)
  from `Notes` free-text, upgrading the interim `unknown` routing (D-301/B.2); flag
  date contradictions / anomalies for review.
- **Statement Analyst** — OCR + transaction-line classification of bank statements to recover
  **true concurrent positions** (incl. other funders' ACH debits Salesforce can't see) and
  **total weekly debit burden**, feeding `active_position_cnt` + `burden_ratio` / `est_weekly_revenue`.

**In scope (Build Plan §6 + Framework §5.9 + Appendix B staging + FU-301/FU-302):**
- **Data Steward agent** (Phase 1 — self-contained; `Notes` already in `silver.deals`):
  LLM classifies default-cause + anomaly flags from `Notes` + the deal record, with a **source
  citation + confidence** per call; a **deterministic mapper** turns the label into
  `default_subtype` + the B.2 route; low-confidence → human-review queue, never auto-applied.
  Validates against the known cases (Starr → true-default/exit or confirmed sub-type).
- **Statement Analyst agent** (Phase 2 — gated on statement access + a labeled sample):
  ingest statement PDFs → OCR → transaction-line classification → **other-funder ACH debits +
  deposits**; a **deterministic tool** counts concurrent positions and sums the weekly debit
  → feeds `active_position_cnt` / `total_weekly_debit` / `est_weekly_revenue` (→ real
  `burden_ratio`, which is null book-wide today). Funding-moment statements only.
- **Grounding + audit:** every agent action writes an `agent_extraction` event (input ref,
  output, model_version, confidence, citation) to `gold.merchant_event_log`; outputs land in a
  point-in-time `gold.merchant_extraction` table; the spine re-runs (S2/S3/S4) consume the
  improved signals.
- **Re-run + measure:** re-run the clock/rung/activation on the enriched signals and **show
  the classification improvement** (e.g., resolved default sub-types; Serial detection sharper
  with true positions; burden no longer null where statements exist).
- Tier-1 (deterministic mappers/tools + grounding contracts) + the accuracy evaluation on a
  **labeled sample**; UC governance; the four validation merchants.

**Out of scope (do NOT build):**
- **No agent touches the spine math** — clock, rung classifier, burden/eligibility formula,
  compliance gate, prediction models stay deterministic (Framework §5.9 "where agents must NOT go").
- **No live/current positions** — statements are funding-moment; live bank-feed truth is the
  app (S9+).
- **No advisory comms / Advisory Composer / Structure Advisor** — S8 (those agents + the
  compliance gate are the next sprint).
- **No outbound anything**; no merchant-facing surface.
- **No rebuild of clock/classifier** — agents only supply better INPUTS; the deterministic
  transforms recompute as before.

## 2. Definition of Ready

| Gate | State |
|---|---|
| S1–S6 in prod; the spine consumes `active_position_cnt`/`burden_ratio`/`default_subtype`/`Notes` | ✅ |
| `Notes` free-text available (Data Steward input) | ✅ `silver.deals.notes` (ingested S0) |
| **Bank-statement PDFs accessible** (Statement Analyst input) | ⏳ **D-702 — NOT ingested today** (S0 ingested account/opportunity/offer/fieldhistory only; statements live in SF Documents/ContentVersion). Gates Phase 2. |
| **LLM / agent platform** chosen + available (governed, cost-confirmed) | ⏳ **D-701** |
| **Labeled accuracy sample** (default sub-types + statement positions) | ⏳ **D-706** — must be created (the 4 merchants + a hand-labeled set) |
| Framework §5.9 guardrails (extract-not-compute, grounding, logging) | ✅ — encoded as the review gate |
| D-701…D-706 signed | ⏳ **awaiting sign-off (this plan)** |

Phasing follows readiness: **Data Steward (Phase 1) is buildable now** (Notes exist);
**Statement Analyst (Phase 2)** waits on statement ingestion + a labeled sample.

## 3. Task breakdown by SDLC stage

1. **Requirements** — restate §5.9 (extract/articulate/orchestrate; where agents must not go);
   define the two agents' exact inputs/outputs, the deterministic tools they call, the
   grounding/citation/confidence contract, the human-review threshold, and the accuracy bar.
2. **Design**
   - **`common/agents/` pure module** (deterministic, tier-1 testable — the parts that must be
     correct): `default_subtype.py` (label → `default_subtype` + B.2 route; the mapper the
     Data Steward's LLM label flows through), `positions.py` (transaction lines → concurrent
     position count + weekly debit — the deterministic counter the Statement Analyst feeds),
     `grounding.py` (the agent-output contract: source_ref + confidence + citation; validators).
   - **Agent definitions** (`agents/` — the LLM-using layer, NOT pure): the Data Steward +
     Statement Analyst prompts/tools, calling the deterministic `common/agents` tools. Platform
     per D-701.
   - **Transforms:** `transform/gold_extraction.py` — orchestrates the agents over the book
     (or a batch), writes point-in-time `gold.merchant_extraction` + `agent_extraction` events;
     a re-run wrapper that feeds the enriched signals into the existing clock/rung/activation.
   - **Layer/shape (D-704):** separate point-in-time `gold.merchant_extraction`
     (merchant_id/deal_id, extraction_run_date, extraction_type, value, confidence, source_ref,
     model_version, review_status) + `_current` view; the spine reads it as an optional
     enrichment (degrades to today's behavior when absent).
3. **Definition of Ready** — D-701…D-706 signed; statement access + labeled sample confirmed
   (Phase 2); LLM platform live.
4. **Build** — Phase 1 Data Steward end-to-end (Notes → label → `default_subtype`/route,
   grounded + logged, human-review for low confidence); then Phase 2 Statement Analyst
   (ingest PDFs → OCR → classify → deterministic position/burden tool). `constants`
   (`ExtractionType`, `ReviewStatus`, `EventType.AGENT_EXTRACTION`, confidence threshold);
   `schemas/gold.merchant_extraction_schema()`; `field_maps`.
   DQ: every extraction carries a source_ref + confidence + model_version; low-confidence →
   review, never auto-applied; agent never writes a spine-math column.
5. **Test** — tier-1 (the deterministic mappers/counters on hand-worked vectors incl. Starr;
   the grounding/citation contract; "agent output without a source_ref is rejected"; no spine
   column written by the agent path) + **accuracy evaluation** (Data Steward default sub-type
   vs the labeled sample ≥ the D-706 bar; Statement Analyst positions/burden vs labeled
   statements ≥ bar) + tier-2 (`gold_test`: extraction outputs land, events logged, re-run
   classification **measurably improves** — resolved sub-types, sharper Serial, real burden
   where statements exist; the Unclassified/`unknown` piles shrink).
6. **Review** — self-review + `code-review`; **§5.9 guardrail gate**: confirm agents only
   extract + call deterministic tools, are grounded + logged, write no spine math; PII/source
   handling per the contract's compliance codes.
7. **Documentation** — agent/extraction field docs; the §5.9 guardrails; update tracker,
   SHARED_COMPONENTS (`common/agents`), TESTING_FRAMEWORK (+ the accuracy register), RUNBOOK
   (agent batch + re-run), DECISIONS (D-701…D-706); resolve FU-301 (NSF/positions/revenue) +
   FU-302 (renewal chain) as the signals land.
8. **Definition of Done** — §9.
9. **Deploy/Activate** — `gold_test` first; **prod `gold` on approval**; LLM spend + statement
   ingestion + the agent batch schedule are approval-gated (Rule 5).

## 4. Shared components created/changed

- **New** `common/agents/`: `default_subtype.py`, `positions.py`, `grounding.py` (pure,
  deterministic — the correctness-critical tools the agents call; tier-1 testable).
- **New** `agents/`: the Data Steward + Statement Analyst definitions (prompts + tool wiring;
  the LLM layer — platform per D-701).
- **Changed** `common/schemas/gold.py`: `merchant_extraction_schema()`; event-log already
  reserves new types (D-305).
- **Changed** `common/field_maps.py`: extraction field map (+ the statement-derived signal
  provenance feeding `active_position_cnt`/`burden_ratio`/`est_weekly_revenue`/`default_subtype`).
- **Changed** `common/constants.py`: `ExtractionType` / `ReviewStatus` enums,
  `EventType.AGENT_EXTRACTION`, `AGENT_CONFIDENCE_REVIEW_MIN`, `GoldTable.MERCHANT_EXTRACTION`.
- **New (gated)** `transform/gold_extraction.py` + the spine re-run wrapper.
- **Reuse:** the S2 clock (position count + burden math — the agent FEEDS it, never replaces
  it), S3 rung (default sub-type → lifecycle gate), the event log, `io.guards`, the four merchants.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`.
- **Tier-1 (local, deterministic):** the `default_subtype` mapper (label → subtype + B.2
  route incl. Starr → exit/do-not-fund); the `positions` counter (transaction lines →
  concurrent count + weekly debit on hand-worked vectors); the grounding contract (reject an
  agent output missing source_ref/confidence; low-confidence → review_status); no `_sf_stored_*`
  / no spine-math column in the extraction map; schema == contract.
- **Accuracy evaluation (the §9 bar, D-706):** Data Steward default sub-type vs a labeled set
  (incl. the four merchants) ≥ threshold; Statement Analyst positions/burden vs labeled
  statements ≥ threshold. Reported, with the labeled sample versioned.
- **Tier-2 (`gold_test`):** run the agents over a sample/book → `gold.merchant_extraction` +
  `agent_extraction` events; re-run clock/rung/activation with the enriched signals and
  **quantify the improvement** (default `unknown` resolved; Serial detections gained from true
  positions; merchants with real burden where statements exist); grounding/audit integrity
  (every extraction has source_ref + model_version); no spine column written by the agent path.
- **Four-merchant scenario:** Starr → default sub-type resolved (exit vs the interim unknown);
  Wolf → true concurrent positions sharpen Serial; etc.

## 6. Data contracts touched

- **Reads:** `silver.deals.notes` (Data Steward), bank-statement PDFs (Statement Analyst —
  source per D-702), `gold.deals` / `gold.merchant_clock_current` (the records the extractions
  attach to + the tools' inputs). **No spine recompute by the agent.**
- **Writes:** `gold.merchant_extraction` (+ `_current`); `agent_extraction` events in
  `gold.merchant_event_log`; the enriched signals flow into the EXISTING `gold.merchant_clock`
  (positions/burden/revenue) + `gold.merchant_rung` (`default_subtype`) via the normal
  deterministic re-run — the agent never writes those tables directly.
- **Stable interface:** sharper `active_position_cnt` / `burden_ratio` / `est_weekly_revenue`
  / `default_subtype` improve S3 (Serial, distress, lifecycle gate) + S4 (state/play) + the
  dashboard + S5 offers, with zero change to their contracts (the agent upgrades inputs, not shapes).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Agent drifts into computing the spine** (auditability loss) | §5.9 review gate: agents extract + call deterministic tools only; the position COUNT + burden math + sub-type ROUTE live in pure `common/agents`/`common/clock`; the agent supplies inputs, never writes spine columns |
| **Hallucinated / ungrounded extractions** | Mandatory source_ref + confidence + citation per extraction; deterministic validators reject ungrounded output; **low-confidence → human-review queue, never auto-applied**; everything logged for audit |
| **Bank statements not accessible / not ingested** | D-702 — Phase 2 gated on a statement-ingestion path (ContentVersion/attachments, NOT in S0); Phase 1 (Data Steward) ships first on existing Notes |
| **OCR / statement-format variance** | Start on a labeled sample; accuracy bar (D-706) before trusting outputs; flag low-confidence for review; iterate formats |
| **LLM cost / platform** | D-701 — Databricks-native (governed, lineage); batch not per-view; cost-confirmed + spend gated (Rule 5) |
| **PII / regulated data in statements + Notes** | Handle per the Data Contract compliance codes (PII); statements stay in governed UC; no surfacing beyond the contract; compliance gate proper is S8 |
| **Over-trusting a thin labeled sample** | Version the labeled set; report accuracy honestly; expand the sample; degrade to today's deterministic behavior when an extraction is absent/low-confidence |
| **Scope creep into S8 comms** | S7 is extraction only; Advisory Composer / Structure Advisor + compliance gate are S8 |

## 8. Open decisions requiring your sign-off (D-701…D-706)

- **D-701 — LLM / agent platform.** *Rec:* Databricks-native — **Foundation Model APIs**
  (e.g. a Claude/Llama endpoint) + the **Mosaic AI Agent Framework**, for governance + lineage
  + the event-log/inference-table audit the Framework wants; batch (not per-view); spend gated.
  Confirm the platform + model + that LLM cost is acceptable.
- **D-702 — Bank-statement source + ingestion.** *Rec:* confirm where the PDFs live (SF
  **ContentVersion/ContentDocument** or Attachments — NOT ingested in S0) and stand up a
  read-only ingestion path to UC; a short spike to confirm availability + volume + format.
  **Gates Phase 2.** Confirm access + whether statements exist for enough of the funded book.
- **D-703 — Phasing.** *Rec:* **Data Steward first** (self-contained on existing `Notes`;
  resolves `default_subtype` + validates vs Starr; no PDF/OCR infra), **Statement Analyst
  second** (after D-702 + a labeled sample). Confirm.
- **D-704 — Extraction output shape.** *Rec:* separate point-in-time `gold.merchant_extraction`
  (+`_current`) with source_ref/confidence/model_version/review_status; the spine consumes it
  as optional enrichment via the normal re-run (agent never writes spine tables). Confirm.
- **D-705 — Guardrails / grounding / human-review.** *Rec:* every extraction grounded
  (source_ref + citation + confidence); **< `AGENT_CONFIDENCE_REVIEW_MIN` → review queue, not
  auto-applied**; agents call deterministic tools for all counting/classification; full event-log
  audit incl. `model_version`. Confirm the review threshold.
- **D-706 — Accuracy bar + labeled sample.** *Rec:* hand-label a sample (the four merchants +
  N more) for default sub-type and statement positions; set a "sane + improving" acceptance
  (e.g. sub-type accuracy ≥ X% on labeled; positions within ±1 on labeled statements) — the
  numbers calibrated on the first sample, not pre-fixed. Confirm the approach.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] `common/agents/` deterministic mappers/tools (`default_subtype`, `positions`, grounding)
  implemented + tier-1 tested incl. the four merchants — *logic tests*.
- [ ] **Data Steward** classifies `default_subtype` from Notes, grounded + logged, low-confidence
  → review; **validated against known cases** (Starr) — *accuracy eval*.
- [ ] **Statement Analyst** recovers concurrent positions + weekly burden from labeled
  statements ≥ the agreed bar (Phase 2) — *accuracy eval*.
- [ ] Extractions land in `gold.merchant_extraction` with source_ref + confidence + model_version;
  `agent_extraction` events logged; **no spine-math column written by the agent path** — *DQ + review gate*.
- [ ] **Re-run classification measurably improves** (default `unknown` resolved; sharper Serial /
  real burden where statements exist; Unclassified/unknown piles shrink) — *tier-2 before/after*.
- [ ] **Agents never recompute the spine** (clock/classifier/burden/eligibility/compliance/models)
  — *§5.9 review gate + no-surface test*.
- [ ] FU-301 (NSF/positions/revenue) + FU-302 (renewal chain) advanced as the signals land —
  *tracker follow-up update*.
- [ ] Agent batch + re-run **defined as code** (DAB); LLM spend + statement ingestion + schedule
  approval-gated — *job definitions in repo*.
- [ ] Unity Catalog governs `gold.merchant_extraction` + the statement store with lineage — *UC check*.
- [ ] Tier-1 + accuracy + tier-2 suites green; results logged — *suite output in tracker*.
