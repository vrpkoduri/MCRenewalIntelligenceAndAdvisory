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


class Thresholds:
    """RESERVED for later sprints — calibration hypotheses from Appendix A/B and the
    Framework. NOT used in S0. Centralized here so calibration happens in one place.
    Sources noted inline.
    """

    DEFAULT_RENEWAL_PAYDOWN = 0.55  # Appendix A.4 — default when no funder-specific value
    BUSINESS_DAYS_PER_MONTH = 21.7  # Appendix A.3 — daily-frequency elapsed-payment count
    BURDEN_DISTRESS_CEILING = 0.30  # Appendix B.3 / Framework 4.2 (~25-30%)
    BURDEN_SERIAL_BAND = (0.15, 0.30)  # Framework 4.3
    DISCIPLINED_BURDEN_MAX = 0.15  # Framework 4.4
    DISCIPLINED_RENEWAL_PAYDOWN_MIN = 0.50  # Framework 4.4
    DORMANCY_MULTIPLIER = 2.0  # Appendix B.2 — idle > 2x median renewal gap
    SERIAL_POSITION_MIN = 2  # Appendix B.3 — concurrent positions
