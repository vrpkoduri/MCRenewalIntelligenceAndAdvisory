"""Tier-2 reconciliation for the S2 Amortization Clock (Appendix A).

Runs ON Databricks (needs Spark). Builds gold.deal_clock + gold.merchant_clock (and the
`*_current` views) for one `run_date` into an ISOLATED test schema (`gold_test` by default;
prod `gold` requires allow_prod=True — Rule 5), then asserts the SPRINT_2 exit criteria.

HARD (failures):
- schema contracts: deal_clock == deal_clock_schema(); merchant_clock == merchant_clock_schema();
- the WHOLE funded book gets a clock: deal_clock(run_date) row count == gold.deals row count;
- (deal_id, clock_run_date) is unique — one clock row per deal per run;
- merchant_clock(run_date) row count == distinct merchant_id on gold.deals (every merchant rolls up);
- no-surface guard: neither clock table exposes a `_sf_stored_*` column (CLAUDE.md 2.1/6);
- range invariants — est_paydown_pct ∈ [0,1] (A.2 cap) and est_current_balance ≥ 0 (A.2 floor);
- closure_status ∈ {active, closed_clean, closed_default};
- THE Starr invariant (A.5b): NO deal with has_default_note=true is closed_clean (default dominates);
- v1 estimated-path-only (D-203): balance_source is 100% 'estimated';
- est_weekly_revenue + burden_ratio are 100% null with their *_is_missing flags 100% true
  (no bank feed → unknown, never faked 0 — CLAUDE.md 2.5);
- the `*_current` views resolve to the latest clock_run_date and match that partition's counts.

DIAGNOSTIC (reported, not failed — legitimately drift with the book/run date):
- closure_status breakdown; is_eligible_now count; clock_inputs_missing count;
- rtr_checkpoint_delta: max + how many exceed RTR_TOLERANCE (terms contradiction, A.0);
- elapsed-cap hits (deals at full term); paydown distribution buckets;
- the four reference merchants (CLAUDE.md §8) located by name → their per-deal closure_status,
  paydown and merchant active_position_cnt (so Starr=closed_default / OBP=closed_clean are eyeballable).
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import deal_clock_schema, merchant_clock_schema
from transform.gold_clock import build_gold_clock

# Reference merchants (CLAUDE.md §8) — diagnostic eyeball of the closure logic on the real book.
REFERENCE_MERCHANT_NAMES = (
    "Starr Window Tinting",
    "One Big Promotion",
    "Tom Snell",
    "Wolf Corporation",
)


def run_recon(
    spark,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD_TEST,
    run_date: date | None = None,
    allow_prod: bool = False,
) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to run tier-2 clock reconciliation against prod 'gold' without "
            "allow_prod=True. Use gold_test (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()
    findings: dict = {
        "catalog": catalog,
        "schema": schema,
        "run_date": run_date.isoformat(),
    }

    # --- build the clock tables into the test schema (reads gold.deals from this schema) ---
    targets = build_gold_clock(
        spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod
    )
    findings["targets"] = targets

    run_lit = F.lit(run_date.isoformat()).cast("date")
    deal_clock = spark.read.table(targets["deal_clock"]).where(F.col("clock_run_date") == run_lit)
    merchant_clock = spark.read.table(targets["merchant_clock"]).where(
        F.col("clock_run_date") == run_lit
    )
    deal_current = spark.read.table(targets["deal_clock_current"])
    merchant_current = spark.read.table(targets["merchant_clock_current"])
    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))

    # --- schema contracts (names + order) ---
    findings["deal_clock_schema_matches"] = (
        deal_clock.columns == [f.name for f in deal_clock_schema().fields]
    )
    findings["merchant_clock_schema_matches"] = (
        merchant_clock.columns == [f.name for f in merchant_clock_schema().fields]
    )
    findings["deal_clock_columns"] = deal_clock.columns
    findings["merchant_clock_columns"] = merchant_clock.columns

    # --- coverage: whole book gets a clock; one row per deal per run ---
    deals_n = deals.count()
    deal_clock_n = deal_clock.count()
    distinct_keys = deal_clock.select("deal_id", "clock_run_date").distinct().count()
    findings["gold_deals_count"] = deals_n
    findings["deal_clock_count"] = deal_clock_n
    findings["deal_clock_distinct_keys"] = distinct_keys

    distinct_merchants = deals.select("merchant_id").distinct().count()
    merchant_clock_n = merchant_clock.count()
    findings["gold_distinct_merchants"] = distinct_merchants
    findings["merchant_clock_count"] = merchant_clock_n

    # --- no-surface guard on both clock tables ---
    findings["deal_clock_surface_offenders"] = offending_surface_columns(deal_clock.columns)
    findings["merchant_clock_surface_offenders"] = offending_surface_columns(
        merchant_clock.columns
    )

    # --- range invariants (A.2 cap + floor) ---
    findings["paydown_out_of_range"] = deal_clock.where(
        F.col("est_paydown_pct").isNotNull()
        & ((F.col("est_paydown_pct") < 0) | (F.col("est_paydown_pct") > 1))
    ).count()
    findings["balance_below_zero"] = deal_clock.where(F.col("est_current_balance") < 0).count()

    # --- closure validity + THE Starr invariant (default note never closed_clean) ---
    findings["closure_invalid"] = deal_clock.where(
        ~F.col("closure_status").isin(list(C.ClosureStatus.ALL))
    ).count()
    findings["default_note_but_clean"] = deal_clock.where(
        (F.col("has_default_note") == F.lit(True))
        & (F.col("closure_status") == F.lit(C.ClosureStatus.CLOSED_CLEAN))
    ).count()

    # --- v1 estimated-path-only (D-203) ---
    findings["balance_source_non_estimated"] = deal_clock.where(
        F.col("balance_source") != F.lit(C.BalanceSource.ESTIMATED)
    ).count()

    # --- revenue/burden unknown (no feed v1): 100% null + 100% flagged ---
    findings["est_weekly_revenue_non_null"] = merchant_clock.where(
        F.col("est_weekly_revenue").isNotNull()
    ).count()
    findings["burden_ratio_non_null"] = merchant_clock.where(
        F.col("burden_ratio").isNotNull()
    ).count()
    findings["revenue_flag_not_true"] = merchant_clock.where(
        F.col("est_weekly_revenue_is_missing") != F.lit(True)
    ).count()
    findings["burden_flag_not_true"] = merchant_clock.where(
        F.col("burden_ratio_is_missing") != F.lit(True)
    ).count()

    # --- `*_current` views: latest run_date + counts match this partition (idempotent build) ---
    cur_dates = [r[0] for r in deal_current.select("clock_run_date").distinct().collect()]
    findings["deal_current_run_dates"] = sorted(str(d) for d in cur_dates)
    findings["deal_current_count"] = deal_current.count()
    findings["merchant_current_count"] = merchant_current.count()

    # === DIAGNOSTICS ===
    findings["closure_breakdown"] = {
        r["closure_status"]: int(r["count"])
        for r in deal_clock.groupBy("closure_status").count().collect()
    }
    findings["is_eligible_now_count"] = deal_clock.where(
        F.col("is_eligible_now") == F.lit(True)
    ).count()
    findings["clock_inputs_missing_count"] = deal_clock.where(
        F.col("clock_inputs_missing") == F.lit(True)
    ).count()

    delta_row = deal_clock.select(F.max("rtr_checkpoint_delta").alias("mx")).collect()[0]
    findings["rtr_checkpoint_delta_max"] = (
        float(delta_row["mx"]) if delta_row["mx"] is not None else None
    )
    findings["rtr_checkpoint_exceeds_tolerance"] = deal_clock.where(
        F.col("rtr_checkpoint_delta") > F.lit(C.RTR_TOLERANCE)
    ).count()

    findings["elapsed_at_term_cap"] = deal_clock.join(
        deals.select("deal_id", "num_payments"), "deal_id", "inner"
    ).where(F.col("elapsed_payments") == F.col("num_payments")).count()

    # Reference merchants by name → join merchants for the name, report closure/paydown.
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog)).select(
        "merchant_id", "business_name"
    )
    ref = (
        deal_clock.join(merchants, "merchant_id", "inner")
        .filter(F.col("business_name").isin(list(REFERENCE_MERCHANT_NAMES)))
        .select("business_name", "deal_id", "closure_status", "est_paydown_pct", "has_default_note")
        .collect()
    )
    findings["reference_merchants"] = [
        {
            "name": r["business_name"],
            "deal_id": r["deal_id"],
            "closure_status": r["closure_status"],
            "est_paydown_pct": float(r["est_paydown_pct"])
            if r["est_paydown_pct"] is not None
            else None,
            "has_default_note": bool(r["has_default_note"]),
        }
        for r in ref
    ]

    return findings


def assert_recon(findings: dict) -> list[str]:
    """Hard expectations -> failures (empty == green). Diagnostics are not asserted."""
    failures: list[str] = []

    if not findings.get("deal_clock_schema_matches"):
        failures.append("schema drift on gold.deal_clock")
    if not findings.get("merchant_clock_schema_matches"):
        failures.append("schema drift on gold.merchant_clock")

    deals_n = findings.get("gold_deals_count")
    if findings.get("deal_clock_count") != deals_n:
        failures.append(
            f"clock coverage: deal_clock={findings.get('deal_clock_count')} != gold.deals={deals_n}"
        )
    if findings.get("deal_clock_distinct_keys") != findings.get("deal_clock_count"):
        failures.append(
            f"(deal_id, clock_run_date) not unique: distinct="
            f"{findings.get('deal_clock_distinct_keys')} != rows={findings.get('deal_clock_count')}"
        )
    if findings.get("merchant_clock_count") != findings.get("gold_distinct_merchants"):
        failures.append(
            f"merchant roll-up coverage: merchant_clock={findings.get('merchant_clock_count')} != "
            f"distinct merchants={findings.get('gold_distinct_merchants')}"
        )

    for key, label in (
        ("deal_clock_surface_offenders", "gold.deal_clock"),
        ("merchant_clock_surface_offenders", "gold.merchant_clock"),
    ):
        offenders = findings.get(key) or []
        if offenders:
            failures.append(f"no-surface guard breached on {label}: {offenders}")

    if findings.get("paydown_out_of_range", 0) != 0:
        failures.append(
            f"{findings.get('paydown_out_of_range')} deals have est_paydown_pct outside [0,1]"
        )
    if findings.get("balance_below_zero", 0) != 0:
        failures.append(
            f"{findings.get('balance_below_zero')} deals have est_current_balance < 0"
        )

    if findings.get("closure_invalid", 0) != 0:
        failures.append(f"{findings.get('closure_invalid')} deals have an invalid closure_status")
    if findings.get("default_note_but_clean", 0) != 0:
        failures.append(
            f"Starr invariant breached: {findings.get('default_note_but_clean')} deals with a "
            "default note are closed_clean (default note must dominate -> closed_default)"
        )

    if findings.get("balance_source_non_estimated", 0) != 0:
        failures.append(
            f"{findings.get('balance_source_non_estimated')} deals have balance_source != "
            "'estimated' (v1 is estimated-path-only, D-203)"
        )

    for key, label in (
        ("est_weekly_revenue_non_null", "est_weekly_revenue has non-null values (faked?)"),
        ("burden_ratio_non_null", "burden_ratio has non-null values (faked?)"),
        ("revenue_flag_not_true", "est_weekly_revenue_is_missing flag not 100% true"),
        ("burden_flag_not_true", "burden_ratio_is_missing flag not 100% true"),
    ):
        if findings.get(key, 0) != 0:
            failures.append(f"{label}: {findings.get(key)} rows")

    # `*_current` view must resolve to exactly the one run_date we just built and match counts.
    cur_dates = findings.get("deal_current_run_dates") or []
    if cur_dates != [findings.get("run_date")]:
        failures.append(
            f"deal_clock_current is not the single latest run_date: {cur_dates} != "
            f"[{findings.get('run_date')}]"
        )
    if findings.get("deal_current_count") != findings.get("deal_clock_count"):
        failures.append("deal_clock_current count != latest deal_clock partition count")
    if findings.get("merchant_current_count") != findings.get("merchant_clock_count"):
        failures.append("merchant_clock_current count != latest merchant_clock partition count")

    return failures
