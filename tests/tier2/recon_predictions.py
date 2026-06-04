"""Tier-2 reconciliation for S6 Prediction (Build Plan §6, Framework §11.2).

Runs ON Databricks (ML runtime — needs PyMC-Marketing + lifelines + MLflow). Builds
gold.merchant_predictions (+`_current`) for one run_date into an ISOLATED test schema
(`gold_test` default; prod requires allow_prod=True — Rule 5), then asserts the SPRINT_6
exit criteria. v1 acceptance is "sane + explainable" (coverage / bounds / honesty + a
direction sanity check), not a fixed accuracy bar (D-608).

HARD (failures):
- schema: merchant_predictions == merchant_predictions_schema();
- coverage: one prediction row per merchant with a dated advance; (merchant_id, prediction_run_date) unique;
- bounds: p_alive / p_defection / prediction_confidence ∈ [0,1] 100%; predicted_next_event_date non-null;
- honesty: `insufficient_history` ⇔ (rfm_frequency == 0); model_version stamped 100%;
- no-surface guard; `_current` resolves to the run_date.

DIAGNOSTIC (reported): insufficient_history count; repeat count; mean p_alive by lifecycle
(active should exceed dormant — the explainable sanity check); predicted_clv non-null count;
cox_fitted; the four reference merchants by name.
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import merchant_predictions_schema
from transform.gold_predictions import build_gold_predictions

REFERENCE_MERCHANT_NAMES = ("Starr Window Tinting", "One Big Promotion", "Tom Snell", "Wolf Corporation")


def run_recon(spark, catalog=C.CATALOG, schema=C.Schema.GOLD_TEST, run_date=None, allow_prod=False) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError("Refusing tier-2 predictions against prod 'gold' without allow_prod=True (Rule 5).")
    run_date = run_date or date.today()
    findings: dict = {"catalog": catalog, "schema": schema, "run_date": run_date.isoformat()}

    targets = build_gold_predictions(spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod)
    findings["targets"] = targets
    findings["cox_fitted"] = targets.get("cox_fitted")

    run_lit = F.lit(run_date.isoformat()).cast("date")
    preds = spark.read.table(targets["merchant_predictions"]).where(F.col("prediction_run_date") == run_lit)
    cur = spark.read.table(targets["merchant_predictions_current"])
    deals = spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))
    rung = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog))

    findings["predictions_schema_matches"] = preds.columns == [f.name for f in merchant_predictions_schema().fields]

    universe = deals.where(F.col("funded_date").isNotNull()).select("merchant_id").distinct().count()
    pred_n = preds.count()
    findings["merchants_with_dated_advance"] = universe
    findings["prediction_count"] = pred_n
    findings["distinct_keys"] = preds.select("merchant_id", "prediction_run_date").distinct().count()

    findings["surface_offenders"] = offending_surface_columns(preds.columns)

    def _oob(col):
        return preds.where(F.col(col).isNotNull() & ((F.col(col) < 0) | (F.col(col) > 1))).count()
    findings["p_alive_oob"] = _oob("p_alive")
    findings["p_defection_oob"] = _oob("p_defection")
    findings["confidence_oob"] = _oob("prediction_confidence")
    findings["confidence_null"] = preds.where(F.col("prediction_confidence").isNull()).count()
    findings["next_event_null"] = preds.where(F.col("predicted_next_event_date").isNull()).count()
    findings["model_version_null"] = preds.where(F.col("model_version").isNull()).count()

    # honesty: insufficient_history ⇔ rfm_frequency == 0
    findings["insufficient_flag_mismatch"] = preds.where(
        (F.col("rfm_frequency") == 0) != F.col("insufficient_history")
    ).count()
    findings["insufficient_history_count"] = preds.where(F.col("insufficient_history")).count()
    findings["repeat_count"] = preds.where(F.col("rfm_frequency") > 0).count()
    findings["predicted_clv_nonnull"] = preds.where(F.col("predicted_clv").isNotNull()).count()

    cur_dates = [str(r[0]) for r in cur.select("prediction_run_date").distinct().collect()]
    findings["current_run_dates"] = sorted(cur_dates)
    findings["current_count"] = cur.count()

    # === DIAGNOSTICS ===
    # explainable sanity: mean p_alive by lifecycle (active should exceed dormant)
    j = preds.join(rung, "merchant_id", "left")
    findings["mean_p_alive_by_lifecycle"] = {
        r["lifecycle_state"]: (float(r["m"]) if r["m"] is not None else None)
        for r in j.groupBy("lifecycle_state").agg(F.avg("p_alive").alias("m")).collect()
    }
    merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog)).select("merchant_id", "business_name")
    ref = (
        preds.join(merchants, "merchant_id", "inner")
        .filter(F.col("business_name").isin(list(REFERENCE_MERCHANT_NAMES)))
        .select("business_name", "rfm_frequency", "p_alive", "p_defection", "predicted_next_event_date",
                "predicted_clv", "prediction_confidence", "insufficient_history")
        .collect()
    )
    findings["reference_merchants"] = [
        {"name": r["business_name"], "rfm_frequency": r["rfm_frequency"],
         "p_alive": float(r["p_alive"]) if r["p_alive"] is not None else None,
         "next_event": str(r["predicted_next_event_date"]),
         "clv": float(r["predicted_clv"]) if r["predicted_clv"] is not None else None,
         "confidence": float(r["prediction_confidence"]) if r["prediction_confidence"] is not None else None,
         "insufficient_history": bool(r["insufficient_history"])}
        for r in ref
    ]
    return findings


def assert_recon(findings: dict) -> list[str]:
    failures: list[str] = []
    if not findings.get("predictions_schema_matches"):
        failures.append("schema drift on gold.merchant_predictions")
    if findings.get("prediction_count") != findings.get("merchants_with_dated_advance"):
        failures.append(
            f"coverage: predictions={findings.get('prediction_count')} != merchants-with-advance="
            f"{findings.get('merchants_with_dated_advance')}"
        )
    if findings.get("distinct_keys") != findings.get("prediction_count"):
        failures.append("(merchant_id, prediction_run_date) not unique")
    if findings.get("surface_offenders"):
        failures.append(f"no-surface breached: {findings.get('surface_offenders')}")
    for k, label in (
        ("p_alive_oob", "p_alive outside [0,1]"), ("p_defection_oob", "p_defection outside [0,1]"),
        ("confidence_oob", "prediction_confidence outside [0,1]"), ("confidence_null", "prediction_confidence null"),
        ("next_event_null", "predicted_next_event_date null"), ("model_version_null", "model_version null"),
        ("insufficient_flag_mismatch", "insufficient_history != (rfm_frequency==0)"),
    ):
        if findings.get(k, 0) != 0:
            failures.append(f"{label}: {findings.get(k)} rows")
    if findings.get("current_run_dates") != [findings.get("run_date")]:
        failures.append(f"_current not the single run_date: {findings.get('current_run_dates')}")
    if findings.get("current_count") != findings.get("prediction_count"):
        failures.append("_current count != latest partition count")
    return failures
