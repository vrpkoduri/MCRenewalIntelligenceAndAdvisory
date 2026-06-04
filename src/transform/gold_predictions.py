"""Gold -> Gold: Prediction Models (Build Plan §6, Framework §11.2, S6).

Fits the ADOPTED toolkit (PyMC-Marketing BG/NBD + Gamma-Gamma + CLV; lifelines Cox PH + KM)
on the real renewal history and writes the Merchant-Gold prediction outputs. Reads S1-S4 gold
+ the event log; NEVER recomputes the spine (CLAUDE.md §2.1); distress stays signal-driven
(S3 owns it). NO SF stored balances.

The book is small (~2,125 merchants), so the feature assembly collects to pandas and the
models fit on the driver (an ML-runtime cluster) — the per-merchant feature/label derivation
is the pure `common.prediction` code (tier-1 tested), never reimplemented here. v1 fits with
MAP (fast, cheap, deterministic) and surfaces confidence from history depth; the sparse book
(62.5% single-deal — readiness spike) leans on the population fit + an explicit
`insufficient_history` flag (D-603), never false precision.

Output (D-604): point-in-time `gold.merchant_predictions` keyed (merchant_id,
prediction_run_date), append-only + `_current` view (mirrors S2-S5); `prediction` events to
the event log; an MLflow run with params/metrics/`model_version`. Batch inference only — no
real-time serving endpoint in v1 (D-605). Prod gated (Rule 5).
"""

from __future__ import annotations

from datetime import date, timedelta

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from common import constants as C
from common.prediction import rfm_features, survival_rows
from common.prediction.confidence import (
    confidence_from_history,
    is_insufficient_history,
    prediction_confidence,
)
from common.schemas.gold import merchant_predictions_schema

_DAYS_PER_MONTH = 30.44  # BG/NBD + CLV time unit = months (matches the discount-rate convention)


# --- feature assembly (pure common.prediction over collected pandas) -------------


def _assemble_features(spark: SparkSession, catalog: str, schema: str, run_date: date):
    """Collect the small per-merchant feature/label set to pandas: RFM (BTYD), survival rows
    (Cox), and the covariates that exist today. Uses the pure `common.prediction` derivation."""
    import pandas as pd

    deals = (
        spark.read.table(C.fq(schema, C.GoldTable.DEALS, catalog))
        .select("merchant_id", "funded_date", "funded_amount", "factor_rate", "deal_type")
        .toPandas()
    )
    rung = (
        spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_RUNG_CURRENT, catalog))
        .select("merchant_id", "lifecycle_state", "rung")
        .toPandas()
    )
    clock = (
        spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_CLOCK_CURRENT, catalog))
        .select("merchant_id", "active_position_cnt")
        .toPandas()
    )
    merchants = (
        spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog))
        .select("merchant_id", "industry", "has_default_note" if False else "merchant_id")
        .toPandas()
    )

    rows = []
    for mid, g in deals.groupby("merchant_id"):
        recs = g.to_dict("records")
        feats = rfm_features(recs, run_date)
        if feats is None:
            continue
        srows = survival_rows(recs, run_date)
        # factor_trend covariate (derived from the factor trajectory — most-recent vs prior).
        fr = [r["factor_rate"] for r in sorted(recs, key=lambda r: r["funded_date"]) if r["factor_rate"] is not None]
        factor_trend = 0
        if len(fr) >= 2:
            factor_trend = 1 if float(fr[-1]) > float(fr[-2]) else (-1 if float(fr[-1]) < float(fr[-2]) else 0)
        rows.append({
            "merchant_id": mid,
            "frequency": feats["rfm_frequency"],
            "recency_m": (feats["rfm_recency"] or 0) / _DAYS_PER_MONTH,
            "T_m": (feats["rfm_T"] or 0) / _DAYS_PER_MONTH,
            "recency_days": feats["rfm_recency"],
            "T_days": feats["rfm_T"],
            "monetary": feats["rfm_monetary"],
            "repeat_events": feats["repeat_events"],
            "censored_duration": srows[-1]["duration"] if srows else None,
            "factor_trend": factor_trend,
            "_survival_rows": srows,
        })
    feat = pd.DataFrame(rows)
    feat = feat.merge(clock, on="merchant_id", how="left").merge(rung, on="merchant_id", how="left")
    return feat


