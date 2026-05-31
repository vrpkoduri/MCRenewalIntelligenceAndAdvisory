"""Central constants — single source of truth for names, enums, and thresholds.

Per GENERAL_INSTRUCTIONS Rule 3: anything that could be referenced in more than one
place lives here so it is changed in exactly one location. Pure Python (no Spark import)
so it is importable in tier-1 local tests.
"""

CATALOG = "mca_mri"


class Schema:
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    # Isolated integration-test mirrors (house convention: *_test).
    BRONZE_TEST = "bronze_test"
    SILVER_TEST = "silver_test"
    GOLD_TEST = "gold_test"


def fq(schema: str, table: str, catalog: str = CATALOG) -> str:
    """Fully-qualified Unity Catalog name: catalog.schema.table."""
    return f"{catalog}.{schema}.{table}"


class SilverTable:
    DEALS = "deals"
    OFFERS = "offers"
    FIELD_HISTORY = "field_history"


class BronzeTable:
    """Lakeflow Connect lands managed tables lowercased from the SF object API names
    (confirmed at G1, 2026-05-31). Centralized so the bronze->silver reads use one source.
    """

    ACCOUNT = "account"
    OPPORTUNITY = "opportunity"
    OFFER = "offer__c"
    OPPORTUNITY_FIELD_HISTORY = "opportunityfieldhistory"


class SFObject:
    """Salesforce source objects to ingest in S0.

    API names are best-guess from the spec; CONFIRM each against the live instance
    during the G1 data audit before the ingestion pipeline is created.
    """

    OPPORTUNITY = "Opportunity"
    MERCHANT = "Account"  # parent merchant object — confirm (Account vs custom) in G1
    OFFER = "Offer__c"  # custom offer object — confirm API name in G1
    OPPORTUNITY_FIELD_HISTORY = "OpportunityFieldHistory"


class DealType:
    """SF 'Type' — trusted as the renewal flag (CLAUDE.md 2.5)."""

    NEW = "New Business"
    RENEWAL = "Renewal"
    BUYOUT = "Buyout"
    ALL = frozenset({NEW, RENEWAL, BUYOUT})


class PaymentFrequency:
    DAILY = "Daily"
    WEEKLY = "Weekly"
    ALL = frozenset({DAILY, WEEKLY})


class BalanceSource:
    """Appendix A — confidence flag on clock outputs."""

    ACTUAL = "actual"
    ESTIMATED = "estimated"


class Verdict:
    """Data Contract availability verdicts."""

    HAVE = "Have"
    CARRY = "Carry"
    DISTRUST = "Distrust"
    DERIVE = "Derive"
    MUST_CAPTURE = "Must-capture"
    REUSE = "Reuse"
    FUTURE = "Future"


# Stored SF values that are frozen funding-moment snapshots — ingest to checkpoint
# columns ONLY, never surface downstream (CLAUDE.md 2.1 / 6).
SF_STORED_PREFIX = "_sf_stored_"
NO_SURFACE_COLUMNS = (
    "_sf_stored_remaining_balance",
    "_sf_stored_percentage_paid",
    "_sf_stored_est_renewal_date",
)

# Stage value that defines the funded book.
FUNDED_STAGE = "Funded"

# RTR cross-check tolerance (diagnostic only; never overwrites).
RTR_TOLERANCE = 1.0

# Date-sanity (C-007): flag a funded/created gap larger than this many days in EITHER
# direction. Catches migration artifacts (e.g. Funded 2020 / Created 2022) without
# flagging normal create->fund latency. Diagnostic only; never drops rows. Calibratable.
DATE_SANITY_GAP_DAYS = 365


class GoldTable:
    """S1 gold layer — conformed single source of truth for S2+ (D-104)."""

    DEALS = "deals"
    MERCHANTS = "merchants"
    # Persisted crosswalk (D-101): merchant_sf_id -> merchant_id, upserted each
    # refresh so ids never re-key on re-merge (stable downstream join key).
    MERCHANT_CROSSWALK = "merchant_crosswalk"


