"""Tier-1 tests for the Advisory layer (Build Plan §7 / Framework §2.3/§2.4/§5.9, S8).

Pure functions only (no Spark; the LLM is a stub `predict_fn`): the grounded fact pack + the
no-invented-numbers validator (D-802), the Structure Advisor's articulate-not-recompute +
honors-suppression behavior (D-803), and the compose→ground→gate orchestration (D-802) that
must BLOCK an ungrounded advisory, BLOCK a suppressed double-dip pitched as an offer, and require
a disclosure block for a specific offer in a regulated state. Plus map / schema / no-surface
invariants. The four validation merchants (Wolf central) anchor the honesty cases.
"""

from __future__ import annotations

import json
from datetime import date

from common import constants as C
from common.advisory import (
    advise_structure,
    build_advisory_rows,
    build_fact_pack,
    compose_advisory,
    ungrounded_tokens,
    validate_grounding,
)
from common.field_maps import MERCHANT_ADVISORY_MAP, merchant_advisory_columns
from common.io.guards import offending_surface_columns

_AT = C.AdvisoryType
_CS = C.ComplianceStatus
_OS = C.OfferStructure
_SV = C.SuitabilityVerdict
_RS = C.ReviewStatus
_OT = C.OfferType
RUN = date(2026, 7, 18)


def _approx(a, b, tol=1e-6):
    return a is not None and abs(float(a) - float(b)) <= tol


# Wolf Corporation (B.5): renewed ~14 days in, barely paid, $46,400 balance at factor 1.45.
WOLF = {
    "est_paydown_pct": 0.20,
    "est_current_balance": 46400.0,
    "factor_rate": 1.45,
    "active_position_cnt": 1,
    "offer_amount": 40000.0,
}
# A healthy, in-market single-position merchant eligible to renew.
HEALTHY = {
    "est_paydown_pct": 0.70,
    "est_current_balance": 10000.0,
    "factor_rate": 1.30,
    "active_position_cnt": 1,
    "offer_amount": 25000.0,
}


def _stub(headline, rationale, confidence=0.9, citation="facts"):
    """A fake Foundation Model reply (mirrors how the Spark driver injects the real chat client)."""
    payload = json.dumps(
        {"headline": headline, "rationale": rationale, "confidence": confidence, "citation": citation}
    )

    def _fn(*, endpoint, messages, max_tokens):
        return payload

    return _fn


# =============================================================================
# Fact pack + grounding validator (D-802)
# =============================================================================


def test_fact_pack_includes_only_present_signals_and_pct_dual_form():
    pack = build_fact_pack(WOLF, RUN, extra={"double_dip_cost": 20880.0})
    facts = pack["facts"]
    assert facts["est_current_balance"]["value"] == 46400.0
    assert facts["est_current_balance"]["source_field"].startswith("merchant_clock_current")
    # a percentage is speakable as both the stored fraction (0.2) and the ×100 form (20)
    assert "0.2" in pack["allowed_numbers"] and "20" in pack["allowed_numbers"]
    assert "46400" in pack["allowed_numbers"] and "20880" in pack["allowed_numbers"]


def test_fact_pack_omits_missing_numbers_never_fabricates():
    pack = build_fact_pack({"est_paydown_pct": 0.20}, RUN)
    assert "est_current_balance" not in pack["facts"]
    assert "offer_amount" not in pack["facts"]


def test_validate_grounding_accepts_grounded_text():
    pack = build_fact_pack(WOLF, RUN, extra={"double_dip_cost": 20880.0})
    text = "You've paid down 20% of your $46,400 balance; renewing now would cost an extra $20,880."
    assert ungrounded_tokens(text, pack) == []
    assert validate_grounding(text, pack) is True


def test_validate_grounding_rejects_invented_number():
    pack = build_fact_pack(WOLF, RUN, extra={"double_dip_cost": 20880.0})
    text = "We can advance you $50,000 today."  # 50000 is not in the pack
    assert "$50,000" in ungrounded_tokens(text, pack)
    assert validate_grounding(text, pack) is False


def test_validate_grounding_handles_dates():
    signals = {"predicted_next_event_date": date(2026, 8, 22)}
    pack = build_fact_pack(signals, RUN)
    assert validate_grounding("You're likely to need capital around 2026-08-22.", pack) is True
    assert validate_grounding("You're likely to need capital around 2026-09-01.", pack) is False


# =============================================================================
# Structure Advisor — articulate, never recompute; honor suppression (D-803)
# =============================================================================


