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
    ALL = frozenset({ACTUAL, ESTIMATED})


class ClosureStatus:
    """Appendix A.5b — open-vs-closed is a COMPUTED clock output (SF has no closure
    status). Three states; a defaulted deal computes to ~100% on schedule, so
    paydown >= 100% ALONE is never `closed_clean` — the Notes default-cause separates
    clean from default (the Starr case). Feeds the S3 lifecycle gate (Appendix B)."""

    ACTIVE = "active"  # computed paydown < 100% (or a default note absent and not yet paid off)
    CLOSED_CLEAN = "closed_clean"  # paydown >= 100% reached, no default note
    CLOSED_DEFAULT = "closed_default"  # a default is indicated in Notes (dominates paydown)
    ALL = frozenset({ACTIVE, CLOSED_CLEAN, CLOSED_DEFAULT})


# --- Rung Classifier (Appendix B, S3 / C-017) ------------------------------------
# The two axes (B.1): every merchant carries a LifecycleState (where they are in the
# funding cycle); active merchants additionally carry a RungState (health rung 1-5).
# Pure-Python enums (no Spark) so common/rung is tier-1 testable.


class LifecycleState:
    """Step-0 lifecycle gate routes (Appendix B.2). String values match the
    `expected.lifecycle_state` in the four-merchant fixtures."""

    DEFAULTED = "defaulted"  # a position computed closed_default (Starr) -> sub-type + route
    DORMANT = "dormant"  # idle > 2x median renewal gap, no active positions (OBP) -> win-back
    NEW_ESTABLISHING = "new-establishing"  # single recent position, no renewal history (Snell)
    ACTIVE = "active"  # >=1 open position -> proceed to the rung waterfall (Wolf)
    ALL = frozenset({DEFAULTED, DORMANT, NEW_ESTABLISHING, ACTIVE})


class RungState:
    """Health rung 1-5 (Appendix B.3 / Framework 4.2-4.6). 1 = worst (Distressed),
    5 = best (Graduate). `rung` is an int 1..5, or None for gated/Unclassified."""

    DISTRESSED = 1
    SERIAL = 2
    DISCIPLINED = 3
    GROWTH = 4
    GRADUATE = 5
    ALL = frozenset({DISTRESSED, SERIAL, DISCIPLINED, GROWTH, GRADUATE})
    NAMES = {
        DISTRESSED: "distressed",
        SERIAL: "serial",
        DISCIPLINED: "disciplined",
        GROWTH: "growth",
        GRADUATE: "graduate",
    }


class DefaultSubtype:
    """Defaulted sub-typing + routing (Appendix B.2). v1 cannot reliably sub-type
    (gated on the data audit / S7 Data Steward), so an undetermined default is
    treated `unknown` -> do-not-fund — the conservative interim (misrouting a true
    default to win-back is the costlier error). Starr -> unknown."""

    TRUE_DEFAULT = "true_default"  # -> distressed/exit (do-not-fund, restructuring referral)
    EARLY_PAYOFF = "early_payoff"  # early-payoff / clawback -> win-back (a healthy merchant who left)
    RESTRUCTURED = "restructured"  # -> impaired-managed
    UNKNOWN = "unknown"  # undetermined -> do-not-fund + flag for review (v1 default)
    ALL = frozenset({TRUE_DEFAULT, EARLY_PAYOFF, RESTRUCTURED, UNKNOWN})


class LifecycleRoute:
    """The advisory action a lifecycle/rung outcome routes to (Appendix B.2 / B.5).
    Carried alongside the rung so S4 activation knows what to do; not an ML output."""

    DO_NOT_FUND = "do-not-fund"  # defaulted, sub-type unknown -> review (Starr)
    DISTRESSED_EXIT = "distressed-exit"  # confirmed true default -> graceful off-boarding
    IMPAIRED_MANAGED = "impaired-managed"  # restructured default
    WIN_BACK = "win-back"  # dormant, or early-payoff/clawback default (OBP)
    CLOCK_RUNNING = "clock-running"  # new/establishing — healthy, not yet Disciplined (Snell)
    WATERFALL = "waterfall"  # active -> rung placed by the waterfall (Wolf -> Serial eval)
    REVIEW = "review"  # Unclassified — key signals missing, needs data capture
    ALL = frozenset(
        {DO_NOT_FUND, DISTRESSED_EXIT, IMPAIRED_MANAGED, WIN_BACK, CLOCK_RUNNING, WATERFALL, REVIEW}
    )


class DirectionOfTravel:
    """Run-over-run trajectory (Framework 4.7) — lets the daily queue prioritize a
    sliding disciplined merchant over a stable one. Deterministic from prev->curr rank."""

    CLIMBING = "climbing"  # moved to a healthier rung/state
    HOLDING = "holding"  # unchanged (or no prior run / not comparable)
    SLIDING = "sliding"  # moved to a less-healthy rung/state — the high-value early alert
    ALL = frozenset({CLIMBING, HOLDING, SLIDING})


