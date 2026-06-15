"""Bank-statement binary fetch — Salesforce Files → UC Volume (S7 Phase 2, D-707/C-026).

The Lakeflow connector drops the `VersionData` base64 blob, so the statement PDFs are pulled
out-of-band: a headless **Client Credentials** token (secret scope `mri-salesforce-api`) → SOQL
to enumerate the statement `ContentVersion`s linked to the funded book → download each
`VersionData` into a governed UC **Volume** (`mca_mri.bronze.statements_raw`) → record one row per
file in `bronze.statement_files`. Raw + immutable (bronze); the agent reads the OCR'd text later.

Linkage (spike §1.6): a file's `FirstPublishLocationId` is either the Opportunity id (`006…`) or the
Application_Submission id (`a0o…`); the latter joins to the funded deal via
`Opportunity.Application_Submission__c`. Identification: the structured `Document_Type__c='Bank
Statement'` tag UNION a title heuristic (recall).

PII (D-712): the Volume is governed by Unity Catalog (restrict grants to the MRI service principal);
only DERIVED aggregates ever leave the agent — raw account numbers / balances are never surfaced.

The pure helpers (classifier, location→deal map, the metadata-row builder) are tier-1 testable; the
network/Volume I/O lives in `fetch_statements` (the driver, run on Databricks).
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request

INSTANCE = "https://mcabrokerage.my.salesforce.com"
API_VERSION = "v60.0"
SECRET_SCOPE = "mri-salesforce-api"
VOLUME = "mca_mri.bronze.statements_raw"
VOLUME_PATH = "/Volumes/mca_mri/bronze/statements_raw"

# SF id key prefixes that link a file to a funded deal.
_OPP_PREFIX = "006"  # Opportunity
_SUB_PREFIX = "a0o"  # Application_Submission custom object

_TITLE_RE = re.compile(r"statement|bank|mtd|deposit|checking", re.IGNORECASE)


# --- pure helpers (tier-1 testable) ----------------------------------------------


def is_statement(title, document_type) -> bool:
    """Identify a bank statement: the structured `Document_Type__c='Bank Statement'` tag (precision)
    OR a title keyword match (recall). Either is sufficient."""
    if document_type and str(document_type).strip().lower() == "bank statement":
        return True
    return bool(title and _TITLE_RE.search(str(title)))


def location_to_deal_map(funded_rows) -> dict:
    """Build {location_id: deal_id} from funded (opp_id, sub_id) pairs — a file published to either
    the Opportunity (006) or its Application_Submission (a0o) maps to the same funded deal."""
    m: dict = {}
    for r in funded_rows:
        opp_id, sub_id = r.get("opp_id"), r.get("sub_id")
        if opp_id:
            m[opp_id] = opp_id
        if sub_id:
            m.setdefault(sub_id, opp_id)
    return m


def covered_statement(cv: dict, loc_to_deal: dict):
    """Return the funded deal_id this ContentVersion is a statement for, or None if it isn't a
    statement or isn't linked to the funded book. `cv` carries Id/Title/Document_Type__c/
    FirstPublishLocationId."""
    if not is_statement(cv.get("Title"), cv.get("Document_Type__c")):
        return None
    return loc_to_deal.get(cv.get("FirstPublishLocationId"))


def statement_file_row(cv: dict, deal_id, sha256: str, volume_path: str) -> dict:
    """One `bronze.statement_files` row. `as_of_date` uses the file CreatedDate as the funding-moment
    proxy (the agent refines it from the statement content during extraction)."""
    created = cv.get("CreatedDate")
    return {
        "cv_id": cv.get("Id"),
        "deal_id": deal_id,
        "location_id": cv.get("FirstPublishLocationId"),
        "title": cv.get("Title"),
        "document_type": cv.get("Document_Type__c"),
        "file_type": cv.get("FileType"),
        "content_size": cv.get("ContentSize"),
        "created_date": created,
        "as_of_date": (created[:10] if created else None),
        "volume_path": volume_path,
        "sha256": sha256,
    }


# --- Salesforce REST (driver) ----------------------------------------------------


def sf_token(client_id: str, client_secret: str, instance: str = INSTANCE) -> tuple[str, str]:
    """Mint a Client-Credentials access token. Returns (token, instance_url)."""
    data = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    ).encode()
    req = urllib.request.Request(f"{instance}/services/oauth2/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read().decode())
    return tok["access_token"], tok.get("instance_url", instance)


def _authed_get(url: str, token: str):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=180)


def enumerate_statements(token: str, inst: str) -> list[dict]:
    """All latest statement-candidate ContentVersion metadata (paginated). Metadata only."""
    soql = (
        "SELECT Id, ContentSize, CreatedDate, FirstPublishLocationId, FileType, Title, Document_Type__c "
        "FROM ContentVersion WHERE IsLatest=true AND "
        "(Document_Type__c='Bank Statement' OR Title LIKE '%statement%' OR Title LIKE '%bank%' "
        "OR Title LIKE '%MTD%' OR Title LIKE '%deposit%' OR Title LIKE '%checking%')"
    )
    recs: list[dict] = []
    url = f"{inst}/services/data/{API_VERSION}/query?q={urllib.parse.quote(soql)}"
    while url:
        with _authed_get(url, token) as r:
            page = json.loads(r.read().decode())
        recs.extend(page.get("records", []))
        nxt = page.get("nextRecordsUrl")
        url = (inst + nxt) if nxt else None
    return recs


def download_version_data(token: str, inst: str, cv_id: str) -> bytes:
    """Download one ContentVersion's binary `VersionData`."""
    with _authed_get(f"{inst}/services/data/{API_VERSION}/sobjects/ContentVersion/{cv_id}/VersionData", token) as r:
        return r.read()


