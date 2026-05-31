"""Contract-enforcement guards for reads/writes.

The no-surface guard makes the CLAUDE.md 2.1 rule executable: no view/table consumed
downstream may expose the frozen SF snapshot columns. Used by the silver transform and
by the no-surface test.
"""

from ..constants import NO_SURFACE_COLUMNS, SF_STORED_PREFIX


def offending_surface_columns(columns) -> list[str]:
    """Return any columns that must never be surfaced downstream."""
    cols = list(columns)
    offenders = [c for c in cols if c in NO_SURFACE_COLUMNS]
    offenders += [
        c for c in cols if c.startswith(SF_STORED_PREFIX) and c not in offenders
    ]
    return offenders


def assert_no_surface(columns) -> None:
    """Raise if a downstream column set exposes a do-not-surface column."""
    offenders = offending_surface_columns(columns)
    if offenders:
        raise AssertionError(
            f"Do-not-surface columns present in downstream output: {offenders}. "
            "These are frozen SF snapshots (CLAUDE.md 2.1) — keep them only in the "
            "checkpoint table, never in a consumed view."
        )
