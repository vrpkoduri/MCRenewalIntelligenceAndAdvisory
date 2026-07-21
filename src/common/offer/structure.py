"""Renewal-vs-buyout structure decision — Framework §5.7 / D-506 (S5).

The book's sharpest honesty test: when a merchant comes back fast and hungry — renewing days
into a position, before meaningful paydown, asking for a larger advance — the transactional
reflex is to book the renewal. But renewing a barely-paid position rolls almost the entire
old balance into a new, larger advance at a fresh factor: the most expensive double-dip. The
honest response is "what structure is right for this merchant?" — often a consolidating buyout,
and sometimes a third answer, "wait and pay down first."

This module computes the structure deterministically from the S2 clock outputs (real numbers,
never invented — honesty constraint, CLAUDE.md §2.3): the double-dip cost of rolling the
current balance, and a recommendation among renewal / buyout / wait-and-pay-down. The math
decides; the merchant's interest is the tiebreaker; buyout is NOT assumed superior.

Pure — no Spark, no I/O (mirrors common/rung / common/clock). Reuses the disciplined-paydown
threshold (no duplicate number, Rule 3).
"""

from __future__ import annotations

from common import constants as C

_PAYDOWN_MIN = C.Thresholds.DISCIPLINED_RENEWAL_PAYDOWN_MIN  # 0.50 — the "barely paid" line
_NEAR_PAYOFF = C.Thresholds.NEAR_PAYOFF_PAYDOWN  # 0.90 — the "essentially complete" line (C-032)
_SERIAL_MIN = C.Thresholds.SERIAL_POSITION_MIN  # 2 — consolidation candidate


def double_dip_cost(est_current_balance, factor_rate) -> float | None:
    """The extra cost of RE-financing money the merchant already owes: rolling the current
    balance into a new advance pays the factor markup again on that balance.
    double_dip_cost = est_current_balance × (factor_rate − 1). None if inputs missing.

    Uses the merchant's OWN current balance + factor (real numbers) — an honest illustration
    of the rollover cost, not a projection of a hypothetical new deal.
    """
    if est_current_balance is None or factor_rate is None:
        return None
    markup = float(factor_rate) - 1.0
    if markup < 0:
        return None
    return float(est_current_balance) * markup


def recommend_structure(est_paydown_pct, active_position_cnt) -> str | None:
    """Recommend renewal / buyout / wait-and-pay-down (D-506; near-payoff refinement C-032/D-808).

    - paydown < 50% (barely paid) → WAIT_AND_PAYDOWN: rolling now is the expensive double-dip;
      the honest move is to wait until less rolls over (Wolf, renewing ~days in).
    - paydown ≥ 90% (essentially complete) → RENEWAL: the position is all but paid off, so a
      consolidating buyout is pointless (it would roll the factor again on a near-zero balance) —
      the honest structure is to finish/renew, NOT consolidate. This ceiling wins over the
      multi-position buyout rule (C-032 — the gold_test full-book run surfaced 2-position merchants
      99.9% paid being told to "consolidate $12", nonsensical advice).
    - else multiple concurrent positions → BUYOUT: consolidating into one cleaner facility is
      the kinder structure for a serial merchant mid-life with meaningful balances.
    - else (single position, healthy paydown) → RENEWAL.

    None when paydown is unknown (cannot decide a structure — never guess).
    """
    if est_paydown_pct is None:
        return None
    p = float(est_paydown_pct)
    if p < _PAYDOWN_MIN:
        return C.OfferStructure.WAIT_AND_PAYDOWN
    if p >= _NEAR_PAYOFF:
        return C.OfferStructure.RENEWAL  # essentially done → finish/renew, never consolidate
    if (active_position_cnt or 0) >= _SERIAL_MIN:
        return C.OfferStructure.BUYOUT
    return C.OfferStructure.RENEWAL


def structure_evaluation(signals: dict) -> dict:
    """Compose the structure decision for a merchant from clock signals. Keys consumed:
    est_paydown_pct, est_current_balance, factor_rate (current position), active_position_cnt.

    Returns {structure, double_dip_cost, rolled_balance} — the recommendation plus the honest
    rollover-cost figure the advisory layer surfaces.
    """
    rolled = signals.get("est_current_balance")
    return {
        "structure": recommend_structure(
            signals.get("est_paydown_pct"), signals.get("active_position_cnt")
        ),
        "double_dip_cost": double_dip_cost(rolled, signals.get("factor_rate")),
        "rolled_balance": rolled,
    }
