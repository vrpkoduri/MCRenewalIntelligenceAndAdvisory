"""The grounded FACT PACK + grounding validator — D-802 / Framework §2.3 (S8).

The honesty constraint made executable: the Advisory Composer may speak ONLY numbers the spine
actually computed. `build_fact_pack` assembles those numbers from the merchant's gold `_current`
signals — each tagged with its source gold field + the run_date it came from — and is the ONLY
numeric vocabulary the advisory is allowed to use. `validate_grounding` then checks that every
number (and date) appearing in the composed advisory text is one of those values; an invented
number fails, so the advisory is REJECTED before it can ever be gated or delivered.

Percentages are stored as fractions on the spine (e.g. paydown 0.20) but read to a merchant as
"20%", so a `_pct` fact admits BOTH forms. Money tolerates `$`/`,` formatting (normalized away).
Dates are validated as whole ISO tokens against the pack's date facts.

Pure — no Spark, no LLM.
"""

from __future__ import annotations

import re
from datetime import date

# A numeric token: optional $, digits with optional thousands commas, optional decimals, optional %.
_NUM_RE = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")
# A whole ISO date (validated separately from bare numbers so 2026-08-22 isn't read as 2026/08/22).
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# (advisory vocabulary name, signal key, source gold field). Only NON-NULL signals are surfaced —
# a missing number is never fabricated. Extend as the advisory learns to speak new facts.
_SPEC = (
    ("est_paydown_pct", "est_paydown_pct", "merchant_clock_current.est_paydown_pct"),
    ("est_current_balance", "est_current_balance", "merchant_clock_current.est_current_balance"),
    ("factor_rate", "factor_rate", "gold.deals.factor_rate"),
    ("active_position_cnt", "active_position_cnt", "merchant_clock_current.active_position_cnt"),
    ("double_dip_cost", "double_dip_cost", "offer.structure.double_dip_cost"),
    ("offer_amount", "offer_amount", "merchant_offers_current.max_sustainable_advance"),
    ("payment_amount", "payment_amount", "gold.deals.payment_amount"),
    ("weekly_debit", "weekly_debit", "merchant_extraction_current.weekly_debit"),
    ("predicted_next_event_date", "predicted_next_event_date", "merchant_predictions_current.predicted_next_event_date"),
    ("est_renewal_eligible_date", "est_renewal_eligible_date", "merchant_clock_current.est_renewal_eligible_date"),
)


def _norm_num(token) -> str | None:
    """Normalize a numeric token to a canonical numeric string (strip $ , %). None if not numeric.
    Drops a trailing .0 so '3' and '3.0' compare equal."""
    t = str(token).strip().lstrip("$").rstrip("%").replace(",", "")
    try:
        f = float(t)
    except ValueError:
        return None
    return str(int(f)) if f == int(f) else str(f)


def _num_variants(value) -> set[str]:
    """Canonical forms a merchant-facing advisory may legitimately use for one computed number:
    the raw value plus its cent- and dollar-rounded forms. Rounding $20,879.9999 → $20,880 is the
    same number read to the merchant, NOT an invented one; genuinely different figures still fail."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return set()
    out: set[str] = set()
    # raw + cent/1-decimal/dollar-rounded — a merchant-facing "99.7%" for 99.72 or "$20,880" for
    # 20879.9999 is the SAME number read to the merchant, not an invented one; a genuinely different
    # figure still fails. (The composer prompt asks for verbatim numbers; this absorbs benign rounding.)
    for form in (f, round(f, 2), round(f, 1), float(round(f))):
        n = _norm_num(form)
        if n is not None:
            out.add(n)
    return out


def _is_date(value) -> bool:
    return isinstance(value, date) or bool(_ISO_DATE_RE.fullmatch(str(value).strip()))


def _fact(value, source_field: str, run_date) -> dict:
    return {"value": value, "source_field": source_field, "run_date": run_date}


def build_fact_pack(signals: dict, run_date, extra: dict | None = None) -> dict:
    """Assemble the grounded fact pack for one merchant from its gold `_current` signals (+ any
    `extra` computed facts, e.g. the Structure Advisor's double_dip_cost). Every included number
    is a value the spine computed, tagged with its source field + run_date. Returns
    ``{facts, allowed_numbers, allowed_dates, run_date}``.
    """
    merged = dict(signals or {})
    if extra:
        merged.update({k: v for k, v in extra.items() if v is not None})

    facts: dict[str, dict] = {}
    allowed_numbers: set[str] = set()
    allowed_dates: set[str] = set()

    for name, key, source_field in _SPEC:
        v = merged.get(key)
        if v is None:
            continue
        facts[name] = _fact(v, source_field, run_date)
        if _is_date(v):
            allowed_dates.add(str(v).strip())
            continue
        variants = _num_variants(v)
        if not variants:
            continue
        allowed_numbers |= variants
        # A percentage fact is read as both the stored fraction (0.2) and the ×100 form (20%).
        if name.endswith("_pct"):
            try:
                allowed_numbers |= _num_variants(float(v) * 100.0)
            except (TypeError, ValueError):
                pass

    return {
        "facts": facts,
        "allowed_numbers": allowed_numbers,
        "allowed_dates": allowed_dates,
        "run_date": run_date,
    }


def ungrounded_tokens(text, pack: dict) -> list[str]:
    """Return the numeric / date tokens in `text` NOT present in the fact pack (invented numbers).
    An empty list means the text is fully grounded (§2.3). Empty/blank text is trivially grounded."""
    if not text:
        return []
    text = str(text)
    allowed_numbers = pack.get("allowed_numbers") or set()
    allowed_dates = pack.get("allowed_dates") or set()
    offenders: list[str] = []

    # 1. Whole ISO dates first — each must be a fact-pack date.
    for d in _ISO_DATE_RE.findall(text):
        if d not in allowed_dates:
            offenders.append(d)
    remaining = _ISO_DATE_RE.sub(" ", text)  # remove dates so their parts aren't re-flagged

    # 2. Bare numeric tokens — each normalized value must be in the allowed set.
    for tok in _NUM_RE.findall(remaining):
        n = _norm_num(tok)
        if n is None:
            continue
        if n not in allowed_numbers:
            offenders.append(tok.strip())

    return offenders


def validate_grounding(text, pack: dict) -> bool:
    """True when every number/date in `text` is backed by the fact pack (no invented numbers)."""
    return not ungrounded_tokens(text, pack)
