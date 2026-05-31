from datetime import date, datetime

from common.dq import predicates as dq


class TestMissingImplausibleZero:
    def test_zero_is_missing(self):
        assert dq.is_missing_implausible_zero(0) is True

    def test_none_is_missing(self):
        assert dq.is_missing_implausible_zero(None) is True

    def test_blank_string_is_missing(self):
        assert dq.is_missing_implausible_zero("   ") is True

    def test_real_value_is_present(self):
        assert dq.is_missing_implausible_zero(520) is False
        assert dq.is_missing_implausible_zero(56) is False


class TestDateSanity:
    # C-007: flag a LARGE gap in EITHER direction (default 365d). Replaces the literal
    # funded>created rule, which both over-flagged normal latency and MISSED the real
    # migration artifact (funded 2020 / created 2022, i.e. funded < created).
    def test_migration_artifact_funded_before_created_flags(self):
        assert dq.date_sanity_flag(date(2020, 6, 1), datetime(2022, 6, 1)) is True

    def test_large_gap_funded_after_created_flags(self):
        assert dq.date_sanity_flag(date(2024, 6, 1), datetime(2020, 1, 1)) is True

    def test_normal_small_gap_does_not_flag(self):
        # a few days/weeks of create->fund latency is normal, either ordering
        assert dq.date_sanity_flag(date(2023, 6, 1), datetime(2023, 5, 20)) is False
        assert dq.date_sanity_flag(date(2020, 3, 10), datetime(2020, 3, 1)) is False

    def test_nulls_do_not_flag(self):
        assert dq.date_sanity_flag(None, datetime(2023, 1, 1)) is False
        assert dq.date_sanity_flag(date(2023, 1, 1), None) is False

    def test_threshold_is_configurable(self):
        # ~214-day gap: under the 365d default, over a tighter 90d threshold
        assert dq.date_sanity_flag(date(2023, 1, 1), datetime(2022, 6, 1)) is False
        assert dq.date_sanity_flag(date(2023, 1, 1), datetime(2022, 6, 1), gap_days=90) is True


class TestRtrCheck:
    def test_consistent_rtr_no_flag(self):
        # 25000 * 1.49 == 37250 exactly
        assert dq.rtr_check_flag(25000.0, 1.49, 37250.0) is False
        assert dq.rtr_check_delta(25000.0, 1.49, 37250.0) == 0.0

    def test_inconsistent_rtr_flags(self):
        assert dq.rtr_check_flag(25000.0, 1.49, 30000.0) is True

    def test_missing_inputs_do_not_flag(self):
        assert dq.rtr_check_flag(None, 1.49, 37250.0) is False
        assert dq.rtr_check_delta(25000.0, None, 37250.0) is None
