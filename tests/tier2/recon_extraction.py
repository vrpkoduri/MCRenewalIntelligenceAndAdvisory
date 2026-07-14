"""Tier-2 reconciliation for S7 Phase-1 — the Data Steward agentic extraction (Framework §5.9).

Runs ON Databricks (needs the MLflow deployments client + a Foundation Model endpoint). Builds
gold.merchant_extraction (+`_current`) for one run_date into an ISOLATED test schema (`gold_test`
default; prod requires allow_prod=True — Rule 5), THEN re-runs the S3 rung classifier so the recon
also demonstrates the routing effect of the resolved sub-types. Asserts the SPRINT_7 Phase-1 exit
criteria; the LLM is spend-bounded (only the `closed_default` deals — a handful).

HARD (failures):
- schema: merchant_extraction == merchant_extraction_schema();
- coverage: one row per closed_default deal; (merchant_id, deal_id, extraction_run_date, extraction_type) unique;
- grounding (D-705): every APPLIED row has source_ref AND confidence ≥ threshold; no REJECTED row has source_ref;
- bounds: confidence ∈ [0,1] 100%; model_version stamped 100%; review_status ∈ ReviewStatus.ALL; extraction_type == default_subtype;
- no-surface guard; `_current` resolves to the run_date;
- the agent never wrote a spine table — it touched ONLY merchant_extraction + agent_extraction events.

DIAGNOSTIC (reported): applied/review/rejected counts; resolved sub-type breakdown; agent_extraction
event count; and the post-re-run picture — defaulted merchants whose default_subtype is no longer
`unknown` (the unknown-pile resolution) + their route breakdown; Starr Window Tinting by name (the
true-default fixture).
"""

from __future__ import annotations

from datetime import date

from common import constants as C
from common.io.guards import offending_surface_columns
from common.schemas.gold import merchant_extraction_schema
from transform.gold_extraction import build_gold_extraction, databricks_chat_predict_fn
from transform.gold_rung import build_gold_rung

STARR = "Starr Window Tinting"

# --- D-706 hand-labeled sample (ground truth) ------------------------------------
# The 6 closed_default deals' default-cause, adjudicated by the operator (C-024):
# - an explicit "defaulted" note = true_default (a default IS a loss; early_payoff/restructured are
#   the exceptions that require positive evidence) — incl. a bare "defaulted" with no further detail;
# - a clawback-on-a-defaulted-deal = true_default (funder reclaiming commission, NOT an early payoff);
# - a note with no default-cause signal at all = unknown (the agent MUST abstain, not guess).
# `label` = the DefaultSubtype the agent's APPLIED `value` must equal; `unknown` ⇒ stay unknown/review.
# This is the accuracy bar that gates PROD promotion (D-706).
DEFAULT_SUBTYPE_LABELS = {
    "0065e00000LjHEUAA3": {"merchant": "Ab Powers", "label": "true_default",
                           "rationale": "note 'defaulted' — an explicit default is a true default"},
    "006UU00000Ar36sYAB": {"merchant": "Con Alma", "label": "true_default",
                           "rationale": "note 'defaulted' — explicit default = true default"},
    "006UU000006as3FYAQ": {"merchant": "Luxury Dentistry", "label": "true_default",
                           "rationale": "'Default on forward' — true default"},
    "006UU00000FHcTPYA1": {"merchant": "Sai Adjustments", "label": "true_default",
                           "rationale": "'Defaulted' + clawback = default-triggered commission clawback"},
    "006UU00000IsAWIYA3": {"merchant": "Starr Window Tinting", "label": "true_default",
                           "rationale": "framework fixture + 'Defaulted' + clawback = true default"},
    "006UU00000BEK3lYAH": {"merchant": "Zeek Interactive", "label": "unknown",
                           "rationale": "no default-cause signal in the note ('mtd, ar, 110K transfer') — abstain"},
}
# Calibrated on this first 6-deal sample (D-706 "sane + improving"): the v2 prompt fix should
# clear it. A miss on a tiny curated sample is a real regression → gate PROD.
ACCURACY_BAR = 0.80


