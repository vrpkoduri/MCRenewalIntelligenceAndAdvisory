"""Silver -> Gold: build mca_mri.gold.deals (canonical Deal Table, 24 contract fields).

S1 (D-103/D-104). Joins silver.deals to the resolved merchant identity
(gold.merchant_crosswalk -> gold.merchants) and adds the S1 derivations:

  - merchant_id        : stable id via the persisted crosswalk (non-null on the whole
                         funded book — AccountId is 100% populated).
  - term_months        : STATIC term in months from num_payments and frequency
                         (Appendix A.3: daily / 21.7, weekly / 4.33). NOT a live clock.
  - is_renewal_of      : deal_id of the immediately-prior same-merchant deal by
                         funded_date; set only for Renewal/Buyout (D-103). prior_factor_rate
                         is that prior deal's factor_rate. renewal_unlinkable flags a
                         Renewal/Buyout with no linkable prior.
  - status             : latest StageName from silver.field_history (event source),
                         falling back to silver.deals.stage.
  - governing_state    : from the merchant dimension, falling back to the deal's
                         state_of_incorporation.

Must-capture gaps (disclosed_positions_cnt, disclosed_balance_total, net_funded,
personal_guarantee) and the S2-deferred holdback_pct stay null + carry *_is_missing
flags — never faked (CLAUDE.md 2.5). No _sf_stored_* snapshot is surfaced here.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from common import constants as C
from common.schemas.gold import deal_table_schema

_DEC = "decimal(18,4)"


def derive_term_months(num_payments_col, frequency_col):
    """Static term-in-months (Appendix A.3). Daily payments / 21.7 business days;
    weekly payments / 4.33 weeks. Null for unknown frequency or missing count."""
    return (
        F.when(
            frequency_col == F.lit(C.PaymentFrequency.DAILY),
            num_payments_col / F.lit(C.Thresholds.BUSINESS_DAYS_PER_MONTH),
        )
        .when(
            frequency_col == F.lit(C.PaymentFrequency.WEEKLY),
            num_payments_col / F.lit(C.Thresholds.WEEKS_PER_MONTH),
        )
        .otherwise(F.lit(None))
        .cast(_DEC)
    )


def latest_status(field_history: DataFrame) -> DataFrame:
    """Most-recent StageName transition per opportunity from the event source.

    Returns columns: deal_id, status_fh. Empty-safe (no rows -> empty frame)."""
    w = Window.partitionBy("opportunity_id").orderBy(F.col("changed_at").desc_nulls_last())
    return (
        field_history.filter(F.col("field") == F.lit("StageName"))
        .withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .select(
            F.col("opportunity_id").alias("deal_id"),
            F.col("new_value").alias("status_fh"),
        )
    )


def derive_renewal_chain(deals_with_merchant: DataFrame) -> DataFrame:
    """Add is_renewal_of / prior_factor_rate / renewal_unlinkable (D-103).

    Per merchant, order deals by funded_date then deal_id; link a Renewal/Buyout to
    the immediately-prior deal. Input must have: merchant_id, deal_id, deal_type,
    funded_date, factor_rate.
    """
    w = Window.partitionBy("merchant_id").orderBy(
        F.col("funded_date").asc_nulls_last(), F.col("deal_id")
    )
    prior_deal = F.lag("deal_id").over(w)
    prior_rate = F.lag("factor_rate").over(w)
    # FU-601: repeat advances = all non-New types (Renewal / Buyout / Stack / Add-On). Stack &
    # Add-On are repeat advances too and must link the renewal chain (previously missed by a
    # literal {Renewal, Buyout}). Broadens is_renewal_of / prior_factor_rate / renewal_unlinkable.
    is_renewal_type = F.col("deal_type").isin(list(C.DealType.REPEAT_TYPES))

    return (
        deals_with_merchant.withColumn(
            "is_renewal_of", F.when(is_renewal_type, prior_deal).otherwise(F.lit(None))
        )
        .withColumn(
            "prior_factor_rate",
            F.when(is_renewal_type, prior_rate).otherwise(F.lit(None)).cast(_DEC),
        )
        .withColumn(
            "renewal_unlinkable", is_renewal_type & prior_deal.isNull()
        )
    )


def project_deal_table(deals: DataFrame, merchants: DataFrame) -> DataFrame:
    """Project the joined frame to the 24 contract fields + gold DQ columns."""
    m = merchants.select(
        F.col("merchant_id").alias("_m_merchant_id"),
        F.col("governing_state").alias("_m_governing_state"),
    )
    j = deals.join(m, deals["merchant_id"] == m["_m_merchant_id"], "left")

    governing_state = F.coalesce(F.col("_m_governing_state"), F.col("state_of_incorporation"))
    status = F.coalesce(F.col("status_fh"), F.col("stage"))

    enriched = j.select(
        F.col("deal_id"),
        F.col("merchant_id"),
        F.col("funder"),
        F.lit(None).cast("string").alias("iso_rep"),  # gap (FU-101)
        F.col("funded_date"),
        F.col("funded_amount").cast(_DEC).alias("funded_amount"),
        F.col("factor_rate").cast(_DEC).alias("factor_rate"),
        F.col("term_months"),
        F.col("num_payments").cast("int").alias("num_payments"),
        F.col("payment_frequency"),
        F.col("payment_amount").cast(_DEC).alias("payment_amount"),
        F.lit(None).cast(_DEC).alias("holdback_pct"),  # defer:S2
        F.col("payback_amount").cast(_DEC).alias("total_payback"),
        F.col("deal_type"),
        F.col("is_renewal_of"),
        F.lit(None).cast("int").alias("disclosed_positions_cnt"),  # gap
        F.col("fico").cast("int").alias("fico"),
        F.col("months_in_business").cast("int").alias("months_in_business"),
        F.lit(None).cast(_DEC).alias("disclosed_balance_total"),  # gap
        F.lit(None).cast(_DEC).alias("net_funded"),  # gap
        governing_state.alias("governing_state"),
        F.col("prior_factor_rate"),
        status.alias("status"),
        F.lit(None).cast("boolean").alias("personal_guarantee"),  # gap
        # --- gold DQ columns ---
        F.lit(True).alias("iso_rep_is_missing"),
        F.col("term_months").isNull().alias("term_months_is_missing"),
        F.col("renewal_unlinkable"),
        F.lit(True).alias("disclosed_positions_cnt_is_missing"),
        F.lit(True).alias("disclosed_balance_total_is_missing"),
        F.lit(True).alias("net_funded_is_missing"),
        F.lit(True).alias("personal_guarantee_is_missing"),
    )
    return enriched.select(*[f.name for f in deal_table_schema().fields])


def build_gold_deals(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    silver_schema: str = C.Schema.SILVER,
) -> str:
    """Entry point: build gold.deals from silver.deals + the resolved merchant identity.

    Idempotent (overwrite). Returns the fq target name. Prod `gold` writes are
    approval-gated (Rule 5) — call with schema=gold_test first.
    """
    deals = spark.read.table(C.fq(silver_schema, C.SilverTable.DEALS, catalog))
    crosswalk = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_CROSSWALK, catalog))
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog))
    field_history = spark.read.table(C.fq(silver_schema, C.SilverTable.FIELD_HISTORY, catalog))

    # merchant_id via crosswalk (deal.merchant_sf_id -> merchant_id).
    xwalk = crosswalk.select(
        F.col("merchant_sf_id").alias("_x_sf_id"), F.col("merchant_id")
    )
    deals_m = (
        deals.alias("d")
        .join(xwalk, F.col("d.merchant_sf_id") == F.col("_x_sf_id"), "left")
        .drop("_x_sf_id")
        .withColumnRenamed("opportunity_id", "deal_id")
    )

    deals_m = deals_m.withColumn(
        "term_months",
        derive_term_months(F.col("num_payments"), F.col("payment_frequency")),
    )
    deals_m = derive_renewal_chain(deals_m)

    status_df = latest_status(field_history)
    deals_m = deals_m.join(status_df, on="deal_id", how="left")

    out = project_deal_table(deals_m, merchants)

    target = C.fq(schema, C.GoldTable.DEALS, catalog)
    out.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
    return target
