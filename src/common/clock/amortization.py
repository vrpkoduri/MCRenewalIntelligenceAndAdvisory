"""Pure amortization arithmetic — Appendix A.2 (S2).

An MCA is NOT an interest-accruing loan; it is a fixed total repayment (RTR) chipped
away by fixed payments. That makes the balance deterministic given the static terms and
an elapsed-payment count (see `calendar.elapsed_payments`).

Pure scalar functions — no Spark, no I/O — so they run in tier-1 local tests AND inside
Spark UDFs in `transform/gold_clock.py` (mirrors `common/identity` normalizers).

THE core principle (CLAUDE.md 2.1 / Appendix A.0): these recompute from static terms +
today's date. Salesforce's stored Remaining Balance / Percentage Paid are frozen
funding-moment snapshots and are NEVER an input here.
"""

from __future__ import annotations

Number = float | int | None


def rtr(funded_amount: Number, factor_rate: Number) -> float | None:
    """RTR (total owed, never changes) = funded_amount × factor_rate (A.2).

    None if either input is missing (never fabricate a balance from partial terms).
    """
    if funded_amount is None or factor_rate is None:
        return None
    return float(funded_amount) * float(factor_rate)


def amount_paid(payment_amount: Number, elapsed_payments: Number) -> float | None:
    """amount_paid = payment_amount × elapsed_payments (A.2).

    `elapsed_payments` is the term-capped count from `calendar.elapsed_payments`, so this
    never exceeds the scheduled payback. None if either input is missing.
    """
    if payment_amount is None or elapsed_payments is None:
        return None
    return float(payment_amount) * float(elapsed_payments)


def est_current_balance(rtr_value: Number, amount_paid_value: Number) -> float | None:
    """est_current_balance = max(0, RTR − amount_paid) (A.2).

    Floored at 0: once payments cover the RTR the balance is paid off, never negative.
    None if either input is missing.
    """
    if rtr_value is None or amount_paid_value is None:
        return None
    return max(0.0, float(rtr_value) - float(amount_paid_value))


def est_paydown_pct(amount_paid_value: Number, rtr_value: Number) -> float | None:
    """est_paydown_pct = amount_paid ÷ RTR (A.2), capped at 1.0.

    The cap is the paydown-side twin of the balance floor: with `elapsed_payments` capped
    at the term, amount_paid can only round slightly past RTR at full payoff, which is
    still "100% paid", never >100% (keeps the [0,1] contract). None if RTR missing/zero.
    """
    if amount_paid_value is None or rtr_value is None:
        return None
    r = float(rtr_value)
    if r == 0.0:
        return None
    return min(1.0, float(amount_paid_value) / r)
