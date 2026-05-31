# Databricks notebook source
# Tier-2 driver (task #19): build the S1 gold layer (identity + canonical Deal Table)
# from live silver/bronze and run the reconciliation/integrity assertions. Parameterized:
# defaults to gold_test; promoting to prod `gold` requires schema=gold AND allow_prod=true
# (Rule 5 — writes prod managed tables).
#
# Uploaded + run as a one-time serverless job; the repo copy is the reproducible source.
# Expects `src/` and `recon_gold.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
import json
import sys

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_gold import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("enrich_aatm", "true")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821
enrich_aatm = dbutils.widgets.get("enrich_aatm").strip().lower() == "true"  # noqa: F821

findings = run_recon(  # noqa: F821
    spark, schema=schema, enrich_aatm=enrich_aatm, allow_prod=allow_prod
)
failures = assert_recon(findings)
findings["FAILURES"] = failures

print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
exit_payload = {
    "failures": failures,
    "silver_deals_count": findings.get("silver_deals_count"),
    "gold_deals_count": findings.get("gold_deals_count"),
    "deals_null_merchant_id": findings.get("deals_null_merchant_id"),
    "collapse_ratio": findings.get("collapse_ratio"),
    "match_reason_breakdown": findings.get("match_reason_breakdown"),
    "azure_fill_rate": findings.get("azure_fill_rate"),
    "reference_merchants": findings.get("reference_merchants"),
    "targets": findings.get("targets"),
}

if failures:
    raise AssertionError(f"Tier-2 gold reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
