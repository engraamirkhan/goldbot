"""End-to-end research pipeline for one specialist on one timeframe:
bars -> features (+ higher-TF context) -> candidates -> labels -> purged walk-forward -> OOF predictions
-> calibration -> metrics -> registry row. This is the code path Phase 1 runs; the live engine reuses the
same feature and model objects."""
from __future__ import annotations

import random
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import Record
from goldbot.data.resample import mid
from goldbot.features import FEATURES, build_features
from goldbot.features.mtf import merge_higher_tf
from goldbot.features.technical import atr
from goldbot.labels import one_at_a_time, triple_barrier, uniqueness_weights
from goldbot.research.metrics import summarize
from goldbot.research.model import MetaLabelModel
from goldbot.research.walkforward import splits_for
from goldbot.specialists.base import FEATURE_SEED_KEY, Specialist

DEFAULT_FEATURE_NAMES = [n for n in FEATURES if n not in ("macro", "calendar_events")]


class ResearchResult(Record):
    agent_id: str
    n_candidates: int
    n_folds: int
    oof: pd.DataFrame
    metrics: dict
    feature_version: str
    importance: pd.Series | None
    model: MetaLabelModel | None = None   # last fold's model, calibrated on all OOF predictions


def build_decision_frame(bars_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None = None,
                         feature_names: list[str] | None = None, ctx: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features on the decision timeframe plus higher-TF context merged without lookahead."""
    names = feature_names or DEFAULT_FEATURE_NAMES
    m = mid(bars_dec).reset_index(drop=True)
    X = build_features(m, names, ctx)
    version = X.attrs["feature_version"]
    for label, hbars in (context or {}).items():
        hm = mid(hbars).reset_index(drop=True)
        hf = build_features(hm, [n for n in ("atr", "trend_strength", "moving_averages", "realised_vol") if n in names], ctx)
        X = merge_higher_tf(X, hf, hbars.reset_index(drop=True), label)
    X.attrs["feature_version"] = version
    return m, X


MAX_FEATURES = 40       # design: at most 40 features per live model


def select_features(eligible: list[str], config: dict[str, Any]) -> list[str]:
    """The model's feature list: the first MAX_FEATURES eligible columns, or, for a clone carrying `feature_seed`, a
    seeded random subset of them (design: a child may differ from its parent by its feature subset)."""
    seed = config.get(FEATURE_SEED_KEY)
    if seed is None or len(eligible) <= MAX_FEATURES:
        return eligible[:MAX_FEATURES]
    rng = random.Random(int(seed))
    return sorted(rng.sample(eligible, MAX_FEATURES), key=eligible.index)


def run_specialist(spec: Specialist, bars_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None = None,
                   feature_names: list[str] | None = None, model_features: list[str] | None = None,
                   n_trials: int = 1, trades_per_year: float = 400.0, ctx: dict | None = None) -> ResearchResult:
    bars_dec = bars_dec.reset_index(drop=True)
    m, X = build_decision_frame(bars_dec, context, feature_names, ctx)
    cands = spec.candidates(m, X)
    a = atr(m, 14)
    labels = one_at_a_time(triple_barrier(bars_dec, cands, spec.label_spec, a))
    if labels.empty:
        return ResearchResult(agent_id=spec.agent_id, n_candidates=0, n_folds=0, oof=labels, metrics={"n": 0}, feature_version=X.attrs["feature_version"], importance=None)
    labels["weight"] = uniqueness_weights(labels, len(bars_dec)).to_numpy()
    feats = X.drop(columns=["ts_utc"]).iloc[labels["idx"].to_numpy()].reset_index(drop=True)
    feats = feats.replace([np.inf, -np.inf], np.nan)
    cols = model_features or select_features([c for c in feats.columns if feats[c].notna().mean() > 0.8], spec.config)
    y = labels["target_hit"].astype(int)
    folds = splits_for(labels, spec.timeframe)
    oof_pred = np.full(len(labels), np.nan)
    last_model = None
    for f in folds:
        mdl = MetaLabelModel(feature_names=cols, feature_version=X.attrs["feature_version"]).fit(
            feats.iloc[f.train_idx], y.iloc[f.train_idx], labels["weight"].iloc[f.train_idx])
        oof_pred[f.test_idx] = mdl.predict_raw(feats.iloc[f.test_idx])
        last_model = mdl
    oof = labels.copy()
    oof["p_raw"] = oof_pred
    scored = oof.dropna(subset=["p_raw"])
    metrics: dict[str, Any] = {"n_candidates": int(len(labels)), "n_folds": len(folds)}
    if len(scored) > 20 and last_model is not None:
        last_model.calibrate(scored["p_raw"].to_numpy(), scored["target_hit"].to_numpy())
        oof["p"] = np.where(np.isnan(oof_pred), np.nan, last_model.predict_raw(feats))
        oof.loc[scored.index.to_numpy(), "p"] = last_model.calibrated(scored["p_raw"].to_numpy())
        # baseline: take every candidate; model: take candidates with p > breakeven+margin
        from goldbot.research.metrics import breakeven_prob
        ls = spec.label_spec
        cost_atr = float(np.nanmedian(m["spread"] / a)) * 2 if len(m) else 0.1
        thr = breakeven_prob(ls.target_atr, ls.stop_atr, cost_atr) + 0.02
        taken = scored[last_model.calibrated(scored["p_raw"].to_numpy()) > thr]
        metrics.update({
            "threshold": float(thr),
            "all_candidates": summarize(scored, trades_per_year, n_trials),
            "model_filtered": summarize(taken, trades_per_year * len(taken) / max(len(scored), 1), n_trials),
            "shuffle_auc": None,
        })
    imp = last_model.importance() if last_model is not None else None
    return ResearchResult(agent_id=spec.agent_id, n_candidates=len(labels), n_folds=len(folds), oof=oof, metrics=metrics, feature_version=X.attrs["feature_version"], importance=imp, model=last_model if "threshold" in metrics else None)
