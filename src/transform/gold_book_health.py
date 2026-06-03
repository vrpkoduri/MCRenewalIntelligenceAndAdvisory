"""Gold -> Gold: Portfolio Analytics / Book Health — the scoreboard (Framework 5.8, S4).

A READ-ONLY aggregation consumer (a third renderer over gold) — rung distribution + drift,
default trend, leading indicators — trended over time by reading the rung-transition history
in the event log. Reads S1/S2/S3/S4 gold; writes nothing back; recomputes no spine.

v1 (D-404) computes ONLY metrics whose inputs exist today (see common.bookhealth.metrics
registry). LTV / defection / offer-acceptance / value-to-ask / comms-SLA metrics are DEFERRED
(S5/S6/S8) and intentionally NOT emitted — honest coverage, never faked.

Output: a TALL point-in-time table `gold.book_health` (report_date, view, metric, dimension,
dimension_value, value_num, value_pct), partitioned by report_date, append-only / idempotent
per run; with one `_current` view per Framework-5.8 view (book_health / renewal_performance /
leading_indicators).
"""

from __future__ import annotations

from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    StringType,
    StructField,
    StructType,
)

from common import constants as C
from common.bookhealth import net_drift, pct
from common.schemas.gold import book_health_schema

_BH = C.BookHealthView
_TOP_N = 10  # cap concentration breakdowns

# Intermediate row schema (cast to book_health_schema on write).
_ROW_SCHEMA = StructType([
    StructField("view", StringType()),
    StructField("metric", StringType()),
    StructField("dimension", StringType()),
    StructField("dimension_value", StringType()),
    StructField("value_num", DoubleType()),
    StructField("value_pct", DoubleType()),
])


def compute_book_health(spark: SparkSession, catalog: str, schema: str, run_date: date) -> DataFrame:
    rung = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog))
    activation = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_ACTIVATION_CURRENT, catalog))
    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog))
    event_log = (
        spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog))
        if spark.catalog.tableExists(C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog))
        else None
    )

    total = rung.count()
    rows: list[tuple] = []

    def add(view, metric, dimension, dimension_value, value_num, value_pct=None):
        rows.append((view, metric, dimension,
                     None if dimension_value is None else str(dimension_value),
                     None if value_num is None else float(value_num),
                     None if value_pct is None else float(value_pct)))

    # --- Book health: rung distribution (reconciles to merchant_rung_current) ---
    for r in rung.groupBy("rung").count().collect():
        key = str(r["rung"]) if r["rung"] is not None else "null"
        add(_BH.BOOK_HEALTH, "rung_distribution", "rung", key, r["count"], pct(r["count"], total))

    # --- Book health: rung drift (net up vs down) from logged rung transitions ---
    if event_log is not None:
        trans = event_log.where(
            (F.col("event_type") == F.lit(C.EventType.TRANSITION))
            & F.col("rung").isNotNull()
            & F.col("prev_rung").isNotNull()
        )
        up = trans.where(F.col("rung") > F.col("prev_rung")).count()
        down = trans.where(F.col("rung") < F.col("prev_rung")).count()
        add(_BH.BOOK_HEALTH, "rung_drift_up", None, None, up)
        add(_BH.BOOK_HEALTH, "rung_drift_down", None, None, down)
        add(_BH.BOOK_HEALTH, "rung_drift_net", None, None, net_drift(up, down))

    # --- Book health: default / restructure trend (v1 = defaulted lifecycle count) ---
    defaulted = rung.where(F.col("lifecycle_state") == F.lit(C.LifecycleState.DEFAULTED)).count()
    add(_BH.BOOK_HEALTH, "default_count", None, None, defaulted, pct(defaulted, total))

    # --- Book health: renewal capture (PARTIAL — full needs S5/S6) ---
    state_counts = {r["current_state"]: r["count"] for r in activation.groupBy("current_state").count().collect()}
    renewed = state_counts.get(C.CurrentState.RENEWED, 0)
    eligible_or_renewed = renewed + state_counts.get(C.CurrentState.IN_MARKET, 0) + state_counts.get(C.CurrentState.APPROACHING, 0)
    add(_BH.BOOK_HEALTH, "renewal_capture_partial", None, None, renewed, pct(renewed, eligible_or_renewed))

    # --- Leading indicators: sliding count ---
    sliding = rung.where(F.col("direction_of_travel") == F.lit(C.DirectionOfTravel.SLIDING)).count()
    add(_BH.LEADING_INDICATORS, "sliding_count", None, None, sliding, pct(sliding, total))

    # --- Leading indicators: approaching pipeline ---
    approaching = state_counts.get(C.CurrentState.APPROACHING, 0)
    add(_BH.LEADING_INDICATORS, "approaching_pipeline", None, None, approaching, pct(approaching, total))

    # --- Leading indicators: concentration risk (funder / governing_state / rung) ---
    funder_conc = (
        deals.groupBy("funder").agg(F.countDistinct("merchant_id").alias("m"))
        .orderBy(F.desc("m")).limit(_TOP_N).collect()
    )
    for r in funder_conc:
        add(_BH.LEADING_INDICATORS, "concentration_funder", "funder", r["funder"], r["m"], pct(r["m"], total))
    state_conc = (
        merchants.groupBy("governing_state").count().orderBy(F.desc("count")).limit(_TOP_N).collect()
    )
    for r in state_conc:
        add(_BH.LEADING_INDICATORS, "concentration_governing_state", "governing_state",
            r["governing_state"], r["count"], pct(r["count"], total))

    raw = spark.createDataFrame(rows, _ROW_SCHEMA)
    out = raw.select(
        F.lit(run_date.isoformat()).cast("date").alias("report_date"),
        F.col("view"),
        F.col("metric"),
        F.col("dimension"),
        F.col("dimension_value"),
        F.col("value_num").cast("decimal(18,4)").alias("value_num"),
        F.col("value_pct").cast("decimal(18,4)").alias("value_pct"),
    )
    return out.select(*[f.name for f in book_health_schema().fields])


