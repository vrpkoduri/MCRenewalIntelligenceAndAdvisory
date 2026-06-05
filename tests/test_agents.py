"""Tier-1 tests for the agentic-layer deterministic tools (Framework §5.9, S7 Phase 1).

Pure functions only (no LLM, no Spark): the Data Steward's default-subtype mapper + confidence
gate, the grounding contract (source_ref/confidence/model_version → review_status), and the S3
lifecycle gate's consumption of a resolved sub-type. These are the correctness-critical tools
the LLM agent calls — the agent EXTRACTS, these + the spine DECIDE/route (so "the rules fired"
stays the auditable answer). Plus field-map / no-surface invariants.
"""

from __future__ import annotations

import pytest

from common import constants as C
from common.agents import (
    apply_default_subtype,
    is_applicable,
    make_extraction,
    normalize_subtype_label,
    review_status,
)
from common.field_maps import MERCHANT_EXTRACTION_MAP, merchant_extraction_columns
from common.io.guards import offending_surface_columns
from common.rung.lifecycle import default_subtype, lifecycle_state

_DS = C.DefaultSubtype
RUN = __import__("datetime").date(2026, 6, 5)


# =============================================================================
# Default-subtype mapper + confidence gate (Data Steward)
# =============================================================================


def test_normalize_subtype_label_synonyms():
    assert normalize_subtype_label("true default") == _DS.TRUE_DEFAULT
    assert normalize_subtype_label("charged off") == _DS.TRUE_DEFAULT
    assert normalize_subtype_label("clawback") == _DS.EARLY_PAYOFF
    assert normalize_subtype_label("paid early") == _DS.EARLY_PAYOFF
    assert normalize_subtype_label("restructured") == _DS.RESTRUCTURED
    assert normalize_subtype_label("workout agreement") == _DS.RESTRUCTURED
    # enum value passes through; junk/empty -> unknown (never guess)
    assert normalize_subtype_label("true_default") == _DS.TRUE_DEFAULT
    assert normalize_subtype_label("") == _DS.UNKNOWN
    assert normalize_subtype_label("something unrelated") == _DS.UNKNOWN


def test_apply_default_subtype_high_confidence_applies_and_routes():
    r = apply_default_subtype("true default", 0.95)
    assert r["default_subtype"] == _DS.TRUE_DEFAULT
    assert r["route"] == C.LifecycleRoute.DISTRESSED_EXIT
    assert r["review_status"] == C.ReviewStatus.APPLIED

    r2 = apply_default_subtype("clawback", 0.9)
    assert r2["default_subtype"] == _DS.EARLY_PAYOFF and r2["route"] == C.LifecycleRoute.WIN_BACK
    r3 = apply_default_subtype("restructured", 0.9)
    assert r3["default_subtype"] == _DS.RESTRUCTURED and r3["route"] == C.LifecycleRoute.IMPAIRED_MANAGED


def test_apply_default_subtype_low_confidence_stays_unknown_review():
    r = apply_default_subtype("true default", 0.40)  # below threshold
    assert r["default_subtype"] == _DS.UNKNOWN
    assert r["route"] == C.LifecycleRoute.DO_NOT_FUND  # conservative interim
    assert r["review_status"] == C.ReviewStatus.REVIEW


def test_apply_default_subtype_unrecognized_label_review():
    r = apply_default_subtype("not sure", 0.99)
    assert r["default_subtype"] == _DS.UNKNOWN and r["review_status"] == C.ReviewStatus.REVIEW


# =============================================================================
# Grounding contract (D-705)
# =============================================================================


def test_review_status_grounding_gate():
    assert review_status(0.9, "silver.deals.notes:OPP-1") == C.ReviewStatus.APPLIED
    assert review_status(0.5, "silver.deals.notes:OPP-1") == C.ReviewStatus.REVIEW  # low confidence
    assert review_status(0.9, None) == C.ReviewStatus.REJECTED  # ungrounded


def test_make_extraction_builds_grounded_row():
    e = make_extraction("M1", "OPP-1", C.ExtractionType.DEFAULT_SUBTYPE, "true_default", 0.95,
                        "silver.deals.notes:OPP-1", "ds-v1", RUN, citation="Defaulted — charged off")
    assert set(e.keys()) == set(merchant_extraction_columns())
    assert e["review_status"] == C.ReviewStatus.APPLIED and is_applicable(e)
    assert e["value"] == "true_default" and e["source_ref"]


def test_make_extraction_ungrounded_is_rejected_not_applicable():
    e = make_extraction("M1", "OPP-1", C.ExtractionType.DEFAULT_SUBTYPE, "true_default", 0.95,
                        None, "ds-v1", RUN)
    assert e["review_status"] == C.ReviewStatus.REJECTED and not is_applicable(e)


def test_make_extraction_requires_model_version_and_known_type():
    with pytest.raises(ValueError):
        make_extraction("M1", "OPP-1", C.ExtractionType.DEFAULT_SUBTYPE, "x", 0.9, "ref", "", RUN)
    with pytest.raises(ValueError):
        make_extraction("M1", "OPP-1", "bogus_type", "x", 0.9, "ref", "v1", RUN)


# =============================================================================
# Lifecycle gate consumes the resolved sub-type (the agent extracts, the gate routes)
# =============================================================================


def test_default_subtype_honors_resolved_else_unknown():
    assert default_subtype(resolved_subtype=_DS.TRUE_DEFAULT) == _DS.TRUE_DEFAULT
    assert default_subtype(resolved_subtype=_DS.UNKNOWN) == _DS.UNKNOWN
    assert default_subtype(resolved_subtype=None) == _DS.UNKNOWN
    assert default_subtype(resolved_subtype="bogus") == _DS.UNKNOWN  # invalid ignored


def test_lifecycle_gate_routes_resolved_default():
    # defaulted merchant whose Data Steward resolved true_default -> distressed-exit route
    out = lifecycle_state({"has_default_note": True, "resolved_default_subtype": _DS.TRUE_DEFAULT})
    assert out["state"] == C.LifecycleState.DEFAULTED
    assert out["default_subtype"] == _DS.TRUE_DEFAULT
    assert out["route"] == C.LifecycleRoute.DISTRESSED_EXIT
    # unresolved -> conservative do-not-fund (today's behavior, unchanged)
    out2 = lifecycle_state({"has_default_note": True})
    assert out2["default_subtype"] == _DS.UNKNOWN and out2["route"] == C.LifecycleRoute.DO_NOT_FUND


# =============================================================================
# Field-map / no-surface / constants invariants
# =============================================================================

_KNOWN = {C.Verdict.HAVE, C.Verdict.CARRY, C.Verdict.DISTRUST, C.Verdict.DERIVE,
          C.Verdict.MUST_CAPTURE, C.Verdict.REUSE, C.Verdict.FUTURE}


def test_extraction_map_unique_known_verdicts_grounding_cols():
    cols = merchant_extraction_columns()
    assert len(cols) == len(set(cols))
    for fs in MERCHANT_EXTRACTION_MAP:
        assert fs.verdict in _KNOWN
    # grounding/audit columns must exist
    for required in ("source_ref", "confidence", "model_version", "review_status", "extraction_type"):
        assert required in cols


def test_extraction_no_surface_and_event_type_and_threshold():
    assert offending_surface_columns(merchant_extraction_columns()) == []
    assert C.EventType.AGENT_EXTRACTION in C.EventType.ALL
    assert 0 < C.AGENT_CONFIDENCE_REVIEW_MIN <= 1
    assert C.ExtractionType.DEFAULT_SUBTYPE in C.ExtractionType.ALL
