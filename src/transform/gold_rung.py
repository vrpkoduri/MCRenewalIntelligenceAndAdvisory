"""Gold -> Gold: the Rung Classifier + State Machine + Event Log (Appendix B, S3).

Reads the S2 clock spine and classifies every funded merchant — NEVER recomputes the spine
(CLAUDE.md 2.1). It consumes `gold.merchant_clock_current` (closure/paydown/eligibility/
burden/active_position_cnt), `gold.deal_clock_current` (per-deal closure + default note),
`gold.deals` (static terms, deal_type, renewal chain) and `gold.merchants` (identity). NO
SF stored balances are read (S2 already isolated `_sf_stored_*`).

Per-merchant the row math is the pure `common.rung` engine (tier-1 tested), applied as
UDFs — the classifier logic lives once in `common/rung` (Rule 3), never reimplemented in
Spark. rapid_reup_flag (D-302) is computed by a UDF over each merchant's ordered deal-term
list (it needs the prior position's paydown recomputed at the new funded_date via the S2
clock — not the as-of-today clock value).

Outputs (D-304/D-305), mirroring the S2 point-in-time pattern:
  - gold.merchant_rung       : point-in-time, keyed (merchant_id, classify_run_date),
                               partitioned by classify_run_date, append-only across days,
                               idempotent within a day (Delta `replaceWhere`); + `_current` view.
  - gold.merchant_event_log  : ONE wide append-only log keyed (merchant_id, event_type,
                               event_ts); v1 = classification + transition events. The
                               point-in-time rung table IS the state history, so the
                               transition event (diff vs the prior run) is the state machine.

NO ML (rules-only; ML is S6). `confidence` is a deterministic rules score, not a probability.
"""

from __future__ import annotations

from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)
from pyspark.sql.window import Window

from common import constants as C
from common.eventlog.events import event_log_columns
from common.rung import classify_merchant
from common.rung.waterfall import (
    rapid_reup_flag,
    rapid_reup_into_worse_terms,
    worsening_factor,
)
from common.schemas.gold import event_log_schema, merchant_rung_schema

_DEC = "decimal(18,4)"
_RENEWAL_TYPES = list(C.DealType.REPEAT_TYPES)  # FU-601: incl. Stack / Add-On (all non-New)

# Per-deal static terms the rapid_reup / trajectory UDF needs (ordered by funded_date).
_DEAL_TERM_FIELDS = (
    "funded_date",
    "funded_amount",
    "factor_rate",
    "payment_amount",
    "num_payments",
    "payment_frequency",
)


# --- UDFs over the pure common.rung engine (logic lives once in common/rung) ------


def _deal_signals_udf():
    """Per-merchant day-one / trajectory signals from the ordered deal-term list, via the
    pure waterfall functions (D-302 rapid_reup is owned there). Returns a struct of bools."""
    out = StructType(
        [
            StructField("rapid_reup_flag", BooleanType()),
            StructField("rapid_reup_into_worse_terms", BooleanType()),
            StructField("worsening_factor", BooleanType()),
            StructField("advance_rising", BooleanType()),
        ]
    )

    def _f(deals):
        rows = [r.asDict() if hasattr(r, "asDict") else dict(r) for r in (deals or [])]
        for r in rows:
            for k in ("funded_amount", "factor_rate", "payment_amount"):
                r[k] = float(r[k]) if r[k] is not None else None
            r["num_payments"] = int(r["num_payments"]) if r["num_payments"] is not None else None
        # advance_rising: most-recent funded_amount strictly above the prior one.
        ordered = sorted(rows, key=lambda d: (d.get("funded_date") or date.min))
        advance_rising = False
        if len(ordered) >= 2:
            a, b = ordered[-2].get("funded_amount"), ordered[-1].get("funded_amount")
            advance_rising = a is not None and b is not None and float(b) > float(a)
        return (
            rapid_reup_flag(rows),
            rapid_reup_into_worse_terms(rows),
            worsening_factor(rows),
            advance_rising,
        )

    return F.udf(_f, out)


