"""Loader for the authoritative Data Contract workbook.

The Gold-Table Data Contract xlsx is the single source of truth for the schema. This
module reads it so tests can assert our code (field_maps, schemas) never drifts from the
contract (GENERAL_INSTRUCTIONS Rule 3). Pure Python + pandas (no Spark).
"""

from functools import lru_cache
from pathlib import Path

import pandas as pd

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "Morgan_Cash_Gold_Table_Data_Contract.xlsx"
)


def contract_path() -> Path:
    return _CONTRACT_PATH


@lru_cache(maxsize=4)
def _sheet(sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(_CONTRACT_PATH, sheet_name=sheet_name, header=None)


def _fields_from_sheet(sheet_name: str) -> dict[str, str]:
    """Return {field_name: verdict} from a contract table sheet.

    The header row is the one whose first cell == 'Field'; data rows follow until blanks.
    """
    df = _sheet(sheet_name)
    header_idx = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "Field":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"No 'Field' header row found in sheet {sheet_name!r}")

    cols = [str(c).strip() for c in df.iloc[header_idx]]
    verdict_col = cols.index("Verdict") if "Verdict" in cols else None
    out: dict[str, str] = {}
    for _, row in df.iloc[header_idx + 1 :].iterrows():
        name = row.iloc[0]
        if pd.isna(name) or not str(name).strip():
            continue
        field = str(name).strip()
        # Skip section divider rows like "— Identity & profile —".
        if field.startswith("—") or field.startswith("-"):
            continue
        verdict = (
            str(row.iloc[verdict_col]).strip()
            if verdict_col is not None and pd.notna(row.iloc[verdict_col])
            else ""
        )
        out[field] = verdict
    return out


def deal_table_fields() -> dict[str, str]:
    return _fields_from_sheet("Deal Table")


def merchant_gold_fields() -> dict[str, str]:
    return _fields_from_sheet("Merchant Gold Table")
