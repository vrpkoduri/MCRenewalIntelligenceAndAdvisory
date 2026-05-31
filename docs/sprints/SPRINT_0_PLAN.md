# Sprint 0 Plan — Foundation & Ingestion

Detailed plan per the 9-part template in `GENERAL_INSTRUCTIONS.md` §1. Authoritative scope: `SPRINT_0.md`. Goal: land the funded book into a governed silver layer. **No intelligence built.**

---

## 1. Objective & scope

**Objective:** complete Salesforce funded book → governed `mca_mri.silver`, with Unity Catalog governance and a scheduled daily refresh.

**In scope:** repo/DAB scaffold; Lakeflow Connect → `bronze` (CDC) for Opportunity, Merchant, Offer/Selected Offer, Opportunity Field History; silver transform (clean/type/rename to canonical schema, DQ rules); UC governance + lineage; scheduled daily refresh; reconciliation + DQ tests.

**Out of scope:** identity resolution (S1), features/clock (S2), rung/state/event log (S3), offers/prediction/comms/app (S5+), **any** recompute of balance/paydown/eligibility (silver only carries static terms).

## 2. Definition of Ready

| Gate | State |
|---|---|
| G2 clock math | ✅ Appendix A (needed S2) |
| G3 rung rules | ✅ Appendix B (needed S3) |
| G4 environment | ✅ Databricks/UC accessible; data rights confirmed (C-001) |
| G1 data audit | ⏳ confirm SF object/field names + resolve 6 must-capture gaps (D-002) |
| SF connection | ⏳ Connected App + UC connection (C-003) |

Ingestion/silver build cannot complete until G1 + SF connection land. Offline scaffolding/shared components/tests proceed without them.

## 3. Task breakdown by SDLC stage

1. **Requirements** ✅ — scope locked from `SPRINT_0.md` + Data Contract Deal table.
2. **Design** ✅ — canonical silver schema from field maps; DQ rules; `_sf_stored_*` isolation; medallion + `_test` mirrors.
3. **DoR** ◑ — G1 + connection outstanding.
4. **Build**
   - ✅ Scaffold (DAB, `src/`, `resources/`, `tests/`).
   - ✅ Shared components (`constants`, `field_maps`, `dq`, `schemas`, `io.guards`).
   - ⏳ Salesforce Connected App + UC connection + secret scope **(your approval)**.
   - ⏳ Lakeflow ingestion pipeline → `bronze` (`resources/ingestion_pipeline.yml`) **(your approval)**.
   - ⏳ Silver transform finalize (`select_rename_deals`/`build_silver_deals`) after G1 confirms columns.
   - ⏳ Scheduled daily silver job (`resources/silver_job.yml`).
5. **Test** — ✅ tier-1 (32 passing); ⏳ tier-2 (transform/reconciliation/integration on `_test` schemas).
6. **Review** — self-review + `code-review` before any deploy.
7. **Documentation** — ✅ governance docs; ⏳ schema doc once finalized.
8. **DoD** — see §9.
9. **Deploy/Activate** — only on your approval (Rule 5).

## 4. Shared components created/changed

`constants`, `field_maps`, `contract`, `dq.predicates`, `dq.rules`, `schemas.silver`, `io.guards` (see `SHARED_COMPONENTS.md`). Reserved: `clock`, `identity`, `rung`, `eventlog`.

## 5. Test plan

Per `TESTING_FRAMEWORK.md`. Tier-1 done. Tier-2 to add once bronze exists: silver transform on synthetic bronze (incl. the 4 merchants), reconciliation vs SF funded count, schema/type, table-level no-surface, date-sanity & missing-flag counts. Full suite runs after every build piece.

## 6. Data contracts touched

- **Writes:** `mca_mri.bronze.*` (raw SF objects), `mca_mri.silver.deals`, `mca_mri.silver.offers` (raw list), `mca_mri.silver.field_history`.
- **Reads:** Salesforce (via Lakeflow Connect). The Data Contract xlsx governs naming/verdicts.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| SF API field names differ from spec | G1 audit before pipeline; field map is data, easy to update |
| Stored balance/%paid wrong (known) | `_sf_stored_*` checkpoint-only + no-surface guard/test |
| Date contradictions / migration artifacts | `date_sanity_flag`; flag not drop — but see D-001 |
| Split (non-fixed-ACH) deals exist | DQ surfaces; route to estimated path in S2; off by default |
| Building ahead of scope | Reserved-home stubs + guardrails in CLAUDE.md |

## 8. Open decisions (need you)

- **D-001** date-sanity rule direction.
- **D-002** SF object/field API names (resolve in G1).
- **D-003** root `CLAUDE.md`.
- **Approvals:** create `mca_mri` catalog+schemas; UC Salesforce connection; Lakeflow pipeline; daily job; any deploy.

## 9. Definition of Done (exit criteria) — how we prove each

- [ ] Every funded Opportunity queryable in `mca_mri.silver.deals`; **row count reconciles** to SF funded book (± explained) — *reconciliation test*.
- [ ] Schema documented; all columns typed per map; `_sf_stored_*` isolated + do-not-surface — *schema test + no-surface test*.
- [ ] `mca_mri.silver.field_history` populated — *count > 0 test*.
- [ ] DQ flags computed & queryable; counts reported — *DQ test + tracker note*.
- [ ] Unity Catalog governs `bronze`/`silver` with lineage visible — *manual UC check + screenshot in tracker*.
- [ ] Scheduled daily refresh runs end-to-end — *job run history*.
- [ ] Four validation merchants present in silver & spot-checked vs source — *scenario test + manual*.
