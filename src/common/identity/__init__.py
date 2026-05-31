"""Identity resolution / entity matching (S1).

Matching logic is PORTED from the AATM `merchant_sync` IP (D-105 / C-013):
pure normalizers + a tiered priority chain, re-expressed for MRI as a batch
union-find dedup of `bronze.account`. MRI mints its OWN stable `merchant_id`
(persisted crosswalk, D-101) and additionally carries AATM's `azure_merchant_id`
(DM Merchant Id) as a cross-system join field (C-014).
"""

from common.identity.keys import (
    CrosswalkResult,
    assign_merchant_ids,
    mint_merchant_id,
)
from common.identity.match import (
    AccountKeys,
    ClusterResult,
    account_match_keys,
    cluster_accounts,
)
from common.identity.normalize import (
    normalize_business_name,
    normalize_phone,
    normalize_state,
    normalize_tax_id,
)

__all__ = [
    "normalize_tax_id",
    "normalize_business_name",
    "normalize_phone",
    "normalize_state",
    "AccountKeys",
    "ClusterResult",
    "cluster_accounts",
    "account_match_keys",
    "CrosswalkResult",
    "assign_merchant_ids",
    "mint_merchant_id",
]
