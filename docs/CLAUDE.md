# CLAUDE.md — Morgan Cash Merchant Retention & Advisory Intelligence (MRI)

This file is the always-on context for building the MRI system. Read it fully before any work. It encodes principles that are easy to get wrong and expensive to undo.

---

## 1. What we are building (one paragraph)

An always-on merchant intelligence and advisory engine for Morgan Cash (an MCA brokerage). It classifies every funded merchant onto a behavioral health "rung," predicts when each will next need capital, and drives honest, timely advisory outreach — so Morgan Cash becomes the merchant's trusted capital advisor and retains every deal as the default. The differentiation is the intelligence + advisory layer, NOT the CRM. Operational plumbing is bought/reused; we build the brain.

**Companion documents (authoritative — do not contradict them):**
- `Framework Specification` (the what/why: rungs, archetypes, architecture)
- `Gold-Table Data Contract` (xlsx — the schema every layer reads/writes; field-by-field source, verdict, audience, compliance)
- `Build Plan & Sprint Roadmap` — including **Appendix A (amortization clock math)** and **Appendix B (rung classification ruleset)**. These two appendices are the exact arithmetic and logic. Implement them literally.

---

## 2. Non-negotiable principles

### 2.1 Static-at-funding vs live-recomputed (THE core principle)
Salesforce captures each deal **at funding** and does **not** keep it current.
- **Ingest as-is** the static *terms*: Funded Amount, Rate (factor), Payment Amount, Frequency, Number of Payments, Funded Date, Payback Amount. These never change post-funding.
- **NEVER trust or surface** Salesforce's stored `Remaining Balance`, `Percentage Paid`, or `Estimated Renewal Date`. They are frozen snapshots and were **wrong on all four** real deals we inspected (e.g. "100% paid" on a defaulted deal; "75% paid" AND a contradictory balance on a same-day deal). **Recompute everything time-dependent daily** from the static terms + today's date (see Appendix A).
- This live-recompute layer is the system's core value over the system of record.

### 2.2 Single source of truth
The **gold merchant-feature table** is the one place every layer reads from and writes to. No block reads another block's working data. Get its grain right (per-merchant, point-in-time, fully-derived, keyed on canonical merchant identity).

### 2.3 Honesty as an engineering constraint
Any merchant-facing output is grounded ONLY in computed numbers, never invented. Advice may legitimately say "don't take money" or "wait and pay down." A matchable offer (e.g. a buyout) may still be unsuitable (double-dip) and must not be auto-pitched. The Offer Engine *proposes*; the advisory + compliance layers *dispose*.

### 2.4 Compliance gate is real architecture
State-aware disclosure rules and "advice vs. specific-offer" gating are a first-class block, not an afterthought. (Not in Sprint 0, but never design around it.)

### 2.5 Data-quality realism (from 5 real deals)
- Field population is inconsistent. Treat **0 and blank as MISSING** where 0 is implausible (Months in Business, FICO, revenue) — never as a real value.
- Dates can contradict each other (one deal: Funded Date 2020 vs Date Created 2022). Validate date sanity; flag contradictions rather than computing confidently-wrong values.
- `Type` (New Business / Renewal / Buyout) IS reliable — trust it for the renewal flag.
- The **Merchant → Opportunity** linkage already exists in Salesforce (each Opportunity = one advance, grouped under a parent Merchant). Validate/extend it; don't rebuild from scratch.
- **Opportunity Field History** (Stage transitions + timestamps) is a usable event source and lets us reconstruct real renewal cadences.

---

## 3. Architecture (modular "lego blocks")

Bottom-up. Each block reads/writes the gold table via stable contracts; any block is swappable.

1. **Ingestion & Lakehouse** — Salesforce → bronze (CDC) → silver (cleaned/typed). *(Sprint 0)*
2. **Identity Resolution + Deal Table** — canonical merchant; deal grain. *(Sprint 1)*
3. **Feature Layer + Amortization Clock** — derived features; balance/paydown/eligible-date (Appendix A). *(Sprint 2)*
4. **Rules-Based Rung Classifier + State Machine + Event Log** — Appendix B. *(Sprint 3)*
5. **Activation** — reverse ETL to Salesforce + Book Health analytics. *(Sprint 4)*
- **Offer Engine** (reuse existing routing IP, run proactively) *(Sprint 5)*
- **Prediction** (PyMC-Marketing BG/NBD + lifelines Cox) *(Sprint 6)*
- **Advisory comms + compliance gate** *(Sprint 7)*
- **Merchant app** (renderer over Lakebase) *(Sprint 8+)*

