"""Suitability gate — D-506 / Framework §5.7 (S5).

The engine PROPOSES, the advisory layer DISPOSES (CLAUDE.md §2.3). Without this gate the
Offer Engine quietly becomes a "sell more money" machine — the exact outcome the framework
exists to avoid. A matchable offer (e.g. a buyout) may still be unsuitable (a double-dip on a
barely-paid position) and must not be surfaced; sometimes the honest output is "wait and pay
down first."

v1 suitability is the renewal-vs-buyout structure check (deterministic, from the S2 clock).
The full COMPLIANCE gate (advice-vs-specific-offer classification, state disclosure) is S8
(D-508) — reserved here as an interface, never designed around. Pure — no Spark, no I/O.
"""

from __future__ import annotations

from common import constants as C

_OT = C.OfferType
_OS = C.OfferStructure
_SV = C.SuitabilityVerdict


def suitability_verdict(offer_type: str, structure: str | None) -> str:
    """Decide whether a candidate offer of `offer_type` is suitable to surface, given the
    recommended `structure` (from structure.recommend_structure):

    - structure == wait-and-pay-down → WAIT (advise paydown; suppress any new advance).
    - a BUYOUT candidate whose recommended structure is NOT buyout → SUPPRESS (matchable but a
      double-dip — the classic unsuitable case the gate exists to block).
    - none-yet → SURFACE (nothing to suppress; it is already the honest "no offer").
    - otherwise → SURFACE.
    """
    if structure == _OS.WAIT_AND_PAYDOWN:
        return _SV.WAIT
    if offer_type == _OT.BUYOUT and structure is not None and structure != _OS.BUYOUT:
        return _SV.SUPPRESS
    return _SV.SURFACE


def is_suitable(offer_type: str, structure: str | None) -> bool:
    """True only when the offer may be surfaced (still subject to the S8 compliance gate)."""
    return suitability_verdict(offer_type, structure) == _SV.SURFACE


def compliance_gate_hook(offer_type: str, governing_state: str | None) -> dict:
    """RESERVED interface for the S8 compliance block (D-508) — NOT implemented in S5.

    The offer flows through this hook before any delivery; S8 will classify advice-vs-specific
    -offer and apply state disclosure / regulated-language rules. v1 returns a pass-through
    marker so the wiring exists without designing around the gate.
    """
    return {"offer_vs_advice": None, "compliance_checked": False, "deferred_to": "S8"}
