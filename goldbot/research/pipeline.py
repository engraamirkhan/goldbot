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
from goldbot.labels import SwapSpec, one_at_a_time, triple_barrier, uniqueness_weights
from goldbot.research.gates import RULE_ONLY_LABEL, holdout_verdict, research_gates
from goldbot.research.metrics import expectancy, summarize
from goldbot.research.model import MAX_FEATURES, MetaLabelModel, fit_calibrator
from goldbot.research.walkforward import Fold, splits_for, window_for
from goldbot.specialists.base import FEATURE_SEED_KEY, AgentIdentity, Specialist

# Features that need an external input in ctx. They are not in the default set, so the default feature version (the
# one the live engine builds) does not depend on whether that data happens to be loaded.
OPT_IN_FEATURES = ("macro", "macro_drivers", "calendar_events")
DEFAULT_FEATURE_NAMES = [n for n in FEATURES if n not in OPT_IN_FEATURES]
MACRO_FEATURE_NAMES = ["macro_drivers"]      # added when ctx carries the macro table (release macro-v1)


def research_feature_names(ctx: dict | None = None) -> list[str]:
    """The default features, plus the macro drivers when ctx["macro"] holds macro rows. A model trained on this set
    carries its feature version, so it scores only frames built with macro too."""
    macro = (ctx or {}).get("macro")
    return [*DEFAULT_FEATURE_NAMES, *MACRO_FEATURE_NAMES] if macro is not None and len(macro) else list(DEFAULT_FEATURE_NAMES)


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
    """Features on the decision timeframe plus higher-TF context merged without lookahead. Without `feature_names`:
    research_feature_names(ctx) (the macro drivers join in when ctx carries macro rows)."""
    names = feature_names or research_feature_names(ctx)
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
                    feature_names: list[str] | None = None, ctx: dict | None = None) -> dict[str, Any]:
    """Leakage check on the data actually used: build the decision frame on the full history and on the history
    truncated at `cut_frac` (context bars only those visible by then, ctx tables such as macro only the rows available
    by then). A feature whose value on a bar before the cut differs between the two used data from after the cut.
    Returns the offending columns (empty = clean)."""
    bars_dec = bars_dec.reset_index(drop=True)
    cut = int(len(bars_dec) * cut_frac)
    cut_ts = pd.Timestamp(bars_dec["visible_at"].iloc[cut - 1])
    _, full = build_decision_frame(bars_dec, context, feature_names, ctx)
    ctx_cut = {k: v[pd.to_datetime(v["visible_at"], utc=True) <= cut_ts] for k, v in (context or {}).items()}
    feat_ctx_cut = {k: v[pd.to_datetime(v["available_utc"], utc=True) <= cut_ts]
                    if isinstance(v, pd.DataFrame) and "available_utc" in v.columns else v for k, v in (ctx or {}).items()}
    _, part = build_decision_frame(bars_dec.iloc[:cut], ctx_cut, feature_names or research_feature_names(ctx), feat_ctx_cut)
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


def candidate_threshold(labels: pd.DataFrame, extra_cost_usd: float) -> np.ndarray:
    """Break-even + THRESHOLD_MARGIN per candidate, from what is known at the signal: its barriers and the extra cost
    in its ATR (metrics.breakeven_prob). A candidate is taken when its calibrated p is above it."""
    atr_sig = labels["atr_sig"].to_numpy(dtype=float)
    cost_atr = np.where(atr_sig > 0, extra_cost_usd / np.where(atr_sig > 0, atr_sig, 1.0), 0.0)
    stop, target = labels["stop_atr"].to_numpy(dtype=float), labels["target_atr"].to_numpy(dtype=float)
    return (stop + cost_atr) / (target + stop) + THRESHOLD_MARGIN


class Prepared(Record):
    """One specialist configuration labelled on one decision timeframe, before any model: the input of the primary-signal
    screen (research.screen), of the per-family walk-forward (`evaluate`) and of the pooled one (`run_pool`)."""
    spec: Any                       # Specialist
    labels: pd.DataFrame            # net labels (spread in the fills, extra cost taken off `ret`) with risk and barriers
    gross: pd.DataFrame             # the same candidates on mid prices: the rule's outcome before any cost
    feats: pd.DataFrame             # decision-frame features of each label's signal bar, plus `side`
    feature_version: str
    n_bars: int
    swap: SwapSpec | None = None    # overnight financing charged in `labels` (None: not charged)


