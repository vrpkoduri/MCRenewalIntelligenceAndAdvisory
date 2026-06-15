"""Tier-1 tests for the Statement Analyst's deterministic position/burden counter
(`common/agents/positions.py`, Framework §5.9 / S7 Phase 2 / C-025).

The agent EXTRACTS classified statement streams; these tests pin the DETERMINISTIC math the spine
relies on: weekly normalization (reusing the S2 clock for Daily/Weekly — Rule 3), the true
concurrent-position count (other funders the spine can't see), the total weekly burden, and the
honesty rules (missing → contribute nothing / revenue stays None, never a fabricated 0).
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.agents.positions import (
    concurrent_position_count,
    est_weekly_revenue,
    is_morgan_cash,
    normalize_to_weekly,
    statement_is_fresh,
    summarize_statement,
    total_weekly_debit,
)


def test_statement_is_fresh_window():
    run = date(2026, 6, 9)
    assert statement_is_fresh("2026-05-01", run) is True              # 39 days — within window
    assert statement_is_fresh("2025-01-01", run) is False             # > STATEMENT_FRESHNESS_MAX_DAYS
    assert statement_is_fresh("2026-09-01", run) is False             # future statement → not fresh
    assert statement_is_fresh(None, run) is False                     # unknown date → don't assume current
    assert statement_is_fresh("garbage", run) is False                # unparseable → not fresh


def test_normalize_to_weekly_reuses_clock_and_handles_extra_frequencies():
    assert normalize_to_weekly(100, "Daily") == 100 * C.Thresholds.BUSINESS_DAYS_PER_WEEK  # 500
    assert normalize_to_weekly(500, "Weekly") == 500.0
    assert normalize_to_weekly(1000, "Biweekly") == 500.0
    assert normalize_to_weekly(1000, "bi-weekly") == 500.0  # token variants
    assert normalize_to_weekly(433, "Monthly") == 433 / C.Thresholds.WEEKS_PER_MONTH  # ≈100


def test_normalize_to_weekly_missing_is_none_never_fabricated():
    assert normalize_to_weekly(None, "Weekly") is None
    assert normalize_to_weekly(100, None) is None
    assert normalize_to_weekly(100, "Quarterly") is None  # unrecognized cadence → unknown


def test_concurrent_position_count_excludes_morgan_cash_by_default():
    # Wolf-flavored: the merchant's own MC advance + two OTHER funders the spine can't see.
    positions = [
        {"funder": "Morgan Cash", "is_morgan_cash": True, "payment_amount": 500, "payment_frequency": "Weekly"},
        {"funder": "Funder B", "payment_amount": 300, "payment_frequency": "Daily"},
        {"funder": "Funder C", "payment_amount": 200, "payment_frequency": "Weekly"},
    ]
    assert concurrent_position_count(positions) == 2  # other-funder count (the additive signal, D-710)
    assert concurrent_position_count(positions, include_morgan_cash=True) == 3


def test_concurrent_position_count_dedupes_funder_and_counts_unlabeled_distinctly():
    positions = [
        {"funder": "ABC Capital"}, {"funder": "abc capital "},  # same funder, normalized → 1
        {"funder": None}, {},  # two unlabeled streams → can't collapse → 2
    ]
    assert concurrent_position_count(positions) == 3
    assert concurrent_position_count([]) == 0


def test_is_morgan_cash_defaults_false():
    assert is_morgan_cash({"funder": "X"}) is False  # unflagged → treated as external (burden-revealing)
    assert is_morgan_cash({"is_morgan_cash": True}) is True


def test_total_weekly_debit_sums_and_skips_unsizable():
    positions = [
        {"funder": "MC", "is_morgan_cash": True, "payment_amount": 500, "payment_frequency": "Weekly"},
        {"funder": "B", "payment_amount": 300, "payment_frequency": "Daily"},   # → 1500
        {"funder": "C", "payment_amount": 200, "payment_frequency": "Weekly"},  # → 200
        {"funder": "D", "payment_amount": None, "payment_frequency": "Weekly"},  # unsizable → 0 contrib
    ]
    assert total_weekly_debit(positions) == 500 + 1500 + 200  # MC included (total obligation)
    assert total_weekly_debit(positions, include_morgan_cash=False) == 1500 + 200


def test_est_weekly_revenue_normalizes_period_and_is_honest_about_missing():
    assert est_weekly_revenue(40000, 28) == 10000.0  # 4 weeks
    assert est_weekly_revenue(None, 28) is None
    assert est_weekly_revenue(40000, None) is None
    assert est_weekly_revenue(40000, 0) is None  # non-positive period → unknown, not div-by-zero


def test_summarize_statement_composes_the_three_signals():
    positions = [
        {"funder": "Morgan Cash", "is_morgan_cash": True, "payment_amount": 500, "payment_frequency": "Weekly"},
        {"funder": "Funder B", "payment_amount": 300, "payment_frequency": "Daily"},
        {"funder": "Funder C", "payment_amount": 200, "payment_frequency": "Weekly"},
    ]
    out = summarize_statement(positions, deposits_total=42000, period_days=28)
    assert out["concurrent_positions"] == 2          # other funders (clock ADDS to its own count)
    assert out["total_weekly_debit"] == 500 + 1500 + 200
    assert out["est_weekly_revenue"] == 10500.0      # 42000 / 4 weeks


def test_summarize_statement_empty_and_no_deposits():
    out = summarize_statement([], deposits_total=None, period_days=None)
    assert out["concurrent_positions"] == 0
    assert out["total_weekly_debit"] == 0.0
    assert out["est_weekly_revenue"] is None  # never fabricated
