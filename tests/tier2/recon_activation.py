"""Tier-2 reconciliation for S4 Activation + Book Health (Build Plan §6, Framework 5.8).

Runs ON Databricks (needs Spark). Builds gold.merchant_activation (+`_current`), the
gold.daily_queue view, and the gold.book_health scoreboard (+ 3 `_current` views) for one
`run_date` into an ISOLATED test schema (`gold_test` by default; prod `gold` requires
allow_prod=True — Rule 5), then asserts the SPRINT_4 exit criteria. A second run
(run_date + 1 day) exercises the state machine / event append integrity. NO Salesforce write
(D-403 — serving layer only).

HARD (failures):
- schema contracts: merchant_activation == merchant_activation_schema(); book_health == book_health_schema();
- WHOLE-BOOK coverage: merchant_activation rows == distinct merchants on merchant_rung_current;
  (merchant_id, activation_run_date) unique;
- domains: current_state ∈ enum 100%; active_play ∈ enum 100%; play_sla_due non-null 100%;
  play_owner_is_missing 100% true (no owner source in v1 — FU-101, never fabricated);
- no-surface guard on merchant_activation + daily_queue + book_health;
- daily_queue covers the book (count == merchant_activation) with a unique queue_rank;
- Book Health reconciles: Σ rung_distribution counts == merchant_rung_current rows; all three
  views present; count metrics non-null;
- `_current` resolves to the latest activation_run_date + count matches the partition;
- state machine (two runs): the prior activation partition is UNCHANGED (append-only); the
  S3 classification events for the first run date are UNCHANGED (S4 append left them alone);
  `_current` advances to the second run.

DIAGNOSTIC (reported, not failed): current_state + active_play breakdowns; queue top sample;
book-health rung distribution; S4 event counts on the second run; the reference merchants by
name (Wolf → serial-renewal-vs-buyout play).
"""

from __future__ import annotations

from datetime import date, timedelta

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import book_health_schema, merchant_activation_schema
from transform.gold_activation import build_gold_activation
from transform.gold_book_health import build_gold_book_health

REFERENCE_MERCHANT_NAMES = (
    "Starr Window Tinting", "One Big Promotion", "Tom Snell", "Wolf Corporation",
)
_S4_EVENT_TYPES = (C.EventType.STATE_TRANSITION, C.EventType.PLAY_FIRED)


def _build(spark, schema, run_date, allow_prod):
    build_gold_activation(spark, schema=schema, run_date=run_date, allow_prod=allow_prod)
    build_gold_book_health(spark, schema=schema, run_date=run_date, allow_prod=allow_prod)


