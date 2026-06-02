"""Confidence + direction-of-travel — D-306 / Framework 4.7 (S3).

CONFIDENCE is deterministic and BORDERLINE-DRIVEN (D-306), NOT an ML probability (ML is
S6). A merchant's confidence is high when its decisive signals sit comfortably inside the
assigned rung's band and falls toward the boundary as a value approaches a threshold.

The stance is innocent-until-proven-otherwise: MISSING data does NOT lower confidence —
an absent peripheral signal is simply omitted from the calculation (not scored as 0), so a
classified merchant is never penalized for our own data gaps and advisory comms keep
flowing. (Missing KEY signals still route a merchant to Unclassified upstream, via the
waterfall returning no rung; that is a separate, explicit bucket.)

  confidence = min over the assigned rung's decisive thresholds of a margin function
               (1.0 deep inside the band -> 0.5 at the boundary); 1.0 when no decisive
               numeric signal is present (benefit of the doubt).

DIRECTION_OF_TRAVEL (climbing / holding / sliding) is the run-over-run change in a
merchant's health rank — what lets the daily queue prioritize a sliding disciplined
merchant over a stable one. Pure functions — no Spark, no I/O.
"""

from __future__ import annotations

from common import constants as C
from common.rung.lifecycle import dormancy_gap_days

_T = C.Thresholds
_PAYDOWN_MIN = _T.DISCIPLINED_RENEWAL_PAYDOWN_MIN  # 0.50
_BURDEN_CEILING = _T.BURDEN_DISTRESS_CEILING  # 0.30
_DISCIPLINED_BURDEN_MAX = _T.DISCIPLINED_BURDEN_MAX  # 0.15
_DORMANCY_MULT = _T.DORMANCY_MULTIPLIER  # 2.0

# Confidence floor — an Unclassified merchant (active, no rung matched) is maximally
# uncertain in placement, so it floats to the top of the attention queue.
_FLOOR = 0.5
_CEIL = 1.0

# Margin bands: how far past a threshold counts as "comfortably inside the band" (full
# confidence). Calibration hypotheses, kept here next to the score that uses them.
_PAYDOWN_BAND = 0.25
_BURDEN_BAND = 0.10


def _clamp(x: float) -> float:
    return max(_FLOOR, min(_CEIL, x))


def _margin(value, threshold, band) -> float | None:
    """A borderline margin in [0.5, 1.0]: 0.5 exactly at the threshold, rising to 1.0 a
    full `band` away. None when the value is absent (omit it — never score missing as 0)."""
    if value is None or band is None or band <= 0:
        return None
    distance = abs(float(value) - float(threshold))
    return _clamp(_FLOOR + _FLOOR * min(1.0, distance / float(band)))


def _min_present(margins) -> float:
    present = [m for m in margins if m is not None]
    return min(present) if present else _CEIL


def _rung_confidence(rung, signals: dict) -> float:
    """Borderline confidence for a placed rung, from the decisive numeric signals present
    for that rung. Absent signals are omitted (D-306)."""
    burden = signals.get("burden_ratio")
    paydown = signals.get("est_paydown_pct")
    if rung == C.RungState.DISTRESSED:
        # Decisive numeric: burden vs the ceiling. A hard stress event (default note) is
        # unambiguous and contributes no penalty (omitted -> stays high).
        return _min_present([_margin(burden, _BURDEN_CEILING, _BURDEN_BAND)])
    if rung == C.RungState.SERIAL:
        return _min_present(
            [_margin(paydown, _PAYDOWN_MIN, _PAYDOWN_BAND), _margin(burden, _BURDEN_CEILING, _BURDEN_BAND)]
        )
    if rung == C.RungState.DISCIPLINED:
        return _min_present(
            [
                _margin(paydown, _PAYDOWN_MIN, _PAYDOWN_BAND),
                _margin(burden, _DISCIPLINED_BURDEN_MAX, _BURDEN_BAND),
            ]
        )
    if rung in (C.RungState.GROWTH, C.RungState.GRADUATE):
        # Growth/Graduate extend Disciplined; their extra qualifiers are boolean (no
        # numeric margin in v1), so the paydown margin governs.
        return _min_present([_margin(paydown, _PAYDOWN_MIN, _PAYDOWN_BAND)])
    return _CEIL


def _dormant_confidence(signals: dict) -> float:
    """Dormancy is borderline-driven too: confidence rises the further past the 2x-gap
    line a merchant sits (band = one reference gap)."""
    gap = dormancy_gap_days(
        signals.get("median_renewal_gap_days"), signals.get("book_median_gap_days")
    )
    if gap is None:
        return _CEIL
    boundary = _DORMANCY_MULT * gap
    return _min_present([_margin(signals.get("time_since_last_active_days"), boundary, gap)])


def confidence(state: str, rung, signals: dict) -> float:
    """Deterministic confidence in [0.5, 1.0] for the assigned label (D-306).

    - Active + a placed rung  -> borderline margin over that rung's decisive thresholds.
    - Active + no rung (Unclassified) -> the floor (0.5): maximally uncertain placement.
    - Dormant -> borderline on how far past the dormancy line the merchant sits.
    - Defaulted / New-establishing -> 1.0 (deterministic from a hard signal / structure).
    """
    if state == C.LifecycleState.ACTIVE:
        if rung is None:
            return _FLOOR
        return _clamp(_rung_confidence(rung, signals))
    if state == C.LifecycleState.DORMANT:
        return _clamp(_dormant_confidence(signals))
    # Defaulted (default note dominates) and New/establishing (single deal, no renewal)
    # are deterministic placements.
    return _CEIL


# --- direction_of_travel (Framework 4.7) -----------------------------------------

# Health rank for run-over-run comparison. Higher = healthier. Active merchants use their
# rung number (1 Distressed .. 5 Graduate). Lifecycle-gated states slot around the ladder:
# defaulted is below distressed; dormant (lost-but-not-failed) sits low; new/establishing
# is healthy-but-unproven (below Disciplined). Unclassified has no rank -> not comparable.
_STATE_RANK = {
    C.LifecycleState.DEFAULTED: 0.0,
    C.LifecycleState.DORMANT: 0.5,
    C.LifecycleState.NEW_ESTABLISHING: 2.5,
}


def health_rank(state: str, rung) -> float | None:
    if state == C.LifecycleState.ACTIVE:
        return float(rung) if rung is not None else None  # Unclassified -> not comparable
    return _STATE_RANK.get(state)


def direction_of_travel(prev: dict | None, curr: dict) -> str:
    """climbing / holding / sliding from the prior run's classification to this one.

    `prev`/`curr` are classification dicts carrying `lifecycle_state` + `rung`. No prior
    run, or either side not rank-comparable (Unclassified), -> holding (we don't invent a
    trajectory). curr healthier than prev -> climbing; less healthy -> sliding.
    """
    if not prev:
        return C.DirectionOfTravel.HOLDING
    pr = health_rank(prev.get("lifecycle_state"), prev.get("rung"))
    cr = health_rank(curr.get("lifecycle_state"), curr.get("rung"))
    if pr is None or cr is None:
        return C.DirectionOfTravel.HOLDING
    if cr > pr:
        return C.DirectionOfTravel.CLIMBING
    if cr < pr:
        return C.DirectionOfTravel.SLIDING
    return C.DirectionOfTravel.HOLDING