def _classify_udf():
    """Apply the pure `classify_merchant` per merchant. Input = a struct of the signal
    columns + the prior run's (lifecycle_state, rung) for direction_of_travel. Returns the
    classification output as a struct (confidence as double -> cast to decimal downstream)."""
    out = StructType(
        [
            StructField("lifecycle_state", StringType()),
            StructField("rung", IntegerType()),
            StructField("confidence", DoubleType()),
            StructField("direction_of_travel", StringType()),
            StructField("default_subtype", StringType()),
            StructField("route", StringType()),
            StructField("rapid_reup_flag", BooleanType()),
            StructField("renewal_chain_incomplete", BooleanType()),
            StructField("missing_signals", StringType()),
        ]
    )

    def _f(sig, prev_lifecycle_state, prev_rung):
        signals = sig.asDict() if hasattr(sig, "asDict") else dict(sig)
        # decimals arrive as Decimal -> floats for the pure math; ints stay ints.
        for k in ("burden_ratio", "est_paydown_pct"):
            signals[k] = float(signals[k]) if signals.get(k) is not None else None
        prev = None
        if prev_lifecycle_state is not None:
            prev = {"lifecycle_state": prev_lifecycle_state, "rung": prev_rung}
        c = classify_merchant(signals, prev=prev)
        ms = ",".join(c["missing_signals"]) if c["missing_signals"] else None
        conf = float(c["confidence"]) if c["confidence"] is not None else None
        return (
            c["lifecycle_state"],
            c["rung"],
            conf,
            c["direction_of_travel"],
            c["default_subtype"],
            c["route"],
            bool(c["rapid_reup_flag"]),
            bool(c["renewal_chain_incomplete"]),
            ms,
        )

    return F.udf(_f, out)


# --- signal assembly -------------------------------------------------------------


