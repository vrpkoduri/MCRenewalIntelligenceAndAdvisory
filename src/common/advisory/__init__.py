"""Advisory layer — Build Plan §7 / Framework §2.3/§2.4/§5.9 (S8). Turns the spine's computed
facts into honest, grounded, merchant-facing guidance, behind the first-class compliance gate
(common/compliance). Agents ARTICULATE; deterministic code owns every fact + the gate verdict.

Pure, Spark-free-at-import (mirrors common/offer / common/agents) so it is tier-1 testable and
reusable inside the Spark UDFs of the (gated) transform/gold_advisory.py:
  - factpack:          the grounded fact pack + the no-invented-numbers validator (D-802)
  - structure_advisor: articulate the S5 renewal-vs-buyout / wait-and-pay-down decision (D-803)
  - composer:          the LLM wording half + the compose→ground→gate orchestration (D-802)

**S8 composes + gates; it does NOT send** (no outbound comms / SF write / merchant app).
"""

from common.advisory.composer import (
    build_advisory_rows,
    compose_advisory,
    compose_draft,
)
from common.advisory.factpack import (
    build_fact_pack,
    ungrounded_tokens,
    validate_grounding,
)
from common.advisory.structure_advisor import advise_structure

__all__ = [
    "build_fact_pack",
    "ungrounded_tokens",
    "validate_grounding",
    "advise_structure",
    "compose_draft",
    "compose_advisory",
    "build_advisory_rows",
]
