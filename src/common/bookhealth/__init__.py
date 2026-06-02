"""Portfolio Analytics / Book Health (Framework 5.8, S4 / C-018). The management scoreboard:
read-only aggregations over the Merchant Gold table + the rung-transition history in the
event log, trended over time. A third renderer over gold — costs almost nothing because the
transitions are already logged (S3). Pure metric definitions here (Spark-free at import); the
aggregation runs in transform/gold_book_health.py. v1 computes only metrics whose inputs
exist (D-404); LTV / defection / offer / comms metrics are explicitly deferred to S5/S6/S8.
"""

from common.bookhealth.metrics import (
    BOOK_HEALTH_METRICS,
    deferred_metrics,
    distribution,
    net_drift,
    pct,
    ratio,
    v1_metrics,
)

__all__ = [
    "pct",
    "ratio",
    "net_drift",
    "distribution",
    "BOOK_HEALTH_METRICS",
    "v1_metrics",
    "deferred_metrics",
]
