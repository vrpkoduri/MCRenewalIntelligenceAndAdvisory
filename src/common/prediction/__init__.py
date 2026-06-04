"""Prediction feature/label derivation — Build Plan §6 / Framework §11.2 (S6). The cheap,
deterministic base the ADOPTED models consume: RFM (BTYD), survival labels (Cox/KM), and the
confidence/insufficient-history logic. The models themselves (PyMC-Marketing BG/NBD +
Gamma-Gamma + CLV; lifelines Cox + KM) are fit on Databricks in transform/gold_predictions.py
(build gated, D-602) — MRI owns only feature/label derivation + orchestration, never a
hand-rolled model (CLAUDE.md §4). Reads S1-S4 gold; never recomputes the spine; distress stays
signal-driven (S3). Pure, Spark/ML-free at import (tier-1 testable).

  - rfm:        rfm_features (frequency / recency / T / monetary) — D-601
  - survival:   inter_advance_intervals / censored_duration / survival_rows (Cox censoring) — D-607
  - confidence: prediction_confidence + is_insufficient_history (posterior-width / history) — D-603
"""

from common.prediction.confidence import (
    confidence_from_history,
    confidence_from_posterior,
    is_insufficient_history,
    prediction_confidence,
)
from common.prediction.rfm import rfm_features
from common.prediction.survival import (
    censored_duration,
    inter_advance_intervals,
    survival_rows,
)

__all__ = [
    "rfm_features",
    "inter_advance_intervals",
    "censored_duration",
    "survival_rows",
    "is_insufficient_history",
    "confidence_from_history",
    "confidence_from_posterior",
    "prediction_confidence",
]
