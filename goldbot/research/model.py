"""Meta-labelling model: P(target hit) for a specialist's candidates. LightGBM, isotonic-calibrated on
out-of-fold predictions. The interface takes any estimator with fit/predict_proba, so other model
types can be trialled through the same walk-forward later."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None

DEFAULT_PARAMS = dict(
    objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=40, feature_fraction=0.7,
    bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, n_estimators=300, verbose=-1,
)


@dataclass
class MetaLabelModel:
    feature_names: list[str]
    params: dict = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    feature_version: str = ""
    model: object = None
    calibrator: IsotonicRegression | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series, w: pd.Series | None = None) -> "MetaLabelModel":
        if len(self.feature_names) > 40:
            raise ValueError("live models are capped at 40 features (design: Features and labels)")
        if lgb is None:
            raise RuntimeError("lightgbm not installed")
        self.model = lgb.LGBMClassifier(**self.params)
        self.model.fit(X[self.feature_names], y, sample_weight=None if w is None else w.values)
        return self

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X[self.feature_names])[:, 1]

    def calibrate(self, oof_pred: np.ndarray, oof_y: np.ndarray) -> "MetaLabelModel":
        self.calibrator = IsotonicRegression(out_of_bounds="clip").fit(oof_pred, oof_y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        p = self.predict_raw(X)
        return self.calibrator.predict(p) if self.calibrator is not None else p

    def importance(self) -> pd.Series:
        return pd.Series(self.model.booster_.feature_importance("gain"), index=self.feature_names).sort_values(ascending=False)


def shuffle_test_auc(X: pd.DataFrame, y: pd.Series, feature_names: list[str], seed: int = 0) -> float:
    """Leakage check: train on labels shifted by one row; AUC must be near 0.5."""
    from sklearn.metrics import roc_auc_score
    y_shift = y.shift(1).bfill().astype(int)
    n = len(X)
    cut = int(n * 0.7)
    m = MetaLabelModel(feature_names).fit(X.iloc[:cut], y_shift.iloc[:cut])
    return float(roc_auc_score(y_shift.iloc[cut:], m.predict_raw(X.iloc[cut:])))
