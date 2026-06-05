"""Agentic-layer deterministic tools — Framework §5.9 (S7). Agents EXTRACT (read messy
inputs); these pure, tier-1-testable tools are what they call for anything that must be
correct + auditable, and the spine still COMPUTES. NO LLM at import; NO spine recompute.

  - default_subtype: Data Steward (Phase 1) — normalize a free-text default-cause label →
    DefaultSubtype + confidence gate + B.2 route (reuses the S3 lifecycle gate's routing).
  - grounding:       the extraction contract — make/validate grounded rows (source_ref +
    confidence + model_version), review-status gate (APPLIED / REVIEW / REJECTED).

The Statement Analyst's deterministic position/burden counter (Phase 2) lands here as
`positions.py` when statement ingestion is wired (D-702).
"""

from common.agents.default_subtype import apply_default_subtype, normalize_subtype_label
from common.agents.grounding import is_applicable, make_extraction, review_status

__all__ = [
    "normalize_subtype_label",
    "apply_default_subtype",
    "make_extraction",
    "review_status",
    "is_applicable",
]
