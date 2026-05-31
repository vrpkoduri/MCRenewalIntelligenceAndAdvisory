import pytest

from common import constants as C
from common.io.guards import assert_no_surface, offending_surface_columns


def test_clean_columns_pass():
    cols = ["opportunity_id", "funded_amount", "factor_rate"]
    assert offending_surface_columns(cols) == []
    assert_no_surface(cols)  # does not raise


def test_sf_stored_columns_are_flagged():
    cols = ["opportunity_id", "_sf_stored_percentage_paid"]
    assert "_sf_stored_percentage_paid" in offending_surface_columns(cols)
    with pytest.raises(AssertionError):
        assert_no_surface(cols)


def test_all_known_no_surface_columns_caught():
    assert set(offending_surface_columns(C.NO_SURFACE_COLUMNS)) == set(C.NO_SURFACE_COLUMNS)