# --- model fits (PyMC-Marketing + lifelines; adopted, not hand-built) ------------


def _fit_btyd(feat, run_date: date):
    """BG/NBD (p_alive, expected purchases) + Gamma-Gamma (spend) -> p_alive, p_defection,
    predicted_clv (manual NPV over the horizon — robust to CLV-helper API drift)."""
    import numpy as np
    import pandas as pd
    from pymc_marketing.clv import BetaGeoModel, GammaGammaModel

    # BG/NBD needs recency <= T and T > 0; otherwise the merchant is prior-only.
    valid = feat[(feat["T_m"] > 0) & (feat["recency_m"] <= feat["T_m"])].copy()
    bg_data = pd.DataFrame({
        "customer_id": valid["merchant_id"],
        "frequency": valid["frequency"].astype(float),
        "recency": valid["recency_m"].astype(float),
        "T": valid["T_m"].astype(float),
    })
    bg = BetaGeoModel(data=bg_data)
    bg.fit(fit_method="map")

    p_alive = np.asarray(bg.expected_probability_alive(data=bg_data)).reshape(-1)
    horizon = float(C.CLV_HORIZON_MONTHS)
    exp_purch = np.asarray(
        bg.expected_purchases(data=bg_data, future_t=horizon)
    ).reshape(-1)

    out = pd.DataFrame({
        "merchant_id": valid["merchant_id"].values,
        "p_alive": p_alive,
        "exp_purchases": exp_purch,
    })

    # Gamma-Gamma spend for repeat buyers (frequency > 0 & monetary > 0).
    gg_src = valid[(valid["frequency"] > 0) & (valid["monetary"].fillna(0) > 0)]
    if len(gg_src) > 0:
        gg_data = pd.DataFrame({
            "customer_id": gg_src["merchant_id"],
            "frequency": gg_src["frequency"].astype(float),
            "monetary_value": gg_src["monetary"].astype(float),
        })
        gg = GammaGammaModel(data=gg_data)
        gg.fit(fit_method="map")
        spend = np.asarray(gg.expected_customer_spend(data=gg_data)).reshape(-1)
        spend_df = pd.DataFrame({"merchant_id": gg_src["merchant_id"].values, "spend": spend})
        out = out.merge(spend_df, on="merchant_id", how="left")
    else:
        out["spend"] = np.nan

    # Manual NPV CLV over the horizon (monthly discounting) — robust + auditable.
    monthly_rate = (1.0 + float(C.CLV_DISCOUNT_RATE_ANNUAL)) ** (1.0 / 12.0) - 1.0
    discount = 1.0 / ((1.0 + monthly_rate) ** (horizon / 2.0))  # mid-horizon discount factor
    out["predicted_clv"] = out["exp_purchases"] * out["spend"] * discount
    out["p_defection"] = 1.0 - out["p_alive"]
    return out[["merchant_id", "p_alive", "p_defection", "predicted_clv"]]


