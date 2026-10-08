"""Meta-labelling model: P(target hit) for a specialist's candidates. LightGBM, calibrated on out-of-fold
predictions (isotonic with enough rows, Platt scaling below that). The interface takes any estimator with
fit/predict_proba, so other model types can be trialled through the same walk-forward later.

A model whose features include `side` is side-aligned: signed features are fed as side x (value - neutral)
(goldbot.features.registry.side_align), in fit and predict alike, so callers pass raw feature frames plus `side`."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field
from sklearn.isotonic import IsotonicRegression

from goldbot.base import Record
from goldbot.features.registry import side_align

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None  # type: ignore[assignment]

MAX_FEATURES = 40                 # design: at most 40 features per live model (`side` included)
MIN_ISOTONIC_ROWS = 500           # isotonic regression overfits small calibration sets; Platt scaling below this


class PlattCalibrator:
    """Logistic map on the logit of the raw score (Platt 1999): two parameters, so it is stable on a few hundred rows."""

    def __init__(self) -> None:
        self.coef, self.intercept = 1.0, 0.0

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        q = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
        return np.log(q / (1 - q))

    def fit(self, p_raw: np.ndarray, y: np.ndarray) -> "PlattCalibrator":
        from sklearn.linear_model import LogisticRegression
        y = np.asarray(y, dtype=int)
        if len(np.unique(y)) < 2:              # one class only: a constant at its frequency
            self.coef, self.intercept = 0.0, float(self._logit(np.array([y.mean() if len(y) else 0.5]))[0])
            return self
        lr = LogisticRegression(C=1e6).fit(self._logit(p_raw).reshape(-1, 1), y)
        self.coef, self.intercept = float(lr.coef_[0, 0]), float(lr.intercept_[0])
        return self

    def predict(self, p_raw: np.ndarray) -> np.ndarray:
        return np.asarray(1 / (1 + np.exp(-(self.coef * self._logit(p_raw) + self.intercept))))


def fit_calibrator(p_raw: np.ndarray, y: np.ndarray) -> IsotonicRegression | PlattCalibrator:
    """Isotonic with at least MIN_ISOTONIC_ROWS rows, Platt scaling below (Niculescu-Mizil & Caruana 2005)."""
    if len(p_raw) >= MIN_ISOTONIC_ROWS:
        return IsotonicRegression(out_of_bounds="clip").fit(p_raw, y)
    return PlattCalibrator().fit(p_raw, y)


DEFAULT_PARAMS = dict(
    objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=40, feature_fraction=0.7,
    bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, n_estimators=300, verbose=-1,
)


class MetaLabelModel(Record):
    feature_names: list[str]
    params: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_PARAMS))
    feature_version: str = ""
    model: Any = None   # fitted LGBMClassifier
    calibrator: Any = None           # IsotonicRegression | PlattCalibrator once calibrate() has run

    @property
    def side_aligned(self) -> bool:
        return "side" in self.feature_names

    def design(self, X: pd.DataFrame) -> pd.DataFrame:
        """The model's input matrix: its feature columns, side-aligned when the model carries `side`."""
        Z = X[self.feature_names]
        return side_align(Z) if self.side_aligned else Z

    def fit(self, X: pd.DataFrame, y: pd.Series, w: pd.Series | None = None) -> "MetaLabelModel":
        if len(self.feature_names) > MAX_FEATURES:
            raise ValueError(f"live models are capped at {MAX_FEATURES} features (design: Features and labels)")
        if lgb is None:
            raise RuntimeError("lightgbm not installed")
        self.model = lgb.LGBMClassifier(**self.params)
        self.model.fit(self.design(X), y, sample_weight=None if w is None else w.to_numpy())
        return self

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self.design(X))[:, 1]

    def calibrate(self, oof_pred: np.ndarray, oof_y: np.ndarray) -> "MetaLabelModel":
        self.calibrator = fit_calibrator(oof_pred, oof_y)
        return self

    def calibrated(self, p_raw: np.ndarray) -> np.ndarray:
        """Map raw scores through the calibrator (identity until calibrate() has run)."""
        return self.calibrator.predict(p_raw) if self.calibrator is not None else p_raw

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.calibrated(self.predict_raw(X))

    def importance(self) -> pd.Series:
        return pd.Series(self.model.booster_.feature_importance("gain"), index=self.feature_names).sort_values(ascending=False)
