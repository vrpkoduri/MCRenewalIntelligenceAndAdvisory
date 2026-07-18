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
    """SF 'Type' — trusted as the renewal flag (CLAUDE.md 2.5). The real funded-book values
    are New Business / Renewal / Stack / Add-On (FU-601, 2026-06-02 — confirmed via the S6
    readiness spike: New Business 2,024 / Renewal 1,854 / Stack 67 / Add-On 14; "Buyout" is a
    valid type that is simply absent in the current book, kept for completeness)."""

    NEW = "New Business"
    RENEWAL = "Renewal"
    BUYOUT = "Buyout"  # valid type; absent in the current funded book
    STACK = "Stack"  # an additional (stacking) advance — a repeat event
    ADD_ON = "Add-On"  # an add-on advance — a repeat event
    ALL = frozenset({NEW, RENEWAL, BUYOUT, STACK, ADD_ON})
    # Repeat/stacking advances = every type EXCEPT New Business. The single source for
    # has_renewal / repeat-event / renewal-chain detection (FU-601 — Stack/Add-On are repeat
    # advances, not new business; they were previously missed by a literal {Renewal, Buyout}).
    REPEAT_TYPES = frozenset({RENEWAL, BUYOUT, STACK, ADD_ON})


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
    """Append-only event log (D-305). v1 (S3) emits classification + transition events into
    ONE wide table keyed (merchant_id, event_type, event_ts); S4 adds activation event types
    (state-transition + play_fired); S5/S8 append offer/comms/touch types to the same log."""

    CLASSIFICATION = "classification"  # one per merchant per classify_run_date (S3)
    TRANSITION = "transition"  # emitted when lifecycle_state or rung changed run-over-run (S3)
    STATE_TRANSITION = "state_transition"  # current_state changed run-over-run (S4 activation)
    PLAY_FIRED = "play_fired"  # a named play assigned/changed for a merchant (S4 activation)
    OFFER_COMPUTED = "offer_computed"  # a proactive offer scan produced/changed options (S5)
    PREDICTION = "prediction"  # a model inference produced/updated predictions for a merchant (S6)
    AGENT_EXTRACTION = "agent_extraction"  # an agent extracted a grounded signal from source (S7)
    ADVISORY_COMPOSED = "advisory_composed"  # the Advisory Composer produced a grounded advisory (S8)
    COMPLIANCE_CHECKED = "compliance_checked"  # a merchant-facing output passed/failed the gate (S8)
    ALL = frozenset(
        {CLASSIFICATION, TRANSITION, STATE_TRANSITION, PLAY_FIRED, OFFER_COMPUTED, PREDICTION,
         AGENT_EXTRACTION, ADVISORY_COMPOSED, COMPLIANCE_CHECKED}
    )


# rapid_reup_flag (D-302) — owned in common/rung (nothing computes it upstream today).
# Fallback day-gap threshold used ONLY when the prior position's paydown can't be computed
# (the paydown-based test is PRIMARY). Calibratable once the book's gap distribution is seen.
RAPID_REUP_MAX_GAP_DAYS = 45


# --- Activation: state machine + plays (Build Plan §6, Framework 5.8, S4 / C-018) ---
# The operational layer over the S3 rung/lifecycle: an action-oriented `current_state`, a
# named `active_play`, an SLA, and grounded next-actions — the floor's read surface. Pure
# enums (no Spark) so common/activation is tier-1 testable. Reads S1/S2/S3 gold; never
# recomputes the spine. NO Salesforce write in S4 (D-403 — serving layer only; SF write-back
# is FU-401). NO merchant comms (S8).


class CurrentState:
    """State machine (D-401) — where a merchant is in the operational funding cycle. Distinct
    from S3 `lifecycle_state`: gated lifecycle states map to `lost-winback`; active merchants
    resolve to a clock-driven operational state."""

    CLOCK_RUNNING = "clock-running"  # active, paying down, not yet approaching eligibility
    APPROACHING = "approaching"  # active, eligible within APPROACHING_WINDOW_DAYS
    IN_MARKET = "in-market"  # active & is_eligible_now (paydown >= renewal threshold)
    RENEWED = "renewed"  # active, just took a new advance (within RENEWED_WINDOW_DAYS)
    LOST_WINBACK = "lost-winback"  # dormant / defaulted (gated) — win-back or do-not-fund-review
    ALL = frozenset({CLOCK_RUNNING, APPROACHING, IN_MARKET, RENEWED, LOST_WINBACK})


