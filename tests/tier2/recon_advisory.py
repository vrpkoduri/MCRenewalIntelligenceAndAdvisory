"""Tier-2 reconciliation for S8 — the Advisory layer + compliance gate (Framework §2.3/§2.4/§5.9).

Runs ON Databricks (needs a Foundation Model endpoint). Composes grounded, compliance-gated
advisories into an ISOLATED test schema (`gold_test` default; prod requires allow_prod=True — Rule
5), then asserts the mechanical exit criteria and — once the operator has filled ADVISORY_LABELS —
the D-807 quality gate. **S8 composes + gates; it does NOT send** — the recon asserts that no
BLOCKED/ungrounded advisory is ever marked deliverable.

HARD (failures):
- schema == merchant_advisory_schema(); advisory_type ∈ AdvisoryType.ALL; compliance_status ∈
  ComplianceStatus.ALL; review_status ∈ ReviewStatus.ALL;
- coverage: one advisory per advised merchant; keys (merchant_id, advisory_run_date) unique;
- bounds: confidence ∈ [0,1]; model_version stamped; no-surface; `_current` resolves to run_date;
- honesty/gate integrity (the whole point):
    * NO BLOCKED artifact is marked deliverable (compliance_status=blocked AND review_status=applied → 0);
    * every APPLIED advisory is PASS (review_status=applied AND compliance_status!=pass → 0);
    * every REJECTED advisory is ungrounded-by-construction (never applied) — covered by the above;
- D-807 quality gate (ONLY when ADVISORY_LABELS is populated): on the labeled merchants the composed
  advisory_type + compliance_status + recommended_action match the operator's labels ≥ ACCURACY_BAR;
  grounding is 100% by construction (an invented number is REJECTED before storage).

DIAGNOSTIC: advisory-type / compliance / review breakdowns, offers_present (FU-501), Wolf sample.
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import merchant_advisory_schema
from transform.gold_advisory import build_gold_advisory
from transform.gold_extraction import databricks_chat_predict_fn

# --- D-807 hand-labeled sample — OPERATOR FILLS after reviewing the sample advisories -------------
# merchant_id -> {"merchant", "advisory_type", "compliance_status", "recommended_action"} =
# the operator-confirmed expected advisory for that merchant, read against the composed output.
# Wolf is central (Serial, barely-paid → must recommend WAIT-AND-PAY-DOWN, never a pitch). Grounding
# (0 invented numbers) is a HARD pipeline invariant, not a label — a hallucinated figure is REJECTED
# before it can be stored, so this sample scores TYPE + COMPLIANCE + HONEST-ACTION correctness.
# Left empty ⇒ the quality gate is a no-op (mirrors D-706/D-711 until the operator fills it).
# Operator-confirmed 2026-07-18 on the gold_test sample run (v2). The scored fields
# (advisory_type + compliance_status + recommended_action) are DETERMINISTIC — the LLM only writes
# the headline/rationale wording + confidence — so the gate is reproducible run-over-run.
ADVISORY_LABELS: dict = {
    "MRI-001UU00000bmPgYYAU": {"merchant": "Wolf Corporation", "advisory_type": "advice", "compliance_status": "pass", "recommended_action": "wait-and-pay-down"},
    "MRI-0015e00000ybzC7AAI": {"merchant": "Bruno's Best Pizza", "advisory_type": "advice", "compliance_status": "pass", "recommended_action": "wait-and-pay-down"},
    "MRI-0015e00000ybz9iAAA": {"merchant": "Family Visions Llc", "advisory_type": "advice", "compliance_status": "pass", "recommended_action": "wait-and-pay-down"},
    "MRI-0015e00000ybzFXAAY": {"merchant": "Concrete For Less Llc", "advisory_type": "advice", "compliance_status": "pass", "recommended_action": "renewal-eligible"},
    "MRI-0015e00000ybzGiAAI": {"merchant": "Designer Stone Solutions Inc", "advisory_type": "advice", "compliance_status": "pass", "recommended_action": "renewal-eligible"},
    "MRI-0015e00000ybyyLAAQ": {"merchant": "Craig Humphries / Csh Electrical", "advisory_type": "factual-summary", "compliance_status": "pass", "recommended_action": None},
    "MRI-0015e00000ybzEQAAY": {"merchant": "Valley Contracting Group Llc", "advisory_type": "factual-summary", "compliance_status": "pass", "recommended_action": None},
    "MRI-0015e00000ybzIrAAI": {"merchant": "Praveen Buddiga Md Inc", "advisory_type": "factual-summary", "compliance_status": "pass", "recommended_action": None},
}
ACCURACY_BAR = 0.80


def run_recon(spark, catalog=C.CATALOG, schema=C.Schema.GOLD_TEST, run_date=None,
              allow_prod=False, sample_merchant_ids=None, predict_fn=None):
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError("Refusing tier-2 advisory against prod 'gold' without allow_prod=True (Rule 5).")
    run_date = run_date or date.today()
    findings: dict = {"catalog": catalog, "schema": schema, "run_date": run_date.isoformat()}

    predict_fn = predict_fn or databricks_chat_predict_fn()
    targets = build_gold_advisory(
        spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod,
        sample_merchant_ids=sample_merchant_ids, predict_fn=predict_fn,
    )
    findings["targets"] = targets
    for k in ("advised_merchants", "advisory_rows", "offers_present", "applied", "review",
              "rejected", "compliance_pass", "compliance_blocked", "type_advice",
              "type_specific_offer", "type_factual_summary", "model_version"):
        findings[k] = targets.get(k)

    run_lit = F.lit(run_date.isoformat()).cast("date")
    adv = spark.read.table(targets["merchant_advisory"]).where(F.col("advisory_run_date") == run_lit)

    findings["schema_matches"] = spark.read.table(targets["merchant_advisory"]).columns == [
        f.name for f in merchant_advisory_schema().fields
    ]
    findings["row_count"] = adv.count()
    findings["distinct_keys"] = adv.select("merchant_id", "advisory_run_date").distinct().count()
    findings["expected_rows"] = targets.get("advised_merchants", 0)
    findings["surface_offenders"] = offending_surface_columns(adv.columns)
    findings["confidence_oob"] = adv.where(
        F.col("confidence").isNotNull() & ((F.col("confidence") < 0) | (F.col("confidence") > 1))
    ).count()
    findings["model_version_null"] = adv.where(F.col("model_version").isNull()).count()
    findings["bad_type"] = adv.where(~F.col("advisory_type").isin(list(C.AdvisoryType.ALL))).count()
    findings["bad_compliance"] = adv.where(~F.col("compliance_status").isin(list(C.ComplianceStatus.ALL))).count()
    findings["bad_review_status"] = adv.where(~F.col("review_status").isin(list(C.ReviewStatus.ALL))).count()

    # the honesty/gate integrity checks — the reason the layer exists
    findings["blocked_but_deliverable"] = adv.where(
        (F.col("compliance_status") == F.lit(C.ComplianceStatus.BLOCKED))
        & (F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
    ).count()
    findings["applied_not_pass"] = adv.where(
        (F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
        & (F.col("compliance_status") != F.lit(C.ComplianceStatus.PASS))
    ).count()

    cur = spark.read.table(targets["merchant_advisory_current"])
    findings["current_run_dates"] = sorted(
        str(r[0]) for r in cur.select("advisory_run_date").distinct().collect()
    )

    # === diagnostics ===
    findings["type_breakdown"] = {
        r["advisory_type"]: r["n"]
        for r in adv.groupBy("advisory_type").agg(F.count(F.lit(1)).alias("n")).collect()
    }
    findings["compliance_breakdown"] = {
        r["compliance_status"]: r["n"]
        for r in adv.groupBy("compliance_status").agg(F.count(F.lit(1)).alias("n")).collect()
    }
    findings["review_breakdown"] = {
        r["review_status"]: r["n"]
        for r in adv.groupBy("review_status").agg(F.count(F.lit(1)).alias("n")).collect()
    }

    # === D-807 quality gate (operator-labeled type + compliance + honest action) ===
    if ADVISORY_LABELS:
        got = {
            r["merchant_id"]: r
            for r in adv.select(
                "merchant_id", "advisory_type", "compliance_status", "recommended_action"
            ).where(F.col("merchant_id").isin(list(ADVISORY_LABELS))).collect()
        }
        results, correct = [], 0
        for mid, label in ADVISORY_LABELS.items():
            row = got.get(mid)
            ok = bool(
                row
                and row["advisory_type"] == label["advisory_type"]
                and row["compliance_status"] == label["compliance_status"]
                and row["recommended_action"] == label["recommended_action"]
            )
            correct += int(ok)
            results.append({
                "merchant": label.get("merchant"), "merchant_id": mid, "ok": ok,
                "expected": {k: label[k] for k in ("advisory_type", "compliance_status", "recommended_action")},
                "got": (None if not row else {
                    "advisory_type": row["advisory_type"], "compliance_status": row["compliance_status"],
                    "recommended_action": row["recommended_action"]}),
            })
        n = len(ADVISORY_LABELS)
        findings["labeled_n"] = n
        findings["labeled_correct"] = correct
        findings["labeled_accuracy"] = round(correct / n, 4) if n else None
        findings["labeled_results"] = results
        findings["accuracy_bar"] = ACCURACY_BAR
    else:
        findings["labeled_pending"] = True

    return findings


def assert_recon(findings: dict) -> list[str]:
    failures: list[str] = []
    if not findings.get("schema_matches"):
        failures.append("schema drift on gold.merchant_advisory")
    if findings.get("row_count") != findings.get("expected_rows"):
        failures.append(f"coverage: rows={findings.get('row_count')} != advised={findings.get('expected_rows')}")
    if findings.get("distinct_keys") != findings.get("row_count"):
        failures.append("(merchant_id, advisory_run_date) not unique")
    if findings.get("surface_offenders"):
        failures.append(f"no-surface breached: {findings.get('surface_offenders')}")
    for k, label in (
        ("confidence_oob", "confidence outside [0,1]"),
        ("model_version_null", "model_version null"),
        ("bad_type", "advisory_type not in AdvisoryType.ALL"),
        ("bad_compliance", "compliance_status not in ComplianceStatus.ALL"),
        ("bad_review_status", "review_status not in ReviewStatus.ALL"),
        ("blocked_but_deliverable", "BLOCKED advisory marked deliverable (applied)"),
        ("applied_not_pass", "APPLIED advisory whose compliance_status != pass"),
    ):
        if findings.get(k, 0) != 0:
            failures.append(f"{label}: {findings.get(k)} rows")
    if findings.get("run_date") not in findings.get("current_run_dates", [findings.get("run_date")]):
        failures.append(f"_current missing run_date: {findings.get('current_run_dates')}")
    acc = findings.get("labeled_accuracy")
    if acc is not None and acc < findings.get("accuracy_bar", ACCURACY_BAR):
        failures.append(f"D-807 quality {acc} < bar {findings.get('accuracy_bar')}: {findings.get('labeled_results')}")
    return failures
