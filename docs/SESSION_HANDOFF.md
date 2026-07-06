# Session Handoff — Morgan Cash MRI

**Last updated:** 2026-06-06 (S7 Phase-2 D-702 spike). This is the fast "where are we, what's next" page for the next session. Authoritative detail lives in `SPRINT_TRACKER.md` (status), `DECISIONS.md` (the C-/D-/FU- ledger), `SHARED_COMPONENTS.md`, `TESTING_FRAMEWORK.md`, `RUNBOOK.md`.

---

## Latest (2026-06-06) — S7 Phase-2 D-702 spike done; **awaiting sign-off on D-707…D-713**

Kicked off **S7 Phase-2 (Statement Analyst)** with the **D-702 statement-ingestion spike** —
desk/scoping only, **nothing in the cloud touched** (Rule 5). Deliverable:
[`docs/D-702_statement_ingestion_spike.md`](D-702_statement_ingestion_spike.md). Findings:
- S0 ingested **no** file/attachment object → statements are outside the lakehouse (confirmed in `resources/ingestion_pipeline.yml`). Candidate SF stores: **ContentVersion/ContentDocumentLink** (most likely) and/or legacy **Attachment**.
- Scoped the bronze→silver→gold path: Lakeflow ingests **metadata** + a **gated read-only fetch** lands **binaries** in a UC Volume + deterministic silver **OCR** → agent extracts to `gold.merchant_extraction`. **No extraction-schema change needed** (the `concurrent_positions`/`weekly_debit`/`est_weekly_revenue` types + generic `value` already exist).
- Labeled sample designed to mirror the D-706 gate (Wolf central; positions ±1; burden/revenue ±10%; permanent tier-2 gate).
- Opened **D-707…D-713**; **signed off as "ok go" → C-025.**
- **Read-only metadata probe RUN 2026-06-06** (C-025 option A — metadata-only `ContentDocument` ingest to `bronze_test`, **no blobs**, fully torn down). **Salesforce Files is the store: 24,285 files / 13.4 GB; 23,700 PDF (97.6%) + ≈467 images; ≥7,552 clearly bank statements, mostly 2024** → Phase 2 well justified. Build must handle title-undercount, OCR (scanned/image), and ZIP/multi-month bundles ([spike §1.5](D-702_statement_ingestion_spike.md)).
- **One open number: per-deal coverage %** — `ContentDocumentLink` bulk ingest is blocked by least-privilege (correct); linkage needs `ContentVersion.FirstPublishedLocationId` (bulk, blob-pull risk) **or** per-record REST in the fetch job (a D-707 sub-decision to settle before sizing the build).
- **Offline deterministic foundation BUILT 2026-06-06** (no cloud): `common/agents/positions.py` — the Statement Analyst's counter (`normalize_to_weekly` reuses the S2 clock; `concurrent_position_count` excludes Morgan Cash's own so the clock ADDS other-funder positions, D-710; `total_weekly_debit`; `est_weekly_revenue`; `summarize_statement`). **9 new tier-1 → 311 green.**
- **Coverage RESOLVED (2026-06-07/09, spike §1.6):** the connector auto-drops base64 (verified), so `ContentVersion` metadata was safely probed (torn down after). Linkage = `FirstPublishLocationId` → `a0o` submission → `Opportunity.Application_Submission__c`; `Document_Type__c` tags 998 "Bank Statement" files. **Coverage: 79 funded deals (2.0% of book; 28.4% of 2025+ — capture started ~2025); 35 active merchants. Wolf has 5 statement files; Starr none. Fetch scope = 784 files / 0.42 GB (324 statements / 0.17 GB)** — trivial cost. **Re-scope: Phase 2 = go-forward capability + ~35-active-merchant enrichment, not a retroactive fix.** New **FU-702**: upstream ops fixes (populate the submission link; tag doc types at intake) multiply coverage free.
- **Re-scoped build APPROVED + burden policy resolved → C-026 (2026-06-09):** **#1 advisory-only** (statement burden/positions surfaced but the rung waterfall keeps ignoring burden — no asymmetry; overrides D-710's "rung re-runs sharper"; the agent never touches the spine); **#2 freshness** (`as_of_date` + `STATEMENT_FRESHNESS_MAX_DAYS`, stale → REVIEW); **#3 revenue softness** (operating-revenue-only + confidence haircut + looser D-711 tolerance).
- **Offline agent layer BUILT 2026-06-09 (no cloud):** `common/agents/statement_analyst.py` (fuzzy half, injected `predict_fn`, → 3 grounded extractions) + `positions.statement_is_fresh` + constants. **17 new tier-1 → 319 green.** The whole Phase-2 OFFLINE foundation (deterministic counter + agent half) is done and tested; nothing committed.
- **SF API fetch path PROVEN (2026-06-09):** stood up a headless **Client Credentials Flow** on the existing "Databricks Ingestion" External Client App (Run-As = `integrations@morgancash.com`, **API Enabled + View All Data** read-only) + a Databricks secret scope **`mri-salesforce-api`** (`client_id`/`client_secret`). Read-only validity test: token minted → `ContentVersion` PDF `VersionData` downloaded **200, 3,317,312 bytes, size-matched, no 403** → the whole SF→UC fetch mechanism works.
- **⚠️ FU-703 (security follow-up):** during secret entry the Consumer Key/Secret were briefly stored as plaintext key *names* (internal exposure only — own scope + local session, NOT public/committed; cleaned up immediately). **Rotate the Consumer Secret** + re-auth the ingestion connection (same app), or move the fetch to a dedicated app. Near-term cleanup.
- **Freshness window CONFIRMED 180d (2026-06-09):** age-distribution probe showed covered-statement median age 333d → at 180d only **18 of 79** covered deals surface as current burden; the other 61 are extracted + recorded but flagged stale (REVIEW). Operator chose strict (honesty over pilot breadth). `STATEMENT_FRESHNESS_MAX_DAYS=180` unchanged.
- **Binary fetch DONE 2026-06-09 (real PII landed, D-712):** `src/ingestion/statement_fetch.py` (pure helpers + driver, +4 tier-1 → **323 green**) ran via the Client-Credentials token → **324 statement PDFs / 167.9 MB** downloaded into governed UC Volume **`mca_mri.bronze.statements_raw`** + **`bronze.statement_files`** metadata (79 covered deals). Raw bronze, immutable; Volume UC-governed (restrict grants). Linkage via `FirstPublishLocationId`→`Application_Submission__c`; identification via `Document_Type__c='Bank Statement'` ∪ title heuristic.
- **Silver OCR DONE → `silver_test.statement_text` (2026-06-09, confirmed by live audit):** `transform/silver_statement_text.py` (pdfplumber) ran over the 324 PDFs — **317 digital text OK / 7 needs_ocr (scanned/image) / 1 parse_error**. So ~98% yielded usable text; the 7 need a true-OCR escalation (`ai_parse_document`) later. *(The job was orphaned by a session teardown but the audit shows it completed.)* **`silver_statement_text.py` is still UNCOMMITTED on the branch.**
- **Live audit (2026-06-09) — all green:** Volume 324 PDFs; `bronze.statement_files` 324/79 deals; PROD spine intact (deals 3959, merchants/clock/rung/activation/queue/predictions all 2125; `merchant_extraction_current` 6 = 5 applied/1 review). Secret scope keys present. (A stray `main.default` "New Pipeline" exists but belongs to harshit.g@ — not ours, left alone.)
- **Statement extraction BUILT + tier-2 PASSED on `gold_test` (2026-07-04, `failures: []`):** `transform/gold_statement_extraction.py` (Spark driver, reuses the Data Steward's FM caller + write helpers; **D-714** = most-recent statement per deal) + `tests/tier2/recon_statement_analyst.py` (mechanical gates + the D-711 accuracy-gate harness). Ran the Claude FM over all **79 covered deals → 237 extraction rows** (3/deal): schema/coverage/keys/grounding/no-surface/`_current` all green; **16 APPLIED / 221 REVIEW / 0 REJECTED**. **6 deals surfaced APPLIED concurrent-positions** (distribution 1×1, 2×1, 3×2, 4×2 — i.e. merchants with **3–4 concurrent positions incl. other funders the spine can't see**). Fewer than the June fresh-18 because run_date drifted ~1mo (statements aged past 180d) — reinforces this is a **go-forward** capability. **D-711 gate is a no-op until the operator fills `STATEMENT_LABELS`.**
- **Reproducibility CONFIRMED (2026-07-04):** re-extracted the 6 APPLIED deals 3× each — **position count stable (spread 0) on all 6**, weekly debit identical, revenue wobble <1% (inside ±15%). **No majority-vote needed.** (The earlier A1a "1 vs 0" was a one-off; the stable answer is 0 — no MCA advance debits, all utilities/card/owner.) Stability ≠ correctness → the labeled sample still validates (e.g. Tom Snell's stable "4", Wolf's $181k/wk revenue).
- **JSON persistence ADDED (`statement_extraction_audit`):** `build_statement_extractions` now returns `{rows, audit}`; the driver writes a `gold.statement_extraction_audit` table (per-statement positions_json breakdown + deposits + period + confidence + citation) so "which statement numbers" is answerable without re-running. +1 tier-1 → **324 green**. *(Populated on the next gold_test run.)*
- **Next:** (a) re-run `gold_test` to populate the audit table (also corrects A1a `1`→`0`); (b) **D-711 labeled sample — needs the operator**: positions + monthly debit + monthly revenue for ~6–10 deals incl. Wolf (I surface the audit breakdown, not raw text) → arm the gate → re-run → PROD on approval. (c) dashboard surfacing (advisory). **FU-702** (ops, deferred), **FU-703** (rotate secret) open.