# Windows for the state machine (D-401, calibratable). Distinct concepts, same v1 horizon:
# `approaching` looks FORWARD to est_renewal_eligible_date; `renewed` looks BACK to the last
# funded_date. Kept separate so each calibrates independently (not a Rule-3 duplicate).
APPROACHING_WINDOW_DAYS = 30
RENEWED_WINDOW_DAYS = 30


class Play:
    """Named plays (D-402) — the floor action for a merchant, assigned by a deterministic
    priority matrix over (lifecycle/route, rung, current_state, direction_of_travel).
    Internal rep guidance only — NOT merchant comms (S8)."""

    DO_NOT_FUND_REVIEW = "do-not-fund-review"  # defaulted, sub-type unknown (Starr)
    WIN_BACK = "win-back"  # dormant (One Big Promotion)
    NEW_ESTABLISHING_NURTURE = "new-establishing-nurture"  # single fresh position (Tom Snell)
    DISTRESSED_STABILIZE = "distressed-stabilize"  # rung 1 — position statement + capacity hold
    SLIDE_INTERVENTION = "slide-intervention"  # direction sliding — catch the slide early
    SERIAL_RENEWAL_VS_BUYOUT = "serial-renewal-vs-buyout"  # rung 2 (Wolf) — show the double-dip delta
    IN_MARKET_RENEWAL = "in-market-renewal"  # eligible & disciplined-or-better
    APPROACHING_PREP = "approaching-prep"  # eligibility window opening soon
    GROWTH_UPSELL = "growth-upsell"  # rung 4 — structured, honest upsell
    GRADUATE_REFERRAL = "graduate-referral"  # rung 5 — help to cheaper capital
    REVIEW_UNCLASSIFIED = "review-unclassified"  # active, no rung — data capture
    DISCIPLINED_REINFORCE = "disciplined-reinforce"  # steady disciplined renewer — value-first
    ALL = frozenset({
        DO_NOT_FUND_REVIEW, WIN_BACK, NEW_ESTABLISHING_NURTURE, DISTRESSED_STABILIZE,
        SLIDE_INTERVENTION, SERIAL_RENEWAL_VS_BUYOUT, IN_MARKET_RENEWAL, APPROACHING_PREP,
        GROWTH_UPSELL, GRADUATE_REFERRAL, REVIEW_UNCLASSIFIED, DISCIPLINED_REINFORCE,
    })


# Play SLA tiers in BUSINESS days (D-402, calibratable). play_sla_due is the nth business day
# after the activation run (reuses common.clock.calendar.nth_business_day_after — Rule 3).
PLAY_SLA_BUSINESS_DAYS = {
    Play.DISTRESSED_STABILIZE: 2,
    Play.SLIDE_INTERVENTION: 2,
    Play.IN_MARKET_RENEWAL: 5,
    Play.SERIAL_RENEWAL_VS_BUYOUT: 5,
    Play.GROWTH_UPSELL: 5,
    Play.DO_NOT_FUND_REVIEW: 5,
    Play.WIN_BACK: 10,
    Play.NEW_ESTABLISHING_NURTURE: 10,
    Play.APPROACHING_PREP: 10,
    Play.GRADUATE_REFERRAL: 10,
    Play.REVIEW_UNCLASSIFIED: 10,
    Play.DISCIPLINED_REINFORCE: 10,
}


class BookHealthView:
    """Portfolio Analytics / Book Health views (Framework 5.8 / Data Contract). The point-in-
    time `gold.book_health` table carries a `view` column; one `_current` view per family."""

    BOOK_HEALTH = "book_health"
    RENEWAL_PERFORMANCE = "renewal_performance"
    LEADING_INDICATORS = "leading_indicators"
    ALL = frozenset({BOOK_HEALTH, RENEWAL_PERFORMANCE, LEADING_INDICATORS})


# --- Offer Engine (Build Plan §6, Framework §5.7, S5 / proactive reuse) -----------
# Reuses the EXISTING funder-criteria dataset + routing engine (the `mca_funders` catalog).
# S5 builds the integration layer only: an MRI merchant profile -> reuse the engine ->
# offer outputs + the renewal-vs-buyout suitability gate. NO routing/criteria rebuild
# (CLAUDE.md §6). NO outbound delivery / comms (S8). Pure enums (no Spark).


class OfferType:
    """eligible_offer_types (Data Contract / D-504) — what the merchant could be offered."""

    RENEWAL = "renewal"  # in-market, single-position disciplined: renew the position
    BUYOUT = "buyout"  # consolidate existing position(s) into one cleaner facility
    LARGER_ADVANCE = "larger-advance"  # qualifies for a bigger advance
    NONE_YET = "none-yet"  # not eligible / gated / no funder match — the honest default
    ALL = frozenset({RENEWAL, BUYOUT, LARGER_ADVANCE, NONE_YET})


