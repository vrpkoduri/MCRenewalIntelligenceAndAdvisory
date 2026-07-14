"""Gold -> Gold: the Data Steward agentic extraction (Framework §5.9, S7 Phase 1, D-704).

The Data Steward reads the free-text servicing `Notes` for every defaulted position (the clock's
`closed_default` deals) and sub-types the default cause — true-default vs early-payoff/clawback vs
restructured — which the S2/S3 spine cannot do from terms alone (it only knows "a default note
exists"). The agent EXTRACTS the label; the deterministic tools (`apply_default_subtype` +
`make_extraction`, tier-1 tested) GATE + GROUND it; the S3 classifier (re-run) ROUTES on the
APPLIED result. The agent never writes a spine table — it writes ONLY `gold.merchant_extraction`,
the durable, fully-audited extraction record.

Outputs (mirroring the S2/S3 point-in-time pattern):
  - gold.merchant_extraction : point-in-time, keyed (merchant_id, deal_id, extraction_run_date,
                               extraction_type), partitioned by extraction_run_date, append-only
                               across days, idempotent within a day (`replaceWhere`); + `_current`.
  - gold.merchant_event_log  : appends `agent_extraction` events to the same wide log (D-305),
                               idempotent via delete-then-append on this run's event type (leaves
                               S3/S4 rows untouched).

The LLM call is INJECTED (`predict_fn`); `build_extraction_rows` is pure orchestration over the
common tools (no Spark) so the whole agent→gate→ground path is tier-1 testable with a fake model.
The real caller (`databricks_chat_predict_fn`) hits a Databricks Foundation Model (Claude) via the
MLflow deployments client. Prod `gold` writes are approval-gated (Rule 5).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from common import constants as C
from common.agents.data_steward import DEFAULT_ENDPOINT, MODEL_VERSION, build_extraction_rows
from common.eventlog.events import agent_extraction_event, event_log_columns
from common.field_maps import merchant_extraction_columns
from common.schemas.gold import event_log_schema, merchant_extraction_schema

_S7_EVENT_TYPES = (C.EventType.AGENT_EXTRACTION,)


# --- the real Foundation Model caller (Databricks) -------------------------------


def databricks_chat_predict_fn():
    """A `predict_fn(endpoint, messages, max_tokens)` that calls a Databricks Foundation Model
    chat endpoint via the Databricks SDK (`databricks-sdk` is preinstalled on serverless +
    classic runtimes — no `mlflow` / provisioning needed). temperature=0 for reproducibility —
    the extraction is an audit record."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

    w = WorkspaceClient()
    _ROLE = {
        "system": ChatMessageRole.SYSTEM,
        "user": ChatMessageRole.USER,
        "assistant": ChatMessageRole.ASSISTANT,
    }

    def _fn(endpoint, messages, max_tokens=300):
        chat = [ChatMessage(role=_ROLE[m["role"]], content=m["content"]) for m in messages]
        resp = w.serving_endpoints.query(
            name=endpoint, messages=chat, max_tokens=max_tokens, temperature=0.0
        )
        return resp.choices[0].message.content

    return _fn


# --- source assembly -------------------------------------------------------------


def closed_default_deals(spark: SparkSession, catalog: str, schema: str) -> DataFrame:
    """(deal_id, merchant_id, notes) for every deal whose clock closure is `closed_default` — the
    defaulted positions the Data Steward sub-types. Notes come from SILVER (gold.deals does not
    surface free-text Notes); join is left so an evidence-less default still gets a row (→ unknown)."""
    deal_clock = (
        spark.read.table(C.fq(schema, C.GoldTable.DEAL_CLOCK_CURRENT, catalog))
        .select("deal_id", "merchant_id", "closure_status")
        .where(F.col("closure_status") == F.lit(C.ClosureStatus.CLOSED_DEFAULT))
    )
    notes = spark.read.table(C.fq(C.Schema.SILVER, C.SilverTable.DEALS, catalog)).select(
        F.col("opportunity_id").alias("deal_id"), F.col("notes")
    )
    return deal_clock.join(notes, "deal_id", "left").select("merchant_id", "deal_id", "notes")


# --- Spark write -----------------------------------------------------------------


def _event_ts(run_date: date) -> datetime:
    return datetime.combine(run_date, time())


def _to_decimal(v):
    return None if v is None else Decimal(str(round(float(v), 4)))


