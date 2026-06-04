"""RFM feature derivation — D-601 (S6 Prediction).

The cheap, deterministic base the BTYD models consume (Data Contract "Prediction Mapping"):
one row per canonical merchant, an "event" = a funded advance. Pure arithmetic over the deal
history — no Spark, no ML at import (mirrors common/clock / common/rung) — so it is tier-1
testable and reused inside the Spark assembly of transform/gold_predictions.py.

BG/NBD / Gamma-Gamma convention:
  - frequency = number of REPEAT advances = deal_count − 1 (0 for a single-deal merchant).
  - recency   = time of the last advance since the first (days).
  - T         = observation-window age = today − first advance (days).
  - monetary  = average advance value (funded_amount).
Times are in DAYS; the model layer rescales as needed. NEVER invents values — missing terms
drop out (CLAUDE.md §2.5).
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


def rfm_features(deals, today) -> dict | None:
    """RFM features for one merchant from its advance list. `deals` = dicts with
    `funded_date` + `funded_amount`. None when no dated advances exist.

    Returns {rfm_frequency, rfm_recency, rfm_T, rfm_monetary, repeat_events} — `repeat_events`
    (= frequency) is the thin-history signal the confidence/insufficient-history logic uses.
    """
    dated = [(d, _as_date(d.get("funded_date"))) for d in deals]
    dated = [(d, fd) for d, fd in dated if fd is not None]
    if not dated:
        return None

    t = _as_date(today)
    fds = sorted(fd for _, fd in dated)
    first, last = fds[0], fds[-1]
    n = len(fds)

    amounts = [float(d.get("funded_amount")) for d, _ in dated if d.get("funded_amount") is not None]
    monetary = (sum(amounts) / len(amounts)) if amounts else None

    recency = (last - first).days
    T = (t - first).days if t is not None and t >= first else None

    return {
        "rfm_frequency": n - 1,  # repeat advances
        "rfm_recency": recency,
        "rfm_T": T,
        "rfm_monetary": monetary,
        "repeat_events": n - 1,
    }
