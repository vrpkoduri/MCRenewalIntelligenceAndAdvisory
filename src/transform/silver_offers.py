"""Bronze -> Silver: build mca_mri.silver.offers (RAW offer catalogue).

Scope (S0): select/type/rename the FULL Offer__c list to the canonical schema. One row
per offer across ALL opportunities — this is NOT the funded book and NOT only the
selected offer. The selected-offer resolution that feeds silver.deals reads from this
same source (is_selected + last_modified_date); here we keep every offer so S5 (Offer
Engine) and S1 can analyse declined / expired / competing offers.

Source-of-truth (G1, DECISIONS C-012):
- Economics use the UNDERSCORED Offer__c fields (Payback_Amount__c / Payment_Amount__c);
  the non-underscored duplicates are near-empty external-feed copies and are NOT mapped.
- Rows are carried raw — IsDeleted is kept as a column, not filtered (CLAUDE.md: flag,
  don't silently drop). Downstream consumers decide.
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common import constants as C
from common.schemas.silver import offers_schema


def select_rename_offers(offer: DataFrame) -> DataFrame:
    """Project bronze Offer__c to canonical silver.offers columns (per OFFERS_MAP)."""
    dec = "decimal(18,4)"
    return offer.select(
        F.col("Id").alias("offer_id"),
        F.col("Opportunity__c").alias("opportunity_id"),
        F.col("Merchant__c").alias("merchant_sf_id"),
        F.col("Name").alias("offer_name"),
        F.col("Funder__c").alias("funder"),
        F.col("Status__c").alias("status"),
        F.col("Select_Offer__c").cast("boolean").alias("is_selected"),
        F.col("Funded_Amount__c").cast(dec).alias("funded_amount"),
        F.col("Factor_Rate__c").cast(dec).alias("factor_rate"),
        # underscored authoritative fields (NOT PaybackAmount__c / PaymentAmnt__c)
        F.col("Payback_Amount__c").cast(dec).alias("payback_amount"),
        F.col("Payment_Amount__c").cast(dec).alias("payment_amount"),
        F.col("Number_Payments__c").cast("int").alias("num_payments"),
        F.col("Frequency__c").alias("payment_frequency"),
        F.col("Days_Weeks__c").alias("days_weeks"),
        F.to_date(F.col("Offer_Expiration_Date__c")).alias("offer_expiration_date"),
        F.col("Total_House_Commission__c").cast(dec).alias("total_house_commission"),
        F.col("Declined_Reason__c").alias("declined_reason"),
        F.col("Notes__c").alias("notes"),
        F.col("CreatedDate").cast("timestamp").alias("created_date"),
        F.col("LastModifiedDate").cast("timestamp").alias("last_modified_date"),
        F.col("IsDeleted").cast("boolean").alias("is_deleted"),
    )


def build_silver_offers(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.SILVER,
    bronze_schema: str = C.Schema.BRONZE,
) -> str:
    """Entry point: read bronze Offer__c, build silver.offers as a managed table.

    Returns the fully-qualified target table name. Idempotent (overwrite).
    """
    offer = spark.read.table(C.fq(bronze_schema, C.BronzeTable.OFFER, catalog))
    offers = select_rename_offers(offer)

    ordered = offers.select(*[f.name for f in offers_schema().fields])

    target = C.fq(schema, C.SilverTable.OFFERS, catalog)
    (
        ordered.write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(target)
    )
    return target
