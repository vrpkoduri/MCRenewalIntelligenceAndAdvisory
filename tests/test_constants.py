from common import constants as C


def test_catalog_is_mca_mri():
    assert C.CATALOG == "mca_mri"


def test_fq_builds_three_level_name():
    assert C.fq(C.Schema.SILVER, C.SilverTable.DEALS) == "mca_mri.silver.deals"


def test_no_surface_columns_all_use_sf_stored_prefix():
    for col in C.NO_SURFACE_COLUMNS:
        assert col.startswith(C.SF_STORED_PREFIX)


def test_deal_type_and_frequency_enums():
    assert C.DealType.ALL == {"New Business", "Renewal", "Buyout"}
    assert C.PaymentFrequency.ALL == {"Daily", "Weekly"}