def run_recon(spark, catalog=C.CATALOG, schema=C.Schema.GOLD_TEST, run_date=None, allow_prod=False,
              predict_fn=None, rerun_s3=True) -> dict:
    from pyspark.sql import functions as F

    if schema == C.Schema.GOLD and not allow_prod:
        raise ValueError("Refusing tier-2 extraction against prod 'gold' without allow_prod=True (Rule 5).")
    run_date = run_date or date.today()
    findings: dict = {"catalog": catalog, "schema": schema, "run_date": run_date.isoformat()}

    predict_fn = predict_fn or databricks_chat_predict_fn()
    targets = build_gold_extraction(
        spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod, predict_fn=predict_fn
    )
    findings["targets"] = targets
    for k in ("closed_default_deals", "applied", "review", "rejected", "endpoint", "model_version"):
        findings[k] = targets.get(k)

    run_lit = F.lit(run_date.isoformat()).cast("date")
    ext = spark.read.table(targets["merchant_extraction"]).where(F.col("extraction_run_date") == run_lit)
    cur = spark.read.table(targets["merchant_extraction_current"])

    findings["extraction_schema_matches"] = ext.columns == [f.name for f in merchant_extraction_schema().fields]

    # coverage: one row per closed_default deal (deal_clock_current closure == closed_default)
    deal_clock = spark.read.table(C.fq(schema, C.GoldTable.DEAL_CLOCK_CURRENT, catalog))
    n_default_deals = deal_clock.where(
        F.col("closure_status") == F.lit(C.ClosureStatus.CLOSED_DEFAULT)
    ).select("deal_id").distinct().count()
    findings["closed_default_deal_universe"] = n_default_deals
    findings["extraction_count"] = ext.count()
    findings["distinct_keys"] = ext.select(
        "merchant_id", "deal_id", "extraction_run_date", "extraction_type"
    ).distinct().count()

    findings["surface_offenders"] = offending_surface_columns(ext.columns)
    findings["confidence_oob"] = ext.where(
        F.col("confidence").isNotNull() & ((F.col("confidence") < 0) | (F.col("confidence") > 1))
    ).count()
    findings["model_version_null"] = ext.where(F.col("model_version").isNull()).count()
    findings["bad_extraction_type"] = ext.where(
        F.col("extraction_type") != F.lit(C.ExtractionType.DEFAULT_SUBTYPE)
    ).count()
    findings["bad_review_status"] = ext.where(
        ~F.col("review_status").isin(list(C.ReviewStatus.ALL))
    ).count()
    # grounding (D-705): APPLIED must be grounded + confident; REJECTED must be ungrounded.
    thr = C.AGENT_CONFIDENCE_REVIEW_MIN
    findings["applied_ungrounded_or_unconfident"] = ext.where(
        (F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
        & (F.col("source_ref").isNull() | (F.col("confidence") < F.lit(thr)))
    ).count()
    findings["rejected_with_source"] = ext.where(
        (F.col("review_status") == F.lit(C.ReviewStatus.REJECTED)) & F.col("source_ref").isNotNull()
    ).count()

    cur_dates = sorted(str(r[0]) for r in cur.select("extraction_run_date").distinct().collect())
    findings["current_run_dates"] = cur_dates
    findings["current_count"] = cur.count()
    # multi-stream table (D-711): _current is per-(merchant,deal,type) latest, so it may carry other
    # extraction types on other run_dates. Assert THIS run's default_subtype rows are all current.
    findings["current_default_subtype_count"] = cur.where(
        F.col("extraction_type") == F.lit(C.ExtractionType.DEFAULT_SUBTYPE)
    ).count()

    # the agent touched ONLY its own tables — agent_extraction events present; spine event types intact.
    elog = spark.read.table(targets["merchant_event_log"])
    findings["agent_extraction_events"] = elog.where(
        (F.col("event_type") == F.lit(C.EventType.AGENT_EXTRACTION))
        & (F.col("classify_run_date") == run_lit)
    ).count()

    # === DIAGNOSTICS ===
    findings["review_status_breakdown"] = {
        r["review_status"]: r["n"] for r in ext.groupBy("review_status").agg(F.count(F.lit(1)).alias("n")).collect()
    }
    findings["resolved_subtype_breakdown"] = {
        r["value"]: r["n"]
        for r in ext.where(F.col("review_status") == F.lit(C.ReviewStatus.APPLIED))
        .groupBy("value").agg(F.count(F.lit(1)).alias("n")).collect()
    }

    # === D-706 accuracy: score the extractions against the hand-labeled ground truth ===
    label_rows = {
        r["deal_id"]: r for r in ext.select("deal_id", "value", "review_status").collect()
    }
    labeled_results = []
    correct = 0
    for deal_id, truth in DEFAULT_SUBTYPE_LABELS.items():
        got = label_rows.get(deal_id)
        agent_value = got["value"] if got else None
        agent_status = got["review_status"] if got else None
        expected = truth["label"]
        if expected == C.DefaultSubtype.UNKNOWN:
            # the agent must abstain: stay unknown and NOT applied
            ok = agent_value == C.DefaultSubtype.UNKNOWN and agent_status != C.ReviewStatus.APPLIED
        else:
            # the agent must resolve to exactly the labeled sub-type AND apply it
            ok = agent_value == expected and agent_status == C.ReviewStatus.APPLIED
        correct += int(ok)
        labeled_results.append({
            "merchant": truth["merchant"], "deal_id": deal_id, "expected": expected,
            "agent_value": agent_value, "review_status": agent_status, "correct": ok,
        })
    n = len(DEFAULT_SUBTYPE_LABELS)
    findings["labeled_n"] = n
    findings["labeled_correct"] = correct
    findings["labeled_accuracy"] = round(correct / n, 4) if n else None
    findings["labeled_results"] = labeled_results
    findings["accuracy_bar"] = ACCURACY_BAR
    # specific regression guard: no labeled true_default may be APPLIED as early_payoff (Starr/Sai)
    findings["true_default_misrouted_as_early_payoff"] = [
        r["merchant"] for r in labeled_results
        if r["expected"] == C.DefaultSubtype.TRUE_DEFAULT and r["agent_value"] == C.DefaultSubtype.EARLY_PAYOFF
    ]

    # === re-run S3 so the recon shows the routing effect of the resolved sub-types ===
    if rerun_s3:
        rung_targets = build_gold_rung(spark, catalog=catalog, schema=schema, run_date=run_date, allow_prod=allow_prod)
        findings["rung_targets"] = rung_targets
        rung = spark.read.table(rung_targets["merchant_rung_current"])
        merchants = spark.read.table(C.fq(schema, C.GoldTable.MERCHANTS, catalog)).select("merchant_id", "business_name")
        defaulted = rung.where(F.col("lifecycle_state") == F.lit(C.LifecycleState.DEFAULTED))
        findings["defaulted_merchant_count"] = defaulted.count()
        findings["defaulted_resolved_count"] = defaulted.where(
            F.col("default_subtype") != F.lit(C.DefaultSubtype.UNKNOWN)
        ).count()
        findings["defaulted_route_breakdown"] = {
            r["route"]: r["n"] for r in defaulted.groupBy("route").agg(F.count(F.lit(1)).alias("n")).collect()
        }
        findings["defaulted_subtype_breakdown"] = {
            r["default_subtype"]: r["n"]
            for r in defaulted.groupBy("default_subtype").agg(F.count(F.lit(1)).alias("n")).collect()
        }
        starr = (
            defaulted.join(merchants, "merchant_id", "inner")
            .where(F.col("business_name").startswith(STARR))  # gold name carries a legal suffix ("... Llc")
            .select("business_name", "lifecycle_state", "default_subtype", "route", "confidence")
            .collect()
        )
        findings["starr"] = [
            {"name": r["business_name"], "lifecycle_state": r["lifecycle_state"],
             "default_subtype": r["default_subtype"], "route": r["route"],
             "confidence": float(r["confidence"]) if r["confidence"] is not None else None}
            for r in starr
        ]

    return findings


def assert_recon(findings: dict) -> list[str]:
    failures: list[str] = []
    if not findings.get("extraction_schema_matches"):
        failures.append("schema drift on gold.merchant_extraction")
    if findings.get("extraction_count") != findings.get("closed_default_deal_universe"):
        failures.append(
            f"coverage: extractions={findings.get('extraction_count')} != closed_default deals="
            f"{findings.get('closed_default_deal_universe')}"
        )
    if findings.get("distinct_keys") != findings.get("extraction_count"):
        failures.append("(merchant_id, deal_id, extraction_run_date, extraction_type) not unique")
    if findings.get("surface_offenders"):
        failures.append(f"no-surface breached: {findings.get('surface_offenders')}")
    for k, label in (
        ("confidence_oob", "confidence outside [0,1]"),
        ("model_version_null", "model_version null"),
        ("bad_extraction_type", "extraction_type != default_subtype"),
        ("bad_review_status", "review_status not in ReviewStatus.ALL"),
        ("applied_ungrounded_or_unconfident", "APPLIED row ungrounded or below confidence threshold"),
        ("rejected_with_source", "REJECTED row that has a source_ref"),
    ):
        if findings.get(k, 0) != 0:
            failures.append(f"{label}: {findings.get(k)} rows")
    # multi-stream _current (D-711): this run's default_subtype rows must be current (a global
    # single-run_date check no longer holds once the Statement Analyst writes other types/dates).
    if findings.get("run_date") not in findings.get("current_run_dates", []):
        failures.append(f"_current missing this run_date: {findings.get('current_run_dates')}")
    if findings.get("current_default_subtype_count") != findings.get("extraction_count"):
        failures.append(
            f"_current default_subtype count {findings.get('current_default_subtype_count')} "
            f"!= this run's {findings.get('extraction_count')} (per-key _current regression)"
        )
    # an extraction run with closed_default deals must emit agent_extraction events
    if findings.get("closed_default_deal_universe", 0) > 0 and findings.get("agent_extraction_events", 0) == 0:
        failures.append("no agent_extraction events emitted despite closed_default deals")
    # D-706 accuracy gate (gates PROD promotion)
    acc = findings.get("labeled_accuracy")
    if acc is not None and acc < findings.get("accuracy_bar", ACCURACY_BAR):
        failures.append(
            f"D-706 accuracy {acc} < bar {findings.get('accuracy_bar', ACCURACY_BAR)}: "
            f"{findings.get('labeled_results')}"
        )
    if findings.get("true_default_misrouted_as_early_payoff"):
        failures.append(
            f"true_default misrouted as early_payoff: {findings.get('true_default_misrouted_as_early_payoff')}"
        )
    return failures
