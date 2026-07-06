"""Tier-1 tests for the Statement Analyst agent half (`common/agents/statement_analyst.py`,
Framework §5.9 / S7 Phase 2 / C-026). No network — the LLM is an injected fake `predict_fn`.

Covers the tolerant/defensive parser, the blank-text short-circuit, and the
`build_statement_rows` orchestration with the three C-026 guardrails: freshness gating (#2),
the revenue confidence haircut (#3), and that a None/unknown value is never APPLIED. (#1 —
advisory-only — is structural: nothing here feeds the rung waterfall; see the module docstring.)
"""

from __future__ import annotations

import json
from datetime import date

from common import constants as C
from common.agents.statement_analyst import (
    MODEL_VERSION,
    build_statement_extractions,
    build_statement_rows,
    classify_statement,
    parse_response,
)

RUN = date(2026, 6, 9)
FRESH = "2026-05-01"   # 39 days before RUN
STALE = "2025-01-01"   # > STATEMENT_FRESHNESS_MAX_DAYS before RUN


def _fake(reply):
    """A predict_fn that returns a fixed reply and records the messages it was called with."""
    calls = []

    def _fn(endpoint, messages, max_tokens=900):
        calls.append({"endpoint": endpoint, "messages": messages, "max_tokens": max_tokens})
        return reply

    _fn.calls = calls
    return _fn


def _reply(positions, deposits, period, as_of=FRESH, confidence=0.9, citation="ACH DEBIT — FUNDER B"):
    return json.dumps({
        "positions": positions, "deposits_operating_total": deposits, "period_days": period,
        "as_of_date": as_of, "confidence": confidence, "citation": citation,
    })


_TWO_POS = [
    {"funder": "Morgan Cash", "payment_amount": 500, "payment_frequency": "Weekly", "is_morgan_cash": True},
    {"funder": "Funder B", "payment_amount": 300, "payment_frequency": "Daily", "is_morgan_cash": False},
]


def test_parse_response_clean_prose_wrapped_and_defensive():
    clean = parse_response(_reply(_TWO_POS, 42000, 28))
    assert len(clean["positions"]) == 2 and clean["period_days"] == 28
    assert clean["deposits_operating_total"] == 42000.0 and clean["as_of_date"] == FRESH
    wrapped = parse_response("here you go:\n" + _reply(_TWO_POS, 42000, 28) + "\nthanks")
    assert len(wrapped["positions"]) == 2
    junk = parse_response("the statement was unreadable")
    assert junk["positions"] == [] and junk["confidence"] == 0.0 and junk["deposits_operating_total"] is None


def test_parse_response_coerces_and_drops_malformed_positions():
    p = parse_response(_reply([
        {"funder": "X", "payment_amount": "not a number", "payment_frequency": "Weekly"},
        "garbage-not-a-dict",
        {"funder": "", "payment_amount": 100, "payment_frequency": "Daily", "is_morgan_cash": True},
    ], None, None))
    assert len(p["positions"]) == 2          # the string entry is dropped
    assert p["positions"][0]["payment_amount"] is None   # uncoercible → None (never fabricated)
    assert p["positions"][1]["funder"] is None           # blank funder → None
    assert p["deposits_operating_total"] is None and p["period_days"] is None


def test_classify_statement_blank_short_circuits_no_call():
    fn = _fake(_reply(_TWO_POS, 42000, 28))
    out = classify_statement("   ", fn)
    assert out["positions"] == [] and out["confidence"] == 0.0
    assert fn.calls == []  # no LLM call on blank text
    classify_statement("STATEMENT ...", fn)
    assert fn.calls and fn.calls[0]["messages"][0]["role"] == "system"


def _rows_by_type(rows):
    return {r["extraction_type"]: r for r in rows}