class OfferStructure:
    """The renewal-vs-buyout structure decision (Framework §5.7 / D-506). The math decides,
    the merchant's interest is the tiebreaker — buyout is NOT assumed superior."""

    RENEWAL = "renewal"  # single position, healthy paydown -> renew
    BUYOUT = "buyout"  # multiple positions -> a consolidating buyout is the kinder structure
    WAIT_AND_PAYDOWN = "wait-and-paydown"  # barely-paid position -> rolling it is the expensive
    # double-dip; the honest answer is "wait and pay down first" (neither structure helps yet)
    ALL = frozenset({RENEWAL, BUYOUT, WAIT_AND_PAYDOWN})


class SuitabilityVerdict:
    """The suitability gate (D-506): the engine PROPOSES, the advisory layer DISPOSES. A
    matchable offer (e.g. a buyout) may still be unsuitable (a double-dip) and must not be
    surfaced. Full compliance gate is S8 (D-508 — interface only here)."""

    SURFACE = "surface"  # suitable — may be presented (still subject to the S8 compliance gate)
    SUPPRESS = "suppress"  # matchable but unsuitable (e.g. a double-dip buyout) — do not pitch
    WAIT = "wait"  # advise wait-and-pay-down instead of any new advance
    ALL = frozenset({SURFACE, SUPPRESS, WAIT})


class FunderCatalog:
    """The EXISTING funder-criteria dataset + routing engine to REUSE (read-only / invoke —
    never rebuilt, never written by MRI). Spike (2026-06-02): the routing OUTPUTS cover only
    the new-deal/submission population (121 merchants) with ZERO id-overlap to the MRI funded
    book, so reusing existing evaluations is non-viable — the engine must be run against MRI
    profiles (mechanism = D-501). The criteria box is small + structured (12 funders / 17
    active programs / 94 versions)."""

    CATALOG = "mca_funders"
    # Input contract the routing engine consumes (the merchant profile shape MRI must build).
    FUNDER_INPUT_VIEW = "gold.v_funder_input"
    # Routing outputs (per-program verdicts + per-merchant decisions).
    ROUTING_EVALUATIONS = "gold.routing_program_evaluations"
    ROUTING_DECISIONS = "gold.routing_decisions"
    # Structured criteria boxes (the funder accept rules; reused as-is).
    FUNDER_PROGRAMS = "silver.funder_programs"
    FUNDER_PROGRAM_VERSIONS = "silver.funder_program_versions"
    FUNDER_INDUSTRIES = "silver.funder_industries"
    FUNDER_STATES = "silver.funder_states"
    FUNDER_OPERATIONS = "silver.funder_operations"
    FUNDERS = "silver.funders"


# --- Prediction Models (Build Plan §6, Framework §11.2, S6 / C-020) ---------------
# First ML sprint: ADOPT PyMC-Marketing (BG/NBD + Gamma-Gamma + CLV) + lifelines (Cox PH +
# KM), not hand-built. The RFM/survival feature derivation is pure (tier-1 testable here);
# the model fitting runs on Databricks. Calibration config lives here (one place, Rule 3).

# A merchant needs at least this many REPEAT advances (frequency) for an individual
# data-driven fit; below it -> `insufficient_history` (book-level prior + wide confidence,
# Cox-censored). Spike (2026-06-02): 1,329/2,125 single-deal merchants land here. (D-603)
INSUFFICIENT_HISTORY_MIN_EVENTS = 1

# CLV horizon + discount rate for PyMC-Marketing CLV (NPV). Calibratable. (D-606)
CLV_HORIZON_MONTHS = 12
CLV_DISCOUNT_RATE_ANNUAL = 0.12

# lifelines Cox covariates for the "time to next advance" model (D-607). `burden_ratio` is
# intentionally EXCLUDED — null book-wide in v1 (no revenue feed, FU-301); folds in later.
COX_COVARIATES = (
    "factor_trend",
    "active_position_cnt",
    "payment_health",
    "industry_vertical",
)


# --- Agentic Extraction (Framework §5.9, S7 / C-022) -----------------------------
# Agents EXTRACT (read messy inputs); the deterministic spine still COMPUTES. Outputs are
# grounded (source_ref + confidence) + logged; low-confidence routes to human review, never
# auto-applied. The deterministic mappers/validators below are pure (tier-1 testable) — the
# correctness-critical tools the LLM agents call. NO agent writes a spine-math column.


