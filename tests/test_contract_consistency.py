"""Tie our code to the authoritative Data Contract xlsx so they cannot silently drift."""

import pytest

from common import contract

pytestmark = pytest.mark.filterwarnings("ignore")


def test_contract_workbook_is_readable():
    assert contract.contract_path().exists()
    fields = contract.deal_table_fields()
    assert len(fields) > 10


def test_deal_table_has_static_economics_fields():
    fields = contract.deal_table_fields()
    for required in (
        "funded_amount",
        "factor_rate",
        "total_payback",
        "num_payments",
        "payment_frequency",
        "payment_amount",
        "funded_date",
        "deal_type",
    ):
        assert required in fields, f"{required} missing from contract Deal Table"


def test_merchant_gold_recompute_fields_are_derive_not_stored():
    """The live-recomputed clock fields must be Derive in the contract (never trusted SF)."""
    fields = contract.merchant_gold_fields()
    for f in ("est_current_balance", "est_paydown_pct", "est_renewal_eligible_date"):
        assert fields.get(f) == "Derive", f"{f} should be Derive in the contract"


def test_must_capture_gaps_present_in_contract():
    """The S0 audit must-capture gaps should appear as Must-capture in the contract."""
    merch = contract.merchant_gold_fields()
    for f in ("ein", "est_weekly_revenue", "stress_event_cnt", "consent_sms", "consent_email"):
        assert merch.get(f) == "Must-capture", f"{f} expected Must-capture"
