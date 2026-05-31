"""Entity matching / clustering for canonical merchant identity (S1).

PORTED + ADAPTED from AATM `resolve_merchant` (D-105 / C-013). AATM resolves one
incoming lead at a time against a mutable Postgres `merchants` table using a
first-match-wins priority chain. MRI instead does a **batch dedup** of the
Salesforce `Account` rows on the funded book — there is no external
`azure_merchant_id` key and no pre-existing canonical table to match against — so
the same priority chain is re-expressed as **edge generation + union-find
clustering**:

    AATM tier                     -> MRI edge (this module)
    1. azure_merchant_id (exact)  -> MasterRecordId merge chains (SF-native)   [AUTO]
    2. tax_id (exact)             -> exact normalized Tax ID                    [AUTO]
    3. name + phone               -> exact normalized phone                    [candidate]
    4. name + email               -> normalized name + governing state         [candidate]

Per D-102 (conservative v1): only AUTO tiers collapse rows into one cluster;
CANDIDATE tiers emit flagged pairs for human review and do **not** merge.

The clustering core (`cluster_accounts`) is pure-Python (no Spark, no I/O) and
operates on the small funded-merchant universe (≤ a few thousand accounts), so it
runs in tier-1 tests AND on the driver after a Spark collect. `account_match_keys`
is the thin Spark adapter that projects + normalizes `bronze.account`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from common import constants as C
from common.identity.normalize import (
    normalize_business_name,
    normalize_phone,
    normalize_state,
    normalize_tax_id,
)


@dataclass(frozen=True)
class AccountKeys:
    """One Salesforce Account reduced to its normalized identity keys.

    `merchant_sf_id` is the Account.Id (the row identity we cluster).
    `master_record_id` is SF's own merge pointer (when SF merged two Accounts,
    the loser keeps a MasterRecordId pointing at the survivor) — a free,
    authoritative merge signal we trust as an auto-merge edge.
    """

    merchant_sf_id: str
    master_record_id: str | None = None
    tax_id: str | None = None  # already normalized
    phone: str | None = None  # already normalized
    name: str | None = None  # already normalized business name
    state: str | None = None  # already normalized 2-letter

    @classmethod
    def from_raw(
        cls,
        merchant_sf_id: str,
        *,
        master_record_id: str | None = None,
        tax_id_raw: str | None = None,
        name_raw: str | None = None,
        phone_raw: str | None = None,
        state_raw: str | None = None,
    ) -> AccountKeys:
        """Build from raw SF field values, applying the ported normalizers."""
        return cls(
            merchant_sf_id=merchant_sf_id,
            master_record_id=(master_record_id or None),
            tax_id=normalize_tax_id(tax_id_raw),
            phone=normalize_phone(phone_raw),
            name=normalize_business_name(name_raw),
            state=normalize_state(state_raw),
        )


@dataclass(frozen=True)
class ClusterResult:
    """Output of `cluster_accounts`.

    - `merchant_of`: merchant_sf_id -> canonical cluster key (the min sf_id in the
      cluster; deterministic and stable for a fixed input). `keys.py` maps this to
      a persisted, never-re-keyed `merchant_id` (D-101).
    - `reasons`: merchant_sf_id -> sorted tuple of AUTO tiers that pulled it into
      its cluster ('master_record'/'tax_id'); empty tuple = singleton (matched to
      nothing → its own merchant).
    - `candidates`: flagged weak-tier pairs (phone / name+state) that were NOT
      auto-merged — the S1 review queue (D-102). Each: (sf_id_a, sf_id_b, tier).
    """

    merchant_of: dict[str, str]
    reasons: dict[str, tuple[str, ...]] = field(default_factory=dict)
    candidates: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def n_accounts(self) -> int:
        return len(self.merchant_of)

    @property
    def n_merchants(self) -> int:
        return len(set(self.merchant_of.values()))

    @property
    def collapse_ratio(self) -> float:
        """accounts / merchants (1.0 = no merges; higher = more dedup)."""
        m = self.n_merchants
        return (self.n_accounts / m) if m else 0.0


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self._parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # path compression
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # deterministic: smaller id becomes the root
        hi, lo = (ra, rb) if ra > rb else (rb, ra)
        self._parent[hi] = lo


def cluster_accounts(accounts: list[AccountKeys]) -> ClusterResult:
    """Cluster accounts into canonical merchants. Pure function (tier-1 testable).

    AUTO edges (collapse into one merchant — D-102):
      - MasterRecordId merge chains: an account whose master_record_id points at
        another account in the set is unioned with it.
      - exact normalized Tax ID: all accounts sharing a non-blank tax_id are unioned.

    CANDIDATE edges (flagged, NOT merged in v1):
      - exact normalized phone shared across accounts not already auto-merged.
      - normalized name + governing state shared across accounts not already
        auto-merged.

    Blank/None keys never match (a missing tax id is not a join key).
    """
    uf = _UnionFind()
    sf_ids = {a.merchant_sf_id for a in accounts}
    reason_edges: dict[str, set[str]] = {a.merchant_sf_id: set() for a in accounts}
    for a in accounts:
        uf.add(a.merchant_sf_id)

    # --- AUTO tier 1: SF MasterRecordId merge chains ---
    for a in accounts:
        mrid = a.master_record_id
        if mrid and mrid in sf_ids and mrid != a.merchant_sf_id:
            uf.union(a.merchant_sf_id, mrid)
            reason_edges[a.merchant_sf_id].add(C.Identity.TIER_MASTER_RECORD)
            reason_edges[mrid].add(C.Identity.TIER_MASTER_RECORD)

    # --- AUTO tier 2: exact normalized Tax ID ---
    by_tax: dict[str, list[str]] = {}
    for a in accounts:
        if a.tax_id:
            by_tax.setdefault(a.tax_id, []).append(a.merchant_sf_id)
    for ids in by_tax.values():
        if len(ids) > 1:
            anchor = ids[0]
            for other in ids[1:]:
                uf.union(anchor, other)
            for sid in ids:
                reason_edges[sid].add(C.Identity.TIER_TAX_ID)

    # Canonical key = min sf_id per cluster (deterministic).
    merchant_of = {sid: uf.find(sid) for sid in sf_ids}

    # --- CANDIDATE tiers: flag weak matches that DID NOT already auto-merge ---
    candidates: list[tuple[str, str, str]] = []

    def _emit_candidates(groups: dict[str, list[str]], tier: str) -> None:
        for ids in groups.values():
            if len(ids) < 2:
                continue
            ids_sorted = sorted(ids)
            for i in range(len(ids_sorted)):
                for j in range(i + 1, len(ids_sorted)):
                    a, b = ids_sorted[i], ids_sorted[j]
                    if merchant_of[a] != merchant_of[b]:  # not already merged
                        candidates.append((a, b, tier))

    by_phone: dict[str, list[str]] = {}
    for a in accounts:
        if a.phone:
            by_phone.setdefault(a.phone, []).append(a.merchant_sf_id)
    _emit_candidates(by_phone, C.Identity.TIER_PHONE)

    by_name_state: dict[tuple[str, str], list[str]] = {}
    for a in accounts:
        if a.name and a.state:
            by_name_state.setdefault((a.name, a.state), []).append(a.merchant_sf_id)
    _emit_candidates(
        {f"{k[0]}|{k[1]}": v for k, v in by_name_state.items()},
        C.Identity.TIER_NAME_STATE,
    )

    reasons = {sid: tuple(sorted(edges)) for sid, edges in reason_edges.items()}
    return ClusterResult(merchant_of=merchant_of, reasons=reasons, candidates=candidates)


def account_match_keys(account_df, opp_df=None):
    """Spark adapter: project `bronze.account` to AccountKeys-shaped rows with
    normalized keys, optionally restricted to the funded-merchant universe.

    Returns a Spark DataFrame with columns:
      merchant_sf_id, master_record_id, tax_id, phone, name, state
    plus the raw business name (`business_name_raw`) the merchant dimension keeps.

    If `opp_df` (funded Opportunities) is given, restricts to accounts referenced
    by a funded opportunity's AccountId — S1 only needs merchants that have deals.
    The normalizers are wrapped as UDFs so the same pure code runs on Spark.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    n_tax = F.udf(normalize_tax_id, StringType())
    n_phone = F.udf(normalize_phone, StringType())
    n_name = F.udf(normalize_business_name, StringType())
    n_state = F.udf(normalize_state, StringType())

    df = account_df
    if opp_df is not None:
        funded_accts = opp_df.select(F.col("AccountId").alias("merchant_sf_id")).distinct()
        df = df.join(
            funded_accts, df["Id"] == funded_accts["merchant_sf_id"], "left_semi"
        )

    tax_raw = F.coalesce(F.col("Key_Reference_Tax_Id__c"), F.col("Tax_ID__c"))
    phone_raw = F.coalesce(F.col("Key_Ref_Merchant_Phone__c"), F.col("Phone"))
    state_raw = F.coalesce(F.col("Business_State__c"), F.col("BillingState"))

    return df.select(
        F.col("Id").alias("merchant_sf_id"),
        F.col("MasterRecordId").alias("master_record_id"),
        n_tax(tax_raw).alias("tax_id"),
        n_phone(phone_raw).alias("phone"),
        n_name(F.col("Name")).alias("name"),
        n_state(state_raw).alias("state"),
        F.col("Name").alias("business_name_raw"),
    )
