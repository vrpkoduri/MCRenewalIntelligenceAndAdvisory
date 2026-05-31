"""Bronze -> Silver field maps (the SPRINT_0 mapping, as data).

This is the executable form of the field-map table in SPRINT_0.md. The silver transform
reads these specs to select/rename/type; tests assert they stay consistent with the
authoritative Data Contract xlsx (see common.contract).
"""

from dataclasses import dataclass

from .constants import Verdict


@dataclass(frozen=True)
class FieldSpec:
    silver_col: str
    dtype: str  # logical type: string|decimal|int|date|timestamp|bool|enum
    sf_source: str  # Salesforce source (object.field); confirm exact names in G1
    verdict: str
    notes: str = ""


# --- Selected-Offer resolution (locked in G1, 2026-05-31; see DECISIONS C-012) ---
# Deal economics come from the merchant's SELECTED offer, not the Opportunity and not the
# raw offers list. The selected offer is the Offer__c row joined to the Opportunity by:
#     Offer__c.Opportunity__c = Opportunity.Id  AND  Offer__c.Select_Offer__c = true
# DQ from G1 profiling (funded book = 3,959 opps, StageName='Funded'):
#   - ~28 funded deals carry >1 row with Select_Offer__c=true -> silver must DEDUP,
#     tie-break on latest Offer__c.LastModifiedDate (keep one selected offer per deal).
#   - 2 funded deals have NO selected offer -> fall back to the Opportunity's own
#     economics fields where present, else leave null + flag selected_offer_missing.
#   - In Offer__c the REAL economics fields are the underscored ones:
#     Payback_Amount__c (3,746 set) NOT PaybackAmount__c (26);
#     Payment_Amount__c (3,643 set) NOT PaymentAmnt__c (16). The non-underscored
#     duplicates are near-empty external-feed copies — do NOT use them.
# Source-label convention below: "SelectedOffer.<API>" = Offer__c field read from the
# resolved selected-offer row; "Opportunity.<API>" = the Opportunity row itself.
SELECTED_OFFER_JOIN = (
    "Offer__c.Opportunity__c = Opportunity.Id AND Offer__c.Select_Offer__c = true"
)