def test_build_statement_rows_emits_three_grounded_signals_and_applies():
    recs = [{"merchant_id": "M1", "deal_id": "D1", "statement_text": "stmt",
             "source_ref": "salesforce.contentversion:068X", "as_of_date": FRESH}]
    rows = build_statement_rows(recs, RUN, _fake(_reply(_TWO_POS, 42000, 28)))
    by = _rows_by_type(rows)
    assert set(by) == {C.ExtractionType.CONCURRENT_POSITIONS, C.ExtractionType.WEEKLY_DEBIT,
                       C.ExtractionType.EST_WEEKLY_REVENUE}
    # other-funder positions only (MC excluded), weekly debit incl MC, weekly revenue from deposits
    assert by[C.ExtractionType.CONCURRENT_POSITIONS]["value"] == "1"
    assert by[C.ExtractionType.WEEKLY_DEBIT]["value"] == "2000.0"        # 500 + 300*5
    assert by[C.ExtractionType.EST_WEEKLY_REVENUE]["value"] == "10500.0"  # 42000 / 4 weeks
    # grounded + fresh + confident → APPLIED; source_ref carries the as_of date; model_version stamped
    assert by[C.ExtractionType.CONCURRENT_POSITIONS]["review_status"] == C.ReviewStatus.APPLIED
    assert by[C.ExtractionType.WEEKLY_DEBIT]["source_ref"] == "salesforce.contentversion:068X@" + FRESH
    assert all(r["model_version"] == MODEL_VERSION for r in rows)


def test_revenue_confidence_haircut_reviews_when_positions_apply():
    # confidence 0.80: positions/debit (0.80 ≥ 0.70) APPLY, but revenue 0.80×0.85=0.68 < 0.70 → REVIEW (#3)
    recs = [{"merchant_id": "M1", "deal_id": "D1", "statement_text": "stmt",
             "source_ref": "ref", "as_of_date": FRESH}]
    by = _rows_by_type(build_statement_rows(recs, RUN, _fake(_reply(_TWO_POS, 42000, 28, confidence=0.80))))
    assert by[C.ExtractionType.CONCURRENT_POSITIONS]["review_status"] == C.ReviewStatus.APPLIED
    assert by[C.ExtractionType.WEEKLY_DEBIT]["review_status"] == C.ReviewStatus.APPLIED
    assert by[C.ExtractionType.EST_WEEKLY_REVENUE]["review_status"] == C.ReviewStatus.REVIEW


def test_stale_statement_all_reviewed_not_surfaced():
    recs = [{"merchant_id": "M1", "deal_id": "D1", "statement_text": "stmt",
             "source_ref": "ref", "as_of_date": STALE}]
    rows = build_statement_rows(recs, RUN, _fake(_reply(_TWO_POS, 42000, 28, as_of=STALE)))
    assert all(r["review_status"] == C.ReviewStatus.REVIEW for r in rows)  # #2 freshness gate


def test_build_statement_extractions_audit_captures_full_parse():
    recs = [{"merchant_id": "M1", "deal_id": "D1", "statement_text": "stmt",
             "source_ref": "salesforce.contentversion:068X", "as_of_date": FRESH}]
    result = build_statement_extractions(recs, RUN, _fake(_reply(_TWO_POS, 42000, 28)))
    assert len(result["rows"]) == 3          # the 3 grounded extraction rows
    assert len(result["audit"]) == 1         # one audit row per statement
    a = result["audit"][0]
    assert a["position_count"] == 1 and a["deposits_operating_total"] == 42000.0 and a["period_days"] == 28
    assert '"funder": "Funder B"' in a["positions_json"]   # full per-position breakdown persisted
    assert a["source_ref"] == "salesforce.contentversion:068X@" + FRESH and a["fresh"] is True
    # build_statement_rows stays a thin wrapper returning just the rows
    assert build_statement_rows(recs, RUN, _fake(_reply(_TWO_POS, 42000, 28))) == result["rows"]


def test_none_revenue_never_applied():
    recs = [{"merchant_id": "M1", "deal_id": "D1", "statement_text": "stmt",
             "source_ref": "ref", "as_of_date": FRESH}]
    by = _rows_by_type(build_statement_rows(recs, RUN, _fake(_reply(_TWO_POS, None, None))))
    assert by[C.ExtractionType.EST_WEEKLY_REVENUE]["value"] is None
    assert by[C.ExtractionType.EST_WEEKLY_REVENUE]["review_status"] == C.ReviewStatus.REVIEW
    # positions are still known + applied even when revenue is unknown
    assert by[C.ExtractionType.CONCURRENT_POSITIONS]["review_status"] == C.ReviewStatus.APPLIED
