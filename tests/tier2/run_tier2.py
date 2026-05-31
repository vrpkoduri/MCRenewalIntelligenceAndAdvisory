# Databricks notebook source
# Tier-2 driver (task #11): build mca_mri.<schema>.deals from live bronze and run the
# reconciliation/DQ assertions. Parameterized: defaults to silver_test; promoting to
# prod `silver` requires schema=silver AND allow_prod=true (Rule 5 — writes prod table).
#
# Uploaded + run as a one-time serverless job; the repo copy is the reproducible source.
# It expects `src/` and `recon_silver_deals.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
import json
import sys

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_silver_deals import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "silver_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821

findings = run_recon(spark, schema=schema, allow_prod=allow_prod)  # noqa: F821
failures = assert_recon(findings)
findings["FAILURES"] = failures

print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
exit_payload = {
    "failures": failures,
    "silver_deals_count": findings.get("silver_deals_count"),
    "bronze_funded_count": findings.get("bronze_funded_count"),
    "schema_matches": findings.get("schema_matches"),
    "consumed_surface_offenders": findings.get("consumed_surface_offenders"),
    "dq_counts": findings.get("dq_counts"),
    "reference_merchants_found": findings.get("reference_merchants_found"),
    "target_table": findings.get("target_table"),
}

# Fail the run loudly if any hard expectation missed; otherwise exit with the summary
# (fetchable via the run output) so the green result is captured structurally.
if failures:
    raise AssertionError(f"Tier-2 reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
