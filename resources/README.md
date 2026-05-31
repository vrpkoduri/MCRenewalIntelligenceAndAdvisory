# resources/ — DAB job & pipeline definitions

Databricks Asset Bundle resource files, included by `databricks.yml`.

- `ingestion_pipeline.yml` — **created.** Lakeflow Connect Salesforce → `mca_mri.bronze`
  (4 objects) + the every-6h refresh job. Mirrors the live UI-created pipeline
  (`0b6d521b-…`) / job (`872972059788644`). **Bind before first deploy** (see the deploy-gate
  note in that file) so the bundle adopts the running resource instead of duplicating it.
- `silver_job.yml` — planned: scheduled daily bronze → silver workflow (S0 exit criterion).

Nothing here deploys until you approve (Rule 5). `databricks bundle validate -t dev` is
safe and should pass at all times.
