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
    classify_default_cause,
    is_applicable,
    make_extraction,
    normalize_subtype_label,
    parse_response,
    review_status,
)
from common.agents.data_steward import ALLOWED_LABELS, build_extraction_rows
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
# Data Steward LLM agent — prompt parse + offline orchestration (no network)
# =============================================================================


def test_parse_response_clean_and_wrapped_json():
    r = parse_response('{"label": "true_default", "confidence": 0.92, "citation": "charged off"}')
    assert r == {"label": "true_default", "confidence": 0.92, "citation": "charged off"}
    # tolerant: a prose-wrapped JSON object is still extracted
    r2 = parse_response('Here is my answer:\n{"label":"early_payoff","confidence":0.8,"citation":null}\nThanks')
    assert r2["label"] == "early_payoff" and r2["citation"] is None


def test_parse_response_defensive_unknown_and_clamp():
    # malformed -> unknown/0.0 (a hallucination can never look confident)
    assert parse_response("not json at all") == {"label": "unknown", "confidence": 0.0, "citation": None}
    assert parse_response("") == {"label": "unknown", "confidence": 0.0, "citation": None}
    # out-of-vocabulary label -> unknown (never guessed into a real category)
    oov = parse_response('{"label": "merchant_died", "confidence": 0.99}')
    assert oov["label"] == "unknown"
    # confidence clamped to [0,1]
    assert parse_response('{"label":"true_default","confidence":1.7}')["confidence"] == 1.0
    assert parse_response('{"label":"true_default","confidence":-3}')["confidence"] == 0.0
    # every allowed label round-trips
    for lab in ALLOWED_LABELS:
        assert parse_response(f'{{"label":"{lab}","confidence":0.5}}')["label"] == lab


def test_classify_default_cause_calls_model_and_short_circuits_blank():
    calls = []

    def fake_predict(endpoint, messages, max_tokens=300):
        calls.append((endpoint, messages))
        return '{"label": "true_default", "confidence": 0.9, "citation": "wrote off balance"}'

    out = classify_default_cause("Acct charged off; uncollectable.", fake_predict)
    assert out["label"] == "true_default" and out["confidence"] == 0.9
    assert len(calls) == 1 and calls[0][1][0]["role"] == "system"  # grounded system prompt sent
    # blank / missing notes never call the model (nothing to ground on) -> unknown
    assert classify_default_cause("   ", fake_predict)["label"] == "unknown"
    assert classify_default_cause(None, fake_predict)["label"] == "unknown"
    assert len(calls) == 1


def _fake_for(label, conf, citation="cited snippet"):
    payload = f'{{"label": "{label}", "confidence": {conf}, "citation": "{citation}"}}'
    return lambda endpoint, messages, max_tokens=300: payload


def test_build_extraction_rows_applies_grounds_and_routes():
    rows = build_extraction_rows(
        [{"merchant_id": "M1", "deal_id": "OPP-1", "notes": "charged off, uncollectable"}],
        RUN, _fake_for("true_default", 0.95), model_version="ds-test/v1",
    )
    e = rows[0]
    assert e["value"] == _DS.TRUE_DEFAULT and e["review_status"] == C.ReviewStatus.APPLIED
    assert e["source_ref"] == "silver.deals.notes:OPP-1" and e["model_version"] == "ds-test/v1"
    # the event-log enrichment carries the resolved route only when APPLIED
    assert e["default_subtype"] == _DS.TRUE_DEFAULT and e["route"] == C.LifecycleRoute.DISTRESSED_EXIT
    assert is_applicable(e) and set(merchant_extraction_columns()).issubset(e.keys())


def test_build_extraction_rows_low_confidence_reviews_and_no_notes_rejected():
    # grounded but low-confidence -> REVIEW, conservative (no resolved route surfaced)
    low = build_extraction_rows(
        [{"merchant_id": "M1", "deal_id": "OPP-1", "notes": "maybe early payoff?"}],
        RUN, _fake_for("early_payoff", 0.4),
    )[0]
    assert low["review_status"] == C.ReviewStatus.REVIEW and low["default_subtype"] is None
    # no notes -> agent short-circuits unknown AND ungrounded (no source_ref) -> REJECTED
    none = build_extraction_rows(
        [{"merchant_id": "M2", "deal_id": "OPP-2", "notes": None}],
        RUN, _fake_for("true_default", 0.99),
    )[0]
    assert none["review_status"] == C.ReviewStatus.REJECTED and not is_applicable(none)
    assert none["source_ref"] is None


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
