"""Survival labeling — D-607 (S6 Prediction).

Prepares the lifelines Cox / Kaplan-Meier inputs for the "time to next advance" model. The
key correctness point (Framework §11.2): a merchant who has NOT yet taken another advance is
**censored, not missing** — they're still "alive" and waiting, and dropping them would bias
the demand clock.

Per merchant the advance history yields:
  - one OBSERVED interval per consecutive advance pair (duration = gap days, event_observed = 1
    — they took capital again), and
  - one CENSORED tail spell (duration = today − last advance, event_observed = 0 — still
    waiting; the open spell we are predicting).

Pure — no Spark, no ML at import. The transform expands these per-merchant rows into the Cox
training frame and attaches the covariates (constants.COX_COVARIATES).
"""

from __future__ import annotations

from datetime import date, datetime


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def inter_advance_intervals(deals) -> list[int]:
    """Completed gaps (days) between consecutive advances — each an OBSERVED event (the
    merchant took capital again). Empty for a single-deal merchant."""
    fds = sorted(fd for fd in (_as_date(d.get("funded_date")) for d in deals) if fd is not None)
    return [(b - a).days for a, b in zip(fds, fds[1:])]


def censored_duration(deals, today) -> int | None:
    """The open, CENSORED spell: days from the last advance to `today` (no new advance yet).
    None when there are no dated advances."""
    fds = [fd for fd in (_as_date(d.get("funded_date")) for d in deals) if fd is not None]
    t = _as_date(today)
    if not fds or t is None:
        return None
    last = max(fds)
    return max(0, (t - last).days)


def survival_rows(deals, today) -> list[dict]:
    """The merchant's lifelines rows: every observed interval (event_observed=1) plus the
    censored tail (event_observed=0). A single-deal merchant contributes ONLY the censored
    tail — present in the model (censored), never dropped (Framework §11.2)."""
    rows = [{"duration": g, "event_observed": 1} for g in inter_advance_intervals(deals) if g >= 0]
    tail = censored_duration(deals, today)
    if tail is not None:
        rows.append({"duration": tail, "event_observed": 0})
    return rows
