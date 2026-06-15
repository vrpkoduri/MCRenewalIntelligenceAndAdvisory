"""Statement Analyst LLM agent — the fuzzy half (Framework §5.9, S7 Phase 2, C-025/C-026).

The agent READS a bank statement's OCR'd text and EXTRACTS the structured streams the spine cannot
see: each recurring funder ACH-debit position (other funders + Morgan Cash's own), and the total
OPERATING-revenue deposits over the statement period. It does NOT count or do the math — the
deterministic tool (`positions.summarize_statement`, tier-1 tested) counts the concurrent positions,
sums the weekly debit, and normalizes deposits to weekly revenue; `grounding.make_extraction` grounds
+ gates each output. So the agent EXTRACTS; the deterministic tools COUNT; the clock COMPUTES.

C-026 guardrails baked in here:
 - **#1 advisory-only:** these extractions are recorded + surfaced but NEVER feed the S3 rung
   waterfall — nothing in this module (or the clock) routes burden into classification, so a covered
   merchant is not judged more harshly than an uncovered peer (D-306 preserved). The agent never
   touches the spine.
 - **#2 freshness:** a funding-moment statement is point-in-time; an extraction whose `as_of_date`
   is stale (`positions.statement_is_fresh`) is recorded but gated to REVIEW (not surfaced as current).
 - **#3 revenue softness:** the prompt admits ONLY operating-revenue deposits (excludes transfers /
   loan proceeds / owner injections), and `est_weekly_revenue`'s confidence is haircut
   (`STATEMENT_REVENUE_CONFIDENCE_HAIRCUT`) before the gate — so a revenue read needs more certainty
   to auto-apply than a positions/debit read.

The LLM call is INJECTED (`predict_fn`) so prompt-builder + parser + the pure `build_statement_rows`
orchestration are import-clean and tier-1 testable with a fake model; the Spark driver
(`transform/gold_statement_extraction.py`, gated) supplies the real Databricks chat client + the
OCR'd `silver.statement_text`.
"""

from __future__ import annotations

import json
import re

from common import constants as C
from common.agents.grounding import make_extraction
from common.agents.positions import statement_is_fresh, summarize_statement

DEFAULT_ENDPOINT = "databricks-claude-sonnet-4-5"
MODEL_VERSION = "statement-analyst/claude-sonnet-4-5/v1"

_SYSTEM = (
    "You are a credit-operations analyst for a merchant cash advance (MCA) brokerage. You read the "
    "OCR'd text of a merchant's BUSINESS BANK STATEMENT and extract — grounding EVERY value strictly "
    "in the statement text, never inventing or assuming — the following:\n\n"
    "1. POSITIONS: each recurring ACH-debit stream that is an MCA / funder advance repayment. For "
    "each, return: funder (the originator/company name as printed), payment_amount (the per-debit "
    "amount), payment_frequency (one of Daily, Weekly, Biweekly, Monthly), and is_morgan_cash (true "
    "ONLY if the originator is Morgan Cash itself; else false). Group repeated debits from the same "
    "funder into ONE position. Do NOT include one-off or non-advance debits (rent, payroll, utilities).\n"
    "2. deposits_operating_total: the total of OPERATING-REVENUE deposits over the period — sales, "
    "card settlements, customer payments. EXCLUDE transfers between the merchant's own accounts, loan "
    "or advance disbursements, owner capital injections, and refunds/reversals. When unsure whether a "
    "deposit is operating revenue, EXCLUDE it and lower your confidence.\n"
    "3. period_days: the number of days the statement covers; as_of_date: the statement's end date "
    "(YYYY-MM-DD).\n\n"
    "If the text is not a readable bank statement, return empty positions, null deposits, and low "
    "confidence. Respond with ONLY a JSON object and no other text:\n"
    "{\"positions\": [{\"funder\": \"<name>\", \"payment_amount\": <number>, "
    "\"payment_frequency\": \"<Daily|Weekly|Biweekly|Monthly>\", \"is_morgan_cash\": <bool>}], "
    "\"deposits_operating_total\": <number or null>, \"period_days\": <int or null>, "
    "\"as_of_date\": \"<YYYY-MM-DD or null>\", \"confidence\": <number 0..1>, "
    "\"citation\": \"<brief verbatim evidence from the statement>\"}"
)


def build_messages(statement_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f'Bank statement text:\n"""\n{statement_text}\n"""'},
    ]


def _coerce_float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None


def _coerce_confidence(value) -> float:
    f = _coerce_float(value)
    if f is None:
        return 0.0
    return max(0.0, min(1.0, f))


_EMPTY = {
    "positions": [], "deposits_operating_total": None, "period_days": None,
    "as_of_date": None, "confidence": 0.0, "citation": None,
}


