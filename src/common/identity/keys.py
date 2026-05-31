"""merchant_id minting + persisted crosswalk (S1 — D-101).

D-101 chose a **persisted crosswalk** (`gold.merchant_crosswalk`: merchant_sf_id
-> merchant_id) over a deterministic hash, so a `merchant_id` never re-keys when
clusters change across daily refreshes — it is the stable join key every later
sprint depends on (SPRINT_1_PLAN §6).

Stability rules (`assign_merchant_ids`, pure / tier-1 testable):
  - A cluster that already has exactly one known merchant_id reuses it.
  - A cluster spanning *several* previously-distinct merchant_ids (two old
    merchants merged) keeps the lexicographically smallest as the survivor and
    remaps the rest to it — no new id is minted, so existing downstream joins
    stay valid (the superseded ids are reported for awareness).
  - A brand-new cluster mints `MRI-<canonical_sf_id>` once, then persists it.
    Basing the mint on the canonical (min) sf_id makes the first mint
    deterministic and traceable to a real Account; persistence means later
    canonical-key shifts cannot re-key it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from common.identity.match import ClusterResult

MERCHANT_ID_PREFIX = "MRI-"


def match_reason_by_merchant(
    crosswalk: dict[str, str],
    cluster: ClusterResult,
) -> dict[str, str]:
    """merchant_id -> "master_record+tax_id": the union of AUTO tiers that formed the
    merchant's cluster, sorted and joined with '+'. Blank for singletons (matched to
    nothing). Pure — feeds the gold.merchants `match_reason` column (collapse visibility).
    """
    merchant_tiers: dict[str, set[str]] = defaultdict(set)
    for sf_id, mid in crosswalk.items():
        merchant_tiers[mid].update(cluster.reasons.get(sf_id, ()))
    return {mid: "+".join(sorted(tiers)) for mid, tiers in merchant_tiers.items()}


def mint_merchant_id(canonical_sf_id: str) -> str:
    """Deterministic first-mint id from a cluster's canonical (min) sf_id."""
    return f"{MERCHANT_ID_PREFIX}{canonical_sf_id}"


@dataclass(frozen=True)
class CrosswalkResult:
    """Output of `assign_merchant_ids`.

    - `crosswalk`: merchant_sf_id -> merchant_id (the full, persisted mapping).
    - `merchant_members`: merchant_id -> sorted list of its merchant_sf_ids.
    - `superseded`: merchant_id -> survivor merchant_id, for any old id absorbed
      by a merge this refresh (downstream may remap; reported, never silently
      dropped).
    - `minted`: merchant_ids newly created this refresh.
    """

    crosswalk: dict[str, str]
    merchant_members: dict[str, list[str]] = field(default_factory=dict)
    superseded: dict[str, str] = field(default_factory=dict)
    minted: list[str] = field(default_factory=list)


def assign_merchant_ids(
    cluster: ClusterResult,
    existing_crosswalk: dict[str, str] | None = None,
) -> CrosswalkResult:
    """Map every account to a stable merchant_id given the new clustering and the
    previously-persisted crosswalk (empty on the first build).

    Pure function — no Spark, no I/O.
    """
    existing = dict(existing_crosswalk or {})

    # Group accounts by their new canonical cluster key.
    clusters: dict[str, list[str]] = defaultdict(list)
    for sf_id, canon in cluster.merchant_of.items():
        clusters[canon].append(sf_id)

    crosswalk: dict[str, str] = {}
    superseded: dict[str, str] = {}
    minted: list[str] = []

    for canon, members in clusters.items():
        members_sorted = sorted(members)
        known = sorted({existing[s] for s in members_sorted if s in existing})

        if not known:
            mid = mint_merchant_id(canon)
            minted.append(mid)
        else:
            mid = known[0]  # lexicographically smallest survives a merge
            for old in known[1:]:
                superseded[old] = mid

        for s in members_sorted:
            crosswalk[s] = mid

    members_by_merchant: dict[str, list[str]] = defaultdict(list)
    for s, mid in crosswalk.items():
        members_by_merchant[mid].append(s)
    merchant_members = {mid: sorted(s) for mid, s in members_by_merchant.items()}

    return CrosswalkResult(
        crosswalk=crosswalk,
        merchant_members=merchant_members,
        superseded=superseded,
        minted=sorted(minted),
    )