def prepare(spec: Specialist, bars_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None = None,
            feature_names: list[str] | None = None, ctx: dict | None = None, extra_cost_usd: float = 0.0,
            holdout: Window | None = None, score_holdout: bool = False,
            frame: tuple[pd.DataFrame, pd.DataFrame] | None = None, swap: SwapSpec | None = None) -> Prepared:
    """Features, candidates and labels (one position at a time) for one configuration. `frame`: the decision frame
    (build_decision_frame's output) when several configurations share a timeframe, so it is built once. Candidates
    and the barrier ATR come from `Specialist.candidates_in_context` / `barrier_atr`, which see the context bars (a
    daily signal executed on 4h bars).

    extra_cost_usd: round-trip cost per oz beyond the bar spread (entry and exit slippage plus commission). Labels
    already pay the spread (entry at the ask, exit at the bid), so the spread is not charged again: the extra cost is
    taken off every label's return and enters the break-even probability per candidate (extra / ATR at the signal).

    swap: overnight financing for every server-day rollover a net label is held through (triple_barrier); the gross
    labels (the screen's) carry no cost. Unknown at the signal (it depends on the hold), so it is not in the threshold.

    holdout: unless score_holdout, every candidate that has not exited before the holdout starts is dropped: the window
    itself and everything after it (bars past the holdout would otherwise form stub test folds)."""
    bars_dec = bars_dec.reset_index(drop=True)
    m, X = frame if frame is not None else build_decision_frame(bars_dec, context, feature_names, ctx)
    version = X.attrs["feature_version"]
    cands = spec.complete_windows(bars_dec, spec.candidates_in_context(m, X, context))   # sample rule (asia_drift)
    own_atr = spec.barrier_atr(m, context)        # e.g. ATR(1d) for a daily signal executed on 4h bars
    a = atr(m, 14) if own_atr is None else own_atr
    ls = spec.label_spec
    labels = one_at_a_time(triple_barrier(bars_dec, cands, ls, a, swap=swap, policy=spec.exit_spec))
    gross = one_at_a_time(triple_barrier(_zero_spread(bars_dec), cands, ls, a, policy=spec.exit_spec))
    if holdout is not None and not score_holdout:
        if not labels.empty:
            labels = labels[_before(labels, holdout[0])].reset_index(drop=True)
        if not gross.empty:
            gross = gross[_before(gross, holdout[0])].reset_index(drop=True)
    feats = pd.DataFrame()
    if not labels.empty:
        if extra_cost_usd > 0:
            labels["ret"] = labels["ret"] - extra_cost_usd / labels["entry"].astype(float)
        labels["risk"] = _risk_fraction(labels, a, ls.stop_atr)
        labels["atr_sig"] = a.to_numpy()[labels["idx"].to_numpy()]
        labels["target_atr"], labels["stop_atr"], labels["family"] = ls.target_atr, ls.stop_atr, spec.family
        feats = X.drop(columns=["ts_utc"]).iloc[labels["idx"].to_numpy()].reset_index(drop=True)
        feats = feats.replace([np.inf, -np.inf], np.nan)
        feats["side"] = labels["side"].to_numpy()
    if not gross.empty:
        gross["risk"] = _risk_fraction(gross, a, ls.stop_atr)
    return Prepared(spec=spec, labels=labels, gross=gross, feats=feats, feature_version=version, n_bars=len(bars_dec),
                    swap=swap)


def rule_only(gross: pd.DataFrame, net: pd.DataFrame) -> dict[str, Any]:
    """The rule's own expectancy in R over every candidate (no model): gross on mid prices, net of every cost."""
    return {"gross": expectancy(gross["ret"].to_numpy(), gross["risk"].to_numpy()) if len(gross) else {"n": 0},
            "net": expectancy(net["ret"].to_numpy(), net["risk"].to_numpy()) if len(net) else {"n": 0}}


def eligible_columns(feats: pd.DataFrame) -> list[str]:
    """Columns populated on more than 80% of the candidates (the model's candidates for inputs)."""
    return [c for c in feats.columns if feats[c].notna().mean() > 0.8]


