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

**Gotchas (Windows):** use **PowerShell** for the CLI (Git Bash mangles `/Workspace` paths); write `--json` bodies to a UTF-8-no-BOM file (`[System.IO.File]::WriteAllText(path, body, New-Object System.Text.UTF8Encoding $false)`) and pass `--json @file`; re-stage a single changed file with `--format AUTO` (it's a FILE, not a NOTEBOOK — `--language PYTHON --format SOURCE` errors with a type mismatch).

### S7 Data Steward extraction (LLM — `run_tier2_extraction`)
Same pattern; stage `recon_extraction.py` + `run_tier2_extraction`. Extra `base_parameters`: `endpoint` (default `databricks-claude-sonnet-4-5` — verify provisioned via `databricks serving-endpoints list`), `rerun_s3` (`true` re-runs S3 to route on the resolved sub-types). Calls a **Foundation Model via the `databricks-sdk` serving client** (serverless has the SDK, NOT `mlflow`); LLM spend is bounded to the `closed_default` deals (~6). **PROD `gold` promotion is gated on the D-706 labeled-sample accuracy pass** — `gold_test` only until then. Run 2026-06-05: `failures: []`, 3 APPLIED / 3 REVIEW / 0 REJECTED (C-023).

## Daily clock recompute (S2, Appendix A — THE core principle)

`transform/gold_clock.build_gold_clock(spark, schema, run_date, allow_prod)` recomputes `gold.deal_clock` + `gold.merchant_clock` for one `run_date` (defaults to today) from `gold.deals` static terms + silver notes, append-only and idempotent per run (`replaceWhere` on `clock_run_date`), refreshing the `*_current` views. **It NEVER reads SF stored balance/paydown/eligible-date** (those are `silver` `_sf_stored_*` checkpoint columns only).

- **Verify on `gold_test` first** (default; `allow_prod=False`). **PROD `gold` write requires `schema=gold` AND `allow_prod=True`** — gated on explicit approval (Rule 5).
- The production cadence is a **daily DAB job** (defined as code; scheduling/activation is itself approval-gated — not auto-enabled).

## Daily rung classify (S3, Appendix B)

`transform/gold_rung.build_gold_rung(spark, schema, run_date, allow_prod)` classifies the whole book for one `run_date` (defaults to today) from the **S2 clock** (`gold.merchant_clock_current` + `gold.deal_clock_current`) + `gold.deals`/`gold.merchants` — it READS the clock and **never recomputes the spine**. Writes point-in-time `gold.merchant_rung` (+`merchant_rung_current` view, `replaceWhere` idempotent per `classify_run_date`) and appends classification + transition events to `gold.merchant_event_log`. The per-merchant logic is the pure `common.rung` engine applied via UDFs (no Spark reimplementation).

- Tier-2 recipe = the block above with `recon_rung.py` / `run_tier2_rung` (the `second_run` widget, default true, does a 2nd run at `run_date + 1 day` to exercise the state machine; set `second_run=false` for a clean single-partition prod build). **Verified on `gold_test` then PROMOTED to PROD `gold` 2026-06-02 (`failures: []`).**
- **PROD `gold` write requires `schema=gold` AND `allow_prod=True`** — gated on explicit approval (Rule 5). Done 2026-06-02.
- `confidence` is a deterministic rules score (D-306), NOT an ML probability (ML is S6).

## MRI dashboard (AI/BI Lakeview, C-021)

Read-only renderer over PROD `mca_mri.gold` `_current` views — Book Health scoreboard, Daily Queue, Merchant 360 (incl. predictions). Authored as code; **no writes, no Salesforce** (reps-in-SF is the deferred FU-401).

```bash
python resources/build_mri_dashboard.py     # regenerates resources/mri_dashboard.lvdash.json + .dashboard_body.json
# Create (once) + publish on the Starter Warehouse:
databricks api post /api/2.0/lakeview/dashboards --json @.dashboard_body.json          # -> dashboard_id
databricks api post /api/2.0/lakeview/dashboards/<dashboard_id>/published --json '{"warehouse_id":"526a06bbae2df35b"}'
# To update an existing dashboard: PATCH /api/2.0/lakeview/dashboards/<id> with a new serialized_dashboard, then re-publish.
```

- **Deployed 2026-06-02:** dashboard_id `01f160f949351871b77829f9bf12c942` ("Morgan Cash MRI — Merchant Intelligence"), published on the Starter Warehouse. Opening it starts the warehouse (auto-stops after idle).
- The spec (`resources/mri_dashboard.lvdash.json`) is the source of truth; edit the builder + redeploy.

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
