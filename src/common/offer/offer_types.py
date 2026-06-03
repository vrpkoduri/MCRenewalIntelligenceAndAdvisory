"""eligible_offer_types — D-504 (S5).

The CANDIDATE offer types from the clock + rung + state (before the funder match). The Spark
transform intersects these candidates with the funder routing result (matched_funders) and
the suitability gate — so a candidate only becomes a real offer if a funder box passes AND the
advisory layer deems it suitable. Honest default is `none-yet`.

Pure — no Spark, no I/O. Reads S2/S3/S4 outputs only; never recomputes the spine.
"""

from __future__ import annotations

from common import constants as C

_LS = C.LifecycleState
_R = C.RungState
_OT = C.OfferType


def candidate_offer_types(signals: dict) -> list[str]:
    """Candidate offer types for a merchant (D-504), from lifecycle/rung/clock signals. Keys:
    lifecycle_state, rung, is_eligible_now, rapid_reup_flag, active_position_cnt.

    Gated lifecycles (defaulted/dormant/new-establishing) and Unclassified → none-yet.
    Active in-market disciplined single position → renewal + larger-advance.
    Active serial / rapid re-up → buyout + larger-advance (the structure eval decides which).
    Anything else (active, not eligible, clock running) → none-yet (the honest "not yet").
    """
    lifecycle = signals.get("lifecycle_state")
    if lifecycle in (_LS.DEFAULTED, _LS.DORMANT, _LS.NEW_ESTABLISHING):
        return [_OT.NONE_YET]
    if lifecycle != _LS.ACTIVE:
        return [_OT.NONE_YET]

    rung = signals.get("rung")
    if rung is None:  # active but Unclassified
        return [_OT.NONE_YET]

    rapid = signals.get("rapid_reup_flag", False)
    serial = rung == _R.SERIAL or rapid or (signals.get("active_position_cnt") or 0) >= C.Thresholds.SERIAL_POSITION_MIN
    if serial:
        return [_OT.BUYOUT, _OT.LARGER_ADVANCE]

    if signals.get("is_eligible_now") and rung in (_R.DISCIPLINED, _R.GROWTH, _R.GRADUATE):
        return [_OT.RENEWAL, _OT.LARGER_ADVANCE]

    return [_OT.NONE_YET]
