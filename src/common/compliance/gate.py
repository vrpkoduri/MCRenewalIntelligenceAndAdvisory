"""The compliance gate — D-801 / Framework §2.4 (S8).

The first-class, DETERMINISTIC block every merchant-facing output passes before it may ever be
delivered. This REALIZES the interface reserved in S5 (`common/offer/suitability.compliance_gate_hook`,
D-508) — the wiring existed; here it gets a real verdict.

A HARD gate. It BLOCKs (the artifact is stored + auditable, but NEVER marked deliverable) when:
  1. the advisory is UNGROUNDED — an invented number survived the grounding validator (§2.3);
  2. a SPECIFIC OFFER is pitched whose S5 `suitability_verdict` is NOT `surface` — a suppressed
     double-dip or a wait-and-pay-down case (the exact unsuitable offer the gate exists to stop);
  3. a SPECIFIC OFFER in a disclosure state carries NO disclosure block (§2.4 state disclosure).
Otherwise PASS.

The agent proposes language + an intent label; THIS decides (Framework §5.9). Every verdict is
machine-readable (`reasons`) for audit. Pure — no Spark, no LLM.
"""

from __future__ import annotations

from common import constants as C
from common.compliance.classify import classify_output_type
from common.compliance.disclosure import required_disclosures

_CS = C.ComplianceStatus
_AT = C.AdvisoryType
_SV = C.SuitabilityVerdict


def compliance_gate(
    advisory: dict,
    governing_state=None,
    suitability_verdict: str | None = None,
    *,
    grounded: bool = True,
    has_disclosure_block: bool = False,
) -> dict:
    """Gate one merchant-facing advisory. Returns
    ``{status, output_type, required_disclosures, reasons}`` — status PASS or BLOCKED (D-801).

    - `grounded`             the grounding validator's verdict (advisory.factpack.validate_grounding);
    - `suitability_verdict`  the S5 gate output for a specific offer (surface/suppress/wait);
    - `has_disclosure_block` whether a disclosure block is attached to the artifact.
    """
    output_type = classify_output_type(advisory)
    needed = required_disclosures(output_type, governing_state)
    reasons: list[str] = []

    if not grounded:
        reasons.append("ungrounded: an output value is not backed by the fact pack")

    if output_type == _AT.SPECIFIC_OFFER:
        if suitability_verdict is not None and suitability_verdict != _SV.SURFACE:
            reasons.append(
                f"unsuitable-offer-pitched: suitability={suitability_verdict} (not surface)"
            )
        if needed and not has_disclosure_block:
            reasons.append(
                f"missing-disclosure: {','.join(needed)} required but no disclosure block present"
            )

    return {
        "status": _CS.BLOCKED if reasons else _CS.PASS,
        "output_type": output_type,
        "required_disclosures": needed,
        "reasons": reasons,
    }


def passes(gate_result: dict) -> bool:
    """True only when the gate PASSED (still not the same as 'deliver' — delivery is S9+/gated)."""
    return gate_result.get("status") == _CS.PASS
