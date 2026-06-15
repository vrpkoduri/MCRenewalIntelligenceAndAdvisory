# D-702 — Statement-Ingestion Spike + S7 Phase-2 (Statement Analyst) Plan

**Status: spike (desk) complete; plan open — awaiting sign-off on D-707…D-713.** Nothing in the
cloud has been touched (Rule 5). This document is the deliverable for "START S7 Phase-2 with the
D-702 spike, then STOP for approval."

It (1) confirms what we know — and don't yet know — about where bank-statement PDFs live, (2)
scopes a read-only ingestion path into bronze/silver, (3) defines the labeled-sample design that
mirrors the D-706 pattern that worked for the Data Steward, and (4) opens the decisions
(D-707…D-713) you need to sign before any build/cloud run.

> **Read alongside:** [`SPRINT_7_PLAN`](sprints/SPRINT_7_PLAN.md) (the parent sprint plan; D-702 is
> the gate on Phase 2), [`DECISIONS`](DECISIONS.md) (C-022/C-023/C-024; D-707… opened here),
> [`SESSION_HANDOFF`](SESSION_HANDOFF.md).

---

## 0. The §5.9 contract this must honor (unchanged)

The agent **EXTRACTS** (OCR + classify messy transaction lines — the unpredictable-path work);
the deterministic tool in `common/agents` **COUNTS** (concurrent positions + sums weekly debit +
sums deposits); the spine **COMPUTES** (the clock folds the new signals into `burden_ratio`, the
rung re-runs). Every extraction is **grounded** (statement file + page/line), **gated**
(confidence → `review_status`), **logged** (`agent_extraction` event + `model_version`), and the
agent **never writes a spine-math column**. This is exactly the mechanism Phase 1 proved.

---

## 1. The spike — where do the statements live?

### 1.1 What S0 ingested (confirmed, in repo)

The live Lakeflow Connect pipeline `mri_sf_ingest_bronze`
([`resources/ingestion_pipeline.yml`](../resources/ingestion_pipeline.yml)) ingests **four
structured objects only**: `Account`, `Opportunity`, `OpportunityFieldHistory`, `Offer__c`.
**No file/attachment object is ingested.** So bank statements are, today, entirely outside the
lakehouse — confirmed by code, not assumption.

### 1.2 Where Salesforce keeps per-deal files (the candidates)

"SF Documents/attachments" (the colloquial phrasing in the handoff) resolves to one of three
concrete Salesforce storage models. The real candidates for per-deal bank statements:

| Model | Objects | Binary field | Link to a deal | Likelihood |
|---|---|---|---|---|
| **Salesforce Files** (modern, default last decade) | `ContentDocument` (logical file) · `ContentVersion` (each version; the bytes) · `ContentDocumentLink` (junction) | `ContentVersion.VersionData` (base64) | `ContentDocumentLink.LinkedEntityId` → Opportunity/Account Id | **High** — the default for application-bundle uploads |
| **Classic Attachments** (legacy) | `Attachment` | `Attachment.Body` (base64) | `Attachment.ParentId` → record Id | **Medium** — common on older funded deals |
| **Documents tab** (folders) | `Document` | `Document.Body` | folder, **not** record-linked | **Low** — usually templates/static assets, not per-deal statements |

The useful metadata fields, by model:
- `ContentVersion`: `Id, ContentDocumentId, Title, FileExtension, FileType, ContentSize,
  PathOnClient, CreatedDate, IsLatest`.
- `ContentDocumentLink`: `ContentDocumentId, LinkedEntityId, ShareType, Visibility`.
- `Attachment`: `Id, ParentId, Name, ContentType, BodyLength, CreatedDate`.

### 1.3 What the spike can and cannot conclude without a cloud read

**Concluded from the repo (no cloud needed):** statements are NOT in the lakehouse; the ingestion
must add a new source; the candidate objects are ContentVersion/ContentDocumentLink (most likely)
and/or Attachment.

