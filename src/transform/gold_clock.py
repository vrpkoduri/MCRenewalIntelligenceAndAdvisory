"""Gold -> Gold: the Amortization Clock (Appendix A, S2).

THE core principle (CLAUDE.md 2.1 / A.0): recompute everything time-dependent DAILY from
the static terms on `gold.deals` + the run's "today". Salesforce's stored Remaining
Balance / Percentage Paid / Estimated Renewal Date are frozen funding-moment snapshots and
are NEVER an input here (they live on silver as `_sf_stored_*` checkpoint columns only).

Outputs two POINT-IN-TIME tables (D-201/C-016), partitioned by `clock_run_date`,
append-only across days and idempotent within a day (Delta `replaceWhere`):
  - gold.deal_clock      : per-deal rtr / elapsed / balance / paydown / eligible-date /
                           closure_status / balance_source (+ `*_current` view)
  - gold.merchant_clock  : the contract "Position & burden (the clock)" roll-up
                           (+ `*_current` view)

The per-row math is the pure `common.clock` functions (tier-1 tested): calendar logic runs
as UDFs; the trivial arithmetic/closure mirrors the pure functions as native columns
(the dq.predicates ↔ dq.rules pattern). v1 runs the ESTIMATED path for the whole book
(D-203 — no servicing feed); the `actual` path is wired via the optional `servicing_feed`
arg but inert until a feed lands.
"""

from __future__ import annotations

import re
from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, IntegerType
from pyspark.sql.window import Window

from common import constants as C
from common.clock import calendar as cal
from common.schemas.gold import deal_clock_schema, merchant_clock_schema

_DEC = "decimal(18,4)"

# Case-insensitive default-cause matcher built from the centralized keyword list (A.5b).
_DEFAULT_NOTE_REGEX = "|".join(re.escape(kw) for kw in C.DEFAULT_NOTE_KEYWORDS)

# Static terms the clock needs off gold.deals.
_STATIC_COLS = (
    "deal_id",
    "merchant_id",
    "funded_amount",
    "factor_rate",
    "payment_amount",
    "num_payments",
    "payment_frequency",
    "funded_date",
    "total_payback",
)


def _elapsed_udf(run_date: date):
    """UDF over the pure `calendar.elapsed_payments` (A.3), `today` = the run date."""

    def _f(funded_date, frequency, num_payments):
        return cal.elapsed_payments(funded_date, run_date, frequency, num_payments)

    return F.udf(_f, IntegerType())


def _eligible_date_udf():
    """UDF over the pure `calendar.eligible_date` (A.4 inverse-solve)."""

    def _f(funded_date, frequency, num_payments, payment_amount, rtr_value, threshold):
        pay = float(payment_amount) if payment_amount is not None else None
        r = float(rtr_value) if rtr_value is not None else None
        th = float(threshold) if threshold is not None else None
        return cal.eligible_date(funded_date, frequency, num_payments, pay, r, th)

    return F.udf(_f, DateType())