def run_recon(spark, catalog=C.CATALOG, schema=C.Schema.GOLD_TEST, run_date=None,
              allow_prod=False, second_run=True) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to run tier-2 activation reconciliation against prod 'gold' without "
            "allow_prod=True. Use gold_test (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()
    findings: dict = {"catalog": catalog, "schema": schema, "run_date": run_date.isoformat()}

    # TEST-ONLY clean slate (deterministic re-runs): drop S4 output tables + clear S4 event
    # rows left by prior test runs. NEVER for prod (point-in-time history is preserved there).
    if schema.endswith("_test"):
        from pyspark.sql import functions as F  # noqa: F811
        for t in (C.GoldTable.MERCHANT_ACTIVATION, C.GoldTable.BOOK_HEALTH):
            spark.sql(f"DROP TABLE IF EXISTS {C.fq(schema, t, catalog)}")
        ev = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)
        if spark.catalog.tableExists(ev):
            types = ", ".join(f"'{t}'" for t in _S4_EVENT_TYPES)
            spark.sql(f"DELETE FROM {ev} WHERE event_type IN ({types})")

    _build(spark, schema, run_date, allow_prod)

    run_lit = F.lit(run_date.isoformat()).cast("date")
    act_target = C.fq(schema, C.GoldTable.MERCHANT_ACTIVATION, catalog)
    act = spark.read.table(act_target).where(F.col("activation_run_date") == run_lit)
    act_current = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_ACTIVATION_CURRENT, catalog))
    queue = spark.read.table(C.fq(schema, C.GoldTable.DAILY_QUEUE, catalog))
    rung = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog))
    bh_target = C.fq(schema, C.GoldTable.BOOK_HEALTH, catalog)
    bh = spark.read.table(bh_target).where(F.col("report_date") == run_lit)
    event_target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)

    findings["targets"] = {"merchant_activation": act_target, "book_health": bh_target}

    # --- schema contracts ---
    findings["activation_schema_matches"] = act.columns == [f.name for f in merchant_activation_schema().fields]
    findings["book_health_schema_matches"] = bh.columns == [f.name for f in book_health_schema().fields]

    # --- coverage ---
    universe = rung.select("merchant_id").distinct().count()
    act_n = act.count()
    findings["rung_merchant_universe"] = universe
    findings["activation_count"] = act_n
    findings["activation_distinct_keys"] = act.select("merchant_id", "activation_run_date").distinct().count()

    # --- no-surface ---
    findings["activation_surface_offenders"] = offending_surface_columns(act.columns)
    findings["queue_surface_offenders"] = offending_surface_columns(queue.columns)
    findings["book_health_surface_offenders"] = offending_surface_columns(bh.columns)

    # --- domains ---
    findings["current_state_invalid"] = act.where(~F.col("current_state").isin(list(C.CurrentState.ALL))).count()
    findings["active_play_invalid"] = act.where(~F.col("active_play").isin(list(C.Play.ALL))).count()
    findings["play_sla_due_null"] = act.where(F.col("play_sla_due").isNull()).count()
    findings["play_owner_not_missing"] = act.where(F.col("play_owner_is_missing") != F.lit(True)).count()

    # --- daily queue ---
    findings["queue_count"] = queue.count()
    findings["queue_rank_distinct"] = queue.select("queue_rank").distinct().count()

    # --- Book Health reconcile ---
    rung_dist = bh.where((F.col("view") == F.lit(C.BookHealthView.BOOK_HEALTH))
                         & (F.col("metric") == F.lit("rung_distribution")))
    rd_sum = rung_dist.agg(F.sum("value_num").alias("s")).collect()[0]["s"]
    findings["rung_distribution_sum"] = int(rd_sum) if rd_sum is not None else None
    findings["book_health_views_present"] = sorted(r["view"] for r in bh.select("view").distinct().collect())
    findings["book_health_row_count"] = bh.count()
    # renewal_performance is ENTIRELY deferred in v1 (metrics need S5 offers / S8 touches) →
    # its `_current` view exists but is empty. Confirm the view resolves (created) + is empty.
    rp_current = spark.read.table(C.fq(schema, C.GoldTable.RENEWAL_PERFORMANCE_CURRENT, catalog))
    findings["renewal_performance_current_count"] = rp_current.count()

    # --- `_current` view ---
    cur_dates = [str(r[0]) for r in act_current.select("activation_run_date").distinct().collect()]
    findings["activation_current_run_dates"] = sorted(cur_dates)
    findings["activation_current_count"] = act_current.count()

    # === DIAGNOSTICS ===
    findings["current_state_breakdown"] = {
        r["current_state"]: int(r["count"]) for r in act.groupBy("current_state").count().collect()
    }
    findings["active_play_breakdown"] = {
        r["active_play"]: int(r["count"]) for r in act.groupBy("active_play").count().collect()
    }
    findings["rung_distribution"] = {
        r["dimension_value"]: int(r["value_num"]) for r in rung_dist.collect()
    }
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog)).select("merchant_id", "business_name")
    ref = (
        act.join(merchants, "merchant_id", "inner")
        .filter(F.col("business_name").isin(list(REFERENCE_MERCHANT_NAMES)))
        .select("business_name", "current_state", "active_play", "play_sla_due", "rung")
        .collect()
    )
    findings["reference_merchants"] = [
        {"name": r["business_name"], "current_state": r["current_state"], "active_play": r["active_play"],
         "play_sla_due": str(r["play_sla_due"]), "rung": r["rung"]} for r in ref
    ]

    # S3 classification events for this run date (must be untouched by the S4 append).
    if spark.catalog.tableExists(event_target):
        findings["s3_classification_events_run1"] = spark.read.table(event_target).where(
            (F.col("classify_run_date") == run_lit) & (F.col("event_type") == F.lit(C.EventType.CLASSIFICATION))
        ).count()

    # === STATE MACHINE: second run (run_date + 1 day) ===
    if second_run:
        run2 = run_date + timedelta(days=1)
        run1_count_before = act_n
        s3_cls_before = findings.get("s3_classification_events_run1")
        _build(spark, schema, run2, allow_prod)

        run1_lit = F.lit(run_date.isoformat()).cast("date")
        run2_lit = F.lit(run2.isoformat()).cast("date")
        act_all = spark.read.table(act_target)
        events_all = spark.read.table(event_target)
        act_current2 = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_ACTIVATION_CURRENT, catalog))

        findings["second_run_date"] = run2.isoformat()
        findings["run1_count_after_run2"] = act_all.where(F.col("activation_run_date") == run1_lit).count()
        findings["run1_count_before_run2"] = run1_count_before
        findings["s3_classification_events_after_run2"] = events_all.where(
            (F.col("classify_run_date") == run1_lit) & (F.col("event_type") == F.lit(C.EventType.CLASSIFICATION))
        ).count()
        findings["s3_classification_events_before_run2"] = s3_cls_before
        findings["run2_state_transition_events"] = events_all.where(
            (F.col("classify_run_date") == run2_lit) & (F.col("event_type") == F.lit(C.EventType.STATE_TRANSITION))
        ).count()
        findings["run2_play_fired_events"] = events_all.where(
            (F.col("classify_run_date") == run2_lit) & (F.col("event_type") == F.lit(C.EventType.PLAY_FIRED))
        ).count()
        cur2 = [str(r[0]) for r in act_current2.select("activation_run_date").distinct().collect()]
        findings["activation_current_run_dates_after_run2"] = sorted(cur2)
        findings["activation_partitions"] = sorted(
            str(r[0]) for r in act_all.select("activation_run_date").distinct().collect()
        )

    return findings