---

## Where we are (2026-06-05)

S0–S7 Phase-1 are **in PROD `gold`** (`mca_mri.gold`). The spine is complete and the first agentic layer is live.

| Sprint | State |
|---|---|
| S0 Ingestion → silver | ✅ PROD |
| S1 Identity + Deal table | ✅ PROD `gold` |
| S2 Features + amortization clock (Appendix A) | ✅ PROD `gold` |
| S3 Rung classifier + state machine + event log (Appendix B) | ✅ PROD `gold` |
| S4 Activation + Book Health + daily queue | ✅ PROD `gold` |
| S5 Offer Engine | ◑ offline modules built + tier-1 green; **MRI-side cloud build gated on FU-501** (handoff contract with the routing team) |
| S6 Prediction | ✅ PROD `gold` (lifetimes BG/NBD + Gamma-Gamma + lifelines Cox; **FU-602** PyMC Bayesian upgrade open) |
| **S7 Phase-1 Data Steward (agentic extraction)** | ✅ **PROD `gold`** — D-706 labeled accuracy **6/6**, `failures: []` (C-024) |
| Activation surface | ✅ read-only Lakeview dashboard over PROD `gold` (C-021); dashboard_id `01f160f949351871b77829f9bf12c942` |

**Tests:** 302 tier-1 green (`python -m pytest -q`). Tier-2 recons run on Databricks (`tests/tier2/`), `gold_test` first then PROD with `allow_prod=True` (RUNBOOK).