# Opportunity + Selected Offer -> mca_mri.silver.deals (one row per advance).
DEALS_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("opportunity_id", "string", "Opportunity.Id", Verdict.HAVE, "PK"),
    FieldSpec("merchant_sf_id", "string", "Opportunity.AccountId", Verdict.CARRY, "standard lookup -> Account (merchant); 100% set on funded book; identity resolution in S1"),
    FieldSpec("opportunity_name", "string", "Opportunity.Name", Verdict.HAVE),
    FieldSpec("stage", "string", "Opportunity.StageName", Verdict.HAVE, "funded book = StageName='Funded' (3,959 rows in G1)"),
    FieldSpec("deal_type", "enum", "Opportunity.Type", Verdict.HAVE, "100% set; trust as renewal flag (New Business / Renewal / Buyout)"),
    FieldSpec("funder", "string", "Opportunity.Funder__c", Verdict.HAVE, "may be multi-value; also Funders__c / Selected_Funder__c; keep raw + parsed"),
    FieldSpec("funded_amount", "decimal", "Opportunity.Funded_Amount__c", Verdict.HAVE, "anchor, ~100% set; cross-check vs SelectedOffer.Funded_Amount__c"),
    FieldSpec("factor_rate", "decimal", "SelectedOffer.Factor_Rate__c", Verdict.HAVE, "only on Offer__c (Opp has none); ~94% of selected; e.g. 1.459"),
    FieldSpec("payback_amount", "decimal", "SelectedOffer.Payback_Amount__c", Verdict.HAVE, "underscored field (NOT PaybackAmount__c); Opp.Payback_Amount__c is 0% — must use selected offer; RTR cross-check"),
    FieldSpec("payment_amount", "decimal", "SelectedOffer.Payment_Amount__c", Verdict.HAVE, "underscored field (NOT PaymentAmnt__c); fixed-ACH"),
    FieldSpec("num_payments", "int", "SelectedOffer.Number_Payments__c", Verdict.HAVE, "Opportunity.Number_Payments__c also ~92% as fallback"),
    FieldSpec("payment_frequency", "enum", "SelectedOffer.Frequency__c", Verdict.HAVE, "Daily / Weekly; Opportunity.Frequency__c also 100% as fallback"),
    FieldSpec("funded_date", "date", "Opportunity.Funded_Date__c", Verdict.HAVE, "100% set; funding anchor; date-sanity vs created_date (Funded_Date00__c also 100%; Legacy_Funded_Date__c rare)"),
    FieldSpec("created_date", "timestamp", "Opportunity.CreatedDate", Verdict.HAVE, "flag large gap vs funded_date either direction (C-007); see Migrated_Deal__c / Legacy_Opportunity__c"),
    FieldSpec("days_in_stage", "int", "Opportunity.Days_in_Stage__c", Verdict.CARRY, "unreliable on migrated records; carry"),
    FieldSpec("state_of_incorporation", "string", "Opportunity.State_of_Incorporation__c", Verdict.HAVE, "blank = missing"),
    FieldSpec("months_in_business", "int", "Opportunity.Months_in_Business__c", Verdict.HAVE, "0/blank = MISSING; sparse (~5%); prefer business_start_date; Month_in_Business__c ~18% as alt"),
    FieldSpec("business_start_date", "date", "Opportunity.Business_Start_Date__c", Verdict.HAVE, "preferred tenure source (~18%); Account.Business_Start__c as alt"),
    FieldSpec("fico", "int", "Opportunity.FICO__c", Verdict.HAVE, "string field, parse to int; 0/blank = MISSING; Fico_Score__c is 0% — do not use"),
    FieldSpec("position_at_funding", "int", "Opportunity.Positions__c", Verdict.CARRY, "0% populated on funded book — effectively unavailable; blank = unknown (NOT zero)"),
    FieldSpec("total_house_commission", "decimal", "Opportunity.Total_House_Commission__c", Verdict.CARRY),
    FieldSpec("max_approved_amount", "decimal", "Opportunity.Maximum_Approved_Amount__c", Verdict.CARRY),
    FieldSpec("num_approvals", "int", "Opportunity.Number_of_Approvals__c", Verdict.CARRY),
    FieldSpec("num_declines", "int", "Opportunity.Number_of_Declines__c", Verdict.CARRY),
    FieldSpec("notes", "string", "Opportunity.Notes__c", Verdict.CARRY, "free-text; may hold default cause"),
    FieldSpec("send_renewal_notices", "bool", "Opportunity.Send_Renewal_Notices__c", Verdict.CARRY, "repurpose later as consent toggle"),
    FieldSpec("contact_name", "string", "Opportunity.Contact__c", Verdict.CARRY, "PII; lookup id — resolve display name in S1"),
    FieldSpec("mobile", "string", "Opportunity.Mobile__c", Verdict.CARRY, "PII; consent before any use"),
    FieldSpec("email", "string", "Opportunity.Email__c", Verdict.CARRY, "PII"),
    FieldSpec("application_submission_id", "string", "Opportunity.Application_Submission__c", Verdict.CARRY),
    FieldSpec("_sf_stored_remaining_balance", "decimal", "Opportunity.Remaining_Balance__c", Verdict.DISTRUST, "checkpoint only — NEVER surface; recompute in S2"),
    FieldSpec("_sf_stored_percentage_paid", "decimal", "Opportunity.Percentage_Paid__c", Verdict.DISTRUST, "checkpoint only — NEVER surface"),
    FieldSpec("_sf_stored_est_renewal_date", "date", "Opportunity.Estimated_Renewal_Date__c", Verdict.DISTRUST, "checkpoint only — replace in S2"),
)

# Opportunity Field History -> mca_mri.silver.field_history (event source for S1/S3).
# Column names confirmed against bronze.opportunityfieldhistory (2026-05-31): Id,
# OpportunityId, Field, DataType, OldValue, NewValue, CreatedDate, CreatedById.
FIELD_HISTORY_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("history_id", "string", "OpportunityFieldHistory.Id", Verdict.HAVE, "PK"),
    FieldSpec("opportunity_id", "string", "OpportunityFieldHistory.OpportunityId", Verdict.HAVE, "FK -> silver.deals.opportunity_id"),
    FieldSpec("field", "string", "OpportunityFieldHistory.Field", Verdict.HAVE, "changed field API name; 'StageName' rows drive renewal-cadence reconstruction in S3"),
    FieldSpec("old_value", "string", "OpportunityFieldHistory.OldValue", Verdict.HAVE, "stored as string regardless of underlying type"),
    FieldSpec("new_value", "string", "OpportunityFieldHistory.NewValue", Verdict.HAVE),
    FieldSpec("data_type", "string", "OpportunityFieldHistory.DataType", Verdict.CARRY, "interprets old/new value typing"),
    FieldSpec("changed_at", "timestamp", "OpportunityFieldHistory.CreatedDate", Verdict.HAVE, "event timestamp"),
    FieldSpec("changed_by", "string", "OpportunityFieldHistory.CreatedById", Verdict.HAVE, "actor user id; resolve in S1"),
)

