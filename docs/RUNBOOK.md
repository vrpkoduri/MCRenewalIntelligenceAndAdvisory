# Runbook — how to run things

## Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Tests

```bash
python3 -m pytest -q          # tier-1 (local, no Spark needed)
python3 -m pytest -q -m spark # tier-2 (needs pyspark+JDK locally, or run as a Databricks job)
```

## Databricks bundle (DAB)

```bash
databricks bundle validate -t dev      # validate config (safe, read-only)
# Deploys are gated on your approval (Rule 5):
# databricks bundle deploy -t dev
```

Workspace: `https://adb-1070256156274807.7.azuredatabricks.net` (profile DEFAULT, user venkat@morgancash.com).

## Tier-2 reconciliation on Databricks (one-time serverless job)

The tier-2 recons run ON Databricks (need Spark). Pattern: stage `src/` + the recon module as Workspace **files** and the driver as a **notebook**, submit a serverless job, parse `notebook_output.result`, then delete the staging dir.

```bash
STAGE=/Workspace/Users/venkat@morgancash.com/mri_tier2
databricks workspace mkdirs "$STAGE"
databricks workspace import-dir src "$STAGE/src" --overwrite
databricks workspace import "$STAGE/recon_clock.py" --file tests/tier2/recon_clock.py --format AUTO --overwrite
databricks workspace import "$STAGE/run_tier2_clock" --file tests/tier2/run_tier2_clock.py --language PYTHON --format SOURCE --overwrite
# Submit against gold_test (Rule 5 — never prod without approval):
databricks jobs submit --no-wait --json '{"run_name":"mri_tier2_clock","tasks":[{"task_key":"t","notebook_task":{"notebook_path":"'"$STAGE"'/run_tier2_clock","base_parameters":{"schema":"gold_test","allow_prod":"false","run_date":"2026-05-31"}}}]}'
# ... poll get-run; read get-run-output -> notebook_output.result; expect "failures": []
databricks workspace delete "$STAGE" --recursive   # clean up staging
```

## Daily clock recompute (S2, Appendix A — THE core principle)

`transform/gold_clock.build_gold_clock(spark, schema, run_date, allow_prod)` recomputes `gold.deal_clock` + `gold.merchant_clock` for one `run_date` (defaults to today) from `gold.deals` static terms + silver notes, append-only and idempotent per run (`replaceWhere` on `clock_run_date`), refreshing the `*_current` views. **It NEVER reads SF stored balance/paydown/eligible-date** (those are `silver` `_sf_stored_*` checkpoint columns only).

- **Verify on `gold_test` first** (default; `allow_prod=False`). **PROD `gold` write requires `schema=gold` AND `allow_prod=True`** — gated on explicit approval (Rule 5).
- The production cadence is a **daily DAB job** (defined as code; scheduling/activation is itself approval-gated — not auto-enabled).

## Unity Catalog quick reference

```bash
databricks catalogs list
databricks schemas list mca_mri          # once the catalog exists
databricks connections list              # UC connections (incl. future Salesforce)
databricks secrets list-scopes
```

## Salesforce ingestion

Not yet created. See [SALESFORCE_CONNECTION_GUIDE.md](SALESFORCE_CONNECTION_GUIDE.md).
Credentials live in a secret scope — never in code or shell history.

## Conventions

- Medallion: `mca_mri.bronze` (raw, immutable) → `mca_mri.silver` (cleaned/typed) → `mca_mri.gold` (later).
- Integration-test writes isolate to `*_test` schemas.
- Bronze is never edited; all cleaning is bronze→silver.