class ExtractionType:
    """What an agent extraction asserts (Data Steward = Phase 1; Statement Analyst = Phase 2)."""

    DEFAULT_SUBTYPE = "default_subtype"  # Data Steward: true-default / early-payoff / restructured
    ANOMALY_FLAG = "anomaly_flag"  # Data Steward: date contradiction / data anomaly for review
    CONCURRENT_POSITIONS = "concurrent_positions"  # Statement Analyst (Phase 2): true position count
    WEEKLY_DEBIT = "weekly_debit"  # Statement Analyst (Phase 2): total weekly burden incl. other funders
    EST_WEEKLY_REVENUE = "est_weekly_revenue"  # Statement Analyst (Phase 2): real deposits/revenue
    ALL = frozenset({DEFAULT_SUBTYPE, ANOMALY_FLAG, CONCURRENT_POSITIONS, WEEKLY_DEBIT, EST_WEEKLY_REVENUE})


class ReviewStatus:
    """Grounding gate (D-705). High-confidence grounded extractions are APPLIED (flow to the
    spine re-run); low-confidence / invalid go to REVIEW (never auto-applied)."""

    APPLIED = "applied"
    REVIEW = "review"  # below the confidence threshold or failed validation — needs a human
    REJECTED = "rejected"  # ungrounded (missing source_ref) — not usable
    ALL = frozenset({APPLIED, REVIEW, REJECTED})


# Minimum agent confidence to auto-APPLY an extraction (D-705); below → human review.
AGENT_CONFIDENCE_REVIEW_MIN = 0.70

# --- Statement Analyst (S7 Phase 2 / C-026) --------------------------------------
# Funding-moment statements are point-in-time; the clock is live-recompute (#2). A statement
# older than this (statement `as_of_date` vs the run date) is NOT surfaced as current truth —
# the extraction is recorded but gated to REVIEW so a stale snapshot never leaks into a live
# burden read. Calibratable. **Operator-confirmed 180d (2026-06-09)** after the age-distribution
# probe (covered-statement median age 333d): the strict window surfaces 18 of 79 covered deals as
# current; the other 61 are recorded + flagged stale (REVIEW). Honesty over pilot breadth — and the
# go-forward stream (new fundings) surfaces inside the window naturally.
STATEMENT_FRESHNESS_MAX_DAYS = 180

# `est_weekly_revenue` (the burden_ratio denominator) is the softest signal — deposits ≠ revenue
# (transfers / loan proceeds / owner injections inflate it), so the agent classifies only
# operating-revenue deposits AND its confidence is haircut here before the D-705 gate (#3). So a
# revenue read is more likely to land in REVIEW than the positions/debit reads. Calibratable.
STATEMENT_REVENUE_CONFIDENCE_HAIRCUT = 0.85


# --- Advisory Layer + Compliance Gate (Build Plan §7, Framework §2.3/§2.4/§5.9, S8 / C-031) ---
# The advisory layer ARTICULATES the spine's computed facts into honest, merchant-facing
# guidance; the compliance gate is a FIRST-CLASS DETERMINISTIC block every merchant-facing
# output must pass. Agents articulate/orchestrate — they never compute the spine and never
# decide compliance (Framework §5.9). Realizes the S5 compliance_gate_hook (D-508). Pure enums
# (no Spark) so common/advisory + common/compliance are tier-1 testable. **S8 COMPOSES + GATES;
# it does NOT send** (no outbound comms / SF write / merchant app).


class AdvisoryType:
    """What a merchant-facing advisory IS (D-806) — drives how strictly the compliance gate
    treats it. The agent proposes an intent; the DETERMINISTIC classifier (common/compliance)
    re-derives the type and is the authority, so a mislabeling agent can never downgrade a
    specific offer into 'advice' to dodge the strict path."""

    FACTUAL_SUMMARY = "factual-summary"  # restates the merchant's OWN computed situation (paydown/clock)
    ADVICE = "advice"  # general guidance / eligibility / paydown coaching — names NO concrete terms
    SPECIFIC_OFFER = "specific-offer"  # names concrete terms (amount / factor / payment) — strict path
    ALL = frozenset({FACTUAL_SUMMARY, ADVICE, SPECIFIC_OFFER})


class ComplianceStatus:
    """The compliance gate verdict (D-801). A HARD gate: a BLOCKED artifact is stored (auditable)
    but NEVER marked deliverable. Only PASS may ever be delivered (delivery itself is S9+/gated)."""

    PASS = "pass"  # grounded, suitable, disclosures satisfied — may be delivered (later, gated)
    BLOCKED = "blocked"  # ungrounded / unsuitable-offer-pitched / missing disclosure — never delivered
    ALL = frozenset({PASS, BLOCKED})