## What just shipped (this session — S7 Phase-1)
The **Data Steward** (Framework §5.9 — *agents EXTRACT, the spine COMPUTES*): a Databricks Foundation Model (`databricks-claude-sonnet-4-5`) reads each defaulted deal's free-text `silver.deals.notes`, proposes a default-cause label; deterministic tier-1-tested tools (`common/agents/`) gate + ground it; the S3 classifier re-runs and routes on the APPLIED result. The agent never writes a spine table — only `gold.merchant_extraction` (+`_current`) + `agent_extraction` events.
- **Outcome:** 5 of 6 previously-`unknown` defaults resolved → distressed-exit (incl. **Starr**, whose "$250 clawback" on a defaulted deal = `true_default`, NOT early-payoff); 1 (Zeek, no cause signal) correctly abstained → human review.
- **D-706 accuracy gate** is permanent in `tests/tier2/recon_extraction.py` (`DEFAULT_SUBTYPE_LABELS`, `ACCURACY_BAR=0.80` + a regression guard) — it guards PROD on every future run. Operator-confirmed labeling policy (C-024): an explicit "defaulted" note = `true_default`; a clawback on a defaulted deal = `true_default`; no cause signal = abstain (`unknown`).
- **FU-701** fixed en route: `gold_rung.compute_event_log` now null-fills missing wide-log columns before the schema projection.

