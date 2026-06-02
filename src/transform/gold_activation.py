"""Gold -> Gold: Activation — state machine + named plays + daily queue (Build Plan §6, S4).

Reads the S3 rung + S2 clock and produces the operational layer the FLOOR consumes — an
action `current_state`, a named `active_play`, an SLA, an owner, and grounded next-actions.
Reads S1/S2/S3 gold only; NEVER recomputes the spine (CLAUDE.md 2.1). NO Salesforce write
(D-403 — serving layer only; the SF write-back is FU-401). NO merchant comms (S8).

Per-merchant logic is the pure `common.activation` engine applied as a UDF — the logic lives
once in common/activation (Rule 3), never reimplemented in Spark.

Outputs (D-404/D-405), mirroring the S2/S3 point-in-time pattern:
  - gold.merchant_activation : point-in-time, keyed (merchant_id, activation_run_date),
                               partitioned by activation_run_date, append-only across days,
                               idempotent within a day (`replaceWhere`); + `_current` view.
  - gold.daily_queue         : a view over `_current` ordering the book sliding-first →
                               in-market/approaching → play_sla_due → confidence (D-406).
  - gold.merchant_event_log  : appends `state_transition` / `play_fired` events (the same
                               wide log — D-305) when current_state / active_play change
                               run-over-run; idempotent via delete-then-append on this run's
                               S4 event types (leaves S3's classification/transition rows).
"""

from __future__ import annotations

from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, StringType, StructField, StructType
from pyspark.sql.window import Window

from common import constants as C
from common.activation import activate_merchant
from common.schemas.gold import event_log_schema, merchant_activation_schema

_DEC = "decimal(18,4)"
_RENEWAL_TYPES = [C.DealType.RENEWAL, C.DealType.BUYOUT]
_S4_EVENT_TYPES = (C.EventType.STATE_TRANSITION, C.EventType.PLAY_FIRED)


def _activate_udf(run_date: date):
    """Apply the pure `activate_merchant` per merchant (state machine + plays). Input = a
    struct of signal columns; `run_date` is the closure for SLA dates. Returns a struct."""
    out = StructType(
        [
            StructField("current_state", StringType()),
            StructField("active_play", StringType()),
            StructField("play_sla_due", DateType()),
            StructField("play_owner", StringType()),
            StructField("next_tactical_action", StringType()),
            StructField("next_strategic_nudge", StringType()),
        ]
    )

    def _f(sig):
        signals = sig.asDict() if hasattr(sig, "asDict") else dict(sig)
        for k in ("days_since_last_funding", "days_to_eligible"):
            signals[k] = int(signals[k]) if signals.get(k) is not None else None
        a = activate_merchant(signals, run_date)
        return (
            a["current_state"],
            a["active_play"],
            a["play_sla_due"],
            a["play_owner"],
            a["next_tactical_action"],
            a["next_strategic_nudge"],
        )

    return F.udf(_f, out)


