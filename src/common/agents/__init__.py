"""Agentic-layer deterministic tools — Framework §5.9 (S7). Agents EXTRACT (read messy
inputs); these pure, tier-1-testable tools are what they call for anything that must be
correct + auditable, and the spine still COMPUTES. NO LLM at import; NO spine recompute.

  - default_subtype: Data Steward (Phase 1) — normalize a free-text default-cause label →
    DefaultSubtype + confidence gate + B.2 route (reuses the S3 lifecycle gate's routing).
  - grounding:       the extraction contract — make/validate grounded rows (source_ref +
    confidence + model_version), review-status gate (APPLIED / REVIEW / REJECTED).
  - data_steward:    the fuzzy half — build the grounded prompt + tolerantly parse the
    Foundation Model's reply into {label, confidence, citation}. The LLM call is INJECTED
    (predict_fn) so this stays import-clean + tier-1 testable; the Spark driver supplies the
    real Databricks client.

  - positions:       Statement Analyst (Phase 2) — count the true concurrent positions, sum the
    weekly debit burden, and normalize deposits to weekly revenue from the agent's classified
    statement streams (the inputs the S2 clock consumes for `burden_ratio`). Reuses the clock's
    weekly normalization (Rule 3). The fuzzy half (OCR/line classification) lands as
    `statement_analyst.py` when statement ingestion is wired (D-702/C-025).
"""

from common.agents.data_steward import (
    classify_default_cause,
    parse_response,
)
from common.agents.default_subtype import apply_default_subtype, normalize_subtype_label
from common.agents.grounding import is_applicable, make_extraction, review_status
from common.agents.positions import (
    concurrent_position_count,
    est_weekly_revenue,
    normalize_to_weekly,
    statement_is_fresh,
    summarize_statement,
    total_weekly_debit,
)
from common.agents.statement_analyst import (
    build_statement_extractions,
    build_statement_rows,
    classify_statement,
)

__all__ = [
    "normalize_subtype_label",
    "apply_default_subtype",
    "make_extraction",
    "review_status",
    "is_applicable",
    "classify_default_cause",
    "parse_response",
    "normalize_to_weekly",
    "concurrent_position_count",
    "total_weekly_debit",
    "est_weekly_revenue",
    "statement_is_fresh",
    "summarize_statement",
    "classify_statement",
    "build_statement_rows",
    "build_statement_extractions",
]
