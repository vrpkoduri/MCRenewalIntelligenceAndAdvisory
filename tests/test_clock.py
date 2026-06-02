"""Tier-1 tests for the Amortization Clock — Appendix A (S2).

Pure functions only (no Spark): amortization (A.2), calendar (A.3/A.4),
closure (A.5b), rollup (A.5). Plus the four validation merchants computed
end-to-end against their hand-worked `expected_clock`, and field-map/no-surface
invariants so the clock layer can never silently drift from the contract or
leak a frozen SF snapshot.

THE core principle under test (CLAUDE.md 2.1 / A.0): every time-dependent value
is recomputed from static terms + today's date; SF's stored Remaining Balance /
Percentage Paid / Estimated Renewal Date are NEVER inputs.
"""

from __future__ import annotations

from datetime import date

import pytest

from common import constants as C
from common.clock import (
    amount_paid,
    active_position_cnt,
    burden_ratio,
    business_days_between,
    closure_status,
    elapsed_payments,
    eligible_date,
    est_current_balance,
    est_paydown_pct,
    has_default_note,
    is_active,
    merchant_rollup,
    nth_business_day_after,
    payments_to_threshold,
    rtr,
    select_primary_position,
    tenure_days,
    total_weekly_debit,
    weakest_balance_source,
    weekly_debit,
)
from common.field_maps import (
    DEAL_CLOCK_MAP,
    GOLD_DEAL_CLOCK_DQ_COLUMNS,
    GOLD_MERCHANT_CLOCK_DQ_COLUMNS,
    MERCHANT_CLOCK_MAP,
    deal_clock_columns,
    merchant_clock_columns,
)
from common.io.guards import offending_surface_columns
from tests.fixtures.validation_merchants import (
    ALL_MERCHANTS,
    CLOCK_RUN_DATE,
    ONE_BIG_PROMOTION,
    STARR_WINDOW_TINTING,
    TOM_SNELL,
    WOLF_CORPORATION,
)

pytestmark = pytest.mark.filterwarnings("ignore")

_THRESHOLD = C.Thresholds.DEFAULT_RENEWAL_PAYDOWN  # 0.55 (D-205 single source)


# =========================================================================
# A.2 — amortization arithmetic
# =========================================================================


def test_rtr_is_funded_times_factor():
    assert rtr(25000.0, 1.49) == pytest.approx(37250.0)


def test_rtr_none_when_any_input_missing():
    assert rtr(None, 1.49) is None
    assert rtr(25000.0, None) is None


def test_amount_paid_is_payment_times_elapsed():
    assert amount_paid(372.5, 100) == pytest.approx(37250.0)
    assert amount_paid(195.0, 0) == pytest.approx(0.0)


def test_amount_paid_none_when_input_missing():
    assert amount_paid(None, 10) is None
    assert amount_paid(100.0, None) is None


def test_est_current_balance_floored_at_zero():
    # Never negative even if payments round past RTR at payoff (A.2 floor).
    assert est_current_balance(37250.0, 37250.0) == pytest.approx(0.0)
    assert est_current_balance(37250.0, 40000.0) == pytest.approx(0.0)
    assert est_current_balance(19500.0, 6240.0) == pytest.approx(13260.0)


def test_est_paydown_pct_capped_at_one():
    # 10100/10000 -> 1.0, never >100% (A.2 cap, paydown twin of the balance floor).
    assert est_paydown_pct(10100.0, 10000.0) == pytest.approx(1.0)
    assert est_paydown_pct(6240.0, 19500.0) == pytest.approx(0.32)


def test_est_paydown_pct_none_when_rtr_missing_or_zero():
    assert est_paydown_pct(100.0, None) is None
    assert est_paydown_pct(100.0, 0.0) is None


# =========================================================================
# A.3 — elapsed-payment counting (business-day / weekly, term-capped)
# =========================================================================


def test_business_days_excludes_funding_day_includes_today():
    # (Mon 2026-05-04, Fri 2026-05-08]: Tue..Fri = 4 business days; funding Mon excluded.
    assert business_days_between(date(2026, 5, 4), date(2026, 5, 8)) == 4