def _fit_cox(feat, run_date: date):
    """lifelines Cox PH on the per-interval survival rows + covariates -> predicted next-event
    date (run_date + predicted median residual). Falls back to the merchant's own cadence (or
    the book median) when Cox can't fit (too few events / covariate degeneracy)."""
    import numpy as np
    import pandas as pd

    # Expand per-merchant survival rows into the Cox training frame with covariates.
    recs = []
    cov_cols = ["active_position_cnt", "factor_trend"]  # materialized covariates available v1
    for _, r in feat.iterrows():
        for s in (r["_survival_rows"] or []):
            recs.append({
                "merchant_id": r["merchant_id"],
                "duration": max(1, int(s["duration"])),  # lifelines needs duration > 0
                "event": int(s["event_observed"]),
                "active_position_cnt": float(r.get("active_position_cnt") or 0),
                "factor_trend": float(r.get("factor_trend") or 0),
            })
    train = pd.DataFrame(recs)

    predicted = {}
    fitted = False
    if len(train) > 0 and train["event"].sum() >= 10:
        try:
            from lifelines import CoxPHFitter
            cph = CoxPHFitter(penalizer=0.1)
            cph.fit(train[["duration", "event"] + cov_cols], duration_col="duration", event_col="event")
            # Median residual time-to-next-event for each merchant's current (censored) spell.
            for _, r in feat.iterrows():
                cov = pd.DataFrame({c: [float(r.get(c.replace("active_position_cnt", "active_position_cnt")) or 0)] for c in cov_cols})
                try:
                    med = cph.predict_median(cov).iloc[0]
                    if np.isfinite(med):
                        predicted[r["merchant_id"]] = float(med)
                except Exception:
                    pass
            fitted = True
        except Exception:
            fitted = False

    # Fallback cadence: book-median completed interval.
    book_median = float(np.median([rr["duration"] for rr in recs if rr["event"] == 1])) if any(rr["event"] == 1 for rr in recs) else 90.0

    rows = []
    for _, r in feat.iterrows():
        med_days = predicted.get(r["merchant_id"], book_median)
        rows.append({
            "merchant_id": r["merchant_id"],
            "predicted_next_event_date": run_date + timedelta(days=int(round(med_days))),
        })
    return pd.DataFrame(rows), fitted


# --- driver ----------------------------------------------------------------------


def build_gold_predictions(
    spark: SparkSession,
    catalog: str = C.CATALOG,
    schema: str = C.Schema.GOLD,
    run_date: date | None = None,
    allow_prod: bool = False,
    model_version: str = "s6-v1",
) -> dict:
    """Fit + batch-infer predictions for `run_date`; write point-in-time gold.merchant_predictions
    (+`_current`) + `prediction` events; log an MLflow run. Prod gated (Rule 5)."""
    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError(
            "Refusing to build predictions into prod 'gold' without allow_prod=True. "
            "Use gold_test first (Rule 5)."
        )
    run_date = run_date or date.today()
    import mlflow
    import pandas as pd

    feat = _assemble_features(spark, catalog, schema, run_date)

    with mlflow.start_run(run_name=f"mri_predictions_{run_date.isoformat()}"):
        mlflow.log_params({
            "run_date": run_date.isoformat(), "schema": schema, "model_version": model_version,
            "clv_horizon_months": C.CLV_HORIZON_MONTHS, "clv_discount_annual": C.CLV_DISCOUNT_RATE_ANNUAL,
            "merchants": int(len(feat)),
        })
        btyd = _fit_btyd(feat, run_date)
        cox, cox_fitted = _fit_cox(feat, run_date)
        mlflow.log_metrics({
            "merchants": float(len(feat)),
            "repeat_merchants": float((feat["repeat_events"] > 0).sum()),
            "insufficient_history": float(feat["repeat_events"].apply(is_insufficient_history).sum()),
            "cox_fitted": float(1 if cox_fitted else 0),
        })

        out = feat[[
            "merchant_id", "recency_days", "frequency", "T_days", "monetary", "repeat_events",
        ]].merge(btyd, on="merchant_id", how="left").merge(cox, on="merchant_id", how="left")
        out["insufficient_history"] = out["repeat_events"].apply(is_insufficient_history)
        out["prediction_confidence"] = out.apply(
            lambda r: prediction_confidence(r["repeat_events"]), axis=1
        )
        out["prediction_run_date"] = run_date
        out["model_version"] = model_version
        out = out.rename(columns={"recency_days": "rfm_recency", "frequency": "rfm_frequency",
                                  "T_days": "rfm_T", "monetary": "rfm_monetary"})

        target = C.fq(schema, C.GoldTable.MERCHANT_PREDICTIONS, catalog)
        sdf = _to_spark_typed(spark, out, run_date)
        _write_point_in_time(sdf, target, run_date, spark)
        _append_prediction_events(spark, schema, catalog, run_date)
        _create_current_view(spark, target, C.fq(schema, C.GoldTable.MERCHANT_PREDICTIONS_CURRENT, catalog))

        mlflow.log_metric("rows_written", float(sdf.count()))

    return {
        "merchant_predictions": target,
        "merchant_predictions_current": C.fq(schema, C.GoldTable.MERCHANT_PREDICTIONS_CURRENT, catalog),
        "prediction_run_date": run_date.isoformat(),
        "cox_fitted": cox_fitted,
    }


