"""Tier-2 reconciliation for the S3 Rung Classifier + Event Log (Appendix B).

Runs ON Databricks (needs Spark). Builds gold.merchant_rung (+`_current` view) and the
append-only gold.merchant_event_log for one `run_date` into an ISOLATED test schema
(`gold_test` by default; prod `gold` requires allow_prod=True — Rule 5), then asserts the
SPRINT_3 exit criteria. A second run (run_date + 1 day) exercises the state machine /
append integrity.

HARD (failures):
- schema contracts: merchant_rung == merchant_rung_schema(); event_log == event_log_schema();
- WHOLE-BOOK coverage: merchant_rung(run_date) row count == distinct merchants on
  merchant_clock_current; (merchant_id, classify_run_date) unique (one row/merchant/run);
- domains: lifecycle_state ∈ enum 100%; rung ∈ {1..5, null} 100%; confidence ∈ [0,1] and
  non-null 100%; route ∈ enum 100%;
- gated merchants (defaulted/dormant/new-establishing) have NULL rung + is_gated=true;
- Unclassified is an EXPLICIT, consistent bucket: is_unclassified ⇔ (active AND rung null);
  no active-with-rung is flagged unclassified, no gated row is flagged unclassified;
- no-surface guard: neither table exposes a `_sf_stored_*` column (CLAUDE.md 2.1/6);
- event log: exactly one classification event per merchant per run; (merchant_id,
  event_type, event_ts) unique; no-surface clean;
- `_current` view resolves to the latest classify_run_date and matches that partition's count;
- state machine (two runs): the prior run's partition is UNCHANGED after the second build
  (append-only, no mutation); both runs' classification events are present; `_current`
  advances to the second run.

DIAGNOSTIC (reported, not failed — legitimately drift with the book/run date):
- lifecycle_state breakdown; rung breakdown; **Unclassified pile count + top missing_signals**
  (the roadmap exit deliverable); direction_of_travel breakdown; rapid_reup_flag count;
  renewal_chain_incomplete count; transition count on the second run;
- the four reference merchants (CLAUDE.md §8) by name → lifecycle_state / rung / route
  (so Starr=defaulted/do-not-fund, OBP=dormant, Snell=new-establishing, Wolf=serial are
  eyeballable; only those present on Account.Name resolve — same FU-002 nuance as S2).
"""

from __future__ import annotations