def compute_merchant_activation(
    spark: SparkSession, catalog: str, schema: str, run_date: date
) -> DataFrame:
    """Assemble per-merchant signals from the S3 rung + S2 clock + gold.deals, then apply the
    pure activation engine. NEVER recomputes the spine — only reads its outputs."""
    rung = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog)).select(
        "merchant_id", "lifecycle_state", "rung", "direction_of_travel", "confidence", "route"
    )
    clock = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_CLOCK_CURRENT, catalog)).select(
        "merchant_id", "is_eligible_now", "est_renewal_eligible_date"
    )
    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))

    is_renewal = F.col("deal_type").isin(_RENEWAL_TYPES)
    deal_agg = deals.groupBy("merchant_id").agg(
        F.max(F.when(is_renewal, F.lit(True)).otherwise(F.lit(False))).alias("has_renewal"),
        F.max("funded_date").alias("last_funded_date"),
    )

    run_lit = F.lit(run_date.isoformat()).cast("date")
    sig = (
        rung.join(clock, "merchant_id", "left")
        .join(deal_agg, "merchant_id", "left")
        .withColumn("has_renewal", F.coalesce(F.col("has_renewal"), F.lit(False)))
        .withColumn("days_since_last_funding", F.datediff(run_lit, F.col("last_funded_date")).cast("int"))
        .withColumn(
            "days_to_eligible",
            F.when(
                F.col("est_renewal_eligible_date").isNotNull(),
                F.datediff(F.col("est_renewal_eligible_date"), run_lit).cast("int"),
            ).otherwise(F.lit(None).cast("int")),
        )
        # owner_id has no source in v1 (iso_rep is a gap, FU-101) -> null -> play_owner null.
        .withColumn("owner_id", F.lit(None).cast("string"))
    )

    sig_struct = F.struct(
        "lifecycle_state", "rung", "direction_of_travel", "has_renewal",
        "days_since_last_funding", "is_eligible_now", "days_to_eligible", "owner_id",
    )
    df = sig.withColumn("_a", _activate_udf(run_date)(sig_struct))

    out = df.select(
        F.col("merchant_id"),
        run_lit.alias("activation_run_date"),
        F.col("_a.current_state").alias("current_state"),
        F.col("_a.active_play").alias("active_play"),
        F.col("_a.play_owner").alias("play_owner"),
        F.col("_a.play_sla_due").alias("play_sla_due"),
        F.col("_a.next_tactical_action").alias("next_tactical_action"),
        F.col("_a.next_strategic_nudge").alias("next_strategic_nudge"),
        F.col("lifecycle_state"),
        F.col("rung"),
        F.col("direction_of_travel"),
        F.col("confidence").cast(_DEC).alias("confidence"),
        F.col("route"),
        F.col("_a.play_owner").isNull().alias("play_owner_is_missing"),
    )
    return out.select(*[f.name for f in merchant_activation_schema().fields])


def compute_activation_events(
    activation: DataFrame, prior: DataFrame | None, run_date: date
) -> DataFrame:
    """state_transition + play_fired events (D-305) — emitted only when current_state /
    active_play changed vs the prior activation run. First run (no prior) → empty. Mirrors
    the pure `eventlog.events` builders as native columns. `event_ts` = run date at midnight."""
    event_ts = F.to_timestamp(F.lit(run_date.isoformat()))
    empty = activation.sparkSession.createDataFrame([], event_log_schema())
    if prior is None:
        return empty

    cur = activation.select("merchant_id", "current_state", "active_play")
    joined = cur.join(prior, cur["merchant_id"] == prior["_p_merchant_id"], "inner").drop("_p_merchant_id")

    def _shape(df, event_type, transition_field):
        base = {c.name: F.lit(None).cast(c.dataType) for c in event_log_schema().fields}
        base.update(
            {
                "merchant_id": F.col("merchant_id"),
                "event_type": F.lit(event_type),
                "event_ts": event_ts,
                "classify_run_date": F.lit(run_date.isoformat()).cast("date"),
                "current_state": F.col("current_state"),
                "active_play": F.col("active_play"),
                "prev_current_state": F.col("prev_current_state"),
                "prev_active_play": F.col("prev_active_play"),
                "transition_field": F.lit(transition_field),
            }
        )
        return df.select(*[base[c.name].alias(c.name) for c in event_log_schema().fields])

    state_changed = joined.where(~F.col("current_state").eqNullSafe(F.col("prev_current_state")))
    play_changed = joined.where(~F.col("active_play").eqNullSafe(F.col("prev_active_play")))
    state_events = _shape(state_changed, C.EventType.STATE_TRANSITION, "current_state")
    play_events = _shape(play_changed, C.EventType.PLAY_FIRED, "active_play")
    return state_events.unionByName(play_events)


