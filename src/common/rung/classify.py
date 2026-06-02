"""classify_merchant — the single pure entry point composing the Appendix B engine (S3).

gate (B.2) -> waterfall (B.3) -> output object (Framework 4.7):
    { lifecycle_state, rung, confidence, missing_signals[], direction_of_travel }
plus the routing/diagnostic fields the floor queue + S4 activation need
(`default_subtype`, `route`, `rapid_reup_flag`, `renewal_chain_incomplete`).

Two axes (B.1): lifecycle_state is always set; rung is 1..5 ONLY for active merchants that
match a rung, else None (gated by lifecycle, or Unclassified when key signals are missing).
Unclassified is an explicit, honest bucket — never force-fit a rung (Framework 4.1).

Pure — no Spark, no I/O. The Spark transform (transform/gold_rung.py, S3 cloud step —
NOT built here, gated on approval per Rule 5) applies this per merchant as a UDF.
"""

from __future__ import annotations

from common import constants as C
from common.rung.confidence import confidence, direction_of_travel
from common.rung.lifecycle import lifecycle_state
from common.rung.waterfall import rung_of

# Signals reported in missing_signals[] when absent — feeds the data-capture roadmap
# (Framework 4.7). Their absence does NOT lower confidence (D-306); it names what data we
# lack. In v1: burden_ratio / est_weekly_revenue need a bank feed (none yet); Position-field
# is 0%-populated (C-012).
_PERIPHERAL_SIGNALS = ("est_weekly_revenue", "burden_ratio", "disclosed_positions_cnt")


def missing_signals(signals: dict, lifecycle: dict) -> list[str]:
    """The sorted list of known classification signals that are absent for this merchant
    (data-capture roadmap). Never lowers confidence; for an active merchant a missing KEY
    signal (paydown) is what leaves them Unclassified."""
    out = set()
    for key in _PERIPHERAL_SIGNALS:
        if signals.get(key) is None:
            out.add(key)
    if lifecycle["proceed_to_waterfall"] and signals.get("est_paydown_pct") is None:
        out.add("est_paydown_pct")
    if signals.get("renewal_chain_incomplete"):
        out.add("renewal_chain_incomplete")  # the unlinkable-renewal data gap (FU-302)
    if (
        lifecycle["state"] == C.LifecycleState.DORMANT
        and signals.get("median_renewal_gap_days") is None
    ):
        out.add("median_renewal_gap_days")  # dormancy judged on the book-median fallback
    return sorted(out)


def is_unclassified(state: str, rung) -> bool:
    """Active merchant that matched no rung — the explicit Unclassified pile (Framework 4.1)."""
    return state == C.LifecycleState.ACTIVE and rung is None


def classify_merchant(signals: dict, prev: dict | None = None) -> dict:
    """Classify one merchant from its signal bundle (assembled by the transform from the
    S2 clock + gold deals/merchants). `prev` is the merchant's prior-run classification
    dict (lifecycle_state + rung) for direction_of_travel; None on the first run.
    """
    lifecycle = lifecycle_state(signals)
    state = lifecycle["state"]

    if lifecycle["proceed_to_waterfall"]:
        rung = rung_of(signals)
        route = lifecycle["route"] if rung is not None else C.LifecycleRoute.REVIEW
    else:
        rung = None
        route = lifecycle["route"]

    curr = {"lifecycle_state": state, "rung": rung}
    return {
        "lifecycle_state": state,
        "rung": rung,
        "confidence": confidence(state, rung, signals),
        "missing_signals": missing_signals(signals, lifecycle),
        "direction_of_travel": direction_of_travel(prev, curr),
        "default_subtype": lifecycle["default_subtype"],
        "route": route,
        "rapid_reup_flag": bool(signals.get("rapid_reup_flag", False)),
        "renewal_chain_incomplete": bool(signals.get("renewal_chain_incomplete", False)),
    }