class EventType:
    """Append-only event log (D-305). v1 emits classification + transition events into
    ONE wide table keyed (merchant_id, event_type, event_ts); S4/S5/S8 append their own
    event types (touch/comms/offer) to the same log later."""

    CLASSIFICATION = "classification"  # one per merchant per classify_run_date
    TRANSITION = "transition"  # emitted only when lifecycle_state or rung changed run-over-run
    ALL = frozenset({CLASSIFICATION, TRANSITION})


# rapid_reup_flag (D-302) — owned in common/rung (nothing computes it upstream today).
# Fallback day-gap threshold used ONLY when the prior position's paydown can't be computed
# (the paydown-based test is PRIMARY). Calibratable once the book's gap distribution is seen.
RAPID_REUP_MAX_GAP_DAYS = 45


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

# --- Amortization Clock (Appendix A, S2 / C-016) ---------------------------------
# Holiday set excluded from the daily business-day elapsed count (A.3). D-204 v1 =
# plain M–F (no holidays) -> empty. Threaded through clock.calendar as a parameter so
# the US-Federal-holiday upgrade is a single edit here. ISO date strings "YYYY-MM-DD".
DEFAULT_HOLIDAYS: frozenset[str] = frozenset()

# Free-text default-cause signals on a deal's Notes (A.5b). A match marks the deal
# closed_default — it dominates a ~100% computed paydown so a defaulted deal is NEVER
# mislabeled closed_clean (the Starr case). Lower-cased substring match. Sub-typing
# (true-default vs early-payoff/clawback vs restructured, Appendix B.2) is the S7 Data
# Steward agent's job; S2 only sets the binary default-note signal deterministically.
DEFAULT_NOTE_KEYWORDS: tuple[str, ...] = (
    "default",  # covers "default" / "defaulted"
    "clawback",
    "charge-off",
    "chargeoff",
    "charged off",
    "write-off",
    "writeoff",
    "written off",
    "uncollect",  # uncollectable / uncollectible
    "nsf",
    "bankrupt",  # bankrupt / bankruptcy
)


class GoldTable:
    """S1 gold layer — conformed single source of truth for S2+ (D-104)."""

    DEALS = "deals"
    MERCHANTS = "merchants"
    # Persisted crosswalk (D-101): merchant_sf_id -> merchant_id, upserted each
    # refresh so ids never re-key on re-merge (stable downstream join key).
    MERCHANT_CROSSWALK = "merchant_crosswalk"
    # S2 Amortization Clock (Appendix A) — separate POINT-IN-TIME tables (D-201/C-016),
    # partitioned by clock_run_date, append-only across days, idempotent within a day.
    # `*_current` are views over the latest clock_run_date for live reads.
    DEAL_CLOCK = "deal_clock"
    MERCHANT_CLOCK = "merchant_clock"
    DEAL_CLOCK_CURRENT = "deal_clock_current"
    MERCHANT_CLOCK_CURRENT = "merchant_clock_current"
    # S3 Rung Classifier (Appendix B) — separate POINT-IN-TIME rung table (D-304),
    # keyed (merchant_id, classify_run_date), append-only with a `*_current` view, and
    # ONE wide append-only event log (D-305) keyed (merchant_id, event_type, event_ts).
    # Mirrors the S2 clock pattern: daily classify never overwrites a prior run (auditable).
    MERCHANT_RUNG = "merchant_rung"
    MERCHANT_RUNG_CURRENT = "merchant_rung_current"
    MERCHANT_EVENT_LOG = "merchant_event_log"


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

    DEFAULT_RENEWAL_PAYDOWN = 0.55  # Appendix A.4 / D-205 — default renewal threshold when
    # no funder-specific value (the per-funder mca_funders lookup is FU-201). This is the
    # single 0.55 source of truth — the clock reads it; no duplicate constant (Rule 3).
    BUSINESS_DAYS_PER_MONTH = 21.7  # Appendix A.3 — daily-frequency elapsed-payment count
    BUSINESS_DAYS_PER_WEEK = 5  # Appendix A.3/A.5 — weekly-normalized debit for daily deals
    WEEKS_PER_MONTH = 4.33  # Appendix A.3 — weekly-frequency term-months divisor (USED in S1)
    BURDEN_DISTRESS_CEILING = 0.30  # Appendix B.3 / Framework 4.2 (~25-30%)
    BURDEN_SERIAL_BAND = (0.15, 0.30)  # Framework 4.3
    DISCIPLINED_BURDEN_MAX = 0.15  # Framework 4.4
    DISCIPLINED_RENEWAL_PAYDOWN_MIN = 0.50  # Framework 4.4
    DORMANCY_MULTIPLIER = 2.0  # Appendix B.2 — idle > 2x median renewal gap
    SERIAL_POSITION_MIN = 2  # Appendix B.3 — concurrent positions
