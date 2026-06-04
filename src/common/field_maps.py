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

# =============================================================================
# GOLD layer (S2) — Amortization Clock outputs (Appendix A). POINT-IN-TIME tables
# (D-201/C-016), keyed (id, clock_run_date). Source-label convention here:
#   "clock:<A.x>"        computed by the Appendix A clock — recomputed daily, NOT SF stored
#   "run:today"          the run's "today" (clock_run_date), stamped for reproducibility
#   "gold.deals.<col>"   static term carried from the canonical Deal Table (single source)
#   "derive:<desc>"      a roll-up / inference computed in the transform
# The per-deal clock fields are MRI-internal (not in the contract Deal Table — that sheet
# is the 24 STATIC fields). The per-merchant fields ARE the contract "Merchant Gold Table"
# → "Position & burden (the clock)" section (+ time-dependent Identity fields).
# =============================================================================

# gold.deals (static terms) + Appendix A clock -> mca_mri.gold.deal_clock (point-in-time).
DEAL_CLOCK_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("deal_id", "string", "gold.deals.deal_id", Verdict.HAVE, "PK part (with clock_run_date)"),
    FieldSpec("merchant_id", "string", "gold.deals.merchant_id", Verdict.HAVE, "roll-up key"),
    FieldSpec("clock_run_date", "date", "run:today", Verdict.DERIVE, "the run's 'today'; PK part; point-in-time stamp (A.0)"),
    FieldSpec("rtr", "decimal", "clock:A.2 funded_amount × factor_rate", Verdict.DERIVE, "total owed; never changes; validated vs total_payback"),
    FieldSpec("elapsed_payments", "int", "clock:A.3 business-day/weekly count", Verdict.DERIVE, "capped at num_payments (never past payoff)"),
    FieldSpec("amount_paid", "decimal", "clock:A.2 payment_amount × elapsed_payments", Verdict.DERIVE),
    FieldSpec("est_current_balance", "decimal", "clock:A.2 max(0, rtr − amount_paid)", Verdict.DERIVE, "NOT SF stored Remaining Balance"),
    FieldSpec("est_paydown_pct", "decimal", "clock:A.2 amount_paid ÷ rtr", Verdict.DERIVE, "capped [0,1]; NOT SF stored Percentage Paid"),
    FieldSpec("est_renewal_eligible_date", "date", "clock:A.4 inverse-solve to threshold", Verdict.DERIVE, "NOT SF stored Estimated Renewal Date"),
    FieldSpec("is_eligible_now", "bool", "clock:A.4 paydown ≥ threshold", Verdict.DERIVE),
    FieldSpec("renewal_threshold", "decimal", "clock:A.4 funder lookup (default 0.55)", Verdict.DERIVE, "D-205: per-funder lookup FU-201; default DEFAULT_RENEWAL_PAYDOWN"),
    FieldSpec("closure_status", "enum", "clock:A.5b three-state", Verdict.DERIVE, "active / closed_clean / closed_default; default note dominates ~100% paydown"),
    FieldSpec("has_default_note", "bool", "clock:A.5b Notes default-cause", Verdict.DERIVE, "binary signal; sub-typing is S7"),
    FieldSpec("balance_source", "enum", "clock:A.3/A.6 confidence flag", Verdict.DERIVE, "actual / estimated; all estimated in v1 (D-203 no feed)"),
)