def test_advise_structure_wolf_recommends_wait_and_pay_down():
    a = advise_structure(WOLF, offer_type=_OT.RENEWAL)
    assert a["structure"] == _OS.WAIT_AND_PAYDOWN
    assert a["suitability"] == _SV.WAIT
    assert _approx(a["double_dip_cost"], 20880.0)  # reused from S5 math, not recomputed
    assert a["recommended_action"] == "wait-and-pay-down"  # honest — never a pitch


def test_advise_structure_cannot_unsuppress_a_suppressed_buyout():
    # a barely-paid buyout candidate -> WAIT verdict -> the action stays wait-and-pay-down
    a = advise_structure(WOLF, offer_type=_OT.BUYOUT)
    assert a["recommended_action"] == "wait-and-pay-down"


def test_advise_structure_healthy_is_renewal_surface():
    a = advise_structure(HEALTHY, offer_type=_OT.RENEWAL)
    assert a["structure"] == _OS.RENEWAL
    assert a["suitability"] == _SV.SURFACE
    assert a["recommended_action"] == "renewal-eligible"


# =============================================================================
# Compose → ground → gate orchestration (D-802)
# =============================================================================


def test_compose_honest_wait_advice_passes_and_is_applied():
    predict = _stub(
        "Hold off on new capital for now",
        "You've paid down 20% of your $46,400 balance. Renewing now would cost an extra $20,880, "
        "so the honest move is to wait and pay down first.",
    )
    row = compose_advisory("M-WOLF", WOLF, RUN, predict, offer_type=None, governing_state="NY")
    assert row["advisory_type"] == _AT.ADVICE  # no concrete offer term named
    assert row["compliance_status"] == _CS.PASS
    assert row["review_status"] == _RS.APPLIED
    assert row["recommended_action"] == "wait-and-pay-down"


def test_compose_blocks_ungrounded_advisory():
    predict = _stub("Great news", "We can advance you $50,000 today.")  # invented number
    row = compose_advisory("M-WOLF", WOLF, RUN, predict, offer_type=None)
    assert row["review_status"] == _RS.REJECTED  # ungrounded -> never usable (§2.3)


def test_compose_blocks_suppressed_double_dip_pitched_as_offer():
    # trying to compose a concrete RENEWAL offer for Wolf (a WAIT case) -> specific-offer + not-surface
    predict = _stub("Renew today", "You can renew your position for $40,000.")
    row = compose_advisory("M-WOLF", WOLF, RUN, predict, offer_type=_OT.RENEWAL, governing_state="TX")
    assert row["advisory_type"] == _AT.SPECIFIC_OFFER
    assert row["compliance_status"] == _CS.BLOCKED
    assert row["review_status"] == _RS.REVIEW  # stored + auditable, never deliverable


def test_compose_specific_offer_needs_disclosure_in_regulated_state():
    predict = _stub("You're eligible to renew", "You can renew for $25,000.")
    blocked = compose_advisory("M-OK", HEALTHY, RUN, predict, offer_type=_OT.RENEWAL, governing_state="NY")
    assert blocked["advisory_type"] == _AT.SPECIFIC_OFFER
    assert blocked["compliance_status"] == _CS.BLOCKED
    assert blocked["required_disclosures"] == C.DisclosureRegime.NY_CFDL

    ok = compose_advisory(
        "M-OK", HEALTHY, RUN, predict, offer_type=_OT.RENEWAL, governing_state="NY",
        has_disclosure_block=True,
    )
    assert ok["compliance_status"] == _CS.PASS
    assert ok["review_status"] == _RS.APPLIED


def test_build_advisory_rows_batch():
    predict = _stub("Hold off", "Paid down 20% of $46,400; wait and pay down.")
    rows = build_advisory_rows(
        [{"merchant_id": "M1", "signals": WOLF, "governing_state": "TX"}], RUN, predict
    )
    assert len(rows) == 1 and rows[0]["merchant_id"] == "M1"
    assert rows[0]["model_version"].startswith("advisory-composer/")


# =============================================================================
# Contract invariants — map / schema / no-surface
# =============================================================================


def test_advisory_columns_match_map():
    # Spark schema builder is exercised in tier-2 (no pyspark in the tier-1 env); here we assert
    # the column list is the single source (the field map), as the other gold tables' tests do.
    assert merchant_advisory_columns() == [fs.silver_col for fs in MERCHANT_ADVISORY_MAP]


def test_advisory_map_no_surface_and_no_spine_column():
    cols = merchant_advisory_columns()
    assert offending_surface_columns(cols) == []  # no _sf_stored_* leaks
    # the advisory writes its OWN fields only — never a spine-math column
    forbidden = {"rung", "lifecycle_state", "burden_ratio", "est_paydown_pct", "current_state"}
    assert forbidden.isdisjoint(set(cols))
