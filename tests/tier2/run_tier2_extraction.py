# Databricks notebook source
# Tier-2 driver (S7 Phase 1): build the Data Steward agentic extraction
# (gold.merchant_extraction + `_current` + agent_extraction events) by calling a Databricks
# Foundation Model (Claude) over every closed_default deal's Notes, then RE-RUN the S3 rung
# classifier so the resolved sub-types route, and run the reconciliation / grounding assertions
# (Framework §5.9, D-704/D-705). Parameterized: defaults to gold_test; promoting to prod `gold`
# requires schema=gold AND allow_prod=true (Rule 5 — writes prod managed tables + spends on the LLM).
# `run_date` defaults to the job's today; pass YYYY-MM-DD to pin it.
#
# Uploaded + run as a one-time job; the repo copy is the reproducible source. Expects `src/` and
# `recon_extraction.py` staged as Workspace FILES under STAGE. Needs the MLflow deployments client
# (present on Databricks runtimes) + a provisioned Foundation Model endpoint.

# COMMAND ----------
import json
import sys
from datetime import date

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_extraction import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("run_date", "")  # noqa: F821
dbutils.widgets.text("endpoint", "databricks-claude-sonnet-4-5")  # noqa: F821
dbutils.widgets.text("rerun_s3", "true")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821
rerun_s3 = dbutils.widgets.get("rerun_s3").strip().lower() == "true"  # noqa: F821
endpoint = dbutils.widgets.get("endpoint").strip()  # noqa: F821
_rd = dbutils.widgets.get("run_date").strip()  # noqa: F821
run_date = date.fromisoformat(_rd) if _rd else None

# real Foundation Model caller, pinned to the chosen endpoint
from transform.gold_extraction import databricks_chat_predict_fn  # noqa: E402

predict_fn = databricks_chat_predict_fn()

findings = run_recon(  # noqa: F821
    spark, schema=schema, run_date=run_date, allow_prod=allow_prod, predict_fn=predict_fn, rerun_s3=rerun_s3
)
# pin the endpoint actually used (the driver default may be overridden via the widget in a later rev)
findings["endpoint_widget"] = endpoint
failures = assert_recon(findings)
findings["FAILURES"] = failures

print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
exit_payload = {
    "failures": failures,
    "run_date": findings.get("run_date"),
    "endpoint": findings.get("endpoint"),
    "model_version": findings.get("model_version"),
    "closed_default_deal_universe": findings.get("closed_default_deal_universe"),
    "extraction_count": findings.get("extraction_count"),
    "review_status_breakdown": findings.get("review_status_breakdown"),
    "resolved_subtype_breakdown": findings.get("resolved_subtype_breakdown"),
    "agent_extraction_events": findings.get("agent_extraction_events"),
    "labeled_accuracy": findings.get("labeled_accuracy"),
    "labeled_correct": findings.get("labeled_correct"),
    "labeled_n": findings.get("labeled_n"),
    "labeled_results": findings.get("labeled_results"),
    "true_default_misrouted_as_early_payoff": findings.get("true_default_misrouted_as_early_payoff"),
    "defaulted_merchant_count": findings.get("defaulted_merchant_count"),
    "defaulted_resolved_count": findings.get("defaulted_resolved_count"),
    "defaulted_route_breakdown": findings.get("defaulted_route_breakdown"),
    "defaulted_subtype_breakdown": findings.get("defaulted_subtype_breakdown"),
    "starr": findings.get("starr"),
    "targets": findings.get("targets"),
}

if failures:
    raise AssertionError(f"Tier-2 extraction reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