def test_business_days_skip_weekend():
    # Fri 2026-05-01 -> Mon 2026-05-04: only Mon counts (Sat/Sun skipped).
    assert business_days_between(date(2026, 5, 1), date(2026, 5, 4)) == 1


def test_business_days_zero_when_end_not_after_start():
    assert business_days_between(date(2026, 5, 8), date(2026, 5, 8)) == 0
    assert business_days_between(date(2026, 5, 8), date(2026, 5, 4)) == 0


def test_business_days_honor_holidays():
    # Same Tue..Fri window, but Thu 2026-05-07 marked holiday -> 3 not 4.
    assert (
        business_days_between(date(2026, 5, 4), date(2026, 5, 8), holidays=[date(2026, 5, 7)])
        == 3
    )


def test_elapsed_day_one_is_zero():
    # On the funding day itself nothing has been paid yet (A.0 checkpoint).
    assert elapsed_payments(date(2026, 5, 1), date(2026, 5, 1), C.PaymentFrequency.DAILY, 100) == 0


def test_elapsed_daily_capped_at_term():
    # Years elapsed -> raw business days >> 100, capped at num_payments.
    assert (
        elapsed_payments(date(2020, 1, 1), date(2026, 5, 31), C.PaymentFrequency.DAILY, 100) == 100
    )


def test_elapsed_weekly_is_whole_weeks():
    # 20 calendar days -> 2 whole weeks (floor), capped at term.
    assert (
        elapsed_payments(date(2026, 5, 1), date(2026, 5, 21), C.PaymentFrequency.WEEKLY, 100) == 2
    )


def test_elapsed_floored_at_zero_before_funding():
    assert elapsed_payments(date(2026, 5, 10), date(2026, 5, 1), C.PaymentFrequency.DAILY, 100) == 0


def test_elapsed_none_when_inputs_missing_or_unknown_freq():
    assert elapsed_payments(None, date(2026, 5, 1), C.PaymentFrequency.DAILY, 100) is None
    assert elapsed_payments(date(2026, 5, 1), date(2026, 5, 10), "Monthly", 100) is None


# =========================================================================
# A.4 — eligible-date inverse-solve
# =========================================================================


def test_payments_to_threshold_ceils_and_caps():
    # ceil(0.55 * 37250 / 372.5) = ceil(55.0) = 55, within the 100-payment term.
    assert payments_to_threshold(37250.0, 372.5, _THRESHOLD, 100) == 55


def test_payments_to_threshold_none_when_payment_nonpositive():
    assert payments_to_threshold(37250.0, 0.0, _THRESHOLD, 100) is None


def test_nth_business_day_after_is_inverse_of_count():
    f = date(2023, 6, 1)
    d = nth_business_day_after(f, 55)
    assert business_days_between(f, d) == 55


def test_eligible_date_daily_matches_nth_business_day():
    f = date(2023, 6, 1)
    assert eligible_date(f, C.PaymentFrequency.DAILY, 100, 372.5, 37250.0, _THRESHOLD) == (
        nth_business_day_after(f, 55)
    )


def test_eligible_date_weekly_adds_calendar_weeks():
    f = date(2026, 1, 5)
    # ceil(0.55*10000/100)=55 weeks weekly -> f + 55 weeks.
    got = eligible_date(f, C.PaymentFrequency.WEEKLY, 100, 100.0, 10000.0, _THRESHOLD)
    assert got == date(2026, 1, 5).fromordinal(f.toordinal() + 55 * 7)


def test_eligible_date_none_when_terms_missing():
    assert eligible_date(None, C.PaymentFrequency.DAILY, 100, 372.5, 37250.0, _THRESHOLD) is None
    assert eligible_date(date(2026, 1, 1), "Monthly", 100, 100.0, 10000.0, _THRESHOLD) is None


# =========================================================================
# A.5b — closure (default-note dominates paydown)
# =========================================================================


