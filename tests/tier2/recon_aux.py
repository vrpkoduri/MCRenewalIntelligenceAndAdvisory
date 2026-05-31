"""Tier-2 reconciliation for the optional silver tables: offers + field_history.

Runs ON Databricks (needs Spark). Builds both tables into an isolated test schema
(`silver_test` by default; prod `silver` requires allow_prod=True — Rule 5), then asserts:

- row counts == their bronze sources (raw projections, no filter/drop);
- schemas == offers_schema() / field_history_schema() (the field_maps contract);
- FK sanity: offers.opportunity_id and field_history.opportunity_id resolve to the
  Opportunity book (orphan counts are diagnostic, reported not failed — bronze refreshes
  asynchronously across objects);
- a few useful rollups for the consuming sprints (selected-offer count; StageName-change
  event count that S3 uses for renewal-cadence reconstruction).
"""

from __future__ import annotations

from common import constants as C
from common.schemas.silver import field_history_schema, offers_schema
from transform.silver_field_history import build_silver_field_history
from transform.silver_offers import build_silver_offers


def run_aux_recon(
    spark,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.SILVER_TEST,
    bronze_schema: str = C.Schema.BRONZE,
    allow_prod: bool = False,
) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.SILVER and not allow_prod:
        raise ValueError(
            "Refusing to write prod 'silver' without allow_prod=True (Rule 5)."
        )

    findings: dict = {"catalog": catalog, "schema": schema, "bronze_schema": bronze_schema}

    opp = spark.read.table(C.fq(bronze_schema, C.BronzeTable.OPPORTUNITY, catalog))
    opp_ids = opp.select(F.col("Id").alias("opportunity_id")).distinct()

    # ---------- offers ----------
    bronze_offer_n = spark.read.table(
        C.fq(bronze_schema, C.BronzeTable.OFFER, catalog)
    ).count()
    offers_target = build_silver_offers(spark, catalog, schema, bronze_schema)
    offers = spark.read.table(offers_target)
    offers_cols = offers.columns
    offers_orphans = (
        offers.select("opportunity_id")
        .where(F.col("opportunity_id").isNotNull())
        .join(opp_ids, "opportunity_id", "left_anti")
        .count()
    )
    findings["offers"] = {
        "target_table": offers_target,
        "silver_count": offers.count(),
        "bronze_count": bronze_offer_n,
        "schema_matches": offers_cols == [f.name for f in offers_schema().fields],
        "selected_count": offers.where(F.col("is_selected") == F.lit(True)).count(),
        "opportunity_orphans": offers_orphans,
    }

    # ---------- field_history ----------
    bronze_fh_n = spark.read.table(
        C.fq(bronze_schema, C.BronzeTable.OPPORTUNITY_FIELD_HISTORY, catalog)
    ).count()
    fh_target = build_silver_field_history(spark, catalog, schema, bronze_schema)
    fh = spark.read.table(fh_target)
    fh_cols = fh.columns
    fh_orphans = (
        fh.select("opportunity_id")
        .where(F.col("opportunity_id").isNotNull())
        .join(opp_ids, "opportunity_id", "left_anti")
        .count()
    )
    findings["field_history"] = {
        "target_table": fh_target,
        "silver_count": fh.count(),
        "bronze_count": bronze_fh_n,
        "schema_matches": fh_cols == [f.name for f in field_history_schema().fields],
        "stagename_event_count": fh.where(F.col("field") == F.lit("StageName")).count(),
        "opportunity_orphans": fh_orphans,
    }

    return findings


def assert_aux(findings: dict) -> list[str]:
    """Hard checks: row counts == bronze source, schema contract. Orphans/rollups are
    diagnostic only (bronze objects refresh independently)."""
    failures: list[str] = []
    for name in ("offers", "field_history"):
        f = findings.get(name, {})
        if f.get("silver_count") != f.get("bronze_count"):
            failures.append(
                f"{name}: silver={f.get('silver_count')} != bronze={f.get('bronze_count')}"
            )
        if not f.get("schema_matches"):
            failures.append(f"{name}: schema drift vs contract")
    return failures
