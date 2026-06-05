from common import constants as C


def test_catalog_is_mca_mri():
    assert C.CATALOG == "mca_mri"


def test_fq_builds_three_level_name():
    assert C.fq(C.Schema.SILVER, C.SilverTable.DEALS) == "mca_mri.silver.deals"


def test_no_surface_columns_all_use_sf_stored_prefix():
    for col in C.NO_SURFACE_COLUMNS:
        assert col.startswith(C.SF_STORED_PREFIX)


def test_deal_type_and_frequency_enums():
    # FU-601: real funded-book Type values include Stack / Add-On (Buyout valid but absent).
    assert C.DealType.ALL == {"New Business", "Renewal", "Buyout", "Stack", "Add-On"}
    assert C.PaymentFrequency.ALL == {"Daily", "Weekly"}


def test_deal_type_repeat_types_excludes_new_only():
    """FU-601: repeat advances = every Type EXCEPT New Business (Stack/Add-On are repeats)."""
    assert C.DealType.REPEAT_TYPES == {"Renewal", "Buyout", "Stack", "Add-On"}
    assert C.DealType.NEW not in C.DealType.REPEAT_TYPES
    for t in (C.DealType.RENEWAL, C.DealType.BUYOUT, C.DealType.STACK, C.DealType.ADD_ON):
        assert t in C.DealType.REPEAT_TYPES
    # REPEAT_TYPES is exactly ALL minus New Business — the single source for has_renewal.
    assert C.DealType.REPEAT_TYPES == C.DealType.ALL - {C.DealType.NEW}
