# Decisions Log

Append-only record of decisions you've signed off on, and open decisions awaiting your call (GENERAL_INSTRUCTIONS Rule 5). Newest first.

## Confirmed

| ID | Date | Decision | Rationale |
|---|---|---|---|
| C-012 | 2026-05-31 | **Field-level source-of-truth locked (closes D-002).** Resolved by profiling landed bronze (funded book = 3,959 `Opportunity` rows where `StageName='Funded'`). Deal economics come from the **selected offer** (`Offer__c` where `Select_Offer__c=true`, joined on `Opportunity__c=Opportunity.Id`): `factor_rate`←`Factor_Rate__c`, `payback_amount`←`Payback_Amount__c`, `payment_amount`←`Payment_Amount__c` (the **underscored** fields; the non-underscored `PaybackAmount__c`/`PaymentAmnt__c` are near-empty feed copies). `funded_amount`←`Opportunity.Funded_Amount__c` (anchor). Merchant link = `Opportunity.AccountId` (not a custom `Merchant__c`). `fico`←`Opportunity.FICO__c` (string; `Fico_Score__c` is 0%). `position`←`Positions__c` is 0% → blank=unknown. **DQ rule:** ~28 funded deals have >1 selected offer → silver dedups on latest `Offer__c.LastModifiedDate`; 2 funded deals have none → fall back to Opportunity fields + flag `selected_offer_missing`. `field_maps.DEALS_MAP` updated to real API names; 32 tier-1 tests green. | Decided on real values, not guesses; honors the no-surface / distrust-SF-balances rules. |
| C-011 | 2026-05-31 | **Offer object API name = `Offer__c`** (resolves the object-level half of D-002). The "Selected Offer" is not a separate object — it's the row of `Offer__c` where `Select_Offer__c = true`. A single `Offer__c` bronze table therefore serves both the full offers list and the selected-offer economics. Excluded `Offer__Feed` (Chatter) and `Offer__History` (Offer-object field history; our event source is `OpportunityFieldHistory`). | Confirmed live in the Lakeflow ingestion wizard against the `mri_salesforce` connection. `constants.SFObject.OFFER` already = `"Offer__c"`. |
| C-008 | 2026-05-29 | **Root `CLAUDE.md` pointer** (D-003 → option b): keep one source in `docs/`, add a root pointer so Claude Code auto-loads it | One source of truth, still loads from repo root |
| C-007 | 2026-05-29 | **Date-sanity rule** (D-001 → option b/c): flag a large gap in *either* direction, not just `funded > created` | Catches the real migration artifact (Funded 2020 / Created 2022) the spec describes |
| C-010 | 2026-05-29 | **All-managed tables** for `mca_mri` bronze/silver/gold (Option B). Verified Lakeflow Connect destinations are managed streaming tables (external not selectable); chose managed end-to-end for the simplest fully-native setup. Diverges from the house external convention (`mca_funders`/`mca_leads` use external Delta at `abfss://mca-pipeline@dlsmcdatastoreprd…`). | No material cost difference (identical storage $/GB; managed adds minor auto-optimization compute, negligible at this data size). Native Lakeflow path; honors CLAUDE.md §4. Revisit if cross-engine reads or data-residency policy emerge. |
| C-009 | 2026-05-29 | **No separate secret scope** for Salesforce creds (supersedes C-006/D-005). The interactive OAuth flow stores credentials inside the **UC connection** `mri_salesforce` itself (browser → UC, governed). | Fewer moving parts; secret never transits chat/code/shell. Scope only needed if we later move to headless JWT |
| C-006 | 2026-05-29 | ~~Secret backend = Databricks-backed scope `mri-salesforce`~~ — **superseded by C-009** | UC connection holds creds; no scope required for OAuth web flow |
| C-005 | 2026-05-29 | **Salesforce OAuth grant type** = web / authorization-code behind the existing integration user (D-004 → default a) | Matches Databricks "Add connection" wizard; JWT later if needed |
| C-004 | 2026-05-29 | Scaffold repo + write plans now; **no cloud resources** (catalog/connection/pipeline) until separately approved | Lets us build & test offline; honors Rule 5 deploy gate |
| C-003 | 2026-05-29 | Guide the user through creating a Salesforce **Connected App (OAuth)** for Lakeflow Connect | Native, best-practice ingestion path; user did not yet have a credential chosen |
| C-002 | 2026-05-29 | New Unity Catalog catalog = **`mca_mri`** (schemas: `bronze`/`silver`/`gold` + `_test` mirrors) | Matches house family (`mca_funders`, `mca_leads`); supersedes the literal `mri` name in CLAUDE.md/specs — schema names unchanged |
| C-001 | 2026-05-29 | Data rights confirmed: Morgan Cash holds full rights to use the funded-book data this way (G4 legal gate) | Stated by Venkat |

## Open — awaiting your decision

_None open._ (D-002 resolved — see below / C-011 + C-012.)

## Resolved (was open)

| ID | Opened | Question | Resolution |
|---|---|---|---|
| D-002 | 2026-05-29 | **Salesforce object + field API names.** | **Resolved 2026-05-31.** Object-level via C-011 (Offer object = `Offer__c`; Merchant = `Account`; event source = `OpportunityFieldHistory`); field-level via C-012 (selected-offer economics, underscored duplicate fields, `AccountId` merchant link, `FICO__c` string, selected-offer dedup DQ rule). `field_maps.DEALS_MAP` locked to real API names. |

<details><summary>Original D-002 framing (for history)</summary>

| ID | Opened | Question | Options | Recommendation |
|---|---|---|---|---|
| D-002 | 2026-05-29 | **Salesforce object + field API names.** Object-level: **resolved** — Offer object = `Offer__c` (C-011); Merchant parent = `Account`; event source = `OpportunityFieldHistory`. Field-level: still open — `Offer__c` exposes **duplicate-looking pairs** (`PaybackAmount__c` vs `Payback_Amount__c`; `PaymentAmnt__c` vs `Payment_Amount__c`) and the deal-economics fields (`Factor_Rate__c`, `Funded_Amount__c`, `Number_Payments__c`, `Frequency__c`, `Total_House_Commission__c`, `Offer_Expiration_Date__c`, links `Opportunity__c`/`Merchant__c`). Must determine which of each pair is authoritative. | Profile landed bronze values to pick the authoritative field of each duplicate pair; then lock `field_maps.DEALS_MAP` source labels. | Resolve the field-level half **after bronze lands** (read real values), before building the bronze→silver transform. |

</details>
