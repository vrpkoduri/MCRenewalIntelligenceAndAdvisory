"""Rung waterfall — Appendix B.3 / Framework 4.2-4.6 (S3).

For ACTIVE merchants only (the Step-0 gate routes Defaulted/Dormant/New first). The
engine is a first-match-wins waterfall with a stress override that can ALWAYS pull a
merchant down (Framework 4.7): evaluate Distressed -> Serial -> Disciplined -> Growth ->
Graduate; any active stress signal forces Distressed regardless of other attributes.

Each rung predicate is the literal Appendix B.3 condition (pure, independently testable);
`rung_of` composes them in spec order so a Serial-looking merchant with a stress signal
lands Distressed, and a merchant matching no rung is Unclassified (rung = None).

Numeric thresholds come from `constants.Thresholds` (the single calibration home, Rule 3)
— no duplicate numbers here. v1 signals are Salesforce + the S2 clock; feed/statement
signals (NSF, true revenue, parsed concurrent positions) are deferred (D-301, FU-301), so
the burden-driven Distressed and the burden-falling Growth tests simply don't trip while
`burden_ratio` is null book-wide — honest, not forced.

rapid_reup_flag (D-302) is OWNED here (nothing computes it upstream). Pure functions —
no Spark, no I/O; the paydown-at-a-date math REUSES the S2 clock (never reimplemented).
"""

from __future__ import annotations

from datetime import date, datetime

from common import constants as C
from common.clock import amount_paid, elapsed_payments, est_paydown_pct, rtr

_T = C.Thresholds
_PAYDOWN_MIN = _T.DISCIPLINED_RENEWAL_PAYDOWN_MIN  # 0.50 — also the rapid-reup "took early" line
_BURDEN_CEILING = _T.BURDEN_DISTRESS_CEILING  # 0.30
_SERIAL_MIN = _T.SERIAL_POSITION_MIN  # 2


# --- rapid_reup_flag (D-302) -----------------------------------------------------


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def prior_paydown_at(prior: dict, as_of_date, holidays=None) -> float | None:
    """The prior position's est_paydown_pct evaluated at `as_of_date` (D-302 primary
    signal), recomputed from the prior's STATIC terms via the S2 clock — never SF stored.

    Returns None when the prior's terms are insufficient to compute (the caller then
    falls back to the day-gap rule).
    """
    elapsed = elapsed_payments(
        prior.get("funded_date"),
        as_of_date,
        prior.get("payment_frequency"),
        prior.get("num_payments"),
        holidays,
    )
    if elapsed is None:
        return None
    rtr_value = rtr(prior.get("funded_amount"), prior.get("factor_rate"))
    paid = amount_paid(prior.get("payment_amount"), elapsed)
    return est_paydown_pct(paid, rtr_value)


def _sorted_by_funded(deals):
    return sorted(deals, key=lambda d: (_as_date(d.get("funded_date")) or date.min))


def rapid_reup_flag(deals, holidays=None) -> bool:
    """rapid_reup_flag (D-302) — the PRIMARY Serial signal. TRUE when, for any
    consecutive same-merchant pair (ordered by funded_date), a new advance funds while
    the prior is still active AND the prior's paydown-at-the-new-funding < 50%.

    Paydown-based is PRIMARY (more honest than raw elapsed days). The day-gap test
    (consecutive funded_dates <= RAPID_REUP_MAX_GAP_DAYS) is the FALLBACK, used only when
    the prior's paydown can't be computed (missing terms). `< _PAYDOWN_MIN` (0.50) implies
    the prior was not yet paid off, so it captures "still active AND took money early".

    `deals` = the merchant's position dicts (funded_date + static terms). < 2 -> False.
    """
    ordered = _sorted_by_funded(deals)
    for prior, new in zip(ordered, ordered[1:]):
        new_funded = _as_date(new.get("funded_date"))
        if new_funded is None:
            continue
        paydown = prior_paydown_at(prior, new_funded, holidays)
        if paydown is not None:
            if float(paydown) < _PAYDOWN_MIN:
                return True
            continue  # prior was healthily paid down before the re-up — not rapid
        # fallback (prior paydown uncomputable): raw day gap
        prior_funded = _as_date(prior.get("funded_date"))
        if prior_funded is not None and (new_funded - prior_funded).days <= C.RAPID_REUP_MAX_GAP_DAYS:
            return True
    return False


def worsening_factor(deals) -> bool:
    """The most recent advance's factor_rate is higher than the prior advance's (B.3 /
    4.2 — "the cleanest distress tell computable without payment feeds"). < 2 deals with
    comparable factors -> False."""
    ordered = _sorted_by_funded(deals)
    if len(ordered) < 2:
        return False
    prior_f = ordered[-2].get("factor_rate")
    new_f = ordered[-1].get("factor_rate")
    if prior_f is None or new_f is None:
        return False
    return float(new_f) > float(prior_f)


