"""Append-only event builders — D-305 (S3).

"Capture every signal and touch as events from the start." The event log is the spine for
the daily floor queue and the Phase-6 learning dataset. v1 emits two event types into ONE
wide append-only table keyed (merchant_id, event_type, event_ts):

  - classification — one per merchant per classify_run_date (the full output snapshot).
  - transition     — emitted ONLY when lifecycle_state or rung changed run-over-run
                     (the state machine; the point-in-time merchant_rung table IS the
                     history, so no separate mutable state table is needed).

S4/S5/S8 later append their own event types (touch / comms / offer) to the same table.

Pure builders — no Spark, no I/O, no mutation: each returns a NEW row dict; nothing is
overwritten (append-only). The Spark writer lives in the transform. Timestamps are passed
IN (`event_ts`) so the builders stay deterministic and the transform owns "now".
"""

from __future__ import annotations

from common import constants as C

# The one wide event-log row shape (D-305). Classification fills the rung snapshot fields;
# transition additionally fills prev_* and transition_field. Columns S4+ don't use stay
# null. Kept here as the single source the schema (schemas/gold.event_log_schema) mirrors.
EVENT_LOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("merchant_id", "string"),  # PK part
    ("event_type", "enum"),  # PK part — EventType.*
    ("event_ts", "timestamp"),  # PK part — when the event was recorded (passed in)
    ("classify_run_date", "date"),  # the classify run this event belongs to
    ("lifecycle_state", "enum"),
    ("rung", "int"),
    ("confidence", "decimal"),
    ("direction_of_travel", "enum"),
    ("default_subtype", "enum"),
    ("route", "enum"),
    ("rapid_reup_flag", "bool"),
    ("renewal_chain_incomplete", "bool"),
    ("missing_signals", "string"),  # comma-joined for the wide table (list lives in rung output)
    # transition-only fields (null on classification events)
    ("prev_lifecycle_state", "enum"),
    ("prev_rung", "int"),
    ("transition_field", "string"),  # which axis changed: 'lifecycle_state' / 'rung' / 'both' / 'current_state' / 'active_play'
    # S4 activation fields (null on S3 classification/transition events). For S4 events the
    # `classify_run_date` column holds the ACTIVATION run date (the run this event belongs to).
    ("current_state", "enum"),  # S4 state_transition / play_fired — the current operational state
    ("active_play", "enum"),  # S4 play_fired — the newly-active named play
    ("prev_current_state", "enum"),  # S4 state_transition — the prior run's current_state
    ("prev_active_play", "enum"),  # S4 play_fired — the prior run's active_play
)


def event_log_columns() -> list[str]:
    return [c for c, _ in EVENT_LOG_COLUMNS]


def _join_missing(missing_signals) -> str | None:
    if not missing_signals:
        return None
    return ",".join(missing_signals)


def _base_row(merchant_id: str, event_type: str, event_ts, classify_run_date) -> dict:
    """A fully-keyed event row with every column present (null where unused) so every
    builder yields the same wide shape."""
    return {c: None for c, _ in EVENT_LOG_COLUMNS} | {
        "merchant_id": merchant_id,
        "event_type": event_type,
        "event_ts": event_ts,
        "classify_run_date": classify_run_date,
    }


def classification_event(
    merchant_id: str, classify_run_date, classification: dict, event_ts
) -> dict:
    """One classification event: the full output snapshot for this run (append-only)."""
    row = _base_row(merchant_id, C.EventType.CLASSIFICATION, event_ts, classify_run_date)
    row.update(
        {
            "lifecycle_state": classification.get("lifecycle_state"),
            "rung": classification.get("rung"),
            "confidence": classification.get("confidence"),
            "direction_of_travel": classification.get("direction_of_travel"),
            "default_subtype": classification.get("default_subtype"),
            "route": classification.get("route"),
            "rapid_reup_flag": classification.get("rapid_reup_flag"),
            "renewal_chain_incomplete": classification.get("renewal_chain_incomplete"),
            "missing_signals": _join_missing(classification.get("missing_signals")),
        }
    )
    return row


