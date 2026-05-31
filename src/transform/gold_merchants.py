"""Silver/Bronze -> Gold: build mca_mri.gold.merchants + mca_mri.gold.merchant_crosswalk.

S1 identity resolution (D-101/D-102/C-013/C-014). Pipeline:

  1. Project bronze.account to normalized match keys (common.identity.match), restricted
     to the funded-merchant universe (AccountIds referenced by silver.deals).
  2. Collect to the driver and run the PURE clustering + id assignment
     (cluster_accounts -> assign_merchant_ids), seeded with the previously-persisted
     crosswalk so merchant_ids never re-key (D-101 stability).
  3. Persist gold.merchant_crosswalk (merchant_sf_id -> merchant_id).
  4. Build the gold.merchants dimension: one row per merchant_id with a representative
     static profile (deterministic: first non-null by ascending sf_id), cluster size,
     and the AUTO match_reason.
  5. Enrich azure_merchant_id by a BUILD-TIME, READ-ONLY join on normalized tax_id to
     the AATM merchants registry (C-014). Optional — degrades to null + flag if AATM is
     unavailable; MRI identity never depends on AATM at runtime.

The clustering universe is the funded book (≤ a few thousand accounts), so the driver-side
pure pass is cheap and keeps the matching logic tier-1 testable.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from common import constants as C
from common.identity.keys import assign_merchant_ids, match_reason_by_merchant
from common.identity.match import AccountKeys, account_match_keys, cluster_accounts
from common.identity.normalize import normalize_tax_id
from common.schemas.gold import merchant_crosswalk_schema, merchant_schema


def _col_or_null(df: DataFrame, name: str, cast: str | None = None):
    """Column if present on `df`, else a typed null literal.

    bronze.account custom fields (business start, industry) were not all confirmed
    at G1 — guard so the transform never hard-fails on a missing optional column.
    """
    if name in df.columns:
        c = F.col(name)
        return c.cast(cast) if cast else c
    return F.lit(None).cast(cast or "string")


def _read_existing_crosswalk(spark: SparkSession, fq_name: str) -> dict[str, str]:
    """Previously-persisted crosswalk (empty on first build). D-101 stability seed."""
    if not spark.catalog.tableExists(fq_name):
        return {}
    rows = spark.read.table(fq_name).select("merchant_sf_id", "merchant_id").collect()
    return {r["merchant_sf_id"]: r["merchant_id"] for r in rows}


def resolve_identity(spark: SparkSession, keys_df: DataFrame, crosswalk_fq: str):
    """Collect normalized keys, run the pure cluster + id assignment, return
    (CrosswalkResult, ClusterResult). Driver-side pure pass (tier-1 logic)."""
    accounts = [
        AccountKeys(
            merchant_sf_id=r["merchant_sf_id"],
            master_record_id=r["master_record_id"],
            tax_id=r["tax_id"],
            phone=r["phone"],
            name=r["name"],
            state=r["state"],
        )
        for r in keys_df.collect()
    ]
    cluster = cluster_accounts(accounts)
    existing = _read_existing_crosswalk(spark, crosswalk_fq)
    crosswalk = assign_merchant_ids(cluster, existing_crosswalk=existing)
    return crosswalk, cluster


def _crosswalk_df(spark: SparkSession, crosswalk: dict[str, str]) -> DataFrame:
    rows = [(sf_id, mid) for sf_id, mid in sorted(crosswalk.items())]
    return spark.createDataFrame(rows, schema=merchant_crosswalk_schema())


def build_merchant_dimension(
    account_profile: DataFrame,
    crosswalk_df: DataFrame,
    match_reason_df: DataFrame,
) -> DataFrame:
    """One row per merchant_id: representative static profile + cluster size.

    Representative = first non-null value ordered by ascending merchant_sf_id
    (deterministic). sf_account_count = # SF Accounts collapsed into the merchant.
    """
    joined = account_profile.join(crosswalk_df, on="merchant_sf_id", how="inner")
    w = (
        Window.partitionBy("merchant_id")
        .orderBy("merchant_sf_id")
        .rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)
    )

    def first_nn(col: str):
        return F.first(F.col(col), ignorenulls=True).over(w)

    dim = (
        joined.select(
            "merchant_id",
            first_nn("business_name_raw").alias("business_name"),
            first_nn("state").alias("governing_state"),
            first_nn("tax_id").alias("tax_id"),
            first_nn("business_start_date").alias("business_start_date"),
            first_nn("industry").alias("industry"),
            F.count(F.lit(1)).over(w).cast("int").alias("sf_account_count"),
        )
        .dropDuplicates(["merchant_id"])
        .join(match_reason_df, on="merchant_id", how="left")
    )
    # principal_name: no reliable Account field at S1 (gap) -> null.
    return dim.withColumn("principal_name", F.lit(None).cast("string"))


def enrich_azure_merchant_id(
    spark: SparkSession,
    merchants_df: DataFrame,
    aatm_catalog: str = C.Identity.AATM_CATALOG,
    aatm_table: str = C.Identity.AATM_MERCHANTS_TABLE,
) -> DataFrame:
    """Left-join AATM's azure_merchant_id on normalized tax_id (C-014).

    Read-only and optional: if the AATM registry is unreadable the column degrades to
    null (flagged downstream). Tie-break when one tax_id maps to >1 AATM merchant:
    deterministic min(azure_merchant_id).
    """
    from pyspark.sql.types import StringType

    fq = f"{aatm_catalog}.{aatm_table}"
    try:
        aatm = spark.read.table(fq)
    except Exception:  # noqa: BLE001 — optional enrichment, never fail the build
        return merchants_df.withColumn("azure_merchant_id", F.lit(None).cast("string"))

    n_tax = F.udf(normalize_tax_id, StringType())
    agg = (
        aatm.select(
            n_tax(F.col("tax_id")).alias("_aatm_tax"),
            F.col("azure_merchant_id").cast("string").alias("azure_merchant_id"),
        )
        .filter(F.col("_aatm_tax").isNotNull())
        .groupBy("_aatm_tax")
        .agg(F.min("azure_merchant_id").alias("azure_merchant_id"))
    )
    return merchants_df.join(
        agg, merchants_df["tax_id"] == agg["_aatm_tax"], "left"
    ).drop("_aatm_tax")


def finalize_merchants(merchants_df: DataFrame) -> DataFrame:
    """Add the missing-flags (never fake values) and project to the gold schema order."""
    out = (
        merchants_df.withColumn(
            "azure_merchant_id_is_missing", F.col("azure_merchant_id").isNull()
        )
        .withColumn("principal_name_is_missing", F.col("principal_name").isNull())
        .withColumn("tax_id_is_missing", F.col("tax_id").isNull())
    )
    return out.select(*[f.name for f in merchant_schema().fields])


def build_gold_merchants(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    bronze_schema: str = C.Schema.BRONZE,
    silver_schema: str = C.Schema.SILVER,
    enrich_aatm: bool = True,
) -> dict[str, str]:
    """Entry point: build gold.merchant_crosswalk + gold.merchants. Idempotent.

    Returns {"crosswalk": fq, "merchants": fq}. Writes managed tables (overwrite).
    Prod `gold` writes are approval-gated (Rule 5) — call with schema=gold_test first.
    """
    account = spark.read.table(C.fq(bronze_schema, C.BronzeTable.ACCOUNT, catalog))
    deals = spark.read.table(C.fq(silver_schema, C.SilverTable.DEALS, catalog))
    # Funded universe: accounts referenced by a funded deal (silver.deals.merchant_sf_id).
    funded_accts = deals.select(F.col("merchant_sf_id").alias("AccountId")).distinct()

    keys_df = account_match_keys(account, opp_df=funded_accts)
    # Profile carries the raw business name + normalized keys + optional static profile.
    account_profile = keys_df.select(
        "merchant_sf_id",
        "business_name_raw",
        "state",
        "tax_id",
    ).join(
        account.select(
            F.col("Id").alias("merchant_sf_id"),
            _col_or_null(account, "Business_Start__c", "date").alias("business_start_date"),
            _col_or_null(account, "Industry").alias("industry"),
        ),
        on="merchant_sf_id",
        how="left",
    )

    crosswalk_fq = C.fq(schema, C.GoldTable.MERCHANT_CROSSWALK, catalog)
    crosswalk, cluster = resolve_identity(spark, keys_df, crosswalk_fq)

    xwalk_df = _crosswalk_df(spark, crosswalk.crosswalk)
    reasons = match_reason_by_merchant(crosswalk.crosswalk, cluster)
    reason_rows = [(mid, reason) for mid, reason in sorted(reasons.items())]
    match_reason_df = spark.createDataFrame(
        reason_rows, schema="merchant_id string, match_reason string"
    )

    dim = build_merchant_dimension(account_profile, xwalk_df, match_reason_df)
    if enrich_aatm:
        dim = enrich_azure_merchant_id(spark, dim)
    else:
        dim = dim.withColumn("azure_merchant_id", F.lit(None).cast("string"))
    merchants = finalize_merchants(dim)

    merchants_fq = C.fq(schema, C.GoldTable.MERCHANTS, catalog)
    xwalk_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(crosswalk_fq)
    merchants.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(merchants_fq)
    return {"crosswalk": crosswalk_fq, "merchants": merchants_fq}
