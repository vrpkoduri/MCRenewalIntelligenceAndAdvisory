"""Compliance gate — Build Plan §7 / Framework §2.4 (S8). The first-class, DETERMINISTIC block
every merchant-facing output must pass before it may ever be delivered. Realizes the S5
`compliance_gate_hook` (D-508). Agents articulate; this decides (Framework §5.9).

Pure, Spark-free-at-import (mirrors common/offer) so it is tier-1 testable and reusable inside
the Spark UDFs of the (gated) transform/gold_advisory.py:
  - classify:    advice vs specific-offer vs factual-summary (D-806) — the strict-path trigger
  - disclosure:  state-aware disclosure regime + required-disclosures lookup (D-805)
  - gate:        the composed HARD gate → PASS / BLOCKED + reasons (D-801)
"""

from common.compliance.classify import classify_output_type, names_concrete_terms
from common.compliance.disclosure import disclosure_regime, required_disclosures
from common.compliance.gate import compliance_gate, passes

__all__ = [
    "classify_output_type",
    "names_concrete_terms",
    "disclosure_regime",
    "required_disclosures",
    "compliance_gate",
    "passes",
]