**Requires a gated, read-only cloud probe (NOT run — Rule 5):** the *actual* object that holds
them, per-deal **coverage** (% of funded Opportunities with ≥1 statement), **file-type** mix (true
text PDFs vs scanned-image PDFs needing OCR vs xls/csv), **volume** (file count + total GB), and
the **identification rule** (how a "bank statement" is told apart from a signed contract, a
driver's license, a voided check, etc. — by `Title`/`PathOnClient` keyword? a doc-type/category
field? the related list it sits in?). These four numbers drive cost, OCR strategy, and whether
Phase 2 is worth building at scale — so the first gated step is the probe below.

### 1.4 The gated read-only probe (scoped here, run only on approval)

A read-only spike against the existing `mri_salesforce` UC connection (no new credentials, no
writes). Conceptually (SOQL — exact API names confirmed at run):

```sql
-- A. Is the modern Files model in use, and how is it linked to the funded book?
SELECT COUNT(Id), FileType FROM ContentVersion WHERE IsLatest = true GROUP BY FileType
SELECT COUNT(Id) FROM ContentDocumentLink WHERE LinkedEntityId IN (:funded_opportunity_ids)
-- B. Title patterns — how do we IDENTIFY a bank statement?
SELECT Title, FileType, ContentSize, CreatedDate FROM ContentVersion
  WHERE IsLatest = true AND (Title LIKE '%statement%' OR Title LIKE '%bank%' OR Title LIKE '%MTD%')
-- C. Legacy attachments still present?
SELECT COUNT(Id), ContentType FROM Attachment GROUP BY ContentType
-- D. Coverage: of N funded Opportunities, how many have ≥1 candidate statement file?
```