def _extraction_df(spark: SparkSession, rows: list[dict]) -> DataFrame:
    """Build the typed gold.merchant_extraction DataFrame (schema-ordered, empty-safe)."""
    schema = merchant_extraction_schema()
    cols = merchant_extraction_columns()

    def _tuple(ext):
        return tuple(
            _to_decimal(ext.get(c)) if c == "confidence" else ext.get(c) for c in cols
        )

    return spark.createDataFrame([_tuple(e) for e in rows], schema=schema)


def _events_df(spark: SparkSession, rows: list[dict], run_date: date) -> DataFrame:
    """Build the typed agent_extraction event DataFrame (the shared wide event-log schema)."""
    schema = event_log_schema()
    cols = event_log_columns()
    ts = _event_ts(run_date)

    def _tuple(ext):
        ev = agent_extraction_event(ext["merchant_id"], run_date, ext, ts)
        return tuple(_to_decimal(ev.get(c)) if c == "confidence" else ev.get(c) for c in cols)

    return spark.createDataFrame([_tuple(e) for e in rows], schema=schema)


def _write_point_in_time(df: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    writer = df.write.format("delta").partitionBy("extraction_run_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"extraction_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_current_view(spark: SparkSession, base: str, view: str) -> None:
    """`_current` = the latest row per (merchant_id, deal_id, extraction_type) — NOT the global
    latest run_date. `merchant_extraction` is a MULTI-STREAM table (Data Steward `default_subtype`
    + Statement Analyst positions/burden/revenue) written on INDEPENDENT cadences; a global-MAX view
    would evict an older stream (e.g. default_subtype) the moment a newer stream is written — which
    the S3 classifier reads for `resolved_default_subtype`, silently reverting resolved defaults.
    Per-key latest keeps every stream's most-recent extraction current."""
    spark.sql(
        f"CREATE OR REPLACE VIEW {view} AS SELECT * EXCEPT (_rn) FROM ("
        f"SELECT *, ROW_NUMBER() OVER (PARTITION BY merchant_id, deal_id, extraction_type "
        f"ORDER BY extraction_run_date DESC) AS _rn FROM {base}) WHERE _rn = 1"
    )


def _append_extraction_events(events: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    """Delete-then-append this run's `agent_extraction` rows (leaves S3/S4 events untouched)."""
    if not spark.catalog.tableExists(target):
        events.write.format("delta").partitionBy("classify_run_date").mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(target)
        return
    types = ", ".join(f"'{t}'" for t in _S7_EVENT_TYPES)
    spark.sql(
        f"DELETE FROM {target} WHERE classify_run_date = date'{run_date.isoformat()}' "
        f"AND event_type IN ({types})"
    )
    if events.limit(1).count() == 0:
        return
    events.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)


def build_gold_extraction(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    run_date: date | None = None,
    allow_prod: bool = False,
    predict_fn=None,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> dict:
    """Entry point: the Data Steward sub-types every `closed_default` deal's Notes for `run_date`
    and writes point-in-time gold.merchant_extraction (+`_current`) + `agent_extraction` events.

    `predict_fn` defaults to a Databricks Foundation Model caller; inject a fake for offline runs.
    Prod `gold` writes are approval-gated (Rule 5): use gold_test first; prod needs schema=gold
    AND allow_prod=True. Returns the fq targets + a small applied/review/rejected summary."""
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build agentic extraction into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables + spends on the LLM)."
        )

    run_date = run_date or date.today()
    predict_fn = predict_fn or databricks_chat_predict_fn()
    ext_target = C.fq(schema, C.GoldTable.MERCHANT_EXTRACTION, catalog)
    ext_current = C.fq(schema, C.GoldTable.MERCHANT_EXTRACTION_CURRENT, catalog)
    event_target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)

    records = [r.asDict() for r in closed_default_deals(spark, catalog, schema).collect()]
    rows = build_extraction_rows(
        records, run_date, predict_fn, endpoint=endpoint, model_version=model_version
    )

    ext_df = _extraction_df(spark, rows)
    _write_point_in_time(ext_df, ext_target, run_date, spark)
    _create_current_view(spark, ext_target, ext_current)

    events = _events_df(spark, rows, run_date)
    _append_extraction_events(events, event_target, run_date, spark)

    def _count(status):
        return sum(1 for r in rows if r["review_status"] == status)

    return {
        "merchant_extraction": ext_target,
        "merchant_extraction_current": ext_current,
        "merchant_event_log": event_target,
        "extraction_run_date": run_date.isoformat(),
        "endpoint": endpoint,
        "model_version": model_version,
        "closed_default_deals": len(rows),
        "applied": _count(C.ReviewStatus.APPLIED),
        "review": _count(C.ReviewStatus.REVIEW),
        "rejected": _count(C.ReviewStatus.REJECTED),
    }
