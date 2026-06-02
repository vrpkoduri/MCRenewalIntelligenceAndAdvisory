"""The four real validation merchants as canonical fixtures (CLAUDE.md §8).

Spec-aligned SYNTHETIC values (real records land after G1). Shared by every test tier.
Values are at the deal/silver grain. `expected` captures the S3 reference outcome so the
same fixtures drive scenario tests from S0 onward.
"""

from datetime import date, datetime

# Fixed "today" the clock fixtures below were hand-computed against (S2 scenario tests).
# Pinning it keeps elapsed-payment counts deterministic regardless of the real run date.
CLOCK_RUN_DATE = date(2026, 5, 31)

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
    # Clock (Appendix A) at CLOCK_RUN_DATE. The critical case: paydown computes to 100%
    # but the default note makes closure closed_default — NEVER closed_clean (A.5b).
    "expected_clock": {
        "rtr": 37250.0,
        "elapsed_payments": 100,  # capped at term
        "amount_paid": 37250.0,
        "est_current_balance": 0.0,
        "est_paydown_pct": 1.0,
        "has_default_note": True,
        "closure_status": "closed_default",
        "est_renewal_eligible_date": date(2023, 8, 17),
    },
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
    # Paid 100% with no default note -> closed_clean. As the only position, the merchant
    # has 0 active positions (dormant/win-back is the S3 lifecycle call, not S2).
    "expected_clock": {
        "rtr": 56000.0,
        "elapsed_payments": 100,
        "amount_paid": 56000.0,
        "est_current_balance": 0.0,
        "est_paydown_pct": 1.0,
        "has_default_note": False,
        "closure_status": "closed_clean",
        "est_renewal_eligible_date": date(2020, 5, 27),
    },
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
    # 1 fresh deal, ~6 weeks in: active, low paydown, clock running.
    "expected_clock": {
        "rtr": 19500.0,
        "elapsed_payments": 32,  # business days (2026-04-15, 2026-05-31]
        "amount_paid": 6240.0,
        "est_current_balance": 13260.0,
        "est_paydown_pct": 0.32,
        "has_default_note": False,
        "closure_status": "active",
        "est_renewal_eligible_date": date(2026, 7, 1),  # 55th business day after funding
        "is_eligible_now": False,
    },
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
    # Renewed ~14 calendar days (20 business days) into the new position: active, low
    # paydown — the serial/rapid-reup signal is the S3 call, not S2.
    "expected_clock": {
        "rtr": 58000.0,
        "elapsed_payments": 20,  # business days (2026-05-01, 2026-05-31]
        "amount_paid": 11600.0,
        "est_current_balance": 46400.0,
        "est_paydown_pct": 0.2,
        "has_default_note": False,
        "closure_status": "active",
        "est_renewal_eligible_date": date(2026, 7, 20),
        "is_eligible_now": False,
    },
}

ALL_MERCHANTS = [
    STARR_WINDOW_TINTING,
    ONE_BIG_PROMOTION,
    TOM_SNELL,
    WOLF_CORPORATION,
]
