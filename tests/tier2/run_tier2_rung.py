# Databricks notebook source
# Tier-2 driver (S3): build the Rung Classifier (gold.merchant_rung + `_current` view) and
# the append-only gold.merchant_event_log from the live S2 clock + gold deals/merchants, then
# run the reconciliation / integrity assertions (Appendix B). Parameterized: defaults to
# gold_test; promoting to prod `gold` requires schema=gold AND allow_prod=true (Rule 5 —
# writes prod managed tables). `run_date` defaults to the job's today; pass YYYY-MM-DD to pin
# it. The recon also performs a SECOND run (run_date + 1 day) to exercise the state machine.
#
# Uploaded + run as a one-time serverless job; the repo copy is the reproducible source.
# Expects `src/` and `recon_rung.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
import json
import sys
from datetime import date

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_rung import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("run_date", "")  # noqa: F821
dbutils.widgets.text("second_run", "true")  # noqa: F821 — state-machine check; off for a clean prod single-partition build
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821
second_run = dbutils.widgets.get("second_run").strip().lower() == "true"  # noqa: F821
_rd = dbutils.widgets.get("run_date").strip()  # noqa: F821
run_date = date.fromisoformat(_rd) if _rd else None

findings = run_recon(  # noqa: F821
    spark, schema=schema, run_date=run_date, allow_prod=allow_prod, second_run=second_run
)
failures = assert_recon(findings)
findings["FAILURES"] = failures

print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
exit_payload = {
    "failures": failures,
    "run_date": findings.get("run_date"),
    "second_run_date": findings.get("second_run_date"),
    "clock_merchant_universe": findings.get("clock_merchant_universe"),
    "merchant_rung_count": findings.get("merchant_rung_count"),
    "lifecycle_breakdown": findings.get("lifecycle_breakdown"),
    "rung_breakdown": findings.get("rung_breakdown"),
    "unclassified_count": findings.get("unclassified_count"),
    "unclassified_top_missing_signals": findings.get("unclassified_top_missing_signals"),
    "direction_breakdown": findings.get("direction_breakdown"),
    "rapid_reup_count": findings.get("rapid_reup_count"),
    "renewal_chain_incomplete_count": findings.get("renewal_chain_incomplete_count"),
    "gated_count": findings.get("gated_count"),
    "classification_event_count": findings.get("classification_event_count"),
    "run2_transition_events": findings.get("run2_transition_events"),
    "reference_merchants": findings.get("reference_merchants"),
    "targets": findings.get("targets"),
}

if failures:
    raise AssertionError(f"Tier-2 rung reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
