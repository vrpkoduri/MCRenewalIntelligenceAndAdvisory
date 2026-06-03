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
    build_activation_events,
    build_events,
    classification_event,
    event_log_columns,
    play_fired_event,
    state_transition_event,
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


# --- S4 activation events ---------------------------------------------------

_ACT_A = {"current_state": "clock-running", "active_play": "serial-renewal-vs-buyout"}
_ACT_B = {"current_state": "in-market", "active_play": "in-market-renewal"}


def test_state_transition_event_only_on_change():
    ev = state_transition_event("M1", RUN, _ACT_A, _ACT_B, TS)
    assert ev["event_type"] == C.EventType.STATE_TRANSITION
    assert ev["prev_current_state"] == "clock-running" and ev["current_state"] == "in-market"
    assert ev["transition_field"] == "current_state"
    assert set(ev.keys()) == set(event_log_columns())  # uniform wide shape
    # unchanged / first run -> no row
    assert state_transition_event("M1", RUN, _ACT_A, _ACT_A, TS) is None
    assert state_transition_event("M1", RUN, {"current_state": None}, _ACT_B, TS) is None


def test_play_fired_event_only_on_change():
    ev = play_fired_event("M1", RUN, _ACT_A, _ACT_B, TS)
    assert ev["event_type"] == C.EventType.PLAY_FIRED
    assert ev["prev_active_play"] == "serial-renewal-vs-buyout" and ev["active_play"] == "in-market-renewal"
    assert ev["transition_field"] == "active_play"
    assert play_fired_event("M1", RUN, _ACT_A, _ACT_A, TS) is None


def test_build_activation_events_first_run_empty_then_changes():
    assert build_activation_events("M1", RUN, _ACT_B, TS, prev=None) == []  # first run: snapshot is the table
    evs = build_activation_events("M1", RUN, _ACT_B, TS, prev=_ACT_A)
    types = sorted(e["event_type"] for e in evs)
    assert types == [C.EventType.PLAY_FIRED, C.EventType.STATE_TRANSITION]


def test_activation_events_do_not_mutate_inputs():
    a, b = dict(_ACT_A), dict(_ACT_B)
    build_activation_events("M1", RUN, _ACT_B, TS, prev=_ACT_A)
    assert _ACT_A == a and _ACT_B == b
