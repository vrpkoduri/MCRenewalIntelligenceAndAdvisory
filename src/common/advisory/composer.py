"""Advisory Composer — the LLM agent half + the compose orchestration (D-802 / Framework
§2.3/§2.4/§5.9, S8).

The Composer WORDS a grounded, merchant-facing advisory from a merchant's fact pack. It may
speak ONLY numbers in the pack (the grounding validator rejects anything else), and it never
computes the spine or decides compliance — the RECOMMENDED ACTION is deterministic (the clock /
Structure Advisor), and the ADVISORY TYPE + compliance verdict are the deterministic gate's; the
LLM only articulates the headline + rationale.

`compose_advisory` is the pure pipeline every merchant-facing output flows through:

    fact pack (deterministic)
      -> Composer draft (LLM: headline + rationale, grounded)
      -> grounding validator (deterministic: no invented numbers)
      -> compliance gate (deterministic: advice/offer + suitability + disclosure)
      -> advisory record (with compliance_status + review_status)

The LLM call is INJECTED (`predict_fn`) so the prompt-builder + parser + orchestration are
import-clean and tier-1 testable with a fake model (mirrors common/agents/data_steward). The
Spark driver (transform/gold_advisory.py, gated) supplies the real Databricks chat client.

**S8 COMPOSES + GATES; it does NOT send.** A BLOCKED or ungrounded advisory is stored (auditable)
but never marked deliverable (review_status != applied).
"""

from __future__ import annotations

import json
import re

from common import constants as C
from common.advisory.factpack import build_fact_pack, ungrounded_tokens
from common.advisory.structure_advisor import advise_structure

DEFAULT_ENDPOINT = "databricks-claude-sonnet-4-5"
MODEL_VERSION = "advisory-composer/claude-sonnet-4-5/v1"

# Concrete-offer candidate types — when composing one of these WITH an amount, the advisory names
# a concrete term and becomes a SPECIFIC OFFER (the gate then enforces suitability + disclosure).
_CONCRETE_OFFER_TYPES = (C.OfferType.RENEWAL, C.OfferType.BUYOUT, C.OfferType.LARGER_ADVANCE)

_SYSTEM = (
    "You are a merchant capital advisor for a merchant cash advance (MCA) brokerage. You write a "
    "SHORT, honest, merchant-facing advisory — a headline and a rationale — from a set of FACTS "
    "your firm has already computed. Absolute rules:\n"
    "1. GROUND EVERY NUMBER. You may use ONLY the numbers given in FACTS. Never invent, estimate, "
    "round to a different value, or introduce any figure that is not in FACTS.\n"
    "2. BE HONEST. If the recommended action is 'wait-and-pay-down', advise the merchant to WAIT "
    "and pay down first — do NOT pitch a new advance. 'Don't take money right now' is a valid and "
    "expected recommendation. Never imply an offer that the recommended action does not support.\n"
    "3. DO NOT compute or contradict the recommended action — it is decided for you; you only "
    "explain it in plain language.\n"
    "Respond with ONLY a JSON object and no other text:\n"
    '{"headline": "<one line>", "rationale": "<2-3 sentences, plain language>", '
    '"confidence": <number 0..1>, "citation": "<which facts you used>"}'
)


