"""Spark column expressions for the S0 data-quality rules.

Native Spark expressions (no Python UDFs — best practice). Semantics mirror
dq/predicates.py exactly. pyspark is imported lazily so this module is importable in
tier-1 environments without Spark, while functions only run on Databricks.
"""


from ..constants import DATE_SANITY_GAP_DAYS


def _F():
    from pyspark.sql import functions as F

    return F


def missing_implausible_zero(col: str):
    """Column<bool>: True where value is null or 0 (0 implausible)."""
    F = _F()
    c = F.col(col)
    return c.isNull() | (c == 0)


def date_sanity_flag(funded_col: str, created_col: str, gap_days: int = DATE_SANITY_GAP_DAYS):
    """Column<bool>: True (contradiction) where funded/created differ by more than
    gap_days in EITHER direction (C-007). Mirrors dq.predicates.date_sanity_flag."""
    F = _F()
    f = F.to_date(F.col(funded_col))
    c = F.to_date(F.col(created_col))
    return f.isNotNull() & c.isNotNull() & (F.abs(F.datediff(f, c)) > gap_days)


def rtr_check_delta(funded_col: str, factor_col: str, payback_col: str):
    """Column<double>: |funded*factor - payback|."""
    F = _F()
    return F.abs(F.col(funded_col) * F.col(factor_col) - F.col(payback_col))


def rtr_check_flag(funded_col: str, factor_col: str, payback_col: str, tol: float):
    """Column<bool>: True where RTR delta exceeds tolerance."""
    return rtr_check_delta(funded_col, factor_col, payback_col) > tol
