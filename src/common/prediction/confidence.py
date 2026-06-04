"""Prediction confidence + insufficient-history gate — D-603 (S6 Prediction).

`prediction_confidence` is FIRST-CLASS (Framework §11.2 / Data Contract: "uncertainty band —
governs how advice is framed", soft vs firm). It is NOT an accuracy claim — it is how sure the
model is, driven by the posterior width once a fit exists, and by history depth otherwise.

The honest stance for the sparse book (62.5% single-deal — the readiness spike): a merchant
with too little repeat history is flagged `insufficient_history` and predicted from the
book-level prior with WIDE confidence — never false precision. Pure — no Spark, no ML.
"""

from __future__ import annotations

from common import constants as C

_FLOOR = 0.05  # never report exactly 0 confidence; even a prior has *some* information
_CEIL = 1.0


def is_insufficient_history(repeat_events) -> bool:
    """True when a merchant has fewer than the minimum repeat advances for an individual
    data-driven fit (D-603). Such merchants get the book-level prior + wide confidence."""
    return (repeat_events or 0) < C.INSUFFICIENT_HISTORY_MIN_EVENTS


def confidence_from_history(repeat_events) -> float:
    """Deterministic confidence proxy from history depth — used for `insufficient_history`
    merchants (no individual posterior) and as a fallback. Monotonic increasing in repeat
    events: 0 → low, asymptotes toward 1. `1 − 1/(1 + n)` then floored."""
    n = max(0, int(repeat_events or 0))
    return _clamp(1.0 - 1.0 / (1.0 + n))


def confidence_from_posterior(posterior_width, scale=1.0) -> float | None:
    """Confidence from a fitted model's posterior width (the transform supplies it post-fit):
    wider posterior → lower confidence. `1 / (1 + width/scale)`. None if width absent."""
    if posterior_width is None or scale is None or scale <= 0:
        return None
    return _clamp(1.0 / (1.0 + float(posterior_width) / float(scale)))


def prediction_confidence(repeat_events, posterior_width=None, scale=1.0) -> float:
    """The merchant's confidence: from the posterior width when a fit exists, else the
    history-depth proxy (insufficient-history / fallback). Always in [floor, 1]."""
    if posterior_width is not None:
        c = confidence_from_posterior(posterior_width, scale)
        if c is not None:
            return c
    return confidence_from_history(repeat_events)


def _clamp(x: float) -> float:
    return max(_FLOOR, min(_CEIL, x))
