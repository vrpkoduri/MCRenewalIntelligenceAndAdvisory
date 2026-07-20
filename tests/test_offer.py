"""Tier-1 tests for the Offer Engine integration layer (Build Plan §6 / Framework §5.7, S5).

Pure functions only (no Spark): the renewal-vs-buyout structure math + double-dip cost
(D-506), the suitability gate (engine proposes / advisory disposes), candidate
eligible_offer_types (D-504), and the merchant-profile assembly with honest missing-field
flags (D-503). Plus field-map / no-surface invariants.

The Offer Engine REUSES the existing routing engine — these tests cover MRI's integration
logic only (offer-type mapping + structure honesty), NOT funder matching (that is the reused
engine, exercised in tier-2).
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.field_maps import (
    GOLD_MERCHANT_OFFERS_DQ_COLUMNS,
    MERCHANT_OFFERS_MAP,
    merchant_offers_columns,
)
from common.io.guards import offending_surface_columns
from common.offer import (
    build_funder_profile,
    candidate_offer_types,
    double_dip_cost,
    is_suitable,
    recommend_structure,
    structure_evaluation,
    suitability_verdict,
    tib_months,
)

_OT = C.OfferType
_OS = C.OfferStructure
_SV = C.SuitabilityVerdict
_LS = C.LifecycleState
_R = C.RungState
RUN = date(2026, 6, 2)


# =============================================================================
# Renewal-vs-buyout structure + double-dip (D-506)
# =============================================================================


def test_double_dip_cost_uses_real_balance_and_factor():
    # rolling a 46,400 balance at factor 1.45 costs 46,400 * 0.45 again
    assert double_dip_cost(46400.0, 1.45) == pytest_approx(20880.0)
    assert double_dip_cost(None, 1.45) is None
    assert double_dip_cost(1000.0, None) is None


def test_recommend_structure_wait_when_barely_paid():
    # paydown < 50% -> rolling is the expensive double-dip -> wait and pay down (Wolf)
    assert recommend_structure(0.20, 1) == _OS.WAIT_AND_PAYDOWN
    assert recommend_structure(0.49, 2) == _OS.WAIT_AND_PAYDOWN


def test_recommend_structure_buyout_when_multi_position():
    assert recommend_structure(0.70, 2) == _OS.BUYOUT  # mid-life paydown + concurrent positions


def test_recommend_structure_near_payoff_never_buyout():
    # C-032/D-808: a 2-position merchant essentially paid off (>=90%) should NOT be told to
    # "consolidate" a near-zero balance — the honest structure is finish/renew, not buyout.
    assert recommend_structure(0.999, 2) == _OS.RENEWAL
    assert recommend_structure(0.90, 2) == _OS.RENEWAL  # boundary
    assert recommend_structure(0.89, 2) == _OS.BUYOUT   # just below → still consolidation candidate


def test_recommend_structure_renewal_single_healthy():
    assert recommend_structure(0.70, 1) == _OS.RENEWAL


def test_recommend_structure_unknown_paydown():
    assert recommend_structure(None, 1) is None  # never guess a structure


def test_structure_evaluation_composes():
    se = structure_evaluation(
        {"est_paydown_pct": 0.20, "est_current_balance": 46400.0, "factor_rate": 1.45, "active_position_cnt": 1}
    )
    assert se["structure"] == _OS.WAIT_AND_PAYDOWN
    assert se["double_dip_cost"] == pytest_approx(20880.0)
    assert se["rolled_balance"] == 46400.0


# =============================================================================
# Suitability gate (engine proposes, advisory disposes)
# =============================================================================


def test_suitability_wait_suppresses_new_advance():
    assert suitability_verdict(_OT.BUYOUT, _OS.WAIT_AND_PAYDOWN) == _SV.WAIT
    assert suitability_verdict(_OT.RENEWAL, _OS.WAIT_AND_PAYDOWN) == _SV.WAIT


def test_suitability_suppresses_double_dip_buyout():
    # a matchable buyout whose right structure is NOT buyout -> suppress (the key gate test)
    assert suitability_verdict(_OT.BUYOUT, _OS.RENEWAL) == _SV.SUPPRESS
    assert is_suitable(_OT.BUYOUT, _OS.RENEWAL) is False


def test_suitability_surfaces_aligned_offer():
    assert suitability_verdict(_OT.BUYOUT, _OS.BUYOUT) == _SV.SURFACE
    assert suitability_verdict(_OT.RENEWAL, _OS.RENEWAL) == _SV.SURFACE
    assert is_suitable(_OT.RENEWAL, _OS.RENEWAL) is True


# =============================================================================
# Candidate eligible_offer_types (D-504)
# =============================================================================


def test_offer_types_gated_and_unclassified_none_yet():
    for lc in (_LS.DEFAULTED, _LS.DORMANT, _LS.NEW_ESTABLISHING):
        assert candidate_offer_types({"lifecycle_state": lc, "rung": None}) == [_OT.NONE_YET]
    # active but Unclassified
    assert candidate_offer_types({"lifecycle_state": _LS.ACTIVE, "rung": None}) == [_OT.NONE_YET]


def test_offer_types_serial_to_buyout():
    types = candidate_offer_types(
        {"lifecycle_state": _LS.ACTIVE, "rung": _R.SERIAL, "rapid_reup_flag": True, "active_position_cnt": 1}
    )
    assert types == [_OT.BUYOUT, _OT.LARGER_ADVANCE]


def test_offer_types_in_market_disciplined_to_renewal():
    types = candidate_offer_types(
        {"lifecycle_state": _LS.ACTIVE, "rung": _R.DISCIPLINED, "rapid_reup_flag": False,
         "active_position_cnt": 1, "is_eligible_now": True}
    )
    assert types == [_OT.RENEWAL, _OT.LARGER_ADVANCE]


def test_offer_types_active_not_eligible_none_yet():
    types = candidate_offer_types(
        {"lifecycle_state": _LS.ACTIVE, "rung": _R.DISCIPLINED, "rapid_reup_flag": False,
         "active_position_cnt": 1, "is_eligible_now": False}
    )
    assert types == [_OT.NONE_YET]


# =============================================================================
# Profile assembly + honest missing fields (D-503)
# =============================================================================


def test_tib_months_prefers_start_date():
    assert tib_months(date(2019, 12, 1), None, RUN) == 78  # 2019-12 -> 2026-06
    assert tib_months(None, 42, RUN) == 42
    assert tib_months(None, 0, RUN) is None  # 0 is missing, not a value


def test_build_profile_populates_available_flags_missing():
    profile, missing = build_funder_profile(
        {"merchant_id": "M1", "azure_merchant_id": "1043872", "governing_state": "NY",
         "fico": 640, "business_start_date": date(2019, 12, 1), "active_position_cnt": 1,
         "industry": "Retail"},
        RUN,
    )
    assert profile["fico"] == 640 and profile["tib_months"] == 78 and profile["business_state"] == "NY"
    # revenue / NSF / bankruptcy never available in v1 -> flagged missing, never faked
    for f in ("monthly_revenue", "nsf_per_month", "has_open_bankruptcy"):
        assert f in missing


def test_build_profile_missing_fico_flagged():
    profile, missing = build_funder_profile(
        {"merchant_id": "M2", "governing_state": "CA", "fico": 0, "active_position_cnt": 2}, RUN
    )
    assert profile["fico"] is None and "fico" in missing  # 0 FICO is missing


# =============================================================================
# Field-map / no-surface invariants
# =============================================================================

_KNOWN = {C.Verdict.HAVE, C.Verdict.CARRY, C.Verdict.DISTRUST, C.Verdict.DERIVE,
          C.Verdict.MUST_CAPTURE, C.Verdict.REUSE, C.Verdict.FUTURE}


def test_offers_map_unique_known_verdicts_pk_order():
    cols = merchant_offers_columns()
    assert len(cols) == len(set(cols))
    assert len(cols) == len(MERCHANT_OFFERS_MAP) + len(GOLD_MERCHANT_OFFERS_DQ_COLUMNS)
    assert MERCHANT_OFFERS_MAP[0].silver_col == "merchant_id"
    assert MERCHANT_OFFERS_MAP[1].silver_col == "offer_run_date"
    for fs in MERCHANT_OFFERS_MAP:
        assert fs.verdict in _KNOWN


def test_offers_no_surface_and_dq_flags():
    assert offending_surface_columns(merchant_offers_columns()) == []
    flags = {name for name, _ in GOLD_MERCHANT_OFFERS_DQ_COLUMNS}
    assert "max_sustainable_advance_is_missing" in flags and "offer_profile_unmatched" in flags


def test_matched_funders_is_reuse_not_rebuilt():
    """matched_funders must be sourced by REUSING the existing routing engine (Rule 6)."""
    fs = next(f for f in MERCHANT_OFFERS_MAP if f.silver_col == "matched_funders")
    assert fs.verdict == C.Verdict.REUSE
    assert "reuse" in fs.sf_source.lower()


# tiny local approx helper (avoid importing pytest just for approx symanttics elsewhere)
def pytest_approx(x, tol=1e-6):
    class _A:
        def __eq__(self, other):
            return other is not None and abs(float(other) - x) <= tol
    return _A()
