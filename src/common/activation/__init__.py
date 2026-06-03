"""Activation — state machine + named plays (Build Plan §6, S4 / C-018). The operational
layer over the S3 rung/lifecycle: an action-oriented `current_state`, a named `active_play`,
an SLA, an owner, and grounded next-actions — the floor's read surface. Reads S1/S2/S3 gold;
NEVER recomputes the spine (CLAUDE.md 2.1). NO Salesforce write (D-403 serving-layer-only;
SF write-back is FU-401). NO merchant comms (S8).

Pure, Spark-free-at-import (mirrors common/rung / common/clock) so it is tier-1 testable and
reusable inside the Spark UDFs of transform/gold_activation.py:
  - state_machine: current_state (D-401) + state_changed (the state-transition event)
  - plays:         active_play priority matrix (D-402) + play_sla_due (reuses the clock
                   calendar) + play_owner + next_tactical_action / next_strategic_nudge
  - activate_merchant: the single composed entry point → the activation output object
"""

from common.activation.plays import (
    active_play,
    next_strategic_nudge,
    next_tactical_action,
    play_owner,
    play_sla_due,
)
from common.activation.state_machine import current_state, state_changed


def activate_merchant(signals: dict, run_date, holidays=None) -> dict:
    """Compose the activation output object for one merchant from its signal bundle (the
    S3 rung output + S2 clock-derived timing signals + owner_id). Pure.

    Returns: {current_state, active_play, play_sla_due, play_owner, next_tactical_action,
              next_strategic_nudge}.
    """
    state = current_state(signals)
    play = active_play({**signals, "current_state": state})
    return {
        "current_state": state,
        "active_play": play,
        "play_sla_due": play_sla_due(play, run_date, holidays),
        "play_owner": play_owner(signals.get("owner_id")),
        "next_tactical_action": next_tactical_action(play),
        "next_strategic_nudge": next_strategic_nudge(play),
    }


__all__ = [
    "current_state",
    "state_changed",
    "active_play",
    "play_sla_due",
    "play_owner",
    "next_tactical_action",
    "next_strategic_nudge",
    "activate_merchant",
]
