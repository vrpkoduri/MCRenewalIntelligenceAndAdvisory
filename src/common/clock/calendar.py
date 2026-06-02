"""Elapsed-payment counting (A.3) and eligible-date inverse-solve (A.4) — S2.

The estimated path (D-203/C-016 — no servicing feed): count scheduled payment periods
from `funded_date` to today, capped at the deal's full term so it never overcounts past
payoff. Two frequencies (A.3):
  - daily  → business days only (M–F, minus holidays). D-204 v1 = plain M–F (no holidays);
             a `holidays` set is threaded through so the federal-holiday upgrade is a
             one-line constant change.
  - weekly → whole calendar weeks elapsed.

Counting convention: payments accrue AFTER funding, so on the funding day itself
elapsed = 0 (day-one checkpoint, A.0). i.e. the daily count is over (funded_date, today]
and the weekly count is floor(days / 7).

Pure functions — no Spark, no I/O. Calendar math uses only `datetime.date`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import ceil

from common import constants as C

_FREQ_DAILY = C.PaymentFrequency.DAILY
_FREQ_WEEKLY = C.PaymentFrequency.WEEKLY


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _holiday_set(holidays) -> frozenset[date]:
    """Normalize a holiday iterable (date or 'YYYY-MM-DD' strings) to a date set.
    None -> the configured default (plain M–F for v1, i.e. empty)."""
    if holidays is None:
        holidays = C.DEFAULT_HOLIDAYS
    out = set()
    for h in holidays:
        d = _as_date(h)
        if d is not None:
            out.add(d)
    return frozenset(out)


def _weekdays_inclusive(a: date, b: date) -> int:
    """Count of M–F days in the inclusive range [a, b] (no holiday logic). 0 if b < a."""
    if b < a:
        return 0
    total = (b - a).days + 1
    full_weeks, extra = divmod(total, 7)
    count = full_weeks * 5
    start_wd = a.weekday()  # Mon=0 … Sun=6
    for i in range(extra):
        if (start_wd + i) % 7 < 5:
            count += 1
    return count


def business_days_between(start_exclusive, end_inclusive, holidays=None) -> int:
    """Number of business days (M–F minus holidays) in (start, end].

    Funding day excluded; today included. 0 when end <= start.
    """
    start = _as_date(start_exclusive)
    end = _as_date(end_inclusive)
    if start is None or end is None or end <= start:
        return 0
    first = start + timedelta(days=1)
    count = _weekdays_inclusive(first, end)
    for h in _holiday_set(holidays):
        if first <= h <= end and h.weekday() < 5:
            count -= 1
    return max(0, count)


def nth_business_day_after(start, n: int, holidays=None) -> date | None:
    """The date of the n-th business day strictly after `start` (inverse of
    `business_days_between`). n <= 0 returns `start`. Holidays/weekends skipped.

    Bounded loop (n is capped at the deal term upstream), so it stays cheap in a UDF.
    """
    d = _as_date(start)
    if d is None:
        return None
    if n <= 0:
        return d
    hol = _holiday_set(holidays)
    counted = 0
    while counted < n:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in hol:
            counted += 1
    return d


def elapsed_payments(funded_date, today, frequency, num_payments, holidays=None) -> int | None:
    """Scheduled payments elapsed from funding to `today`, capped at the term (A.3).

    daily  → business days in (funded_date, today] (M–F minus holidays).
    weekly → whole calendar weeks since funding = (today − funded_date).days // 7.
    Floored at 0 (today before funding) and capped at `num_payments` (never past payoff).
    None when the inputs needed to count/cap are missing (never guess).
    """
    f = _as_date(funded_date)
    t = _as_date(today)
    if f is None or t is None or num_payments is None or frequency is None:
        return None
    if frequency == _FREQ_DAILY:
        raw = business_days_between(f, t, holidays)
    elif frequency == _FREQ_WEEKLY:
        raw = max(0, (t - f).days // 7)
    else:
        return None
    return max(0, min(int(num_payments), raw))


def payments_to_threshold(rtr_value, payment_amount, threshold, num_payments) -> int | None:
    """Smallest payment number n where paydown (payment×n ÷ RTR) ≥ threshold (A.4).

    n = ceil(threshold × RTR ÷ payment), capped at the term. None if inputs missing/zero.
    """
    if rtr_value is None or payment_amount is None or threshold is None or num_payments is None:
        return None
    p = float(payment_amount)
    if p <= 0:
        return None
    n = ceil(float(threshold) * float(rtr_value) / p)
    n = max(0, min(int(num_payments), n))
    return n


def eligible_date(
    funded_date,
    frequency,
    num_payments,
    payment_amount,
    rtr_value,
    threshold,
    holidays=None,
) -> date | None:
    """est_renewal_eligible_date (A.4): solve for the payment number at which paydown
    crosses `threshold`, then map it back to a calendar date.

    daily  → the n-th business day after funding.
    weekly → funding + n calendar weeks.
    None when terms are missing or the frequency is unknown (never fabricate a date).
    """
    f = _as_date(funded_date)
    n = payments_to_threshold(rtr_value, payment_amount, threshold, num_payments)
    if f is None or n is None or frequency is None:
        return None
    if frequency == _FREQ_DAILY:
        return nth_business_day_after(f, n, holidays)
    if frequency == _FREQ_WEEKLY:
        return f + timedelta(weeks=n)
    return None