def _write_point_in_time(df: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    writer = df.write.format("delta").partitionBy("activation_run_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"activation_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_current_view(spark: SparkSession, base: str, view: str) -> None:
    spark.sql(
        f"CREATE OR REPLACE VIEW {view} AS "
        f"SELECT * FROM {base} WHERE activation_run_date = (SELECT MAX(activation_run_date) FROM {base})"
    )


def _create_daily_queue_view(spark: SparkSession, activation_current: str, queue: str) -> None:
    """The floor consumption surface (D-406): order sliding-first → in-market/approaching →
    play_sla_due → lower confidence, materialized as `queue_rank`."""
    spark.sql(
        f"""CREATE OR REPLACE VIEW {queue} AS
        SELECT *,
          ROW_NUMBER() OVER (ORDER BY
            CASE direction_of_travel WHEN 'sliding' THEN 0 WHEN 'holding' THEN 1 ELSE 2 END,
            CASE current_state WHEN 'in-market' THEN 0 WHEN 'approaching' THEN 1
                 WHEN 'renewed' THEN 2 WHEN 'clock-running' THEN 3 ELSE 4 END,
            play_sla_due ASC NULLS LAST,
            confidence ASC NULLS LAST,
            merchant_id
          ) AS queue_rank
        FROM {activation_current}"""
    )


def _prior_activation(spark: SparkSession, target: str, run_date: date) -> DataFrame | None:
    """Latest prior activation per merchant (activation_run_date < run_date) for transition
    detection. None when no prior run exists."""
    if not spark.catalog.tableExists(target):
        return None
    run_lit = F.lit(run_date.isoformat()).cast("date")
    prior = spark.read.table(target).where(F.col("activation_run_date") < run_lit)
    if prior.limit(1).count() == 0:
        return None
    w = Window.partitionBy("merchant_id").orderBy(F.col("activation_run_date").desc())
    return (
        prior.withColumn("_rn", F.row_number().over(w))
        .where(F.col("_rn") == 1)
        .select(
            F.col("merchant_id").alias("_p_merchant_id"),
            F.col("current_state").alias("prev_current_state"),
            F.col("active_play").alias("prev_active_play"),
        )
    )


def _append_activation_events(events: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    """Append S4 events to the shared event log; idempotent via delete-then-append on THIS
    run's S4 event types (leaves S3 classification/transition rows untouched)."""
    if not spark.catalog.tableExists(target):
        # No event log yet (shouldn't happen post-S3) — create with the full schema.
        events.write.format("delta").partitionBy("classify_run_date").mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(target)
        return
    types = ", ".join(f"'{t}'" for t in _S4_EVENT_TYPES)
    spark.sql(
        f"DELETE FROM {target} WHERE classify_run_date = date'{run_date.isoformat()}' "
        f"AND event_type IN ({types})"
    )
    if events.limit(1).count() == 0:
        return
    events.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)


def build_gold_activation(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    run_date: date | None = None,
    allow_prod: bool = False,
) -> dict:
    """Entry point: build gold.merchant_activation (+`_current`), the gold.daily_queue view,
    and append state_transition/play_fired events for `run_date`.

    Prod `gold` writes are approval-gated (Rule 5): use schema=gold_test first; prod needs
    schema=gold AND allow_prod=True. NO Salesforce write here (D-403). Returns fq targets.
    """
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build activation into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()
    activation_target = C.fq(schema, C.GoldTable.MERCHANT_ACTIVATION, catalog)
    event_target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)

    prior = _prior_activation(spark, activation_target, run_date)

    activation = compute_merchant_activation(spark, catalog, schema, run_date)
    _write_point_in_time(activation, activation_target, run_date, spark)

    activation_written = spark.read.table(activation_target).where(
        F.col("activation_run_date") == F.lit(run_date.isoformat()).cast("date")
    )
    events = compute_activation_events(activation_written, prior, run_date)
    _append_activation_events(events, event_target, run_date, spark)

    activation_current = C.fq(schema, C.GoldTable.MERCHANT_ACTIVATION_CURRENT, catalog)
    queue = C.fq(schema, C.GoldTable.DAILY_QUEUE, catalog)
    _create_current_view(spark, activation_target, activation_current)
    _create_daily_queue_view(spark, activation_current, queue)

    return {
        "merchant_activation": activation_target,
        "merchant_activation_current": activation_current,
        "daily_queue": queue,
        "merchant_event_log": event_target,
        "activation_run_date": run_date.isoformat(),
    }
