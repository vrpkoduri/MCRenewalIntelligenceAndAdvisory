# GENERAL INSTRUCTIONS — Ways of Working (MRI Project)

**Status: binding.** This is the governance page for *how* we build the Morgan Cash Merchant Retention & Advisory Intelligence (MRI) system. It sits **above** the sprint work and applies to every task, every sprint, every build cycle. Read it before any work.

## 0. Precedence & relationship to other documents

Read order and authority (highest first):

1. **`CLAUDE.md`** — always-on context: principles, guardrails, tech stack, current status.
2. **This page (`GENERAL_INSTRUCTIONS.md`)** — *how we work*: SDLC, planning, shared components, testing, decision gates, documentation.
3. **Companion specs (authoritative on the *what/why*, never contradict):**
   - `Morgan_Cash_Merchant_Advisory_Framework.docx` — Framework Specification.
   - `Morgan_Cash_Gold_Table_Data_Contract.xlsx` — the schema every layer reads/writes.
   - `Morgan_Cash_Build_Plan_Sprint_Roadmap.docx` — build order + **Appendix A (clock math)** + **Appendix B (rung ruleset)**.
4. **`SPRINT_<n>.md`** — the active sprint's exact scope and Definition of Done.

If this page and a companion spec ever conflict on *what to build*, the spec wins and we raise the conflict to you. If they conflict on *how to build*, this page wins.

---

## 1. RULE 1 — Always plan first, and keep the master sprint document current

**Commitment:** No code is written without an approved, written plan, and the master sprint record is updated to reflect it.

