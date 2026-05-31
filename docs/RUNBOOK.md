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