def compute_deal_clock(
    deals: DataFrame,
    notes: DataFrame,
    run_date: date,
    threshold: float = C.Thresholds.DEFAULT_RENEWAL_PAYDOWN,
    servicing_feed: DataFrame | None = None,
) -> DataFrame:
    """Per-deal clock outputs (A.2–A.5b) for a single `run_date`.

    `deals` is gold.deals (static terms). `notes` carries (deal_id, notes) from silver
    (gold.deals intentionally does not surface free-text Notes). `servicing_feed`, if given,
    supplies real elapsed counts for the actual path (inert in v1).
    """
    static = deals.select(*_STATIC_COLS)
    n = notes.select(F.col("deal_id").alias("_n_deal_id"), F.col("notes"))
    df = static.join(n, static["deal_id"] == n["_n_deal_id"], "left").drop("_n_deal_id")

    inputs_missing = (
        F.col("funded_amount").isNull()
        | F.col("factor_rate").isNull()
        | F.col("payment_amount").isNull()
        | F.col("num_payments").isNull()
        | F.col("payment_frequency").isNull()
        | F.col("funded_date").isNull()
    )

    df = df.withColumn("rtr", (F.col("funded_amount") * F.col("factor_rate")).cast(_DEC))
    df = df.withColumn("renewal_threshold", F.lit(threshold).cast(_DEC))  # D-205 default; FU-201 funder lookup

    # Estimated path (A.3): business-day / weekly count, term-capped — via the pure fn.
    est_elapsed = _elapsed_udf(run_date)(
        F.col("funded_date"), F.col("payment_frequency"), F.col("num_payments")
    )

    # Actual path (A.3, D-203 inert in v1): coalesce a real elapsed count if a feed exists.
    if servicing_feed is not None:
        feed = servicing_feed.select(
            F.col("deal_id").alias("_f_deal_id"),
            F.col("actual_payments_made").cast("int").alias("_actual_elapsed"),
        )
        df = df.join(feed, df["deal_id"] == feed["_f_deal_id"], "left").drop("_f_deal_id")
        df = df.withColumn("elapsed_payments", F.coalesce(F.col("_actual_elapsed"), est_elapsed))
        df = df.withColumn(
            "balance_source",
            F.when(F.col("_actual_elapsed").isNotNull(), F.lit(C.BalanceSource.ACTUAL)).otherwise(
                F.lit(C.BalanceSource.ESTIMATED)
            ),
        ).drop("_actual_elapsed")
    else:
        df = df.withColumn("elapsed_payments", est_elapsed)
        df = df.withColumn("balance_source", F.lit(C.BalanceSource.ESTIMATED))

    df = df.withColumn(
        "amount_paid", (F.col("payment_amount") * F.col("elapsed_payments")).cast(_DEC)
    )
    # est_current_balance = max(0, rtr − amount_paid) — floored (A.2).
    df = df.withColumn(
        "est_current_balance",
        F.greatest(F.lit(0.0).cast(_DEC), (F.col("rtr") - F.col("amount_paid")).cast(_DEC)),
    )
    # est_paydown_pct = amount_paid ÷ rtr, capped at 1.0 (A.2). Null when rtr missing/zero.
    df = df.withColumn(
        "est_paydown_pct",
        F.when(
            F.col("rtr").isNotNull() & (F.col("rtr") != 0),
            F.least(F.lit(1.0).cast(_DEC), (F.col("amount_paid") / F.col("rtr")).cast(_DEC)),
        ).otherwise(F.lit(None).cast(_DEC)),
    )

    df = df.withColumn(
        "est_renewal_eligible_date",
        _eligible_date_udf()(
            F.col("funded_date"),
            F.col("payment_frequency"),
            F.col("num_payments"),
            F.col("payment_amount"),
            F.col("rtr"),
            F.col("renewal_threshold"),
        ),
    )
    df = df.withColumn(
        "is_eligible_now",
        F.col("est_paydown_pct").isNotNull() & (F.col("est_paydown_pct") >= F.col("renewal_threshold")),
    )

    # has_default_note (A.5b) — case-insensitive substring match on Notes.
    df = df.withColumn(
        "has_default_note",
        F.when(
            F.col("notes").isNotNull() & F.lower(F.col("notes")).rlike(_DEFAULT_NOTE_REGEX),
            F.lit(True),
        ).otherwise(F.lit(False)),
    )
    # closure_status (A.5b): default note dominates; else paydown ≥ 100% is clean; else active.
    df = df.withColumn(
        "closure_status",
        F.when(F.col("has_default_note"), F.lit(C.ClosureStatus.CLOSED_DEFAULT))
        .when(F.col("est_paydown_pct") >= F.lit(1.0), F.lit(C.ClosureStatus.CLOSED_CLEAN))
        .otherwise(F.lit(C.ClosureStatus.ACTIVE)),
    )

    # DQ: inputs-missing (math fields are null, never faked) + day-one RTR checkpoint (A.0).
    df = df.withColumn("clock_inputs_missing", inputs_missing)
    df = df.withColumn(
        "rtr_checkpoint_delta",
        F.when(
            F.col("rtr").isNotNull() & F.col("total_payback").isNotNull(),
            F.abs(F.col("rtr") - F.col("total_payback")).cast(_DEC),
        ).otherwise(F.lit(None).cast(_DEC)),
    )

    df = df.withColumn("clock_run_date", F.lit(run_date.isoformat()).cast("date"))

    return df.select(*[f.name for f in deal_clock_schema().fields])