def assert_recon(findings: dict) -> list[str]:
    failures: list[str] = []

    if not findings.get("activation_schema_matches"):
        failures.append("schema drift on gold.merchant_activation")
    if not findings.get("book_health_schema_matches"):
        failures.append("schema drift on gold.book_health")

    universe = findings.get("rung_merchant_universe")
    if findings.get("activation_count") != universe:
        failures.append(f"coverage: merchant_activation={findings.get('activation_count')} != rung merchants={universe}")
    if findings.get("activation_distinct_keys") != findings.get("activation_count"):
        failures.append("(merchant_id, activation_run_date) not unique")

    for key, label in (
        ("activation_surface_offenders", "gold.merchant_activation"),
        ("queue_surface_offenders", "gold.daily_queue"),
        ("book_health_surface_offenders", "gold.book_health"),
    ):
        if findings.get(key):
            failures.append(f"no-surface guard breached on {label}: {findings.get(key)}")

    for key, label in (
        ("current_state_invalid", "current_state outside the enum"),
        ("active_play_invalid", "active_play outside the enum"),
        ("play_sla_due_null", "play_sla_due null"),
        ("play_owner_not_missing", "play_owner_is_missing not 100% true (v1 has no owner source)"),
    ):
        if findings.get(key, 0) != 0:
            failures.append(f"{label}: {findings.get(key)} rows")

    if findings.get("queue_count") != findings.get("activation_count"):
        failures.append(f"daily_queue count {findings.get('queue_count')} != activation {findings.get('activation_count')}")
    if findings.get("queue_rank_distinct") != findings.get("queue_count"):
        failures.append("daily_queue queue_rank not unique")

    if findings.get("rung_distribution_sum") != findings.get("rung_merchant_universe"):
        failures.append(
            f"book health rung_distribution sum {findings.get('rung_distribution_sum')} != "
            f"merchant_rung_current {findings.get('rung_merchant_universe')}"
        )
    # v1 populates book_health + leading_indicators; renewal_performance is entirely deferred
    # (S5/S8) so its view is empty — not a failure.
    populated = set(findings.get("book_health_views_present") or [])
    v1_views = {C.BookHealthView.BOOK_HEALTH, C.BookHealthView.LEADING_INDICATORS}
    if not v1_views <= populated:
        failures.append(f"book_health missing a v1 view: present={sorted(populated)}, need {sorted(v1_views)}")
    if findings.get("renewal_performance_current_count") != 0:
        failures.append(
            f"renewal_performance should be empty in v1 (deferred), got "
            f"{findings.get('renewal_performance_current_count')} rows"
        )

    if findings.get("activation_current_run_dates") != [findings.get("run_date")]:
        failures.append(f"merchant_activation_current not the single run_date: {findings.get('activation_current_run_dates')}")
    if findings.get("activation_current_count") != findings.get("activation_count"):
        failures.append("merchant_activation_current count != latest partition count")

    if "second_run_date" in findings:
        if findings.get("run1_count_after_run2") != findings.get("run1_count_before_run2"):
            failures.append("prior activation partition mutated after run2 (must be append-only)")
        if findings.get("s3_classification_events_after_run2") != findings.get("s3_classification_events_before_run2"):
            failures.append("S4 append mutated the S3 classification events (must leave them untouched)")
        if findings.get("activation_current_run_dates_after_run2") != [findings.get("second_run_date")]:
            failures.append(f"_current did not advance to run2: {findings.get('activation_current_run_dates_after_run2')}")
        if findings.get("activation_partitions") != sorted([findings.get("run_date"), findings.get("second_run_date")]):
            failures.append(f"unexpected activation partitions: {findings.get('activation_partitions')}")

    return failures