# gold.merchants + Deal-Clock roll-up -> mca_mri.gold.merchant_clock (point-in-time).
# These field NAMES are the contract Merchant Gold "Position & burden (the clock)" section
# (+ time-dependent first_funded_date / tenure_days from the Identity section).
MERCHANT_CLOCK_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_id", "string", "gold.merchants.merchant_id", Verdict.HAVE, "PK part (with clock_run_date)"),
    FieldSpec("clock_run_date", "date", "run:today", Verdict.DERIVE, "point-in-time stamp; PK part"),
    FieldSpec("first_funded_date", "date", "derive:MIN(deal.funded_date)", Verdict.DERIVE, "contract Identity; static but seeded here (drives tenure)"),
    FieldSpec("tenure_days", "int", "derive:today − first_funded_date", Verdict.DERIVE, "contract Identity; time-dependent → recomputed here, not S1"),
    FieldSpec("active_position_cnt", "int", "derive:count closure_status=active", Verdict.DERIVE, "Position&burden; itself an inference (A.5b)"),
    FieldSpec("total_weekly_debit", "decimal", "derive:Σ active weekly-normalized payments", Verdict.DERIVE, "Position&burden"),
    FieldSpec("est_weekly_revenue", "decimal", "gap", Verdict.MUST_CAPTURE, "Position&burden; bank feed (none v1) → null + flag, never 0"),
    FieldSpec("burden_ratio", "decimal", "derive:total_weekly_debit ÷ est_weekly_revenue", Verdict.DERIVE, "Position&burden; null + flag where revenue missing"),
    FieldSpec("est_current_balance", "decimal", "derive:Σ active est_current_balance", Verdict.DERIVE, "Position&burden; NOT SF stored"),
    FieldSpec("est_paydown_pct", "decimal", "derive:primary active position paydown", Verdict.DERIVE, "Position&burden; primary drives eligibility (A.5)"),
    FieldSpec("est_renewal_eligible_date", "date", "derive:primary active position eligible_date", Verdict.DERIVE, "Position&burden"),
    FieldSpec("is_eligible_now", "bool", "derive:primary paydown ≥ threshold", Verdict.DERIVE, "convenience (not a contract column)"),
    FieldSpec("balance_source", "enum", "derive:weakest across positions", Verdict.DERIVE, "Position&burden; any estimated → estimated (A.5)"),
)

GOLD_DEAL_CLOCK_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    # Static terms absent so the clock could not compute (math fields null, never faked).
    ("clock_inputs_missing", "bool"),
    # Day-one checkpoint (A.0): |rtr − total_payback|. Both are legit terms (NOT the SF
    # stored balance); a large delta flags a terms contradiction, never overwrites.
    ("rtr_checkpoint_delta", "decimal"),
)

GOLD_MERCHANT_CLOCK_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("est_weekly_revenue_is_missing", "bool"),
    ("burden_ratio_is_missing", "bool"),
)

# =============================================================================
# GOLD layer (S3) — Rung Classifier outputs (Appendix B). POINT-IN-TIME table
# (D-304), keyed (merchant_id, classify_run_date), append-only + `_current` view —
# mirrors the S2 clock pattern. Source-label convention here:
#   "rung:<B.x>"               computed by the Appendix B classifier (NOT Salesforce)
#   "run:today"                the run's "today" (classify_run_date), point-in-time stamp
#   "clock:merchant_clock_current.<col>"  read from the S2 merchant clock (spine; never recomputed)
#   "gold.deals.<col>"         a roll-up over the merchant's canonical Deal Table rows
# These are MRI-internal classification fields (not the contract's 24 STATIC Deal fields):
# the Data Contract Merchant Gold Table "rung / lifecycle" section. NO SF stored balances
# are read — the classifier consumes only S2 clock outputs (CLAUDE.md 2.1).
# =============================================================================

# gold.merchant_clock_current + gold deals/merchants -> mca_mri.gold.merchant_rung (point-in-time).
MERCHANT_RUNG_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_id", "string", "clock:merchant_clock_current.merchant_id", Verdict.HAVE, "PK part (with classify_run_date)"),
    FieldSpec("classify_run_date", "date", "run:today", Verdict.DERIVE, "the run's 'today'; PK part; point-in-time stamp (mirrors clock_run_date)"),
    FieldSpec("lifecycle_state", "enum", "rung:B.2 lifecycle gate", Verdict.DERIVE, "defaulted / dormant / new-establishing / active"),
    FieldSpec("rung", "int", "rung:B.3 waterfall (first-match + stress override)", Verdict.DERIVE, "1..5 (Distressed..Graduate); null when gated or Unclassified"),
    FieldSpec("confidence", "decimal", "rung:D-306 borderline margin", Verdict.DERIVE, "[0.5,1.0] deterministic rules score, NOT an ML probability; missing data never lowers it"),
    FieldSpec("direction_of_travel", "enum", "rung:4.7 prev->curr health rank", Verdict.DERIVE, "climbing / holding / sliding; prioritizes the daily queue"),
    FieldSpec("default_subtype", "enum", "rung:B.2 default sub-type", Verdict.CARRY, "v1 always 'unknown' for defaulted (S7 refines, FU-301); null when not defaulted"),
    FieldSpec("route", "enum", "rung:B.2/B.5 advisory route", Verdict.DERIVE, "do-not-fund / win-back / clock-running / waterfall / review / ..."),
    FieldSpec("rapid_reup_flag", "bool", "rung:D-302 (owned in common/rung)", Verdict.DERIVE, "prior position <50% paid down & still active at new funding, OR <=45-day gap fallback"),
    FieldSpec("renewal_chain_incomplete", "bool", "gold.deals.renewal_unlinkable roll-up", Verdict.CARRY, "D-303 data-linkage gap (FU-302); flagged, NEVER a disqualifier"),
    FieldSpec("missing_signals", "string", "rung:4.7 data-capture roadmap", Verdict.DERIVE, "comma-joined absent signals; absence never lowers confidence (D-306)"),
)