def run_specialist(spec: Specialist, bars_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None = None,
                   feature_names: list[str] | None = None, model_features: list[str] | None = None,
                   n_trials: int = 1, trades_per_year: float | None = None, ctx: dict | None = None,
                   extra_cost_usd: float = 0.0, holdout: Window | None = None,
                   score_holdout: bool = False, swap: SwapSpec | None = None) -> ResearchResult:
    """Walk-forward one specialist configuration: `prepare` then `evaluate` (see both)."""
    prep = prepare(spec, bars_dec, context, feature_names, ctx, extra_cost_usd, holdout, score_holdout, swap=swap)
    return evaluate(prep, model_features=model_features, n_trials=n_trials, trades_per_year=trades_per_year,
                    extra_cost_usd=extra_cost_usd, holdout=holdout, score_holdout=score_holdout)


def evaluate(prep: Prepared, model_features: list[str] | None = None, n_trials: int = 1,
             trades_per_year: float | None = None, extra_cost_usd: float = 0.0, holdout: Window | None = None,
             score_holdout: bool = False) -> ResearchResult:
    """Purged walk-forward of one prepared configuration with the specialist's declared inputs and its walk-forward
    windows (the timeframe's, with the specialist's `walkforward` overrides).

    Selection is cross-fitted: fold k's calibrator comes from earlier folds' out-of-fold predictions only, and the
    threshold uses only information at the signal, so `model_filtered` never selects on the outcome it reports.

    holdout: with score_holdout=True the walk-forward runs over everything and the metrics are computed on the holdout
    candidates only, judged by the holdout rule (gates.holdout_verdict) instead of the walk-forward gates (scored once
    per configuration; see TrialRegistry.holdout_scored)."""
    spec = prep.spec
    if prep.labels.empty:
        return ResearchResult(agent_id=spec.agent_id, n_candidates=0, n_folds=0, oof=prep.labels, metrics={"n": 0},
                              feature_version=prep.feature_version, importance=None)
    labels = prep.labels.copy()
    labels["weight"] = uniqueness_weights(labels, prep.n_bars).to_numpy()
    cols = model_features or model_inputs(spec, eligible_columns(prep.feats))
    window = window_for(spec.timeframe, **spec.walkforward)
    folds = splits_for(labels, spec.timeframe, **spec.walkforward)
    return _walk_forward(spec.agent_id, labels, prep.gross, prep.feats, cols, folds, prep.feature_version,
                         n_trials=n_trials, trades_per_year=trades_per_year, extra_cost_usd=extra_cost_usd,
                         holdout=holdout, score_holdout=score_holdout, window=window, swap=prep.swap)


