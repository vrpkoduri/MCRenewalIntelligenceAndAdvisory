"""Tier-1 tests for Activation — state machine + named plays (Build Plan §6, S4 / C-018).

Pure functions only (no Spark): current_state (D-401), the active_play priority matrix +
SLA + grounded next-actions (D-402), the composed activate_merchant output, and the four
validation merchants carried through to their state + play. Plus field-map / no-surface /
SF-write-back-reference invariants.

Activation reads S1/S2/S3 gold; it NEVER recomputes the spine. NO Salesforce write in S4
(D-403 — serving layer only). NO merchant comms (S8 — next-actions are internal rep guidance).
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.activation import (
    activate_merchant,
    active_play,
    current_state,
    next_strategic_nudge,
    next_tactical_action,
    play_owner,
    play_sla_due,
    state_changed,
)
from common.clock.calendar import nth_business_day_after
from common.field_maps import (
    GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS,
    MERCHANT_ACTIVATION_MAP,
    SF_WRITEBACK_REFERENCE,
    book_health_columns,
    merchant_activation_columns,
)
from common.io.guards import offending_surface_columns

RUN_DATE = date(2026, 6, 2)
_CS = C.CurrentState
_P = C.Play
_R = C.RungState
_LS = C.LifecycleState


def _active(**over):
    base = {
        "lifecycle_state": _LS.ACTIVE,
        "rung": _R.DISCIPLINED,
        "direction_of_travel": C.DirectionOfTravel.HOLDING,
        "has_renewal": True,
        "days_since_last_funding": 400,
        "is_eligible_now": False,
        "days_to_eligible": 200,
        "owner_id": None,
    }
    base.update(over)
    return base


# =============================================================================
# State machine (D-401)
# =============================================================================


def test_state_gated_lifecycles_map_to_lost_or_clock():
    assert current_state({"lifecycle_state": _LS.DEFAULTED}) == _CS.LOST_WINBACK
    assert current_state({"lifecycle_state": _LS.DORMANT}) == _CS.LOST_WINBACK
    assert current_state({"lifecycle_state": _LS.NEW_ESTABLISHING}) == _CS.CLOCK_RUNNING


def test_state_in_market_when_eligible_now():
    assert current_state(_active(is_eligible_now=True, has_renewal=False)) == _CS.IN_MARKET


def test_state_renewed_when_recently_funded():
    assert current_state(_active(has_renewal=True, days_since_last_funding=10)) == _CS.RENEWED
    # just past the window -> not renewed
    assert current_state(_active(has_renewal=True, days_since_last_funding=31, days_to_eligible=200)) == _CS.CLOCK_RUNNING


def test_state_renewed_requires_has_renewal():
    # a first-time active position funded recently is not "renewed" (no renewal history)
    assert current_state(_active(has_renewal=False, days_since_last_funding=10, days_to_eligible=200)) == _CS.CLOCK_RUNNING


def test_state_approaching_within_window_boundary():
    assert current_state(_active(has_renewal=False, days_to_eligible=C.APPROACHING_WINDOW_DAYS)) == _CS.APPROACHING
    assert current_state(_active(has_renewal=False, days_to_eligible=C.APPROACHING_WINDOW_DAYS + 1)) == _CS.CLOCK_RUNNING


def test_state_in_market_precedes_approaching():
    # eligible now wins over an approaching window
    assert current_state(_active(is_eligible_now=True, has_renewal=False, days_to_eligible=5)) == _CS.IN_MARKET


def test_state_changed():
    assert state_changed(None, _CS.CLOCK_RUNNING) is False  # first run
    assert state_changed(_CS.CLOCK_RUNNING, _CS.CLOCK_RUNNING) is False
    assert state_changed(_CS.APPROACHING, _CS.IN_MARKET) is True


# =============================================================================
# Plays + SLA + next-actions (D-402)
# =============================================================================


def test_play_priority_gated_first():
    assert active_play({"lifecycle_state": _LS.DEFAULTED}) == _P.DO_NOT_FUND_REVIEW
    assert active_play({"lifecycle_state": _LS.DORMANT}) == _P.WIN_BACK
    assert active_play({"lifecycle_state": _LS.NEW_ESTABLISHING}) == _P.NEW_ESTABLISHING_NURTURE


def test_play_distressed_beats_everything_active():
    s = _active(rung=_R.DISTRESSED, direction_of_travel=C.DirectionOfTravel.SLIDING, current_state=_CS.IN_MARKET)
    assert active_play(s) == _P.DISTRESSED_STABILIZE


def test_play_slide_intervention_before_rung_posture():
    s = _active(rung=_R.SERIAL, direction_of_travel=C.DirectionOfTravel.SLIDING, current_state=_CS.CLOCK_RUNNING)
    assert active_play(s) == _P.SLIDE_INTERVENTION


def test_play_serial_and_growth_and_graduate():
    assert active_play(_active(rung=_R.SERIAL, current_state=_CS.CLOCK_RUNNING)) == _P.SERIAL_RENEWAL_VS_BUYOUT
    assert active_play(_active(rung=_R.GROWTH, current_state=_CS.IN_MARKET)) == _P.GROWTH_UPSELL
    assert active_play(_active(rung=_R.GRADUATE, current_state=_CS.IN_MARKET)) == _P.GRADUATE_REFERRAL


def test_play_unclassified():
    assert active_play(_active(rung=None, current_state=_CS.CLOCK_RUNNING)) == _P.REVIEW_UNCLASSIFIED


def test_play_disciplined_follows_renewal_timing():
    assert active_play(_active(rung=_R.DISCIPLINED, current_state=_CS.IN_MARKET)) == _P.IN_MARKET_RENEWAL
    assert active_play(_active(rung=_R.DISCIPLINED, current_state=_CS.APPROACHING)) == _P.APPROACHING_PREP
    assert active_play(_active(rung=_R.DISCIPLINED, current_state=_CS.CLOCK_RUNNING)) == _P.DISCIPLINED_REINFORCE


def test_play_sla_due_reuses_business_day_calendar():
    # distressed = 2 business days; 2026-06-02 is a Tuesday -> Thu 2026-06-04
    assert play_sla_due(_P.DISTRESSED_STABILIZE, RUN_DATE) == nth_business_day_after(RUN_DATE, 2)
    # in-market = 5 business days -> Tue 2026-06-09
    assert play_sla_due(_P.IN_MARKET_RENEWAL, RUN_DATE) == date(2026, 6, 9)
    # every play has an SLA tier
    for play in C.Play.ALL:
        assert play in C.PLAY_SLA_BUSINESS_DAYS
        assert play_sla_due(play, RUN_DATE) is not None


def test_play_owner_passthrough_and_null():
    assert play_owner("005xx") == "005xx"
    assert play_owner(None) is None
    assert play_owner("") is None


def test_next_actions_present_for_every_play():
    for play in C.Play.ALL:
        assert next_tactical_action(play)
        assert next_strategic_nudge(play)


# =============================================================================
# Composed activate_merchant + four validation merchants
# =============================================================================


def test_activate_output_object_shape():
    out = activate_merchant(_active(), RUN_DATE)
    for key in ("current_state", "active_play", "play_sla_due", "play_owner",
                "next_tactical_action", "next_strategic_nudge"):
        assert key in out
    assert out["current_state"] in C.CurrentState.ALL
    assert out["active_play"] in C.Play.ALL


def test_four_merchants_state_and_play():
    starr = activate_merchant({"lifecycle_state": _LS.DEFAULTED, "rung": None,
                               "direction_of_travel": C.DirectionOfTravel.HOLDING}, RUN_DATE)
    assert starr["current_state"] == _CS.LOST_WINBACK
    assert starr["active_play"] == _P.DO_NOT_FUND_REVIEW

    obp = activate_merchant({"lifecycle_state": _LS.DORMANT, "rung": None,
                             "direction_of_travel": C.DirectionOfTravel.HOLDING}, RUN_DATE)
    assert obp["current_state"] == _CS.LOST_WINBACK and obp["active_play"] == _P.WIN_BACK

    snell = activate_merchant({"lifecycle_state": _LS.NEW_ESTABLISHING, "rung": None,
                               "direction_of_travel": C.DirectionOfTravel.HOLDING}, RUN_DATE)
    assert snell["current_state"] == _CS.CLOCK_RUNNING and snell["active_play"] == _P.NEW_ESTABLISHING_NURTURE

    # Wolf: active, Serial, holding, eligible ~48d out, funded 32d ago -> clock-running / serial play
    wolf = activate_merchant({"lifecycle_state": _LS.ACTIVE, "rung": _R.SERIAL,
                              "direction_of_travel": C.DirectionOfTravel.HOLDING, "has_renewal": True,
                              "days_since_last_funding": 32, "is_eligible_now": False,
                              "days_to_eligible": 48, "owner_id": None}, RUN_DATE)
    assert wolf["current_state"] == _CS.CLOCK_RUNNING
    assert wolf["active_play"] == _P.SERIAL_RENEWAL_VS_BUYOUT


# =============================================================================
# Field-map / no-surface / SF-write-back-reference invariants
# =============================================================================

_KNOWN_VERDICTS = {C.Verdict.HAVE, C.Verdict.CARRY, C.Verdict.DISTRUST, C.Verdict.DERIVE,
                   C.Verdict.MUST_CAPTURE, C.Verdict.REUSE, C.Verdict.FUTURE}


def test_activation_map_columns_unique_known_verdicts_pk_order():
    cols = merchant_activation_columns()
    assert len(cols) == len(set(cols))
    assert len(cols) == len(MERCHANT_ACTIVATION_MAP) + len(GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS)
    assert MERCHANT_ACTIVATION_MAP[0].silver_col == "merchant_id"
    assert MERCHANT_ACTIVATION_MAP[1].silver_col == "activation_run_date"
    for fs in MERCHANT_ACTIVATION_MAP:
        assert fs.verdict in _KNOWN_VERDICTS


def test_activation_and_book_health_no_surface():
    assert offending_surface_columns(merchant_activation_columns()) == []
    assert offending_surface_columns(book_health_columns()) == []


def test_play_owner_missing_flag_present():
    flags = {name for name, _ in GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS}
    assert "play_owner_is_missing" in flags


def test_sf_writeback_reference_only_floor_dual_audiences_no_surface():
    """FU-401 reference: only floor (F) / dual (D) fields are ever pushed to Salesforce, and
    never a frozen `_sf_stored_*` snapshot (CLAUDE.md 2.1)."""
    for gold_col, sf_target, audience in SF_WRITEBACK_REFERENCE:
        assert audience in {"F", "D"}, f"{gold_col} has non-floor audience {audience}"
        assert not gold_col.startswith(C.SF_STORED_PREFIX)
        assert sf_target.startswith("MRI__"), f"{sf_target} must be a dedicated MRI field"