@pytest.mark.parametrize(
    "notes",
    ["Defaulted — $250 clawback", "charge-off", "NSF returned", "Bankruptcy filed", "WRITE-OFF"],
)
def test_has_default_note_detects_keywords_case_insensitive(notes):
    assert has_default_note(notes) is True


@pytest.mark.parametrize("notes", ["Paid in full", "Clean, full docs", "", None])
def test_has_default_note_false_for_clean_notes(notes):
    assert has_default_note(notes) is False


def test_closure_default_note_dominates_full_paydown():
    # The Starr invariant: 100% paydown + default note -> closed_default, NEVER closed_clean.
    assert closure_status(1.0, True) == C.ClosureStatus.CLOSED_DEFAULT


def test_closure_clean_when_paid_off_and_no_default():
    assert closure_status(1.0, False) == C.ClosureStatus.CLOSED_CLEAN


def test_closure_active_when_partial_paydown():
    s = closure_status(0.32, False)
    assert s == C.ClosureStatus.ACTIVE
    assert is_active(s) is True


def test_closure_null_paydown_no_default_is_active_not_closed():
    # Clock could not compute (missing terms): not paid off, not defaulted -> active.
    assert closure_status(None, False) == C.ClosureStatus.ACTIVE


# =========================================================================
# A.5 — merchant roll-up
# =========================================================================


def test_weekly_debit_daily_scaled_by_business_days():
    assert weekly_debit(100.0, C.PaymentFrequency.DAILY) == pytest.approx(500.0)


def test_weekly_debit_weekly_as_is():
    assert weekly_debit(560.0, C.PaymentFrequency.WEEKLY) == pytest.approx(560.0)


def test_weekly_debit_none_when_missing_or_unknown():
    assert weekly_debit(None, C.PaymentFrequency.DAILY) is None
    assert weekly_debit(100.0, "Monthly") is None


def test_active_position_cnt_counts_only_active():
    closures = [
        C.ClosureStatus.ACTIVE,
        C.ClosureStatus.ACTIVE,
        C.ClosureStatus.CLOSED_CLEAN,
        C.ClosureStatus.CLOSED_DEFAULT,
    ]
    assert active_position_cnt(closures) == 2


def test_total_weekly_debit_sums_active_positions():
    active = [
        {"payment_amount": 100.0, "payment_frequency": C.PaymentFrequency.DAILY},  # 500
        {"payment_amount": 560.0, "payment_frequency": C.PaymentFrequency.WEEKLY},  # 560
    ]
    assert total_weekly_debit(active) == pytest.approx(1060.0)


def test_burden_ratio_none_when_revenue_missing_or_zero():
    # CLAUDE.md 2.5: missing-revenue burden is UNKNOWN (null), never 0.
    assert burden_ratio(500.0, None) is None
    assert burden_ratio(500.0, 0.0) is None
    assert burden_ratio(500.0, 2000.0) == pytest.approx(0.25)


def test_weakest_balance_source_any_estimated_wins():
    assert (
        weakest_balance_source([C.BalanceSource.ACTUAL, C.BalanceSource.ESTIMATED])
        == C.BalanceSource.ESTIMATED
    )
    assert (
        weakest_balance_source([C.BalanceSource.ACTUAL, C.BalanceSource.ACTUAL])
        == C.BalanceSource.ACTUAL
    )
    # Empty -> conservative book default in v1.
    assert weakest_balance_source([]) == C.BalanceSource.ESTIMATED


def test_tenure_days_none_when_first_funded_in_future_or_missing():
    assert tenure_days(date(2026, 5, 1), CLOCK_RUN_DATE) == 30
    assert tenure_days(None, CLOCK_RUN_DATE) is None
    assert tenure_days(date(2026, 12, 1), CLOCK_RUN_DATE) is None


def test_select_primary_position_is_most_recent_active():
    positions = [
        {"deal_id": "A", "funded_date": date(2025, 1, 1), "funded_amount": 10000.0},
        {"deal_id": "B", "funded_date": date(2026, 5, 1), "funded_amount": 40000.0},
    ]
    assert select_primary_position(positions)["deal_id"] == "B"
    assert select_primary_position([]) is None