# Offer__c -> mca_mri.silver.offers (RAW offer catalogue: one row per offer, all
# opportunities — NOT just the funded book and NOT just the selected offer).
# Confirmed against bronze.offer__c (2026-05-31). Economics use the UNDERSCORED fields
# (Payback_Amount__c / Payment_Amount__c) — the non-underscored duplicates
# (PaybackAmount__c / PaymentAmnt__c) are near-empty external-feed copies (C-012); they
# are intentionally NOT mapped. The selected-offer resolution for silver.deals reads from
# this same source (Select_Offer__c + LastModifiedDate); here we carry the full list so
# S5 (Offer Engine) and S1 can analyse declined/expired/competing offers.
OFFERS_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("offer_id", "string", "Offer__c.Id", Verdict.HAVE, "PK"),
    FieldSpec("opportunity_id", "string", "Offer__c.Opportunity__c", Verdict.HAVE, "FK -> opportunity; Opportuniy_Link__c is a misspelled alt"),
    FieldSpec("merchant_sf_id", "string", "Offer__c.Merchant__c", Verdict.CARRY, "sparse lookup; identity resolution in S1"),
    FieldSpec("offer_name", "string", "Offer__c.Name", Verdict.HAVE),
    FieldSpec("funder", "string", "Offer__c.Funder__c", Verdict.HAVE),
    FieldSpec("status", "string", "Offer__c.Status__c", Verdict.HAVE, "offer lifecycle status (e.g. selected/declined/expired)"),
    FieldSpec("is_selected", "bool", "Offer__c.Select_Offer__c", Verdict.HAVE, "drives selected-offer resolution for silver.deals (C-012)"),
    FieldSpec("funded_amount", "decimal", "Offer__c.Funded_Amount__c", Verdict.HAVE),
    FieldSpec("factor_rate", "decimal", "Offer__c.Factor_Rate__c", Verdict.HAVE, "double in SF -> decimal"),
    FieldSpec("payback_amount", "decimal", "Offer__c.Payback_Amount__c", Verdict.HAVE, "underscored authoritative field (NOT PaybackAmount__c)"),
    FieldSpec("payment_amount", "decimal", "Offer__c.Payment_Amount__c", Verdict.HAVE, "underscored authoritative field (NOT PaymentAmnt__c)"),
    FieldSpec("num_payments", "int", "Offer__c.Number_Payments__c", Verdict.HAVE, "double in SF -> int"),
    FieldSpec("payment_frequency", "enum", "Offer__c.Frequency__c", Verdict.HAVE, "Daily / Weekly"),
    FieldSpec("days_weeks", "string", "Offer__c.Days_Weeks__c", Verdict.CARRY),
    FieldSpec("offer_expiration_date", "date", "Offer__c.Offer_Expiration_Date__c", Verdict.CARRY),
    FieldSpec("total_house_commission", "decimal", "Offer__c.Total_House_Commission__c", Verdict.CARRY),
    FieldSpec("declined_reason", "string", "Offer__c.Declined_Reason__c", Verdict.CARRY, "free-text; useful for S5"),
    FieldSpec("notes", "string", "Offer__c.Notes__c", Verdict.CARRY),
    FieldSpec("created_date", "timestamp", "Offer__c.CreatedDate", Verdict.HAVE),
    FieldSpec("last_modified_date", "timestamp", "Offer__c.LastModifiedDate", Verdict.HAVE, "tie-break key for selected-offer dedup (C-012)"),
    FieldSpec("is_deleted", "bool", "Offer__c.IsDeleted", Verdict.CARRY, "carried raw; not filtered at silver"),
)