## Key files (S7)
- `src/common/agents/{default_subtype,grounding,data_steward}.py` — deterministic tools + the fuzzy half (prompt + parser + pure `build_extraction_rows` orchestration; injected `predict_fn`, tier-1 testable).
- `src/transform/gold_extraction.py` — Spark driver + `databricks_chat_predict_fn` (FM via `databricks-sdk` serving client — serverless has no `mlflow`).
- `tests/tier2/{recon_extraction,run_tier2_extraction}.py` — tier-2 recon + D-706 gate.
- `src/transform/gold_rung.py` — reads APPLIED `resolved_default_subtype` (backward-compatible).

---

## Recommended next steps (pick one)

1. **S8 — Advisory comms + the remaining agents (Composer, Structure Advisor) + compliance gate.** The next major sprint per the roadmap. The spine + Data Steward give it grounded facts to articulate; the compliance gate (state-aware disclosure, advice-vs-specific-offer) is first-class architecture (Framework §2.4). Decisions to open first (D-801…).
2. **S7 Phase-2 — Statement Analyst.** Highest-value remaining *extraction* work: OCR + transaction-line classification of bank-statement PDFs → true concurrent positions + total weekly burden → feed `active_position_cnt`/`burden_ratio`/`est_weekly_revenue` (advances FU-301). **Gated on D-702** — the statement-ingestion spike (statements confirmed to live in SF Documents/attachments; needs an ingestion path + a labeled sample, mirroring the D-706 pattern that worked well for the Data Steward).
3. **Close an open follow-up:** FU-501 (offer handoff contract with the routing team → unblocks the S5 cloud build), FU-602 (PyMC Bayesian upgrade — needs interactive ML-runtime debugging; lifetimes v1 is in prod), FU-401 (governed Salesforce write-back, sandbox-first).

**Suggested default:** kick off the **D-702 statement-ingestion spike** to scope S7 Phase-2 — it's the natural continuation of the agentic extraction work and reuses the now-proven grounding + labeled-sample + accuracy-gate pattern. If you'd rather build breadth than depth, **S8** is the bigger roadmap step.

## Binding working rules (do not drift — see `GENERAL_INSTRUCTIONS.md`)
- **Plan first** (no code without an approved plan); SDLC stages; **ASK before any cloud build/deploy** — build on `*_test` first, PROD needs explicit approval + `allow_prod=True` (Rule 5).
- Shared logic lives **once** in `common/` (Rule 3 — no duplicate numbers/logic); maintain + run tier-1/tier-2 tests every cycle; thorough docs in the same change.
- **Never surface/trust** SF stored `Remaining Balance` / `Percentage Paid` / `Estimated Renewal Date` (`_sf_stored_*` no-surface guard). Recompute everything time-dependent daily (Appendix A).
- **Secrets never touch chat/commits/notebooks/shell.** Git: commit/push only when asked; branch first if on default; commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Databricks CLI via **PowerShell** (Git Bash mangles `/Workspace` paths); `--json @file` written UTF-8-no-BOM; Foundation Models via the `databricks-sdk` serving client on serverless.

## Environment quick-reference
- Catalog `mca_mri`; schemas `bronze`/`silver`/`gold` (+ `gold_test` mirror). Point-in-time gold tables keyed `(merchant_id, <run>_date)`, append-only, idempotent via `replaceWhere`, with `_current` views.
- Tier-2 staging dir: `/Workspace/Users/venkat@morgancash.com/mri_tier2` (deleted after each run). Starter Warehouse id `526a06bbae2df35b`. FM endpoint `databricks-claude-sonnet-4-5` (verify: `databricks serving-endpoints list`).
- Four validation merchants (Framework §8): Starr (now true_default→distressed-exit), One Big Promotion (dormant→win-back), Tom Snell (new/establishing), Wolf (serial rapid re-up).
