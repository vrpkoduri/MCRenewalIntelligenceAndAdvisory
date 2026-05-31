"""Tier-2 reconciliation for the S1 gold layer: identity + canonical Deal Table.

Runs ON Databricks (needs Spark). Builds gold.merchant_crosswalk + gold.merchants
(identity resolution) and gold.deals (canonical Deal Table) into an ISOLATED test
schema (`gold_test` by default; prod `gold` requires allow_prod=True — Rule 5), then
asserts the SPRINT_1 exit criteria:

HARD (failures):
- schema contracts: gold.deals == deal_table_schema(); gold.merchants == merchant_schema();
  gold.merchant_crosswalk == merchant_crosswalk_schema();
- gold.deals row count == silver.deals row count (the whole funded book carries through);
- every deal has a non-null merchant_id (AccountId is 100% populated → no orphan deals);
- deal_id is unique (no deal maps to >1 merchant);
- crosswalk covers every funded SF Account and every row has a non-null merchant_id;
- no-surface guard: gold.deals exposes NO _sf_stored_* checkpoint column (CLAUDE.md 2.1);
- the four Must-capture gap columns are 100% null and their *_is_missing flags 100% true
  (never faked — CLAUDE.md 2.5).

DIAGNOSTIC (reported, not failed — legitimately drift):
- collapse ratio (accounts / merchants) and merge tier breakdown;
- azure_merchant_id fill rate (C-014 expects ~84% — AATM may be unreachable in test);
- term_months coverage; renewal-chain linkage; candidate-edge count;
- FU-002: the four reference merchants located by name and how many merchant_ids each
  collapsed to (a name appearing under >1 merchant_id is surfaced for review).
"""

from __future__ import annotations

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import (
    deal_table_schema,
    merchant_crosswalk_schema,
    merchant_schema,
)
from transform.gold_deals import build_gold_deals
from transform.gold_merchants import build_gold_merchants

# Reference merchants (CLAUDE.md §8). FU-002 = the by-name collapse check.
REFERENCE_MERCHANT_NAMES = (
    "Starr Window Tinting",
    "One Big Promotion",
    "Tom Snell",
    "Wolf Corporation",
)

GAP_FLAG_PAIRS = (
    ("disclosed_positions_cnt", "disclosed_positions_cnt_is_missing"),
    ("disclosed_balance_total", "disclosed_balance_total_is_missing"),
    ("net_funded", "net_funded_is_missing"),
    ("personal_guarantee", "personal_guarantee_is_missing"),
)