def rapid_reup_into_worse_terms(deals, holidays=None) -> bool:
    """Distressed sub-condition (B.3): a rapid re-up taken into a worse factor rate."""
    return rapid_reup_flag(deals, holidays) and worsening_factor(deals)


# --- rung predicates (Appendix B.3) ----------------------------------------------


def is_distressed(signals: dict) -> bool:
    """Rung 1 — Distressed (B.3, OR / the stress override). Trips on ANY one strong
    signal (the cost of funding a distressed merchant into default is high):
      - a stress event (default note; NSF deferred to a feed — D-301/FU-301), OR
      - burden_ratio > the distress ceiling (~30%) [only when burden is known], OR
      - worsening factor AND shrinking net across recent deals, OR
      - a rapid re-up into worse terms.
    """
    if signals.get("has_default_note", False):
        return True
    burden = signals.get("burden_ratio")
    if burden is not None and float(burden) > _BURDEN_CEILING:
        return True
    if signals.get("worsening_factor", False) and signals.get("shrinking_net", False):
        return True
    if signals.get("rapid_reup_into_worse_terms", False):
        return True
    return False


def is_serial(signals: dict) -> bool:
    """Rung 2 — Serial / Multi-position (B.3): rapid_reup_flag (PRIMARY) OR Position-field
    >= 2 OR active concurrent positions >= 2 — AND not Distressed. Payment health + burden
    on the same multi-position shape is the discriminator vs Distressed (Wolf -> Serial)."""
    if is_distressed(signals):
        return False
    if signals.get("rapid_reup_flag", False):
        return True
    posfield = signals.get("disclosed_positions_cnt")
    if posfield is not None and int(posfield) >= _SERIAL_MIN:
        return True
    active = signals.get("active_position_cnt")
    if active is not None and int(active) >= _SERIAL_MIN:
        return True
    return False


def has_prior_clean_renewal(signals: dict) -> bool:
    """>= 1 prior clean renewal (B.3 Disciplined input), per D-303. A linked prior that
    reached `closed_clean` counts. Where the renewal chain is INCOMPLETE (the 503
    unlinkable, FU-302), we trust `Type=Renewal` as a true renewal (CLAUDE.md 2.5) and
    lean on the merchant's current clean signals rather than denying — a linking gap
    NEVER demotes a merchant."""
    if (signals.get("prior_clean_renewal_count", 0) or 0) >= 1:
        return True
    return (
        signals.get("has_renewal", False)
        and signals.get("renewal_chain_incomplete", False)
        and signals.get("clean_payments", False)
    )


def is_disciplined(signals: dict) -> bool:
    """Rung 3 — Disciplined Renewer (B.3, AND — discipline requires ALL the good signals):
    single position AND healthy-paydown renewal (>= 50%) AND clean payments AND >= 1 prior
    clean renewal. Unknown active count or unknown paydown -> not assertable -> False."""
    active = signals.get("active_position_cnt")
    single_position = active is not None and int(active) <= 1
    paydown = signals.get("est_paydown_pct")
    healthy_paydown = paydown is not None and float(paydown) >= _PAYDOWN_MIN
    clean = signals.get("clean_payments", False)
    return single_position and healthy_paydown and clean and has_prior_clean_renewal(signals)


def is_growth(signals: dict) -> bool:
    """Rung 4 — Growth Borrower (B.3, AND): Disciplined-or-better AND advance rising while
    RELATIVE burden falls (the defining tell — bigger advances, lighter relative load).
    Burden is null book-wide in v1 (no feed) so this does not trip — honest."""
    if not is_disciplined(signals):
        return False
    return signals.get("advance_rising", False) and signals.get("relative_burden_falling", False)


def is_graduate(signals: dict) -> bool:
    """Rung 5 — Graduate (B.3): Growth-or-better AND qualifies for cheaper products.
    Qualification indicators are not available in v1 -> does not trip."""
    if not is_growth(signals):
        return False
    return signals.get("graduate_qualified", False)


def rung_of(signals: dict):
    """Apply the waterfall in spec order (first match wins; stress override pulls down).
    Returns an int rung 1..5, or None for Unclassified (active but no rung matched —
    key signals missing; the caller records `missing_signals`)."""
    if is_distressed(signals):
        return C.RungState.DISTRESSED
    if is_serial(signals):
        return C.RungState.SERIAL
    if is_disciplined(signals):
        if is_growth(signals):
            return C.RungState.GRADUATE if is_graduate(signals) else C.RungState.GROWTH
        return C.RungState.DISCIPLINED
    return None
