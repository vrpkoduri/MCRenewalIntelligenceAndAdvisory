"""Gold -> Gold: the Advisory layer (Framework §2.3/§2.4/§5.9, S8, C-031).

The Advisory Composer + Structure Advisor turn the spine's ALREADY-COMPUTED facts (clock paydown/
balance, positions, the S5 renewal-vs-buyout math, predictions) into an honest, grounded,
merchant-facing advisory — and EVERY output passes the first-class deterministic compliance gate.
The agent ARTICULATES; the deterministic tools own the fact pack, the grounding validator, the
gate verdict, and the S5 structure math (`common/advisory` + `common/compliance` + `common/offer`,
all tier-1 tested). The agent never writes a spine table — it writes ONLY `gold.merchant_advisory`
(+`_current`) + `advisory_composed`/`compliance_checked` events.

**S8 COMPOSES + GATES; it does NOT send.** A BLOCKED or ungrounded advisory is stored + auditable
(compliance_status / review_status) but never marked deliverable (review_status != applied);
delivery is a separate later gated step (S9+). Prod `gold` writes are approval-gated (Rule 5).

v1 signal coverage (honest): the driver reads the facts that exist in PROD today — clock, rung,
predictions, the merchant's most-recent ACTIVE position (for the double-dip factor/balance). The
SPECIFIC-OFFER path (naming a concrete advance amount) depends on `gold.merchant_offers` (FU-501,
gated); until that lands the driver degrades gracefully to grounded ADVICE + structure guidance
(offer_type=None → no concrete offer term → the gate classifies ADVICE / FACTUAL_SUMMARY). The
Foundation Model caller + the confidence-decimal/event helpers are REUSED from `gold_extraction`
(Rule 3 — the logic lives once).
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from common import constants as C
from common.advisory.composer import DEFAULT_ENDPOINT, MODEL_VERSION, compose_advisory
from common.eventlog.events import build_advisory_events, event_log_columns
from common.field_maps import merchant_advisory_columns
from common.schemas.gold import event_log_schema, merchant_advisory_schema
from transform.gold_extraction import databricks_chat_predict_fn

_S8_EVENT_TYPES = (C.EventType.ADVISORY_COMPOSED, C.EventType.COMPLIANCE_CHECKED)


# --- source assembly -------------------------------------------------------------


def _current_position(spark: SparkSession, catalog: str, schema: str) -> DataFrame:
    """The merchant's MOST-RECENT ACTIVE position (deal_clock_current ⋈ gold.deals), ordered by
    funded_date — the position a renewal would roll, so its factor/balance/paydown drive the
    honest double-dip figure. One row per merchant."""
    dc = (
        spark.read.table(C.fq(schema, C.GoldTable.DEAL_CLOCK_CURRENT, catalog))
        .where(F.col("closure_status") == F.lit(C.ClosureStatus.ACTIVE))
        .select("deal_id", "merchant_id", "est_current_balance", "est_paydown_pct",
                "est_renewal_eligible_date")
    )
    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog)).select(
        "deal_id", "funded_date", "factor_rate", "payment_amount"
    )
    joined = dc.join(deals, "deal_id", "inner")
    w = Window.partitionBy("merchant_id").orderBy(
        F.col("funded_date").desc_nulls_last(), F.col("deal_id").desc()
    )
    return joined.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") == 1).drop("_rn")


def _offers_if_present(spark: SparkSession, catalog: str, schema: str) -> DataFrame | None:
    """merchant_offers_current is gated on FU-501 (routing-team handoff) — read it only if it
    exists so the SPECIFIC-OFFER path lights up automatically once offers land, and the driver
    degrades to advice-only until then (no fabricated offer)."""
    tbl = C.fq(schema, C.GoldTable.MERCHANT_OFFERS_CURRENT, catalog)
    if not spark.catalog.tableExists(tbl):
        return None
    return spark.read.table(tbl).select(
        "merchant_id", "eligible_offer_types", "max_sustainable_advance", "suitability_verdict"
    )


def _primary_offer_type(eligible_offer_types) -> str | None:
    """The single candidate offer type to compose for, from the comma-joined `eligible_offer_types`
    (prefer renewal > buyout > larger-advance). None / none-yet → advice-only."""
    if not eligible_offer_types:
        return None
    types = {t.strip() for t in str(eligible_offer_types).split(",") if t.strip()}
    for t in (C.OfferType.RENEWAL, C.OfferType.BUYOUT, C.OfferType.LARGER_ADVANCE):
        if t in types:
            return t
    return None


def advisory_records(
    spark: SparkSession,
    catalog: str,
    schema: str,
    sample_merchant_ids: list[str] | None = None,
) -> list[dict]:
    """One record per ACTIVE merchant for the Composer: {merchant_id, signals, offer_type,
    governing_state}. Universe = active merchants (merchant_rung_current) that have a most-recent
    active position. `sample_merchant_ids` restricts to a labeled/sample set (sample-first, Rule 5
    cost discipline). offer_type/offer_amount come from merchant_offers_current when present
    (FU-501), else None (advice-only)."""
    rung = (
        spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog))
        .where(F.col("lifecycle_state") == F.lit(C.LifecycleState.ACTIVE))
        .select("merchant_id", "rung", "route")
    )
    clock = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_CLOCK_CURRENT, catalog)).select(
        "merchant_id", "active_position_cnt", "total_weekly_debit"
    )
    pos = _current_position(spark, catalog, schema)
    preds = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_PREDICTIONS_CURRENT, catalog)).select(
        "merchant_id", "predicted_next_event_date"
    )
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog)).select(
        "merchant_id", "governing_state"
    )

    df = (
        rung.join(clock, "merchant_id", "left")
        .join(pos, "merchant_id", "inner")  # must have a current active position to advise structure
        .join(preds, "merchant_id", "left")
        .join(merchants, "merchant_id", "left")
    )
    offers = _offers_if_present(spark, catalog, schema)
    if offers is not None:
        df = df.join(offers, "merchant_id", "left")

    if sample_merchant_ids:
        df = df.where(F.col("merchant_id").isin(list(sample_merchant_ids)))

    records = []
    for r in df.collect():
        d = r.asDict()
        offer_type = _primary_offer_type(d.get("eligible_offer_types")) if offers is not None else None
        # An offer is only "surfaceable" if the S5 gate already said surface; otherwise the
        # Composer stays advice-only and the honest structure recommendation carries (the compliance
        # gate still hard-blocks a specific offer that isn't surface — defence in depth).
        signals = {
            "est_paydown_pct": _f(d.get("est_paydown_pct")),
            "est_current_balance": _f(d.get("est_current_balance")),
            "factor_rate": _f(d.get("factor_rate")),
            "active_position_cnt": d.get("active_position_cnt"),
            "payment_amount": _f(d.get("payment_amount")),
            "weekly_debit": _f(d.get("total_weekly_debit")),
            "est_renewal_eligible_date": d.get("est_renewal_eligible_date"),
            "predicted_next_event_date": d.get("predicted_next_event_date"),
            "offer_amount": _f(d.get("max_sustainable_advance")) if offers is not None else None,
        }
        records.append({
            "merchant_id": d["merchant_id"],
            "signals": signals,
            "offer_type": offer_type,
            "governing_state": d.get("governing_state"),
        })
    return records


def _f(v):
    return None if v is None else float(v)


# --- Spark write -----------------------------------------------------------------


def _event_ts(run_date: date) -> datetime:
    return datetime.combine(run_date, time())


def _to_decimal(v):
    return None if v is None else Decimal(str(round(float(v), 4)))


def _advisory_df(spark: SparkSession, rows: list[dict]) -> DataFrame:
    """Typed gold.merchant_advisory DataFrame (schema-ordered, empty-safe)."""
    schema = merchant_advisory_schema()
    cols = merchant_advisory_columns()

    def _tuple(a):
        return tuple(_to_decimal(a.get(c)) if c == "confidence" else a.get(c) for c in cols)

    return spark.createDataFrame([_tuple(a) for a in rows], schema=schema)


def _advisory_events_df(spark: SparkSession, rows: list[dict], run_date: date) -> DataFrame:
    """Typed advisory event DataFrame (advisory_composed + compliance_checked; shared wide log)."""
    schema = event_log_schema()
    cols = event_log_columns()
    ts = _event_ts(run_date)
    tuples = []
    for a in rows:
        for ev in build_advisory_events(a["merchant_id"], run_date, a, ts):
            tuples.append(tuple(_to_decimal(ev.get(c)) if c == "confidence" else ev.get(c) for c in cols))
    return spark.createDataFrame(tuples, schema=schema)


def _write_point_in_time(df: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    writer = df.write.format("delta").partitionBy("advisory_run_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"advisory_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_current_view(spark: SparkSession, base: str, view: str) -> None:
    """`_current` = the latest advisory per merchant_id (single stream per merchant → global-latest
    per merchant is correct; contrast the multi-stream merchant_extraction view)."""
    spark.sql(
        f"CREATE OR REPLACE VIEW {view} AS SELECT * EXCEPT (_rn) FROM ("
        f"SELECT *, ROW_NUMBER() OVER (PARTITION BY merchant_id "
        f"ORDER BY advisory_run_date DESC) AS _rn FROM {base}) WHERE _rn = 1"
    )


def _append_advisory_events(events: DataFrame, target: str, run_date: date, spark: SparkSession) -> None:
    """Delete-then-append this run's S8 events (leaves S3/S4/S7 events untouched)."""
    if not spark.catalog.tableExists(target):
        events.write.format("delta").partitionBy("classify_run_date").mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(target)
        return
    types = ", ".join(f"'{t}'" for t in _S8_EVENT_TYPES)
    spark.sql(
        f"DELETE FROM {target} WHERE classify_run_date = date'{run_date.isoformat()}' "
        f"AND event_type IN ({types})"
    )
    if events.limit(1).count() == 0:
        return
    events.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)


