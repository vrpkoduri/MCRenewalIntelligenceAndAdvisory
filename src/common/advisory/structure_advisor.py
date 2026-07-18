"""Structure Advisor — D-803 / Framework §5.7 (S8).

ARTICULATES the S5 renewal-vs-buyout / wait-and-pay-down decision into an honest, merchant-facing
recommendation. It READS the already-computed structure evaluation
(`common/offer/structure.structure_evaluation`) and the S5 `suitability_verdict`, and turns them
into a grounded action + the facts the Advisory Composer may speak. It NEVER recomputes the
double-dip math, and it CANNOT un-suppress a suppressed offer: a SUPPRESS/WAIT verdict yields
"wait and pay down" (the honest "don't take money"), never a pitch (CLAUDE.md §2.3).

Pure — no Spark, no LLM. The wording layer (LLM) is the Advisory Composer, which speaks only the
facts this surfaces.
"""

from __future__ import annotations

from common import constants as C
from common.offer.structure import structure_evaluation
from common.offer.suitability import suitability_verdict

_OS = C.OfferStructure
_SV = C.SuitabilityVerdict


def _action_for(structure, verdict) -> str | None:
    """The deterministic recommended action. WAIT/SUPPRESS both resolve to wait-and-pay-down —
    the honest answer that refuses to roll a barely-paid position (the double-dip)."""
    if structure == _OS.WAIT_AND_PAYDOWN or verdict in (_SV.WAIT, _SV.SUPPRESS):
        return "wait-and-pay-down"
    if structure == _OS.BUYOUT:
        return "consider-consolidating-buyout"
    if structure == _OS.RENEWAL:
        return "renewal-eligible"
    return None


def advise_structure(signals: dict, offer_type: str | None = None) -> dict:
    """Articulate the structure decision for one merchant. Reuses the S5 math + gate (NEVER
    recomputes). Returns ``{structure, suitability, double_dip_cost, rolled_balance,
    recommended_action, facts}`` — where `recommended_action` is honest (WAIT/SUPPRESS ⇒ advise
    paydown, never a new-advance pitch) and `facts` are the grounded numbers the Composer may speak.

    `offer_type` is the candidate S5 offer type (renewal/buyout/larger-advance) when the merchant
    is being considered for a concrete offer; None for a pure structure/advice articulation.
    """
    ev = structure_evaluation(signals)
    structure = ev.get("structure")
    verdict = suitability_verdict(offer_type, structure) if offer_type else None

    facts: dict = {}
    if ev.get("double_dip_cost") is not None:
        facts["double_dip_cost"] = ev["double_dip_cost"]
    if ev.get("rolled_balance") is not None:
        facts["est_current_balance"] = ev["rolled_balance"]
    if signals.get("est_paydown_pct") is not None:
        facts["est_paydown_pct"] = signals["est_paydown_pct"]

    return {
        "structure": structure,
        "suitability": verdict,
        "double_dip_cost": ev.get("double_dip_cost"),
        "rolled_balance": ev.get("rolled_balance"),
        "recommended_action": _action_for(structure, verdict),
        "facts": facts,
    }
