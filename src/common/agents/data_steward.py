"""Data Steward LLM agent — the fuzzy half (Framework §5.9, S7 Phase 1).

The agent READS a defaulted merchant's free-text servicing `Notes` and PROPOSES a default-cause
label + confidence + the grounding snippet, by calling a Databricks Foundation Model (Claude).
It does NOT decide the route — the deterministic tools (`apply_default_subtype` + `make_extraction`)
do. So the agent EXTRACTS; the spine COMPUTES, and "the rules fired" stays the auditable answer.

The LLM call is INJECTED (`predict_fn`) so this module imports and unit-tests with no network:
the prompt builder + the tolerant response parser are pure and tier-1 testable; the Spark driver
(transform/gold_extraction.py) supplies the real Databricks chat client. The prompt is strictly
grounded — "answer ONLY from the Notes; if unclear, return unknown with low confidence" — so the
honesty constraint (CLAUDE.md 2.3) holds at the model boundary, and the confidence gate (D-705)
then refuses to auto-apply anything the model wasn't sure about.
"""

from __future__ import annotations

import json
import re

from common import constants as C
from common.agents.default_subtype import apply_default_subtype
from common.agents.grounding import make_extraction

# Default Foundation Model endpoint (verified provisioned in-workspace) + a version string that
# stamps every extraction for audit/reproducibility (Event Log contract; make_extraction requires it).
DEFAULT_ENDPOINT = "databricks-claude-sonnet-4-5"
MODEL_VERSION = "data-steward/claude-sonnet-4-5/v1"

# The four labels the model may return (mirrors DefaultSubtype; normalize_subtype_label maps
# synonyms, but we constrain the model to these to keep parsing tight).
ALLOWED_LABELS = ("true_default", "early_payoff", "restructured", "unknown")

_SYSTEM = (
    "You are a credit-operations data steward for a merchant cash advance (MCA) brokerage. "
    "You read the free-text servicing Notes for a merchant whose advance is marked closed / "
    "defaulted, and you classify the CAUSE of that closure into exactly one category. "
    "Ground every answer ONLY in the provided Notes text — never invent or assume facts not "
    "written there. If the Notes do not clearly indicate a cause, return \"unknown\".\n\n"
    "Categories:\n"
    "- true_default: the merchant failed to repay — uncollectable, charged off, written off, "
    "bankruptcy, stopped paying with a loss to the funder.\n"
    "- early_payoff: the merchant repaid early / in full / prepaid, or a commission clawback due "
    "to early payoff. This is a HEALTHY exit, not a loss.\n"
    "- restructured: the balance was modified, settled, or a workout / revised payment plan was "
    "agreed.\n"
    "- unknown: the Notes do not clearly state a cause.\n\n"
    "Respond with ONLY a JSON object and no other text:\n"
    "{\"label\": \"<one category>\", \"confidence\": <number 0..1>, "
    "\"citation\": \"<verbatim snippet from the Notes that justifies the label, or null>\"}"
)


def build_messages(notes: str) -> list[dict]:
    """The chat messages for one merchant's Notes (system grounding + the Notes payload)."""
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f'Notes:\n"""\n{notes}\n"""'},
    ]


def _coerce_confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    if c != c:  # NaN
        return 0.0
    return max(0.0, min(1.0, c))


def parse_response(content) -> dict:
    """Tolerantly parse the model's reply into {label, confidence, citation}.

    Defensive by design (the honesty constraint): anything we can't parse, or any label outside
    ALLOWED_LABELS, collapses to label='unknown' confidence=0.0 — so a malformed/hallucinated
    reply can never masquerade as a confident classification. Extracts the first {...} block so a
    stray prose wrapper doesn't break us."""
    if not content:
        return {"label": "unknown", "confidence": 0.0, "citation": None}
    text = str(content).strip()
    obj = None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
            except (ValueError, TypeError):
                obj = None
    if not isinstance(obj, dict):
        return {"label": "unknown", "confidence": 0.0, "citation": None}
    label = str(obj.get("label", "")).strip().lower()
    if label not in ALLOWED_LABELS:
        # don't guess; an out-of-vocabulary label is treated as unknown (the gate will REVIEW it)
        return {"label": "unknown", "confidence": _coerce_confidence(obj.get("confidence")),
                "citation": obj.get("citation")}
    citation = obj.get("citation")
    citation = None if citation in (None, "", "null") else str(citation)
    return {"label": label, "confidence": _coerce_confidence(obj.get("confidence")), "citation": citation}


def classify_default_cause(notes, predict_fn, *, endpoint: str = DEFAULT_ENDPOINT, max_tokens: int = 300) -> dict:
    """Propose a default-cause label for one merchant's Notes via the injected `predict_fn`.

    `predict_fn(endpoint=..., messages=[...], max_tokens=...)` returns the model's raw text reply.
    Empty/blank Notes short-circuit to unknown (no LLM call — nothing to ground on). Returns
    {label, confidence, citation}; the caller routes it through apply_default_subtype + make_extraction.
    """
    if not notes or not str(notes).strip():
        return {"label": "unknown", "confidence": 0.0, "citation": None}
    raw = predict_fn(endpoint=endpoint, messages=build_messages(str(notes)), max_tokens=max_tokens)
    return parse_response(raw)


def build_extraction_rows(
    records: list[dict],
    run_date,
    predict_fn,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> list[dict]:
    """Pure orchestration (no Spark): the agent→gate→ground path for a list of closed_default
    records {merchant_id, deal_id, notes}. For each: ask the model for a label+confidence+citation,
    run the deterministic gate (`apply_default_subtype`) + the grounding contract
    (`make_extraction`), and return an enriched `gold.merchant_extraction` row dict — plus the
    resolved default_subtype/route carried for the event log (NOT extraction columns).

    Reconciliation: `make_extraction` grounds it (ungrounded → REJECTED); the gate decides
    concreteness+confidence. Final review_status = REJECTED if ungrounded, else the gate's verdict
    — so an `unknown` / low-confidence label is never auto-APPLIED even when it is grounded. Lives
    here (Spark-free) so the whole path is tier-1 testable with a fake predict_fn; the transform is
    a thin Spark wrapper (Rule 3: the logic lives once)."""
    rows: list[dict] = []
    for r in records:
        merchant_id, deal_id, notes = r["merchant_id"], r["deal_id"], r.get("notes")
        proposal = classify_default_cause(notes, predict_fn, endpoint=endpoint)
        gate = apply_default_subtype(proposal["label"], proposal["confidence"])
        source_ref = f"silver.deals.notes:{deal_id}" if notes else None
        ext = make_extraction(
            merchant_id,
            deal_id,
            C.ExtractionType.DEFAULT_SUBTYPE,
            gate["default_subtype"],
            proposal["confidence"],
            source_ref,
            model_version,
            run_date,
            citation=proposal.get("citation"),
        )
        if ext["review_status"] != C.ReviewStatus.REJECTED:
            ext["review_status"] = gate["review_status"]
        applied = ext["review_status"] == C.ReviewStatus.APPLIED
        ext["default_subtype"] = gate["default_subtype"] if applied else None
        ext["route"] = gate["route"] if applied else None
        rows.append(ext)
    return rows
