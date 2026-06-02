"""State machine — current_state (D-401, S4 Activation).

The operational layer over the S3 rung/lifecycle. `current_state` answers "where is this
merchant in the funding cycle, actionably?" — distinct from `lifecycle_state` (which is the
Step-0 gate). Gated lifecycle states (defaulted / dormant) map to `lost-winback`;
new-establishing maps to `clock-running`; active merchants resolve from the S2 clock.

Reads S1/S2/S3 gold signals only; NEVER recomputes the spine (CLAUDE.md 2.1). Pure — no
Spark, no I/O (mirrors common/rung / common/clock); the Spark transform applies it as a UDF.

States (Framework / Build Plan §6):
  clock-running — active, paying down, not yet near eligibility.
  approaching   — active, eligibility window opens within APPROACHING_WINDOW_DAYS.
  in-market     — active & eligible now (paydown >= renewal threshold).
  renewed       — active, just took a new advance (within RENEWED_WINDOW_DAYS).
  lost-winback  — dormant or defaulted (gated lifecycle) — win-back or do-not-fund-review.
"""

from __future__ import annotations

from common import constants as C

_LS = C.LifecycleState
_CS = C.CurrentState


def current_state(signals: dict) -> str:
    """Resolve `current_state` from the merchant's signal bundle (assembled by the transform
    from the S2 clock + S3 rung). Keys consumed:
      lifecycle_state, has_renewal, days_since_last_funding (today − last funded_date),
      is_eligible_now, days_to_eligible (est_renewal_eligible_date − today; None if no date).

    Order is the spec: gated lifecycle first, then active resolves renewed → in-market →
    approaching → clock-running.
    """
    lifecycle = signals.get("lifecycle_state")

    # Gated lifecycle states are off the active ladder (B.2) → win-back / do-not-fund-review.
    if lifecycle in (_LS.DEFAULTED, _LS.DORMANT):
        return _CS.LOST_WINBACK
    if lifecycle == _LS.NEW_ESTABLISHING:
        return _CS.CLOCK_RUNNING

    # Active merchants:
    days_since = signals.get("days_since_last_funding")
    if (
        signals.get("has_renewal")
        and days_since is not None
        and 0 <= int(days_since) <= C.RENEWED_WINDOW_DAYS
    ):
        return _CS.RENEWED

    if signals.get("is_eligible_now"):
        return _CS.IN_MARKET

    days_to_eligible = signals.get("days_to_eligible")
    if days_to_eligible is not None and 0 <= int(days_to_eligible) <= C.APPROACHING_WINDOW_DAYS:
        return _CS.APPROACHING

    return _CS.CLOCK_RUNNING


def state_changed(prev_state, curr_state) -> bool:
    """True when current_state moved run-over-run (drives the `state_transition` event).
    No prior run (prev_state None) → not a transition."""
    if prev_state is None:
        return False
    return prev_state != curr_state