def _transition_field(prev: dict, curr: dict) -> str | None:
    """Which axis changed between runs, or None when nothing changed."""
    lifecycle_changed = prev.get("lifecycle_state") != curr.get("lifecycle_state")
    rung_changed = prev.get("rung") != curr.get("rung")
    if lifecycle_changed and rung_changed:
        return "both"
    if lifecycle_changed:
        return "lifecycle_state"
    if rung_changed:
        return "rung"
    return None


def transition_event(
    merchant_id: str, classify_run_date, prev: dict, curr: dict, event_ts
) -> dict | None:
    """A transition event — emitted ONLY when lifecycle_state or rung changed from the
    prior run (the state machine). Returns None when nothing changed (no row appended).
    `prev`/`curr` are classification dicts."""
    changed = _transition_field(prev, curr)
    if changed is None:
        return None
    row = _base_row(merchant_id, C.EventType.TRANSITION, event_ts, classify_run_date)
    row.update(
        {
            "lifecycle_state": curr.get("lifecycle_state"),
            "rung": curr.get("rung"),
            "confidence": curr.get("confidence"),
            "direction_of_travel": curr.get("direction_of_travel"),
            "default_subtype": curr.get("default_subtype"),
            "route": curr.get("route"),
            "rapid_reup_flag": curr.get("rapid_reup_flag"),
            "renewal_chain_incomplete": curr.get("renewal_chain_incomplete"),
            "missing_signals": _join_missing(curr.get("missing_signals")),
            "prev_lifecycle_state": prev.get("lifecycle_state"),
            "prev_rung": prev.get("rung"),
            "transition_field": changed,
        }
    )
    return row


# --- S4 activation events (D-305 — same wide table; new event types) -------------


def state_transition_event(
    merchant_id: str, activation_run_date, prev: dict, curr: dict, event_ts
) -> dict | None:
    """A `state_transition` event — emitted ONLY when `current_state` changed from the prior
    activation run. `prev`/`curr` are activation dicts (current_state + active_play). Returns
    None when unchanged (no row appended). Append-only — a new row, nothing mutated."""
    prev_state, curr_state = prev.get("current_state"), curr.get("current_state")
    if prev_state is None or prev_state == curr_state:
        return None
    row = _base_row(merchant_id, C.EventType.STATE_TRANSITION, event_ts, activation_run_date)
    row.update(
        {
            "current_state": curr_state,
            "prev_current_state": prev_state,
            "active_play": curr.get("active_play"),
            "transition_field": "current_state",
        }
    )
    return row


def play_fired_event(
    merchant_id: str, activation_run_date, prev: dict, curr: dict, event_ts
) -> dict | None:
    """A `play_fired` event — emitted ONLY when `active_play` changed from the prior run.
    Returns None when unchanged. Append-only."""
    prev_play, curr_play = prev.get("active_play"), curr.get("active_play")
    if prev_play is None or prev_play == curr_play:
        return None
    row = _base_row(merchant_id, C.EventType.PLAY_FIRED, event_ts, activation_run_date)
    row.update(
        {
            "active_play": curr_play,
            "prev_active_play": prev_play,
            "current_state": curr.get("current_state"),
            "transition_field": "active_play",
        }
    )
    return row


def build_activation_events(
    merchant_id: str, activation_run_date, activation: dict, event_ts, prev: dict | None = None
) -> list[dict]:
    """S4 activation events for one merchant for one run: a `state_transition` and/or
    `play_fired` event when (and only when) the prior run differs. First run (prev None) →
    no events (the merchant_activation table is the snapshot; the log captures CHANGES).
    Append-only — these are NEW rows; prior runs are never touched."""
    if not prev:
        return []
    events = []
    st = state_transition_event(merchant_id, activation_run_date, prev, activation, event_ts)
    if st is not None:
        events.append(st)
    pf = play_fired_event(merchant_id, activation_run_date, prev, activation, event_ts)
    if pf is not None:
        events.append(pf)
    return events


def build_events(
    merchant_id: str, classify_run_date, classification: dict, event_ts, prev: dict | None = None
) -> list[dict]:
    """All event rows for one merchant for one classify run: always a classification
    event, plus a transition event when (and only when) the prior run differs. Append-only
    — these are NEW rows; prior runs are never touched."""
    events = [classification_event(merchant_id, classify_run_date, classification, event_ts)]
    if prev:
        transition = transition_event(merchant_id, classify_run_date, prev, classification, event_ts)
        if transition is not None:
            events.append(transition)
    return events
