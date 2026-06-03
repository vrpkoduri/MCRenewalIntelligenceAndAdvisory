"""Portfolio Analytics / Book Health metric definitions (Framework 5.8, D-404, S4).

The management scoreboard: read-only aggregations over the Merchant Gold table + the
rung-transition history in the event log, trended over time. This pure module holds (a) the
None-safe scalar calculators the metrics use, and (b) the canonical METRIC REGISTRY marking
which metrics are computable in v1 vs deferred until their inputs land (S5/S6/S8). The Spark
aggregation in transform/gold_book_health.py mirrors these (the dq.predicates ↔ dq.rules
pattern). Pure — no Spark, no I/O.

v1 honesty (D-404): we compute ONLY metrics whose inputs exist today. LTV / defection /
offer-acceptance / value-to-ask / comms-SLA metrics are DEFERRED and explicitly marked —
never faked or zeroed.
"""

from __future__ import annotations

from common import constants as C


def pct(part, whole) -> float | None:
    """part / whole as a fraction in [0,1] (None-safe). None when whole is missing/zero —
    never fabricate a rate from no denominator."""
    if part is None or whole is None:
        return None
    w = float(whole)
    if w == 0.0:
        return None
    return float(part) / w


def ratio(numerator, denominator) -> float | None:
    """numerator / denominator (None-safe; None when denominator missing/zero)."""
    if numerator is None or denominator is None:
        return None
    d = float(denominator)
    if d == 0.0:
        return None
    return float(numerator) / d


def net_drift(up_moves, down_moves) -> int | None:
    """Net rung movement = up − down over the period (None when both inputs absent).
    Positive = the book is climbing; negative = sliding."""
    if up_moves is None and down_moves is None:
        return None
    return int(up_moves or 0) - int(down_moves or 0)


def distribution(counts: dict) -> dict:
    """Turn {bucket: count} into {bucket: {count, pct}} over the total (rung distribution,
    concentration shares). Empty / all-zero → empty pcts (no fabricated denominator)."""
    total = sum(int(v or 0) for v in counts.values())
    out = {}
    for bucket, n in counts.items():
        n = int(n or 0)
        out[bucket] = {"count": n, "pct": (n / total if total else None)}
    return out


# Canonical metric registry (Framework 5.8 / Data Contract "Book Health Metrics"). Each entry:
# (view, metric, available_in_v1, source/deferred-reason). v1 = inputs exist after S1–S4;
# deferred = needs S5 (offers) / S6 (predictions) / S8 (comms).
BOOK_HEALTH_METRICS: tuple[dict, ...] = (
    # --- Book health view ---
    {"view": C.BookHealthView.BOOK_HEALTH, "metric": "rung_distribution", "available_in_v1": True,
     "source": "current_rung (merchant_rung_current)"},
    {"view": C.BookHealthView.BOOK_HEALTH, "metric": "rung_drift", "available_in_v1": True,
     "source": "event log: rung transition (merchant_event_log)"},
    {"view": C.BookHealthView.BOOK_HEALTH, "metric": "renewal_capture_rate", "available_in_v1": True,
     "source": "current_state / lifecycle (partial — full capture needs S5/S6)"},
    {"view": C.BookHealthView.BOOK_HEALTH, "metric": "default_restructure_trend", "available_in_v1": True,
     "source": "lifecycle_state defaulted + closure_status (clock)"},
    {"view": C.BookHealthView.BOOK_HEALTH, "metric": "defection_rate_destination", "available_in_v1": False,
     "source": "DEFERRED — needs p_defection (S6 prediction)"},
    {"view": C.BookHealthView.BOOK_HEALTH, "metric": "aggregate_book_ltv", "available_in_v1": False,
     "source": "DEFERRED — needs predicted_clv (S6 prediction)"},
    # --- Renewal performance view (ENTIRELY deferred in v1 — every metric needs touch
    #     events (S8) or offer status (S5); the renewal_performance view is empty until then) ---
    {"view": C.BookHealthView.RENEWAL_PERFORMANCE, "metric": "play_sla_adherence", "available_in_v1": False,
     "source": "DEFERRED — needs touch events (S8 comms) to know when a play was actioned"},
    {"view": C.BookHealthView.RENEWAL_PERFORMANCE, "metric": "time_to_contact", "available_in_v1": False,
     "source": "DEFERRED — needs touch events (S8 comms)"},
    {"view": C.BookHealthView.RENEWAL_PERFORMANCE, "metric": "value_to_ask_ratio", "available_in_v1": False,
     "source": "DEFERRED — needs value/ask touches (S8 comms)"},
    {"view": C.BookHealthView.RENEWAL_PERFORMANCE, "metric": "offer_acceptance", "available_in_v1": False,
     "source": "DEFERRED — needs eligible_offer_types + accept status (S5 offers)"},
    # --- Leading indicators view ---
    {"view": C.BookHealthView.LEADING_INDICATORS, "metric": "sliding_count", "available_in_v1": True,
     "source": "direction_of_travel = sliding (merchant_rung_current)"},
    {"view": C.BookHealthView.LEADING_INDICATORS, "metric": "approaching_pipeline", "available_in_v1": True,
     "source": "current_state = approaching (merchant_activation_current)"},
    {"view": C.BookHealthView.LEADING_INDICATORS, "metric": "concentration_risk", "available_in_v1": True,
     "source": "funder / governing_state / rung shares (gold)"},
)


def v1_metrics() -> list[dict]:
    """The metrics computable in S4 v1 (their inputs exist)."""
    return [m for m in BOOK_HEALTH_METRICS if m["available_in_v1"]]


def deferred_metrics() -> list[dict]:
    """Metrics explicitly deferred to S5/S6/S8 — surfaced (not faked) so the scoreboard's
    coverage is honest."""
    return [m for m in BOOK_HEALTH_METRICS if not m["available_in_v1"]]
