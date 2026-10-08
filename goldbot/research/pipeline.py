"""End-to-end research pipeline for one specialist on one timeframe:
bars -> features (+ higher-TF context) -> candidates -> labels -> purged walk-forward -> OOF predictions
-> cross-fitted calibration -> metrics and the design's gates -> registry row. This is the code path Phase 1 runs; the live engine reuses the
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
from goldbot.research.gates import holdout_verdict, research_gates
from goldbot.research.metrics import expectancy, summarize
from goldbot.research.model import MAX_FEATURES, MetaLabelModel, fit_calibrator
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
    model: MetaLabelModel | None = None   # last fold's model, calibrated on all OOF predictions (for deployment)


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


def select_features(eligible: list[str], config: dict[str, Any], limit: int = MAX_FEATURES) -> list[str]:
    """The first `limit` eligible columns, or, for a clone carrying `feature_seed`, a seeded random subset of them
    (design: a child may differ from its parent by its feature subset)."""
    seed = config.get(FEATURE_SEED_KEY)
    if seed is None or len(eligible) <= limit:
        return eligible[:limit]
    rng = random.Random(int(seed))
    return sorted(rng.sample(eligible, limit), key=eligible.index)


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None


def lookahead_check(bars_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None, cut_frac: float = 0.7,
                    feature_names: list[str] | None = None) -> dict[str, Any]:
    """Leakage check on the data actually used: build the decision frame on the full history and on the history
    truncated at `cut_frac` (context bars only those visible by then). A feature whose value on a bar before the cut
    differs between the two used data from after the cut. Returns the offending columns (empty = clean)."""
    bars_dec = bars_dec.reset_index(drop=True)
    cut = int(len(bars_dec) * cut_frac)
    cut_ts = pd.Timestamp(bars_dec["visible_at"].iloc[cut - 1])
    _, full = build_decision_frame(bars_dec, context, feature_names)
    ctx_cut = {k: v[pd.to_datetime(v["visible_at"], utc=True) <= cut_ts] for k, v in (context or {}).items()}
    _, part = build_decision_frame(bars_dec.iloc[:cut], ctx_cut, feature_names)
    a = full.drop(columns=["ts_utc"]).iloc[:cut].reset_index(drop=True)
    b = part.drop(columns=["ts_utc"]).reset_index(drop=True)
    bad = []
    for c in a.columns:
        if c not in b.columns:
            bad.append(c)
            continue
        x, z = pd.to_numeric(a[c], errors="coerce").to_numpy(float), pd.to_numeric(b[c], errors="coerce").to_numpy(float)
        same = np.isclose(x, z, rtol=1e-6, atol=1e-9, equal_nan=True)
        if not same.all():
            bad.append(c)
    return {"cut_utc": cut_ts.isoformat(), "columns_checked": int(a.shape[1]), "lookahead_columns": bad}


MIN_CALIBRATION_ROWS = 50      # a test fold is calibrated only from at least this many earlier out-of-fold rows (Platt below 500)
THRESHOLD_MARGIN = 0.02        # take a candidate when calibrated p > break-even + this margin (design)
Window = tuple[pd.Timestamp, pd.Timestamp]   # [start, end) in UTC


def _zero_spread(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars with bid = ask = mid: the rule's gross outcome, before any cost."""
    out = bars.copy()
    for k in ("high", "low", "close"):
        midk = (bars[f"bid_{k}"].astype(float) + bars[f"ask_{k}"].astype(float)) / 2
        out[f"bid_{k}"] = out[f"ask_{k}"] = midk
    return out


def _risk_fraction(labels: pd.DataFrame, a: pd.Series, stop_atr: float) -> np.ndarray:
    """Stop distance as a fraction of the entry price, so ret / risk is the trade's result in R."""
    return stop_atr * a.to_numpy()[labels["idx"].to_numpy()] / labels["entry"].to_numpy(dtype=float)


def _before(labels: pd.DataFrame, start: pd.Timestamp) -> np.ndarray:
    """Labels that exited before `start` (their whole life precedes it)."""
    return np.asarray(pd.DatetimeIndex(pd.to_datetime(labels["ts_exit"], utc=True)) < start)


def _in_window(labels: pd.DataFrame, window: Window) -> np.ndarray:
    """Labels whose life [signal, exit] touches the window."""
    ts = pd.DatetimeIndex(pd.to_datetime(labels["ts_utc"], utc=True))
    te = pd.DatetimeIndex(pd.to_datetime(labels["ts_exit"], utc=True))
    return np.asarray((te >= window[0]) & (ts < window[1]))


