"""Closure determination — Appendix A.5b (S2).

Salesforce has NO closure status ("Funded" is the only deal stage; a deal is never
marked paid-off, closed, or defaulted). So open-vs-closed is a COMPUTED clock output, not
a lookup, and it feeds the S3 lifecycle gate (Appendix B).

Three states (A.5b):
  - closed_default — a default is indicated in Notes. This DOMINATES paydown: a defaulted
    deal also computes to ~100% on schedule, so paydown ≥ 100% alone is NEVER closed_clean
    (the Starr case: "Defaulted — $250 clawback" computing 100% must land closed_default).
  - closed_clean   — computed paydown ≥ 100%, no default note.
  - active         — paydown < 100%, no default note.

Because active-position count (the Serial/Rung-2 test and the burden ratio depend on it)
is itself this inference, clock accuracy affects CLASSIFICATION, not just displayed
balances. Pure functions — no Spark, no I/O.
"""

from __future__ import annotations

from common import constants as C

_PAID_OFF = 1.0


def has_default_note(notes: str | None) -> bool:
    """True when the free-text Notes contain any default-cause signal (A.5b).

    Case-insensitive substring match against `constants.DEFAULT_NOTE_KEYWORDS`. The binary
    signal only; sub-typing (true-default vs early-payoff vs restructured, Appendix B.2)
    is the S7 Data Steward agent's job — S2 stays deterministic.
    """
    if not notes:
        return False
    text = notes.lower()
    return any(kw in text for kw in C.DEFAULT_NOTE_KEYWORDS)


def closure_status(paydown_pct, default_note: bool) -> str:
    """Three-state closure (A.5b). A default note dominates; else paydown ≥ 100% is
    closed_clean; else active. Paydown is the (capped) est_paydown_pct.

    A null paydown (clock could not compute — missing terms) with no default note is
    treated `active` (it is not paid off and not defaulted) — never fabricated as closed.
    """
    if default_note:
        return C.ClosureStatus.CLOSED_DEFAULT
    if paydown_pct is not None and float(paydown_pct) >= _PAID_OFF:
        return C.ClosureStatus.CLOSED_CLEAN
    return C.ClosureStatus.ACTIVE


def is_active(closure: str) -> bool:
    return closure == C.ClosureStatus.ACTIVE
