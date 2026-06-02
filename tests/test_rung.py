"""Tier-1 tests for the Rung Classifier — Appendix B (S3).

Pure functions only (no Spark): the Step-0 lifecycle gate (B.2), the rung waterfall
(B.3, first-match-wins + stress-override-pulls-down), rapid_reup_flag (D-302),
borderline confidence + direction_of_travel (D-306 / 4.7), and the four validation
merchants (B.5) classified end-to-end from hand-built signal vectors. Plus field-map /
schema invariants so the rung layer can never silently drift from the contract or leak a
frozen SF snapshot.

NO ML (rules-only; ML is S6). The classifier reads S2 clock outputs and NEVER recomputes
the spine (CLAUDE.md 2.1) — rapid_reup's paydown-at-a-date reuses common.clock.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from common import constants as C
from common.field_maps import (
    GOLD_MERCHANT_RUNG_DQ_COLUMNS,
    MERCHANT_RUNG_MAP,
    merchant_rung_columns,
)
from common.io.guards import offending_surface_columns
from common.rung import (
    classify_merchant,
    confidence,
    direction_of_travel,
    has_prior_clean_renewal,
    is_disciplined,
    is_distressed,
    is_dormant,
    is_new_establishing,
    is_serial,
    is_unclassified,
    lifecycle_state,
    prior_paydown_at,
    rapid_reup_flag,
    rapid_reup_into_worse_terms,
    rung_of,
    worsening_factor,
)
from tests.fixtures.validation_merchants import (
    ONE_BIG_PROMOTION,
    STARR_WINDOW_TINTING,
    TOM_SNELL,
    WOLF_CORPORATION,
)

RUN_DATE = date(2026, 6, 2)
_T = C.Thresholds


# =============================================================================
# Step-0 lifecycle gate (B.2)
# =============================================================================


def _active_signals(**over):
    base = {
        "has_default_note": False,
        "active_position_cnt": 1,
        "deal_count": 2,
        "has_renewal": True,
        "prior_clean_renewal_count": 1,
        "renewal_chain_incomplete": False,
        "time_since_last_active_days": 30,
        "median_renewal_gap_days": 120,
        "book_median_gap_days": 365,
        "burden_ratio": None,
        "est_paydown_pct": 0.6,
        "rapid_reup_flag": False,
        "rapid_reup_into_worse_terms": False,
        "disclosed_positions_cnt": None,
        "worsening_factor": False,
        "shrinking_net": False,
        "advance_rising": False,
        "relative_burden_falling": False,
        "graduate_qualified": False,
        "clean_payments": True,
    }
    base.update(over)
    return base


def test_gate_defaulted_routes_do_not_fund_subtype_unknown():
    lc = lifecycle_state(_active_signals(has_default_note=True))
    assert lc["state"] == C.LifecycleState.DEFAULTED
    assert lc["default_subtype"] == C.DefaultSubtype.UNKNOWN  # v1 never guesses (Starr)
    assert lc["route"] == C.LifecycleRoute.DO_NOT_FUND
    assert lc["proceed_to_waterfall"] is False


def test_gate_default_note_dominates_even_when_active_and_clean():
    """A default note routes Defaulted regardless of active positions / clean signals —
    the conservative, safe route (misrouting a true default is the costlier error)."""
    lc = lifecycle_state(_active_signals(has_default_note=True, active_position_cnt=2))
    assert lc["state"] == C.LifecycleState.DEFAULTED


def test_gate_dormant_when_idle_beyond_2x_gap_and_no_active():
    assert is_dormant(0, 800, None, 365) is True  # 800 > 2*365=730
    lc = lifecycle_state(
        _active_signals(
            active_position_cnt=0,
            time_since_last_active_days=800,
            median_renewal_gap_days=None,
            book_median_gap_days=365,
        )
    )
    assert lc["state"] == C.LifecycleState.DORMANT
    assert lc["route"] == C.LifecycleRoute.WIN_BACK


def test_gate_dormancy_boundary_is_strict():
    """Exactly 2x the gap is the boundary — NOT yet dormant (B.2 self-calibrating)."""
    assert is_dormant(0, 730, None, 365) is False  # == 2*365
    assert is_dormant(0, 731, None, 365) is True


def test_gate_dormancy_uses_own_median_when_present():
    # own median 100 -> boundary 200; book median ignored
    assert is_dormant(0, 250, 100, 365) is True
    assert is_dormant(0, 150, 100, 365) is False


def test_gate_open_position_is_never_dormant():
    assert is_dormant(1, 99999, None, 365) is False


def test_gate_new_establishing_single_position_no_history():
    assert is_new_establishing(1, 1, has_renewal=False, prior_clean_renewal_count=0) is True
    lc = lifecycle_state(
        _active_signals(active_position_cnt=1, deal_count=1, has_renewal=False, prior_clean_renewal_count=0)
    )
    assert lc["state"] == C.LifecycleState.NEW_ESTABLISHING
    assert lc["route"] == C.LifecycleRoute.CLOCK_RUNNING


def test_gate_new_establishing_requires_active_and_no_renewal():
    assert is_new_establishing(0, 1, False, 0) is False  # paid off -> not new/establishing
    assert is_new_establishing(1, 2, True, 1) is False  # has renewal history


def test_gate_active_proceeds_to_waterfall():
    lc = lifecycle_state(_active_signals())
    assert lc["state"] == C.LifecycleState.ACTIVE
    assert lc["proceed_to_waterfall"] is True


def test_gate_order_defaulted_before_dormant():
    """A defaulted-and-idle merchant gates Defaulted, not Dormant (order is the spec)."""
    lc = lifecycle_state(
        _active_signals(has_default_note=True, active_position_cnt=0, time_since_last_active_days=9999)
    )
    assert lc["state"] == C.LifecycleState.DEFAULTED


# =============================================================================
# rapid_reup_flag (D-302)
# =============================================================================


def _deal(funded, amount, factor, payment, freq="Daily", n=100):
    return {
        "funded_date": funded,
        "funded_amount": amount,
        "factor_rate": factor,
        "payment_amount": payment,
        "num_payments": n,
        "payment_frequency": freq,
    }


def test_rapid_reup_paydown_primary_under_50pct():
    """Prior <50% paid down at the new funding -> rapid (the PRIMARY paydown signal)."""
    prior = _deal(date(2026, 4, 17), 30000.0, 1.45, 435.0)
    new = _deal(date(2026, 5, 1), 40000.0, 1.45, 580.0)
    # prior paydown at 2026-05-01 is small (~10 business days of 100) -> well under 50%
    assert prior_paydown_at(prior, date(2026, 5, 1)) < _T.DISCIPLINED_RENEWAL_PAYDOWN_MIN
    assert rapid_reup_flag([prior, new]) is True


def test_rapid_reup_not_flagged_when_prior_healthily_paid():
    """Prior >=50% paid down before the re-up -> disciplined timing, NOT rapid."""
    # prior funded far earlier so it is well past 50% by the new funding date
    prior = _deal(date(2025, 1, 1), 30000.0, 1.45, 435.0)  # 100 daily payments ~ paid off by 2025
    new = _deal(date(2026, 5, 1), 40000.0, 1.45, 580.0)
    assert prior_paydown_at(prior, date(2026, 5, 1)) >= _T.DISCIPLINED_RENEWAL_PAYDOWN_MIN
    assert rapid_reup_flag([prior, new]) is False


def test_rapid_reup_day_gap_fallback_when_paydown_uncomputable():
    """When the prior's paydown can't be computed (missing terms), fall back to the
    <=45-day gap rule (D-302 fallback)."""
    prior = {"funded_date": date(2026, 4, 20), "payment_amount": None, "num_payments": None,
             "payment_frequency": None, "funded_amount": None, "factor_rate": None}
    new = _deal(date(2026, 5, 1), 40000.0, 1.45, 580.0)  # 11 days apart <= 45
    assert prior_paydown_at(prior, date(2026, 5, 1)) is None
    assert rapid_reup_flag([prior, new]) is True
    # widen the gap beyond the threshold -> no longer rapid by the fallback
    new_far = _deal(date(2026, 8, 1), 40000.0, 1.45, 580.0)  # 103 days apart
    assert rapid_reup_flag([prior, new_far]) is False


def test_rapid_reup_single_deal_is_false():
    assert rapid_reup_flag([_deal(date(2026, 5, 1), 40000.0, 1.45, 580.0)]) is False


def test_worsening_factor_and_into_worse_terms():
    prior = _deal(date(2026, 4, 17), 30000.0, 1.40, 435.0)
    new = _deal(date(2026, 5, 1), 40000.0, 1.49, 580.0)  # higher factor
    assert worsening_factor([prior, new]) is True
    assert rapid_reup_into_worse_terms([prior, new]) is True
    # same factor -> not worse terms even though rapid
    same = _deal(date(2026, 5, 1), 40000.0, 1.40, 580.0)
    assert worsening_factor([prior, same]) is False
    assert rapid_reup_into_worse_terms([prior, same]) is False


# =============================================================================
# Rung waterfall (B.3) — first match wins, stress override pulls down
# =============================================================================


def test_distressed_on_burden_over_ceiling():
    assert is_distressed(_active_signals(burden_ratio=0.35)) is True  # > 0.30
    assert is_distressed(_active_signals(burden_ratio=0.30)) is False  # boundary not over


def test_distressed_on_stress_event_default_note():
    assert is_distressed(_active_signals(has_default_note=True)) is True


def test_distressed_on_rapid_reup_into_worse_terms():
    assert is_distressed(_active_signals(rapid_reup_into_worse_terms=True)) is True


def test_distressed_requires_both_worsening_and_shrinking():
    assert is_distressed(_active_signals(worsening_factor=True, shrinking_net=False)) is False
    assert is_distressed(_active_signals(worsening_factor=True, shrinking_net=True)) is True


def test_stress_override_pulls_a_serial_looking_merchant_down():
    """A Serial-looking merchant (rapid re-up, 2 positions) with burden > 0.30 lands
    Distressed, not Serial — the stress override pulls down (B.3 / 4.7)."""
    s = _active_signals(rapid_reup_flag=True, active_position_cnt=2, burden_ratio=0.40)
    assert is_distressed(s) is True
    assert is_serial(s) is False  # serial explicitly excludes distressed
    assert rung_of(s) == C.RungState.DISTRESSED


def test_serial_first_match_over_disciplined():
    """rapid_reup (PRIMARY Serial signal) wins over a disciplined-looking shape."""
    s = _active_signals(rapid_reup_flag=True, est_paydown_pct=0.6, active_position_cnt=1)
    assert rung_of(s) == C.RungState.SERIAL


def test_serial_on_position_count():
    assert is_serial(_active_signals(active_position_cnt=2, rapid_reup_flag=False)) is True
    assert is_serial(_active_signals(disclosed_positions_cnt=3, active_position_cnt=1)) is True


def test_disciplined_requires_all_and_conditions():
    good = _active_signals(
        active_position_cnt=1, est_paydown_pct=0.6, clean_payments=True, prior_clean_renewal_count=1,
        rapid_reup_flag=False,
    )
    assert is_disciplined(good) is True
    assert rung_of(good) == C.RungState.DISCIPLINED
    # drop each AND-condition -> not disciplined
    assert is_disciplined({**good, "active_position_cnt": 2}) is False
    assert is_disciplined({**good, "est_paydown_pct": 0.4}) is False
    assert is_disciplined({**good, "clean_payments": False}) is False
    assert is_disciplined({**good, "prior_clean_renewal_count": 0, "has_renewal": False}) is False


def test_disciplined_unlinkable_renewal_is_not_a_disqualifier():
    """D-303: an unlinkable renewal (renewal_chain_incomplete) with current clean signals
    still counts as a prior clean renewal — a linking gap NEVER demotes a merchant."""
    s = _active_signals(
        active_position_cnt=1, est_paydown_pct=0.6, clean_payments=True,
        prior_clean_renewal_count=0, has_renewal=True, renewal_chain_incomplete=True,
        rapid_reup_flag=False,
    )
    assert has_prior_clean_renewal(s) is True
    assert rung_of(s) == C.RungState.DISCIPLINED


def test_growth_requires_rising_advance_and_falling_relative_burden():
    base = _active_signals(
        active_position_cnt=1, est_paydown_pct=0.6, clean_payments=True, prior_clean_renewal_count=1,
        rapid_reup_flag=False,
    )
    # disciplined but burden null in v1 -> Growth cannot trip (honest)
    assert rung_of({**base, "advance_rising": True, "relative_burden_falling": False}) == C.RungState.DISCIPLINED
    # with both growth signals present -> Growth
    assert rung_of({**base, "advance_rising": True, "relative_burden_falling": True}) == C.RungState.GROWTH


def test_graduate_requires_qualification():
    base = _active_signals(
        active_position_cnt=1, est_paydown_pct=0.6, clean_payments=True, prior_clean_renewal_count=1,
        rapid_reup_flag=False, advance_rising=True, relative_burden_falling=True,
    )
    assert rung_of(base) == C.RungState.GROWTH
    assert rung_of({**base, "graduate_qualified": True}) == C.RungState.GRADUATE


def test_unclassified_when_key_signals_missing():
    """Active merchant with no usable placement signal -> Unclassified (rung None),
    an explicit honest pile, never force-fit."""
    s = _active_signals(
        est_paydown_pct=None, rapid_reup_flag=False, active_position_cnt=1,
        prior_clean_renewal_count=0, has_renewal=False, burden_ratio=None,
        disclosed_positions_cnt=None,
    )
    assert rung_of(s) is None
    out = classify_merchant(s)
    assert is_unclassified(out["lifecycle_state"], out["rung"])
    assert out["route"] == C.LifecycleRoute.REVIEW
    assert "est_paydown_pct" in out["missing_signals"]


# =============================================================================
# Confidence (D-306) + direction_of_travel (4.7)
# =============================================================================


def test_confidence_is_borderline_driven_and_monotonic():
    """Deeper inside the band -> higher confidence; at the boundary -> the 0.5 floor."""
    at_boundary = confidence(C.LifecycleState.ACTIVE, C.RungState.DISCIPLINED,
                             _active_signals(est_paydown_pct=0.50))
    mid = confidence(C.LifecycleState.ACTIVE, C.RungState.DISCIPLINED,
                     _active_signals(est_paydown_pct=0.62))
    deep = confidence(C.LifecycleState.ACTIVE, C.RungState.DISCIPLINED,
                      _active_signals(est_paydown_pct=0.90))
    assert at_boundary == pytest.approx(0.5)
    assert at_boundary < mid < deep
    assert deep == pytest.approx(1.0)


def test_missing_data_does_not_lower_confidence():
    """D-306: an absent peripheral signal is omitted (not scored 0); a present borderline
    value is what lowers confidence."""
    deep = _active_signals(est_paydown_pct=0.95)
    absent_burden = confidence(C.LifecycleState.ACTIVE, C.RungState.DISCIPLINED, {**deep, "burden_ratio": None})
    borderline_burden = confidence(C.LifecycleState.ACTIVE, C.RungState.DISCIPLINED, {**deep, "burden_ratio": 0.145})
    assert absent_burden == pytest.approx(1.0)
    assert borderline_burden < absent_burden


def test_confidence_unclassified_is_floor():
    assert confidence(C.LifecycleState.ACTIVE, None, _active_signals()) == pytest.approx(0.5)


def test_confidence_in_bounds_for_all_states():
    for state, rung in [
        (C.LifecycleState.DEFAULTED, None),
        (C.LifecycleState.NEW_ESTABLISHING, None),
        (C.LifecycleState.DORMANT, None),
        (C.LifecycleState.ACTIVE, C.RungState.SERIAL),
        (C.LifecycleState.ACTIVE, None),
    ]:
        c = confidence(state, rung, _active_signals(time_since_last_active_days=800, active_position_cnt=0))
        assert 0.0 <= c <= 1.0


def test_direction_of_travel():
    serial = {"lifecycle_state": "active", "rung": 2}
    distressed = {"lifecycle_state": "active", "rung": 1}
    disciplined = {"lifecycle_state": "active", "rung": 3}
    assert direction_of_travel(serial, distressed) == C.DirectionOfTravel.SLIDING
    assert direction_of_travel(distressed, disciplined) == C.DirectionOfTravel.CLIMBING
    assert direction_of_travel(serial, serial) == C.DirectionOfTravel.HOLDING
    assert direction_of_travel(None, serial) == C.DirectionOfTravel.HOLDING
    # Unclassified is not rank-comparable -> holding
    assert direction_of_travel(serial, {"lifecycle_state": "active", "rung": None}) == C.DirectionOfTravel.HOLDING


def test_direction_lifecycle_slide_active_to_defaulted():
    assert direction_of_travel(
        {"lifecycle_state": "active", "rung": 3}, {"lifecycle_state": "defaulted", "rung": None}
    ) == C.DirectionOfTravel.SLIDING


# =============================================================================
# Four validation merchants (B.5) — classified end to end
# =============================================================================


def test_starr_defaulted_unknown_do_not_fund():
    out = classify_merchant({
        "has_default_note": True,  # closure closed_default (A.5b)
        "active_position_cnt": 0,
        "deal_count": 1,
        "has_renewal": True,
        "notes": STARR_WINDOW_TINTING["notes"],
    })
    assert out["lifecycle_state"] == C.LifecycleState.DEFAULTED == STARR_WINDOW_TINTING["expected"]["lifecycle_state"]
    assert out["default_subtype"] == C.DefaultSubtype.UNKNOWN
    assert out["route"] == C.LifecycleRoute.DO_NOT_FUND
    assert out["rung"] is None


def test_one_big_promotion_dormant_winback():
    tsla = (RUN_DATE - ONE_BIG_PROMOTION["funded_date"]).days  # ~6 years
    out = classify_merchant({
        "has_default_note": False,
        "active_position_cnt": 0,  # paid 100%, no open position
        "deal_count": 1,
        "has_renewal": False,
        "time_since_last_active_days": tsla,
        "median_renewal_gap_days": None,  # no history -> book median
        "book_median_gap_days": 365,
    })
    assert out["lifecycle_state"] == C.LifecycleState.DORMANT == ONE_BIG_PROMOTION["expected"]["lifecycle_state"]
    assert out["route"] == C.LifecycleRoute.WIN_BACK
    assert out["rung"] is None


def test_tom_snell_new_establishing_not_disciplined():
    out = classify_merchant({
        "has_default_note": False,
        "active_position_cnt": 1,
        "deal_count": 1,
        "has_renewal": False,
        "prior_clean_renewal_count": 0,
        "est_paydown_pct": 0.32,
        "clean_payments": True,
    })
    assert out["lifecycle_state"] == C.LifecycleState.NEW_ESTABLISHING == TOM_SNELL["expected"]["lifecycle_state"]
    assert out["route"] == C.LifecycleRoute.CLOCK_RUNNING
    assert out["rung"] is None  # NOT Disciplined until a clean renewal completes


def test_wolf_active_serial_rapid_reup():
    """Wolf renewed ~14 days into the prior position, upsizing 30k->40k. rapid_reup_flag
    computed from the two deals (D-302) -> Serial, and NOT Distressed (clean payments)."""
    prior = _deal(date(2026, 4, 17), 30000.0, 1.45, 435.0)
    new = _deal(WOLF_CORPORATION["funded_date"], 40000.0, 1.45, 580.0)
    reup = rapid_reup_flag([prior, new])
    assert reup is True
    out = classify_merchant({
        "has_default_note": False,
        "active_position_cnt": 1,
        "deal_count": 2,
        "has_renewal": True,
        "rapid_reup_flag": reup,
        "rapid_reup_into_worse_terms": rapid_reup_into_worse_terms([prior, new]),
        "est_paydown_pct": 0.20,
        "clean_payments": True,
        "burden_ratio": None,
    })
    assert out["lifecycle_state"] == C.LifecycleState.ACTIVE == WOLF_CORPORATION["expected"]["lifecycle_state"]
    assert out["rung"] == C.RungState.SERIAL
    assert out["route"] == C.LifecycleRoute.WATERFALL


# =============================================================================
# Output-object contract + field-map / no-surface invariants
# =============================================================================


def test_classification_output_object_shape():
    out = classify_merchant(_active_signals())
    for key in ("lifecycle_state", "rung", "confidence", "missing_signals", "direction_of_travel"):
        assert key in out  # Framework 4.7 output object
    assert isinstance(out["missing_signals"], list)
    assert out["lifecycle_state"] in C.LifecycleState.ALL
    assert out["rung"] in C.RungState.ALL or out["rung"] is None
    assert out["direction_of_travel"] in C.DirectionOfTravel.ALL
    assert 0.0 <= out["confidence"] <= 1.0


def test_rung_map_columns_unique_and_known_verdicts():
    cols = merchant_rung_columns()
    assert len(cols) == len(set(cols))
    assert len(cols) == len(MERCHANT_RUNG_MAP) + len(GOLD_MERCHANT_RUNG_DQ_COLUMNS)
    known = {C.Verdict.HAVE, C.Verdict.CARRY, C.Verdict.DISTRUST, C.Verdict.DERIVE,
             C.Verdict.MUST_CAPTURE, C.Verdict.REUSE, C.Verdict.FUTURE}
    for fs in MERCHANT_RUNG_MAP:
        assert fs.verdict in known


def test_rung_pk_is_merchant_and_run_date():
    assert MERCHANT_RUNG_MAP[0].silver_col == "merchant_id"
    assert MERCHANT_RUNG_MAP[1].silver_col == "classify_run_date"


def test_rung_reads_clock_not_sf_stored():
    """The classifier consumes S2 clock outputs only — no frozen SF snapshot leaks in."""
    cols = merchant_rung_columns()
    assert offending_surface_columns(cols) == []
    # confidence is a deterministic rules score, documented NOT-ML
    fs = next(f for f in MERCHANT_RUNG_MAP if f.silver_col == "confidence")
    assert "NOT an ML" in fs.notes


def test_unclassified_bucket_flag_present():
    flags = {name for name, _ in GOLD_MERCHANT_RUNG_DQ_COLUMNS}
    assert "is_unclassified" in flags and "is_gated" in flags