def model_inputs(spec: Specialist, eligible: list[str]) -> list[str]:
    """The meta-model's columns: `side` plus the specialist's declared `model_features` (those present and eligible on
    this timeframe), or, for a clone carrying `feature_seed`, a seeded random subset of all eligible columns; a
    specialist without a declaration gets the first eligible columns. At most MAX_FEATURES in all."""
    pool = [c for c in eligible if c != "side"]
    if spec.config.get(FEATURE_SEED_KEY) is None and spec.model_features:
        ok = set(pool)
        cols = [c for c in spec.model_features if c in ok][: MAX_FEATURES - 1]
    else:
        cols = select_features(pool, spec.config, limit=MAX_FEATURES - 1)
    return ["side", *cols]


def _cross_fitted(p_raw: np.ndarray, y: np.ndarray, folds: list[Any]) -> np.ndarray:
    """Calibrated out-of-fold probabilities where fold k's map is fitted only on the out-of-fold predictions of the
    folds before it (all earlier in time), never on fold k itself. Folds without MIN_CALIBRATION_ROWS earlier rows stay
    NaN: their trades are scored for AUC but never selected."""
    p = np.full(len(p_raw), np.nan)
    seen: list[np.ndarray] = []
    for f in folds:
        prior = np.concatenate(seen) if seen else np.array([], dtype=int)
        if len(prior) >= MIN_CALIBRATION_ROWS:
            cal = fit_calibrator(p_raw[prior], y[prior])
            p[f.test_idx] = cal.predict(p_raw[f.test_idx])
        seen.append(np.asarray(f.test_idx))
    return p