from datetime import date, timedelta

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import event_log_schema, merchant_rung_schema
from transform.gold_rung import build_gold_rung

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
    second_run: bool = True,
) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to run tier-2 rung reconciliation against prod 'gold' without "
            "allow_prod=True. Use gold_test (Rule 5: this writes managed tables)."
        )

    run_date = run_date or date.today()
    findings: dict = {"catalog": catalog, "schema": schema, "run_date": run_date.isoformat()}

    # --- build the rung + event-log tables for run_date (run 1) ---
    targets = build_gold_rung(spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod)
    findings["targets"] = targets

    run_lit = F.lit(run_date.isoformat()).cast("date")
    rung = spark.read.table(targets["merchant_rung"]).where(F.col("classify_run_date") == run_lit)
    rung_current = spark.read.table(targets["merchant_rung_current"])
    events = spark.read.table(targets["merchant_event_log"]).where(F.col("classify_run_date") == run_lit)
    merchant_clock = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_CLOCK_CURRENT, catalog))

    # --- schema contracts ---
    findings["rung_schema_matches"] = rung.columns == [f.name for f in merchant_rung_schema().fields]
    findings["event_schema_matches"] = events.columns == [f.name for f in event_log_schema().fields]
    findings["rung_columns"] = rung.columns
    findings["event_columns"] = events.columns

    # --- whole-book coverage ---
    universe = merchant_clock.select("merchant_id").distinct().count()
    rung_n = rung.count()
    distinct_keys = rung.select("merchant_id", "classify_run_date").distinct().count()
    findings["clock_merchant_universe"] = universe
    findings["merchant_rung_count"] = rung_n
    findings["merchant_rung_distinct_keys"] = distinct_keys

    # --- no-surface ---
    findings["rung_surface_offenders"] = offending_surface_columns(rung.columns)
    findings["event_surface_offenders"] = offending_surface_columns(events.columns)

    # --- domain validity ---
    findings["lifecycle_invalid"] = rung.where(
        ~F.col("lifecycle_state").isin(list(C.LifecycleState.ALL))
    ).count()
    findings["rung_invalid"] = rung.where(
        F.col("rung").isNotNull() & ~F.col("rung").isin(list(C.RungState.ALL))
    ).count()
    findings["confidence_out_of_range"] = rung.where(
        F.col("confidence").isNull() | (F.col("confidence") < 0) | (F.col("confidence") > 1)
    ).count()
    findings["route_invalid"] = rung.where(
        ~F.col("route").isin(list(C.LifecycleRoute.ALL))
    ).count()

    # --- gated merchants: null rung + is_gated ---
    gated_states = [C.LifecycleState.DEFAULTED, C.LifecycleState.DORMANT, C.LifecycleState.NEW_ESTABLISHING]
    findings["gated_with_rung"] = rung.where(
        F.col("lifecycle_state").isin(gated_states) & F.col("rung").isNotNull()
    ).count()
    findings["gated_flag_mismatch"] = rung.where(
        F.col("lifecycle_state").isin(gated_states) != F.col("is_gated")
    ).count()

    # --- Unclassified is an explicit, consistent bucket ---
    is_unclassified_truth = (F.col("lifecycle_state") == F.lit(C.LifecycleState.ACTIVE)) & F.col("rung").isNull()
    findings["unclassified_flag_mismatch"] = rung.where(
        is_unclassified_truth != F.col("is_unclassified")
    ).count()
    findings["unclassified_count"] = rung.where(F.col("is_unclassified")).count()

    # --- event log: one classification per merchant per run; keys unique ---
    classification = events.where(F.col("event_type") == F.lit(C.EventType.CLASSIFICATION))
    findings["classification_event_count"] = classification.count()
    findings["event_key_count"] = events.count()
    findings["event_distinct_keys"] = events.select("merchant_id", "event_type", "event_ts").distinct().count()

    # --- `_current` view = latest classify_run_date + count match ---
    cur_dates = [str(r[0]) for r in rung_current.select("classify_run_date").distinct().collect()]
    findings["rung_current_run_dates"] = sorted(cur_dates)
    findings["rung_current_count"] = rung_current.count()

    # === DIAGNOSTICS ===
    findings["lifecycle_breakdown"] = {
        r["lifecycle_state"]: int(r["count"]) for r in rung.groupBy("lifecycle_state").count().collect()
    }
    findings["rung_breakdown"] = {
        (str(r["rung"]) if r["rung"] is not None else "null"): int(r["count"])
        for r in rung.groupBy("rung").count().collect()
    }
    findings["direction_breakdown"] = {
        r["direction_of_travel"]: int(r["count"]) for r in rung.groupBy("direction_of_travel").count().collect()
    }
    findings["rapid_reup_count"] = rung.where(F.col("rapid_reup_flag")).count()
    findings["renewal_chain_incomplete_count"] = rung.where(F.col("renewal_chain_incomplete")).count()
    findings["gated_count"] = rung.where(F.col("is_gated")).count()

    # Top missing_signals among Unclassified merchants (the data-capture roadmap deliverable).
    uncl = rung.where(F.col("is_unclassified") & F.col("missing_signals").isNotNull())
    exploded = uncl.select(F.explode(F.split(F.col("missing_signals"), ",")).alias("sig"))
    findings["unclassified_top_missing_signals"] = {
        r["sig"]: int(r["count"]) for r in exploded.groupBy("sig").count().orderBy(F.desc("count")).limit(10).collect()
    }

    # Reference merchants by name (FU-002: only Account.Name matches resolve).
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog)).select("merchant_id", "business_name")
    ref = (
        rung.join(merchants, "merchant_id", "inner")
        .filter(F.col("business_name").isin(list(REFERENCE_MERCHANT_NAMES)))
        .select("business_name", "lifecycle_state", "rung", "route", "rapid_reup_flag", "default_subtype")
        .collect()
    )
    findings["reference_merchants"] = [
        {
            "name": r["business_name"],
            "lifecycle_state": r["lifecycle_state"],
            "rung": r["rung"],
            "route": r["route"],
            "rapid_reup_flag": bool(r["rapid_reup_flag"]),
            "default_subtype": r["default_subtype"],
        }
        for r in ref
    ]

    # === STATE MACHINE: a second run on run_date + 1 day ===
    if second_run:
        run2 = run_date + timedelta(days=1)
        run1_count_before = rung_n
        build_gold_rung(spark, catalog=catalog, schema=schema, run_date=run2, allow_prod=allow_prod)

        run1_lit = F.lit(run_date.isoformat()).cast("date")
        run2_lit = F.lit(run2.isoformat()).cast("date")
        rung_all = spark.read.table(targets["merchant_rung"])
        events_all = spark.read.table(targets["merchant_event_log"])
        rung_current2 = spark.read.table(targets["merchant_rung_current"])

        findings["second_run_date"] = run2.isoformat()
        # prior run partition UNCHANGED (append-only, no mutation)
        findings["run1_count_after_run2"] = rung_all.where(F.col("classify_run_date") == run1_lit).count()
        findings["run1_count_before_run2"] = run1_count_before
        # both runs have classification events
        findings["run1_classification_events"] = events_all.where(
            (F.col("classify_run_date") == run1_lit) & (F.col("event_type") == F.lit(C.EventType.CLASSIFICATION))
        ).count()
        findings["run2_classification_events"] = events_all.where(
            (F.col("classify_run_date") == run2_lit) & (F.col("event_type") == F.lit(C.EventType.CLASSIFICATION))
        ).count()
        # transitions detected on run2 (diagnostic — may be 0 if nothing shifted)
        findings["run2_transition_events"] = events_all.where(
            (F.col("classify_run_date") == run2_lit) & (F.col("event_type") == F.lit(C.EventType.TRANSITION))
        ).count()
        # `_current` advances to run2
        cur2 = [str(r[0]) for r in rung_current2.select("classify_run_date").distinct().collect()]
        findings["rung_current_run_dates_after_run2"] = sorted(cur2)
        findings["rung_partitions"] = sorted(
            str(r[0]) for r in rung_all.select("classify_run_date").distinct().collect()
        )

    return findings


