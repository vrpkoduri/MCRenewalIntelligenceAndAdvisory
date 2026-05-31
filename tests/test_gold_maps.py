"""Tier-1 tests for the S1 gold field maps + schema column lists.

Pin the gold Deal Table to the authoritative Data Contract (cannot silently drift),
and lock the identity/enrichment invariants from C-014 (own merchant_id + carried
azure_merchant_id) and the no-surface guard (S2 recompute fields never leak into S1).
"""

from __future__ import annotations

import pytest

from common import constants as C
from common import contract
from common.field_maps import (
    DEAL_TABLE_MAP,
    GOLD_DEALS_DQ_COLUMNS,
    GOLD_MERCHANTS_DQ_COLUMNS,
    MERCHANT_CROSSWALK_MAP,
    MERCHANT_MAP,
    deal_table_columns,
    merchant_columns,
    merchant_crosswalk_columns,
)

pytestmark = pytest.mark.filterwarnings("ignore")

_KNOWN_VERDICTS = {
    C.Verdict.HAVE,
    C.Verdict.CARRY,
    C.Verdict.DISTRUST,
    C.Verdict.DERIVE,
    C.Verdict.MUST_CAPTURE,
    C.Verdict.REUSE,
    C.Verdict.FUTURE,
}


# --- Deal Table conforms to the Data Contract -------------------------------


def test_deal_table_matches_contract_fields_in_order():
    """gold.deals must be exactly the contract 'Deal Table' fields, same order."""
    contract_fields = list(contract.deal_table_fields().keys())
    map_fields = [fs.silver_col for fs in DEAL_TABLE_MAP]
    assert map_fields == contract_fields


def test_deal_id_is_pk_first_and_have():
    assert DEAL_TABLE_MAP[0].silver_col == "deal_id"
    assert DEAL_TABLE_MAP[0].verdict == C.Verdict.HAVE


def test_all_gold_verdicts_known():
    for fs in (*DEAL_TABLE_MAP, *MERCHANT_MAP, *MERCHANT_CROSSWALK_MAP):
        assert fs.verdict in _KNOWN_VERDICTS, f"unknown verdict {fs.verdict!r} on {fs.silver_col}"


# --- No-surface guard: S2 clock fields never leak into the S1 gold ----------


def test_no_sf_stored_columns_in_gold_deals():
    cols = {fs.silver_col for fs in DEAL_TABLE_MAP}
    for banned in C.NO_SURFACE_COLUMNS:
        assert banned not in cols
    assert not any(c.startswith(C.SF_STORED_PREFIX) for c in cols)


def test_holdback_pct_deferred_to_s2_not_trusted():
    """holdback_pct needs the S2 clock — it is Derive and sourced as defer:S2 (null in S1)."""
    fs = next(f for f in DEAL_TABLE_MAP if f.silver_col == "holdback_pct")
    assert fs.verdict == C.Verdict.DERIVE
    assert fs.sf_source.startswith("defer:S2")


# --- Identity invariants (C-014 / D-101) ------------------------------------


def test_merchant_id_is_pk_and_derive():
    assert MERCHANT_MAP[0].silver_col == "merchant_id"
    assert MERCHANT_MAP[0].verdict == C.Verdict.DERIVE


def test_azure_merchant_id_carried_from_aatm():
    """C-014: MRI mints its own id but CARRIES AATM's azure_merchant_id as the bridge."""
    fs = next(f for f in MERCHANT_MAP if f.silver_col == "azure_merchant_id")
    assert fs.verdict == C.Verdict.CARRY
    assert fs.sf_source.startswith("aatm:")
    # Optional enrichment -> must have a missing flag (degrades to null, never faked).
    flags = {name for name, _ in GOLD_MERCHANTS_DQ_COLUMNS}
    assert "azure_merchant_id_is_missing" in flags


def test_crosswalk_is_sf_id_to_merchant_id():
    cols = [fs.silver_col for fs in MERCHANT_CROSSWALK_MAP]
    assert cols == ["merchant_sf_id", "merchant_id"]
    assert MERCHANT_CROSSWALK_MAP[0].verdict == C.Verdict.HAVE


# --- Must-capture gaps each carry a *_is_missing flag (never faked 0/blank) --


def test_must_capture_gaps_have_missing_flags():
    gap_fields = {
        fs.silver_col for fs in DEAL_TABLE_MAP if fs.sf_source == "gap"
    }
    deal_flags = {name for name, _ in GOLD_DEALS_DQ_COLUMNS}
    for g in gap_fields:
        assert f"{g}_is_missing" in deal_flags, f"{g} gap missing its *_is_missing flag"


# --- Column helpers / schema column lists -----------------------------------


def test_column_helpers_have_no_duplicates():
    for cols in (deal_table_columns(), merchant_columns(), merchant_crosswalk_columns()):
        assert len(cols) == len(set(cols))


def test_deal_table_columns_include_contract_fields_plus_dq():
    cols = deal_table_columns()
    assert len(cols) == len(DEAL_TABLE_MAP) + len(GOLD_DEALS_DQ_COLUMNS)
    for fs in DEAL_TABLE_MAP:
        assert fs.silver_col in cols
