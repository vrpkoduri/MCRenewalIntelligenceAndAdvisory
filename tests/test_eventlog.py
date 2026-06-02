"""Tier-1 tests for the append-only event log — D-305 (S3).

Pure builders only (no Spark): classification + transition event construction, the
append-only / no-mutation invariant, transition-only-on-change (the state machine), and
the event schema/no-surface invariants. The log is keyed (merchant_id, event_type,
event_ts) — one wide table (D-305).
"""

from __future__ import annotations

from datetime import date, datetime

from common import constants as C
from common.eventlog import (
    EVENT_LOG_COLUMNS,
    build_events,
    classification_event,
    event_log_columns,
    transition_event,
)
from common.io.guards import offending_surface_columns

RUN = date(2026, 6, 2)
TS = datetime(2026, 6, 2, 12, 0, 0)

_CLS_SERIAL = {
    "lifecycle_state": "active", "rung": 2, "confidence": 0.9, "direction_of_travel": "holding",
    "default_subtype": None, "route": "waterfall", "rapid_reup_flag": True,
    "renewal_chain_incomplete": False, "missing_signals": ["burden_ratio", "est_weekly_revenue"],
}
_CLS_DISTRESSED = {**_CLS_SERIAL, "rung": 1, "confidence": 0.7, "direction_of_travel": "sliding",
                   "missing_signals": []}


def test_classification_event_is_well_keyed_and_wide():
    ev = classification_event("M1", RUN, _CLS_SERIAL, TS)
    assert ev["merchant_id"] == "M1"
    assert ev["event_type"] == C.EventType.CLASSIFICATION
    assert ev["event_ts"] == TS
    assert ev["classify_run_date"] == RUN
    assert ev["rung"] == 2
    assert ev["missing_signals"] == "burden_ratio,est_weekly_revenue"  # comma-joined for the wide table
    # every column present (uniform wide shape), transition-only fields null
    assert set(ev.keys()) == set(event_log_columns())
    assert ev["prev_rung"] is None and ev["transition_field"] is None


def test_transition_event_only_when_changed():
    # rung 2 -> 1 changed
    t = transition_event("M1", RUN, _CLS_SERIAL, _CLS_DISTRESSED, TS)
    assert t is not None
    assert t["event_type"] == C.EventType.TRANSITION
    assert t["prev_rung"] == 2 and t["rung"] == 1
    assert t["transition_field"] == "rung"
    # unchanged -> no transition row
    assert transition_event("M1", RUN, _CLS_SERIAL, _CLS_SERIAL, TS) is None


def test_transition_field_distinguishes_lifecycle_rung_both():
    a = {"lifecycle_state": "active", "rung": 2}
    assert transition_event("M", RUN, a, {"lifecycle_state": "active", "rung": 3}, TS)["transition_field"] == "rung"
    assert transition_event("M", RUN, a, {"lifecycle_state": "defaulted", "rung": 2}, TS)["transition_field"] == "lifecycle_state"
    assert transition_event("M", RUN, a, {"lifecycle_state": "dormant", "rung": None}, TS)["transition_field"] == "both"


def test_build_events_first_run_is_classification_only():
    evs = build_events("M1", RUN, _CLS_SERIAL, TS, prev=None)
    assert len(evs) == 1
    assert evs[0]["event_type"] == C.EventType.CLASSIFICATION


def test_build_events_appends_transition_on_change():
    evs = build_events("M1", RUN, _CLS_DISTRESSED, TS, prev=_CLS_SERIAL)
    types = [e["event_type"] for e in evs]
    assert types == [C.EventType.CLASSIFICATION, C.EventType.TRANSITION]


def test_build_events_no_transition_when_stable():
    evs = build_events("M1", RUN, _CLS_SERIAL, TS, prev=_CLS_SERIAL)
    assert [e["event_type"] for e in evs] == [C.EventType.CLASSIFICATION]


def test_builders_do_not_mutate_inputs():
    """Append-only: builders return NEW rows; the classification/prev dicts are untouched."""
    cls_before = dict(_CLS_SERIAL)
    prev_before = dict(_CLS_DISTRESSED)
    build_events("M1", RUN, _CLS_SERIAL, TS, prev=_CLS_DISTRESSED)
    assert _CLS_SERIAL == cls_before
    assert _CLS_DISTRESSED == prev_before


def test_event_log_no_surface_columns():
    assert offending_surface_columns(event_log_columns()) == []


def test_event_log_columns_are_unique():
    cols = event_log_columns()
    assert len(cols) == len(set(cols))
    # keyed (merchant_id, event_type, event_ts) leading the wide table
    assert cols[:3] == ["merchant_id", "event_type", "event_ts"]


def test_event_types_enumerated():
    ev = classification_event("M1", RUN, _CLS_SERIAL, TS)
    assert ev["event_type"] in C.EventType.ALL
