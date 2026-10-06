"""Meta-labelling model: P(target hit) for a specialist's candidates. LightGBM, isotonic-calibrated on
out-of-fold predictions. The interface takes any estimator with fit/predict_proba, so other model
types can be trialled through the same walk-forward later."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import Field
from sklearn.isotonic import IsotonicRegression

from goldbot.base import Record

try:
    import lightgbm as lgb
except ImportError:  # pragma: no cover
    lgb = None  # type: ignore[assignment]

DEFAULT_PARAMS = dict(
    objective="binary", learning_rate=0.03, num_leaves=15, min_child_samples=40, feature_fraction=0.7,
    bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, n_estimators=300, verbose=-1,
)


class MetaLabelModel(Record):
    feature_names: list[str]
    params: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_PARAMS))
    feature_version: str = ""
    model: Any = None   # fitted LGBMClassifier
    calibrator: IsotonicRegression | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series, w: pd.Series | None = None) -> "MetaLabelModel":
        if len(self.feature_names) > 40:
            raise ValueError("live models are capped at 40 features (design: Features and labels)")
        if lgb is None:
            raise RuntimeError("lightgbm not installed")
        self.model = lgb.LGBMClassifier(**self.params)
        self.model.fit(X[self.feature_names], y, sample_weight=None if w is None else w.to_numpy())
        return self

    def predict_raw(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X[self.feature_names])[:, 1]

    def calibrate(self, oof_pred: np.ndarray, oof_y: np.ndarray) -> "MetaLabelModel":
        self.calibrator = IsotonicRegression(out_of_bounds="clip").fit(oof_pred, oof_y)
        return self

    def calibrated(self, p_raw: np.ndarray) -> np.ndarray:
        """Map raw scores through the isotonic calibrator (identity until calibrate() has run)."""
        return self.calibrator.predict(p_raw) if self.calibrator is not None else p_raw

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.calibrated(self.predict_raw(X))

    def importance(self) -> pd.Series:
        return pd.Series(self.model.booster_.feature_importance("gain"), index=self.feature_names).sort_values(ascending=False)