def compute_merchant_clock(deal_clock: DataFrame, deals: DataFrame, run_date: date) -> DataFrame:
    """Per-merchant roll-up (A.5) for a single `run_date`. Mirrors `clock.rollup`:
    burden across ACTIVE positions; PRIMARY (most-recent) active position's paydown drives
    eligibility; `balance_source` = weakest across positions; revenue/burden null in v1.
    """
    static = deals.select(
        F.col("deal_id").alias("_d_deal_id"),
        F.col("payment_amount"),
        F.col("payment_frequency"),
        F.col("funded_date"),
        F.col("funded_amount"),
    )
    work = deal_clock.join(static, deal_clock["deal_id"] == static["_d_deal_id"], "left").drop(
        "_d_deal_id"
    )

    is_active = F.col("closure_status") == F.lit(C.ClosureStatus.ACTIVE)
    # Weekly-normalized debit per position (A.5): daily → ×5 business days; weekly → as-is.
    weekly_debit = (
        F.when(
            F.col("payment_frequency") == F.lit(C.PaymentFrequency.DAILY),
            F.col("payment_amount") * F.lit(C.Thresholds.BUSINESS_DAYS_PER_WEEK),
        )
        .when(
            F.col("payment_frequency") == F.lit(C.PaymentFrequency.WEEKLY),
            F.col("payment_amount"),
        )
        .otherwise(F.lit(None))
    )
    work = work.withColumn("_weekly_debit", weekly_debit)
    work = work.withColumn("_is_active", is_active)
    work = work.withColumn(
        "_estimated_flag",
        F.when(F.col("balance_source") == F.lit(C.BalanceSource.ESTIMATED), 1).otherwise(0),
    )

    agg = work.groupBy("merchant_id").agg(
        F.sum(F.when(F.col("_is_active"), 1).otherwise(0)).cast("int").alias("active_position_cnt"),
        F.sum(F.when(F.col("_is_active"), F.col("_weekly_debit"))).cast(_DEC).alias("total_weekly_debit"),
        F.sum(F.when(F.col("_is_active"), F.col("est_current_balance"))).cast(_DEC).alias("_active_balance"),
        F.min("funded_date").alias("first_funded_date"),
        F.max("_estimated_flag").alias("_any_estimated"),
        F.count(F.lit(1)).alias("_position_cnt"),
    )

    # PRIMARY active position (A.5): most-recent funded; tie-break funded_amount, deal_id.
    w = Window.partitionBy("merchant_id").orderBy(
        F.col("funded_date").desc_nulls_last(),
        F.col("funded_amount").desc_nulls_last(),
        F.col("deal_id").desc_nulls_last(),
    )
    primary = (
        work.filter(is_active)
        .withColumn("_rn", F.row_number().over(w))
        .filter(F.col("_rn") == 1)
        .select(
            F.col("merchant_id").alias("_p_merchant_id"),
            F.col("est_paydown_pct").alias("primary_paydown"),
            F.col("est_renewal_eligible_date").alias("primary_eligible_date"),
        )
    )

    m = agg.join(primary, agg["merchant_id"] == primary["_p_merchant_id"], "left").drop(
        "_p_merchant_id"
    )

    threshold = F.lit(C.Thresholds.DEFAULT_RENEWAL_PAYDOWN).cast(_DEC)
    run_col = F.lit(run_date.isoformat()).cast("date")

    out = m.select(
        F.col("merchant_id"),
        run_col.alias("clock_run_date"),
        F.col("first_funded_date"),
        F.datediff(run_col, F.col("first_funded_date")).cast("int").alias("tenure_days"),
        F.col("active_position_cnt"),
        F.coalesce(F.col("total_weekly_debit"), F.lit(0).cast(_DEC)).alias("total_weekly_debit"),
        F.lit(None).cast(_DEC).alias("est_weekly_revenue"),  # Must-capture; no feed v1
        F.lit(None).cast(_DEC).alias("burden_ratio"),  # null (no revenue) — never 0
        F.coalesce(F.col("_active_balance"), F.lit(0).cast(_DEC)).alias("est_current_balance"),
        F.col("primary_paydown").cast(_DEC).alias("est_paydown_pct"),
        F.col("primary_eligible_date").alias("est_renewal_eligible_date"),
        (F.col("primary_paydown").isNotNull() & (F.col("primary_paydown") >= threshold)).alias(
            "is_eligible_now"
        ),
        # Weakest balance_source across positions (A.5): any estimated → estimated.
        F.when(F.col("_any_estimated") == 1, F.lit(C.BalanceSource.ESTIMATED))
        .otherwise(F.lit(C.BalanceSource.ACTUAL))
        .alias("balance_source"),
        F.lit(True).alias("est_weekly_revenue_is_missing"),
        F.lit(True).alias("burden_ratio_is_missing"),
    )
    return out.select(*[f.name for f in merchant_clock_schema().fields])


