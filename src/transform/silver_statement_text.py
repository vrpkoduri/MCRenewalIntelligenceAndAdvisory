"""Silver OCR — bank-statement PDFs (UC Volume) -> silver.statement_text (S7 Phase 2, D-708).

Deterministic text extraction is a SILVER transform, NOT the agent's job (keeps the agent's role
pure judgment, makes the text cacheable/auditable/cheaper on re-run). One row per statement file:
the extracted text + page/char counts + a `needs_ocr` flag (low char count ⇒ a scanned/image PDF
pdfplumber can't read → escalate to a true OCR pass later). The Statement Analyst agent reads this
text; raw PDFs stay in the governed Volume (D-712 — only derived aggregates ever leave the agent).

pdfplumber handles digital (text) PDFs; scanned/image PDFs yield little text and are flagged
`needs_ocr=True` for a later `ai_parse_document`/OCR escalation. Idempotent (full overwrite per run).
"""

from __future__ import annotations

VOLUME_FILES = "mca_mri.bronze.statement_files"
SILVER_TABLE = "mca_mri.silver.statement_text"
SILVER_TEST_TABLE = "mca_mri.silver_test.statement_text"
MIN_CHARS = 200  # below this, the PDF is almost certainly scanned/image → needs_ocr


def _extract_text(path: str):
    """Return (text, page_count, error) for one PDF via pdfplumber; never raises."""
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
        return "\n".join(pages), len(pages), None
    except Exception as e:  # noqa: BLE001 — a bad/locked PDF must not abort the batch
        return "", 0, str(e)[:200]


def build_silver_statement_text(spark, schema: str = "silver", allow_prod: bool = False, min_chars: int = MIN_CHARS) -> dict:
    """Extract text from every PDF in `bronze.statement_files` → `<schema>.statement_text`.
    `schema='silver'` requires allow_prod (Rule 5); use `silver_test` to dry-run."""
    if schema == "silver" and not allow_prod:
        raise ValueError("Refusing to write prod 'silver' without allow_prod=True (Rule 5). Use silver_test.")
    target = f"mca_mri.{schema}.statement_text"

    files = spark.read.table(VOLUME_FILES).select("cv_id", "deal_id", "volume_path", "as_of_date").collect()
    rows = []
    for f in files:
        text, pages, err = _extract_text(f["volume_path"])
        cc = len(text)
        rows.append({
            "cv_id": f["cv_id"], "deal_id": f["deal_id"], "as_of_date": f["as_of_date"],
            "parser": "pdfplumber", "page_count": pages, "char_count": cc,
            "needs_ocr": cc < min_chars, "parse_error": err, "text": text,
        })

    spark.createDataFrame(rows).write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(target)

    n = len(rows)
    digital = sum(1 for r in rows if not r["needs_ocr"])
    return {
        "table": target,
        "files": n,
        "digital_text_ok": digital,
        "needs_ocr": n - digital,
        "parse_errors": sum(1 for r in rows if r["parse_error"]),
        "deals": len({r["deal_id"] for r in rows}),
    }