**Outputs the probe must return** (the spike's real exit criteria): object in use; coverage %
across the funded book; file-type distribution; an inventory of `Title`/`PathOnClient` patterns we
can turn into a deterministic "is-a-bank-statement" classifier; total file count + GB (cost input).
The four validation merchants (esp. **Wolf** — serial/rapid-reup, where TRUE concurrent positions
are the entire point — and **Starr**) are eyeballed by name to confirm their statements exist.

### 1.5 Probe RESULTS (run 2026-06-06 — metadata-only, read-only; C-025 option A)

Ran a throwaway metadata-only Lakeflow ingest of **`ContentDocument`** (+ attempted
`ContentDocumentLink`) into `bronze_test` — **no `ContentVersion`/`Attachment`, so zero PDF blobs /
PII were pulled** — queried, then **fully torn down** (pipeline + both tables + staging deleted).

**Salesforce Files is the store, and the statement corpus is large, recent, and overwhelmingly PDF:**

| Metric | Value |
|---|---|
| Total files (`ContentDocument`) | **24,285** · **13.4 GB** |
| File types | **PDF 23,700 (97.6%)** · PNG 204 · JPEG 182 · JPG 81 (≈467 images → scanned, need OCR) · ZIP 34 · CSV 36 · DOCX/XLSX/HTML/TXT/RTF/etc. small tails |
| Titles matching `statement\|bank\|mtd\|deposit\|checking` | **7,552** (a *floor* — see caveat) |
| Sample titles (clearly bank statements) | "December 2024 Monthly Statement", "DepositAccountStatements_0149_2024-12…", "STMT_Frost Business Checking_2928_20241031", "ES_DDA0048_Checking_Statement_S12312024…", "Bank-Statement-8106_October-2024.pdf", "103124 WellsFargo", "BusBasic_…_Account_Statement_November_2024…" — mostly **2024-dated** (recent / funding-moment) |

**Verdict: statements exist in abundance, in the right format (PDF), and recent → Phase 2 is well
justified.** Caveats the build must handle (all visible in the title sample):
- **Title keywords undercount.** Many statements have non-obvious titles ("Sept", "PRONOV",
  "download", "eStmt_2024-12-31", "113024 WellsFargo"). The deterministic `is_bank_statement`
  classifier (D-707) needs **filetype + date-pattern + linked-deal context**, not titles alone —
  true count is **≥ 7,552**.
- **OCR is genuinely required** (D-708): ≈467 image files + an unknown share of *scanned* PDFs.
- **Bundling:** 34 ZIPs and multi-month single PDFs ("…Oct-Dec.pdf", "2024-monthly-statements.zip")
  → the ingestion must unbundle / split multi-statement files.

**One question the probe could NOT answer — per-deal coverage %.** `ContentDocumentLink` (the
file→Opportunity junction) **failed to bulk-ingest**: `SFDC_CONNECTOR_CREATE_BULK_QUERY_JOB_FAILED …
ensure the authenticated user has 'View all Data'`. This is **correct least-privilege behavior** —
the read-only integration user (rightly) lacks "View All Data", and Salesforce restricts bulk SOQL
on `ContentDocumentLink`. So linkage cannot come from a bulk CDC table. **Two resolution paths
(a D-707 sub-decision):**
1. **`ContentVersion.FirstPublishedLocationId`** — bulk-queryable, points to the record a file was
   first uploaded to (often the Opportunity/Account); gives linkage without ContentDocumentLink. But
   `ContentVersion` carries the `VersionData` blob → **blob-pull risk** unless the connector can omit
   that column. *Needs a careful metadata-only test before trusting it.*
2. **Per-record REST** — `ContentDocumentLink` *is* queryable via REST when filtered by
   `LinkedEntityId`; the gated binary-fetch job (D-707) can resolve linkage per funded Opportunity at
   the same time it pulls the PDFs. (Requires an SF API path that exposes a token; the Lakeflow UC
   connection does not expose one to notebooks — a small dependency to settle.)

Coverage % is therefore the **one open number** before sizing the build; everything else (exists,
format, recency, volume, identification approach) is now answered.

### 1.6 Coverage RESULTS (run 2026-06-07/09 — `ContentVersion` metadata probe; closes the open number)

The web-doc check confirmed the Lakeflow Salesforce connector **auto-drops `base64` columns**
(`VersionData` never lands — verified in the probe: `versiondata_column_present: false`), so
`ContentVersion` **metadata** was safely ingested to `bronze_test` (then fully torn down:
pipeline + table + staging deleted). Three probe runs resolved linkage + coverage:

**Linkage (better than expected, two upgrades on §1.5):**
- `ContentVersion.FirstPublishLocationId` (note: no "ed") resolves the file→record link
  **without** the blocked `ContentDocumentLink` — prefix breakdown: **`a0o` (Application_Submission custom object) 22,716** ·
  `006` (Opportunity) 1,111 · `005` (User) 413. The real chain is
  **file → `a0o` submission → `Opportunity.Application_Submission__c` → funded deal** (the same
  sparsely-populated link C-014 found: 17.8% overall).
- **`ContentVersion.Document_Type__c`** is a structured doc-type field: **998 files tagged
  "Bank Statement"**, 227 "Signed Application", rest null — a high-precision identifier; the
  title heuristic adds recall (union = **8,146 statement files** org-wide).

**Coverage (the honest re-scope finding):** statement capture **started ~2025** — every
statement-linked funded deal is a 2025+ funding.

| Cohort | Deals | With statement | Coverage |
|---|---|---|---|
| Whole funded book | 3,959 | 79 | **2.0%** |
| 2024+ fundings | 684 | 79 | 11.6% |
| **2025+ fundings** | 278 | 79 | **28.4%** |
| **Active book** (clock `active`) | 894 deals / 651 merchants | 38 deals / **35 merchants** | 4.3% |

Constraint chain: `Application_Submission__c` populated on only 704/3,959 funded deals (but
**154/278 = 55% of 2025+**, and every linked 2025+ deal has files) → the bottleneck is the
upstream link + tagging, not file existence. **Wolf has 5 statement files** (labeled-sample
anchor ✓); Starr has none (older funding).

**Fetch scope collapses** (D-713 cost input): the build pulls only funded-linked files —
**784 files / 0.42 GB total; 324 statement files / 0.17 GB** — NOT the org-wide 24k/13.4 GB.
OCR + LLM cost is trivial at this scale.

**Verdict — Phase 2 re-scoped, still worth building:**
1. **Go-forward capability, not a retroactive fix.** The historical book has effectively no linked
   statements; `burden_ratio` fills on **~35 active merchants today** and grows with every new
   funding/renewal (2025+ capture is 28% and rising). The exit criterion "burden no longer null
   where statements exist" stands, with the population honestly quantified.
2. **Ops finding worth raising (new follow-up FU-702):** populating `Application_Submission__c`
   on every funded Opportunity and tagging `Document_Type__c` at intake are *upstream ops fixes*
   that multiply MRI's statement coverage for free — the data exists, the links don't.
3. The labeled sample (D-711) draws from the 79 statement-covered deals (Wolf anchor + ~5–9 more
   recent fundings).

---

## 2. The scoped ingestion path (bronze → silver → gold)

Designed to the medallion best-practice (Rule 6) and the §5.9 split. Two reasons the path is not
"just add an object to Lakeflow":

1. **Lakeflow Connect ingests structured fields, not large blobs.** It will land
   `ContentVersion`/`ContentDocumentLink`/`Attachment` **metadata** natively + cheaply (CDC), which
   gives us the inventory + the deal linkage. But streaming thousands of multi-MB base64
   `VersionData`/`Body` blobs through a CDC connector is the wrong tool — the binaries need a
   separate, governed, read-only fetch.
2. **OCR is a deterministic transform, not the agent's job** (keeps the agent's role pure judgment;
   makes the text cacheable + auditable + cheaper on re-run). See D-708.