def run_specialist(spec: Specialist, bars_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None = None,
                   feature_names: list[str] | None = None, model_features: list[str] | None = None,
                   n_trials: int = 1, trades_per_year: float | None = None, ctx: dict | None = None,
                   extra_cost_usd: float = 0.0, holdout: Window | None = None,
                   score_holdout: bool = False) -> ResearchResult:
    """Walk-forward one specialist configuration.

    extra_cost_usd: round-trip cost per oz beyond the bar spread (entry and exit slippage plus commission). Labels
    already pay the spread (entry at the ask, exit at the bid), so the spread is not charged again: the extra cost is
    taken off every label's return and enters the break-even probability per candidate (extra / ATR at the signal).

    Selection is cross-fitted: fold k's calibrator comes from earlier folds' out-of-fold predictions only, and the
    threshold uses only information at the signal, so `model_filtered` never selects on the outcome it reports.

    holdout: [start, end) window the research loop never sees. By default every candidate that has not exited before
    the holdout starts is dropped before the walk-forward: the window itself and everything after it (bars past the
    holdout would otherwise form stub test folds). With score_holdout=True the walk-forward runs over everything and
    the metrics are computed on the holdout candidates only, judged by the holdout rule (gates.holdout_verdict)
    instead of the walk-forward gates (scored once per configuration; see TrialRegistry.holdout_scored)."""
    bars_dec = bars_dec.reset_index(drop=True)
    m, X = build_decision_frame(bars_dec, context, feature_names, ctx)
    version = X.attrs["feature_version"]
    cands = spec.candidates(m, X)
    a = atr(m, 14)
    ls = spec.label_spec
    labels = one_at_a_time(triple_barrier(bars_dec, cands, ls, a))
    gross = one_at_a_time(triple_barrier(_zero_spread(bars_dec), cands, ls, a))
    if holdout is not None and not score_holdout:
        if not labels.empty:
            labels = labels[_before(labels, holdout[0])].reset_index(drop=True)
        if not gross.empty:
            gross = gross[_before(gross, holdout[0])].reset_index(drop=True)
    if labels.empty:
        return ResearchResult(agent_id=spec.agent_id, n_candidates=0, n_folds=0, oof=labels, metrics={"n": 0},
                              feature_version=version, importance=None)
    if extra_cost_usd > 0:
        labels["ret"] = labels["ret"] - extra_cost_usd / labels["entry"].astype(float)
    labels["risk"] = _risk_fraction(labels, a, ls.stop_atr)
    labels["weight"] = uniqueness_weights(labels, len(bars_dec)).to_numpy()
    feats = X.drop(columns=["ts_utc"]).iloc[labels["idx"].to_numpy()].reset_index(drop=True)
    feats = feats.replace([np.inf, -np.inf], np.nan)
    feats["side"] = labels["side"].to_numpy()
    cols = model_features or model_inputs(spec, [c for c in feats.columns if feats[c].notna().mean() > 0.8])
    y = labels["target_hit"].astype(int)
    folds = splits_for(labels, spec.timeframe)
    oof_pred = np.full(len(labels), np.nan)
    last_model = None
    for f in folds:
        mdl = MetaLabelModel(feature_names=cols, feature_version=version).fit(
            feats.iloc[f.train_idx], y.iloc[f.train_idx], labels["weight"].iloc[f.train_idx])
        oof_pred[f.test_idx] = mdl.predict_raw(feats.iloc[f.test_idx])
        last_model = mdl
    oof = labels.copy()
    oof["p_raw"] = oof_pred
    oof["p"] = _cross_fitted(oof_pred, y.to_numpy(), folds)
    # break-even + margin per candidate, from what is known at the signal: barriers and the extra cost in its ATR
    atr_sig = a.to_numpy()[labels["idx"].to_numpy()]
    cost_atr = np.where(atr_sig > 0, extra_cost_usd / np.where(atr_sig > 0, atr_sig, 1.0), 0.0)
    oof["threshold"] = (ls.stop_atr + cost_atr) / (ls.target_atr + ls.stop_atr) + THRESHOLD_MARGIN   # metrics.breakeven_prob
    oof["taken"] = oof["p"].notna() & (oof["p"] > oof["threshold"])

    in_hold = _in_window(oof, holdout) if holdout is not None and score_holdout else np.ones(len(oof), dtype=bool)
    scored = oof[oof["p_raw"].notna() & in_hold]
    calibrated = scored[scored["p"].notna()]
    taken = calibrated[calibrated["taken"]]
    gross_eval = gross[_in_window(gross, holdout)] if holdout is not None and score_holdout and not gross.empty else gross
    net_eval = oof[in_hold]
    metrics: dict[str, Any] = {
        "n_candidates": int(len(labels)), "n_folds": len(folds),
        "fold_test_sizes": [int(len(f.test_idx)) for f in folds],
        "complete_fold_test_sizes": [int(len(f.test_idx)) for f in folds if f.complete],
        "extra_cost_usd": float(extra_cost_usd), "evaluation": "cross-fitted",
        "holdout": None if holdout is None else {"from": str(holdout[0]), "to": str(holdout[1]), "scored": score_holdout},
        "rule_only": {
            "gross": expectancy(gross_eval["ret"].to_numpy(), _risk_fraction(gross_eval, a, ls.stop_atr)) if len(gross_eval) else {"n": 0},
            "net": expectancy(net_eval["ret"].to_numpy(), net_eval["risk"].to_numpy()),
        },
    }
    if len(scored) > 20 and last_model is not None:
        # the deployed model (last fold) is calibrated on every out-of-fold prediction: all of them precede its use
        last_model.calibrate(oof["p_raw"].dropna().to_numpy(), oof.loc[oof["p_raw"].notna(), "target_hit"].to_numpy())
        tpy = trades_per_year if trades_per_year is not None else _per_year(scored)
        metrics.update({
            "threshold": float(np.median(scored["threshold"])),
            "n_calibrated": int(len(calibrated)),
            "all_candidates": summarize(scored, tpy, n_trials),
            "model_filtered": summarize(taken, tpy * len(taken) / max(len(scored), 1), n_trials),
            "oof_auc": _auc(scored["target_hit"].to_numpy(), scored["p_raw"].to_numpy()),
        })
    if holdout is not None and score_holdout:
        metrics["holdout_verdict"] = holdout_verdict(taken["ret"].to_numpy(), taken["risk"].to_numpy())
    else:
        metrics["gates"] = research_gates(int(len(labels)), metrics["complete_fold_test_sizes"], taken[["ts_utc", "ret"]],
                                          metrics.get("model_filtered"))
    imp = last_model.importance() if last_model is not None else None
    return ResearchResult(agent_id=spec.agent_id, n_candidates=len(labels), n_folds=len(folds), oof=oof, metrics=metrics,
                          feature_version=version, importance=imp, model=last_model if "threshold" in metrics else None)


def _per_year(rows: pd.DataFrame) -> float:
    """Rows per year over the span they cover (at least one week, so a single row does not divide by zero)."""
    ts = pd.DatetimeIndex(pd.to_datetime(rows["ts_utc"], utc=True))
    years = max((ts.max() - ts.min()).days / 365.25, 7 / 365.25)
    return len(rows) / years
