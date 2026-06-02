"""Amortization clock — Appendix A (S2). THE core principle (CLAUDE.md 2.1): recompute
everything time-dependent daily from the static terms + today's date; NEVER trust SF's
frozen Remaining Balance / Percentage Paid / Estimated Renewal Date.

Pure, Spark-free-at-import functions (mirrors `common/identity`) so they are tier-1
testable and reusable inside the Spark UDFs of `transform/gold_clock.py`:
  - amortization: rtr, amount_paid, est_current_balance (floored), est_paydown_pct (capped)
  - calendar:     elapsed_payments (daily business-day / weekly, term-capped); eligible_date
  - closure:      three-state closure_status (default-note dominates); has_default_note
  - rollup:       merchant roll-up (active count, weekly debit, burden, weakest source)
"""

from common.clock.amortization import (
    amount_paid,
    est_current_balance,
    est_paydown_pct,
    rtr,
)
from common.clock.calendar import (
    business_days_between,
    elapsed_payments,
    eligible_date,
    nth_business_day_after,
    payments_to_threshold,
)
from common.clock.closure import (
    closure_status,
    has_default_note,
    is_active,
)
from common.clock.rollup import (
    active_position_cnt,
    burden_ratio,
    merchant_rollup,
    select_primary_position,
    tenure_days,
    total_weekly_debit,
    weakest_balance_source,
    weekly_debit,
)

__all__ = [
    # amortization (A.2)
    "rtr",
    "amount_paid",
    "est_current_balance",
    "est_paydown_pct",
    # calendar (A.3 / A.4)
    "elapsed_payments",
    "eligible_date",
    "business_days_between",
    "nth_business_day_after",
    "payments_to_threshold",
    # closure (A.5b)
    "closure_status",
    "has_default_note",
    "is_active",
    # rollup (A.5)
    "active_position_cnt",
    "total_weekly_debit",
    "weekly_debit",
    "burden_ratio",
    "weakest_balance_source",
    "tenure_days",
    "select_primary_position",
    "merchant_rollup",
]
