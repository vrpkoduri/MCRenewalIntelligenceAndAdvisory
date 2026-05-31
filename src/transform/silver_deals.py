"""Bronze -> Silver: build mca_mri.silver.deals (one row per funded advance).

Scope (S0): select/type/rename the funded book to the canonical schema and apply the
data-quality rules. Silver only CARRIES static terms — it computes NO live balance /
paydown / eligibility (that is S2, Appendix A).

Source-of-truth (locked in G1, DECISIONS C-012):
- Funded book = Opportunity where StageName = 'Funded'.
- Deal economics come from the merchant's SELECTED offer: the Offer__c row joined on
  Offer__c.Opportunity__c = Opportunity.Id AND Offer__c.Select_Offer__c = true, deduped
  to one row per deal (latest LastModifiedDate). factor_rate / payback_amount /
  payment_amount use the underscored Offer__c fields; the non-underscored duplicates
  (PaybackAmount__c, PaymentAmnt__c) are near-empty feed copies — never used.
- funded_amount anchors on Opportunity.Funded_Amount__c; merchant link = AccountId; FICO
  from the string FICO__c (Fico_Score__c is empty). See common.field_maps.DEALS_MAP.
- The frozen SF snapshots (_sf_stored_*) are carried as checkpoint columns only and are
  NEVER surfaced downstream (CLAUDE.md 2.1; enforced by io.guards on consumed views).
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from common import constants as C
from common.dq import rules as dq
from common.schemas.silver import deals_schema


def resolve_selected_offer(offer: DataFrame) -> DataFrame:
    """Reduce bronze Offer__c to one SELECTED offer per Opportunity (C-012).

    Filter Select_Offer__c = true, then dedup to a single row per Opportunity__c keeping
    the latest LastModifiedDate (covers the ~28 funded deals carrying >1 selected offer).
    Emits `multi_selected_offer` so the ambiguity is visible, never silently dropped.
    """
    selected = offer.filter(F.col("Select_Offer__c") == F.lit(True))
    part = Window.partitionBy("Opportunity__c")
    ranked = (
        selected.withColumn(
            "_rn", F.row_number().over(part.orderBy(F.col("LastModifiedDate").desc_nulls_last()))
        )
        .withColumn("_cnt", F.count(F.lit(1)).over(part))
    )
    return (
        ranked.filter(F.col("_rn") == 1)
        .withColumn("multi_selected_offer", F.col("_cnt") > 1)
        .drop("_rn", "_cnt")
    )


def select_rename_deals(opportunity: DataFrame, selected_offer: DataFrame) -> DataFrame:
    """Join the funded Opportunity book to its selected offer and project to canonical
    silver columns (per field_maps.DEALS_MAP). Economics prefer the selected offer and
    fall back to the Opportunity's own fields when no selected offer exists (the 2 funded
    deals with none) — flagged via `selected_offer_missing`.
    """
    funded = opportunity.filter(F.col("StageName") == F.lit(C.FUNDED_STAGE))
    j = funded.alias("o").join(
        selected_offer.alias("s"),
        F.col("o.Id") == F.col("s.Opportunity__c"),
        "left",
    )

    dec = "decimal(18,4)"
    # FICO__c is a string in SF (e.g. "520" / "520.0"); via double so decimal strings
    # parse, then to int. Non-numeric -> null (-> MISSING by DQ rule 1).
    fico = F.col("o.FICO__c").cast("double").cast("int")

    return j.select(
        F.col("o.Id").alias("opportunity_id"),
        F.col("o.AccountId").alias("merchant_sf_id"),
        F.col("o.Name").alias("opportunity_name"),
        F.col("o.StageName").alias("stage"),
        F.col("o.Type").alias("deal_type"),
        F.col("o.Funder__c").alias("funder"),
        F.col("o.Funded_Amount__c").cast(dec).alias("funded_amount"),
        # factor_rate exists only on the offer
        F.col("s.Factor_Rate__c").cast(dec).alias("factor_rate"),
        F.coalesce(F.col("s.Payback_Amount__c"), F.col("o.Payback_Amount__c")).cast(dec).alias("payback_amount"),
        F.coalesce(F.col("s.Payment_Amount__c"), F.col("o.Payment_Amount__c")).cast(dec).alias("payment_amount"),
        F.coalesce(F.col("s.Number_Payments__c"), F.col("o.Number_Payments__c")).cast("int").alias("num_payments"),
        F.coalesce(F.col("s.Frequency__c"), F.col("o.Frequency__c")).alias("payment_frequency"),
        F.to_date(F.col("o.Funded_Date__c")).alias("funded_date"),
        F.col("o.CreatedDate").cast("timestamp").alias("created_date"),
        F.col("o.Days_in_Stage__c").cast("int").alias("days_in_stage"),
        F.col("o.State_of_Incorporation__c").alias("state_of_incorporation"),
        F.col("o.Months_in_Business__c").cast("int").alias("months_in_business"),
        F.to_date(F.col("o.Business_Start_Date__c")).alias("business_start_date"),
        fico.alias("fico"),
        F.col("o.Positions__c").cast("int").alias("position_at_funding"),
        F.col("o.Total_House_Commission__c").cast(dec).alias("total_house_commission"),
        F.col("o.Maximum_Approved_Amount__c").cast(dec).alias("max_approved_amount"),
        F.col("o.Number_of_Approvals__c").cast("int").alias("num_approvals"),
        F.col("o.Number_of_Declines__c").cast("int").alias("num_declines"),
        F.col("o.Notes__c").alias("notes"),
        F.col("o.Send_Renewal_Notices__c").cast("boolean").alias("send_renewal_notices"),
        F.col("o.Contact__c").alias("contact_name"),
        F.col("o.Mobile__c").alias("mobile"),
        F.col("o.Email__c").alias("email"),
        F.col("o.Application_Submission__c").alias("application_submission_id"),
        # ── checkpoint-only frozen snapshots — NEVER surface downstream (CLAUDE.md 2.1) ──
        F.col("o.Remaining_Balance__c").cast(dec).alias("_sf_stored_remaining_balance"),
        F.col("o.Percentage_Paid__c").cast(dec).alias("_sf_stored_percentage_paid"),
        F.to_date(F.col("o.Estimated_Renewal_Date__c")).alias("_sf_stored_est_renewal_date"),
        # ── selected-offer DQ flags (C-012) ──
        F.col("s.Opportunity__c").isNull().alias("selected_offer_missing"),
        F.coalesce(F.col("s.multi_selected_offer"), F.lit(False)).alias("multi_selected_offer"),
    )


def apply_dq_columns(deals: DataFrame) -> DataFrame:
    """Add the S0 DQ flags/derived columns. Operates on canonical silver column names.

    - 0/blank-as-missing for months_in_business, fico (typed value preserved + *_is_missing)
    - date_sanity_flag (large funded/created gap either direction — C-007)
    - rtr_check_delta / rtr_check_flag (diagnostic; never overwrites payback_amount)
    - funder_parsed (normalized first value of multi-value Funder)
    """
    return (
        deals.withColumn(
            "months_in_business_is_missing", dq.missing_implausible_zero("months_in_business")
        )
        .withColumn("fico_is_missing", dq.missing_implausible_zero("fico"))
        .withColumn("date_sanity_flag", dq.date_sanity_flag("funded_date", "created_date"))
        .withColumn(
            "rtr_check_delta", dq.rtr_check_delta("funded_amount", "factor_rate", "payback_amount")
        )
        .withColumn(
            "rtr_check_flag",
            dq.rtr_check_flag("funded_amount", "factor_rate", "payback_amount", C.RTR_TOLERANCE),
        )
        .withColumn("funder_parsed", F.trim(F.split(F.col("funder"), "[;,]").getItem(0)))
    )


def build_silver_deals(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.SILVER,
    bronze_schema: str = C.Schema.BRONZE,
) -> str:
    """Entry point: read bronze, build silver.deals, write as a managed table.

    Returns the fully-qualified target table name. Idempotent (overwrite).
    """
    opportunity = spark.read.table(C.fq(bronze_schema, C.BronzeTable.OPPORTUNITY, catalog))
    offer = spark.read.table(C.fq(bronze_schema, C.BronzeTable.OFFER, catalog))

    selected_offer = resolve_selected_offer(offer)
    deals = apply_dq_columns(select_rename_deals(opportunity, selected_offer))

    # Project to the canonical column order (field_maps -> deals_schema is the contract).
    ordered = deals.select(*[f.name for f in deals_schema().fields])

    target = C.fq(schema, C.SilverTable.DEALS, catalog)
    (
        ordered.write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(target)
    )
    return target
