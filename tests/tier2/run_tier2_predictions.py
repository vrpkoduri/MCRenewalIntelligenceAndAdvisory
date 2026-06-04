# Databricks notebook source
# Tier-2 driver (S6): fit the ADOPTED models (PyMC-Marketing BG/NBD + Gamma-Gamma + CLV;
# lifelines Cox PH + KM) on the real renewal history, batch-infer, write
# gold.merchant_predictions (+`_current`) + `prediction` events, log an MLflow run, then run
# the reconciliation / sanity assertions (Build Plan §6, Framework §11.2). Parameterized:
# defaults to gold_test; prod `gold` requires schema=gold AND allow_prod=true (Rule 5).
#
# REQUIRES a Databricks ML runtime. Run as a one-time job on a single-node ML cluster.
# Expects `src/` and `recon_predictions.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
# MAGIC %pip install "pymc-marketing==0.13.1" "lifelines==0.30.0" --quiet

# COMMAND ----------
dbutils.library.restartPython()  # noqa: F821

# COMMAND ----------
import json
import sys
from datetime import date

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_predictions import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("run_date", "")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821
_rd = dbutils.widgets.get("run_date").strip()  # noqa: F821
run_date = date.fromisoformat(_rd) if _rd else None

findings = run_recon(spark, schema=schema, run_date=run_date, allow_prod=allow_prod)  # noqa: F821
failures = assert_recon(findings)
findings["FAILURES"] = failures
print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
exit_payload = {
    "failures": failures,
    "run_date": findings.get("run_date"),
    "prediction_count": findings.get("prediction_count"),
    "merchants_with_dated_advance": findings.get("merchants_with_dated_advance"),
    "insufficient_history_count": findings.get("insufficient_history_count"),
    "repeat_count": findings.get("repeat_count"),
    "predicted_clv_nonnull": findings.get("predicted_clv_nonnull"),
    "cox_fitted": findings.get("cox_fitted"),
    "mean_p_alive_by_lifecycle": findings.get("mean_p_alive_by_lifecycle"),
    "reference_merchants": findings.get("reference_merchants"),
    "targets": findings.get("targets"),
}
if failures:
    raise AssertionError(f"Tier-2 prediction reconciliation FAILED: {json.dumps(exit_payload, default=str)}")
dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