def compute_merchant_signals(
    spark: SparkSession, catalog: str, schema: str, run_date: date
) -> DataFrame:
    """Assemble one signal row per merchant from the S2 clock + gold deals/merchants.

    Reads `merchant_clock_current` (active_position_cnt, est_paydown_pct, burden_ratio),
    `deal_clock_current` (per-deal closure + has_default_note), and `gold.deals` (terms,
    deal_type, renewal chain). NEVER recomputes the clock — only reads its outputs.
    """
    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))
    merchant_clock = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_CLOCK_CURRENT, catalog))
    deal_clock = spark.read.table(C.fq(schema, C.GoldTable.DEAL_CLOCK_CURRENT, catalog)).select(
        "deal_id", "merchant_id", "closure_status", "has_default_note", "est_paydown_pct"
    )

    run_lit = F.lit(run_date.isoformat()).cast("date")

    # --- per-merchant deal aggregates (counts, renewal flags, dates, gaps) ---
    is_renewal = F.col("deal_type").isin(_RENEWAL_TYPES)
    deal_agg = deals.groupBy("merchant_id").agg(
        F.count(F.lit(1)).cast("int").alias("deal_count"),
        F.max(F.when(is_renewal, F.lit(True)).otherwise(F.lit(False))).alias("has_renewal"),
        F.max(F.when(F.col("renewal_unlinkable") == F.lit(True), F.lit(True)).otherwise(F.lit(False))).alias(
            "renewal_chain_incomplete"
        ),
        F.max("funded_date").alias("last_funded_date"),
        F.max("disclosed_positions_cnt").cast("int").alias("disclosed_positions_cnt"),
    )

    # prior_clean_renewal_count: a renewal deal whose linked prior position closed clean
    # (D-303). Where the chain is unlinked (is_renewal_of null) it is NOT counted here —
    # the classifier leans on `renewal_chain_incomplete` + current clean signals instead.
    prior_closure = deal_clock.select(
        F.col("deal_id").alias("_prior_id"), F.col("closure_status").alias("_prior_closure")
    )
    renewals = deals.where(is_renewal & F.col("is_renewal_of").isNotNull()).select(
        "merchant_id", F.col("is_renewal_of")
    )
    clean_renewals = (
        renewals.join(prior_closure, renewals["is_renewal_of"] == prior_closure["_prior_id"], "left")
        .where(F.col("_prior_closure") == F.lit(C.ClosureStatus.CLOSED_CLEAN))
        .groupBy("merchant_id")
        .agg(F.count(F.lit(1)).cast("int").alias("prior_clean_renewal_count"))
    )

    # has_default_note rolled up across the merchant's positions (closed_default dominates).
    default_agg = deal_clock.groupBy("merchant_id").agg(
        F.max(F.when(F.col("has_default_note") == F.lit(True), F.lit(True)).otherwise(F.lit(False))).alias(
            "has_default_note"
        )
    )

    # median renewal gap (own) + book-median gap (B.2 dormancy fallback).
    w = Window.partitionBy("merchant_id").orderBy(F.col("funded_date").asc_nulls_last())
    gaps = (
        deals.select("merchant_id", "funded_date")
        .withColumn("_prev", F.lag("funded_date").over(w))
        .where(F.col("_prev").isNotNull())
        .withColumn("_gap", F.datediff(F.col("funded_date"), F.col("_prev")))
    )
    own_median = gaps.groupBy("merchant_id").agg(
        F.expr("percentile_approx(_gap, 0.5)").cast("int").alias("median_renewal_gap_days")
    )
    book_median_row = gaps.select(
        F.expr("percentile_approx(_gap, 0.5)").alias("bm")
    ).collect()
    book_median = int(book_median_row[0]["bm"]) if book_median_row and book_median_row[0]["bm"] is not None else None

    # day-one / trajectory signals via the pure UDF over the ordered deal-term list.
    term_struct = F.struct(*[F.col(c) for c in _DEAL_TERM_FIELDS])
    deal_terms = deals.groupBy("merchant_id").agg(
        F.collect_list(term_struct).alias("_deals")
    )
    deal_terms = deal_terms.withColumn("_ds", _deal_signals_udf()(F.col("_deals"))).select(
        "merchant_id",
        F.col("_ds.rapid_reup_flag").alias("rapid_reup_flag"),
        F.col("_ds.rapid_reup_into_worse_terms").alias("rapid_reup_into_worse_terms"),
        F.col("_ds.worsening_factor").alias("worsening_factor"),
        F.col("_ds.advance_rising").alias("advance_rising"),
    )

    mc = merchant_clock.select(
        "merchant_id", "active_position_cnt", "est_paydown_pct", "burden_ratio"
    )

    sig = (
        mc.join(deal_agg, "merchant_id", "left")
        .join(default_agg, "merchant_id", "left")
        .join(clean_renewals, "merchant_id", "left")
        .join(deal_terms, "merchant_id", "left")
        .join(own_median, "merchant_id", "left")
    )

    sig = sig.withColumn(
        "has_default_note", F.coalesce(F.col("has_default_note"), F.lit(False))
    )
    sig = sig.withColumn("has_renewal", F.coalesce(F.col("has_renewal"), F.lit(False)))
    sig = sig.withColumn(
        "renewal_chain_incomplete", F.coalesce(F.col("renewal_chain_incomplete"), F.lit(False))
    )
    sig = sig.withColumn(
        "prior_clean_renewal_count", F.coalesce(F.col("prior_clean_renewal_count"), F.lit(0)).cast("int")
    )
    for col in ("rapid_reup_flag", "rapid_reup_into_worse_terms", "worsening_factor", "advance_rising"):
        sig = sig.withColumn(col, F.coalesce(F.col(col), F.lit(False)))
    sig = sig.withColumn("book_median_gap_days", F.lit(book_median).cast("int"))
    # time_since_last_active (B.2 dormancy) — days from the merchant's last funding to today.
    sig = sig.withColumn(
        "time_since_last_active_days", F.datediff(run_lit, F.col("last_funded_date")).cast("int")
    )
    # clean_payments (v1): no stress event = no default note (NSF deferred, D-301/FU-301).
    sig = sig.withColumn("clean_payments", ~F.col("has_default_note"))
    # signals not available in v1 (no bank feed / net) — explicit, never faked.
    sig = sig.withColumn("shrinking_net", F.lit(False))
    sig = sig.withColumn("relative_burden_falling", F.lit(False))
    sig = sig.withColumn("graduate_qualified", F.lit(False))

    return sig


