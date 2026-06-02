# Databricks notebook source
# Tier-2 driver (S4): build the Activation layer (gold.merchant_activation + `_current` +
# the gold.daily_queue view), append state_transition/play_fired events, and build the
# gold.book_health scoreboard (+ 3 `_current` views) from the live S2/S3 gold, then run the
# reconciliation / integrity assertions (Build Plan §6, Framework 5.8). Parameterized:
# defaults to gold_test; prod `gold` requires schema=gold AND allow_prod=true (Rule 5). NO
# Salesforce write (D-403 — serving layer only). `second_run` (default true) does a 2nd run
# at run_date + 1 day to exercise the state machine; set false for a clean single-partition
# prod build. `run_date` defaults to the job's today; pass YYYY-MM-DD to pin it.
#
# Uploaded + run as a one-time serverless job; the repo copy is the reproducible source.
# Expects `src/` and `recon_activation.py` staged as Workspace FILES under STAGE.

# COMMAND ----------
import json
import sys
from datetime import date

STAGE = "/Workspace/Users/venkat@morgancash.com/mri_tier2"
for p in (f"{STAGE}/src", STAGE):
    if p not in sys.path:
        sys.path.insert(0, p)

from recon_activation import assert_recon, run_recon  # noqa: E402

dbutils.widgets.text("schema", "gold_test")  # noqa: F821
dbutils.widgets.text("allow_prod", "false")  # noqa: F821
dbutils.widgets.text("run_date", "")  # noqa: F821
dbutils.widgets.text("second_run", "true")  # noqa: F821
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
    "rung_merchant_universe": findings.get("rung_merchant_universe"),
    "activation_count": findings.get("activation_count"),
    "current_state_breakdown": findings.get("current_state_breakdown"),
    "active_play_breakdown": findings.get("active_play_breakdown"),
    "rung_distribution": findings.get("rung_distribution"),
    "book_health_row_count": findings.get("book_health_row_count"),
    "run2_state_transition_events": findings.get("run2_state_transition_events"),
    "run2_play_fired_events": findings.get("run2_play_fired_events"),
    "reference_merchants": findings.get("reference_merchants"),
    "targets": findings.get("targets"),
}

if failures:
    raise AssertionError(f"Tier-2 activation reconciliation FAILED: {json.dumps(exit_payload, default=str)}")

dbutils.notebook.exit(json.dumps(exit_payload, default=str))  # noqa: F821
