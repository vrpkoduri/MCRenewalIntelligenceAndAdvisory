"""Gold -> Gold: the Statement Analyst agentic extraction (Framework §5.9, S7 Phase 2, C-026).

Reads the OCR'd `silver[_test].statement_text`, picks the **most-recent statement per deal within
the freshness window** (D-714 — one point-in-time burden snapshot per deal; multi-month combine is
deferred), and has the Statement Analyst agent EXTRACT the concurrent positions + weekly debit +
operating-revenue deposits. The deterministic tools COUNT/normalize (`positions.summarize_statement`)
and GROUND/GATE (`grounding.make_extraction`); the agent never writes a spine table — it writes ONLY
`gold.merchant_extraction` (the SAME point-in-time table the Data Steward uses; new extraction_types)
+ `agent_extraction` events. Per C-026 #1 these rows are advisory-only — the spine's rung waterfall
never consumes them; they are surfaced (dashboard) and stamped `as_of_date`.

Writes are scoped to the three statement extraction_types via delete-then-append so a same-day Data
Steward `default_subtype` write is never clobbered. Prod `gold` writes are approval-gated (Rule 5).
The Foundation Model caller + the typed-DataFrame/event/write helpers are REUSED from
`gold_extraction` (Rule 3 — the logic lives once).
"""

from __future__ import annotations

from datetime import date

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

from common import constants as C
from common.agents.statement_analyst import DEFAULT_ENDPOINT, MODEL_VERSION, build_statement_extractions
from transform.gold_extraction import (
    _append_extraction_events,
    _create_current_view,
    _events_df,
    _extraction_df,
    databricks_chat_predict_fn,
)

_STMT_TYPES = (
    C.ExtractionType.CONCURRENT_POSITIONS,
    C.ExtractionType.WEEKLY_DEBIT,
    C.ExtractionType.EST_WEEKLY_REVENUE,
)


def statement_records(spark: SparkSession, catalog: str, silver_schema: str) -> list[dict]:
    """One record per covered deal = its MOST-RECENT statement (D-714): {merchant_id, deal_id,
    statement_text, source_ref, as_of_date}. merchant_id from the canonical PROD `gold.deals`."""
    txt = spark.read.table(C.fq(silver_schema, "statement_text", catalog)).select(
        "cv_id", "deal_id", "as_of_date", "text"
    )
    w = Window.partitionBy("deal_id").orderBy(F.col("as_of_date").desc_nulls_last(), F.col("cv_id").desc())
    recent = txt.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") == 1).drop("_rn")
    deals = spark.read.table(C.fq(C.Schema.GOLD, C.GoldTable.DEALS, catalog)).select("deal_id", "merchant_id")
    joined = recent.join(deals, "deal_id", "inner")
    return [
        {
            "merchant_id": r["merchant_id"],
            "deal_id": r["deal_id"],
            "statement_text": r["text"],
            "as_of_date": (str(r["as_of_date"]) if r["as_of_date"] is not None else None),
            "source_ref": f"salesforce.contentversion:{r['cv_id']}",
        }
        for r in joined.collect()
    ]


def _write_statement_extractions(df, target: str, run_date: date, spark: SparkSession) -> None:
    """Delete-then-append THIS run's statement extraction_types (leaves default_subtype rows intact)."""
    if not spark.catalog.tableExists(target):
        df.write.format("delta").partitionBy("extraction_run_date").mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(target)
        return
    types = ", ".join(f"'{t}'" for t in _STMT_TYPES)
    spark.sql(
        f"DELETE FROM {target} WHERE extraction_run_date = date'{run_date.isoformat()}' "
        f"AND extraction_type IN ({types})"
    )
    if df.limit(1).count() == 0:
        return
    df.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)


def _write_audit(spark: SparkSession, audit: list[dict], target: str, run_date: date) -> None:
    """Write the agent's full per-statement parse (audit trail). Delete-then-append on run_date."""
    if not audit:
        return
    df = spark.createDataFrame(audit)
    if not spark.catalog.tableExists(target):
        df.write.format("delta").partitionBy("extraction_run_date").mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(target)
        return
    spark.sql(f"DELETE FROM {target} WHERE extraction_run_date = date'{run_date.isoformat()}'")
    df.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)


def build_gold_statement_extraction(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD_TEST,
    silver_schema: str | None = None,
    run_date: date | None = None,
    allow_prod: bool = False,
    predict_fn=None,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> dict:
    """Run the Statement Analyst over the covered deals' most-recent statements for `run_date` and
    write point-in-time gold.merchant_extraction (+`_current`) + `agent_extraction` events.
    `gold_test` first; prod needs schema=gold AND allow_prod=True (Rule 5)."""
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build statement extraction into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables + spends on the LLM)."
        )
    run_date = run_date or date.today()
    silver_schema = silver_schema or (C.Schema.SILVER_TEST if schema == C.Schema.GOLD_TEST else C.Schema.SILVER)
    predict_fn = predict_fn or databricks_chat_predict_fn()

    ext_target = C.fq(schema, C.GoldTable.MERCHANT_EXTRACTION, catalog)
    ext_current = C.fq(schema, C.GoldTable.MERCHANT_EXTRACTION_CURRENT, catalog)
    event_target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)

    records = statement_records(spark, catalog, silver_schema)
    result = build_statement_extractions(records, run_date, predict_fn, endpoint=endpoint, model_version=model_version)
    rows, audit = result["rows"], result["audit"]

    ext_df = _extraction_df(spark, rows)
    _write_statement_extractions(ext_df, ext_target, run_date, spark)
    _create_current_view(spark, ext_target, ext_current)

    audit_target = C.fq(schema, C.GoldTable.STATEMENT_EXTRACTION_AUDIT, catalog)
    _write_audit(spark, audit, audit_target, run_date)

    events = _events_df(spark, rows, run_date)
    _append_extraction_events(events, event_target, run_date, spark)

    def _count(status):
        return sum(1 for r in rows if r["review_status"] == status)

    return {
        "merchant_extraction": ext_target,
        "merchant_extraction_current": ext_current,
        "statement_extraction_audit": audit_target,
        "merchant_event_log": event_target,
        "extraction_run_date": run_date.isoformat(),
        "silver_schema": silver_schema,
        "endpoint": endpoint,
        "model_version": model_version,
        "covered_deals": len(records),
        "extraction_rows": len(rows),
        "applied": _count(C.ReviewStatus.APPLIED),
        "review": _count(C.ReviewStatus.REVIEW),
        "rejected": _count(C.ReviewStatus.REJECTED),
    }
