"""Grounding contract for agent extractions (Framework §5.9 / D-705, S7).

Every agent output MUST be grounded (cite a source record) + carry a confidence + a
model_version, so it is auditable and the spine can decide whether to trust it. This module is
the deterministic gate that builds + validates an extraction row:
  - ungrounded (no source_ref) -> REJECTED (not usable);
  - grounded but low-confidence -> REVIEW (human confirms; never auto-applied);
  - grounded + confident -> APPLIED (flows to the spine re-run).

Pure — no Spark, no LLM at import. The Spark writer (transform/gold_extraction.py) and the LLM
agents both go through `make_extraction`, so nothing ungrounded ever reaches gold.
"""

from __future__ import annotations

from common import constants as C


def review_status(confidence, source_ref, threshold: float = C.AGENT_CONFIDENCE_REVIEW_MIN) -> str:
    """REJECTED if ungrounded; REVIEW if below the confidence threshold; else APPLIED."""
    if not source_ref:
        return C.ReviewStatus.REJECTED
    if confidence is None or float(confidence) < threshold:
        return C.ReviewStatus.REVIEW
    return C.ReviewStatus.APPLIED


def make_extraction(
    merchant_id: str,
    deal_id,
    extraction_type: str,
    value,
    confidence,
    source_ref,
    model_version: str,
    extraction_run_date,
    citation: str | None = None,
    threshold: float = C.AGENT_CONFIDENCE_REVIEW_MIN,
) -> dict:
    """Build one grounded extraction row (the `gold.merchant_extraction` shape). Raises on an
    unknown extraction_type or a missing model_version (auditability is non-negotiable);
    `review_status` is derived from grounding + confidence."""
    if extraction_type not in C.ExtractionType.ALL:
        raise ValueError(f"unknown extraction_type {extraction_type!r}")
    if not model_version:
        raise ValueError("extraction missing model_version (required for audit)")
    return {
        "merchant_id": merchant_id,
        "deal_id": deal_id,
        "extraction_run_date": extraction_run_date,
        "extraction_type": extraction_type,
        "value": None if value is None else str(value),
        "confidence": confidence,
        "source_ref": source_ref,
        "citation": citation,
        "model_version": model_version,
        "review_status": review_status(confidence, source_ref, threshold),
    }


def is_applicable(extraction: dict) -> bool:
    """True only for grounded, confident extractions the spine re-run may consume (APPLIED).
    REVIEW/REJECTED extractions are recorded + logged but never auto-applied to the spine."""
    return extraction.get("review_status") == C.ReviewStatus.APPLIED
