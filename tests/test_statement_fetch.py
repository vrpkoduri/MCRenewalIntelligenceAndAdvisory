"""Tier-1 tests for the pure helpers in `ingestion/statement_fetch.py` (S7 Phase 2 / D-707).
The network/Volume I/O (token, download, writes) is the driver and is exercised on Databricks;
here we pin the deterministic classifier, the location→deal map, and the metadata-row builder.
"""

from __future__ import annotations

from ingestion.statement_fetch import (
    covered_statement,
    is_statement,
    location_to_deal_map,
    statement_file_row,
)


def test_is_statement_doctype_or_title():
    assert is_statement("anything.pdf", "Bank Statement") is True       # structured tag wins
    assert is_statement("October 2025 Statement", None) is True          # title heuristic
    assert is_statement("WellsFargo checking", "Signed Application") is True
    assert is_statement("drivers_license.pdf", "Signed Application") is False
    assert is_statement(None, None) is False


def test_location_to_deal_map_opp_and_submission():
    rows = [
        {"opp_id": "006A", "sub_id": "a0oX"},
        {"opp_id": "006B", "sub_id": None},
    ]
    m = location_to_deal_map(rows)
    assert m["006A"] == "006A" and m["a0oX"] == "006A"   # both link to deal 006A
    assert m["006B"] == "006B"
    assert "a0oZ" not in m


def test_covered_statement_only_funded_statements():
    loc_to_deal = {"a0oX": "006A", "006B": "006B"}
    stmt_linked = {"Id": "068a", "Title": "Oct Statement", "Document_Type__c": None, "FirstPublishLocationId": "a0oX"}
    assert covered_statement(stmt_linked, loc_to_deal) == "006A"
    non_stmt = {"Id": "068b", "Title": "license.pdf", "Document_Type__c": None, "FirstPublishLocationId": "a0oX"}
    assert covered_statement(non_stmt, loc_to_deal) is None          # not a statement
    unlinked = {"Id": "068c", "Title": "Bank Statement", "Document_Type__c": "Bank Statement", "FirstPublishLocationId": "a0oZZZ"}
    assert covered_statement(unlinked, loc_to_deal) is None          # statement, but not funded-linked


def test_statement_file_row_shape_and_as_of():
    cv = {"Id": "068x", "Title": "Nov Statement", "Document_Type__c": "Bank Statement",
          "FileType": "PDF", "ContentSize": 12345, "CreatedDate": "2026-05-01T10:20:30.000+0000",
          "FirstPublishLocationId": "a0oX"}
    row = statement_file_row(cv, "006A", "abc123", "/Volumes/mca_mri/bronze/statements_raw/068x.pdf")
    assert row["cv_id"] == "068x" and row["deal_id"] == "006A"
    assert row["as_of_date"] == "2026-05-01"     # CreatedDate truncated to the date (funding-moment proxy)
    assert row["sha256"] == "abc123" and row["content_size"] == 12345