def parse_response(content) -> dict:
    """Tolerantly parse the model reply into the extraction dict. Defensive (honesty constraint):
    anything unparseable collapses to an empty/zero-confidence result so a malformed reply can never
    masquerade as a confident extraction. Coerces position fields; drops malformed positions."""
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
    positions = []
    for p in obj.get("positions") or []:
        if not isinstance(p, dict):
            continue
        positions.append({
            "funder": (str(p["funder"]).strip() if p.get("funder") not in (None, "") else None),
            "payment_amount": _coerce_float(p.get("payment_amount")),
            "payment_frequency": p.get("payment_frequency"),
            "is_morgan_cash": bool(p.get("is_morgan_cash", False)),
        })
    citation = obj.get("citation")
    return {
        "positions": positions,
        "deposits_operating_total": _coerce_float(obj.get("deposits_operating_total")),
        "period_days": (int(obj["period_days"]) if str(obj.get("period_days") or "").strip().isdigit() else None),
        "as_of_date": (None if obj.get("as_of_date") in (None, "", "null") else str(obj.get("as_of_date"))),
        "confidence": _coerce_confidence(obj.get("confidence")),
        "citation": (None if citation in (None, "", "null") else str(citation)),
    }


def classify_statement(statement_text, predict_fn, *, endpoint: str = DEFAULT_ENDPOINT, max_tokens: int = 900) -> dict:
    """Extract positions + operating-revenue deposits from one statement's text via `predict_fn`.
    Blank text short-circuits to the empty result (no LLM call — nothing to ground on)."""
    if not statement_text or not str(statement_text).strip():
        return dict(_EMPTY)
    raw = predict_fn(endpoint=endpoint, messages=build_messages(str(statement_text)), max_tokens=max_tokens)
    return parse_response(raw)


def _row(merchant_id, deal_id, ext_type, value, confidence, source_ref, citation, model_version, run_date, fresh):
    """One grounded extraction row; stale statements (#2) are recorded but forced to REVIEW (not
    surfaced as current), and a None value can never be APPLIED."""
    ext = make_extraction(
        merchant_id, deal_id, ext_type, value, confidence, source_ref, model_version, run_date, citation=citation,
    )
    if ext["review_status"] == C.ReviewStatus.APPLIED and (not fresh or value is None):
        ext["review_status"] = C.ReviewStatus.REVIEW
    return ext


def build_statement_rows(
    records: list[dict],
    run_date,
    predict_fn,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> list[dict]:
    """Pure orchestration (no Spark): for each statement record
    {merchant_id, deal_id, statement_text, source_ref, as_of_date}, ask the model, run the
    deterministic counter (`summarize_statement`), and emit up to THREE grounded extraction rows —
    CONCURRENT_POSITIONS, WEEKLY_DEBIT, EST_WEEKLY_REVENUE. Freshness (#2) gates stale snapshots to
    REVIEW; the revenue confidence is haircut (#3). These rows are recorded + surfaced only — they
    NEVER feed the rung waterfall (#1). Lives here (Spark-free) so the whole path is tier-1 testable
    with a fake predict_fn; the transform is a thin wrapper (Rule 3)."""
    rows: list[dict] = []
    for r in records:
        merchant_id, deal_id = r["merchant_id"], r.get("deal_id")
        text = r.get("statement_text")
        proposal = classify_statement(text, predict_fn, endpoint=endpoint)
        summary = summarize_statement(
            proposal["positions"], proposal["deposits_operating_total"], proposal["period_days"],
        )
        as_of = proposal.get("as_of_date") or r.get("as_of_date")
        fresh = statement_is_fresh(as_of, run_date)
        base_ref = r.get("source_ref")
        source_ref = f"{base_ref}@{as_of}" if (base_ref and as_of) else base_ref
        conf = proposal["confidence"]
        citation = proposal.get("citation")

        rows.append(_row(merchant_id, deal_id, C.ExtractionType.CONCURRENT_POSITIONS,
                         summary["concurrent_positions"], conf, source_ref, citation, model_version, run_date, fresh))
        rows.append(_row(merchant_id, deal_id, C.ExtractionType.WEEKLY_DEBIT,
                         round(summary["total_weekly_debit"], 2), conf, source_ref, citation, model_version, run_date, fresh))
        rev = summary["est_weekly_revenue"]
        rows.append(_row(merchant_id, deal_id, C.ExtractionType.EST_WEEKLY_REVENUE,
                         None if rev is None else round(rev, 2),
                         conf * C.STATEMENT_REVENUE_CONFIDENCE_HAIRCUT,  # #3 — revenue is the softest signal
                         source_ref, citation, model_version, run_date, fresh))
    return rows