# --- classify + write ------------------------------------------------------------

_SIGNAL_COLS = (
    "has_default_note",
    "active_position_cnt",
    "deal_count",
    "has_renewal",
    "prior_clean_renewal_count",
    "renewal_chain_incomplete",
    "time_since_last_active_days",
    "median_renewal_gap_days",
    "book_median_gap_days",
    "burden_ratio",
    "est_paydown_pct",
    "rapid_reup_flag",
    "rapid_reup_into_worse_terms",
    "disclosed_positions_cnt",
    "worsening_factor",
    "shrinking_net",
    "advance_rising",
    "relative_burden_falling",
    "graduate_qualified",
    "clean_payments",
)


def _prior_run(spark: SparkSession, target: str, run_date: date) -> DataFrame | None:
    """The latest prior classification per merchant (classify_run_date < run_date), for
    direction_of_travel + transition detection. None when no prior run exists."""
    if not spark.catalog.tableExists(target):
        return None
    run_lit = F.lit(run_date.isoformat()).cast("date")
    prior = spark.read.table(target).where(F.col("classify_run_date") < run_lit)
    if prior.limit(1).count() == 0:
        return None
    w = Window.partitionBy("merchant_id").orderBy(F.col("classify_run_date").desc())
    return (
        prior.withColumn("_rn", F.row_number().over(w))
        .where(F.col("_rn") == 1)
        .select(
            F.col("merchant_id").alias("_p_merchant_id"),
            F.col("lifecycle_state").alias("prev_lifecycle_state"),
            F.col("rung").alias("prev_rung"),
        )
    )


def compute_merchant_rung(
    signals: DataFrame, prior: DataFrame | None, run_date: date
) -> DataFrame:
    """Apply the pure classifier per merchant and shape the point-in-time rung row."""
    df = signals
    if prior is not None:
        df = df.join(prior, df["merchant_id"] == prior["_p_merchant_id"], "left").drop(
            "_p_merchant_id"
        )
    else:
        df = df.withColumn("prev_lifecycle_state", F.lit(None).cast("string"))
        df = df.withColumn("prev_rung", F.lit(None).cast("int"))

    sig_struct = F.struct(*[F.col(c) for c in _SIGNAL_COLS])
    df = df.withColumn(
        "_c", _classify_udf()(sig_struct, F.col("prev_lifecycle_state"), F.col("prev_rung"))
    )

    run_col = F.lit(run_date.isoformat()).cast("date")
    gated_states = [
        C.LifecycleState.DEFAULTED,
        C.LifecycleState.DORMANT,
        C.LifecycleState.NEW_ESTABLISHING,
    ]
    out = df.select(
        F.col("merchant_id"),
        run_col.alias("classify_run_date"),
        F.col("_c.lifecycle_state").alias("lifecycle_state"),
        F.col("_c.rung").alias("rung"),
        F.col("_c.confidence").cast(_DEC).alias("confidence"),
        F.col("_c.direction_of_travel").alias("direction_of_travel"),
        F.col("_c.default_subtype").alias("default_subtype"),
        F.col("_c.route").alias("route"),
        F.col("_c.rapid_reup_flag").alias("rapid_reup_flag"),
        F.col("_c.renewal_chain_incomplete").alias("renewal_chain_incomplete"),
        F.col("_c.missing_signals").alias("missing_signals"),
        F.col("_c.lifecycle_state").isin(gated_states).alias("is_gated"),
        (
            (F.col("_c.lifecycle_state") == F.lit(C.LifecycleState.ACTIVE)) & F.col("_c.rung").isNull()
        ).alias("is_unclassified"),
    )
    return out.select(*[f.name for f in merchant_rung_schema().fields])