def run_recon(
    spark,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD_TEST,
    silver_schema: str = C.Schema.SILVER,
    bronze_schema: str = C.Schema.BRONZE,
    enrich_aatm: bool = True,
    allow_prod: bool = False,
) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to run tier-2 reconciliation against prod 'gold' without "
            "allow_prod=True. Use gold_test (Rule 5: this writes managed tables)."
        )

    findings: dict = {
        "catalog": catalog,
        "schema": schema,
        "silver_schema": silver_schema,
        "bronze_schema": bronze_schema,
    }

    # --- build the gold tables into the test schema (merchants first; deals reads them) ---
    merch_targets = build_gold_merchants(
        spark,
        catalog=catalog,
        schema=schema,
        bronze_schema=bronze_schema,
        silver_schema=silver_schema,
        enrich_aatm=enrich_aatm,
    )
    deals_target = build_gold_deals(
        spark, catalog=catalog, schema=schema, silver_schema=silver_schema
    )
    findings["targets"] = {**merch_targets, "deals": deals_target}

    deals = spark.read.table(deals_target)
    merchants = spark.read.table(merch_targets["merchants"])
    crosswalk = spark.read.table(merch_targets["crosswalk"])
    silver_deals = spark.read.table(C.fq(silver_schema, C.SilverTable.DEALS, catalog))

    # --- schema contracts (names + order) ---
    findings["deals_schema_matches"] = (
        deals.columns == [f.name for f in deal_table_schema().fields]
    )
    findings["merchants_schema_matches"] = (
        merchants.columns == [f.name for f in merchant_schema().fields]
    )
    findings["crosswalk_schema_matches"] = (
        crosswalk.columns == [f.name for f in merchant_crosswalk_schema().fields]
    )
    findings["deals_columns"] = deals.columns
    findings["merchants_columns"] = merchants.columns

    # --- counts + integrity ---
    silver_n = silver_deals.count()
    deals_n = deals.count()
    distinct_deal_ids = deals.select("deal_id").distinct().count()
    null_merchant = deals.where(F.col("merchant_id").isNull()).count()
    findings["silver_deals_count"] = silver_n
    findings["gold_deals_count"] = deals_n
    findings["distinct_deal_ids"] = distinct_deal_ids
    findings["deals_null_merchant_id"] = null_merchant

    funded_accts = silver_deals.select("merchant_sf_id").distinct().count()
    xwalk_n = crosswalk.count()
    xwalk_null_mid = crosswalk.where(F.col("merchant_id").isNull()).count()
    findings["funded_account_count"] = funded_accts
    findings["crosswalk_count"] = xwalk_n
    findings["crosswalk_null_merchant_id"] = xwalk_null_mid

    n_merchants = merchants.count()
    findings["merchant_count"] = n_merchants
    findings["collapse_ratio"] = round(xwalk_n / n_merchants, 4) if n_merchants else None

    # --- merge tier breakdown (collapse visibility) ---
    tier_rows = (
        merchants.groupBy("match_reason").count().collect()
    )
    findings["match_reason_breakdown"] = {
        (r["match_reason"] or "<singleton>"): int(r["count"]) for r in tier_rows
    }

    # --- no-surface guard on gold.deals ---
    findings["deals_surface_offenders"] = offending_surface_columns(deals.columns)

    # --- Must-capture gaps: 100% null value, 100% true flag (never faked) ---
    gap_checks = {}
    for value_col, flag_col in GAP_FLAG_PAIRS:
        non_null_vals = deals.where(F.col(value_col).isNotNull()).count()
        false_flags = deals.where(F.col(flag_col) != F.lit(True)).count()
        gap_checks[value_col] = {"non_null_values": non_null_vals, "non_true_flags": false_flags}
    findings["gap_checks"] = gap_checks

    # --- term_months coverage (Appendix A.3 derivation) ---
    findings["term_months_populated"] = deals.where(F.col("term_months").isNotNull()).count()
    findings["term_months_missing_flag"] = deals.where(
        F.col("term_months_is_missing") == F.lit(True)
    ).count()

    # --- renewal chain (D-103) ---
    findings["is_renewal_of_populated"] = deals.where(F.col("is_renewal_of").isNotNull()).count()
    findings["renewal_unlinkable"] = deals.where(
        F.col("renewal_unlinkable") == F.lit(True)
    ).count()
    findings["prior_factor_rate_populated"] = deals.where(
        F.col("prior_factor_rate").isNotNull()
    ).count()

    # --- azure_merchant_id fill (C-014 — diagnostic; ~84% on the funded book) ---
    azure_filled = merchants.where(F.col("azure_merchant_id").isNotNull()).count()
    findings["azure_merchant_id_filled"] = azure_filled
    findings["azure_fill_rate"] = round(azure_filled / n_merchants, 4) if n_merchants else None

    # --- FU-002: reference merchants by name -> # distinct merchant_ids each maps to ---
    ref = (
        merchants.filter(F.col("business_name").isin(list(REFERENCE_MERCHANT_NAMES)))
        .groupBy("business_name")
        .agg(F.countDistinct("merchant_id").alias("merchant_ids"))
        .collect()
    )
    findings["reference_merchants"] = {
        r["business_name"]: int(r["merchant_ids"]) for r in ref
    }

    return findings


def assert_recon(findings: dict) -> list[str]:
    """Hard expectations -> failures (empty == green). Diagnostics are not asserted."""
    failures: list[str] = []

    for key, label in (
        ("deals_schema_matches", "gold.deals"),
        ("merchants_schema_matches", "gold.merchants"),
        ("crosswalk_schema_matches", "gold.merchant_crosswalk"),
    ):
        if not findings.get(key):
            failures.append(f"schema drift on {label}")

    silver_n = findings.get("silver_deals_count")
    gold_n = findings.get("gold_deals_count")
    if silver_n != gold_n:
        failures.append(f"deal count: gold.deals={gold_n} != silver.deals={silver_n}")

    if findings.get("distinct_deal_ids") != gold_n:
        failures.append(
            f"deal_id not unique: distinct={findings.get('distinct_deal_ids')} != rows={gold_n}"
        )

    if findings.get("deals_null_merchant_id", 0) != 0:
        failures.append(
            f"{findings.get('deals_null_merchant_id')} deals have a null merchant_id"
        )

    if findings.get("crosswalk_null_merchant_id", 0) != 0:
        failures.append("crosswalk has rows with null merchant_id")

    if findings.get("crosswalk_count") != findings.get("funded_account_count"):
        failures.append(
            f"crosswalk coverage: {findings.get('crosswalk_count')} rows != "
            f"{findings.get('funded_account_count')} funded accounts"
        )

    offenders = findings.get("deals_surface_offenders") or []
    if offenders:
        failures.append(f"no-surface guard breached on gold.deals: {offenders}")

    for value_col, chk in (findings.get("gap_checks") or {}).items():
        if chk.get("non_null_values", 0) != 0:
            failures.append(f"gap {value_col} has {chk['non_null_values']} non-null values (faked?)")
        if chk.get("non_true_flags", 0) != 0:
            failures.append(f"gap {value_col} has {chk['non_true_flags']} rows with flag != true")

    # FU-002: a reference merchant name splitting across >1 merchant_id means we
    # UNDER-merged (real identity bug). Name not found (0) is only an Account-vs-
    # Opportunity name nuance — surfaced in findings, not a failure.
    for name, n_ids in (findings.get("reference_merchants") or {}).items():
        if n_ids > 1:
            failures.append(f"FU-002: '{name}' maps to {n_ids} merchant_ids (under-merged)")

    return failures
