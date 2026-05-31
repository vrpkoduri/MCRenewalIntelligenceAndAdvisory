"""Silver schema builders, derived from the field maps (single source of truth).

Spark types are built lazily so this module imports without Spark for tier-1 tests,
which can still inspect the logical column lists.
"""

from ..field_maps import (
    DEALS_DQ_COLUMNS,
    DEALS_MAP,
    FIELD_HISTORY_MAP,
    OFFERS_MAP,
)

_LOGICAL_TO_SPARK = {
    "string": "StringType",
    "decimal": "DecimalType",  # see _spark_type for precision/scale
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


def deals_schema():
    """StructType for mca_mri.silver.deals (source columns + DQ-derived columns)."""
    from pyspark.sql.types import StructField, StructType

    fields = [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in DEALS_MAP]
    fields += [StructField(name, _spark_type(dt), True) for name, dt in DEALS_DQ_COLUMNS]
    return StructType(fields)


def offers_schema():
    """StructType for mca_mri.silver.offers (raw offer catalogue, no DQ-derived cols)."""
    from pyspark.sql.types import StructField, StructType

    return StructType(
        [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in OFFERS_MAP]
    )


def field_history_schema():
    from pyspark.sql.types import StructField, StructType

    return StructType(
        [StructField(fs.silver_col, _spark_type(fs.dtype), True) for fs in FIELD_HISTORY_MAP]
    )