def compute_event_log(
    rung: DataFrame, prior: DataFrame | None, run_date: date
) -> DataFrame:
    """Classification + transition events (D-305) for this run. Mirrors the pure
    `eventlog.events` builders as native columns: a classification event per merchant; a
    transition event only when lifecycle_state or rung changed vs the prior run. event_ts =
    the run date at midnight (deterministic -> the run is idempotent; the wide table is
    keyed (merchant_id, event_type, event_ts))."""
    event_ts = F.to_timestamp(F.lit(run_date.isoformat()))

    base = rung.select(
        "merchant_id",
        "classify_run_date",
        "lifecycle_state",
        "rung",
        "confidence",
        "direction_of_travel",
        "default_subtype",
        "route",
        "rapid_reup_flag",
        "renewal_chain_incomplete",
        "missing_signals",
    )

    classification = (
        base.withColumn("event_type", F.lit(C.EventType.CLASSIFICATION))
        .withColumn("event_ts", event_ts)
        .withColumn("prev_lifecycle_state", F.lit(None).cast("string"))
        .withColumn("prev_rung", F.lit(None).cast("int"))
        .withColumn("transition_field", F.lit(None).cast("string"))
    )

    if prior is not None:
        joined = base.join(prior, base["merchant_id"] == prior["_p_merchant_id"], "inner").drop(
            "_p_merchant_id"
        )
        lifecycle_changed = ~F.col("lifecycle_state").eqNullSafe(F.col("prev_lifecycle_state"))
        rung_changed = ~F.col("rung").eqNullSafe(F.col("prev_rung"))
        transition = (
            joined.where(lifecycle_changed | rung_changed)
            .withColumn("event_type", F.lit(C.EventType.TRANSITION))
            .withColumn("event_ts", event_ts)
            .withColumn(
                "transition_field",
                F.when(lifecycle_changed & rung_changed, F.lit("both"))
                .when(lifecycle_changed, F.lit("lifecycle_state"))
                .otherwise(F.lit("rung")),
            )
        )
        events = classification.unionByName(transition, allowMissingColumns=True)
    else:
        events = classification

    return events.select(*[f.name for f in event_log_schema().fields])


def _write_point_in_time(df: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    """Append-only across classify_run_dates, idempotent within a run via `replaceWhere`."""
    writer = df.write.format("delta").partitionBy("classify_run_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"classify_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_current_view(spark: SparkSession, base: str, view: str) -> None:
    spark.sql(
        f"CREATE OR REPLACE VIEW {view} AS "
        f"SELECT * FROM {base} WHERE classify_run_date = (SELECT MAX(classify_run_date) FROM {base})"
    )


def build_gold_rung(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    run_date: date | None = None,
    allow_prod: bool = False,
) -> dict:
    """Entry point: classify the whole book for `run_date` from the S2 clock + gold deals/
    merchants; write point-in-time gold.merchant_rung (+`_current` view) and append the
    classification/transition events to gold.merchant_event_log.

    Prod `gold` writes are approval-gated (Rule 5): use schema=gold_test first; prod needs
    schema=gold AND allow_prod=True. Returns the fq target names.
    """
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build the rung classifier into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()
    rung_target = C.fq(schema, C.GoldTable.MERCHANT_RUNG, catalog)
    event_target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)

    prior = _prior_run(spark, rung_target, run_date)

    signals = compute_merchant_signals(spark, catalog, schema, run_date)
    rung = compute_merchant_rung(signals, prior, run_date)
    _write_point_in_time(rung, rung_target, run_date, spark)

    # Re-read the persisted partition so the event log is built on the written truth.
    rung_written = spark.read.table(rung_target).where(
        F.col("classify_run_date") == F.lit(run_date.isoformat()).cast("date")
    )
    events = compute_event_log(rung_written, prior, run_date)
    # Append-only across runs; idempotent within a run (replace this run's classify_run_date).
    ev_writer = events.write.format("delta").partitionBy("classify_run_date")
    if spark.catalog.tableExists(event_target):
        ev_writer.mode("overwrite").option(
            "replaceWhere", f"classify_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(event_target)
    else:
        ev_writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(event_target)

    rung_current = C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog)
    _create_current_view(spark, rung_target, rung_current)

    return {
        "merchant_rung": rung_target,
        "merchant_rung_current": rung_current,
        "merchant_event_log": event_target,
        "classify_run_date": run_date.isoformat(),
    }