def _write_point_in_time(df: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    writer = df.write.format("delta").partitionBy("report_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"report_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_view_for(spark: SparkSession, base: str, view_name: str, view_value: str) -> None:
    spark.sql(
        f"CREATE OR REPLACE VIEW {view_name} AS SELECT * FROM {base} "
        f"WHERE report_date = (SELECT MAX(report_date) FROM {base}) AND view = '{view_value}'"
    )


def build_gold_book_health(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    run_date: date | None = None,
    allow_prod: bool = False,
) -> dict:
    """Entry point: build the tall gold.book_health scoreboard for `run_date` + the three
    per-view `_current` views. Read-only over gold; prod gated (Rule 5)."""
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build book_health into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()
    target = C.fq(schema, C.GoldTable.BOOK_HEALTH, catalog)

    bh = compute_book_health(spark, catalog, schema, run_date)
    _write_point_in_time(bh, target, run_date, spark)

    _create_view_for(spark, target, C.fq(schema, C.GoldTable.BOOK_HEALTH_CURRENT, catalog), _BH.BOOK_HEALTH)
    _create_view_for(spark, target, C.fq(schema, C.GoldTable.RENEWAL_PERFORMANCE_CURRENT, catalog), _BH.RENEWAL_PERFORMANCE)
    _create_view_for(spark, target, C.fq(schema, C.GoldTable.LEADING_INDICATORS_CURRENT, catalog), _BH.LEADING_INDICATORS)

    return {
        "book_health": target,
        "book_health_current": C.fq(schema, C.GoldTable.BOOK_HEALTH_CURRENT, catalog),
        "renewal_performance_current": C.fq(schema, C.GoldTable.RENEWAL_PERFORMANCE_CURRENT, catalog),
        "leading_indicators_current": C.fq(schema, C.GoldTable.LEADING_INDICATORS_CURRENT, catalog),
        "report_date": run_date.isoformat(),
    }