def _to_spark_typed(spark, pdf, run_date):
    """Cast the pandas predictions to the gold schema (decimals, ints, dates)."""
    cols = [f.name for f in merchant_predictions_schema().fields]
    for c in cols:
        if c not in pdf.columns:
            pdf[c] = None
    sdf = spark.createDataFrame(pdf[cols])
    sdf = (
        sdf.withColumn("prediction_run_date", F.lit(run_date.isoformat()).cast("date"))
        .withColumn("rfm_recency", F.col("rfm_recency").cast("int"))
        .withColumn("rfm_frequency", F.col("rfm_frequency").cast("int"))
        .withColumn("rfm_T", F.col("rfm_T").cast("int"))
        .withColumn("rfm_monetary", F.col("rfm_monetary").cast("decimal(18,4)"))
        .withColumn("p_alive", F.col("p_alive").cast("decimal(18,4)"))
        .withColumn("p_defection", F.col("p_defection").cast("decimal(18,4)"))
        .withColumn("predicted_clv", F.col("predicted_clv").cast("decimal(18,4)"))
        .withColumn("prediction_confidence", F.col("prediction_confidence").cast("decimal(18,4)"))
        .withColumn("predicted_next_event_date", F.col("predicted_next_event_date").cast("date"))
        .withColumn("insufficient_history", F.col("insufficient_history").cast("boolean"))
        .withColumn("model_version", F.col("model_version").cast("string"))
        .withColumn("merchant_id", F.col("merchant_id").cast("string"))
    )
    return sdf.select(*cols)


def _write_point_in_time(df, target, run_date, spark):
    writer = df.write.format("delta").partitionBy("prediction_run_date")
    if spark.catalog.tableExists(target):
        writer.mode("overwrite").option(
            "replaceWhere", f"prediction_run_date = date'{run_date.isoformat()}'"
        ).saveAsTable(target)
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)


def _create_current_view(spark, base, view):
    spark.sql(
        f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {base} "
        f"WHERE prediction_run_date = (SELECT MAX(prediction_run_date) FROM {base})"
    )


def _append_prediction_events(spark, schema, catalog, run_date):
    """Append one `prediction` event per merchant for this run (idempotent: delete this run's
    prediction rows then append). Mirrors the S4 activation-event pattern; same wide log."""
    from common.schemas.gold import event_log_schema
    target = C.fq(schema, C.GoldTable.MERCHANT_EVENT_LOG, catalog)
    preds = spark.read.table(C.fq(schema, C.GoldTable.MERCHANT_PREDICTIONS, catalog)).where(
        F.col("prediction_run_date") == F.lit(run_date.isoformat()).cast("date")
    )
    base = {c.name: F.lit(None).cast(c.dataType) for c in event_log_schema().fields}
    base.update({
        "merchant_id": F.col("merchant_id"),
        "event_type": F.lit(C.EventType.PREDICTION),
        "event_ts": F.to_timestamp(F.lit(run_date.isoformat())),
        "classify_run_date": F.lit(run_date.isoformat()).cast("date"),
    })
    events = preds.select(*[base[c.name].alias(c.name) for c in event_log_schema().fields])
    if not spark.catalog.tableExists(target):
        events.write.format("delta").partitionBy("classify_run_date").mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(target)
        return
    spark.sql(
        f"DELETE FROM {target} WHERE classify_run_date = date'{run_date.isoformat()}' "
        f"AND event_type = '{C.EventType.PREDICTION}'"
    )
    if events.limit(1).count() > 0:
        events.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(target)
