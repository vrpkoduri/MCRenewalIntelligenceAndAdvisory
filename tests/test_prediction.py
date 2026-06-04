"""Tier-1 tests for Prediction feature/label derivation (Build Plan §6 / Framework §11.2, S6).

Pure functions only (no Spark, no ML): RFM features (D-601), survival duration/event labeling
with censoring (D-607), and the confidence / insufficient-history logic (D-603). The models
themselves (PyMC-Marketing + lifelines) are fit on Databricks (tier-2) — these tests pin the
inputs/labels the models consume + the honesty rules. Plus field-map / no-surface invariants.
"""

from __future__ import annotations

from datetime import date

import pytest

from common import constants as C
from common.field_maps import (
    GOLD_MERCHANT_PREDICTIONS_DQ_COLUMNS,
    MERCHANT_PREDICTIONS_MAP,
    merchant_predictions_columns,
)
from common.io.guards import offending_surface_columns
from common.prediction import (
    censored_duration,
    confidence_from_history,
    confidence_from_posterior,
    inter_advance_intervals,
    is_insufficient_history,
    prediction_confidence,
    rfm_features,
    survival_rows,
)

TODAY = date(2026, 6, 2)


def _deal(funded, amount=10000.0):
    return {"funded_date": funded, "funded_amount": amount}


# =============================================================================
# RFM features (D-601)
# =============================================================================


def test_rfm_single_deal_frequency_zero():
    f = rfm_features([_deal(date(2026, 4, 3), 15000.0)], TODAY)
    assert f["rfm_frequency"] == 0  # no repeat advances
    assert f["rfm_recency"] == 0  # last == first
    assert f["rfm_T"] == (TODAY - date(2026, 4, 3)).days
    assert f["rfm_monetary"] == 15000.0
    assert f["repeat_events"] == 0


def test_rfm_multi_deal():
    deals = [_deal(date(2025, 1, 1), 10000.0), _deal(date(2025, 4, 1), 20000.0), _deal(date(2025, 7, 1), 30000.0)]
    f = rfm_features(deals, TODAY)
    assert f["rfm_frequency"] == 2  # 3 advances → 2 repeats
    assert f["rfm_recency"] == (date(2025, 7, 1) - date(2025, 1, 1)).days
    assert f["rfm_T"] == (TODAY - date(2025, 1, 1)).days
    assert f["rfm_monetary"] == 20000.0  # (10k+20k+30k)/3


def test_rfm_none_when_no_dated_advances():
    assert rfm_features([], TODAY) is None
    assert rfm_features([{"funded_date": None, "funded_amount": 5000.0}], TODAY) is None


def test_rfm_missing_amount_not_faked():
    f = rfm_features([_deal(date(2025, 1, 1), None), _deal(date(2025, 6, 1), 20000.0)], TODAY)
    assert f["rfm_monetary"] == 20000.0  # the one known amount; missing dropped, not zeroed


# =============================================================================
# Survival labeling + censoring (D-607)
# =============================================================================


def test_intervals_and_censoring_multi_deal():
    deals = [_deal(date(2025, 1, 1)), _deal(date(2025, 3, 2)), _deal(date(2025, 5, 1))]
    gaps = inter_advance_intervals(deals)
    assert gaps == [(date(2025, 3, 2) - date(2025, 1, 1)).days, (date(2025, 5, 1) - date(2025, 3, 2)).days]
    rows = survival_rows(deals, TODAY)
    # two observed events + one censored tail
    assert [r["event_observed"] for r in rows] == [1, 1, 0]
    assert rows[-1]["duration"] == (TODAY - date(2025, 5, 1)).days


def test_single_deal_is_censored_not_dropped():
    """Framework §11.2: a not-yet-renewed merchant is CENSORED, never missing."""
    deals = [_deal(date(2026, 3, 1))]
    assert inter_advance_intervals(deals) == []
    rows = survival_rows(deals, TODAY)
    assert len(rows) == 1 and rows[0]["event_observed"] == 0
    assert rows[0]["duration"] == (TODAY - date(2026, 3, 1)).days


def test_censored_duration_none_when_empty():
    assert censored_duration([], TODAY) is None


# =============================================================================
# Confidence + insufficient history (D-603)
# =============================================================================


def test_insufficient_history_threshold():
    assert is_insufficient_history(0) is True  # single-deal → prior-only
    assert is_insufficient_history(1) is False
    assert is_insufficient_history(None) is True


def test_confidence_from_history_monotonic():
    c0, c1, c3 = confidence_from_history(0), confidence_from_history(1), confidence_from_history(3)
    assert c0 < c1 < c3
    assert c0 >= 0.05 and c3 <= 1.0  # floored, capped


def test_confidence_from_posterior_wider_is_lower():
    narrow = confidence_from_posterior(0.1)
    wide = confidence_from_posterior(5.0)
    assert narrow > wide
    assert confidence_from_posterior(None) is None


def test_prediction_confidence_prefers_posterior_else_history():
    # with a posterior width → uses it
    assert prediction_confidence(5, posterior_width=0.1) == pytest.approx(confidence_from_posterior(0.1))
    # without → history fallback (insufficient-history merchants)
    assert prediction_confidence(0, posterior_width=None) == pytest.approx(confidence_from_history(0))
    assert 0.0 <= prediction_confidence(0) <= 1.0


# =============================================================================
# Field-map / no-surface invariants
# =============================================================================

_KNOWN = {C.Verdict.HAVE, C.Verdict.CARRY, C.Verdict.DISTRUST, C.Verdict.DERIVE,
          C.Verdict.MUST_CAPTURE, C.Verdict.REUSE, C.Verdict.FUTURE}


def test_predictions_map_unique_known_verdicts_pk_order():
    cols = merchant_predictions_columns()
    assert len(cols) == len(set(cols))
    assert len(cols) == len(MERCHANT_PREDICTIONS_MAP) + len(GOLD_MERCHANT_PREDICTIONS_DQ_COLUMNS)
    assert MERCHANT_PREDICTIONS_MAP[0].silver_col == "merchant_id"
    assert MERCHANT_PREDICTIONS_MAP[1].silver_col == "prediction_run_date"
    for fs in MERCHANT_PREDICTIONS_MAP:
        assert fs.verdict in _KNOWN


def test_predictions_no_surface_and_dq_flag_and_model_version():
    assert offending_surface_columns(merchant_predictions_columns()) == []
    flags = {name for name, _ in GOLD_MERCHANT_PREDICTIONS_DQ_COLUMNS}
    assert "insufficient_history" in flags
    # model_version present for reproducibility/audit (Event Log contract)
    assert any(fs.silver_col == "model_version" for fs in MERCHANT_PREDICTIONS_MAP)


def test_prediction_event_type_and_config_present():
    assert C.EventType.PREDICTION in C.EventType.ALL
    assert C.INSUFFICIENT_HISTORY_MIN_EVENTS == 1
    assert C.CLV_HORIZON_MONTHS == 12 and 0 < C.CLV_DISCOUNT_RATE_ANNUAL < 1
    assert "burden_ratio" not in C.COX_COVARIATES  # null book-wide v1 (D-607)
