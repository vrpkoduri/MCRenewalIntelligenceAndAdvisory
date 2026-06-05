"""Data Steward — deterministic default-subtype mapper (Framework §5.9, S7 Phase 1).

The agent (LLM) READS the free-text `Notes` and proposes a default-cause label + confidence.
This module is the DETERMINISTIC tool it calls: it normalizes the proposed label to the
`DefaultSubtype` enum and applies the confidence gate (D-705) — a concrete sub-type is
auto-APPLIED only when confidence ≥ the threshold; otherwise it falls back to the conservative
interim `unknown` → do-not-fund + REVIEW (B.2 / D-301). Routing reuses the S3 lifecycle gate's
`route_for_default` (no duplicate logic, Rule 3). Pure — no Spark, no LLM at import.

The agent EXTRACTS the label; this tool + the S3 lifecycle gate DECIDE the route — so "the
rules fired" remains the auditable answer to why a merchant was routed (Framework §5.9).
"""

from __future__ import annotations

from common import constants as C
from common.rung.lifecycle import route_for_default

_DS = C.DefaultSubtype

# Free-text label → DefaultSubtype. Substring-matched (lower-cased) so the agent can return
# natural phrasing. Order/specificity handled in normalize_subtype_label.
_SYNONYMS: tuple[tuple[str, str], ...] = (
    # true-default (uncollectable / charged off / bankruptcy) -> distressed/exit
    ("true default", _DS.TRUE_DEFAULT), ("true-default", _DS.TRUE_DEFAULT),
    ("charge-off", _DS.TRUE_DEFAULT), ("chargeoff", _DS.TRUE_DEFAULT), ("charged off", _DS.TRUE_DEFAULT),
    ("write-off", _DS.TRUE_DEFAULT), ("writeoff", _DS.TRUE_DEFAULT), ("written off", _DS.TRUE_DEFAULT),
    ("uncollect", _DS.TRUE_DEFAULT), ("bankrupt", _DS.TRUE_DEFAULT), ("defaulted", _DS.TRUE_DEFAULT),
    # early-payoff / clawback (a healthy merchant who left) -> win-back
    ("early payoff", _DS.EARLY_PAYOFF), ("early-payoff", _DS.EARLY_PAYOFF), ("paid early", _DS.EARLY_PAYOFF),
    ("early payment", _DS.EARLY_PAYOFF), ("clawback", _DS.EARLY_PAYOFF), ("claw back", _DS.EARLY_PAYOFF),
    ("prepaid", _DS.EARLY_PAYOFF), ("paid in full", _DS.EARLY_PAYOFF),
    # restructured / workout -> impaired-managed
    ("restructure", _DS.RESTRUCTURED), ("restructured", _DS.RESTRUCTURED), ("workout", _DS.RESTRUCTURED),
    ("modified", _DS.RESTRUCTURED), ("settlement", _DS.RESTRUCTURED), ("settled", _DS.RESTRUCTURED),
    # explicit unknown
    ("unknown", _DS.UNKNOWN), ("unclear", _DS.UNKNOWN), ("undetermined", _DS.UNKNOWN),
)


def normalize_subtype_label(label) -> str:
    """Map a free-text / LLM label to a `DefaultSubtype` enum value. Unrecognized/empty →
    UNKNOWN (never guess). Exact enum values pass through."""
    if not label:
        return _DS.UNKNOWN
    key = str(label).strip().lower()
    if key in _DS.ALL:
        return key
    for needle, subtype in _SYNONYMS:
        if needle in key:
            return subtype
    return _DS.UNKNOWN


def apply_default_subtype(label, confidence, threshold: float = C.AGENT_CONFIDENCE_REVIEW_MIN) -> dict:
    """The deterministic gate (D-705). Returns
        {default_subtype, route, review_status}
    Auto-APPLY a concrete sub-type only when the normalized label is concrete AND confidence ≥
    threshold; otherwise keep the conservative `unknown` → do-not-fund and flag REVIEW (so a
    human confirms before any non-conservative routing — misrouting a true default is costly).
    """
    subtype = normalize_subtype_label(label)
    confident = confidence is not None and float(confidence) >= threshold
    if subtype != _DS.UNKNOWN and confident:
        return {"default_subtype": subtype, "route": route_for_default(subtype),
                "review_status": C.ReviewStatus.APPLIED}
    # unresolved or low-confidence -> conservative interim + human review
    return {"default_subtype": _DS.UNKNOWN, "route": route_for_default(_DS.UNKNOWN),
            "review_status": C.ReviewStatus.REVIEW}
