"""Tier-2 reconciliation for the bronze->silver `deals` transform (S0, task #11).

Runs ON Databricks (needs Spark). Builds `silver.deals` into an ISOLATED test schema
(`silver_test` by default — NEVER prod `silver`) from the live bronze tables, then
asserts the reconciliation + data-quality expectations locked at G1:

- funded book count == bronze Opportunity rows with StageName='Funded' (~3,959);
- schema == deals_schema() (column names + order — the field_maps contract);
- DQ flags populate sanely: selected_offer_missing ~= 2, multi_selected_offer ~= 28
  (the G1-profiled anomalies — see DECISIONS C-012);
- no-surface guard holds on the CONSUMED projection (the table carries `_sf_stored_*`
  checkpoint columns, but a downstream view must not expose them — CLAUDE.md 2.1);
- the four reference merchants (CLAUDE.md §8) are located by name and reported.

`run_recon` returns a JSON-serialisable dict of findings; `assert_recon` turns the
hard expectations into failures. Tolerances are wide on counts that legitimately drift
as bronze refreshes (the funded book grows); the structural checks are exact.
"""

from __future__ import annotations

from common import constants as C
from common.io.guards import assert_no_surface, offending_surface_columns
from common.schemas.silver import deals_schema
from transform.silver_deals import build_silver_deals

# Reference merchants (CLAUDE.md §8) — matched by name in the live book.
REFERENCE_MERCHANT_NAMES = (
    "Starr Window Tinting",
    "One Big Promotion",
    "Tom Snell",
    "Wolf Corporation",
)

# G1-profiled expectations (DECISIONS C-012). Counts allow drift as bronze refreshes;
# anomaly counts are checked within a tolerance band, not exactly.
EXPECTED_FUNDED_COUNT = 3959
SELECTED_OFFER_MISSING_MAX = 10      # G1 found 2; allow head-room for refresh churn
MULTI_SELECTED_OFFER_BAND = (10, 80)  # G1 found ~28


def run_recon(
    spark,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.SILVER_TEST,
    bronze_schema: str = C.Schema.BRONZE,
    allow_prod: bool = False,
) -> dict:
    """Build silver.deals into `schema` and gather reconciliation findings.

    Returns a dict (JSON-safe). Does not raise on expectation misses — call
    `assert_recon` on the result for that. Defaults to the test schema; writing prod
    `silver` requires an explicit `allow_prod=True` (Rule 5 — this writes a managed
    table) and is gated on the same reconciliation passing.
    """
    from pyspark.sql import functions as F

    if schema == C.Schema.SILVER and not allow_prod:
        raise ValueError(
            "Refusing to run tier-2 reconciliation against prod 'silver' without "
            "allow_prod=True. Use silver_test (Rule 5: this writes a managed table)."
        )

    findings: dict = {"catalog": catalog, "schema": schema, "bronze_schema": bronze_schema}

    # --- bronze baseline (the funded book is defined by StageName='Funded') ---
    opp = spark.read.table(C.fq(bronze_schema, C.BronzeTable.OPPORTUNITY, catalog))
    bronze_funded = opp.filter(F.col("StageName") == F.lit(C.FUNDED_STAGE)).count()
    findings["bronze_funded_count"] = bronze_funded

    # --- build the silver table into the test schema ---
    target = build_silver_deals(spark, catalog=catalog, schema=schema, bronze_schema=bronze_schema)
    findings["target_table"] = target

    deals = spark.read.table(target)
    row_count = deals.count()
    findings["silver_deals_count"] = row_count

    # --- schema contract: names + order must equal deals_schema() ---
    expected_cols = [f.name for f in deals_schema().fields]
    actual_cols = deals.columns
    findings["expected_columns"] = expected_cols
    findings["actual_columns"] = actual_cols
    findings["schema_matches"] = actual_cols == expected_cols

    # --- DQ flag rollups (single pass) ---
    agg = deals.select(
        F.sum(F.col("selected_offer_missing").cast("int")).alias("selected_offer_missing"),
        F.sum(F.col("multi_selected_offer").cast("int")).alias("multi_selected_offer"),
        F.sum(F.col("date_sanity_flag").cast("int")).alias("date_sanity_flag"),
        F.sum(F.col("rtr_check_flag").cast("int")).alias("rtr_check_flag"),
        F.sum(F.col("fico_is_missing").cast("int")).alias("fico_is_missing"),
        F.sum(F.col("months_in_business_is_missing").cast("int")).alias("months_in_business_is_missing"),
    ).collect()[0].asDict()
    findings["dq_counts"] = {k: (int(v) if v is not None else 0) for k, v in agg.items()}

    # --- no-surface guard (CLAUDE.md 2.1) ---
    # The base table legitimately CARRIES the frozen checkpoint columns; a CONSUMED
    # projection must drop them. Assert both: checkpoints present on base, guard clean
    # once they are projected away.
    findings["checkpoint_cols_on_base"] = sorted(
        c for c in actual_cols if c in C.NO_SURFACE_COLUMNS or c.startswith(C.SF_STORED_PREFIX)
    )
    consumed_cols = [c for c in actual_cols if not c.startswith(C.SF_STORED_PREFIX)]
    findings["consumed_surface_offenders"] = offending_surface_columns(consumed_cols)

    # --- reference merchants (by name) ---
    ref = (
        deals.filter(F.col("opportunity_name").isin(list(REFERENCE_MERCHANT_NAMES)))
        .groupBy("opportunity_name")
        .count()
        .collect()
    )
    found = {r["opportunity_name"]: int(r["count"]) for r in ref}
    findings["reference_merchants_found"] = {n: found.get(n, 0) for n in REFERENCE_MERCHANT_NAMES}

    return findings


def assert_recon(findings: dict) -> list[str]:
    """Turn findings into pass/fail. Returns the list of failures (empty == green).

    Hard (exact): schema contract, no-surface guard on the consumed projection,
    checkpoint columns present on base, silver count == bronze funded count.
    Soft (banded, warn-only here but surfaced): anomaly counts vs G1.
    """
    failures: list[str] = []

    if not findings.get("schema_matches"):
        failures.append(
            f"schema drift: expected {findings.get('expected_columns')} "
            f"got {findings.get('actual_columns')}"
        )

    offenders = findings.get("consumed_surface_offenders") or []
    if offenders:
        failures.append(f"no-surface guard breached on consumed projection: {offenders}")

    if not findings.get("checkpoint_cols_on_base"):
        failures.append("expected _sf_stored_* checkpoint columns on base table, found none")

    silver_n = findings.get("silver_deals_count")
    bronze_n = findings.get("bronze_funded_count")
    if silver_n != bronze_n:
        failures.append(
            f"funded-book reconciliation: silver_deals={silver_n} != bronze_funded={bronze_n}"
        )

    # G1 sanity (banded). Treated as failures because a large swing means the
    # selected-offer resolution (C-012) regressed.
    dq = findings.get("dq_counts", {})
    miss = dq.get("selected_offer_missing", 0)
    if miss > SELECTED_OFFER_MISSING_MAX:
        failures.append(
            f"selected_offer_missing={miss} exceeds tolerance {SELECTED_OFFER_MISSING_MAX} (G1=2)"
        )
    multi = dq.get("multi_selected_offer", 0)
    lo, hi = MULTI_SELECTED_OFFER_BAND
    if not (lo <= multi <= hi):
        failures.append(
            f"multi_selected_offer={multi} outside band {MULTI_SELECTED_OFFER_BAND} (G1~28)"
        )

    return failures
