"""Advice-vs-specific-offer classifier — D-806 / Framework §2.4 (S8).

The core distinction the compliance gate turns on: a *specific offer* (one that names concrete
money terms) is regulated far more tightly than general *advice* or a *factual summary* of the
merchant's own situation. The Advisory Composer proposes an intent label, but THIS deterministic
classifier is the authority — it re-derives the type from the advisory's structured content, so
a mislabeling (or adversarial) agent can never downgrade a specific offer into "advice" to dodge
the strict path (Framework §5.9: the agent articulates, the deterministic gate decides).

Pure — no Spark, no LLM.
"""

from __future__ import annotations

from common import constants as C

_AT = C.AdvisoryType

# Fact-pack keys that make an output a SPECIFIC OFFER: it names a concrete, quotable money term.
_OFFER_TERM_KEYS = ("offer_amount", "advance_amount", "factor_rate", "payment_amount")


def names_concrete_terms(facts: dict) -> bool:
    """True when the advisory's fact set carries a concrete, quotable money term (offer amount /
    factor / payment) — the hallmark of a specific offer rather than general advice."""
    if not facts:
        return False
    return any(facts.get(k) is not None for k in _OFFER_TERM_KEYS)


def classify_output_type(advisory: dict) -> str:
    """Deterministically classify a merchant-facing advisory (D-806), independent of the agent's
    self-declared intent:
      - names concrete offer terms                 -> SPECIFIC_OFFER (the strict path);
      - carries a recommended action / guidance    -> ADVICE;
      - otherwise (just restates the situation)     -> FACTUAL_SUMMARY.

    `advisory` carries `facts` (the grounded fact-pack subset it speaks) + `recommended_action`.
    """
    if names_concrete_terms(advisory.get("facts") or {}):
        return _AT.SPECIFIC_OFFER
    if advisory.get("recommended_action"):
        return _AT.ADVICE
    return _AT.FACTUAL_SUMMARY