# Rung DQ / classification-bucket columns (beyond the field set). (col, dtype).
GOLD_MERCHANT_RUNG_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("is_gated", "bool"),  # lifecycle routed off the waterfall (defaulted/dormant/new) -> rung null
    ("is_unclassified", "bool"),  # active but no rung matched -> the explicit Unclassified pile
)


def merchant_rung_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_RUNG_MAP] + [
        c for c, _ in GOLD_MERCHANT_RUNG_DQ_COLUMNS
    ]


# =============================================================================
# GOLD layer (S4) — Activation (state machine + plays) + Book Health (Appendix/§6).
# POINT-IN-TIME tables (D-404), mirroring S2/S3. Source-label convention:
#   "activation:<...>"   computed by common/activation (state machine + plays) — NOT SF
#   "rung:carry"         carried from gold.merchant_rung_current (single source)
#   "run:today"          the activation/report run date (point-in-time stamp)
#   "bookhealth:<...>"   aggregated by transform/gold_book_health
# NO SF stored balances; NO Salesforce write in S4 (serving layer only, D-403 — the SF
# write-back is FU-401). The floor reads merchant_activation_current + daily_queue.
# =============================================================================

# merchant_rung_current + merchant_clock_current -> gold.merchant_activation (point-in-time).
MERCHANT_ACTIVATION_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_id", "string", "rung:carry", Verdict.HAVE, "PK part (with activation_run_date)"),
    FieldSpec("activation_run_date", "date", "run:today", Verdict.DERIVE, "point-in-time stamp; PK part (mirrors classify_run_date)"),
    FieldSpec("current_state", "enum", "activation:state machine (D-401)", Verdict.DERIVE, "clock-running / approaching / in-market / renewed / lost-winback"),
    FieldSpec("active_play", "enum", "activation:play matrix (D-402)", Verdict.DERIVE, "named floor action; internal rep guidance (NOT comms)"),
    FieldSpec("play_owner", "string", "activation:SF Opportunity OwnerId", Verdict.CARRY, "accountable rep; null + play_owner_is_missing until ingested (FU-101)"),
    FieldSpec("play_sla_due", "date", "activation:nth business day after run (D-402)", Verdict.DERIVE, "response deadline; reuses the clock business-day calendar"),
    FieldSpec("next_tactical_action", "string", "activation:grounded play template", Verdict.DERIVE, "what to do this week (floor script); invents no merchant numbers"),
    FieldSpec("next_strategic_nudge", "string", "activation:grounded play template", Verdict.DERIVE, "the next rung-climbing move"),
    FieldSpec("lifecycle_state", "enum", "rung:carry", Verdict.DERIVE, "carried from merchant_rung_current (single source)"),
    FieldSpec("rung", "int", "rung:carry", Verdict.DERIVE, "1..5 or null; carried"),
    FieldSpec("direction_of_travel", "enum", "rung:carry", Verdict.DERIVE, "climbing / holding / sliding; carried"),
    FieldSpec("confidence", "decimal", "rung:carry", Verdict.DERIVE, "deterministic rules score [0,1]; carried (queue ordering)"),
    FieldSpec("route", "enum", "rung:carry", Verdict.DERIVE, "carried lifecycle/rung route"),
)

GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("play_owner_is_missing", "bool"),  # no SF owner available yet (never fabricate accountability)
)

# gold.book_health — TALL point-in-time scoreboard (D-404). One row per metric value per
# report_date; the `view` column carries the Framework-5.8 family; `_current` views filter it.
BOOK_HEALTH_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("report_date", "date", "run:today", Verdict.DERIVE, "point-in-time stamp; PK part"),
    FieldSpec("view", "enum", "bookhealth:Framework 5.8", Verdict.DERIVE, "book_health / renewal_performance / leading_indicators"),
    FieldSpec("metric", "string", "bookhealth:metric key", Verdict.DERIVE, "e.g. rung_distribution, sliding_count, rung_drift"),
    FieldSpec("dimension", "string", "bookhealth:breakdown axis", Verdict.DERIVE, "null for scalars; e.g. 'rung' / 'funder' / 'governing_state'"),
    FieldSpec("dimension_value", "string", "bookhealth:breakdown value", Verdict.DERIVE, "null for scalars; e.g. '2' / 'Funder A' / 'NY'"),
    FieldSpec("value_num", "decimal", "bookhealth:aggregate", Verdict.DERIVE, "count / net / scalar value"),
    FieldSpec("value_pct", "decimal", "bookhealth:share", Verdict.DERIVE, "fraction in [0,1] where applicable; else null"),
)

