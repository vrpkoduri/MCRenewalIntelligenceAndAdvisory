# Databricks notebook source
# Tier-2 driver (task #7): build the S2 Amortization Clock (gold.deal_clock +
# gold.merchant_clock + `*_current` views) from live gold.deals + silver notes and run the
# reconciliation/integrity assertions (Appendix A). Parameterized: defaults to gold_test;
# promoting to prod `gold` requires schema=gold AND allow_prod=true (Rule 5 — writes prod
# managed tables). `run_date` defaults to the job's today; pass YYYY-MM-DD to pin it.
#
# Uploaded + run as a one-time serverless job; the repo copy is the reproducible source.
# Expects `src/` and `recon_clock.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
import json
import sys
from datetime import date

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_clock import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("run_date", "")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
allow_prod = dbutils.widgets.get("allow_prod").strip().lower() == "true"  # noqa: F821
_rd = dbutils.widgets.get("run_date").strip()  # noqa: F821
run_date = date.fromisoformat(_rd) if _rd else None

findings = run_recon(  # noqa: F821
    spark, schema=schema, run_date=run_date, allow_prod=allow_prod
)
failures = assert_recon(findings)
findings["FAILURES"] = failures

print(json.dumps(findings, indent=2, default=str))

# COMMAND ----------
exit_payload = {
    "failures": failures,
    "run_date": findings.get("run_date"),
    "gold_deals_count": findings.get("gold_deals_count"),
    "deal_clock_count": findings.get("deal_clock_count"),
    "merchant_clock_count": findings.get("merchant_clock_count"),
    "closure_breakdown": findings.get("closure_breakdown"),
    "is_eligible_now_count": findings.get("is_eligible_now_count"),
    "clock_inputs_missing_count": findings.get("clock_inputs_missing_count"),
    "rtr_checkpoint_delta_max": findings.get("rtr_checkpoint_delta_max"),
    "rtr_checkpoint_exceeds_tolerance": findings.get("rtr_checkpoint_exceeds_tolerance"),
    "elapsed_at_term_cap": findings.get("elapsed_at_term_cap"),
    "reference_merchants": findings.get("reference_merchants"),
    "targets": findings.get("targets"),
}

if failures:
    raise AssertionError(f"Tier-2 clock reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
