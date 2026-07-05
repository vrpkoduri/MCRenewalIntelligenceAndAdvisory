"""Tier-2 reconciliation for S7 Phase-2 — the Statement Analyst agentic extraction (Framework §5.9).

Runs ON Databricks (needs a Foundation Model endpoint). Builds the statement extractions into an
ISOLATED test schema (`gold_test` default; prod requires allow_prod=True — Rule 5) over the covered
deals' most-recent statements, then asserts the mechanical exit criteria and — once the operator has
filled STATEMENT_LABELS — the D-711 accuracy gate.

HARD (failures):
- schema == merchant_extraction_schema(); extraction_type ∈ the 3 statement types;
- coverage: 3 rows (positions/debit/revenue) per covered deal; keys unique;
- grounding (D-705): every APPLIED row has source_ref AND confidence ≥ threshold; no REJECTED row has source_ref;
- bounds: confidence ∈ [0,1]; model_version stamped; review_status ∈ ReviewStatus.ALL; no-surface;
- `_current` resolves to the run_date;
- D-711 accuracy (ONLY when STATEMENT_LABELS is populated): positions within ±POSITION_TOL,
  weekly_debit/est_weekly_revenue within ±AMOUNT_TOL_PCT, on the labeled deals ≥ ACCURACY_BAR.

DIAGNOSTIC: applied/review split (fresh vs stale, C-026 #2), per-type value distributions, Wolf.
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import merchant_extraction_schema
from transform.gold_statement_extraction import build_gold_statement_extraction
from transform.gold_extraction import databricks_chat_predict_fn

_STMT_TYPES = (
    C.ExtractionType.CONCURRENT_POSITIONS,
    C.ExtractionType.WEEKLY_DEBIT,
    C.ExtractionType.EST_WEEKLY_REVENUE,
)

# --- D-711 hand-labeled sample (OPERATOR fills this; empty ⇒ gate is a no-op) -------------------
# deal_id -> operator-read ground truth from the actual statement(s):
#   {"merchant": "...", "concurrent_positions": <int>, "weekly_debit": <float>, "est_weekly_revenue": <float>}
# Anchor on Wolf + ~5–9 recent (fresh) covered deals; a couple of stale ones exercise the REVIEW path.
STATEMENT_LABELS: dict = {}
POSITION_TOL = 1           # concurrent positions within ±1 (SPRINT_7_PLAN / D-711)
AMOUNT_TOL_PCT = 0.15      # weekly_debit / est_weekly_revenue within ±15% (revenue is the soft one, #3)
ACCURACY_BAR = 0.80


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_recon(spark, catalog=C.CATALOG, schema=C.Schema.GOLD_TEST, run_date=None, allow_prod=False, predict_fn=None):
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError("Refusing tier-2 statement extraction against prod 'gold' without allow_prod=True (Rule 5).")
    run_date = run_date or date.today()
    findings: dict = {"catalog": catalog, "schema": schema, "run_date": run_date.isoformat()}

    predict_fn = predict_fn or databricks_chat_predict_fn()
    targets = build_gold_statement_extraction(
        spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod, predict_fn=predict_fn
    )
    findings["targets"] = targets
    for k in ("covered_deals", "extraction_rows", "applied", "review", "rejected", "silver_schema", "model_version"):
        findings[k] = targets.get(k)

    run_lit = F.lit(run_date.isoformat()).cast("date")
    ext = (
        spark.read.table(targets["merchant_extraction"])
        .where((F.col("extraction_run_date") == run_lit) & F.col("extraction_type").isin(list(_STMT_TYPES)))
    )

    findings["schema_matches"] = spark.read.table(targets["merchant_extraction"]).columns == [
        f.name for f in merchant_extraction_schema().fields
    ]
    findings["row_count"] = ext.count()
    findings["distinct_keys"] = ext.select(
        "merchant_id", "deal_id", "extraction_run_date", "extraction_type"
    ).distinct().count()
    findings["expected_rows"] = targets.get("covered_deals", 0) * len(_STMT_TYPES)
    findings["surface_offenders"] = offending_surface_columns(ext.columns)
    findings["confidence_oob"] = ext.where(
        F.col("confidence").isNotNull() & ((F.col("confidence") < 0) | (F.col("confidence") > 1))
    ).count()
    findings["model_version_null"] = ext.where(F.col("model_version").isNull()).count()
    findings["bad_type"] = ext.where(~F.col("extraction_type").isin(list(_STMT_TYPES))).count()
    findings["bad_review_status"] = ext.where(~F.col("review_status").isin(list(C.ReviewStatus.ALL))).count()
    thr = C.AGENT_CONFIDENCE_REVIEW_MIN
    findings["applied_ungrounded_or_unconfident"] = ext.where(
        (F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
        & (F.col("source_ref").isNull() | (F.col("confidence") < F.lit(thr)))
    ).count()
    findings["rejected_with_source"] = ext.where(
        (F.col("review_status") == F.lit(C.ReviewStatus.REJECTED)) & F.col("source_ref").isNotNull()
    ).count()

    cur = spark.read.table(targets["merchant_extraction_current"])
    findings["current_run_dates"] = sorted(str(r[0]) for r in cur.select("extraction_run_date").distinct().collect())

    # === diagnostics ===
    findings["review_status_breakdown"] = {
        r["review_status"]: r["n"]
        for r in ext.groupBy("review_status").agg(F.count(F.lit(1)).alias("n")).collect()
    }
    # positions APPLIED (the fresh, surfaced burden) — the pilot's headline
    pos = ext.where(F.col("extraction_type") == F.lit(C.ExtractionType.CONCURRENT_POSITIONS))
    findings["positions_applied"] = pos.where(F.col("review_status") == F.lit(C.ReviewStatus.APPLIED)).count()
    findings["positions_value_dist"] = {
        str(r["value"]): r["n"]
        for r in pos.where(F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
        .groupBy("value").agg(F.count(F.lit(1)).alias("n")).collect()
    }

    # === D-711 accuracy (only when labels are provided) ===
    if STATEMENT_LABELS:
        applied = {
            (r["deal_id"], r["extraction_type"]): r["value"]
            for r in ext.where(F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
            .select("deal_id", "extraction_type", "value").collect()
        }
        results, correct = [], 0
        for deal_id, truth in STATEMENT_LABELS.items():
            got_pos = _num(applied.get((deal_id, C.ExtractionType.CONCURRENT_POSITIONS)))
            got_deb = _num(applied.get((deal_id, C.ExtractionType.WEEKLY_DEBIT)))
            got_rev = _num(applied.get((deal_id, C.ExtractionType.EST_WEEKLY_REVENUE)))
            ok_pos = got_pos is not None and abs(got_pos - truth["concurrent_positions"]) <= POSITION_TOL
            ok_deb = got_deb is not None and abs(got_deb - truth["weekly_debit"]) <= AMOUNT_TOL_PCT * truth["weekly_debit"]
            ok_rev = got_rev is not None and abs(got_rev - truth["est_weekly_revenue"]) <= AMOUNT_TOL_PCT * truth["est_weekly_revenue"]
            ok = ok_pos and ok_deb and ok_rev
            correct += int(ok)
            results.append({"merchant": truth["merchant"], "deal_id": deal_id, "ok": ok,
                            "ok_positions": ok_pos, "ok_debit": ok_deb, "ok_revenue": ok_rev,
                            "got": {"positions": got_pos, "debit": got_deb, "revenue": got_rev}})
        n = len(STATEMENT_LABELS)
        findings["labeled_n"] = n
        findings["labeled_correct"] = correct
        findings["labeled_accuracy"] = round(correct / n, 4) if n else None
        findings["labeled_results"] = results
        findings["accuracy_bar"] = ACCURACY_BAR
    else:
        findings["labeled_pending"] = True  # operator has not filled STATEMENT_LABELS yet

    return findings


def assert_recon(findings: dict) -> list[str]:
    failures: list[str] = []
    if not findings.get("schema_matches"):
        failures.append("schema drift on gold.merchant_extraction")
    if findings.get("row_count") != findings.get("expected_rows"):
        failures.append(f"coverage: rows={findings.get('row_count')} != expected={findings.get('expected_rows')} (3/deal)")
    if findings.get("distinct_keys") != findings.get("row_count"):
        failures.append("(merchant_id, deal_id, extraction_run_date, extraction_type) not unique")
    if findings.get("surface_offenders"):
        failures.append(f"no-surface breached: {findings.get('surface_offenders')}")
    for k, label in (
        ("confidence_oob", "confidence outside [0,1]"),
        ("model_version_null", "model_version null"),
        ("bad_type", "extraction_type not a statement type"),
        ("bad_review_status", "review_status not in ReviewStatus.ALL"),
        ("applied_ungrounded_or_unconfident", "APPLIED row ungrounded or below threshold"),
        ("rejected_with_source", "REJECTED row that has a source_ref"),
    ):
        if findings.get(k, 0) != 0:
            failures.append(f"{label}: {findings.get(k)} rows")
    if findings.get("current_run_dates") and findings.get("current_run_dates") != [findings.get("run_date")]:
        # _current may legitimately mix a same-day Data Steward run; only flag if run_date absent
        if findings.get("run_date") not in findings.get("current_run_dates", []):
            failures.append(f"_current missing run_date: {findings.get('current_run_dates')}")
    acc = findings.get("labeled_accuracy")
    if acc is not None and acc < findings.get("accuracy_bar", ACCURACY_BAR):
        failures.append(f"D-711 accuracy {acc} < bar {findings.get('accuracy_bar')}: {findings.get('labeled_results')}")
    return failures
