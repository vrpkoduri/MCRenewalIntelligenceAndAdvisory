# Databricks notebook source
# Tier-2 driver for the optional tables: build mca_mri.<schema>.{offers,field_history}
# from live bronze and run reconciliation. Defaults to silver_test; prod requires
# schema=silver AND allow_prod=true (Rule 5 — writes prod tables).
#
# Uploaded + run as a one-time serverless job; the repo copy is the reproducible source.
# Expects `src/` and `recon_aux.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
import json
import sys

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_aux import assert_aux, run_aux_recon  # noqa: E402

dbutils.widgets.text("schema", "silver_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821

findings = run_aux_recon(spark, schema=schema, allow_prod=allow_prod)  # noqa: F821
failures = assert_aux(findings)
findings["FAILURES"] = failures

print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
if failures:
    raise AssertionError(f"Tier-2 aux reconciliation FAILED: {json.dumps(findings, default=str)}")

dbutils.notebook.exit(json.dumps(findings, default=str))  # noqa: F821
