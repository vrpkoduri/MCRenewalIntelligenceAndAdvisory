"""Bronze -> Silver: build mca_mri.silver.field_history (Opportunity field-change events).

Scope (S0): select/type/rename bronze OpportunityFieldHistory to the canonical schema.
One row per field change. This is the event source S3 uses to reconstruct real renewal
cadences (StageName transitions + timestamps — CLAUDE.md 2.5) and S1 uses for lineage.

Carried raw: values arrive as strings regardless of underlying type (DataType carries
the hint). No DQ derivation here — interpretation happens in the consuming sprints.
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common import constants as C
from common.schemas.silver import field_history_schema


def select_rename_field_history(history: DataFrame) -> DataFrame:
    """Project bronze OpportunityFieldHistory to canonical silver columns (FIELD_HISTORY_MAP)."""
    return history.select(
        F.col("Id").alias("history_id"),
        F.col("OpportunityId").alias("opportunity_id"),
        F.col("Field").alias("field"),
        F.col("OldValue").alias("old_value"),
        F.col("NewValue").alias("new_value"),
        F.col("DataType").alias("data_type"),
        F.col("CreatedDate").cast("timestamp").alias("changed_at"),
        F.col("CreatedById").alias("changed_by"),
    )


def build_silver_field_history(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.SILVER,
    bronze_schema: str = C.Schema.BRONZE,
) -> str:
    """Entry point: read bronze OpportunityFieldHistory, build silver.field_history.

    Returns the fully-qualified target table name. Idempotent (overwrite).
    """
    history = spark.read.table(
        C.fq(bronze_schema, C.BronzeTable.OPPORTUNITY_FIELD_HISTORY, catalog)
    )
    fh = select_rename_field_history(history)

    ordered = fh.select(*[f.name for f in field_history_schema().fields])

    target = C.fq(schema, C.SilverTable.FIELD_HISTORY, catalog)
    (
        ordered.write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(target)
    )
    return target
