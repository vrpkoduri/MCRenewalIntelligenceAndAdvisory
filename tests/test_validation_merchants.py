"""Scenario tests on the four validation merchants — carried from S0 (CLAUDE.md §8).

At S0 we can only assert the static-term + DQ-level expectations (no clock/rung yet).
Later sprints extend these same fixtures with lifecycle/rung assertions.
"""

from common.dq import predicates as dq
from tests.fixtures.validation_merchants import (
    ALL_MERCHANTS,
    ONE_BIG_PROMOTION,
    STARR_WINDOW_TINTING,
    WOLF_CORPORATION,
)


def test_all_four_present():
    names = {m["opportunity_name"] for m in ALL_MERCHANTS}
    assert names == {
        "Starr Window Tinting",
        "One Big Promotion",
        "Tom Snell",
        "Wolf Corporation",
    }


def test_rtr_consistent_for_each_fixture():
    for m in ALL_MERCHANTS:
        assert not dq.rtr_check_flag(
            m["funded_amount"], m["factor_rate"], m["payback_amount"]
        ), f"RTR mismatch in fixture {m['opportunity_name']}"


def test_one_big_promotion_has_missing_fico_and_mib():
    assert dq.is_missing_implausible_zero(ONE_BIG_PROMOTION["fico"]) is True
    assert dq.is_missing_implausible_zero(ONE_BIG_PROMOTION["months_in_business"]) is True


def test_starr_carries_default_cause_in_notes():
    assert "default" in STARR_WINDOW_TINTING["notes"].lower()


def test_wolf_is_renewal_and_upsized():
    assert WOLF_CORPORATION["deal_type"] == "Renewal"
    assert WOLF_CORPORATION["funded_amount"] == 40000.0
