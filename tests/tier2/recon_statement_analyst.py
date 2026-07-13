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

# --- D-711 hand-labeled sample — operator-confirmed on prompt v2 (2026-07-09) --------------------
# deal_id -> {"merchant", "positions"} = the operator-confirmed TRUE count of genuine cash-advance
# positions, read against the actual statements ("all look good and as expected" on the v2 run).
# This is a POSITIONS-only gate (the highest-value signal); revenue accuracy is deferred to FU-704
# (est_weekly_revenue is advisory/soft — C-026 #3). Scored on the agent's extracted position count
# regardless of review_status, so accuracy is decoupled from the freshness surfacing gate (#2).
STATEMENT_LABELS: dict = {
    "006UU00000QSuEoYAL": {"merchant": "A1a Environmental", "positions": 0},   # hosting bill excluded (v2)
    "0065e00000LjuQXAAZ": {"merchant": "5 Star Burgers", "positions": 2},      # Fundomate, DoorDash Capital
    "006UU00000QnGnpYAF": {"merchant": "Tom Snell", "positions": 2},           # Northland, GrtWestCas (leases excluded)
    "006UU00000RU9kfYAD": {"merchant": "Hjm Construction", "positions": 2},    # OnDeck, Aspire (Coast rightly excluded)
    "006UU00000RdVHLYA3": {"merchant": "All Point Limo", "positions": 4},      # Honest, Fundworks, Forward, Spartan
    "006UU00000RlkO3YAJ": {"merchant": "Wolf Corporation", "positions": 3},    # ExpansionCap, OnDeck, ByzFunder
    "006UU00000DYz9mYAD": {"merchant": "Wolfe Pack Express", "positions": 1},  # Forward Financing (stale→REVIEW)
    "0065e00000KCQpYAAX": {"merchant": "David Meyers / D&K", "positions": 0},  # image, no text → abstain
    "0065e00000KCPqJAAX": {"merchant": "Findley White / Ind Pawn", "positions": 0},  # bank letter → abstain
    "0065e00000KCgIYAA1": {"merchant": "Flener IP Law", "positions": 2},       # OnDeck, Newtek (SBA/bank loans excluded)
}
POSITION_TOL = 1           # concurrent positions within ±1 (SPRINT_7_PLAN / D-711)
AMOUNT_TOL_PCT = 0.15      # reserved for the revenue gate (FU-704); not scored today (#3, revenue soft)
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

    # === D-711 POSITIONS accuracy (operator-confirmed truth; revenue deferred to FU-704) ===
    # Score the extracted position COUNT regardless of review_status, so accuracy is decoupled from
    # the freshness surfacing gate (#2) and robust to run_date drift.
    if STATEMENT_LABELS:
        posval = {
            r["deal_id"]: r["value"]
            for r in ext.where(F.col("extraction_type") == F.lit(C.ExtractionType.CONCURRENT_POSITIONS))
            .select("deal_id", "value").collect()
        }
        results, correct = [], 0
        for deal_id, truth in STATEMENT_LABELS.items():
            got = _num(posval.get(deal_id))
            ok = got is not None and abs(got - truth["positions"]) <= POSITION_TOL
            correct += int(ok)
            results.append({"merchant": truth["merchant"], "deal_id": deal_id,
                            "true_positions": truth["positions"], "agent_positions": got, "ok": ok})
        n = len(STATEMENT_LABELS)
        findings["labeled_n"] = n
        findings["labeled_correct"] = correct
        findings["labeled_accuracy"] = round(correct / n, 4) if n else None
        findings["labeled_results"] = results
        findings["accuracy_bar"] = ACCURACY_BAR
        findings["position_tolerance"] = POSITION_TOL
    else:
        findings["labeled_pending"] = True

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