# Salesforce write-back FIELD MAP — DOCUMENTATION for FU-401 (NOT written in S4; D-403
# serving-layer-only). Each entry: (gold activation column, intended SF target, audience).
# Audience codes (Data Contract): F = floor-only, D = dual (floor + app). Only F/D fields are
# ever pushed to the floor; never a `_sf_stored_*` column. SF target object/fields are
# confirmed + created at FU-401 time (sandbox-first, allow_sf_write-gated).
SF_WRITEBACK_REFERENCE: tuple[tuple[str, str, str], ...] = (
    ("current_rung", "MRI__Rung__c", "D"),
    ("lifecycle_state", "MRI__Lifecycle_State__c", "D"),
    ("current_state", "MRI__Current_State__c", "D"),
    ("direction_of_travel", "MRI__Direction__c", "D"),
    ("active_play", "MRI__Active_Play__c", "F"),
    ("play_owner", "MRI__Play_Owner__c", "F"),
    ("play_sla_due", "MRI__Play_SLA_Due__c", "F"),
    ("next_tactical_action", "MRI__Next_Action__c", "F"),
    ("next_strategic_nudge", "MRI__Next_Nudge__c", "D"),
    ("rung_confidence", "MRI__Rung_Confidence__c", "D"),
    ("est_renewal_eligible_date", "MRI__Renewal_Eligible_Date__c", "D"),
)


def merchant_activation_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_ACTIVATION_MAP] + [
        c for c, _ in GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS
    ]


def book_health_columns() -> list[str]:
    return [fs.silver_col for fs in BOOK_HEALTH_MAP]


# =============================================================================
# GOLD layer (S5) — Offer Engine outputs (Build Plan §6 / Framework §5.7). POINT-IN-TIME
# `merchant_offers` keyed (merchant_id, offer_run_date), append-only + `_current` view
# (mirrors S2/S3/S4). Source-label convention:
#   "offer:<...>"        computed by common/offer (offer types, structure, suitability)
#   "funder:reuse"       from REUSING the existing routing engine (mca_funders) — NOT rebuilt
#   "run:today"          the proactive-scan run date (the tap-early cadence marker)
# NO writes to mca_funders; NO SF stored balances; NO offer SENT (proposes only — S8 delivers).
# =============================================================================

MERCHANT_OFFERS_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_id", "string", "offer:from gold.merchants", Verdict.HAVE, "PK part (with offer_run_date)"),
    FieldSpec("offer_run_date", "date", "run:today", Verdict.DERIVE, "proactive-scan date; PK part; = offer_refresh_date"),
    FieldSpec("eligible_offer_types", "string", "offer:D-504 candidates ∩ funder match", Verdict.DERIVE, "comma-joined: renewal / buyout / larger-advance / none-yet"),
    FieldSpec("matched_funders", "string", "funder:reuse routing_program_evaluations", Verdict.REUSE, "comma-joined funders whose box the merchant fits (from the existing engine); empty when none"),
    FieldSpec("max_sustainable_advance", "decimal", "offer:capacity (revenue-dependent)", Verdict.DERIVE, "D-505: null in v1 (no revenue feed, FU-301) → max_sustainable_advance_is_missing; never a fabricated ceiling"),
    FieldSpec("best_offer_summary", "string", "offer:plain-language best credible option", Verdict.DERIVE, "drafted from the matched option + suitability; honest, no invented numbers"),
    FieldSpec("recommended_structure", "enum", "offer:D-506 renewal-vs-buyout", Verdict.DERIVE, "renewal / buyout / wait-and-paydown (the math decides)"),
    FieldSpec("double_dip_cost", "decimal", "offer:est_current_balance × (factor−1)", Verdict.DERIVE, "honest rollover cost surfaced for the structure decision; null when inputs missing"),
    FieldSpec("suitability_verdict", "enum", "offer:D-506 gate", Verdict.DERIVE, "surface / suppress / wait — engine proposes, advisory disposes"),
    FieldSpec("offer_refresh_date", "date", "run:today", Verdict.DERIVE, "tap-early cadence marker (= offer_run_date)"),
)

