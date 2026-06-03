"""Gold schema builders (S1), derived from the gold field maps (single source of truth).

Mirrors schemas/silver.py: Spark types are built lazily so this module imports
without Spark for tier-1 tests, which can still inspect the logical column lists.

Three gold tables (D-104):
  mca_mri.gold.deals             -- canonical Deal Table (24 contract fields + DQ)
  mca_mri.gold.merchants         -- resolved merchant dimension (S1 identity/profile seed)
  mca_mri.gold.merchant_crosswalk-- persisted SF-Account -> merchant_id crosswalk (D-101)
"""

from ..eventlog.events import EVENT_LOG_COLUMNS
from ..field_maps import (
    BOOK_HEALTH_MAP,
    DEAL_CLOCK_MAP,
    DEAL_TABLE_MAP,
    GOLD_DEAL_CLOCK_DQ_COLUMNS,
    GOLD_DEALS_DQ_COLUMNS,
    GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS,
    GOLD_MERCHANT_CLOCK_DQ_COLUMNS,
    GOLD_MERCHANT_RUNG_DQ_COLUMNS,
    GOLD_MERCHANTS_DQ_COLUMNS,
    MERCHANT_ACTIVATION_MAP,
    MERCHANT_CLOCK_MAP,
    MERCHANT_CROSSWALK_MAP,
    MERCHANT_MAP,
    MERCHANT_RUNG_MAP,
)

# Logical dtype -> Spark type name. Identical contract to schemas/silver.py so the
# two layers never drift. See _spark_type for decimal precision/scale.
_LOGICAL_TO_SPARK = {
    "string": "StringType",
    "decimal": "DecimalType",
    "int": "IntegerType",
    "date": "DateType",
    "timestamp": "TimestampType",
    "bool": "BooleanType",
    "enum": "StringType",
}


def _spark_type(dtype: str):
    from pyspark.sql import types as T

    if dtype == "decimal":
        return T.DecimalType(18, 4)
    return getattr(T, _LOGICAL_TO_SPARK[dtype])()


def deal_table_schema():
    """StructType for mca_mri.gold.deals (24 contract fields + gold DQ columns)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in DEAL_TABLE_MAP]
    fields += [StructField(name, _spark_type(dt), True) for name, dt in GOLD_DEALS_DQ_COLUMNS]
    return StructType(fields)


def merchant_schema():
    """StructType for mca_mri.gold.merchants (S1 identity/profile seed + DQ columns)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in MERCHANT_MAP]
    fields += [
        StructField(name, _spark_type(dt), True) for name, dt in GOLD_MERCHANTS_DQ_COLUMNS
    ]
    return StructType(fields)


def merchant_crosswalk_schema():
    """StructType for mca_mri.gold.merchant_crosswalk (D-101 persisted crosswalk)."""
    from pyspark.sql.types import StructField, StructType

    return StructType(
        [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in MERCHANT_CROSSWALK_MAP]
    )


def deal_clock_schema():
    """StructType for mca_mri.gold.deal_clock (S2 Appendix A per-deal clock, point-in-time)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in DEAL_CLOCK_MAP]
    fields += [
        StructField(name, _spark_type(dt), True) for name, dt in GOLD_DEAL_CLOCK_DQ_COLUMNS
    ]
    return StructType(fields)


def merchant_clock_schema():
    """StructType for mca_mri.gold.merchant_clock (S2 Appendix A merchant roll-up, point-in-time)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in MERCHANT_CLOCK_MAP]
    fields += [
        StructField(name, _spark_type(dt), True) for name, dt in GOLD_MERCHANT_CLOCK_DQ_COLUMNS
    ]
    return StructType(fields)


def merchant_rung_schema():
    """StructType for mca_mri.gold.merchant_rung (S3 Appendix B classifier, point-in-time, D-304)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in MERCHANT_RUNG_MAP]
    fields += [
        StructField(name, _spark_type(dt), True) for name, dt in GOLD_MERCHANT_RUNG_DQ_COLUMNS
    ]
    return StructType(fields)


def event_log_schema():
    """StructType for mca_mri.gold.merchant_event_log (S3 append-only event log, D-305).

    Built from the single column source in common.eventlog.events (EVENT_LOG_COLUMNS) so
    the schema and the pure builders can never drift.
    """
    from pyspark.sql.types import StructField, StructType

    return StructType(
        [StructField(name, _spark_type(dt), True) for name, dt in EVENT_LOG_COLUMNS]
    )


def merchant_activation_schema():
    """StructType for mca_mri.gold.merchant_activation (S4 state machine + plays, point-in-time, D-404)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in MERCHANT_ACTIVATION_MAP]
    fields += [
        StructField(name, _spark_type(dt), True) for name, dt in GOLD_MERCHANT_ACTIVATION_DQ_COLUMNS
    ]
    return StructType(fields)


def book_health_schema():
    """StructType for mca_mri.gold.book_health (S4 scoreboard, tall point-in-time, D-404)."""
    from pyspark.sql.types import StructField, StructType

    return StructType(
        [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in BOOK_HEALTH_MAP]
    )