class DisclosureRegime:
    """State-aware commercial-financing disclosure regimes (D-805). v1 FLAGS which regime applies
    and REQUIRES a disclosure block be present on a specific offer; it does NOT draft binding legal
    language (counsel owns wording). NONE = no special regime identified for the state (NOT an
    assertion that none exists — the list is seeded + counsel-extended)."""

    NONE = "none"
    CA_CFDL = "CA-commercial-financing-disclosure"  # CA commercial financing disclosure (SB 1235 / DFPI)
    NY_CFDL = "NY-commercial-financing-disclosure"  # NY Commercial Finance Disclosure Law
    UT_CFR = "UT-commercial-financing-registration"  # UT commercial financing registration/disclosure
    VA_CFR = "VA-commercial-financing-disclosure"  # VA commercial financing disclosure
    ALL = frozenset({NONE, CA_CFDL, NY_CFDL, UT_CFR, VA_CFR})


# State (USPS code) -> disclosure regime (D-805). Config-driven so the list changes in ONE place
# (Rule 3), **pending counsel review before any cloud run**. Keyed by gold.merchants.governing_state.
# v1 = flag the regime + require a disclosure block; it is NOT legal wording. Absent state => NONE.
DISCLOSURE_RULES = {
    "CA": DisclosureRegime.CA_CFDL,
    "NY": DisclosureRegime.NY_CFDL,
    "UT": DisclosureRegime.UT_CFR,
    "VA": DisclosureRegime.VA_CFR,
}


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
    # S4 Activation (Build Plan §6 / C-018) — point-in-time `merchant_activation`
    # (+ `_current` view), the `daily_queue` read view (floor consumption surface), and the
    # point-in-time `book_health` scoreboard family (+ per-view `_current` views). Mirrors the
    # S2/S3 point-in-time pattern. NO Salesforce write in S4 (serving layer only, D-403).
    MERCHANT_ACTIVATION = "merchant_activation"
    MERCHANT_ACTIVATION_CURRENT = "merchant_activation_current"
    DAILY_QUEUE = "daily_queue"
    # S5 Offer Engine (Build Plan §6 / Framework §5.7) — point-in-time `merchant_offers`
    # (+ `_current` view), reusing the existing routing engine. NO writes to mca_funders.
    MERCHANT_OFFERS = "merchant_offers"
    MERCHANT_OFFERS_CURRENT = "merchant_offers_current"
    # S6 Prediction (Build Plan §6 / Framework §11.2) — point-in-time `merchant_predictions`
    # (+ `_current` view): BTYD (p_alive/CLV) + survival (next-event) + confidence. Batch
    # inference (D-605); models adopted (PyMC-Marketing + lifelines), MLflow-versioned.
    MERCHANT_PREDICTIONS = "merchant_predictions"
    MERCHANT_PREDICTIONS_CURRENT = "merchant_predictions_current"
    # S7 Agentic extraction (Framework §5.9) — point-in-time `merchant_extraction` (+`_current`):
    # grounded agent outputs (default_subtype, positions, burden, …) the spine consumes as
    # optional enrichment via the normal re-run. The agent never writes spine tables.
    MERCHANT_EXTRACTION = "merchant_extraction"
    MERCHANT_EXTRACTION_CURRENT = "merchant_extraction_current"
    # S7 Phase-2 Statement Analyst audit trail — the agent's FULL per-statement parse (positions
    # breakdown JSON, deposits, period, confidence, citation), so "which statement numbers" is
    # answerable without re-running the model. One row per statement per run.
    STATEMENT_EXTRACTION_AUDIT = "statement_extraction_audit"
    # S8 Advisory layer (Build Plan §7 / Framework §2.3/§2.4/§5.9) — point-in-time
    # `merchant_advisory` (+`_current`): the grounded, compliance-gated, merchant-facing advisory
    # the Advisory Composer + Structure Advisor produce. STORED, not delivered (S8 composes +
    # gates; delivery is S9+/gated). Every row carries a compliance_status; a BLOCKED row is never
    # marked deliverable. The agent never writes a spine-math column (Framework §5.9).
    MERCHANT_ADVISORY = "merchant_advisory"
    MERCHANT_ADVISORY_CURRENT = "merchant_advisory_current"
    BOOK_HEALTH = "book_health"
    BOOK_HEALTH_CURRENT = "book_health_current"
    RENEWAL_PERFORMANCE_CURRENT = "renewal_performance_current"
    LEADING_INDICATORS_CURRENT = "leading_indicators_current"


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