def _walk_forward(agent_id: str, labels: pd.DataFrame, gross: pd.DataFrame, feats: pd.DataFrame, cols: list[str],
                  folds: list[Fold], version: str, *, n_trials: int, trades_per_year: float | None,
                  extra_cost_usd: float, holdout: Window | None, score_holdout: bool,
                  window: dict[str, Any], swap: SwapSpec | None = None,
                  fold_cols: dict[int, list[str]] | None = None) -> ResearchResult:
    """Shared by the per-family and the pooled walk-forward: fit per fold, cross-fitted calibration, per-candidate
    threshold from its own barriers, metrics, rule-only expectancy and the design's gates (or the holdout rule).
    `labels` carries ret (net), risk, weight, target_atr, stop_atr and atr_sig; `feats` is row-aligned with it.
    fold_cols: per fold number, the inputs selected from that fold's training rows only (feature discovery);
    folds not in it use `cols`."""
    y = labels["target_hit"].astype(int)
    oof_pred = np.full(len(labels), np.nan)
    last_model = None
    for f in folds:
        fc = (fold_cols or {}).get(f.k, cols)
        mdl = MetaLabelModel(feature_names=fc, feature_version=version).fit(
            feats.iloc[f.train_idx], y.iloc[f.train_idx], labels["weight"].iloc[f.train_idx])
        oof_pred[f.test_idx] = mdl.predict_raw(feats.iloc[f.test_idx])
        last_model = mdl
    oof = labels.copy()
    oof["p_raw"] = oof_pred
    oof["p"] = _cross_fitted(oof_pred, y.to_numpy(), folds)
    oof["threshold"] = candidate_threshold(labels, extra_cost_usd)
    oof["taken"] = oof["p"].notna() & (oof["p"] > oof["threshold"])

    in_hold = _in_window(oof, holdout) if holdout is not None and score_holdout else np.ones(len(oof), dtype=bool)
    scored = oof[oof["p_raw"].notna() & in_hold]
    calibrated = scored[scored["p"].notna()]
    taken = calibrated[calibrated["taken"]]
    gross_eval = gross[_in_window(gross, holdout)] if holdout is not None and score_holdout and not gross.empty else gross
    metrics: dict[str, Any] = {
        "n_candidates": int(len(labels)), "n_folds": len(folds),
        "fold_test_sizes": [int(len(f.test_idx)) for f in folds],
        "complete_fold_test_sizes": [int(len(f.test_idx)) for f in folds if f.complete],
        "walkforward": window,
        "extra_cost_usd": float(extra_cost_usd), "evaluation": "cross-fitted",
        "swap": _swap_summary(swap, oof[in_hold]),
        "holdout": None if holdout is None else {"from": str(holdout[0]), "to": str(holdout[1]), "scored": score_holdout},
        "rule_only": rule_only(gross_eval, oof[in_hold]),
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
        metrics["rule_only_gates"] = rule_only_gates(oof, metrics["complete_fold_test_sizes"], n_trials, trades_per_year)
    imp = last_model.importance() if last_model is not None else None
    return ResearchResult(agent_id=agent_id, n_candidates=len(labels), n_folds=len(folds), oof=oof, metrics=metrics,
                          feature_version=version, importance=imp, model=last_model if "threshold" in metrics else None)


def rule_only_gates(net: pd.DataFrame, fold_test_sizes: list[int], n_trials: int,
                    trades_per_year: float | None = None) -> dict[str, Any]:
    """The design's gates applied to the rule alone: every candidate's net result (spread, slippage, commission, swap)
    in the research window, no model filter, the same candidates and folds, the DSR with the same trial count.
    Informational (RULE_ONLY_LABEL): a passed trial still needs the model path's gates."""
    rows = net[["ts_utc", "ret"]]
    tpy = trades_per_year if trades_per_year is not None else (_per_year(rows) if len(rows) else 0.0)
    summary = summarize(rows, tpy, n_trials)
    out = research_gates(int(len(net)), fold_test_sizes, rows, summary, subject="rule-only")
    return {**out, "label": RULE_ONLY_LABEL, "n_trades": int(len(net)), "summary": summary}


def _swap_summary(swap: SwapSpec | None, labels: pd.DataFrame) -> dict[str, Any] | None:
    """The swap charged in the net labels: its rates and, per trade, the mean nights and mean charge in R."""
    if swap is None:
        return None
    out: dict[str, Any] = swap.model_dump()
    if "swap_nights" in labels and len(labels):
        out["mean_nights"] = float(labels["swap_nights"].mean())
        out["share_held_overnight"] = float((labels["swap_nights"] > 0).mean())
        risk = labels["risk"].to_numpy(dtype=float) if "risk" in labels else np.ones(len(labels))
        with np.errstate(invalid="ignore", divide="ignore"):
            out["mean_r"] = float(np.nanmean(np.where(risk > 0, labels["swap_ret"].to_numpy(dtype=float) / risk, np.nan)))
    return out


def _per_year(rows: pd.DataFrame) -> float:
    """Rows per year over the span they cover (at least one week, so a single row does not divide by zero)."""
    ts = pd.DatetimeIndex(pd.to_datetime(rows["ts_utc"], utc=True))
    years = max((ts.max() - ts.min()).days / 365.25, 7 / 365.25)
    return len(rows) / years


# ---------------------------------------------------------------------------------------------- pooled (P5)
FAMILY_PREFIX = "family_"
# The pooled meta-model's declared inputs per decision timeframe (proposal P5): context every family's candidates
# share (volatility regime, trend and stretch on the bar and the higher timeframes, momentum, flow, session), after
# `side` and one indicator column per family. At most 40 in all (pooled_inputs checks).
POOLED_FEATURES: dict[str, tuple[str, ...]] = {
    "15m": (
        "ret_1", "ret_4", "ret_16", "ret_96", "tsmom_z_24", "tsmom_score", "atr14_pct", "atr_ratio_14_100",
        "rv_ratio", "vol_tercile", "dist_ema20_atr", "dist_ema50_atr", "slope_ema50", "ribbon_state", "adx14",
        "donchian_pos_20", "bb_z_20", "rsi14", "range_width_24_atr", "tick_vol_ratio_20", "spread_atr",
        "dist_res_atr", "dist_sup_atr", "session_id", "dow", "im_ny_ret_atr", "im_ldn_ret_atr", "h1_adx14",
        "h4_slope_ema50", "h4_dist_ema50_atr", "d1_dist_ema50_atr", "d1_slope_ema50",
    ),
    "1h": (
        "ret_1", "ret_4", "ret_16", "ret_96", "tsmom_z_24", "tsmom_z_120", "tsmom_score", "atr14_pct",
        "atr_ratio_14_100", "rv_ratio", "vol_tercile", "dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr",
        "slope_ema50", "ribbon_state", "adx14", "donchian_pos_20", "bb_z_20", "rsi14", "range_width_8_atr",
        "range_width_96_atr", "tick_vol_ratio_20", "dist_res_atr", "dist_sup_atr", "session_id", "dow",
        "h4_adx14", "h4_slope_ema50", "h4_dist_ema50_atr", "d1_dist_ema50_atr", "d1_slope_ema50",
    ),
}


def pooled_family(timeframe: str) -> str:
    return f"pooled_{timeframe}"


def pooled_members(timeframe: str) -> list[str]:
    """The families a pooled model on `timeframe` unites: every registered family whose default timeframe it is, except
    a family still under its pre-registered screen (`Specialist.screening`), so adding one leaves the pools unchanged."""
    from goldbot.specialists import SPECIALISTS
    return sorted(f for f, cls in SPECIALISTS.items() if cls.timeframe == timeframe and not cls.screening)


def pooled_inputs(timeframe: str, families: list[str], eligible: list[str]) -> list[str]:
    """`side`, one indicator per family, then the timeframe's declared pooled features present and eligible."""
    if timeframe not in POOLED_FEATURES:
        raise ValueError(f"no pooled feature list for {timeframe}; known: {sorted(POOLED_FEATURES)}")
    ok = set(eligible)
    cols = ["side", *[f"{FAMILY_PREFIX}{f}" for f in sorted(families)], *[c for c in POOLED_FEATURES[timeframe] if c in ok]]
    if len(cols) > MAX_FEATURES:
        raise ValueError(f"pooled {timeframe} model would have {len(cols)} inputs; the cap is {MAX_FEATURES}")
    return cols


def pool_identity(timeframe: str, preps: list[Prepared]) -> AgentIdentity:
    """Registry identity of a pooled trial: its timeframe and every member's family and full config."""
    return AgentIdentity(family=pooled_family(timeframe),
                         config={"timeframe": timeframe, "families": {p.spec.family: p.spec.config for p in preps}})


def pool_frames(preps: list[Prepared]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The union of the members' labels (each family one position at a time, as each agent trades) in signal-time
    order, their features with one indicator column per family (row-aligned), and their gross labels."""
    fams = sorted({p.spec.family for p in preps})
    labs, feats, gross = [], [], []
    for p in preps:
        if not p.gross.empty:
            gross.append(p.gross.assign(family=p.spec.family))
        if p.labels.empty:
            continue
        f = p.feats.copy()
        for fam in fams:
            f[f"{FAMILY_PREFIX}{fam}"] = float(fam == p.spec.family)
        labs.append(p.labels)
        feats.append(f)
    gr = pd.concat(gross, ignore_index=True) if gross else pd.DataFrame()
    if not labs:
        return pd.DataFrame(), pd.DataFrame(), gr
    lab = pd.concat(labs, ignore_index=True)
    fe = pd.concat(feats, ignore_index=True)
    order = np.argsort(pd.DatetimeIndex(pd.to_datetime(lab["ts_utc"], utc=True)).to_numpy(), kind="stable")
    return lab.iloc[order].reset_index(drop=True), fe.iloc[order].reset_index(drop=True), gr


MIN_LOCAL_TRAIN = 100            # a family's own model in a pooled fold needs this many training rows (else no local score)


def run_pool(preps: list[Prepared], timeframe: str, n_trials: int = 1, trades_per_year: float | None = None,
             extra_cost_usd: float = 0.0, holdout: Window | None = None, score_holdout: bool = False,
             compare_local: bool = True) -> ResearchResult:
    """Walk-forward ONE meta-model over the union of every member's candidates on `timeframe` (proposal P5), with
    family indicators and `side` among its inputs, and the same cross-fitted calibration, thresholds (each candidate's
    own barriers) and gates as a single family (`_walk_forward`). Uniqueness weights are computed across the pool (all
    members share the decision bars) and the purge sees every member's labels. Folds use the timeframe's windows.

    `by_family` reports each family's share, and with compare_local the deciding comparison of P5: per family, the
    out-of-fold log-loss of the pooled model against the family's own model (its declared inputs) trained on the
    family's rows of the same training folds and scored on the same test rows."""
    if any(p.spec.timeframe != timeframe for p in preps):
        raise ValueError(f"every pooled member must decide on {timeframe}")
    ident = pool_identity(timeframe, preps)
    version = preps[0].feature_version if preps else ""
    labels, feats, gross = pool_frames(preps)
    if labels.empty:
        return ResearchResult(agent_id=ident.agent_id, n_candidates=0, n_folds=0, oof=labels, metrics={"n": 0},
                              feature_version=version, importance=None)
    labels["weight"] = uniqueness_weights(labels, max(p.n_bars for p in preps)).to_numpy()
    families = sorted({p.spec.family for p in preps})
    cols = pooled_inputs(timeframe, families, eligible_columns(feats))
    folds = splits_for(labels, timeframe)
    res = _walk_forward(ident.agent_id, labels, gross, feats, cols, folds, version, n_trials=n_trials,
                        trades_per_year=trades_per_year, extra_cost_usd=extra_cost_usd, holdout=holdout,
                        score_holdout=score_holdout, window=window_for(timeframe),
                        swap=preps[0].swap if preps else None)
    local = _local_predictions(preps, labels, feats, folds, version) if compare_local else {}
    res.metrics["by_family"] = _by_family(res.oof, gross, local, holdout if score_holdout else None)
    res.metrics["families"] = families
    return res


def _local_predictions(preps: list[Prepared], labels: pd.DataFrame, feats: pd.DataFrame, folds: list[Fold],
                       version: str) -> dict[str, np.ndarray]:
    """Per family: raw out-of-fold predictions of its own model on its rows of each pooled fold (NaN where its
    training rows were too few or one class)."""
    out: dict[str, np.ndarray] = {}
    y = labels["target_hit"].astype(int)
    fam = labels["family"].to_numpy()
    for p in preps:
        mine = fam == p.spec.family
        pred = np.full(len(labels), np.nan)
        cols = model_inputs(p.spec, eligible_columns(feats.loc[mine, list(p.feats.columns)])) if mine.any() else []
        for f in folds:
            tr, te = f.train_idx[mine[f.train_idx]], f.test_idx[mine[f.test_idx]]
            if len(tr) < MIN_LOCAL_TRAIN or len(te) == 0 or y.iloc[tr].nunique() < 2:
                continue
            mdl = MetaLabelModel(feature_names=cols, feature_version=version).fit(
                feats.iloc[tr], y.iloc[tr], labels["weight"].iloc[tr])
            pred[te] = mdl.predict_raw(feats.iloc[te])
        out[p.spec.family] = pred
    return out


def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def _by_family(oof: pd.DataFrame, gross: pd.DataFrame, local: dict[str, np.ndarray],
               holdout: Window | None) -> dict[str, Any]:
    """Per member family: rule-only expectancy, out-of-fold AUC under the pooled model, the model-filtered
    expectancy, and pooled vs local log-loss on the rows both scored (when the local models ran)."""
    in_hold = _in_window(oof, holdout) if holdout is not None else np.ones(len(oof), dtype=bool)
    out: dict[str, Any] = {}
    for fam in sorted(oof["family"].unique()):
        mine = (oof["family"] == fam).to_numpy() & in_hold
        rows = oof[mine]
        g = gross[gross["family"] == fam] if not gross.empty else gross
        if holdout is not None and not g.empty:
            g = g[_in_window(g, holdout)]
        scored = rows[rows["p_raw"].notna()]
        taken = scored[scored["p"].notna() & scored["taken"]]
        entry: dict[str, Any] = {
            "n_candidates": int(len(rows)), "rule_only": rule_only(g, rows),
            "oof_auc": _auc(scored["target_hit"].to_numpy(), scored["p_raw"].to_numpy()) if len(scored) else None,
            "model_filtered": expectancy(taken["ret"].to_numpy(), taken["risk"].to_numpy()),
        }
        lp = local.get(str(fam))
        if lp is not None:
            both = np.isfinite(lp[mine]) & rows["p_raw"].notna().to_numpy()
            if both.any():
                yv = rows["target_hit"].to_numpy(dtype=float)[both]
                entry["logloss"] = {"n": int(both.sum()),
                                    "pooled": _log_loss(yv, rows["p_raw"].to_numpy(dtype=float)[both]),
                                    "local": _log_loss(yv, lp[mine][both])}
        out[str(fam)] = entry
    return out
