"""Tier-1 tests for the first-class Compliance Gate (Build Plan §7 / Framework §2.4, S8).

Pure functions only (no Spark, no LLM): the advice-vs-specific-offer classifier (D-806), the
state-aware disclosure lookup (D-805), and the HARD gate (D-801) that BLOCKs an ungrounded
output, a suppressed/unsuitable offer pitched as specific, or a specific offer missing a required
disclosure — the exact §2.4/§2.3 failures the gate exists to stop. Realizes the S5
compliance_gate_hook (D-508).
"""

from __future__ import annotations

from common import constants as C
from common.compliance import (
    classify_output_type,
    compliance_gate,
    disclosure_regime,
    names_concrete_terms,
    passes,
    required_disclosures,
)

_AT = C.AdvisoryType
_CS = C.ComplianceStatus
_SV = C.SuitabilityVerdict
_DR = C.DisclosureRegime


# =============================================================================
# Advice-vs-specific-offer classification (D-806)
# =============================================================================


def test_classify_specific_offer_when_concrete_terms():
    a = {"facts": {"offer_amount": 40000.0}, "recommended_action": "renewal-eligible"}
    assert names_concrete_terms(a["facts"]) is True
    assert classify_output_type(a) == _AT.SPECIFIC_OFFER


def test_classify_advice_when_action_but_no_concrete_terms():
    a = {"facts": {"double_dip_cost": 20880.0, "est_paydown_pct": 0.20}, "recommended_action": "wait-and-pay-down"}
    assert names_concrete_terms(a["facts"]) is False
    assert classify_output_type(a) == _AT.ADVICE


def test_classify_factual_summary_when_no_action_no_terms():
    a = {"facts": {"est_paydown_pct": 0.80}, "recommended_action": None}
    assert classify_output_type(a) == _AT.FACTUAL_SUMMARY


# =============================================================================
# State-aware disclosure (D-805)
# =============================================================================


def test_disclosure_regime_by_state_case_insensitive():
    assert disclosure_regime("CA") == _DR.CA_CFDL
    assert disclosure_regime("ny") == _DR.NY_CFDL
    assert disclosure_regime("UT") == _DR.UT_CFR
    assert disclosure_regime("va") == _DR.VA_CFR


def test_disclosure_regime_unknown_or_absent_is_none():
    # NONE = "no special regime identified", NOT an assertion that none exists (D-805).
    assert disclosure_regime("TX") == _DR.NONE
    assert disclosure_regime(None) == _DR.NONE
    assert disclosure_regime("") == _DR.NONE


def test_required_disclosures_only_for_specific_offer():
    assert required_disclosures(_AT.SPECIFIC_OFFER, "NY") == [_DR.NY_CFDL]
    # advice / factual summaries name no concrete terms -> no disclosure required
    assert required_disclosures(_AT.ADVICE, "NY") == []
    assert required_disclosures(_AT.FACTUAL_SUMMARY, "CA") == []
    # a specific offer in an unregulated state -> none required
    assert required_disclosures(_AT.SPECIFIC_OFFER, "TX") == []


# =============================================================================
# The HARD gate (D-801)
# =============================================================================


def _advice(action="wait-and-pay-down"):
    return {"facts": {"double_dip_cost": 20880.0}, "recommended_action": action}


def _specific_offer():
    return {"facts": {"offer_amount": 40000.0}, "recommended_action": "renewal-eligible"}


def test_gate_passes_grounded_honest_advice():
    g = compliance_gate(_advice(), "NY", None, grounded=True)
    assert g["status"] == _CS.PASS
    assert g["output_type"] == _AT.ADVICE
    assert g["required_disclosures"] == []
    assert passes(g) is True


def test_gate_blocks_ungrounded_output():
    g = compliance_gate(_advice(), "TX", None, grounded=False)
    assert g["status"] == _CS.BLOCKED
    assert any("ungrounded" in r for r in g["reasons"])
    assert passes(g) is False


def test_gate_blocks_suppressed_double_dip_pitched_as_offer():
    # the classic §2.3 failure: a matchable buyout that is a double-dip must never be surfaced
    g = compliance_gate(_specific_offer(), "TX", _SV.SUPPRESS, grounded=True)
    assert g["status"] == _CS.BLOCKED
    assert any("unsuitable-offer-pitched" in r for r in g["reasons"])


def test_gate_blocks_wait_case_pitched_as_offer():
    g = compliance_gate(_specific_offer(), "TX", _SV.WAIT, grounded=True)
    assert g["status"] == _CS.BLOCKED


def test_gate_blocks_specific_offer_missing_required_disclosure():
    g = compliance_gate(_specific_offer(), "NY", _SV.SURFACE, grounded=True, has_disclosure_block=False)
    assert g["status"] == _CS.BLOCKED
    assert any("missing-disclosure" in r for r in g["reasons"])
    assert g["required_disclosures"] == [_DR.NY_CFDL]


def test_gate_passes_specific_offer_with_disclosure_block():
    g = compliance_gate(_specific_offer(), "NY", _SV.SURFACE, grounded=True, has_disclosure_block=True)
    assert g["status"] == _CS.PASS


def test_gate_passes_surface_offer_in_unregulated_state():
    g = compliance_gate(_specific_offer(), "TX", _SV.SURFACE, grounded=True)
    assert g["status"] == _CS.PASS
    assert g["output_type"] == _AT.SPECIFIC_OFFER
