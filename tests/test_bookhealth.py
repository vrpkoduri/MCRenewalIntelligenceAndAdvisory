"""Tier-1 tests for Book Health metric definitions (Framework 5.8, D-404, S4).

Pure functions only: the None-safe scalar calculators and the metric registry's v1-available
vs deferred split (LTV / defection / offers / comms are deferred until S5/S6/S8 — honest, not
faked). Plus the tall book_health schema invariants.
"""

from __future__ import annotations

from common import constants as C
from common.bookhealth import (
    deferred_metrics,
    distribution,
    net_drift,
    pct,
    ratio,
    v1_metrics,
)
from common.field_maps import book_health_columns
from common.io.guards import offending_surface_columns


def test_pct_and_ratio_none_safe():
    assert pct(2, 8) == 0.25
    assert pct(5, 0) is None  # no fabricated rate from a zero denominator
    assert pct(None, 8) is None
    assert ratio(3, 6) == 0.5
    assert ratio(1, 0) is None
    assert ratio(None, 4) is None


def test_net_drift():
    assert net_drift(10, 4) == 6  # climbing
    assert net_drift(2, 7) == -5  # sliding
    assert net_drift(None, None) is None
    assert net_drift(3, None) == 3


def test_distribution_counts_and_pcts():
    d = distribution({"1": 1, "2": 1, "3": 2})
    assert d["3"]["count"] == 2
    assert d["3"]["pct"] == 0.5
    assert abs(sum(v["pct"] for v in d.values()) - 1.0) < 1e-9


def test_distribution_empty_has_no_fabricated_pct():
    d = distribution({"1": 0, "2": 0})
    assert all(v["pct"] is None for v in d.values())  # total 0 -> no denominator


def test_v1_vs_deferred_split():
    v1 = {m["metric"] for m in v1_metrics()}
    deferred = {m["metric"] for m in deferred_metrics()}
    # available now (inputs exist after S1-S4)
    assert {"rung_distribution", "rung_drift", "sliding_count", "approaching_pipeline",
            "concentration_risk", "default_restructure_trend"} <= v1
    # explicitly deferred (need S5 offers / S6 predictions / S8 comms)
    assert {"aggregate_book_ltv", "defection_rate_destination", "offer_acceptance",
            "value_to_ask_ratio", "time_to_contact", "play_sla_adherence"} <= deferred
    # no overlap; every metric classified
    assert v1.isdisjoint(deferred)


def test_renewal_performance_view_entirely_deferred_in_v1():
    """Every renewal_performance metric needs S5 offers / S8 touches, so that view is empty
    in v1 (honest — omitted, not faked)."""
    v1_rp = [m for m in v1_metrics() if m["view"] == C.BookHealthView.RENEWAL_PERFORMANCE]
    assert v1_rp == []


def test_metric_views_are_valid():
    for m in (*v1_metrics(), *deferred_metrics()):
        assert m["view"] in C.BookHealthView.ALL


def test_book_health_columns_unique_and_no_surface():
    cols = book_health_columns()
    assert len(cols) == len(set(cols))
    assert cols[:2] == ["report_date", "view"]
    assert offending_surface_columns(cols) == []