def fetch_statements(spark, client_id: str, client_secret: str, allow_prod: bool = False) -> dict:
    """Driver: enumerate funded-linked statements, download each PDF into the UC Volume, and write
    `bronze.statement_files`. Raw bronze (immutable source) — re-runnable/idempotent on cv_id.
    Returns a summary. Volume + bronze are the shared raw layer (S0 ingests bronze directly too).
    `client_id`/`client_secret` are read from the `mri-salesforce-api` secret scope by the caller
    (an imported module can't see the notebook's `dbutils`)."""
    from pyspark.sql import functions as F

    token, inst = sf_token(client_id, client_secret)

    # funded book + its submission link → location→deal map
    opp = spark.read.table("mca_mri.bronze.opportunity")
    om = {c.lower(): c for c in opp.columns}
    opp2 = opp.select(F.col("Id").alias("opp_id"), F.col(om["application_submission__c"]).alias("sub_id"))
    funded = spark.read.table("mca_mri.gold.deals").select(F.col("deal_id").alias("opp_id")).distinct()
    funded_rows = [r.asDict() for r in funded.join(opp2, "opp_id", "left").collect()]
    loc_to_deal = location_to_deal_map(funded_rows)

    cvs = enumerate_statements(token, inst)
    covered = [(cv, covered_statement(cv, loc_to_deal)) for cv in cvs]
    covered = [(cv, d) for cv, d in covered if d is not None]

    spark.sql(f"CREATE VOLUME IF NOT EXISTS {VOLUME}")
    rows, downloaded, bytes_total = [], 0, 0
    for cv, deal_id in covered:
        cv_id = cv["Id"]
        path = f"{VOLUME_PATH}/{cv_id}.pdf"
        content = download_version_data(token, inst, cv_id)
        with open(path, "wb") as f:
            f.write(content)
        downloaded += 1
        bytes_total += len(content)
        rows.append(statement_file_row(cv, deal_id, hashlib.sha256(content).hexdigest(), path))

    if rows:
        df = spark.createDataFrame(rows)
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(
            "mca_mri.bronze.statement_files"
        )

    return {
        "statement_candidates": len(cvs),
        "covered_files": len(covered),
        "downloaded": downloaded,
        "downloaded_mb": round(bytes_total / 1e6, 1),
        "covered_deals": len({d for _, d in covered}),
        "volume": VOLUME,
        "metadata_table": "mca_mri.bronze.statement_files",
    }
