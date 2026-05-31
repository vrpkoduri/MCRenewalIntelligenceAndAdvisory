"""Tier-1 tests for the ported identity normalizers (common/identity/normalize).

Tax-id / business-name / phone cases are ported verbatim from the AATM
`test_normalize.py` suite (the IP we reused — D-105) so we inherit its proven
edge coverage. `normalize_state` cases are MRI-specific (new in S1).
"""

from __future__ import annotations

import pytest

from common.identity.normalize import (
    normalize_business_name,
    normalize_phone,
    normalize_state,
    normalize_tax_id,
)


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("12-3456789", "123456789"),
        ("123456789", "123456789"),
        ("  12-3456789  ", "123456789"),
        ("XX-XXXXX1", "1"),
        (None, None),
        ("", None),
        ("abc-xyz", None),
    ],
)
def test_normalize_tax_id(inp, expected):
    assert normalize_tax_id(inp) == expected


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("Acme Corp", "acme"),
        ("Acme Corp Ltd.", "acme"),
        ("acme corp inc", "acme"),
        ("  Acme Inc  ", "acme"),
        ("Acme", "acme"),
        ("Café Latte LLC", "café latte"),
        (None, None),
        ("", None),
        ("Inc", "inc"),  # lone "inc" with no preceding separator stays as-is
    ],
)
def test_normalize_business_name(inp, expected):
    assert normalize_business_name(inp) == expected


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("(555) 123-4567", "5551234567"),
        ("+1 (555) 123-4567", "5551234567"),
        ("555-123-4567 ext 8", "5551234567"),
        ("555-123-4567x456", "5551234567"),
        ("5551234567", "5551234567"),
        ("12345", "12345"),  # short — return as-is, not None
        (None, None),
        ("", None),
        ("not a phone", None),
    ],
)
def test_normalize_phone(inp, expected):
    assert normalize_phone(inp) == expected


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("NY", "NY"),
        ("ny", "NY"),
        (" Ny ", "NY"),
        ("New York", "NY"),
        ("new york", "NY"),
        ("California", "CA"),
        ("District of Columbia", "DC"),
        ("Puerto Rico", "PR"),
        ("XX", None),  # not a real abbreviation
        ("Westeros", None),  # not a real state
        (None, None),
        ("", None),
    ],
)
def test_normalize_state(inp, expected):
    assert normalize_state(inp) == expected
