"""Offer Engine (proactive) — Build Plan §6 / Framework §5.7 (S5). Reuses the EXISTING
funder-criteria dataset + routing engine (`mca_funders` catalog) against the funded book;
NO routing/criteria rebuild (CLAUDE.md §6), NO outbound delivery / comms (S8). Reads S1-S4
gold; never recomputes the spine; never writes to mca_funders.

Pure, Spark-free-at-import (mirrors common/rung) so it is tier-1 testable and reusable inside
the Spark UDFs of transform/gold_offers.py (build pending — the engine-reuse mechanism is
D-501, gated):
  - profile:      MRI gold -> the engine's v_funder_input profile + missing-field flags (D-503)
  - offer_types:  candidate eligible_offer_types from clock/rung/state (D-504)
  - structure:    renewal-vs-buyout math + double-dip cost + recommendation (D-506)
  - suitability:  the gate (engine proposes, advisory disposes) + the S8 compliance hook (D-508)
"""

from common.offer.offer_types import candidate_offer_types
from common.offer.profile import build_funder_profile, tib_months
from common.offer.structure import double_dip_cost, recommend_structure, structure_evaluation
from common.offer.suitability import (
    compliance_gate_hook,
    is_suitable,
    suitability_verdict,
)

__all__ = [
    "build_funder_profile",
    "tib_months",
    "candidate_offer_types",
    "double_dip_cost",
    "recommend_structure",
    "structure_evaluation",
    "suitability_verdict",
    "is_suitable",
    "compliance_gate_hook",
]