# =============================================================================
# GOLD layer (S1) — identity-resolved canonical Deal Table + merchant dimension.
# Source labels here are NOT Salesforce objects but the GOLD provenance:
#   "silver.deals.<col>"  carried from the silver Deal projection
#   "derive:<desc>"       computed in the S1 gold transform
#   "merchant.<col>"      pulled from the resolved merchant dimension
#   "aatm:<col>"          build-time read-only enrichment from AATM (C-014)
#   "gap"                 Must-capture — no S1 source; null + *_is_missing flag
#   "defer:S2"            Derive field that needs the live clock (S2) — null in S1
# The 24 Deal Table fields below are the Data Contract "Deal Table" sheet, in
# order, so gold.deals conforms to the contract (test_contract_consistency).
# =============================================================================

# silver.deals + S1 derivations -> mca_mri.gold.deals (canonical Deal Table).
DEAL_TABLE_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("deal_id", "string", "silver.deals.opportunity_id", Verdict.HAVE, "PK"),
    FieldSpec("merchant_id", "string", "derive:identity crosswalk (D-101)", Verdict.DERIVE, "stable MRI id; non-null on 100% of funded book (AccountId 100% set)"),
    FieldSpec("funder", "string", "silver.deals.funder", Verdict.HAVE),
    FieldSpec("iso_rep", "string", "gap", Verdict.HAVE, "not carried to silver.deals; null + iso_rep_is_missing in S1 (FU-101 backfill from bronze.opportunity)"),
    FieldSpec("funded_date", "date", "silver.deals.funded_date", Verdict.HAVE),
    FieldSpec("funded_amount", "decimal", "silver.deals.funded_amount", Verdict.HAVE),
    FieldSpec("factor_rate", "decimal", "silver.deals.factor_rate", Verdict.HAVE),
    FieldSpec("term_months", "decimal", "derive:Appendix A.3", Verdict.DERIVE, "static: daily num_payments/21.7, weekly /4.33; null + term_months_is_missing when inputs absent"),
    FieldSpec("num_payments", "int", "silver.deals.num_payments", Verdict.HAVE),
    FieldSpec("payment_frequency", "enum", "silver.deals.payment_frequency", Verdict.HAVE),
    FieldSpec("payment_amount", "decimal", "silver.deals.payment_amount", Verdict.HAVE),
    FieldSpec("holdback_pct", "decimal", "defer:S2", Verdict.DERIVE, "needs est revenue (Must-capture) -> S2 clock; null in S1"),
    FieldSpec("total_payback", "decimal", "silver.deals.payback_amount", Verdict.HAVE, "contract total_payback = silver payback_amount (RTR anchor)"),
    FieldSpec("deal_type", "enum", "silver.deals.deal_type", Verdict.HAVE, "New Business / Renewal / Buyout — trusted (CLAUDE.md 2.5)"),
    FieldSpec("is_renewal_of", "string", "derive:renewal chain (D-103)", Verdict.DERIVE, "deal_id of immediately-prior same-merchant deal by funded_date; null for New Business / first deal"),
    FieldSpec("disclosed_positions_cnt", "int", "gap", Verdict.MUST_CAPTURE, "Positions__c 0% on funded book; null + flag (never 0)"),
    FieldSpec("fico", "int", "silver.deals.fico", Verdict.HAVE, "0/blank already MISSING in silver"),
    FieldSpec("months_in_business", "int", "silver.deals.months_in_business", Verdict.HAVE),
    FieldSpec("disclosed_balance_total", "decimal", "gap", Verdict.MUST_CAPTURE, "null + flag"),
    FieldSpec("net_funded", "decimal", "gap", Verdict.MUST_CAPTURE, "null + flag"),
    FieldSpec("governing_state", "string", "merchant.governing_state", Verdict.HAVE, "merchant dimension governing_state; fallback silver.deals.state_of_incorporation"),
    FieldSpec("prior_factor_rate", "decimal", "derive:prior deal factor_rate (D-103)", Verdict.DERIVE, "factor_rate of is_renewal_of deal; null when no prior"),
    FieldSpec("status", "string", "derive:field_history StageName", Verdict.DERIVE, "latest StageName transition from silver.field_history; fallback silver.deals.stage"),
    FieldSpec("personal_guarantee", "bool", "gap", Verdict.MUST_CAPTURE, "null + flag"),
)

