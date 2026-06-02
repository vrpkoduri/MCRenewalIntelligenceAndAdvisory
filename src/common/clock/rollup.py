"""Merchant roll-up helpers — Appendix A.5 (S2).

Sum balances/payments across a merchant's ACTIVE positions for total burden; the PRIMARY
(most-recent) active position's paydown drives the merchant's eligibility; `balance_source`
rolls up as the WEAKEST across positions (any estimated → merchant estimated).

These are the canonical semantics in pure Python (tier-1 testable); the Spark aggregation
in `transform/gold_clock.py` mirrors them exactly (the dq.predicates ↔ dq.rules pattern).
No Spark, no I/O.
"""

from __future__ import annotations

from datetime import date, datetime

from common import constants as C

_DAILY = C.PaymentFrequency.DAILY
_WEEKLY = C.PaymentFrequency.WEEKLY


def weekly_debit(payment_amount, frequency) -> float | None:
    """A single position's payment normalized to a weekly figure (A.5).

    daily  → payment × business days per week (5); weekly → payment as-is.
    None when payment or frequency is missing/unknown (never assume a debit).
    """
    if payment_amount is None or frequency is None:
        return None
    if frequency == _DAILY:
        return float(payment_amount) * C.Thresholds.BUSINESS_DAYS_PER_WEEK
    if frequency == _WEEKLY:
        return float(payment_amount)
    return None


def active_position_cnt(closures) -> int:
    """Count of positions whose computed closure_status is `active` (A.5 — itself an
    inference, hence carries the balance_source confidence flag)."""
    return sum(1 for c in closures if c == C.ClosureStatus.ACTIVE)


def total_weekly_debit(active_positions) -> float:
    """Σ weekly-normalized payments across active positions. Missing per-position debits
    contribute 0 (they are flagged elsewhere), never fabricated."""
    total = 0.0
    for pos in active_positions:
        wd = weekly_debit(pos.get("payment_amount"), pos.get("payment_frequency"))
        if wd is not None:
            total += wd
    return total


def burden_ratio(total_weekly_debit_value, est_weekly_revenue) -> float | None:
    """burden_ratio = total_weekly_debit ÷ est_weekly_revenue (A.5).

    None when revenue is missing or zero — never 0 (CLAUDE.md 2.5: a missing-revenue
    burden is unknown, not "no burden"). In v1 (no bank feed) revenue is null for the
    whole book, so burden_ratio is null + flagged everywhere.
    """
    if est_weekly_revenue is None:
        return None
    rev = float(est_weekly_revenue)
    if rev == 0.0:
        return None
    return float(total_weekly_debit_value) / rev


def weakest_balance_source(sources) -> str:
    """Weakest confidence across positions: any `estimated` → `estimated` (A.5).
    Empty (no positions) → `estimated` (the conservative book default in v1)."""
    srcs = list(sources)
    if any(s == C.BalanceSource.ESTIMATED for s in srcs):
        return C.BalanceSource.ESTIMATED
    if srcs and all(s == C.BalanceSource.ACTUAL for s in srcs):
        return C.BalanceSource.ACTUAL
    return C.BalanceSource.ESTIMATED


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def tenure_days(first_funded_date, run_date) -> int | None:
    """today − first_funded_date (A.5 / contract Identity section — time-dependent, so
    recomputed here, not in S1). None if first_funded_date missing or in the future."""
    f = _as_date(first_funded_date)
    r = _as_date(run_date)
    if f is None or r is None:
        return None
    diff = (r - f).days
    return diff if diff >= 0 else None


def select_primary_position(active_positions):
    """The merchant's PRIMARY active position whose paydown drives eligibility (A.5):
    the most-recently funded active position (tie-break: larger funded_amount, then
    deal_id). None when there are no active positions."""
    if not active_positions:
        return None

    def _key(pos):
        fd = _as_date(pos.get("funded_date")) or date.min
        amt = pos.get("funded_amount") or 0.0
        did = pos.get("deal_id") or ""
        return (fd, float(amt), did)

    return max(active_positions, key=_key)


def merchant_rollup(positions, first_funded_date, run_date, est_weekly_revenue=None) -> dict:
    """Compose the per-merchant clock fields from a list of this merchant's position
    dicts (A.5). Each position dict carries: closure_status, payment_amount,
    payment_frequency, est_current_balance, est_paydown_pct, est_renewal_eligible_date,
    balance_source, funded_date, funded_amount, deal_id.

    The Spark transform mirrors this logic via group-by aggregation.
    """
    active = [p for p in positions if p.get("closure_status") == C.ClosureStatus.ACTIVE]
    twd = total_weekly_debit(active)
    primary = select_primary_position(active)
    paydown = primary.get("est_paydown_pct") if primary else None
    threshold = C.Thresholds.DEFAULT_RENEWAL_PAYDOWN

    return {
        "active_position_cnt": active_position_cnt(p.get("closure_status") for p in positions),
        "total_weekly_debit": twd,
        "est_weekly_revenue": est_weekly_revenue,  # null in v1 (no feed)
        "burden_ratio": burden_ratio(twd, est_weekly_revenue),
        "est_current_balance": sum(
            float(p.get("est_current_balance") or 0.0) for p in active
        ),
        "est_paydown_pct": paydown,  # primary position drives eligibility
        "est_renewal_eligible_date": primary.get("est_renewal_eligible_date") if primary else None,
        "is_eligible_now": (paydown is not None and float(paydown) >= threshold),
        "balance_source": weakest_balance_source(p.get("balance_source") for p in positions),
        "tenure_days": tenure_days(first_funded_date, run_date),
        "first_funded_date": first_funded_date,
    }