def test_merchant_rollup_two_active_positions():
    positions = [
        {
            "deal_id": "A",
            "closure_status": C.ClosureStatus.ACTIVE,
            "payment_amount": 100.0,
            "payment_frequency": C.PaymentFrequency.DAILY,
            "est_current_balance": 5000.0,
            "est_paydown_pct": 0.20,
            "est_renewal_eligible_date": date(2026, 9, 1),
            "balance_source": C.BalanceSource.ESTIMATED,
            "funded_date": date(2026, 1, 1),
            "funded_amount": 10000.0,
        },
        {
            "deal_id": "B",
            "closure_status": C.ClosureStatus.ACTIVE,
            "payment_amount": 560.0,
            "payment_frequency": C.PaymentFrequency.WEEKLY,
            "est_current_balance": 8000.0,
            "est_paydown_pct": 0.60,
            "est_renewal_eligible_date": date(2026, 7, 1),
            "balance_source": C.BalanceSource.ESTIMATED,
            "funded_date": date(2026, 5, 1),  # most recent -> primary
            "funded_amount": 40000.0,
        },
    ]
    roll = merchant_rollup(positions, date(2026, 1, 1), CLOCK_RUN_DATE)
    assert roll["active_position_cnt"] == 2
    assert roll["total_weekly_debit"] == pytest.approx(500.0 + 560.0)
    assert roll["est_current_balance"] == pytest.approx(13000.0)
    # Primary (most-recent, B) drives eligibility.
    assert roll["est_paydown_pct"] == pytest.approx(0.60)
    assert roll["est_renewal_eligible_date"] == date(2026, 7, 1)
    assert roll["is_eligible_now"] is True  # 0.60 >= 0.55
    assert roll["balance_source"] == C.BalanceSource.ESTIMATED
    assert roll["est_weekly_revenue"] is None
    assert roll["burden_ratio"] is None  # no revenue in v1


def test_merchant_rollup_excludes_closed_positions_from_burden():
    positions = [
        {
            "deal_id": "A",
            "closure_status": C.ClosureStatus.CLOSED_DEFAULT,
            "payment_amount": 100.0,
            "payment_frequency": C.PaymentFrequency.DAILY,
            "est_current_balance": 0.0,
            "est_paydown_pct": 1.0,
            "balance_source": C.BalanceSource.ESTIMATED,
            "funded_date": date(2023, 1, 1),
            "funded_amount": 10000.0,
        }
    ]
    roll = merchant_rollup(positions, date(2023, 1, 1), CLOCK_RUN_DATE)
    assert roll["active_position_cnt"] == 0
    assert roll["total_weekly_debit"] == pytest.approx(0.0)
    assert roll["est_paydown_pct"] is None  # no active primary
    assert roll["is_eligible_now"] is False


# =========================================================================
# Four validation merchants — end-to-end clock vs hand-worked expectations
# =========================================================================


def _compute_clock(m: dict) -> dict:
    """Compose the per-deal clock exactly as transform/gold_clock does, from a fixture's
    static terms at CLOCK_RUN_DATE. Mirrors the pure-function pipeline under test."""
    r = rtr(m["funded_amount"], m["factor_rate"])
    elapsed = elapsed_payments(
        m["funded_date"], CLOCK_RUN_DATE, m["payment_frequency"], m["num_payments"]
    )
    paid = amount_paid(m["payment_amount"], elapsed)
    bal = est_current_balance(r, paid)
    paydown = est_paydown_pct(paid, r)
    default_note = has_default_note(m["notes"])
    return {
        "rtr": r,
        "elapsed_payments": elapsed,
        "amount_paid": paid,
        "est_current_balance": bal,
        "est_paydown_pct": paydown,
        "has_default_note": default_note,
        "closure_status": closure_status(paydown, default_note),
        "est_renewal_eligible_date": eligible_date(
            m["funded_date"],
            m["payment_frequency"],
            m["num_payments"],
            m["payment_amount"],
            r,
            _THRESHOLD,
        ),
        "is_eligible_now": (paydown is not None and float(paydown) >= _THRESHOLD),
    }


