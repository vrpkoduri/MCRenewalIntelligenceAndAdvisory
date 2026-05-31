"""The four real validation merchants as canonical fixtures (CLAUDE.md §8).

Spec-aligned SYNTHETIC values (real records land after G1). Shared by every test tier.
Values are at the deal/silver grain. `expected` captures the S3 reference outcome so the
same fixtures drive scenario tests from S0 onward.
"""

from datetime import date, datetime

# Each entry mirrors mca_mri.silver.deals static-term columns we ingest in S0.
STARR_WINDOW_TINTING = {
    "opportunity_id": "OPP-STARR-001",
    "merchant_sf_id": "ACC-STARR",
    "opportunity_name": "Starr Window Tinting",
    "stage": "Funded",
    "deal_type": "Renewal",
    "funder": "Funder A",
    "funded_amount": 25000.0,
    "factor_rate": 1.49,
    "payback_amount": 37250.0,  # 25000 * 1.49 — RTR consistent
    "payment_amount": 372.5,
    "num_payments": 100,
    "payment_frequency": "Daily",
    "funded_date": date(2023, 6, 1),
    "created_date": datetime(2023, 5, 20, 9, 0, 0),
    "months_in_business": 56,
    "business_start_date": date(2018, 10, 1),
    "fico": 520,
    "position_at_funding": 4,
    "notes": "Defaulted — $250 clawback",
    "expected": {"lifecycle_state": "defaulted", "outcome": "do-not-fund + review"},
}

ONE_BIG_PROMOTION = {
    "opportunity_id": "OPP-OBP-001",
    "merchant_sf_id": "ACC-OBP",
    "opportunity_name": "One Big Promotion",
    "stage": "Funded",
    "deal_type": "New Business",
    "funder": "Funder B",
    "funded_amount": 40000.0,
    "factor_rate": 1.40,
    "payback_amount": 56000.0,
    "payment_amount": 560.0,
    "num_payments": 100,
    "payment_frequency": "Daily",
    "funded_date": date(2020, 3, 10),
    "created_date": datetime(2020, 3, 1, 9, 0, 0),
    "months_in_business": 0,  # 0 => MISSING (exercises DQ rule 1)
    "business_start_date": None,
    "fico": 0,  # 0 => MISSING
    "position_at_funding": 1,
    "notes": "Paid in full",
    "expected": {"lifecycle_state": "dormant", "outcome": "win-back"},
}

TOM_SNELL = {
    "opportunity_id": "OPP-SNELL-001",
    "merchant_sf_id": "ACC-SNELL",
    "opportunity_name": "Tom Snell",
    "stage": "Funded",
    "deal_type": "New Business",
    "funder": "Funder C",
    "funded_amount": 15000.0,
    "factor_rate": 1.30,
    "payback_amount": 19500.0,
    "payment_amount": 195.0,
    "num_payments": 100,
    "payment_frequency": "Daily",
    "funded_date": date(2026, 4, 15),
    "created_date": datetime(2026, 4, 10, 9, 0, 0),
    "months_in_business": 42,
    "business_start_date": date(2022, 9, 1),
    "fico": 690,
    "position_at_funding": 1,
    "notes": "Clean, full docs",
    "expected": {"lifecycle_state": "new-establishing", "outcome": "healthy clock-running"},
}

WOLF_CORPORATION = {
    "opportunity_id": "OPP-WOLF-002",
    "merchant_sf_id": "ACC-WOLF",
    "opportunity_name": "Wolf Corporation",
    "stage": "Funded",
    "deal_type": "Renewal",
    "funder": "Funder D",
    "funded_amount": 40000.0,  # upsized from 30k
    "factor_rate": 1.45,
    "payback_amount": 58000.0,
    "payment_amount": 580.0,
    "num_payments": 100,
    "payment_frequency": "Daily",
    "funded_date": date(2026, 5, 1),
    "created_date": datetime(2026, 4, 28, 9, 0, 0),
    "months_in_business": 75,
    "business_start_date": date(2019, 12, 1),
    "fico": 640,
    "position_at_funding": 2,
    "notes": "Renewed ~14 days into prior position; 30k -> 40k",
    "expected": {"lifecycle_state": "active", "outcome": "serial -> renewal-vs-buyout eval"},
}

ALL_MERCHANTS = [
    STARR_WINDOW_TINTING,
    ONE_BIG_PROMOTION,
    TOM_SNELL,
    WOLF_CORPORATION,
]