```
Salesforce Files / Attachments
   │
   ├─ (native Lakeflow, metadata) ──────────────► bronze.contentversion / contentdocumentlink
   │                                               (+ attachment) — inventory + deal linkage
   │
   └─ (gated read-only fetch job, binaries) ─────► UC Volume  mca_mri.bronze.statements_raw/
                                                   + bronze.statement_files (1 row/file:
                                                     file_id, opportunity_id/merchant_id, title,
                                                     filetype, size, created_date, sha256,
                                                     volume_path, is_bank_statement)
                                                          │
                       (deterministic OCR/parse, silver)  ▼
                                                   silver.statement_text  (per file/page text;
                                                     ai_parse_document / Document AI / pdfplumber)
                                                          │
                    (Statement Analyst agent, gold, P2)   ▼
                                                   gold.merchant_extraction rows:
                                                     concurrent_positions / weekly_debit /
                                                     est_weekly_revenue  (grounded + gated + logged)
                                                          │
                          (deterministic spine re-run)    ▼
                                                   gold.merchant_clock  (active_position_cnt↑,
                                                     est_weekly_revenue filled → real burden_ratio)
                                                   gold.merchant_rung   (sharper Serial/Distressed)
```

**Layer-by-layer:**

- **bronze (inventory)** — add `ContentVersion`, `ContentDocumentLink`, `ContentDocument` (and
  `Attachment` if the probe finds it) to the Lakeflow pipeline. Metadata only; raw + immutable.
- **bronze (binaries)** — a gated, read-only batch job downloads candidate PDFs via SF REST
  (`GET /sobjects/ContentVersion/{Id}/VersionData`, or `/Attachment/{Id}/Body`) into a UC **Volume**
  `mca_mri.bronze.statements_raw/`, recording `bronze.statement_files`. Idempotent on `sha256`.