def build_gold_advisory(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD_TEST,
    run_date: date | None = None,
    allow_prod: bool = False,
    sample_merchant_ids: list[str] | None = None,
    predict_fn=None,
    endpoint: str = DEFAULT_ENDPOINT,
    model_version: str = MODEL_VERSION,
) -> dict:
    """Compose a grounded, compliance-gated advisory per active merchant for `run_date` and write
    point-in-time gold.merchant_advisory (+`_current`) + advisory_composed/compliance_checked
    events. STORED, not delivered. `gold_test` first; prod needs schema=gold AND allow_prod=True
    (Rule 5 — writes managed tables + spends on the LLM). Pass `sample_merchant_ids` for a cheap
    sample-first run (the D-807 labeled set)."""
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build advisory into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5: this writes managed tables + spends on the LLM)."
        )
    run_date = run_date or date.today()
    predict_fn = predict_fn or databricks_chat_predict_fn()

    adv_target = C.fq(schema, C.GoldTable.MERCHANT_ADVISORY, catalog)
    adv_current = C.fq(schema, C.GoldTable.MERCHANT_ADVISORY_CURRENT, catalog)
    event_target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)

    records = advisory_records(spark, catalog, schema, sample_merchant_ids=sample_merchant_ids)
    rows = [
        compose_advisory(
            r["merchant_id"], r["signals"], run_date, predict_fn,
            offer_type=r.get("offer_type"), governing_state=r.get("governing_state"),
            endpoint=endpoint, model_version=model_version,
        )
        for r in records
    ]

    adv_df = _advisory_df(spark, rows)
    _write_point_in_time(adv_df, adv_target, run_date, spark)
    _create_current_view(spark, adv_target, adv_current)

    events = _advisory_events_df(spark, rows, run_date)
    _append_advisory_events(events, event_target, run_date, spark)

    def _count_status(status):
        return sum(1 for r in rows if r["review_status"] == status)

    def _count_compliance(status):
        return sum(1 for r in rows if r["compliance_status"] == status)

    def _count_type(t):
        return sum(1 for r in rows if r["advisory_type"] == t)

    return {
        "merchant_advisory": adv_target,
        "merchant_advisory_current": adv_current,
        "merchant_event_log": event_target,
        "advisory_run_date": run_date.isoformat(),
        "endpoint": endpoint,
        "model_version": model_version,
        "advised_merchants": len(records),
        "advisory_rows": len(rows),
        "offers_present": bool(_offers_if_present(spark, catalog, schema) is not None),
        "applied": _count_status(C.ReviewStatus.APPLIED),
        "review": _count_status(C.ReviewStatus.REVIEW),
        "rejected": _count_status(C.ReviewStatus.REJECTED),
        "compliance_pass": _count_compliance(C.ComplianceStatus.PASS),
        "compliance_blocked": _count_compliance(C.ComplianceStatus.BLOCKED),
        "type_advice": _count_type(C.AdvisoryType.ADVICE),
        "type_specific_offer": _count_type(C.AdvisoryType.SPECIFIC_OFFER),
        "type_factual_summary": _count_type(C.AdvisoryType.FACTUAL_SUMMARY),
    }
