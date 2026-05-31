"""Tier-1 integrity tests for the optional silver maps: offers + field_history.

Pure-Python (no Spark): assert the maps are well-formed and encode the G1 source-of-truth
(C-012 underscored economics fields, PK ordering, FK presence) so the transforms can't
silently drift.
"""

from common.field_maps import (
    FIELD_HISTORY_MAP,
    OFFERS_MAP,
    field_history_silver_columns,
    offers_silver_columns,
)


# --- offers ------------------------------------------------------------------
def test_offers_no_duplicate_columns():
    cols = offers_silver_columns()
    assert len(cols) == len(set(cols)), "duplicate silver columns in OFFERS_MAP"


def test_offers_pk_first():
    assert OFFERS_MAP[0].silver_col == "offer_id"


def test_offers_has_opportunity_fk_and_selected_flag():
    cols = {fs.silver_col for fs in OFFERS_MAP}
    assert "opportunity_id" in cols  # FK back to the funded book
    assert "is_selected" in cols     # drives C-012 selected-offer resolution


def test_offers_economics_use_underscored_sources():
    src = {fs.silver_col: fs.sf_source for fs in OFFERS_MAP}
    # the underscored fields are authoritative; the non-underscored dupes are NOT mapped
    assert src["payback_amount"] == "Offer__c.Payback_Amount__c"
    assert src["payment_amount"] == "Offer__c.Payment_Amount__c"
    bad = {fs.sf_source for fs in OFFERS_MAP}
    assert "Offer__c.PaybackAmount__c" not in bad
    assert "Offer__c.PaymentAmnt__c" not in bad


# --- field_history -----------------------------------------------------------
def test_field_history_no_duplicate_columns():
    cols = field_history_silver_columns()
    assert len(cols) == len(set(cols)), "duplicate silver columns in FIELD_HISTORY_MAP"


def test_field_history_pk_first_and_has_fk():
    assert FIELD_HISTORY_MAP[0].silver_col == "history_id"
    cols = {fs.silver_col for fs in FIELD_HISTORY_MAP}
    assert "opportunity_id" in cols  # FK -> silver.deals.opportunity_id


def test_field_history_carries_event_essentials():
    cols = {fs.silver_col for fs in FIELD_HISTORY_MAP}
    for required in ("field", "old_value", "new_value", "changed_at", "changed_by"):
        assert required in cols