- **Master sprint document** = `Morgan_Cash_Build_Plan_Sprint_Roadmap.docx` (the canonical roadmap) plus a living **`docs/SPRINT_TRACKER.md`** index that records, per sprint: status, the detailed build plan link, decisions made, test results, and exit-criteria sign-off. *(The .docx stays the authoritative narrative; the tracker is the fast-moving operational log so we don't churn the formal document for every change.)*
- **Per-sprint detailed plan**: each sprint gets a `docs/sprints/SPRINT_<n>_PLAN.md` containing scope, task breakdown (mapped to the SDLC stages in §3), shared-component impact (§4), the test plan (§5), risks, and explicit open decisions for you (§6).
- **Plan template (every plan uses this skeleton):**
  1. Objective & in/out of scope (from the sprint spec)
  2. Definition of Ready (preconditions/gates cleared)
  3. Task breakdown by SDLC stage
  4. Shared components created/changed
  5. Test plan (which test types, which fixtures, expected reconciliations)
  6. Data contracts touched (inputs read / outputs written)
  7. Risks & mitigations
  8. Open decisions requiring your sign-off
  9. Definition of Done / exit criteria + how we'll prove each
- **Discipline:** plans are updated when reality changes — we never let the plan and the work diverge silently. Progress is tracked with tasks during execution.

---

## 2. RULE 2 — Follow a core SDLC framework for every solution

**Commitment:** Every build piece — large or small — moves through an explicit lifecycle. No step skipped.

**The MRI build cycle (applied per block / per sprint):**

| Stage | What happens | Artifact / gate |
|---|---|---|
| **1. Requirements** | Restate the need from the spec + contract; confirm scope boundaries | Section in the sprint plan |
| **2. Design** | Data contract in/out, schema, shared-component plan, interfaces, error/DQ handling | Design notes in the plan; contract diffs reviewed |
| **3. Definition of Ready** | Gates cleared (e.g. G1/G4), inputs exist, fixtures available | Checklist signed in tracker |
| **4. Build** | Implement against contracts; prefer shared libs (§4); small commits | Code + inline docs |
| **5. Test** | Author/extend ALL applicable test types (§5); run the full suite | Green suite; results logged |
| **6. Review** | Self-review + (where relevant) `code-review`; verify behavior, not just types | Review notes |
| **7. Documentation** | Update component docs, schema docs, tracker, runbook (§7) | Docs current |
| **8. Definition of Done** | Exit criteria proven (reconciliations pass, fixtures spot-checked) | Sign-off in tracker |
| **9. Deploy/Activate** | Only after your explicit approval (§6) | Deployment logged |

**Non-negotiable:** we **do not** proceed past a sprint's exit criteria with failing reconciliation/tests (per CLAUDE.md guardrail). A block is "done" only when stages 1–8 are complete and evidenced.

---

## 3. RULE 3 — Centralize shared components & libraries (defined upfront)

**Commitment:** Anything reusable is a shared component with a stable contract, defined *before* sprint code leans on it. No block reaches into another block's internals; everything flows through the gold-table contract and these shared libs.

**Planned shared-library layout (reserved now, populated as sprints arrive):**

```
src/common/
  schemas/        # canonical schema defs for deal / merchant_gold / event_log,
                  #   validated against the Data Contract xlsx (single source of truth)
  field_maps/     # Salesforce -> silver field map (SPRINT_0 mapping as code)
  constants.py    # catalog/schema names (mri.bronze/silver/gold), enums
                  #   (rung, lifecycle_state, deal_type, payment_frequency, balance_source),
                  #   thresholds (renewal default 55%, burden bands, dormancy 2x median, ...)
  dq/             # reusable data-quality rules: 0/blank-as-missing, date-sanity,
                  #   RTR cross-check, *_is_missing flag helpers
  io/             # read/write helpers that ENFORCE "read/write only via the contract"
                  #   + _sf_stored_* no-surface guard
  clock/          # amortization clock (Appendix A)            [home reserved; built S2]
  identity/       # entity-resolution utilities                [home reserved; built S1]
  rung/           # lifecycle gate + rung waterfall (Appendix B)[home reserved; built S3]
  eventlog/       # append-only event emitter                  [home reserved; built S3]
  utils/          # logging, config, date/business-day helpers
tests/
  fixtures/       # the FOUR validation merchants + synthetic edge cases — shared by ALL tests
```

**Upfront principles:**
- **Schema is generated/validated from the Data Contract xlsx**, never hand-maintained in parallel — this prevents drift between the contract and the code.
- **Constants and thresholds live in one place** (`constants.py`) — Appendix A/B numbers are calibration hypotheses and must be changeable in exactly one location.
- **The four validation merchants are canonical fixtures** reused across every test type from S0 onward.
- A new utility used by ≥2 places is promoted to `src/common/` immediately, not copy-pasted.

*Concrete shared components are catalogued and versioned in `docs/SHARED_COMPONENTS.md` as they are built.*

---

## 4. RULE 4 — A consolidated, ever-growing testing framework; test after every build piece

**Commitment:** One master testing document, one consolidated suite, run in full after **every** build cycle. Tests are appended as we go, never abandoned.

- **Master testing document:** `docs/TESTING_FRAMEWORK.md` — defines the strategy, the test types below, how to run the suite, the fixtures, and a per-sprint test register (what was added, current pass/fail, coverage).
- **Test types we maintain (each build adds to the relevant ones):**

  | Type | Purpose |
  |---|---|
  | **Unit** | Individual functions/transforms (e.g. clock arithmetic, a DQ rule) |
  | **Data-quality** | 0/blank-as-missing, date-sanity, null/typing rules produce correct flags |
  | **Data-integrity** | Keys/grain/uniqueness, referential integrity (deal→merchant), no-surface guard on `_sf_stored_*` |
  | **Reconciliation** | Row counts & totals reconcile to source (e.g. silver funded count vs Salesforce) |
  | **Scenario** | The four validation merchants produce expected outcomes (e.g. Starr → defaulted/do-not-fund) |
  | **Integration** | Block-to-block via the contract (bronze→silver→gold→engine) |
  | **E2E** | Full pipeline run end to end on a sample book |
  | **Regression** | The entire accumulated suite re-run every cycle to catch breakage |

  *(Plus any others a sprint warrants — performance, point-in-time correctness for the Feature Store, backtest sanity for models.)*
- **Tooling baseline:** `pytest` + PySpark assertions (e.g. `chispa`) for transforms; declarative data expectations for DQ/integrity; reconciliation as queries; fixtures in `tests/fixtures/`.
- **Cadence:** after each build piece we (a) write/extend the applicable tests, (b) run the **full** suite (regression), (c) log results in `TESTING_FRAMEWORK.md` and the sprint tracker. **A build piece is not "done" until its tests exist and the whole suite is green.**

---

## 5. RULE 5 — Ask before deploying or building anything that needs your decision

**Commitment:** When a choice has real consequences, I stop and ask — I do not assume.

- **Always require your explicit approval before:**
  - Deploying/activating anything (DAB deploy, scheduled jobs, reverse-ETL writes back to Salesforce, any outbound comms).
  - Anything touching shared/external state or that is hard to reverse.
  - Starting a new sprint or expanding scope beyond the current sprint spec.
  - Calibrating/overriding any Appendix A/B threshold (they change classifications).
  - Resolving an ambiguity where the spec is silent or two specs conflict.
- **Decision log:** every decision you make is recorded in `docs/DECISIONS.md` (date, question, options, your choice, rationale) so it's durable and auditable.
- **How I'll ask:** concise, with the options and my recommendation, before any irreversible step — never after.

---

## 6. RULE 6 — Follow best practice to the letter

**Commitment:** Where a recognized best practice exists, we follow it exactly — no shortcuts.

- **Data engineering:** medallion layers (`mri.bronze/silver/gold`); bronze kept raw & immutable; all cleaning bronze→silver; idempotent, re-runnable transforms; Unity Catalog governance + lineage; point-in-time-correct features.
- **Databricks/platform:** native components over hand-rolled (Lakeflow Connect, Feature Store, Lakebase, Model Serving) per the spec; packaged as Databricks Asset Bundles with `dev`/`prod` targets; config-driven, no hardcoded secrets/paths.
- **Code:** PySpark/SQL for transforms, Python for logic; typed, small, single-responsibility functions; shared logic centralized (§3); no secrets in code; deterministic over clever.
- **Security & compliance:** least privilege; PII fields handled per the contract's compliance codes (PII/REG/CONSENT); the compliance gate is real architecture (never design around it); honesty constraint enforced (outputs grounded only in computed numbers).
- **Source control:** small reviewable commits; meaningful messages; never bypass hooks; never force-push shared branches; nothing committed that contains secrets.
- **The static-vs-live guardrail is sacred:** never surface/trust stored `Remaining Balance` / `Percentage Paid` / `Estimated Renewal Date` — recompute (CLAUDE.md §2.1, Appendix A).

---

## 7. RULE 7 — Thorough documentation, always

**Commitment:** We document what we build, as we build it, and keep it current.

- **Documentation set (kept live):**
  - `SPRINT_TRACKER.md` — status, decisions, results per sprint.
  - `docs/sprints/SPRINT_<n>_PLAN.md` — the detailed plan per sprint.
  - `SHARED_COMPONENTS.md` — catalogue of shared libs + their contracts.
  - `TESTING_FRAMEWORK.md` — strategy + test register + how to run.
  - `DECISIONS.md` — decision log.
  - `docs/RUNBOOK.md` — how to run pipelines/jobs/tests locally and in Databricks.
  - **Schema docs** — generated from / validated against the Data Contract xlsx.
  - **Inline docs** — only where the *why* is non-obvious (per CLAUDE.md style); the heavy narrative lives in these docs, not in code comments.
- **Standard:** every block ships with: what it does, its input/output contract, how to run it, how it's tested, and any decisions/assumptions behind it. Docs are updated in the same change as the code (SDLC stage 7) — never left for "later."

---

## Quick checklist (apply to every build cycle)

- [ ] Detailed plan written & approved; tracker updated *(Rule 1)*
- [ ] SDLC stages 1–8 followed; nothing skipped *(Rule 2)*
- [ ] Reusable logic placed in `src/common/`; contracts stable *(Rule 3)*
- [ ] All applicable tests written; **full suite green**; results logged *(Rule 4)*
- [ ] Any decision/deploy paused for your explicit sign-off *(Rule 5)*
- [ ] Best practices followed to the letter; guardrails honored *(Rule 6)*
- [ ] Documentation updated in the same change *(Rule 7)*
