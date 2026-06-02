"""Step-0 lifecycle gate — Appendix B.2 (S3).

Runs BEFORE the rung waterfall. Routes every merchant onto one of four lifecycle
states (B.1 — the lifecycle axis is distinct from the active health ladder):

  - Defaulted        -- a position computed `closed_default` (S2 clock A.5b). Sub-type +
                        route; an undetermined default is `unknown` -> do-not-fund (Starr).
  - Dormant          -- no active positions AND idle > 2x the merchant's median renewal gap
                        (or 2x the book-median when they have no history) -> win-back (OBP).
  - New / establishing-- a single recently-funded position with no renewal history: healthy
                        clock-running, but NOT Disciplined until a clean renewal completes (Snell).
  - Active           -- one or more open positions -> proceed to the waterfall (Wolf).

Order is the specification (B.2): Defaulted, then Dormant, then New/establishing, then
Active. Pure functions — no Spark, no I/O (mirrors common/clock); the Spark transform
applies these as columns/UDFs.

THE classifier reads S2 clock outputs; it NEVER recomputes the spine (CLAUDE.md 2.1).
`closure_status`, `active_position_cnt`, `has_default_note` arrive precomputed.
"""

from __future__ import annotations

from common import constants as C

_DORMANCY_MULT = C.Thresholds.DORMANCY_MULTIPLIER  # 2.0 (B.2)


def default_subtype(notes: str | None = None, has_default_note: bool = False) -> str:
    """Defaulted sub-type (Appendix B.2). v1 cannot reliably distinguish true-default vs
    early-payoff/clawback vs restructured — detection is gated on the data audit and the
    S7 Data Steward agent. So every default is `unknown` (the conservative interim:
    do-not-fund + flag for review). Starr ("Defaulted — $250 clawback") -> `unknown`,
    NOT early-payoff, even though the note mentions a clawback (we never guess).

    Signature carries `notes`/`has_default_note` so S7 can refine in place without a
    caller change (the upgrade path); v1 ignores them and returns UNKNOWN.
    """
    return C.DefaultSubtype.UNKNOWN


def route_for_default(subtype: str) -> str:
    """Map a default sub-type to its advisory route (Appendix B.2)."""
    return {
        C.DefaultSubtype.TRUE_DEFAULT: C.LifecycleRoute.DISTRESSED_EXIT,
        C.DefaultSubtype.EARLY_PAYOFF: C.LifecycleRoute.WIN_BACK,
        C.DefaultSubtype.RESTRUCTURED: C.LifecycleRoute.IMPAIRED_MANAGED,
        C.DefaultSubtype.UNKNOWN: C.LifecycleRoute.DO_NOT_FUND,
    }.get(subtype, C.LifecycleRoute.DO_NOT_FUND)


def is_defaulted(has_default_note: bool) -> bool:
    """A merchant is Defaulted when any of its positions carries a default note — i.e.
    computed `closed_default` (A.5b). The default note dominates a ~100% paydown (Starr)."""
    return bool(has_default_note)


def dormancy_gap_days(median_renewal_gap_days, book_median_gap_days) -> float | None:
    """The dormancy reference gap (B.2): the merchant's OWN median renewal gap when they
    have history, else 2x is applied to the book-median. None when neither is available
    (cannot judge dormancy — caller flags it)."""
    if median_renewal_gap_days is not None:
        return float(median_renewal_gap_days)
    if book_median_gap_days is not None:
        return float(book_median_gap_days)
    return None


def is_dormant(
    active_position_cnt: int,
    time_since_last_active_days,
    median_renewal_gap_days,
    book_median_gap_days,
) -> bool:
    """Dormant (B.2): no active positions AND idle longer than 2x the reference renewal
    gap. A merchant with an open position is Active, not Dormant. Self-calibrating per
    merchant; falls back to the book-median when they have no renewal history.

    `>` (strict): exactly 2x the gap is the boundary, not yet dormant.
    """
    if active_position_cnt and active_position_cnt > 0:
        return False
    if time_since_last_active_days is None:
        return False
    gap = dormancy_gap_days(median_renewal_gap_days, book_median_gap_days)
    if gap is None:
        return False
    return float(time_since_last_active_days) > _DORMANCY_MULT * gap


def is_new_establishing(
    active_position_cnt: int,
    deal_count: int,
    has_renewal: bool,
    prior_clean_renewal_count: int = 0,
) -> bool:
    """New / establishing (B.2): a single funded position with an open clock and NO
    renewal history. Discipline is demonstrated, not presumed (Snell stays here, not
    Disciplined, until a clean renewal completes). Requires an active position (a
    paid-off single deal between cycles is handled by Dormant or the waterfall)."""
    return (
        (active_position_cnt or 0) >= 1
        and (deal_count or 0) <= 1
        and not has_renewal
        and (prior_clean_renewal_count or 0) == 0
    )


def lifecycle_state(signals: dict) -> dict:
    """The Step-0 gate (B.2). Returns the route decision:
        {"state": LifecycleState, "default_subtype": DefaultSubtype|None,
         "route": LifecycleRoute, "proceed_to_waterfall": bool}

    Evaluation order is the spec: Defaulted -> Dormant -> New/establishing -> Active.
    `signals` keys consumed (all precomputed from the S2 clock + gold deals/merchants):
      has_default_note, active_position_cnt, deal_count, has_renewal,
      prior_clean_renewal_count, time_since_last_active_days,
      median_renewal_gap_days, book_median_gap_days, notes.
    """
    has_default = signals.get("has_default_note", False)
    active_cnt = signals.get("active_position_cnt", 0) or 0

    if is_defaulted(has_default):
        subtype = default_subtype(signals.get("notes"), has_default)
        return {
            "state": C.LifecycleState.DEFAULTED,
            "default_subtype": subtype,
            "route": route_for_default(subtype),
            "proceed_to_waterfall": False,
        }

    if is_dormant(
        active_cnt,
        signals.get("time_since_last_active_days"),
        signals.get("median_renewal_gap_days"),
        signals.get("book_median_gap_days"),
    ):
        return {
            "state": C.LifecycleState.DORMANT,
            "default_subtype": None,
            "route": C.LifecycleRoute.WIN_BACK,
            "proceed_to_waterfall": False,
        }

    if is_new_establishing(
        active_cnt,
        signals.get("deal_count", 0),
        signals.get("has_renewal", False),
        signals.get("prior_clean_renewal_count", 0),
    ):
        return {
            "state": C.LifecycleState.NEW_ESTABLISHING,
            "default_subtype": None,
            "route": C.LifecycleRoute.CLOCK_RUNNING,
            "proceed_to_waterfall": False,
        }

    return {
        "state": C.LifecycleState.ACTIVE,
        "default_subtype": None,
        "route": C.LifecycleRoute.WATERFALL,
        "proceed_to_waterfall": True,
    }
