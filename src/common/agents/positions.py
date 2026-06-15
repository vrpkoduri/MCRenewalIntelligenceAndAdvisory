"""Statement Analyst — deterministic position/burden counter (Framework §5.9, S7 Phase 2).

The Statement Analyst agent (LLM) READS a bank statement and EXTRACTS classified transaction
streams — which recurring ACH debits are funder advances (and which funder), and the total
deposits over the statement period. This module is the DETERMINISTIC tool it calls: it COUNTS
the concurrent positions, SUMS the weekly debit burden, and normalizes deposits to a weekly
revenue figure. The agent extracts; this tool counts; the S2 clock then COMPUTES `burden_ratio`
from these inputs (it is never recomputed here). Pure — no Spark, no LLM at import.

Why this exists (FU-301): Salesforce only knows Morgan Cash's OWN advances, so the spine's
`active_position_cnt` and (null) `burden_ratio` understate a merchant's true obligations. A bank
statement shows EVERY funder's ACH debit — the concurrent positions and total weekly burden the
spine cannot see. Per D-710 the clock ADDS the other-funder positions to `active_position_cnt` and
fills `est_weekly_revenue` (→ a real `burden_ratio`); absent/low-confidence extractions degrade to
today's behavior.

Weekly normalization for Daily/Weekly REUSES the S2 clock (`clock.rollup.weekly_debit`) so there is
ONE source for that arithmetic (Rule 3); Biweekly/Monthly are added here against the same
`Thresholds` constants. Honesty (CLAUDE.md 2.5): a missing amount/frequency contributes nothing
rather than a fabricated 0 or guess; unknown revenue stays None (unknown), never 0.
"""

from __future__ import annotations

from datetime import date, datetime

from common import constants as C
from common.clock.rollup import weekly_debit as _clock_weekly_debit

# Frequency tokens the agent may report (free-text → normalized). Daily/Weekly delegate to the
# clock; Biweekly/Monthly are normalized here. Anything else → unknown (None, never assumed).
_DAILY, _WEEKLY, _BIWEEKLY, _MONTHLY = "daily", "weekly", "biweekly", "monthly"


def _norm_freq(frequency) -> str | None:
    if frequency is None:
        return None
    key = str(frequency).strip().lower().replace("-", "").replace(" ", "")
    if key in ("daily", "day"):
        return _DAILY
    if key in ("weekly", "week"):
        return _WEEKLY
    if key in ("biweekly", "everytwoweeks", "fortnightly"):
        return _BIWEEKLY
    if key in ("monthly", "month"):
        return _MONTHLY
    return None


def normalize_to_weekly(amount, frequency) -> float | None:
    """One stream's payment normalized to a weekly figure. Daily/Weekly reuse the S2 clock;
    Biweekly = amount/2; Monthly = amount / WEEKS_PER_MONTH (Appendix A.3 constant, reused).
    None when amount or frequency is missing/unrecognized — never assume a debit."""
    if amount is None:
        return None
    freq = _norm_freq(frequency)
    if freq is None:
        return None
    if freq == _DAILY:
        return _clock_weekly_debit(amount, C.PaymentFrequency.DAILY)
    if freq == _WEEKLY:
        return _clock_weekly_debit(amount, C.PaymentFrequency.WEEKLY)
    if freq == _BIWEEKLY:
        return float(amount) / 2.0
    if freq == _MONTHLY:
        return float(amount) / C.Thresholds.WEEKS_PER_MONTH
    return None


def _funder_key(position: dict) -> str | None:
    """A position's dedupe key: the normalized funder/originator label, or None if absent."""
    f = position.get("funder")
    if f is None or not str(f).strip():
        return None
    return str(f).strip().lower()