GOLD_MERCHANT_OFFERS_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("max_sustainable_advance_is_missing", "bool"),  # revenue-dependent; null+flag in v1 (FU-301)
    ("offer_profile_unmatched", "bool"),  # merchant did not join to the funder engine (id gap) → none-yet
)


def merchant_offers_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_OFFERS_MAP] + [
        c for c, _ in GOLD_MERCHANT_OFFERS_DQ_COLUMNS
    ]


# =============================================================================
# GOLD layer (S6) — Prediction outputs (Build Plan §6 / Framework §11.2). POINT-IN-TIME
# `merchant_predictions` keyed (merchant_id, prediction_run_date), append-only + `_current`
# (mirrors S2–S5). Source-label convention:
#   "predict:rfm"        deterministic RFM features (common/prediction; tier-1 tested)
#   "predict:BTYD"       PyMC-Marketing BG/NBD + Gamma-Gamma + CLV output (model)
#   "predict:survival"   lifelines Cox PH output (model)
#   "run:today"          the inference run date (point-in-time stamp)
# Models ADOPTED (not hand-built); MLflow-versioned (model_version). Distress is NOT model-
# driven (S3 owns it). NO SF stored balances.
# =============================================================================

MERCHANT_PREDICTIONS_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("merchant_id", "string", "predict:from gold.merchants", Verdict.HAVE, "PK part (with prediction_run_date)"),
    FieldSpec("prediction_run_date", "date", "run:today", Verdict.DERIVE, "inference run date; PK part (mirrors clock_run_date)"),
    FieldSpec("rfm_recency", "int", "predict:rfm last−first advance (days)", Verdict.DERIVE, "BG/NBD recency"),
    FieldSpec("rfm_frequency", "int", "predict:rfm deal_count−1", Verdict.DERIVE, "BG/NBD repeat-advance count"),
    FieldSpec("rfm_T", "int", "predict:rfm today−first advance (days)", Verdict.DERIVE, "BG/NBD observation-window age"),
    FieldSpec("rfm_monetary", "decimal", "predict:rfm avg funded_amount", Verdict.DERIVE, "Gamma-Gamma monetary value"),
    FieldSpec("p_alive", "decimal", "predict:BTYD BG/NBD", Verdict.DERIVE, "[0,1] probability still active"),
    FieldSpec("p_defection", "decimal", "predict:BTYD 1−p_alive (adj.)", Verdict.DERIVE, "[0,1] take-next-capital-elsewhere risk; win-back trigger"),
    FieldSpec("predicted_next_event_date", "date", "predict:survival Cox PH", Verdict.DERIVE, "predicted next capital-event timing; queue: in-market"),
    FieldSpec("predicted_clv", "decimal", "predict:BTYD CLV (NPV)", Verdict.DERIVE, "net-present lifetime value over the configured horizon/discount"),
    FieldSpec("prediction_confidence", "decimal", "predict:posterior width / history", Verdict.DERIVE, "[0,1] uncertainty band — governs soft vs firm advice framing; NOT accuracy"),
    FieldSpec("model_version", "string", "predict:MLflow", Verdict.DERIVE, "ruleset/model version — reproducibility & audit (Event Log contract)"),
)

GOLD_MERCHANT_PREDICTIONS_DQ_COLUMNS: tuple[tuple[str, str], ...] = (
    ("insufficient_history", "bool"),  # repeat events < INSUFFICIENT_HISTORY_MIN_EVENTS → prior-only + wide confidence
)


def merchant_predictions_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_PREDICTIONS_MAP] + [
        c for c, _ in GOLD_MERCHANT_PREDICTIONS_DQ_COLUMNS
    ]


def deal_table_columns() -> list[str]:
    return [fs.silver_col for fs in DEAL_TABLE_MAP] + [c for c, _ in GOLD_DEALS_DQ_COLUMNS]


def merchant_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_MAP] + [c for c, _ in GOLD_MERCHANTS_DQ_COLUMNS]


def merchant_crosswalk_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_CROSSWALK_MAP]


def deal_clock_columns() -> list[str]:
    return [fs.silver_col for fs in DEAL_CLOCK_MAP] + [c for c, _ in GOLD_DEAL_CLOCK_DQ_COLUMNS]


def merchant_clock_columns() -> list[str]:
    return [fs.silver_col for fs in MERCHANT_CLOCK_MAP] + [
        c for c, _ in GOLD_MERCHANT_CLOCK_DQ_COLUMNS
    ]


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