def build_messages(pack: dict, advice: dict) -> list[dict]:
    """Present the grounded facts + the deterministic recommended action to the model."""
    facts_lines = [
        f"- {name} = {f['value']}  (source: {f['source_field']})"
        for name, f in (pack.get("facts") or {}).items()
    ]
    facts_block = "\n".join(facts_lines) if facts_lines else "(no computed numbers available)"
    user = (
        f"RECOMMENDED ACTION (decided deterministically — explain, do not change): "
        f"{advice.get('recommended_action')}\n\n"
        f"FACTS (the ONLY numbers you may use):\n{facts_block}"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def _coerce_confidence(value) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return max(0.0, min(1.0, f))


_EMPTY = {"headline": None, "rationale": None, "confidence": 0.0, "citation": None}


def parse_response(content) -> dict:
    """Tolerantly parse the model reply into a draft dict. Defensive (honesty constraint): an
    unparseable reply collapses to an empty/zero-confidence draft so a malformed reply can never
    masquerade as a confident advisory."""
    if not content:
        return dict(_EMPTY)
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
        return dict(_EMPTY)
    headline = obj.get("headline")
    rationale = obj.get("rationale")
    citation = obj.get("citation")
    return {
        "headline": (None if headline in (None, "") else str(headline).strip()),
        "rationale": (None if rationale in (None, "") else str(rationale).strip()),
        "confidence": _coerce_confidence(obj.get("confidence")),
        "citation": (None if citation in (None, "", "null") else str(citation).strip()),
    }


def compose_draft(pack: dict, advice: dict, predict_fn, *, endpoint: str = DEFAULT_ENDPOINT, max_tokens: int = 500) -> dict:
    """Ask the model to word the advisory from the fact pack + the deterministic action."""
    raw = predict_fn(endpoint=endpoint, messages=build_messages(pack, advice), max_tokens=max_tokens)
    return parse_response(raw)


def _advisory_facts(advice: dict, signals: dict, offer_type: str | None) -> dict:
    """The fact subset that defines what the advisory NAMES — used by the deterministic gate to
    classify advice vs specific-offer. A concrete offer type WITH an amount names a concrete term
    (so the gate's suitability + disclosure checks fire); otherwise only the structure facts."""
    facts = dict(advice.get("facts") or {})
    if offer_type in _CONCRETE_OFFER_TYPES and signals.get("offer_amount") is not None:
        facts["offer_amount"] = signals["offer_amount"]
    return facts


def _review_status(grounded: bool, gate_status: str, confidence: float) -> str:
    """Grounding + gate + confidence → the deliverability disposition. Only a grounded, PASSing,
    confident advisory is APPLIED (deliverable, later + gated); everything else is held."""
    if not grounded:
        return C.ReviewStatus.REJECTED  # an invented number — never usable (§2.3)
    if gate_status == C.ComplianceStatus.BLOCKED:
        return C.ReviewStatus.REVIEW  # stored + auditable, but never auto-deliverable
    if confidence < C.AGENT_CONFIDENCE_REVIEW_MIN:
        return C.ReviewStatus.REVIEW
    return C.ReviewStatus.APPLIED


def compose_advisory(
    merchant_id: str,
    signals: dict,
    run_date,
    predict_fn,
    *,
    offer_type: str | None = None,
    governing_state=None,
    has_disclosure_block: bool = False,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> dict:
    """Compose ONE grounded, compliance-gated advisory for a merchant (the gold.merchant_advisory
    row shape). Pure orchestration (no Spark): fact pack → LLM draft → grounding → gate. The LLM
    is injected via `predict_fn`. STORED, not delivered."""
    from common.compliance.gate import compliance_gate

    advice = advise_structure(signals, offer_type)
    pack = build_fact_pack(signals, run_date, extra={"double_dip_cost": advice.get("double_dip_cost")})
    draft = compose_draft(pack, advice, predict_fn, endpoint=endpoint)

    text = " ".join(t for t in (draft.get("headline"), draft.get("rationale")) if t)
    grounded = not ungrounded_tokens(text, pack)

    advisory_for_gate = {
        "facts": _advisory_facts(advice, signals, offer_type),
        "recommended_action": advice.get("recommended_action"),
    }
    gate = compliance_gate(
        advisory_for_gate,
        governing_state,
        advice.get("suitability"),
        grounded=grounded,
        has_disclosure_block=has_disclosure_block,
    )

    confidence = draft.get("confidence", 0.0)
    review = _review_status(grounded, gate["status"], confidence)
    grounded_refs = json.dumps(
        {name: f["source_field"] for name, f in (pack.get("facts") or {}).items()}
    )

    return {
        "merchant_id": merchant_id,
        "advisory_run_date": run_date,
        "advisory_type": gate["output_type"],
        "headline": draft.get("headline"),
        "rationale": draft.get("rationale"),
        "recommended_action": advice.get("recommended_action"),
        "grounded_refs": grounded_refs,
        "confidence": confidence,
        "model_version": model_version,
        "compliance_status": gate["status"],
        "required_disclosures": ",".join(gate["required_disclosures"]) or None,
        "review_status": review,
    }


def build_advisory_rows(
    records: list[dict],
    run_date,
    predict_fn,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> list[dict]:
    """Pure batch orchestration (no Spark): compose one grounded, gated advisory per merchant
    record ``{merchant_id, signals, offer_type?, governing_state?, has_disclosure_block?}``.
    Spark-free → tier-1 testable with a fake model (mirrors common/agents.build_statement_rows)."""
    rows: list[dict] = []
    for r in records:
        rows.append(
            compose_advisory(
                r["merchant_id"],
                r.get("signals") or {},
                run_date,
                predict_fn,
                offer_type=r.get("offer_type"),
                governing_state=r.get("governing_state"),
                has_disclosure_block=bool(r.get("has_disclosure_block", False)),
                endpoint=endpoint,
                model_version=model_version,
            )
        )
    return rows
