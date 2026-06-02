"""Named plays + SLA + next-actions (D-402, S4 Activation).

Assigns the floor's action for a merchant by a DETERMINISTIC priority matrix over
(lifecycle_state, rung, current_state, direction_of_travel) — first match wins. The most
urgent / most-specific play wins: do-not-fund-review and win-back (gated lifecycle) first,
then distressed, then catch-the-slide, then the rung-specific advisory posture, then the
Disciplined-renewer timing plays, then the steady reinforce.

`play_sla_due` reuses `common.clock.calendar.nth_business_day_after` (Rule 3 — the same
business-day calendar the clock uses). next_tactical_action / next_strategic_nudge are
GROUNDED deterministic per-play templates describing the action — internal rep guidance,
NOT merchant comms (S8), and they invent no merchant-specific numbers (honesty constraint).

Pure — no Spark, no I/O.
"""

from __future__ import annotations

from common import constants as C
from common.clock.calendar import nth_business_day_after

_LS = C.LifecycleState
_CS = C.CurrentState
_R = C.RungState
_DOT = C.DirectionOfTravel
_P = C.Play


def active_play(signals: dict) -> str:
    """The single named play for a merchant (first-match priority). Keys consumed:
    lifecycle_state, rung (1..5 or None), current_state, direction_of_travel."""
    lifecycle = signals.get("lifecycle_state")
    rung = signals.get("rung")
    state = signals.get("current_state")
    direction = signals.get("direction_of_travel")

    # Gated lifecycle (off the active ladder) — most specific routing first.
    if lifecycle == _LS.DEFAULTED:
        return _P.DO_NOT_FUND_REVIEW
    if lifecycle == _LS.DORMANT:
        return _P.WIN_BACK
    if lifecycle == _LS.NEW_ESTABLISHING:
        return _P.NEW_ESTABLISHING_NURTURE

    # Active merchants — urgency first, then rung posture, then renewal timing.
    if rung == _R.DISTRESSED:
        return _P.DISTRESSED_STABILIZE
    if direction == _DOT.SLIDING:
        return _P.SLIDE_INTERVENTION  # catch a slide early — the high-value alert
    if rung == _R.SERIAL:
        return _P.SERIAL_RENEWAL_VS_BUYOUT
    if rung == _R.GROWTH:
        return _P.GROWTH_UPSELL
    if rung == _R.GRADUATE:
        return _P.GRADUATE_REFERRAL
    if rung is None:
        return _P.REVIEW_UNCLASSIFIED  # active but Unclassified — capture missing signals

    # Disciplined renewer (rung 3) — play follows the renewal timing.
    if state == _CS.IN_MARKET:
        return _P.IN_MARKET_RENEWAL
    if state == _CS.APPROACHING:
        return _P.APPROACHING_PREP
    return _P.DISCIPLINED_REINFORCE


def play_sla_due(play: str, run_date, holidays=None):
    """The play's response deadline = the nth business day after the run date (the SLA tier).
    Reuses the clock's business-day calendar. None when the play is unknown."""
    sla_days = C.PLAY_SLA_BUSINESS_DAYS.get(play)
    if sla_days is None:
        return None
    return nth_business_day_after(run_date, sla_days, holidays)


def play_owner(owner_id):
    """The accountable rep (D-402) — sourced from SF Opportunity OwnerId by the transform.
    Pass-through: null when no owner is available (the caller flags `play_owner_is_missing`;
    accountability is never fabricated — FU-101)."""
    return owner_id if owner_id else None


# Grounded, deterministic per-play guidance (Framework 4.x climbing nudges). Internal rep
# scripts — NOT merchant comms; they describe the action and invent no merchant numbers.
_TACTICAL = {
    _P.DO_NOT_FUND_REVIEW: "Do not extend capital; route to review (default sub-type unknown).",
    _P.WIN_BACK: "Re-engage the dormant merchant with a relevant, honest check-in.",
    _P.NEW_ESTABLISHING_NURTURE: "Nurture the new relationship; confirm the clock is running cleanly.",
    _P.DISTRESSED_STABILIZE: "Send the position statement (all positions, total weekly burden, projected payoff); place a capacity hold — no new capital until burden falls.",
    _P.SLIDE_INTERVENTION: "Review what changed since the last run (burden, a new position, a stress signal); contact before it worsens.",
    _P.SERIAL_RENEWAL_VS_BUYOUT: "Run the renewal-vs-buyout comparison and show the double-dip delta; do not auto-pitch the buyout.",
    _P.IN_MARKET_RENEWAL: "Eligibility window is open — lead with value and present the renewal; price is earned by tenure, not the headline.",
    _P.APPROACHING_PREP: "Eligibility opens soon — prep the renewal conversation and best-time coaching (wait a little, less rolls over).",
    _P.GROWTH_UPSELL: "Offer a structured, productive upsize matched to the growth; partner, don't just fund.",
    _P.GRADUATE_REFERRAL: "Help them toward the cheaper product (LOC / SBA); make the graduation referral.",
    _P.REVIEW_UNCLASSIFIED: "Capture the missing signals (see missing_signals) so the merchant can be classified.",
    _P.DISCIPLINED_REINFORCE: "Value-first reinforcement (speed, advice, position clarity); no ask needed yet.",
}

_STRATEGIC = {
    _P.DO_NOT_FUND_REVIEW: "Confirm the default sub-type; restructure-referral or graceful off-boarding as the review decides.",
    _P.WIN_BACK: "Win back the relationship; re-enter at the appropriate rung with trust banked.",
    _P.NEW_ESTABLISHING_NURTURE: "Let a clean renewal cycle complete to demonstrate (not presume) discipline.",
    _P.DISTRESSED_STABILIZE: "Model an honest single consolidation; target stabilization toward Disciplined.",
    _P.SLIDE_INTERVENTION: "Arrest the slide early — re-establish a clean renewal rhythm.",
    _P.SERIAL_RENEWAL_VS_BUYOUT: "Coach toward a renewal rhythm (wait for healthier paydown) to climb toward Disciplined.",
    _P.IN_MARKET_RENEWAL: "Reinforce discipline; open the growth-capital conversation if revenue supports it.",
    _P.APPROACHING_PREP: "Set up the renewal at the merchant's best timing.",
    _P.GROWTH_UPSELL: "Begin benchmarking toward cheaper products — set graduation up as a gift, not a loss.",
    _P.GRADUATE_REFERRAL: "Advisor-for-life cadence; convert to a referral and reputation asset.",
    _P.REVIEW_UNCLASSIFIED: "Close the data gap so the merchant can be confidently classified next run.",
    _P.DISCIPLINED_REINFORCE: "Hold them happily; coach best-time renewal; benchmark toward Growth if revenue supports.",
}


def next_tactical_action(play: str) -> str | None:
    """What to do this week (floor script) for the play."""
    return _TACTICAL.get(play)


def next_strategic_nudge(play: str) -> str | None:
    """The next rung-climbing advisory move for the play."""
    return _STRATEGIC.get(play)