def is_morgan_cash(position: dict) -> bool:
    """The agent flags Morgan Cash's OWN debit (matched to the MC ACH descriptor) so this tool can
    separate it from the OTHER-funder positions the spine can't see. Defaults False (treat an
    unflagged stream as an external funder — the conservative, burden-revealing reading)."""
    return bool(position.get("is_morgan_cash", False))


def concurrent_position_count(positions, *, include_morgan_cash: bool = False) -> int:
    """Count DISTINCT funder ACH-debit streams on the statement (the true concurrent positions).

    By default EXCLUDES Morgan Cash's own debit — per D-710 the clock ADDS this other-funder count
    to its own `active_position_cnt`, so double-counting MC would inflate burden. Distinctness is by
    normalized `funder` label where present; a stream with no funder label counts as its own
    position (we never collapse two unknowns into one). `positions` is the agent's list of detected
    streams (it has already grouped recurring lines into streams)."""
    seen_funders: set[str] = set()
    unlabeled = 0
    for p in positions:
        if not include_morgan_cash and is_morgan_cash(p):
            continue
        key = _funder_key(p)
        if key is None:
            unlabeled += 1
        else:
            seen_funders.add(key)
    return len(seen_funders) + unlabeled


def total_weekly_debit(positions, *, include_morgan_cash: bool = True) -> float:
    """Σ weekly-normalized payments across the statement's funder streams (the true weekly burden).

    Includes Morgan Cash's own debit by default — the TOTAL weekly obligation is what burden is
    measured against (the merchant pays every funder, MC included). A stream whose amount/frequency
    can't be normalized contributes 0 (it is flagged for review elsewhere), never a fabricated value.
    """
    total = 0.0
    for p in positions:
        if not include_morgan_cash and is_morgan_cash(p):
            continue
        wd = normalize_to_weekly(p.get("payment_amount"), p.get("payment_frequency"))
        if wd is not None:
            total += wd
    return total


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def statement_is_fresh(as_of_date, run_date, max_age_days: int = C.STATEMENT_FRESHNESS_MAX_DAYS) -> bool:
    """C-026 #2: a funding-moment statement is point-in-time; the clock is live. True only when the
    statement's `as_of_date` is within `max_age_days` of `run_date` (and not in the future). A stale
    statement's extraction is recorded but NOT surfaced as current truth (the orchestration gates it
    to REVIEW). Unknown/unparseable dates → False (don't assume a stale snapshot is current)."""
    a = _as_date(as_of_date)
    r = _as_date(run_date)
    if a is None or r is None:
        return False
    age = (r - a).days
    return 0 <= age <= max_age_days


def est_weekly_revenue(deposits_total, period_days) -> float | None:
    """Average weekly revenue from the statement's deposits: deposits_total ÷ (period_days / 7).

    None when deposits_total is missing or the period is missing/non-positive — revenue is then
    UNKNOWN, never 0 (mirrors the clock's burden_ratio honesty: a missing figure is not "no
    revenue"). period_days handles partial months and multi-month statements without assuming 30."""
    if deposits_total is None or period_days is None:
        return None
    days = float(period_days)
    if days <= 0:
        return None
    weeks = days / 7.0
    return float(deposits_total) / weeks


def summarize_statement(
    positions,
    deposits_total=None,
    period_days=None,
    *,
    include_morgan_cash_in_count: bool = False,
) -> dict:
    """Compose the three statement-derived signals the Statement Analyst emits as grounded
    extractions (CONCURRENT_POSITIONS / WEEKLY_DEBIT / EST_WEEKLY_REVENUE). The agent supplies the
    classified streams + deposits; this returns the deterministic counts/sums the clock consumes.
    `positions` may be empty → 0 positions / 0 debit; revenue is None unless deposits + period given."""
    return {
        "concurrent_positions": concurrent_position_count(
            positions, include_morgan_cash=include_morgan_cash_in_count
        ),
        "total_weekly_debit": total_weekly_debit(positions),
        "est_weekly_revenue": est_weekly_revenue(deposits_total, period_days),
    }
