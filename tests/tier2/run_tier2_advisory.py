# Databricks notebook source
# Tier-2 driver (S8): compose grounded, compliance-gated advisories (gold.merchant_advisory +
# `_current` + advisory_composed/compliance_checked events) by calling a Databricks Foundation
# Model (Claude) over active merchants, then run the reconciliation / gate-integrity assertions
# (Framework §2.3/§2.4/§5.9, D-801..D-807). **S8 composes + gates; it does NOT send.**
# Parameterized: defaults to gold_test; promoting to prod `gold` requires schema=gold AND
# allow_prod=true (Rule 5). `sample_merchant_ids` (comma-separated) restricts to a cheap sample-
# first / D-807-labeled set; empty = all active merchants. `run_date` defaults to the job's today.
#
# Uploaded + run as a one-time job; the repo copy is the reproducible source. Expects `src/` and
# `recon_advisory.py` staged as Workspace FILES under STAGE. Needs a Foundation Model endpoint.
# NOTE: D-805 disclosure state-list should have counsel review before a PROD run; the D-807 labeled
# sample (recon_advisory.ADVISORY_LABELS) should be filled before promotion.

# COMMAND ----------
import json
import sys
from datetime import date

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_advisory import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("run_date", "")  # noqa: F821
dbutils.widgets.text("endpoint", "databricks-claude-sonnet-4-5")  # noqa: F821
dbutils.widgets.text("sample_merchant_ids", "")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821
endpoint = dbutils.widgets.get("endpoint").strip()  # noqa: F821
_rd = dbutils.widgets.get("run_date").strip()  # noqa: F821
run_date = date.fromisoformat(_rd) if _rd else None
_ids = dbutils.widgets.get("sample_merchant_ids").strip()  # noqa: F821
sample_merchant_ids = [s.strip() for s in _ids.split(",") if s.strip()] or None

from transform.gold_extraction import databricks_chat_predict_fn  # noqa: E402

predict_fn = databricks_chat_predict_fn()

findings = run_recon(  # noqa: F821
    spark, schema=schema, run_date=run_date, allow_prod=allow_prod,  # noqa: F821
    sample_merchant_ids=sample_merchant_ids, predict_fn=predict_fn,
)
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
    "advised_merchants": findings.get("advised_merchants"),
    "advisory_rows": findings.get("advisory_rows"),
    "offers_present": findings.get("offers_present"),
    "type_breakdown": findings.get("type_breakdown"),
    "compliance_breakdown": findings.get("compliance_breakdown"),
    "review_breakdown": findings.get("review_breakdown"),
    "blocked_but_deliverable": findings.get("blocked_but_deliverable"),
    "applied_not_pass": findings.get("applied_not_pass"),
    "labeled_accuracy": findings.get("labeled_accuracy"),
    "labeled_results": findings.get("labeled_results"),
    "targets": findings.get("targets"),
}

if failures:
    raise AssertionError(f"Tier-2 advisory reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
