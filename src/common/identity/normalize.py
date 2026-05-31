"""Pure-Python identity-key normalizers (S1).

PORTED from the AATM merchant-sync IP (`jobs/lib/aatm_jobs` in the
`lakebase_aatm_*` workspace — D-105). AATM proved this logic in production
resolving daily lead extracts into canonical merchants; we reuse it rather
than re-derive it (CLAUDE.md 2.5: "the Merchant -> Opportunity linkage already
exists ... validate/extend, don't rebuild"; GENERAL_INSTRUCTIONS Rule 6).

What changed vs AATM:
- `normalize_email` dropped (MRI's `bronze.account` has no reliable merchant
  email key; AATM's name+email tier becomes name+state here — see match.py).
- `normalize_state` added (MRI keys on governing state, not phone/email).

Pure functions, no Spark / no I/O, so they run in tier-1 local tests AND inside
a Spark UDF in the batch matcher. Suffix list is centralized in
`constants.Identity.BUSINESS_SUFFIXES`.
"""

from __future__ import annotations

import re

from common import constants as C

_NON_DIGIT = re.compile(r"\D+")
_PHONE_EXTENSION = re.compile(r"\s*(?:ext\.?|extension|x|#)\s*\d+\s*$", re.IGNORECASE)
_SUFFIX_PATTERN = re.compile(
    r"[\s,\.]+(?:" + "|".join(C.Identity.BUSINESS_SUFFIXES) + r")\.?\s*$",
    re.IGNORECASE,
)


def normalize_tax_id(value: str | None) -> str | None:
    """Digits-only. `XX-XXXXXXX` -> `XXXXXXXXX`. None/blank/no-digits -> None.

    Blank/None must never become a match key (SPRINT_1_PLAN risk row).
    """
    if not value:
        return None
    digits = _NON_DIGIT.sub("", value)
    return digits or None


def normalize_business_name(value: str | None) -> str | None:
    """Lowercase, strip, iteratively drop common business suffixes
    (LLC/Inc/Corp/Ltd/Co/etc.). A lone "Inc" with no preceding separator is
    kept as-is (matches AATM behavior). None/blank -> None."""
    if not value:
        return None
    s = value.strip().lower()
    if not s:
        return None
    while True:
        new = _SUFFIX_PATTERN.sub("", s).strip(" ,.")
        if new == s:
            break
        s = new
    return s or None


def normalize_phone(value: str | None) -> str | None:
    """Digits-only, last 10. Strips `ext`/`extension`/`x`/`#` extensions first.
    Short (<10 digit) values are returned as-is, not None. None/blank/no-digits
    -> None."""
    if not value:
        return None
    cleaned = _PHONE_EXTENSION.sub("", value)
    digits = _NON_DIGIT.sub("", cleaned)
    if not digits:
        return None
    return digits[-10:] if len(digits) >= 10 else digits


# US state name -> USPS 2-letter, for folding free-text state values to a key.
_STATE_ABBR: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR",
}
_VALID_ABBR = frozenset(_STATE_ABBR.values())


def normalize_state(value: str | None) -> str | None:
    """Fold a state value to a USPS 2-letter code. Accepts already-2-letter
    codes (e.g. "ny" -> "NY") or full names ("New York" -> "NY"). Unknown /
    blank -> None (so a junk state never anchors a name+state match)."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    upper = s.upper()
    if len(upper) == 2 and upper in _VALID_ABBR:
        return upper
    return _STATE_ABBR.get(s.lower())