# Account cluster (identity) + static profile -> mca_mri.gold.merchants.
# S1 SEED ONLY: identity + static profile + the AATM cross-system bridge.
# Feature/clock/prediction columns of the 66-field Merchant Gold Table accrete
# in S2+ (reserved, not built here).
MERCHANT_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_id", "string", "derive:identity crosswalk (D-101)", Verdict.DERIVE, "PK; stable MRI id"),
    FieldSpec("azure_merchant_id", "string", "aatm:merchants.azure_merchant_id via tax_id (C-014)", Verdict.CARRY, "DM Merchant Id — cross-system join key; ~84% fill; null + azure_merchant_id_is_missing"),
    FieldSpec("business_name", "string", "merchant.Account.Name", Verdict.HAVE, "representative name across collapsed accounts (first non-null by sf_id)"),
    FieldSpec("principal_name", "string", "gap", Verdict.HAVE, "no reliable Account field; null + principal_name_is_missing (resolve from Contact later)"),
    FieldSpec("governing_state", "string", "merchant.norm(Business_State__c|BillingState)", Verdict.HAVE, "normalized 2-letter"),
    FieldSpec("tax_id", "string", "merchant.norm(Key_Reference_Tax_Id__c|Tax_ID__c)", Verdict.CARRY, "normalized; static profile + the AATM bridge key; 96.7% populated"),
    FieldSpec("business_start_date", "date", "merchant.Account.Business_Start__c", Verdict.HAVE, "tenure source; tenure_days math is S2"),
    FieldSpec("industry", "string", "merchant.Account.Industry", Verdict.CARRY, "raw; contract industry_vertical (taxonomy) is Derive -> S5"),
    FieldSpec("sf_account_count", "int", "derive:cluster size", Verdict.DERIVE, "# SF Accounts collapsed into this merchant (collapse visibility)"),
    FieldSpec("match_reason", "string", "derive:auto-merge tiers", Verdict.DERIVE, "sorted AUTO tiers that formed the cluster (master_record/tax_id); blank = singleton"),
)

# Persisted crosswalk -> mca_mri.gold.merchant_crosswalk (D-101). One row per SF
# Account on the funded book; never re-keys (see common.identity.keys).
MERCHANT_CROSSWALK_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_sf_id", "string", "bronze.account.Id", Verdict.HAVE, "PK; SF Account Id"),
    FieldSpec("merchant_id", "string", "derive:identity crosswalk (D-101)", Verdict.DERIVE, "stable MRI id this account maps to"),
)

# Gold DQ / derived columns (beyond the contract field set). (col, dtype).
GOLD_DEALS_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("iso_rep_is_missing", "bool"),
    ("term_months_is_missing", "bool"),
    ("renewal_unlinkable", "bool"),  # Renewal/Buyout with no linkable prior same-merchant deal
    ("disclosed_positions_cnt_is_missing", "bool"),
    ("disclosed_balance_total_is_missing", "bool"),
    ("net_funded_is_missing", "bool"),
    ("personal_guarantee_is_missing", "bool"),
)

GOLD_MERCHANTS_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("azure_merchant_id_is_missing", "bool"),
    ("principal_name_is_missing", "bool"),
    ("tax_id_is_missing", "bool"),
)


def deal_table_columns() -> list[str]:
    return [fs.silver_col for fs in DEAL_TABLE_MAP] + [c for c, _ in GOLD_DEALS_DQ_COLUMNS]


def merchant_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_MAP] + [c for c, _ in GOLD_MERCHANTS_DQ_COLUMNS]


def merchant_crosswalk_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_CROSSWALK_MAP]


# DQ-derived columns added by the silver transform (not direct source maps).
# (col, dtype). Kept here so schema + tests stay in sync with the transform.
DEALS_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("months_in_business_is_missing", "bool"),
    ("fico_is_missing", "bool"),
    ("date_sanity_flag", "bool"),
    ("rtr_check_delta", "decimal"),
    ("rtr_check_flag", "bool"),
    ("funder_parsed", "string"),  # parsed/normalized form of multi-value Funder
    ("selected_offer_missing", "bool"),  # C-012: no Offer__c row with Select_Offer__c=true for this deal
    ("multi_selected_offer", "bool"),  # C-012: >1 selected offer pre-dedup (kept latest LastModifiedDate)
)


def deals_silver_columns() -> list[str]:
    return [fs.silver_col for fs in DEALS_MAP] + [c for c, _ in DEALS_DQ_COLUMNS]


def offers_silver_columns() -> list[str]:
    return [fs.silver_col for fs in OFFERS_MAP]


def field_history_silver_columns() -> list[str]:
    return [fs.silver_col for fs in FIELD_HISTORY_MAP]
