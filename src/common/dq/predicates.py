"""Pure-Python data-quality predicates — the canonical semantics of the S0 DQ rules.

These define the rules in plain Python so they are unit-testable with no Spark/Java
(tier-1). The Spark column implementations in dq/rules.py must mirror these exactly;
shared tier-2 tests on Databricks pin the Spark side.
"""

from datetime import date, datetime

from ..constants import DATE_SANITY_GAP_DAYS, RTR_TOLERANCE


def is_missing_implausible_zero(value) -> bool:
    """DQ rule 1: treat 0 and blank/None as MISSING where 0 is implausible
    (months_in_business, fico, revenue). Returns True when the value is missing.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def date_sanity_flag(funded_date, created_date, gap_days: int = DATE_SANITY_GAP_DAYS) -> bool:
    """DQ rule 2 (C-007): True (contradiction) when funded_date and created_date differ
    by more than `gap_days` in EITHER direction.

    This catches the real migration artifact (e.g. Funded 2020 / Created 2022, i.e.
    funded < created) that the earlier literal `funded > created` rule missed, while not
    flagging normal create->fund latency of a few days/weeks. Either null -> not a
    contradiction (nothing to compare). Callers mark flagged rows, never drop them.
    """
    f = _as_date(funded_date)
    c = _as_date(created_date)
    if f is None or c is None:
        return False
    return abs((f - c).days) > gap_days


def rtr_check_delta(funded_amount, factor_rate, payback_amount):
    """DQ rule 3: |funded_amount * factor_rate - payback_amount|. None if inputs missing."""
    if funded_amount is None or factor_rate is None or payback_amount is None:
        return None
    return abs(float(funded_amount) * float(factor_rate) - float(payback_amount))


def rtr_check_flag(funded_amount, factor_rate, payback_amount, tol: float = RTR_TOLERANCE) -> bool:
    """True when the RTR cross-check exceeds tolerance (diagnostic only)."""
    delta = rtr_check_delta(funded_amount, factor_rate, payback_amount)
    if delta is None:
        return False
    return delta > tol
