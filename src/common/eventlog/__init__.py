"""Append-only event log — D-305 (S3). One wide table keyed (merchant_id, event_type,
event_ts); v1 emits classification + transition events; S4/S5/S8 append touch/comms/offer
events to the same log. Pure builders (no Spark, no I/O, no mutation); the Spark writer
lives in the transform. The point-in-time merchant_rung table is the state history, so the
transition event IS the state machine — no separate mutable state table.
"""

from common.eventlog.events import (
    EVENT_LOG_COLUMNS,
    build_activation_events,
    build_events,
    classification_event,
    event_log_columns,
    play_fired_event,
    state_transition_event,
    transition_event,
)

__all__ = [
    "EVENT_LOG_COLUMNS",
    "event_log_columns",
    "classification_event",
    "transition_event",
    "build_events",
    "state_transition_event",
    "play_fired_event",
    "build_activation_events",
]