- **silver (deterministic OCR)** — `silver.statement_text`: text + page coordinates per file via a
  Databricks-native parser (digital PDFs → pdfplumber; scanned → `ai_parse_document`/Document AI).
  Deterministic, re-runnable, auditable. A deterministic `is_bank_statement` classifier (from the
  probe's Title/pattern findings) filters to true bank statements.
- **gold (agent, Phase-2 build — gated)** — the Statement Analyst classifies transaction lines
  (other-funder ACH debits, MCA payments, deposits) from `silver.statement_text`; the deterministic
  `common/agents/positions.py` tool COUNTS concurrent positions + sums weekly debit + sums deposits;
  rows land in `gold.merchant_extraction`.

**No extraction-table schema change needed.** `constants.ExtractionType` already defines
`CONCURRENT_POSITIONS` / `WEEKLY_DEBIT` / `EST_WEEKLY_REVENUE`, and
`field_maps.MERCHANT_EXTRACTION_MAP` stores `value` as generic text ("the extracted value as text
… '3', '5200.00'"). Phase 2 reuses the Phase-1 grounding/gate/event machinery as-is — the new code
is the **OCR transform**, the **`positions.py` counter**, the **`statement_analyst.py` agent half**,
and the **clock enrichment hook** (D-710).

---

## 3. Where the new signals feed the spine (the enrichment hook)

Today (`common/clock/rollup.py`): `active_position_cnt` counts only **MRI's own** advances that
compute to `active`; `est_weekly_revenue` is **null book-wide** (no feed) → `burden_ratio` is null
everywhere. The Statement Analyst recovers the bank's-eye view:

- **other-funder ACH positions** → ADD to `active_position_cnt` (the positions Salesforce can't see
  — the whole reason burden is understated today and Serial detection is blunt).
- **total weekly debit incl. other funders** → the true `total_weekly_debit`.
- **deposits** → fills `est_weekly_revenue` → a **real** `burden_ratio` where statements exist.

Per D-704/D-710, the clock reads **APPLIED** `merchant_extraction_current` rows as an *optional
enrichment input* — absent or low-confidence ⇒ exactly today's deterministic behavior (graceful
degrade). The agent supplies inputs; the clock still computes `burden_ratio`. This advances
**FU-301** (the deferred NSF/positions/revenue signals) precisely as that follow-up anticipated.

---

## 4. Labeled-sample design — mirroring the D-706 pattern that worked

The Data Steward's accuracy gate is the template
([`tests/tier2/recon_extraction.py`](../tests/tier2/recon_extraction.py)): a hand-labeled
ground-truth dict keyed by `deal_id`, an `ACCURACY_BAR`, exact-match-or-abstain scoring, and
specific regression guards — all baked into the tier-2 recon as a **permanent PROD-promotion gate**.
Phase 2 mirrors it:

```python
# tests/tier2/recon_statement_analyst.py  (Phase-2, to build)
STATEMENT_LABELS = {
  # deal_id: operator-read ground truth from the actual statement(s)
  "<wolf_deal_id>":  {"merchant": "Wolf Corporation",  "concurrent_positions": 2,
                      "weekly_debit": 3850.00, "est_weekly_revenue": 21000.00,
                      "rationale": "2 distinct ACH funders on the statement; $30k+$40k stack"},
  "<starr_deal_id>": {"merchant": "Starr Window Tinting", ...},
  # … the four validation merchants where statements exist + N more (target ~6–10, as D-706 began at 6)
}
POSITION_TOLERANCE   = 1       # positions within ±1 (per the SPRINT_7_PLAN rec)
AMOUNT_TOLERANCE_PCT = 0.10    # weekly_debit / est_weekly_revenue within ±10% (calibrate on sample 1)
ACCURACY_BAR         = 0.80    # "sane + improving", calibrated — not pre-fixed
```

**Scoring (mirrors D-706):** for each labeled deal, the APPLIED extraction's `concurrent_positions`
must be within ±1; `weekly_debit` and `est_weekly_revenue` within ±tolerance; overall ≥ bar.
**Regression guards** (the analogue of "true_default must not be APPLIED as early_payoff"): never
*under*-count a clearly-present other-funder position; never fabricate revenue when the statement
has no deposit lines (abstain → REVIEW instead). Labeled set is **versioned**; the gate is permanent.

**Wolf is the centerpiece** (as Starr was for D-706): a serial/rapid-reup merchant whose true
concurrent positions — including other funders' debits — are exactly what the spine cannot see and
what sharpens Serial detection. **Building the labeled sample requires the gated probe first** (we
need the statements in hand to read ground truth off them).

---

## 5. Open decisions — sign-off needed before any build/cloud run (D-707…D-713)

| ID | Question | Recommendation |
|---|---|---|
| **D-707** | **Statement source object + binary-retrieval mechanism.** | Lakeflow ingests `ContentVersion`/`ContentDocumentLink` (+`Attachment` if present) **metadata** to bronze; a **separate gated read-only fetch** downloads candidate PDFs to a UC **Volume** + `bronze.statement_files`. **First action: run the §1.4 read-only probe** to confirm the object, coverage, file-types, volume, and the identification rule before committing the path. |
| **D-708** | **OCR/parse placement** — deterministic silver transform vs agent-native multimodal read. | **Deterministic silver OCR** (`ai_parse_document`/Document AI for scans; pdfplumber for digital PDFs) → `silver.statement_text`; the agent classifies from text. Keeps the agent's role pure judgment, makes OCR cacheable/auditable/cheaper on re-run. |
| **D-709** | **Statement scope / recency.** | **Funding-moment statements only** (the application-bundle months at/just-before `funded_date`); if multiple, the most-recent ~3 months. NOT live bank truth (that's the app, S9+) — consistent with the SPRINT_7_PLAN out-of-scope line. |
| **D-710** | **Statement→spine enrichment integration** (how the new signals reach the clock without the agent writing a spine column). | The clock reads **APPLIED** `concurrent_positions`/`weekly_debit`/`est_weekly_revenue` from `merchant_extraction_current` as **optional enrichment**: other-funder positions **ADD** to `active_position_cnt`; deposits fill `est_weekly_revenue` → real `burden_ratio`; absent/low-confidence ⇒ today's behavior. Confirm the **merge rule** (add vs override for positions/debit). |
| **D-711** | **Labeled sample composition + accuracy bars.** | Hand-label ~6–10 deals incl. the four validation merchants (**Wolf** central); **positions ±1**, **weekly_debit/revenue ±10%** (calibrate on sample 1), overall **≥ 0.80**; permanent tier-2 gate + regression guards (no under-count of a present position; abstain rather than fabricate revenue). Mirrors D-706. |
| **D-712** | **Bank-statement PII / no-surface.** Statements are materially more sensitive than Notes (account numbers, running balances). | Statements + OCR text stay in **governed UC** (restricted Volume + silver), access-limited; define a **no-surface guard** for raw bank account numbers / running balances (analogous to the `_sf_stored_*` guard); only **derived aggregates** (position count, weekly burden, est revenue) leave the agent. Handle per the Data Contract PII/REG codes; the full compliance gate is still S8. |
| **D-713** | **OCR + LLM cost / batch scope.** | **Sample-first** (the labeled set) → measure accuracy → then batch the funded deals that have statements. OCR text **cached in silver** so re-runs don't re-OCR. Cost-confirmed + spend-gated (Rule 5), as the Data Steward's LLM spend was. |

---

## 6. Proposed build sequence (after sign-off — each step still gated)

1. **Run the §1.4 read-only probe** (gated) → confirm object, coverage, file-types, volume, the
   identification rule. *If coverage is too thin, we say so and re-scope — honesty over building.*
2. **bronze ingestion** — add the metadata objects to Lakeflow; stand up the gated binary-fetch job
   + `bronze.statement_files` + the UC Volume.
3. **silver OCR** — `silver.statement_text` + the deterministic `is_bank_statement` classifier.
4. **`common/agents/positions.py`** (deterministic counter) + tier-1 tests on hand-worked
   transaction-line vectors — built/tested offline, no cloud.
5. **`common/agents/statement_analyst.py`** (prompt + tolerant parser + pure `build_*_rows`
   orchestration, injected `predict_fn`) + `transform/gold_statement_extraction.py` (Spark driver,
   Foundation Model via the `databricks-sdk` serving client — mirrors `gold_extraction.py`).
6. **Build the labeled sample** off the probe's statements; add `recon_statement_analyst.py` with the
   accuracy gate.
7. **Run on `gold_test`** → accuracy ≥ bar → re-run the clock/rung → quantify the improvement
   (burden no longer null where statements exist; Serial sharper from true positions) → **PROD on
   explicit approval + `allow_prod=True`** (Rule 5).

**STOP here** per the task — no build, no cloud run, until D-707…D-713 are signed.
