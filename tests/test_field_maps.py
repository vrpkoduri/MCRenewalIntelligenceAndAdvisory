from common import constants as C
from common.field_maps import DEALS_MAP, FIELD_HISTORY_MAP, OFFERS_MAP, deals_silver_columns


def test_no_duplicate_silver_columns():
    cols = deals_silver_columns()
    assert len(cols) == len(set(cols)), "duplicate silver columns in deals map"


def test_opportunity_id_is_first_and_pk():
    assert DEALS_MAP[0].silver_col == "opportunity_id"
    assert DEALS_MAP[0].verdict == C.Verdict.HAVE


def test_sf_stored_columns_are_distrust_and_match_no_surface_set():
    distrust = {fs.silver_col for fs in DEALS_MAP if fs.verdict == C.Verdict.DISTRUST}
    assert distrust == set(C.NO_SURFACE_COLUMNS)


def test_every_distrust_column_uses_prefix():
    for fs in DEALS_MAP:
        if fs.verdict == C.Verdict.DISTRUST:
            assert fs.silver_col.startswith(C.SF_STORED_PREFIX)


def test_all_verdicts_are_known():
    known = {
        C.Verdict.HAVE,
        C.Verdict.CARRY,
        C.Verdict.DISTRUST,
        C.Verdict.DERIVE,
        C.Verdict.MUST_CAPTURE,
        C.Verdict.REUSE,
        C.Verdict.FUTURE,
    }
    for fs in (*DEALS_MAP, *FIELD_HISTORY_MAP, *OFFERS_MAP):
        assert fs.verdict in known, f"unknown verdict {fs.verdict!r} on {fs.silver_col}"


def test_economics_fields_present():
    cols = {fs.silver_col for fs in DEALS_MAP}
    for required in (
        "funded_amount",
        "factor_rate",
        "payback_amount",
        "payment_amount",
        "num_payments",
        "payment_frequency",
        "funded_date",
    ):
        assert required in cols