def _write_point_in_time(df: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    """Write a point-in-time partition (D-201): append-only across run dates, idempotent
    within a run date via Delta `replaceWhere`. First write creates the table."""
    writer = df.write.format("delta").partitionBy("clock_run_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"clock_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_current_view(spark: SparkSession, base: str, view: str) -> None:
    """`*_current` = the rows from the latest clock_run_date (the live read surface)."""
    spark.sql(
        f"CREATE OR REPLACE VIEW {view} AS "
        f"SELECT * FROM {base} WHERE clock_run_date = (SELECT MAX(clock_run_date) FROM {base})"
    )


def build_gold_clock(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    run_date: date | None = None,
    servicing_feed: DataFrame | None = None,
    allow_prod: bool = False,
) -> dict:
    """Entry point: build gold.deal_clock + gold.merchant_clock for `run_date` (defaults to
    today) from gold.deals + silver notes, and refresh the `*_current` views.

    Prod `gold` writes are approval-gated (Rule 5): call with schema=gold_test first; prod
    requires schema=gold AND allow_prod=True. Returns the fq target names.
    """
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build the clock into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()

    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))
    notes = spark.read.table(C.fq(C.Schema.SILVER, C.SilverTable.DEALS, catalog)).select(
        F.col("opportunity_id").alias("deal_id"), F.col("notes")
    )

    deal_clock = compute_deal_clock(deals, notes, run_date, servicing_feed=servicing_feed)
    deal_clock_target = C.fq(schema, C.GoldTable.DEAL_CLOCK, catalog)
    _write_point_in_time(deal_clock, deal_clock_target, run_date, spark)

    # Re-read the persisted partition so the merchant roll-up is on the written truth.
    deal_clock_written = spark.read.table(deal_clock_target).where(
        F.col("clock_run_date") == F.lit(run_date.isoformat()).cast("date")
    )
    merchant_clock = compute_merchant_clock(deal_clock_written, deals, run_date)
    merchant_clock_target = C.fq(schema, C.GoldTable.MERCHANT_CLOCK, catalog)
    _write_point_in_time(merchant_clock, merchant_clock_target, run_date, spark)

    deal_current = C.fq(schema, C.GoldTable.DEAL_CLOCK_CURRENT, catalog)
    merchant_current = C.fq(schema, C.GoldTable.MERCHANT_CLOCK_CURRENT, catalog)
    _create_current_view(spark, deal_clock_target, deal_current)
    _create_current_view(spark, merchant_clock_target, merchant_current)

    return {
        "deal_clock": deal_clock_target,
        "merchant_clock": merchant_clock_target,
        "deal_clock_current": deal_current,
        "merchant_clock_current": merchant_current,
        "clock_run_date": run_date.isoformat(),
    }
