"""Merchant profile assembly — D-503 (S5).

Builds the routing engine's input profile (the `mca_funders.gold.v_funder_input` shape) from
MRI gold, so the EXISTING engine can evaluate a funded merchant against the funder boxes.
Populates the fields MRI actually has; leaves the rest ABSENT (tracked in `missing_fields`)
so the engine's own missing-data handling produces honest case-by-case verdicts — MRI never
fabricates a funder input (CLAUDE.md §2.5: 0/blank is missing, not a value).

v1 MRI can supply: governing/incorporation state, FICO, time-in-business, current position
count, industry. It CANNOT supply revenue / deposits / NSF / bankruptcy / liens (no bank feed
— FU-301). Pure — no Spark, no I/O.
"""

from __future__ import annotations

from datetime import date, datetime

# The v_funder_input fields MRI cannot populate in v1 (no bank/credit feed) — always missing,
# so the engine flags them. Documented here so the gap is explicit, not silent.
_UNAVAILABLE_V1 = (
    "monthly_revenue", "annual_revenue", "avg_daily_balance", "avg_monthly_deposits",
    "deposit_count_per_month", "nsf_per_month", "negative_days_per_month", "max_holdback_pct",
    "has_open_bankruptcy", "has_tax_lien", "has_judgment", "datamerch_default_flag",
)


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def tib_months(business_start_date, months_in_business, run_date) -> int | None:
    """Time-in-business in months: prefer business_start_date (months to run_date), else the
    declared months_in_business (0/None → missing). None when neither is available."""
    start = _as_date(business_start_date)
    rd = _as_date(run_date)
    if start is not None and rd is not None and rd >= start:
        return (rd.year - start.year) * 12 + (rd.month - start.month)
    if months_in_business and int(months_in_business) > 0:
        return int(months_in_business)
    return None


def build_funder_profile(merchant: dict, run_date) -> tuple[dict, list[str]]:
    """MRI gold → (profile dict in v_funder_input shape, sorted missing_fields list).

    `merchant` carries the joined MRI fields: merchant_id, azure_merchant_id, governing_state,
    state_of_incorporation, fico, business_start_date, months_in_business, active_position_cnt,
    industry. Populated fields use real values; everything MRI lacks is omitted + reported.
    """
    tib = tib_months(
        merchant.get("business_start_date"), merchant.get("months_in_business"), run_date
    )
    fico = merchant.get("fico")
    fico = int(fico) if fico not in (None, 0, "0", "") else None

    profile = {
        "merchant_id": merchant.get("merchant_id"),
        "azure_merchant_id": merchant.get("azure_merchant_id"),
        "business_state": merchant.get("governing_state"),
        "state_of_incorporation": merchant.get("state_of_incorporation") or merchant.get("governing_state"),
        "industry_code": merchant.get("industry"),
        "tib_months": tib,
        "fico": fico,
        "max_existing_positions_observed": merchant.get("active_position_cnt"),
    }

    missing = [k for k, v in profile.items() if v is None and k not in ("merchant_id",)]
    missing += list(_UNAVAILABLE_V1)  # never available in v1 (no bank/credit feed)
    return profile, sorted(set(missing))