class Identity:
    """Entity-resolution config (S1). Matching logic is PORTED from the AATM
    `merchant_sync` IP (`lakebase_aatm_*` / jobs/lib/aatm_jobs — D-105 resolved):
    its 4-tier `resolve_merchant` priority chain and pure normalizers, adapted
    from AATM's row-by-row Postgres upsert to a batch Spark dedup of
    `bronze.account`, and made conservative per D-102.

    Match-key priority (first/strongest wins), MRI adaptation of AATM's
    azure_id -> tax_id -> name+phone -> name+email chain:
      1. SF MasterRecordId merge chains  (SF-native dedup; ~AATM tier-1 external id)
      2. exact normalized Tax ID         -> AUTO-MERGE (D-102 high confidence)
      3. exact normalized phone          -> candidate, flagged, NOT auto-merged (v1)
      4. normalized name + governing state -> candidate, flagged, NOT auto-merged (v1)
    """

    # Tiers in descending strength. AUTO_MERGE_TIERS collapse into one cluster;
    # CANDIDATE_TIERS only emit a flagged candidate edge for review (D-102 v1).
    TIER_MASTER_RECORD = "master_record"
    TIER_TAX_ID = "tax_id"
    TIER_PHONE = "phone"
    TIER_NAME_STATE = "name_state"
    AUTO_MERGE_TIERS = (TIER_MASTER_RECORD, TIER_TAX_ID)
    CANDIDATE_TIERS = (TIER_PHONE, TIER_NAME_STATE)

    # AATM cross-system enrichment (C-014). MRI mints its OWN merchant_id but
    # carries AATM's azure_merchant_id ("DM Merchant Id") as a join field,
    # populated by a BUILD-TIME, READ-ONLY join on normalized tax_id against the
    # AATM merchant registry. There is NO direct id-to-id link (SF
    # Application_Submission__c != AATM app_id); tax_id is the bridge (~84%
    # of the funded book matches). Optional: degrades to null if unavailable —
    # MRI identity never depends on AATM at runtime.
    AATM_CATALOG = "lakebase_aatm_prod"
    AATM_MERCHANTS_TABLE = "public.merchants"  # columns: tax_id, azure_merchant_id, lead_count

    # Business-name suffix stop-words dropped before name comparison.
    # Ported verbatim from AATM normalize_business_name.
    BUSINESS_SUFFIXES = (
        "incorporated",
        "corporation",
        "limited",
        "company",
        "llc",
        "inc",
        "corp",
        "ltd",
        "lp",
        "co",
    )


class Thresholds:
    """RESERVED for later sprints — calibration hypotheses from Appendix A/B and the
    Framework. NOT used in S0. Centralized here so calibration happens in one place.
    Sources noted inline.
    """

    DEFAULT_RENEWAL_PAYDOWN = 0.55  # Appendix A.4 — default when no funder-specific value
    BUSINESS_DAYS_PER_MONTH = 21.7  # Appendix A.3 — daily-frequency elapsed-payment count
    WEEKS_PER_MONTH = 4.33  # Appendix A.3 — weekly-frequency term-months divisor (USED in S1)
    BURDEN_DISTRESS_CEILING = 0.30  # Appendix B.3 / Framework 4.2 (~25-30%)
    BURDEN_SERIAL_BAND = (0.15, 0.30)  # Framework 4.3
    DISCIPLINED_BURDEN_MAX = 0.15  # Framework 4.4
    DISCIPLINED_RENEWAL_PAYDOWN_MIN = 0.50  # Framework 4.4
    DORMANCY_MULTIPLIER = 2.0  # Appendix B.2 — idle > 2x median renewal gap
    SERIAL_POSITION_MIN = 2  # Appendix B.3 — concurrent positions