@pytest.mark.parametrize(
    "merchant", ALL_MERCHANTS, ids=[m["opportunity_name"] for m in ALL_MERCHANTS]
)
def test_validation_merchant_clock_matches_expected(merchant):
    got = _compute_clock(merchant)
    exp = merchant["expected_clock"]
    for key, want in exp.items():
        actual = got[key]
        if isinstance(want, float):
            assert actual == pytest.approx(want), f"{merchant['opportunity_name']} {key}"
        else:
            assert actual == want, f"{merchant['opportunity_name']} {key}: {actual!r} != {want!r}"


def test_starr_defaulted_but_full_paydown_is_closed_default():
    """The critical S2 case: Starr computes 100% paydown but the default note must make
    closure closed_default, never closed_clean (A.5b)."""
    got = _compute_clock(STARR_WINDOW_TINTING)
    assert got["est_paydown_pct"] == pytest.approx(1.0)
    assert got["closure_status"] == C.ClosureStatus.CLOSED_DEFAULT


def test_one_big_promotion_full_paydown_is_closed_clean():
    got = _compute_clock(ONE_BIG_PROMOTION)
    assert got["est_paydown_pct"] == pytest.approx(1.0)
    assert got["closure_status"] == C.ClosureStatus.CLOSED_CLEAN


def test_snell_and_wolf_are_active_low_paydown():
    for m in (TOM_SNELL, WOLF_CORPORATION):
        got = _compute_clock(m)
        assert got["closure_status"] == C.ClosureStatus.ACTIVE
        assert got["est_paydown_pct"] < _THRESHOLD
        assert got["is_eligible_now"] is False


# =========================================================================
# Field-map / schema / no-surface invariants
# =========================================================================


def test_clock_column_helpers_have_no_duplicates():
    for cols in (deal_clock_columns(), merchant_clock_columns()):
        assert len(cols) == len(set(cols))


def test_deal_clock_pk_is_deal_id_plus_run_date():
    names = [fs.silver_col for fs in DEAL_CLOCK_MAP]
    assert names[0] == "deal_id"
    assert "clock_run_date" in names


def test_merchant_clock_pk_is_merchant_id_plus_run_date():
    names = [fs.silver_col for fs in MERCHANT_CLOCK_MAP]
    assert names[0] == "merchant_id"
    assert "clock_run_date" in names


def test_clock_tables_never_surface_sf_stored_columns():
    """The whole point of S2: recompute, never echo SF's frozen snapshots (CLAUDE.md 2.1)."""
    for cols in (deal_clock_columns(), merchant_clock_columns()):
        assert offending_surface_columns(cols) == []
        assert not any(c.startswith(C.SF_STORED_PREFIX) for c in cols)


def test_clock_dq_flags_present_for_missing_inputs():
    deal_dq = {name for name, _ in GOLD_DEAL_CLOCK_DQ_COLUMNS}
    merch_dq = {name for name, _ in GOLD_MERCHANT_CLOCK_DQ_COLUMNS}
    assert "clock_inputs_missing" in deal_dq
    assert "rtr_checkpoint_delta" in deal_dq
    assert "est_weekly_revenue_is_missing" in merch_dq
    assert "burden_ratio_is_missing" in merch_dq


def test_merchant_clock_fields_are_in_the_contract():
    """Every per-merchant clock field name must exist in the contract Merchant Gold Table
    (the 'Position & burden' + Identity sections) so the layer cannot drift from the spec.
    `is_eligible_now` is an MRI convenience column (documented), so it is exempted."""
    from common import contract

    contract_fields = set(contract.merchant_gold_fields())
    exempt = {"is_eligible_now", "clock_run_date"}
    for fs in MERCHANT_CLOCK_MAP:
        if fs.silver_col in exempt:
            continue
        assert fs.silver_col in contract_fields, f"{fs.silver_col} not in Merchant Gold contract"
