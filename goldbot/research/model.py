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
from scipy.special import expit
from sklearn.isotonic import IsotonicRegression

from goldbot.base import Record
from goldbot.features.registry import side_align
from goldbot.research.drift import reference_bins
from goldbot.research.metrics import calibration_ece

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


class RecalLayer(Record):
    """One bounded recalibration: p' = sigmoid(coef x logit(p) + intercept), clipped to p +- max_shift."""
    coef: float
    intercept: float
    max_shift: float

    def apply(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        q = 1 / (1 + np.exp(-(self.coef * PlattCalibrator._logit(p) + self.intercept)))
        return np.asarray(np.clip(q, p - self.max_shift, p + self.max_shift))


class RecalibratedCalibrator:
    """The calibrator a model was validated with (`base`; None = identity) followed by one recalibration layer.
    Each weekly run replaces the layer, fitted again from the validated map: layers are never stacked, because the
    outcome windows of consecutive runs overlap and stacked layers would refit the same rows, compounding past both
    the prior's shrinkage and the per-run cap. So p never moves more than `max_shift` from the validated map."""

    def __init__(self, base: Any, layers: list[RecalLayer]) -> None:
        self.base, self.layers = base, list(layers)

    @staticmethod
    def validated(current: Any) -> Any:
        """The calibrator the model was validated with, under any recalibration layer."""
        return current.base if isinstance(current, RecalibratedCalibrator) else current

    @classmethod
    def replacing(cls, current: Any, layer: RecalLayer) -> "RecalibratedCalibrator":
        return cls(cls.validated(current), [layer])

    def predict(self, p_raw: np.ndarray) -> np.ndarray:
        p = np.asarray(self.base.predict(p_raw) if self.base is not None else p_raw, dtype=float)
        for layer in self.layers:
            p = layer.apply(p)
        return p


class RecalibrationFit(Record):
    n: int
    coef: float
    intercept: float
    prior_weight: float
    max_shift: float
    ece_before: float
    ece_after: float                 # in-sample (the same outcomes the layer was fitted on)
    max_abs_shift: float             # largest |p' - p| over the sample
    mean_shift: float

    def layer(self) -> RecalLayer:
        return RecalLayer(coef=self.coef, intercept=self.intercept, max_shift=self.max_shift)


def fit_recalibration(p: np.ndarray, y: np.ndarray, *, prior_weight: float, max_shift: float,
                      min_samples: int) -> RecalibrationFit | None:
    """Platt map on logit(p) fitted to recent outcomes and shrunk toward the identity (the current calibration) by a
    prior worth `prior_weight` trades: pseudo-outcomes equal to the current p, so the fit uses the soft target
    (n_obs x y + prior_weight x p) / (n_obs + prior_weight) per row. 20 outcomes against a prior of 200 move the map
    a little, 2,000 move it most of the way. The result is capped at +-max_shift per probability. None below
    `min_samples` outcomes (no update)."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    n = len(p)
    ridge = 1.0                                      # a weak pull to the identity keeps near-separable samples finite
    if n < max(min_samples, 1):
        return None
    t = (n * y + prior_weight * p) / (n + prior_weight)
    X = np.column_stack([np.ones(n), PlattCalibrator._logit(p)])
    beta = np.array([0.0, 1.0])                      # start at the prior's mode: the identity map
    for _ in range(100):                             # Newton on the convex soft-label log-loss
        q = expit(X @ beta)
        g = X.T @ (q - t) + ridge * (beta - np.array([0.0, 1.0]))
        H = (X * (q * (1 - q))[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(H, g)
        beta = beta - step
        if np.abs(step).max() < 1e-10:
            break
    layer = RecalLayer(coef=float(beta[1]), intercept=float(beta[0]), max_shift=max_shift)
    p_new = layer.apply(p)
    return RecalibrationFit(n=n, coef=layer.coef, intercept=layer.intercept, prior_weight=prior_weight,
                            max_shift=max_shift, ece_before=calibration_ece(p, y), ece_after=calibration_ece(p_new, y),
                            max_abs_shift=float(np.abs(p_new - p).max()), mean_shift=float((p_new - p).mean()))


def fit_calibrator(p_raw: np.ndarray, y: np.ndarray) -> IsotonicRegression | PlattCalibrator:
    """Isotonic with at least MIN_ISOTONIC_ROWS rows, Platt scaling below (Niculescu-Mizil & Caruana 2005)."""
    if len(p_raw) >= MIN_ISOTONIC_ROWS:
        return IsotonicRegression(out_of_bounds="clip").fit(p_raw, y)
    return PlattCalibrator().fit(p_raw, y)


DEFAULT_PARAMS = dict(
    objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=40, feature_fraction=0.7,
    bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, n_estimators=300, verbose=-1,
)


# LightGBM threads for fit and predict unless `params` names n_jobs. The training sets are a few hundred to a few
# thousand candidates, where OpenMP's per-iteration thread start-up and spin-waiting cost far more than they save,
# catastrophically so on a busy machine (measured on 300 rows x 40 features with the CPU shared: 40 s with all cores,
# 0.09 s with one). One thread also fixes the histogram summation order, so the trees are reproducible bit for bit
# (multi-threaded row-wise histograms may sum in a different order); predictions were identical in every test.
FIT_THREADS = 1


class MetaLabelModel(Record):
    feature_names: list[str]
    params: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_PARAMS))
    feature_version: str = ""
    model: Any = None   # fitted LGBMClassifier
    calibrator: Any = None           # IsotonicRegression | PlattCalibrator once calibrate() has run
    feature_ref: dict[str, Any] = Field(default_factory=dict)   # training distribution per input (drift PSI)

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
        self.model = lgb.LGBMClassifier(**{"n_jobs": FIT_THREADS, **self.params})
        Z = self.design(X)
        self.model.fit(Z, y, sample_weight=None if w is None else w.to_numpy())
        self.feature_ref = reference_bins(Z)           # what "normal" looks like for the daily drift check
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
