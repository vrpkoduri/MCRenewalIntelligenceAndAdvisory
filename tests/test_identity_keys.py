"""Tier-1 tests for merchant_id minting + persisted crosswalk (common/identity/keys).

Focus: STABILITY (D-101) — ids never re-key on re-merge; a merge keeps the
smallest surviving id rather than minting a new one.
"""

from __future__ import annotations

from common.identity.keys import (
    assign_merchant_ids,
    match_reason_by_merchant,
    mint_merchant_id,
)
from common.identity.match import AccountKeys, cluster_accounts


def _cluster(*accts):
    return cluster_accounts(list(accts))


def _k(sf_id, **kw):
    return AccountKeys.from_raw(sf_id, **kw)


def test_first_build_mints_from_canonical_min_sf_id():
    cl = _cluster(
        _k("001Z", tax_id_raw="12-3456789"),
        _k("001A", tax_id_raw="123456789"),
    )
    res = assign_merchant_ids(cl, existing_crosswalk=None)
    assert res.crosswalk["001A"] == res.crosswalk["001Z"] == mint_merchant_id("001A")
    assert res.minted == ["MRI-001A"]
    assert res.superseded == {}


def test_singletons_each_get_their_own_id():
    cl = _cluster(_k("001A", tax_id_raw="111111111"), _k("001B", tax_id_raw="222222222"))
    res = assign_merchant_ids(cl)
    assert res.crosswalk["001A"] != res.crosswalk["001B"]
    assert len(res.merchant_members) == 2


def test_existing_id_is_reused_not_reminted():
    cl = _cluster(
        _k("001A", tax_id_raw="123456789"),
        _k("001B", tax_id_raw="123456789"),
    )
    existing = {"001A": "MRI-OLD", "001B": "MRI-OLD"}
    res = assign_merchant_ids(cl, existing_crosswalk=existing)
    assert res.crosswalk["001A"] == res.crosswalk["001B"] == "MRI-OLD"
    assert res.minted == []


def test_new_account_joins_existing_merchant_keeps_id():
    # 001A had an id; 001C newly appears in the same tax-id cluster -> inherits it.
    cl = _cluster(
        _k("001A", tax_id_raw="123456789"),
        _k("001C", tax_id_raw="123456789"),
    )
    existing = {"001A": "MRI-001A"}
    res = assign_merchant_ids(cl, existing_crosswalk=existing)
    assert res.crosswalk["001C"] == "MRI-001A"
    assert res.minted == []  # no new id minted


def test_merge_keeps_smallest_id_and_reports_superseded():
    # Two formerly-distinct merchants (MRI-001A, MRI-001B) now share a tax id ->
    # one cluster. Smallest id survives; the other is reported superseded.
    cl = _cluster(
        _k("001A", tax_id_raw="123456789"),
        _k("001B", tax_id_raw="123456789"),
    )
    existing = {"001A": "MRI-001A", "001B": "MRI-001B"}
    res = assign_merchant_ids(cl, existing_crosswalk=existing)
    survivor = res.crosswalk["001A"]
    assert res.crosswalk["001B"] == survivor == "MRI-001A"
    assert res.superseded == {"MRI-001B": "MRI-001A"}
    assert res.minted == []


def test_match_reason_tax_id_cluster_and_singleton_blank():
    # 001A+001B share a tax id (auto-merge tier=tax_id); 001C is a singleton (blank).
    cl = _cluster(
        _k("001A", tax_id_raw="123456789"),
        _k("001B", tax_id_raw="123456789"),
        _k("001C", tax_id_raw="999999999"),
    )
    res = assign_merchant_ids(cl)
    reasons = match_reason_by_merchant(res.crosswalk, cl)
    merged = res.crosswalk["001A"]
    single = res.crosswalk["001C"]
    assert reasons[merged] == "tax_id"
    assert reasons[single] == ""  # matched nothing


def test_match_reason_unions_tiers_across_cluster():
    # master_record edge + tax_id edge in one cluster -> sorted "master_record+tax_id".
    cl = _cluster(
        _k("001A", tax_id_raw="123456789"),
        _k("001B", master_record_id="001A"),
        _k("001C", tax_id_raw="123456789"),
    )
    res = assign_merchant_ids(cl)
    reasons = match_reason_by_merchant(res.crosswalk, cl)
    mid = res.crosswalk["001A"]
    assert reasons[mid] == "master_record+tax_id"


def test_every_account_present_in_crosswalk():
    cl = _cluster(
        _k("001A", tax_id_raw="111111111"),
        _k("001B", master_record_id="001A"),
        _k("001C", tax_id_raw="222222222"),
    )
    res = assign_merchant_ids(cl)
    assert set(res.crosswalk) == {"001A", "001B", "001C"}
    # 001A+001B merged, 001C alone -> 2 merchants
    assert len(res.merchant_members) == 2