def assert_recon(findings: dict) -> list[str]:
    """Hard expectations -> failures (empty == green). Diagnostics are not asserted."""
    failures: list[str] = []

    if not findings.get("rung_schema_matches"):
        failures.append("schema drift on gold.merchant_rung")
    if not findings.get("event_schema_matches"):
        failures.append("schema drift on gold.merchant_event_log")

    universe = findings.get("clock_merchant_universe")
    if findings.get("merchant_rung_count") != universe:
        failures.append(
            f"coverage: merchant_rung={findings.get('merchant_rung_count')} != clock merchants={universe}"
        )
    if findings.get("merchant_rung_distinct_keys") != findings.get("merchant_rung_count"):
        failures.append(
            f"(merchant_id, classify_run_date) not unique: distinct="
            f"{findings.get('merchant_rung_distinct_keys')} != rows={findings.get('merchant_rung_count')}"
        )

    for key, label in (
        ("rung_surface_offenders", "gold.merchant_rung"),
        ("event_surface_offenders", "gold.merchant_event_log"),
    ):
        if findings.get(key):
            failures.append(f"no-surface guard breached on {label}: {findings.get(key)}")

    for key, label in (
        ("lifecycle_invalid", "lifecycle_state outside the enum"),
        ("rung_invalid", "rung outside {1..5,null}"),
        ("confidence_out_of_range", "confidence null or outside [0,1]"),
        ("route_invalid", "route outside the enum"),
        ("gated_with_rung", "gated merchants carrying a non-null rung"),
        ("gated_flag_mismatch", "is_gated flag != (lifecycle is gated)"),
        ("unclassified_flag_mismatch", "is_unclassified flag != (active AND rung null)"),
    ):
        if findings.get(key, 0) != 0:
            failures.append(f"{label}: {findings.get(key)} rows")

    # event log: one classification per merchant per run; keys unique.
    if findings.get("classification_event_count") != findings.get("merchant_rung_count"):
        failures.append(
            f"classification events={findings.get('classification_event_count')} != "
            f"merchants={findings.get('merchant_rung_count')}"
        )
    if findings.get("event_distinct_keys") != findings.get("event_key_count"):
        failures.append(
            f"event keys not unique: distinct={findings.get('event_distinct_keys')} != "
            f"rows={findings.get('event_key_count')}"
        )

    # `_current` view (before the second run) resolves to exactly run_date and matches counts.
    if findings.get("rung_current_run_dates") != [findings.get("run_date")]:
        failures.append(
            f"merchant_rung_current is not the single run_date: "
            f"{findings.get('rung_current_run_dates')} != [{findings.get('run_date')}]"
        )
    if findings.get("rung_current_count") != findings.get("merchant_rung_count"):
        failures.append("merchant_rung_current count != latest merchant_rung partition count")

    # state machine (two runs)
    if "second_run_date" in findings:
        if findings.get("run1_count_after_run2") != findings.get("run1_count_before_run2"):
            failures.append(
                f"prior run mutated: run1 count {findings.get('run1_count_before_run2')} -> "
                f"{findings.get('run1_count_after_run2')} after run2 (must be append-only)"
            )
        if findings.get("run1_classification_events") != findings.get("run1_count_before_run2"):
            failures.append("run1 classification events != run1 merchant count after run2")
        if findings.get("run2_classification_events") != findings.get("merchant_rung_count"):
            failures.append("run2 classification events != merchant count")
        if findings.get("rung_current_run_dates_after_run2") != [findings.get("second_run_date")]:
            failures.append(
                f"_current did not advance to run2: {findings.get('rung_current_run_dates_after_run2')}"
            )
        if findings.get("rung_partitions") != sorted([findings.get("run_date"), findings.get("second_run_date")]):
            failures.append(f"unexpected rung partitions: {findings.get('rung_partitions')}")

    return failures
