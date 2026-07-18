"""State-aware disclosure rules — D-805 / Framework §2.4 (S8).

Some states impose commercial-financing DISCLOSURE obligations (e.g. CA's SB 1235 / DFPI regime,
NY's Commercial Finance Disclosure Law, UT, VA). v1 is deliberately narrow and honest: it FLAGS
which regime applies to the merchant's governing state and REQUIRES that a disclosure block be
present on a specific offer — it does NOT author the binding legal language (counsel owns the
wording; D-805). The rule table lives in `constants.DISCLOSURE_RULES` (one place, Rule 3) and is
**pending counsel review before any cloud run**.

Pure — no Spark, no LLM.
"""

from __future__ import annotations

from common import constants as C


def disclosure_regime(governing_state) -> str:
    """The disclosure regime for a merchant's governing state (USPS code). An unknown / absent /
    unlisted state -> NONE — which means 'no special regime identified', NOT an assertion that
    none exists (the list is seeded + counsel-extended, D-805)."""
    if not governing_state:
        return C.DisclosureRegime.NONE
    return C.DISCLOSURE_RULES.get(str(governing_state).strip().upper(), C.DisclosureRegime.NONE)


def required_disclosures(output_type: str, governing_state) -> list[str]:
    """Which disclosure regimes a merchant-facing output must satisfy before it may be delivered
    (D-805). Only a SPECIFIC OFFER in a regulated state requires a disclosure block in v1; advice
    and factual summaries name no concrete terms and do not. Returns [] when none is required."""
    if output_type != C.AdvisoryType.SPECIFIC_OFFER:
        return []
    regime = disclosure_regime(governing_state)
    return [] if regime == C.DisclosureRegime.NONE else [regime]
