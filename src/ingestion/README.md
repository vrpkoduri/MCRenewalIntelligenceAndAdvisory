# Ingestion — Lakeflow Connect (Salesforce → bronze)

Sprint 0 ingestion uses **Lakeflow Connect** (native managed Salesforce connector with
CDC) — do NOT hand-roll a Bulk-API pipeline (CLAUDE.md §4).

## Objects to ingest (S0) — confirmed in G1 (C-011)
- `Opportunity` — one row per advance (426K rows; funded book = 3,959 where `StageName='Funded'`)
- `Account` — parent merchant (333K)
- `Offer__c` — offers list + selected offer (`Select_Offer__c=true`); deal economics (115K)
- `OpportunityFieldHistory` — Stage transitions + timestamps, event source (220K)

## Setup status
**Live.** Pipeline `mri_sf_ingest_bronze` (id `0b6d521b-…`) created via the Lakeflow Connect
wizard and ran successfully on 2026-05-31 (0 errors); refreshes every 6h. Captured as code
in [`resources/ingestion_pipeline.yml`](../../resources/ingestion_pipeline.yml). See
[`docs/SALESFORCE_CONNECTION_GUIDE.md`](../../docs/SALESFORCE_CONNECTION_GUIDE.md).

## Secrets
Salesforce credentials live **inside the UC connection `mri_salesforce`** (OAUTH_U2M),
not a secret scope (C-009 supersedes the earlier secret-scope plan). The connection is a
governed UC securable; the pipeline references it by `connection_name` only. No credentials
in code or this repo.
