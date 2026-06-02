"""Lifecycle gate + rung waterfall — Appendix B (S3). Two-stage deterministic engine:
Step-0 lifecycle gate (B.2) routes Defaulted/Dormant/New/Active; the rung waterfall (B.3)
places active merchants onto health rung 1-5 (first-match-wins + a stress override that
pulls down). Emits the Framework 4.7 output object per merchant. NO ML (rules-only; ML is
S6). Reads the S2 clock; NEVER recomputes the spine (CLAUDE.md 2.1).

Pure, Spark-free-at-import (mirrors common/clock / common/identity) so it is tier-1
testable and reusable inside the Spark UDFs of transform/gold_rung.py:
  - lifecycle:  the Step-0 gate (default sub-typing, dormancy, new/establishing)
  - waterfall:  the 5-rung predicates + rung_of; rapid_reup_flag (D-302, owned here)
  - confidence: borderline-driven confidence (D-306) + direction_of_travel (climbing/holding/sliding)
  - classify:   classify_merchant — the single composed entry point
"""

from common.rung.classify import (
    classify_merchant,
    is_unclassified,
    missing_signals,
)
from common.rung.confidence import (
    confidence,
    direction_of_travel,
    health_rank,
)
from common.rung.lifecycle import (
    default_subtype,
    dormancy_gap_days,
    is_defaulted,
    is_dormant,
    is_new_establishing,
    lifecycle_state,
    route_for_default,
)
from common.rung.waterfall import (
    has_prior_clean_renewal,
    is_disciplined,
    is_distressed,
    is_graduate,
    is_growth,
    is_serial,
    prior_paydown_at,
    rapid_reup_flag,
    rapid_reup_into_worse_terms,
    rung_of,
    worsening_factor,
)

__all__ = [
    # lifecycle gate (B.2)
    "lifecycle_state",
    "default_subtype",
    "route_for_default",
    "is_defaulted",
    "is_dormant",
    "is_new_establishing",
    "dormancy_gap_days",
    # waterfall (B.3)
    "rung_of",
    "is_distressed",
    "is_serial",
    "is_disciplined",
    "is_growth",
    "is_graduate",
    "has_prior_clean_renewal",
    "rapid_reup_flag",
    "rapid_reup_into_worse_terms",
    "prior_paydown_at",
    "worsening_factor",
    # confidence + direction (D-306 / 4.7)
    "confidence",
    "direction_of_travel",
    "health_rank",
    # composed entry point
    "classify_merchant",
    "is_unclassified",
    "missing_signals",
]
