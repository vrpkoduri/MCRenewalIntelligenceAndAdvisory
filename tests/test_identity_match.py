"""Tier-1 tests for the batch clustering core (common/identity/match).

Covers the ported AATM priority chain re-expressed as union-find clustering:
AUTO merges (MasterRecordId chains, exact Tax ID) collapse; weak tiers (phone,
name+state) are flagged candidates only (D-102). Edge cases mirror the
SPRINT_1_PLAN risk rows: blank tax id never matches; shared phone across
distinct merchants is a candidate, not an auto-merge.
"""

from __future__ import annotations

from common import constants as C
from common.identity.match import AccountKeys, cluster_accounts


def _k(sf_id, **kw):
    return AccountKeys.from_raw(sf_id, **kw)


def test_singletons_when_no_keys_match():
    accts = [
        _k("001A", tax_id_raw="11-1111111", name_raw="Alpha LLC", state_raw="NY"),
        _k("001B", tax_id_raw="22-2222222", name_raw="Beta Inc", state_raw="CA"),
    ]
    res = cluster_accounts(accts)
    assert res.n_accounts == 2
    assert res.n_merchants == 2
    assert res.collapse_ratio == 1.0
    assert res.reasons["001A"] == ()
    assert res.candidates == []


def test_exact_tax_id_auto_merges():
    accts = [
        _k("001A", tax_id_raw="12-3456789", name_raw="Wolf Corporation", state_raw="NJ"),
        _k("001B", tax_id_raw="123456789", name_raw="Wolf Corp", state_raw="NJ"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 1
    assert res.merchant_of["001A"] == res.merchant_of["001B"]
    assert C.Identity.TIER_TAX_ID in res.reasons["001A"]


def test_master_record_chain_auto_merges():
    # SF merged 001B into 001A: 001B.MasterRecordId -> 001A
    accts = [
        _k("001A", name_raw="One Big Promotion", state_raw="FL"),
        _k("001B", master_record_id="001A", name_raw="One Big Promotion LLC", state_raw="FL"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 1
    assert C.Identity.TIER_MASTER_RECORD in res.reasons["001B"]


def test_blank_tax_id_never_matches():
    # Two accounts with blank/None tax id must NOT collapse on the empty key.
    accts = [
        _k("001A", tax_id_raw="", name_raw="Starr Window Tinting", state_raw="TX"),
        _k("001B", tax_id_raw=None, name_raw="Tom Snell Co", state_raw="TX"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 2


def test_shared_phone_is_candidate_not_merge():
    # Distinct merchants sharing an ISO/office phone — flagged, NOT merged (D-102).
    accts = [
        _k("001A", name_raw="Alpha", state_raw="NY", phone_raw="(555) 123-4567"),
        _k("001B", name_raw="Beta", state_raw="NY", phone_raw="555-123-4567"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 2  # not merged
    assert ("001A", "001B", C.Identity.TIER_PHONE) in res.candidates


def test_name_state_is_candidate_not_merge():
    accts = [
        _k("001A", name_raw="Acme Corp", state_raw="New York"),
        _k("001B", name_raw="Acme Inc", state_raw="NY"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 2
    assert ("001A", "001B", C.Identity.TIER_NAME_STATE) in res.candidates


def test_no_duplicate_candidate_when_already_auto_merged():
    # Same tax id (auto-merge) AND same phone — phone must not also be flagged.
    accts = [
        _k("001A", tax_id_raw="12-3456789", name_raw="Wolf", state_raw="NJ",
           phone_raw="5551234567"),
        _k("001B", tax_id_raw="123456789", name_raw="Wolf Corp", state_raw="NJ",
           phone_raw="5551234567"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 1
    assert res.candidates == []


def test_canonical_key_is_deterministic_min():
    accts = [
        _k("001Z", tax_id_raw="12-3456789"),
        _k("001A", tax_id_raw="123456789"),
        _k("001M", tax_id_raw="1-2-3-4-5-6-7-8-9"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 1
    assert set(res.merchant_of.values()) == {"001A"}  # min sf_id is the canonical key


def test_transitive_tax_id_chain():
    # A~B by tax, B~C by master record -> one cluster of 3.
    accts = [
        _k("001A", tax_id_raw="999000999"),
        _k("001B", tax_id_raw="999-00-0999"),
        _k("001C", master_record_id="001B"),
    ]
    res = cluster_accounts(accts)
    assert res.n_merchants == 1
    assert len({res.merchant_of[s] for s in ("001A", "001B", "001C")}) == 1
