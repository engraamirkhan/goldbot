"""Feature discovery: ONE pre-registered trial that screens many candidate features (indicator survey 4b).

The library of point-in-time features can exceed the 40-feature cap of a live model; adding a feature is not a trial.
Choosing which features a model uses is a look at the data, so it happens inside the estimator, fold by fold:

* inputs: a specialist (its candidates and triple-barrier labels) and every eligible feature column (`eligible_columns`,
  `side` excluded: it is always an input);
* inside each purged walk-forward fold, on that fold's TRAINING rows only (the walk-forward's purge already removed
  every training label whose life reaches the test window), stability selection (Meinshausen & Buehlmann 2010):
  `n_subsamples` half-samples drawn by weekly block (serially correlated candidates stay together), a shallow
  LightGBM (or an L1-logistic regression) fitted on each, and a record of which features land in the top `top_k` by
  importance. A feature's frequency in the fold is the share of subsamples that put it in the top k. Optionally a
  confirmation by permutation importance on an inner validation split of the same training rows (the last
  `inner_val_frac` of them in time; inner training rows whose label reaches the validation start minus the
  walk-forward's purge are dropped): the feature must also reduce the validation log-loss;
* the fold's model is then fitted on the fold's selection (frequency >= `freq_threshold`, confirmed, at most 39 plus
  `side`) and predicts the test fold, with the cross-fitted calibration, thresholds and gates of every walk-forward
  (`pipeline._walk_forward`). No test row ever influences which features its model uses;
* reported: per-feature and per-group frequency per fold (a group's is its most frequent member's, so a large group
  is not favoured for its size), the mean across folds, and stability = the share of folds in
  which the frequency reached `freq_threshold`. Survivors (stability >= `stability_threshold`, at most 39) are the
  candidates for later trials.

Groups (`--families`): every column is its own group by default; `feature` groups columns by the registered feature
that produced them (h1_/h4_/d1_ context columns join their base feature), `family` by the feature's registry family.

Trial accounting: the run is ONE registry trial (status "discovery"), written after a "preregistered" row that holds
its config and READING_RULE. Its own out-of-fold score is honest because selection sits inside the estimator; but
survivors are chosen from frequencies over the whole research window, so a later trial of a survivor uses
registry.n_trials_effective (registry trials + n_groups_screened of every discovery) in its deflated Sharpe.
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, Record
from goldbot.data.timeutil import epoch_ns
from goldbot.features import FEATURES
from goldbot.features.registry import _CONTEXT_PREFIX, side_align
from goldbot.labels import uniqueness_weights
from goldbot.research.metrics import expectancy
from goldbot.research.model import MAX_FEATURES
from goldbot.research.pipeline import (
    Prepared,
    ResearchResult,
    Window,
    _auc,
    _log_loss,
    _walk_forward,
    eligible_columns,
)
from goldbot.research.walkforward import Fold, splits_for, window_for

GroupMode = Literal["column", "feature", "family"]
GROUP_MODES: tuple[str, ...] = ("column", "feature", "family")
WEEK_NS = 7 * 86_400 * 10**9

# Reading rule, written into the pre-registration before the run (survey 4b, decided in advance)
CONTINUE_AUC = 0.53
STOP_AUC = 0.52
AUC_LOWER_BOUND = 0.50
MIN_READ_EVENTS = 1000
READING_RULE = (
    f"continue if the pooled out-of-fold AUC >= {CONTINUE_AUC} with a weekly-block bootstrap 95% lower bound > "
    f"{AUC_LOWER_BOUND}, at least {MIN_READ_EVENTS:,} candidates scored out of fold, and the model-filtered "
    f"(cross-fitted, break-even + margin) trades have net mean R > 0; stop if the AUC < {STOP_AUC} (the screened "
    f"features carry no information for this label: keep them as risk and cost filters only); otherwise inconclusive. "
    f"Survivors: feature groups whose fold frequency reaches the threshold in >= the stability share of folds; at most "
    f"five go forward, and every later trial of one uses n_trials_effective (registry trials + groups screened) in its "
    f"deflated Sharpe. The design's gates are reported; a discovery is never promoted.")

SELECT_PARAMS: dict[str, Any] = dict(
    objective="binary", learning_rate=0.05, num_leaves=7, min_child_samples=40, feature_fraction=0.8,
    bagging_fraction=1.0, lambda_l2=5.0, n_estimators=100, verbose=-1, n_jobs=1,
)


class DiscoveryConfig(FrozenRecord):
    """Pre-registered settings of one discovery trial."""
    n_subsamples: int = 50              # half-samples per fold
    subsample_frac: float = 0.5         # share of the training fold's weeks in each subsample
    top_k: int = 10                     # a subsample "selects" its top_k features by importance
    method: Literal["lgbm", "l1"] = "lgbm"
    l1_c: float = 0.05                  # inverse L1 strength (method "l1"; standardised inputs)
    freq_threshold: float = 0.6         # selected in a fold when its top-k frequency reaches this
    stability_threshold: float = 0.7    # survivor: frequency reached the threshold in at least this share of folds
    permutation: bool = False           # confirm by permutation importance on an inner validation split
    inner_val_frac: float = 0.2
    permutation_repeats: int = 3
    max_selected: int = MAX_FEATURES - 1   # `side` is always the 40th input
    seed: int = 0


class FoldSelection(Record):
    k: int
    n_train: int
    n_subsamples: int                   # subsamples actually fitted (a one-class subsample is skipped)
    frequency: dict[str, float]         # feature -> share of subsamples with it in the top k
    group_frequency: dict[str, float]   # group -> its most frequent member's frequency (size-neutral)
    permutation: dict[str, float] | None = None   # feature -> validation log-loss increase when permuted
    selected: list[str]                 # the fold model's inputs besides `side`
    fallback: bool = False              # nothing reached the threshold: the top_k most frequent were used


class DiscoveryResult(Record):
    result: ResearchResult              # walk-forward of the model on each fold's own selection
    folds: list[FoldSelection]
    table: pd.DataFrame                 # per feature: group, f<k> frequencies, mean_freq, stability
    group_table: pd.DataFrame           # per group: n_features, f<k> frequencies, mean_freq, stability
    selected: list[str]                 # survivors (features), at most max_selected
    survivor_groups: list[str]
    n_features_screened: int
    n_groups_screened: int
    group_mode: str
    verdict: dict[str, Any]


# ---------------------------------------------------------------------------------------------- groups
def column_groups(columns: list[str], mode: str, m: pd.DataFrame | None = None, names: list[str] | None = None,
                  ctx: dict | None = None) -> dict[str, str]:
    """Group of each column. "column": itself; "feature" / "family": the registered feature that produced it (found by
    running each feature on the last bars of `m`) or that feature's family. A context column (h1_/h4_/d1_/w1_) joins
    its base column's group; a column no feature claims is its own group."""
    if mode not in GROUP_MODES:
        raise ValueError(f"unknown group mode {mode!r}; one of {GROUP_MODES}")
    if mode == "column":
        return {c: c for c in columns}
    owner: dict[str, str] = {}
    if m is not None:
        tail = m.tail(2000).reset_index(drop=True)
        for n in names or list(FEATURES):
            try:
                cols = FEATURES[n].fn(tail, ctx or {}).columns
            except Exception:            # a feature that needs an input this run lacks produced no column here
                continue
            for c in cols:
                owner.setdefault(str(c), n)
    out = {}
    for c in columns:
        who = owner.get(c) or owner.get(_CONTEXT_PREFIX.sub("", c))
        out[c] = c if who is None else (who if mode == "feature" else FEATURES[who].family)
    return out