Cross-cutting: **event log** (from Sprint 3), **Unity Catalog** governance/lineage (from Sprint 0).

**Agentic layer (later sprints only):** AI agents do the fuzzy, tool-using work the spine cannot — they **articulate, extract, orchestrate**, and call deterministic tools for anything that must be correct. They NEVER compute the spine (clock, classifier, burden/eligibility, compliance, models stay deterministic and auditable). Four agents: Statement Analyst + Data Steward (S7, extraction), Advisory Composer + Structure Advisor (S8, articulate/orchestrate). All grounded in computed facts, logged, and gated by compliance. No agents before the spine exists.

---

## 4. Tech stack (use native components; don't hand-build what exists)

- **Platform:** Azure + Databricks, Unity Catalog (governance/lineage).
- **Ingestion:** Lakeflow Connect (native Salesforce connector, managed CDC). Do NOT hand-roll a Bulk-API pipeline.
- **Feature store:** Databricks Feature Store (the gold table is registered here). Feast is reference only.
- **Serving (later):** Lakebase (managed Postgres synced to Delta) for floor queue + app reads.
- **ML (later):** MLflow + Model Serving + inference tables (doubles as event log + audit trail). PyMC-Marketing (BG/NBD, Gamma-Gamma, CLV) and lifelines (Kaplan-Meier, Cox) — adopt, don't build.
- **Packaging:** Databricks Asset Bundles (DABs).
- **Language:** PySpark / SQL for transforms; Python for logic.

---

## 5. Repo structure

```
morgan-cash-mri/
  databricks.yml            # DAB config (targets: dev / prod)
  CLAUDE.md                 # this file
  src/
    ingestion/              # Lakeflow Connect config; bronze landing
    transform/              # silver cleaning/typing; schema definitions
    common/                 # shared utils, constants, field maps
  resources/                # job & pipeline (workflow) definitions
  tests/                    # reconciliation + data-quality tests
  docs/                     # pointers to Framework, Contract, Build Plan
```

Medallion layers in Unity Catalog: `mri.bronze.*`, `mri.silver.*`, `mri.gold.*`.

---

## 6. Guardrails — what NOT to do

- Do NOT surface or trust stored `Remaining Balance` / `Percentage Paid` / `Estimated Renewal Date`. Recompute. (Keep them only as a funding-day checkpoint for validation.)
- Do NOT build ML, the rung engine, the clock, identity resolution, the Offer Engine, comms, or the app in Sprint 0. Sprint 0 is ingestion → silver ONLY.
- Do NOT treat 0/blank as a real value where 0 is implausible.
- Do NOT build authentication, bank-linking, or any merchant-facing surface (app is S8+).
- Do NOT rebuild the funder-criteria dataset or routing engine — they exist and are reused (Offer Engine, S5).
- Do NOT proceed past a sprint's exit criteria without the reconciliation/tests passing.

---

## 7. Build order & current status

| Sprint | Block | Status |
|---|---|---|
| **S0** | Ingestion → silver | **CURRENT** — see `SPRINT_0.md` |
| S1 | Identity + Deal table | next |
| S2 | Features + clock (Appendix A) | |
| S3 | Rung classifier + state machine + event log (Appendix B) | |
| S4 | Activation + Book Health | |
| S5 | Offer Engine | |
| S6 | Prediction | |
| S7 | Agentic extraction (Statement Analyst + Data Steward) | |
| S8 | Advisory comms + agents (Composer, Structure Advisor) + compliance | |
| S9+ | Merchant app | |

**Gates before S0 coding:** G1 data audit (in progress), G2 clock math (RESOLVED — Appendix A), G3 rung rules (RESOLVED — Appendix B), G4 environment + data-rights (confirm).

---

## 8. Four real validation merchants (use as test fixtures throughout)

| Merchant | Shape | Expected classification |
|---|---|---|
| Starr Window Tinting | FICO 520, Position 4, defaulted ($250 clawback) | Defaulted → sub-type (do-not-fund + review) |
| One Big Promotion | Paid 100%, single deal, quiet since 2020 | Dormant → win-back |
| Tom Snell | 1 fresh deal, clean, full docs, no history | New/establishing → healthy clock-running |
| Wolf Corporation | Renewed ~14 days in, $30k→$40k upsizing | Serial (rapid re-up) → renewal-vs-buyout eval |

These are reference outcomes for S3, but carry them as known cases from S0 onward (their deal records are the first thing to ingest and eyeball).