# ---------------------------------------------------------------------------------------------- fold selection
def _design(X: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """The selection model's inputs: side-aligned like the meta-model's when `side` is present."""
    if "side" in X.columns:
        return side_align(X[["side", *cols]])
    return X[cols]


def _fit_importance(Z: pd.DataFrame, y: np.ndarray, w: np.ndarray, cols: list[str], cfg: DiscoveryConfig,
                    seed: int) -> np.ndarray:
    """Importance of each of `cols` (gain for LightGBM, |coefficient| for L1-logistic) on one subsample."""
    if cfg.method == "lgbm":
        import lightgbm as lgb
        mdl = lgb.LGBMClassifier(**{**SELECT_PARAMS, "random_state": seed}).fit(Z, y, sample_weight=w)
        imp = pd.Series(mdl.booster_.feature_importance("gain"), index=list(Z.columns))
        return imp.reindex(cols).fillna(0.0).to_numpy(dtype=float)
    from sklearn.linear_model import LogisticRegression
    A = Z.apply(pd.to_numeric, errors="coerce").astype(float)
    A = A.fillna(A.median()).fillna(0.0)
    sd = A.std().replace(0.0, 1.0)
    A = (A - A.mean()) / sd
    lr = LogisticRegression(penalty="l1", solver="liblinear", C=cfg.l1_c, random_state=seed)
    lr.fit(A.to_numpy(), y, sample_weight=w)
    coef = pd.Series(np.abs(lr.coef_[0]), index=list(Z.columns))
    return coef.reindex(cols).fillna(0.0).to_numpy(dtype=float)


def _permutation_importance(X: pd.DataFrame, y: np.ndarray, w: np.ndarray, ts: pd.DatetimeIndex,
                            te: pd.DatetimeIndex, cols: list[str], cfg: DiscoveryConfig, purge_days: int,
                            rng: np.random.Generator) -> dict[str, float]:
    """Mean validation log-loss increase when each column is permuted, on an inner time split of the training rows:
    the last inner_val_frac by signal time validate; inner training rows whose label ends after the validation start
    minus the purge are dropped. Columns are permuted before side alignment, as the raw feature would be."""
    import lightgbm as lgb
    cut = ts.sort_values()[int(len(ts) * (1 - cfg.inner_val_frac))]
    val = np.asarray(ts >= cut)
    fit = np.asarray(ts < cut) & np.asarray(te < cut - pd.Timedelta(days=purge_days))
    if fit.sum() < 50 or val.sum() < 20 or len(np.unique(y[fit])) < 2 or len(np.unique(y[val])) < 2 or not cols:
        return {c: 0.0 for c in cols}
    mdl = lgb.LGBMClassifier(**{**SELECT_PARAMS, "random_state": cfg.seed}).fit(
        _design(X.iloc[np.flatnonzero(fit)], cols), y[fit], sample_weight=w[fit])
    Xv = X.iloc[np.flatnonzero(val)].reset_index(drop=True)
    yv = y[val].astype(float)
    base = _log_loss(yv, np.asarray(mdl.predict_proba(_design(Xv, cols)))[:, 1])
    out = {}
    for c in cols:
        deltas = []
        for _ in range(cfg.permutation_repeats):
            Xp = Xv.copy()
            Xp[c] = Xp[c].to_numpy()[rng.permutation(len(Xp))]
            deltas.append(_log_loss(yv, np.asarray(mdl.predict_proba(_design(Xp, cols)))[:, 1]) - base)
        out[c] = float(np.mean(deltas))
    return out


def select_in_fold(feats: pd.DataFrame, labels: pd.DataFrame, fold: Fold, pool: list[str], cfg: DiscoveryConfig,
                   groups: dict[str, str] | None = None, purge_days: int = 0) -> FoldSelection:
    """Stability selection on `fold.train_idx` ONLY (never a test row). `labels` needs ts_utc, ts_exit, target_hit
    and weight, row-aligned with `feats`."""
    groups = groups or {c: c for c in pool}
    tr = np.asarray(fold.train_idx)
    X = feats.iloc[tr].reset_index(drop=True)
    y = labels["target_hit"].to_numpy(dtype=int)[tr]
    w = labels["weight"].to_numpy(dtype=float)[tr] if "weight" in labels else np.ones(len(tr))
    ts = pd.DatetimeIndex(pd.to_datetime(labels["ts_utc"], utc=True))[tr]
    te = pd.DatetimeIndex(pd.to_datetime(labels["ts_exit"], utc=True))[tr]
    week = epoch_ns(ts) // WEEK_NS
    weeks = np.unique(week)
    rng = np.random.default_rng([cfg.seed, fold.k])
    n_pick = max(1, int(round(cfg.subsample_frac * len(weeks))))
    hits = dict.fromkeys(pool, 0)
    Z_all = _design(X, pool)
    fitted = 0
    for s in range(cfg.n_subsamples):
        rows = np.flatnonzero(np.isin(week, rng.choice(weeks, size=n_pick, replace=False)))
        if len(np.unique(y[rows])) < 2:
            continue
        imp = _fit_importance(Z_all.iloc[rows], y[rows], w[rows], pool, cfg, seed=cfg.seed * 1000 + fold.k * 100 + s)
        order = [i for i in np.argsort(-imp, kind="stable")[: cfg.top_k] if imp[i] > 0]
        for i in order:
            hits[pool[i]] += 1
        fitted += 1
    denom = max(fitted, 1)
    freq = {c: hits[c] / denom for c in pool}
    gfreq: dict[str, float] = {}
    for c in pool:                       # a group is as frequent as its best member: "any member" would favour big groups
        gfreq[groups[c]] = max(gfreq.get(groups[c], 0.0), freq[c])
    perm = None
    if cfg.permutation:
        cand = [c for c in pool if freq[c] > 0]
        perm = _permutation_importance(X, y, w, ts, te, cand, cfg, purge_days, rng)
    ok = [c for c in pool if freq[c] >= cfg.freq_threshold and (perm is None or perm.get(c, 0.0) > 0)]
    rank = {c: i for i, c in enumerate(pool)}
    selected = sorted(ok, key=lambda c: (-freq[c], rank[c]))[: cfg.max_selected]
    fallback = not selected
    if fallback:
        selected = sorted((c for c in pool if freq[c] > 0), key=lambda c: (-freq[c], rank[c]))[: min(cfg.top_k, cfg.max_selected)]
    return FoldSelection(k=fold.k, n_train=len(tr), n_subsamples=fitted, frequency=freq, group_frequency=gfreq,
                         permutation=perm, selected=selected, fallback=fallback)


# ---------------------------------------------------------------------------------------------- across folds
def stability_tables(sels: list[FoldSelection], pool: list[str], groups: dict[str, str],
                     cfg: DiscoveryConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per feature and per group: frequency in each fold (columns f<k>), the mean, and stability (share of folds where
    the frequency reached freq_threshold). Sorted by stability, then mean frequency."""
    def table(keys: list[str], freqs: list[dict[str, float]], extra: dict[str, Any]) -> pd.DataFrame:
        df = pd.DataFrame({f"f{s.k}": [f.get(c, 0.0) for c in keys] for s, f in zip(sels, freqs)}, index=keys)
        fcols = list(df.columns)
        for name, col in extra.items():
            df.insert(0, name, col)
        df["mean_freq"] = df[fcols].mean(axis=1) if fcols else 0.0
        df["stability"] = (df[fcols] >= cfg.freq_threshold).mean(axis=1) if fcols else 0.0
        return df.sort_values(["stability", "mean_freq"], ascending=False, kind="stable")
    feat = table(pool, [s.frequency for s in sels], {"group": [groups[c] for c in pool]})
    gkeys = sorted({groups[c] for c in pool})
    size = pd.Series([groups[c] for c in pool]).value_counts()
    grp = table(gkeys, [s.group_frequency for s in sels], {"n_features": [int(size[g]) for g in gkeys]})
    return feat, grp


def auc_lower_bound(y: np.ndarray, p: np.ndarray, ts: pd.DatetimeIndex, n_boot: int = 500, seed: int = 0,
                    alpha: float = 0.05) -> float | None:
    """Lower alpha/2 quantile of the AUC under a weekly block bootstrap (candidates in a week resampled together)."""
    if len(np.unique(y)) < 2:
        return None
    from sklearn.metrics import roc_auc_score
    week = epoch_ns(ts) // WEEK_NS
    weeks, inv = np.unique(week, return_inverse=True)
    members = [np.flatnonzero(inv == i) for i in range(len(weeks))]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_boot):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(weeks), len(weeks))])
        if len(np.unique(y[idx])) == 2:
            out.append(roc_auc_score(y[idx], p[idx]))
    return float(np.quantile(out, alpha / 2)) if out else None


def reading_verdict(auc: float | None, auc_lb: float | None, n_scored: int, taken_mean_r: float | None,
                    n_taken: int) -> dict[str, Any]:
    """READING_RULE applied to the result."""
    if auc is None or auc < STOP_AUC:
        decision = "stop"
    elif (auc >= CONTINUE_AUC and auc_lb is not None and auc_lb > AUC_LOWER_BOUND and n_scored >= MIN_READ_EVENTS
          and n_taken > 0 and taken_mean_r is not None and taken_mean_r > 0):
        decision = "continue"
    else:
        decision = "inconclusive"
    return {"decision": decision, "rule": READING_RULE, "oof_auc": auc, "auc_lower_bound": auc_lb,
            "n_scored": int(n_scored), "n_taken": int(n_taken), "taken_mean_r": taken_mean_r}


# ---------------------------------------------------------------------------------------------- the trial
def run_discovery(agent_id: str, labels: pd.DataFrame, gross: pd.DataFrame, feats: pd.DataFrame, folds: list[Fold],
                  version: str, window: dict[str, Any], pool: list[str], cfg: DiscoveryConfig,
                  groups: dict[str, str] | None = None, group_mode: str = "column", *, n_trials: int = 1,
                  trades_per_year: float | None = None, extra_cost_usd: float = 0.0, holdout: Window | None = None,
                  swap: Any = None) -> DiscoveryResult:
    """Fold-internal stability selection, then the walk-forward of a model fitted in each fold on that fold's own
    selection (cross-fitted calibration and gates as every walk-forward). `labels` carries weight."""
    groups = groups or {c: c for c in pool}
    purge = int(window.get("purge_days", 0))
    sels = [select_in_fold(feats, labels, f, pool, cfg, groups, purge_days=purge) for f in folds]
    feat_t, grp_t = stability_tables(sels, pool, groups, cfg)
    surv = feat_t[feat_t["stability"] >= cfg.stability_threshold]
    selected = list(surv.index[: cfg.max_selected])
    surv_groups = list(grp_t.index[grp_t["stability"] >= cfg.stability_threshold])
    res = _walk_forward(agent_id, labels, gross, feats, ["side", *selected], folds, version, n_trials=n_trials,
                        trades_per_year=trades_per_year, extra_cost_usd=extra_cost_usd, holdout=holdout,
                        score_holdout=False, window=window, swap=swap,
                        fold_cols={s.k: ["side", *s.selected] for s in sels})
    oof = res.oof
    scored = oof[oof["p_raw"].notna()] if "p_raw" in oof else oof.iloc[0:0]
    taken = scored[scored["p"].notna() & scored["taken"]] if len(scored) else scored
    auc = _auc(scored["target_hit"].to_numpy(dtype=int), scored["p_raw"].to_numpy()) if len(scored) else None
    lb = (auc_lower_bound(scored["target_hit"].to_numpy(dtype=int), scored["p_raw"].to_numpy(dtype=float),
                          pd.DatetimeIndex(pd.to_datetime(scored["ts_utc"], utc=True)), seed=cfg.seed)
          if auc is not None else None)
    tk = expectancy(taken["ret"].to_numpy(), taken["risk"].to_numpy()) if len(taken) else {"n": 0}
    verdict = reading_verdict(auc, lb, len(scored), tk.get("mean_r"), int(tk.get("n", 0)))
    n_groups = len(set(groups[c] for c in pool))
    res.metrics["discovery"] = {
        "config": cfg.model_dump(), "group_mode": group_mode, "n_features_screened": len(pool),
        "n_groups_screened": n_groups, "selected": selected, "survivor_groups": surv_groups,
        "folds": [{"k": s.k, "n_train": s.n_train, "n_subsamples": s.n_subsamples, "selected": s.selected,
                   "fallback": s.fallback,
                   "frequency": {c: round(v, 4) for c, v in s.frequency.items() if v > 0},
                   "group_frequency": {g: round(v, 4) for g, v in s.group_frequency.items() if v > 0},
                   **({"permutation": {c: round(v, 6) for c, v in s.permutation.items()}} if s.permutation else {})}
                  for s in sels],
        "stability": {c: round(float(v), 4) for c, v in feat_t["stability"].items() if v > 0},
        "group_stability": {g: round(float(v), 4) for g, v in grp_t["stability"].items() if v > 0},
        "reading_verdict": verdict,
    }
    return DiscoveryResult(result=res, folds=sels, table=feat_t, group_table=grp_t, selected=selected,
                           survivor_groups=surv_groups, n_features_screened=len(pool), n_groups_screened=n_groups,
                           group_mode=group_mode, verdict=verdict)


def discovery_pool(prep: Prepared) -> list[str]:
    """Every eligible column of a prepared configuration except `side` (always an input)."""
    return [c for c in eligible_columns(prep.feats) if c != "side"]


def discover(prep: Prepared, cfg: DiscoveryConfig, groups: dict[str, str] | None = None, group_mode: str = "column",
             *, n_trials: int = 1, trades_per_year: float | None = None, extra_cost_usd: float = 0.0,
             holdout: Window | None = None) -> DiscoveryResult:
    """One discovery trial over a prepared specialist configuration on its walk-forward windows."""
    spec = prep.spec
    labels = prep.labels.copy()
    labels["weight"] = uniqueness_weights(labels, prep.n_bars).to_numpy()
    window = window_for(spec.timeframe, **spec.walkforward)
    folds = splits_for(labels, spec.timeframe, **spec.walkforward)
    pool = discovery_pool(prep)
    return run_discovery(spec.agent_id, labels, prep.gross, prep.feats, folds, prep.feature_version, window, pool, cfg,
                         groups, group_mode, n_trials=n_trials, trades_per_year=trades_per_year,
                         extra_cost_usd=extra_cost_usd, holdout=holdout, swap=prep.swap)


def discovery_family(family: str) -> str:
    """Registry family of a discovery trial: never a specialist family, so it is not evidence for promotion."""
    return f"discovery_{family}"


# ---------------------------------------------------------------------------------------------- report
def _fmt(v: Any) -> str:
    return "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def render_discovery(d: DiscoveryResult, meta: dict[str, Any], top: int = 25) -> str:
    """Markdown: header, reading verdict, group and feature stability tables, the design's gates."""
    m = d.result.metrics
    v = d.verdict
    lines = [f"## Feature discovery on {meta['specialist']} ({meta['from_year']}-{meta['to_year']}, {meta['tf']})", "",
             f"- registry trial #{meta['trial']} (status `discovery`, one trial), pre-registered at {meta['prereg_ts']}",
             f"- screened {d.n_features_screened} features in {d.n_groups_screened} groups (grouping: {d.group_mode}); "
             f"a later trial of a survivor uses n_trials_effective = {meta['n_trials_effective']} in its deflated Sharpe",
             f"- candidates {d.result.n_candidates:,}, folds {d.result.n_folds}; selection inside each fold on its "
             f"training rows only ({m['discovery']['config']['n_subsamples']} weekly-block half-samples, "
             f"{m['discovery']['config']['method']}, top {m['discovery']['config']['top_k']}"
             + (", permutation-confirmed" if m["discovery"]["config"]["permutation"] else "") + ")",
             f"- lookahead check: {meta.get('lookahead', 'n/a')}",
             f"- costs: bar spread in every label plus {m.get('extra_cost_usd', 0.0):.2f} $/oz round trip; "
             f"cost source: {meta.get('cost_source', 'settings priors')}", "",
             f"### Reading rule (pre-registered): **{v['decision'].upper()}**", "", f"- rule: {v['rule']}",
             f"- out-of-fold AUC {_fmt(v['oof_auc'])}, bootstrap 95% lower bound {_fmt(v['auc_lower_bound'])}, "
             f"{v['n_scored']:,} scored; model-filtered {v['n_taken']} trades, net mean R {_fmt(v['taken_mean_r'])}", ""]
    folds = [c for c in d.group_table.columns if c.startswith("f") and c[1:].isdigit()]
    lines += [f"### Stability by group (top {top})", "",
              "| group | features | " + " | ".join(folds) + " | mean | stability |",
              "|---|---:|" + "---:|" * (len(folds) + 2)]
    for g, r in d.group_table.head(top).iterrows():
        lines.append(f"| {g} | {int(r['n_features'])} | " + " | ".join(f"{r[c]:.2f}" for c in folds)
                     + f" | {r['mean_freq']:.2f} | {r['stability']:.2f} |")
    lines += ["", f"### Stability by feature (top {top})", "",
              "| feature | group | " + " | ".join(folds) + " | mean | stability |",
              "|---|---|" + "---:|" * (len(folds) + 2)]
    for c, r in d.table.head(top).iterrows():
        lines.append(f"| {c} | {r['group']} | " + " | ".join(f"{r[f]:.2f}" for f in folds)
                     + f" | {r['mean_freq']:.2f} | {r['stability']:.2f} |")
    cfg = m["discovery"]["config"]
    lines += ["", f"Frequency = share of a fold's subsamples with the feature in the top {cfg['top_k']} (a group: its "
                  f"most frequent member); stability = share of folds with frequency >= {cfg['freq_threshold']}.",
              f"Survivor groups (stability >= {cfg['stability_threshold']}): {', '.join(d.survivor_groups) or 'none'}",
              f"Selected set ({len(d.selected)} of at most {cfg['max_selected']} + side): {', '.join(d.selected) or 'none'}",
              ""]
    for name, g in (("Design gates", m.get("gates")), ("Design gates, rule-only (informational)", m.get("rule_only_gates"))):
        if g:
            lines += [f"### {name}: {'**PASS**' if g['passed'] else '**FAIL**'}", ""]
            lines += [f"- {'pass' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}" for c in g["checks"]] + [""]
    lines.append("A discovery trial is never promoted; survivors need their own pre-registered trial.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- the CLI's pass
def discovery_pass(spec: Any, b1: pd.DataFrame, reg: Any, *, budget_cap: int, cfg: DiscoveryConfig,
                   group_mode: str = "column", extra_cost_usd: float = 0.0, swap: Any = None,
                   holdout: Window | None = None, ctx: dict | None = None, rationale: str = "",
                   cost_source: str = "settings priors") -> tuple[dict[str, Any], str]:
    """`research_pass.py --discover`: budget check (one trial), the pre-registration row, then the run and its result
    row (status "discovery"). Call under `reg.locked()`. Returns (result row, report)."""
    from goldbot.data.resample import resample_bars
    from goldbot.features.mtf import TF_LABEL, context_tfs
    from goldbot.features.registry import feature_version
    from goldbot.research.pipeline import (
        build_decision_frame,
        lookahead_check,
        prepare,
        research_feature_names,
    )
    from goldbot.research.registry import DISCOVERY

    quarter = reg.check_budget(1, budget_cap)
    names = research_feature_names(ctx)
    family = discovery_family(spec.family)
    config = {"specialist": spec.family, "spec": spec.config, "discovery": cfg.model_dump(), "group_mode": group_mode,
              "features": names, "bars_from": str(b1["ts_utc"].iloc[0]), "bars_to": str(b1["ts_utc"].iloc[-1]),
              "holdout": None if holdout is None else [str(holdout[0]), str(holdout[1])]}
    prereg = reg.preregister(agent_id=spec.agent_id, family=family, config=config,
                             feature_version=feature_version(names), rationale=rationale, reading_rule=READING_RULE,
                             plan={"features": names, "group_mode": group_mode, "trial_status": DISCOVERY})
    tf = spec.timeframe
    b_dec = resample_bars(b1, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    leak = lookahead_check(b_dec, context, ctx=ctx)
    frame = build_decision_frame(b_dec, context, ctx=ctx)
    prep = prepare(spec, b_dec, context, extra_cost_usd=extra_cost_usd, holdout=holdout, frame=frame, swap=swap)
    if prep.labels.empty:
        raise ValueError(f"{spec.family} produced no labelled candidates; nothing to discover")
    pool = discovery_pool(prep)
    groups = column_groups(pool, group_mode, frame[0], names, ctx)
    n_eff = reg.n_trials_effective + 1
    d = discover(prep, cfg, groups, group_mode, n_trials=n_eff, extra_cost_usd=extra_cost_usd, holdout=holdout)
    metrics = {**d.result.metrics, "lookahead": leak, "cost_source": cost_source,
               "bars_from": config["bars_from"], "bars_to": config["bars_to"]}
    row = reg.record(agent_id=spec.agent_id, family=family, config=config, feature_version=d.result.feature_version,
                     rationale=rationale, results=metrics, status=DISCOVERY, budget_quarter=quarter,
                     preregistration=prereg)
    bad = leak["lookahead_columns"]
    text = render_discovery(d, {"specialist": spec.family, "from_year": int(b1["ts_utc"].iloc[0].year),
                                "to_year": int(b1["ts_utc"].iloc[-1].year), "tf": tf, "trial": row["trial"],
                                "prereg_ts": prereg["ts"], "n_trials_effective": reg.n_trials_effective,
                                "lookahead": "clean" if not bad else f"**{len(bad)} columns use future data**",
                                "cost_source": cost_source})
    return row, text
