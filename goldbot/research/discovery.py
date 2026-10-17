"""Feature discovery: ONE pre-registered trial that screens many candidate features (indicator survey 4b).

The library of point-in-time features can exceed the 40-feature cap of a live model; adding a feature is not a trial.
Choosing which features a model uses is a look at the data, so it happens inside the estimator, fold by fold:

* inputs: a specialist (its candidates and triple-barrier labels) and every eligible feature column (`eligible_columns`,
  `side` excluded: it is always an input);
* inside each purged walk-forward fold, on that fold's TRAINING rows only (the walk-forward's purge already removed
  every training label whose life reaches the test window), stability selection (Meinshausen & Buehlmann 2010):
  `n_subsamples` half-samples drawn by weekly block (serially correlated candidates stay together), a shallow
  LightGBM (or an L1-logistic regression) fitted on each, and a record of which features land in the top `top_k` by
  importance. A feature's frequency in the fold is the share of subsamples that put it in the top k; a group's is the
  share of subsamples in which its JOINT PERMUTATION importance (`group_permutation_importance`: the log-loss increase
  on the fold's training rows whose labels share no time with the subsample's when all its members are permuted
  together,
  averaged over `permutation_repeats` shuffles) ranks in the top
  `group_top_k` groups: size-neutral (dead members add nothing, so a large group gains nothing by size) and
  redundancy-safe (near-copies are permuted together, so splitting one signal does not deflate its group); ADR 0007
  amendment 2026-10-11. With one column per group the column's own importance is used (the same ranking).
  Optionally a confirmation by permutation importance on an inner validation split of the same training rows (the
  last `inner_val_frac` of them in time; inner training rows whose label reaches the validation start minus the
  walk-forward's purge are dropped, `inner_split`): the feature must also reduce the validation log-loss;
* the fold's model is then fitted on the fold's selection (frequency >= `freq_threshold`, confirmed, at most 39 plus
  `side`) and predicts the test fold, with the cross-fitted calibration, thresholds and gates of every walk-forward
  (`pipeline._walk_forward`). No test row ever influences which features its model uses. The trades are taken under
  the pre-registered `threshold_rule`: the coded break-even + 0.02 (default) or O-4's top-tercile cut of the earlier
  folds' out-of-fold p (E-2, `pipeline.fold_cuts`), recorded on the row with its cuts and retention;
* reported: per-feature and per-group frequency per fold, the mean across folds, and stability = the share of folds
  in which the frequency reached `freq_threshold`. Survivors (stability >= `stability_threshold`) are the candidates for
  later trials, in the pre-registered unit `survivor_unit`: "feature" (default; at most 39 features) or "group" (whole
  groups go forward; `selected` is then their members, at most 39).

Groups (`--families`): every column is its own group by default; `feature` groups columns by the registered feature
that produced them (h1_/h4_/d1_ context columns join their base feature, or with the pre-registered
`context_groups: per_timeframe` form one group `ctx_<tf>` per timeframe), `family` by the feature's registry family.
The column -> feature map is found by running each feature on the last bars BEFORE the holdout start (never a holdout
bar).

Trial accounting: the run is ONE registry trial (status "discovery"), written after a "preregistered" row that holds
its config and READING_RULE. Its own out-of-fold score is honest because selection sits inside the estimator; but
survivors are chosen from frequencies over the whole research window, so a later trial of a survivor uses
registry.n_trials_effective (registry trials + every discovery's `k_eff`) in its deflated Sharpe. `k_eff` is the number
of units a survivor was picked from: `n_features_screened` when survivors go forward as features (a feature-level
survivor beat every screened column, not just every group), `n_groups_screened` only when whole groups go forward.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
from fractions import Fraction
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from goldbot.base import FrozenRecord, Record
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features import FEATURES
from goldbot.features.registry import _CONTEXT_PREFIX, side_align
from goldbot.labels import uniqueness_weights
from goldbot.research.metrics import expectancy
from goldbot.research.model import MAX_FEATURES
from goldbot.research.pipeline import (
    DEFAULT_THRESHOLD_RULE,
    TERCILE_METHOD,
    TERCILE_Q,
    THRESHOLD_MARGIN,
    Prepared,
    ResearchResult,
    ThresholdRule,
    Window,
    _auc,
    _log_loss,
    _walk_forward,
    eligible_columns,
    mask_columns,
    selection_summary,
    uncovered_columns,
)
from goldbot.research.walkforward import Fold, splits_for, window_for

GroupMode = Literal["column", "feature", "family", "cluster"]
GROUP_MODES: tuple[str, ...] = ("column", "feature", "family", "cluster")
ContextGroups = Literal["join", "per_timeframe"]
# H-02's pre-registered hypothesis clusters (preregistration-2027Q1.md rows 6-9; group mode "cluster"; amended
# 2026-10-11 to follow hypotheses.md exactly): each entry is a registered feature (all its columns) or a column name or
# glob (context prefixes h1_/h4_/d1_/w1_ stripped); first match wins, in this order; every other column stays in its
# feature family's group (realised_vol and vol_estimators in volatility, swings and bos_* in structure). A cluster's
# group stability is what H-02's survivor slots rank by (director.slot_survivors).
H02_CLUSTERS: dict[str, list[str]] = {
    "H-03": ["macro_gvz*", "macro_vrp"],                            # gold VRP: GVZ and the GVZ^2 - HAR-RV^2 spread
    "H-05": ["round_numbers", "support_resistance",                 # stop-cluster levels: rounds, S/R, prior day/week
             "pdh_dist_atr", "pdl_dist_atr", "pwh_dist_atr", "pwl_dist_atr"],
    "H-07": ["macro_vix*", "macro_spx*"],                           # equity-stress haven
    "H-08": ["sweep_*", "sess_high_dist_atr", "sess_low_dist_atr",  # sweeps of the session / Asia / London extremes
             "prev_sess_high_dist_atr", "prev_sess_low_dist_atr", "asia_high_dist_atr", "asia_low_dist_atr",
             "london_high_dist_atr", "london_low_dist_atr"],
    "H-10": ["jumps"],                                              # post-jump behaviour: jumps only (no news split)
}


def cluster_of(column: str, owner: str | None, clusters: dict[str, list[str]]) -> str | None:
    """The first cluster with an entry naming `column`'s producing feature (`owner`) or matching the column (context
    prefix stripped) as a name or glob, else None."""
    base = _CONTEXT_PREFIX.sub("", column)
    return next((cid for cid, entries in clusters.items()
                 if any(e == owner or fnmatch.fnmatchcase(base, e) for e in entries)), None)
WEEK_NS = 7 * 86_400 * 10**9

# Reading rule, written into the pre-registration before the run (survey 4b, decided in advance)
CONTINUE_AUC = 0.53
STOP_AUC = 0.52
AUC_LOWER_BOUND = 0.50
MIN_READ_EVENTS = 1000
BREAK_EVEN_RULE = "break_even_margin"      # the coded rule, as a literal (the dump keys on it)
assert DEFAULT_THRESHOLD_RULE == BREAK_EVEN_RULE, "the default threshold rule must stay the coded break-even rule"


BREAK_EVEN_CLAUSE = "(cross-fitted, break-even + margin)"
# E-2 (quant plan review condition 3): the tercile rule REPLACES the break-even clause, so the default string stays
# byte-identical (the director compares cited_rule == the pre-registration's reading_rule) ...


def _q_text(q: float) -> str:
    """q as written in the rule: a simple fraction when it is one (2/3), else its repr."""
    f = Fraction(q).limit_denominator(1000)
    return f"{f.numerator}/{f.denominator}" if float(f) == q else repr(q)


def tercile_clause(q: float = TERCILE_Q, method: str = TERCILE_METHOD) -> str:
    """The tercile rule's clause, built from the pinned q and quantile method (both part of the rule). O-4's rule
    reads "net mean R > 0 on >= 1,000 trades" (quant review R1, fix 1): the clause names the trade floor."""
    return (f"(top-tercile cut, E-2: test-fold raw p >= the {_q_text(q)} quantile ({method}) of the earlier folds' "
            f"out-of-fold p inside the fold's training window; no break-even floor) "
            f"(at least {MIN_READ_EVENTS:,} taken)")


TERCILE_CLAUSE = tercile_clause()
# ... and adds one sentence (quant ruling 2: live trading keeps break-even + 0.02)
LIVE_RULE_NOTE = (f"A result under the tercile rule does not carry to live trading (break-even + {THRESHOLD_MARGIN}): "
                  f"promotion needs a separate trial under the live rule.")


def reading_rule(survivor_unit: str = "feature", threshold_rule: ThresholdRule = DEFAULT_THRESHOLD_RULE,
                 tercile_q: float = TERCILE_Q) -> str:
    """The pre-registered reading rule; the survivor unit (and so the K_eff a later trial pays) and the threshold rule
    (`pipeline.select`; under the tercile rule its q and quantile method, and the 1,000-trade floor) are part of it."""
    tercile = threshold_rule == "tercile_inner"
    unit = ("survivors are features: individual features whose fold frequency reaches the threshold in >= the "
            "stability share of folds; at most five go forward, and every later trial of one uses n_trials_effective "
            "(registry trials + features screened)"
            if survivor_unit == "feature" else
            "survivors are whole groups: groups whose fold frequency (joint permutation importance in the top groups) "
            "reaches the threshold in >= the stability share of folds; at most five go forward as whole groups, and "
            "every later trial of one uses n_trials_effective (registry trials + groups screened)")
    return (
    f"continue if the pooled out-of-fold AUC >= {CONTINUE_AUC} with a weekly-block bootstrap 95% lower bound > "
    f"{AUC_LOWER_BOUND}, at least {MIN_READ_EVENTS:,} candidates scored out of fold, and the model-filtered "
    f"{tercile_clause(tercile_q) if tercile else BREAK_EVEN_CLAUSE} trades have net mean R > 0; stop if the AUC < "
    f"{STOP_AUC} (the screened "
    f"features carry no information for this label: keep them as risk and cost filters only); otherwise inconclusive. "
    f"Survivor unit: {unit} in its deflated Sharpe, and must also pass the holdout rule before promotion. The design's "
    f"gates are reported; a discovery is never promoted." + (f" {LIVE_RULE_NOTE}" if tercile else ""))


READING_RULE = reading_rule("feature")

SELECT_PARAMS: dict[str, Any] = dict(
    objective="binary", learning_rate=0.05, num_leaves=7, min_child_samples=40, feature_fraction=0.8,
    bagging_fraction=1.0, lambda_l2=5.0, n_estimators=100, verbose=-1, n_jobs=1,
)


class DiscoveryConfig(FrozenRecord):
    """Pre-registered settings of one discovery trial."""
    n_subsamples: int = 50              # half-samples per fold
    subsample_frac: float = 0.5         # share of the training fold's weeks in each subsample
    top_k: int = 10                     # a subsample "selects" its top_k features by importance
    group_top_k: int | None = None      # ... and its top groups by joint permutation importance (None: top_k's share)
    survivor_unit: Literal["feature", "group"] = "feature"   # what goes forward; sets K_eff (features or groups)
    method: Literal["lgbm", "l1"] = "lgbm"
    l1_c: float = 0.05                  # inverse L1 strength (method "l1"; standardised inputs)
    freq_threshold: float = 0.6         # selected in a fold when its top-k frequency reaches this
    stability_threshold: float = 0.7    # survivor: frequency reached the threshold in at least this share of folds
    permutation: bool = False           # confirm by permutation importance on an inner validation split
    inner_val_frac: float = 0.2
    permutation_repeats: int = 3
    max_selected: int = MAX_FEATURES - 1   # `side` is always the 40th input
    seed: int = 0
    clusters: dict[str, list[str]] | None = None   # group mode "cluster": the pre-registered map (e.g. H02_CLUSTERS)
    # the model-stage threshold (pipeline.select): "break_even_margin" (the coded rule) or O-4's "tercile_inner" (E-2)
    threshold_rule: ThresholdRule = DEFAULT_THRESHOLD_RULE
    # the tercile rule's q and quantile method: part of the rule, so in the dump and the config hash under it (quant
    # review R1, fix 2: any other q is a new trial); left at the constants under the break-even rule
    tercile_q: float = Field(TERCILE_Q, gt=0.0, lt=1.0)
    tercile_method: Literal["linear"] = TERCILE_METHOD
    # where h1_/h4_/d1_/w1_ context columns go (`column_groups`): "join" their base column's group (the coded rule) or,
    # O-4's pinned splitting rule (BACKLOG 81), "per_timeframe": one group `ctx_<tf>` per context timeframe across
    # families; in the dump and the config hash only when "per_timeframe", so every existing hash holds
    context_groups: ContextGroups = "join"
    # H-02 amendment 2026-10-17 (BACKLOG 82): columns that are NaN in both train and test of any fold whose training
    # rows do not all carry a finite value ("full coverage"; `pipeline.uncovered_columns`), e.g. ("macro_spx_dd20",)
    # so H-07 is VIX-only there; in the dump and the config hash only when set, so every existing hash holds
    full_coverage_columns: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _q_only_under_tercile(self) -> DiscoveryConfig:
        if self.threshold_rule == BREAK_EVEN_RULE and (self.tercile_q != TERCILE_Q
                                                       or self.tercile_method != TERCILE_METHOD):
            raise ValueError("tercile_q / tercile_method are set but threshold_rule is break_even_margin: they would "
                             "be ignored; pre-register threshold_rule tercile_inner or leave them at their defaults")
        return self

    @model_serializer(mode="wrap")
    def _dump(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Under the break-even rule (the literal "break_even_margin", whatever DEFAULT_THRESHOLD_RULE says) the rule,
        q and method are left out of the dump, so such a config dumps and hashes exactly as before E-2 (H-02's pinned
        dump and every queued config hash hold); under the tercile rule all three are in the dump and so in the
        pre-registered config hash: another rule or another q is another trial. `context_groups` likewise: left out
        at "join", in the dump (and the hash) at "per_timeframe"; `full_coverage_columns` left out when empty."""
        out: dict[str, Any] = handler(self)
        if self.threshold_rule == BREAK_EVEN_RULE:
            for k in ("threshold_rule", "tercile_q", "tercile_method"):
                out.pop(k, None)
        if self.context_groups == "join":
            out.pop("context_groups", None)
        if not self.full_coverage_columns:
            out.pop("full_coverage_columns", None)
        return out


def threshold_plan(cfg: DiscoveryConfig) -> dict[str, Any]:
    """The threshold rule's pins for a discovery's pre-registration plan (and its result row): rule, q and quantile
    method under the tercile rule, the margin under the break-even rule."""
    tercile = cfg.threshold_rule == "tercile_inner"
    return {"threshold_rule": cfg.threshold_rule, "tercile_q": cfg.tercile_q if tercile else None,
            "tercile_method": cfg.tercile_method if tercile else None, "margin": None if tercile else THRESHOLD_MARGIN}


class FoldSelection(Record):
    k: int
    n_train: int
    n_subsamples: int                   # subsamples actually fitted (a one-class subsample is skipped)
    frequency: dict[str, float]         # feature -> share of subsamples with it in the top k
    group_frequency: dict[str, float]   # group -> share of subsamples with its joint permutation importance in the top
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
    survivor_unit: str
    k_eff: int                          # n_features_screened or n_groups_screened, by survivor_unit
    group_mode: str
    verdict: dict[str, Any]


# ---------------------------------------------------------------------------------------------- groups
def column_groups(columns: list[str], mode: str, m: pd.DataFrame | None = None, names: list[str] | None = None,
                  ctx: dict | None = None, holdout_start: pd.Timestamp | None = None,
                  clusters: dict[str, list[str]] | None = None,
                  context_groups: ContextGroups = "join") -> dict[str, str]:
    """Group of each column. "column": itself; "feature" / "family": the registered feature that produced it (found by
    running each feature on the last 2,000 bars of `m` BEFORE `holdout_start`, so no holdout bar is read) or that
    feature's family; "cluster": the first pre-registered cluster (`clusters`, e.g. H02_CLUSTERS) with an entry
    naming the column's feature or matching it as a glob, else its family. A column no feature claims is its own
    group. A context column (h1_/h4_/d1_/w1_) joins its base column's group under `context_groups` "join"; under
    "per_timeframe" (O-4's pinned splitting rule, preregistration-2027Q1.md: "every context column goes to the group
    `ctx_<tf>` of its timeframe (`ctx_h1`, `ctx_h4`, `ctx_d1`), across families") it goes to `ctx_<tf>`, so at 15m
    the h1/h4/d1 copies of trend and volatility no longer push their families past max_selected."""
    if mode not in GROUP_MODES:
        raise ValueError(f"unknown group mode {mode!r}; one of {GROUP_MODES}")
    if mode == "column":
        return {c: c for c in columns}
    if mode == "cluster" and not clusters:
        raise ValueError("group mode 'cluster' needs the pre-registered clusters map (DiscoveryConfig.clusters)")
    owner: dict[str, str] = {}
    if m is not None:
        if holdout_start is not None:
            m = m[np.asarray(pd.to_datetime(m["ts_utc"], utc=True) < holdout_start)]
            if m.empty:
                raise ValueError(f"no bar before the holdout start {holdout_start} to map columns to features")
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
        tf = _CONTEXT_PREFIX.match(c)
        if context_groups == "per_timeframe" and tf is not None:
            out[c] = f"ctx_{tf.group(1)}"
            continue
        who = owner.get(c) or owner.get(_CONTEXT_PREFIX.sub("", c))
        hit = cluster_of(c, who, clusters or {})
        if mode == "cluster" and hit is not None:
            out[c] = hit
        else:
            out[c] = c if who is None else (who if mode == "feature" else FEATURES[who].family)
    return out


# ---------------------------------------------------------------------------------------------- fold selection
def _design(X: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """The selection model's inputs: side-aligned like the meta-model's when `side` is present."""
    if "side" in X.columns:
        return side_align(X[["side", *cols]])
    return X[cols]


Predict = Callable[[pd.DataFrame], np.ndarray]


def _fit_model(Z: pd.DataFrame, y: np.ndarray, w: np.ndarray, cols: list[str], cfg: DiscoveryConfig,
               seed: int) -> tuple[np.ndarray, Predict]:
    """(importance of each of `cols`: gain for LightGBM, |coefficient| for L1-logistic, the fitted model's
    probability of a target hit) on one subsample."""
    if cfg.method == "lgbm":
        import lightgbm as lgb
        mdl = lgb.LGBMClassifier(**{**SELECT_PARAMS, "random_state": seed}).fit(Z, y, sample_weight=w)
        imp = pd.Series(mdl.booster_.feature_importance("gain"), index=list(Z.columns))
        return (imp.reindex(cols).fillna(0.0).to_numpy(dtype=float),
                lambda X: np.asarray(mdl.predict_proba(X))[:, 1])
    from sklearn.linear_model import LogisticRegression
    A = Z.apply(pd.to_numeric, errors="coerce").astype(float)
    med = A.median()
    A = A.fillna(med).fillna(0.0)
    mu, sd = A.mean(), A.std().replace(0.0, 1.0)
    lr = LogisticRegression(penalty="l1", solver="liblinear", C=cfg.l1_c, random_state=seed)
    lr.fit(((A - mu) / sd).to_numpy(), y, sample_weight=w)
    coef = pd.Series(np.abs(lr.coef_[0]), index=list(Z.columns))

    def predict(X: pd.DataFrame) -> np.ndarray:
        B = X.apply(pd.to_numeric, errors="coerce").astype(float).fillna(med).fillna(0.0)
        return np.asarray(lr.predict_proba(((B - mu) / sd).to_numpy()))[:, 1]
    return coef.reindex(cols).fillna(0.0).to_numpy(dtype=float), predict


def purged_rest(ts: pd.DatetimeIndex, te: pd.DatetimeIndex, subsample: np.ndarray) -> np.ndarray:
    """Indices of the training fold's rows outside the subsample (`subsample`: boolean mask) whose label interval
    [ts, te] overlaps no subsample row's: the rows the group measure is scored on (fold-internal; purged by label
    exit times, so only rows that share label time with the subsample are dropped)."""
    s, e = epoch_ns(ts), epoch_ns(te)
    cand = np.flatnonzero(~subsample)
    if not subsample.any():
        return cand
    order = np.argsort(s[subsample], kind="stable")
    ss, se = s[subsample][order], np.maximum.accumulate(e[subsample][order])
    # the latest subsample interval starting at or before the row's exit; the running max of exits covers the union
    k = np.searchsorted(ss, e[cand], side="right") - 1
    overlap = (k >= 0) & (se[np.clip(k, 0, None)] >= s[cand])
    return cand[~overlap]


def group_permutation_importance(predict: Predict, Z: pd.DataFrame, y: np.ndarray, members: list[list[int]],
                                 rng: np.random.Generator, repeats: int = 1) -> np.ndarray:
    """Per group: the log-loss increase on `Z` when every member column is permuted with ONE shared row permutation
    (joint permutation importance). Size-neutral: dead members change nothing, so padding a group does not raise it;
    redundancy-safe: near-copies are permuted together, so splitting one signal across copies does not lower it (ADR
    0007 amendment, 2026-10-11). Averaged over `repeats` shuffles drawn in turn from `rng` (cfg.permutation_repeats).
    0 for a group with no member."""
    yv = y.astype(float)
    base = _log_loss(yv, predict(Z))
    out = np.zeros(len(members))
    for j, cols in enumerate(members):
        if not cols:
            continue
        inc = []
        for _ in range(max(int(repeats), 1)):
            perm = rng.permutation(len(Z))
            Zp = Z.copy()
            Zp.iloc[:, cols] = Z.iloc[perm, cols].to_numpy()
            inc.append(_log_loss(yv, predict(Zp)) - base)
        out[j] = float(np.mean(inc))
    return out


def inner_split(ts: pd.DatetimeIndex, te: pd.DatetimeIndex, inner_val_frac: float,
                purge_days: int) -> tuple[np.ndarray, np.ndarray]:
    """(fit, validation) masks of the inner time split of a training fold: the last inner_val_frac by signal time
    validate; an earlier row fits only if its label ends before the validation start minus the purge."""
    cut = ts.sort_values()[int(len(ts) * (1 - inner_val_frac))]
    val = np.asarray(ts >= cut)
    fit = np.asarray(ts < cut) & np.asarray(te < cut - pd.Timedelta(days=purge_days))
    return fit, val


def _permutation_importance(X: pd.DataFrame, y: np.ndarray, w: np.ndarray, ts: pd.DatetimeIndex,
                            te: pd.DatetimeIndex, cols: list[str], cfg: DiscoveryConfig, purge_days: int,
                            rng: np.random.Generator) -> dict[str, float]:
    """Mean validation log-loss increase when each column is permuted, on an inner time split of the training rows:
    the last inner_val_frac by signal time validate; inner training rows whose label ends after the validation start
    minus the purge are dropped. Columns are permuted before side alignment, as the raw feature would be."""
    import lightgbm as lgb
    fit, val = inner_split(ts, te, cfg.inner_val_frac, purge_days)
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


GROUP_TOP_K_FLOOR = 3          # groups a subsample selects at least (when that many exist)


def group_top_k(cfg: DiscoveryConfig, n_features: int, n_groups: int) -> int:
    """Groups a subsample "selects" by joint permutation importance: cfg.group_top_k, else top_k's share of the pool applied to
    the groups (ceil(top_k * n_groups / n_features)), with a floor of GROUP_TOP_K_FLOOR when several groups exist: with
    one group per subsample the frequencies would sum to at most 1 and a second real group could never survive the
    0.6 threshold (quant review of dab47c7)."""
    if cfg.group_top_k is not None:
        return max(1, min(cfg.group_top_k, n_groups))
    share = math.ceil(cfg.top_k * n_groups / max(n_features, 1))
    return max(1, min(n_groups, max(share, GROUP_TOP_K_FLOOR)))


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
    gkeys = list(dict.fromkeys(groups[c] for c in pool))
    gpos = {g: j for j, g in enumerate(gkeys)}
    gidx = np.array([gpos[groups[c]] for c in pool])
    gk = group_top_k(cfg, len(pool), len(gkeys))
    singletons = len(gkeys) == len(pool)
    ghits = dict.fromkeys(gkeys, 0)
    Z_all = _design(X, pool)
    zpos = {c: j for j, c in enumerate(Z_all.columns)}         # `side` may lead the design matrix
    members = [[zpos[c] for c in pool if groups[c] == g] for g in gkeys]
    fitted = 0
    for s in range(cfg.n_subsamples):
        picked = rng.choice(weeks, size=n_pick, replace=False)
        rows = np.flatnonzero(np.isin(week, picked))
        if len(np.unique(y[rows])) < 2:
            continue
        seed = cfg.seed * 1000 + fold.k * 100 + s
        imp, predict = _fit_model(Z_all.iloc[rows], y[rows], w[rows], pool, cfg, seed=seed)
        order = [i for i in np.argsort(-imp, kind="stable")[: cfg.top_k] if imp[i] > 0]
        for i in order:
            hits[pool[i]] += 1
        if singletons:          # one column per group: the column's own importance is its group's (size-neutral)
            gimp: np.ndarray = np.bincount(gidx, weights=imp, minlength=len(gkeys)).astype(float)
        else:                   # joint permutation importance on the training fold's rows outside the subsample
            sub = np.zeros(len(y), dtype=bool)
            sub[rows] = True
            rest = purged_rest(ts, te, sub)                  # purged by label exit times, fold-internal
            gimp = (group_permutation_importance(predict, Z_all.iloc[rest], y[rest], members,
                                                 np.random.default_rng([cfg.seed, fold.k, s]),
                                                 repeats=cfg.permutation_repeats)
                    if len(rest) and len(np.unique(y[rest])) == 2 else np.zeros(len(gkeys)))
        for j in np.argsort(-gimp, kind="stable")[:gk]:
            if gimp[j] > 0:
                ghits[gkeys[j]] += 1
        fitted += 1
    denom = max(fitted, 1)
    freq = {c: hits[c] / denom for c in pool}
    gfreq = {g: ghits[g] / denom for g in gkeys}
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


def group_map_hash(groups: dict[str, str]) -> str:
    """sha256 of the column -> group map (sorted JSON): frozen in a group-unit pre-registration (quant ruling Q-3) and
    recorded on the discovery row, so a different grouping at run time is visible as a deviation."""
    return hashlib.sha256(json.dumps(groups, sort_keys=True).encode()).hexdigest()


def check_threshold_config(cfg: DiscoveryConfig, window: dict[str, Any]) -> None:
    """Before the budget check, the pre-registration and any look (quant review R1, fix 3): under the tercile rule the
    walk-forward's test windows must not overlap (`step_months` >= `test_months`), or `pipeline.fold_cuts` would
    refuse the run after its rows were written. ValueError otherwise; the break-even rule is not affected."""
    if cfg.threshold_rule == "tercile_inner" and int(window["step_months"]) < int(window["test_months"]):
        raise ValueError(f"threshold_rule tercile_inner needs non-overlapping test folds, but step_months "
                         f"{window['step_months']} < test_months {window['test_months']}: the earlier folds' "
                         f"out-of-fold p would hold the fold's own test rows")


def check_group_config(cfg: DiscoveryConfig) -> None:
    """A group-unit discovery pre-registers `group_top_k` explicitly (quant ruling Q-3; ValueError otherwise)."""
    if cfg.survivor_unit == "group" and cfg.group_top_k is None:
        raise ValueError("survivor_unit 'group' needs group_top_k set explicitly in the pre-registered config "
                         "(quant ruling Q-3): it decides the group frequencies the survivors are read from")


def check_group_sizes(groups: dict[str, str], cfg: DiscoveryConfig, group_mode: str) -> None:
    """Before any look at the data (quant re-review, MEDIUM 4): when whole groups go forward (survivor_unit "group")
    or a cluster becomes a survivor trial's inputs (group mode "cluster"), every group that can go forward (in cluster mode with feature unit: each cluster) must fit one model:
    <= max_selected columns besides `side` (ValueError naming the group otherwise). `discovery_pass` runs it on the
    decision frame's full map, context columns included, before the pre-registration row is written (BACKLOG 81); a
    group over the cap is refused, never truncated or re-split after a count."""
    if cfg.survivor_unit != "group" and group_mode != "cluster":
        return
    sizes: dict[str, int] = {}
    for g in groups.values():
        sizes[g] = sizes.get(g, 0) + 1
    if cfg.survivor_unit != "group":     # cluster mode, feature unit: only a cluster becomes a trial's inputs
        sizes = {g: n for g, n in sizes.items() if g in (cfg.clusters or {})}
    big = {g: n for g, n in sizes.items() if n > cfg.max_selected}
    if big:
        raise ValueError(f"{', '.join(f'{g} has {n} columns' for g, n in big.items())}, over max_selected "
                         f"{cfg.max_selected}: a whole group must fit one model (quant ruling Q-3); pre-register "
                         f"smaller groups")


def check_cluster_columns(feats: pd.DataFrame, pool: list[str], groups: dict[str, str], cfg: DiscoveryConfig,
                          group_mode: str) -> dict[str, dict[str, int]] | None:
    """Group mode "cluster", before any look at the labels (quant re-review, freeze item): every pre-registered cluster
    has at least one column in the pool that is not all NaN and not constant on the candidate rows (feature values
    only; with macro opted in, H-03 and H-07 have their GVZ/VIX columns). Returns {cluster: {columns, informative}},
    recorded on the discovery row; ValueError naming each cluster without one (or None outside cluster mode)."""
    if group_mode != "cluster":
        return None
    out: dict[str, dict[str, int]] = {}
    for cid in cfg.clusters or {}:
        cols = [c for c in pool if groups.get(c) == cid]
        good = 0
        for c in cols:
            v = pd.to_numeric(feats[c], errors="coerce").dropna()
            good += int(v.nunique() > 1)
        out[cid] = {"columns": len(cols), "informative": good}
    empty = [cid for cid, c in out.items() if c["informative"] == 0]
    if empty:
        raise ValueError(f"{', '.join(f'{cid} has no non-constant, non-NaN column' for cid in empty)} in the pool "
                         f"(macro opted in? feature set as pre-registered?): every H-02 cluster must be screenable")
    return out


def survivor_selection(feat_t: pd.DataFrame, grp_t: pd.DataFrame,
                       cfg: DiscoveryConfig) -> tuple[list[str], list[str], dict[str, int], list[str]]:
    """(groups forward, the selected features, n_features per group forward, survivor groups skipped).

    Feature unit: the features with stability >= stability_threshold, best first, at most max_selected (K_eff = every
    feature screened, so cutting the list costs nothing in honesty); nothing is skipped. Group unit (quant ruling Q-3,
    re-review MEDIUM 4): the survivor groups go forward WHOLE in group-stability order while their members fit within
    max_selected; the first that does not fit and every one after it are recorded as skipped, never truncated (a
    partial group would be a feature-level choice that K_eff = groups screened does not pay for). A group-unit config
    pre-registers `group_top_k` explicitly (ValueError otherwise)."""
    surv_all = [str(g) for g in grp_t.index[grp_t["stability"] >= cfg.stability_threshold]]
    sizes = {str(g): n for g, n in grp_t["n_features"].items()}
    if cfg.survivor_unit == "group":
        check_group_config(cfg)
        forward: list[str] = []
        total = 0
        for g in surv_all:
            if total + int(sizes[g]) > cfg.max_selected:
                break
            forward.append(g)
            total += int(sizes[g])
        skipped = surv_all[len(forward):]
        surv = feat_t[feat_t["group"].isin(forward)]
        return forward, [str(c) for c in surv.index], {g: int(sizes[g]) for g in forward}, skipped
    surv = feat_t[feat_t["stability"] >= cfg.stability_threshold]
    return surv_all, [str(c) for c in surv.index[: cfg.max_selected]], {g: int(sizes[g]) for g in surv_all}, []


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
                    n_taken: int, rule: str = READING_RULE, min_taken: int = 1) -> dict[str, Any]:
    """READING_RULE applied to the result. `min_taken`: the taken trades "continue" needs, 1 under the coded rule and
    MIN_READ_EVENTS under the tercile rule (O-4: "net mean R > 0 on >= 1,000 trades"; quant review R1, fix 1)."""
    if auc is None or auc < STOP_AUC:
        decision = "stop"
    elif (auc >= CONTINUE_AUC and auc_lb is not None and auc_lb > AUC_LOWER_BOUND and n_scored >= MIN_READ_EVENTS
          and n_taken >= max(min_taken, 1) and taken_mean_r is not None and taken_mean_r > 0):
        decision = "continue"
    else:
        decision = "inconclusive"
    return {"decision": decision, "rule": rule, "oof_auc": auc, "auc_lower_bound": auc_lb,
            "n_scored": int(n_scored), "n_taken": int(n_taken), "taken_mean_r": taken_mean_r}


# ---------------------------------------------------------------------------------------------- the trial
def run_discovery(agent_id: str, labels: pd.DataFrame, gross: pd.DataFrame, feats: pd.DataFrame, folds: list[Fold],
                  version: str, window: dict[str, Any], pool: list[str], cfg: DiscoveryConfig,
                  groups: dict[str, str] | None = None, group_mode: str = "column", *, n_trials: int = 1,
                  trades_per_year: float | None = None, extra_cost_usd: float = 0.0, holdout: Window | None = None,
                  swap: Any = None) -> DiscoveryResult:
    """Fold-internal stability selection, then the walk-forward of a model fitted in each fold on that fold's own
    selection (cross-fitted calibration and gates as every walk-forward). `labels` carries weight."""
    check_group_config(cfg)              # before any look at the data
    check_threshold_config(cfg, window)
    groups = groups or {c: c for c in pool}
    check_group_sizes(groups, cfg, group_mode)
    cluster_columns = check_cluster_columns(feats, pool, groups, cfg, group_mode)
    cover = cfg.full_coverage_columns
    if cover:
        uncovered_columns(feats, np.array([], dtype=int), cover)     # a column the frame lacks: refused before any look
    purge = int(window.get("purge_days", 0))
    # BACKLOG 82: a fold's selection sees an uncovered column as NaN, as its model will (`pipeline._walk_forward`)
    sels = [select_in_fold(mask_columns(feats, uncovered_columns(feats, f.train_idx, cover) if cover else []), labels,
                           f, pool, cfg, groups, purge_days=purge) for f in folds]
    feat_t, grp_t = stability_tables(sels, pool, groups, cfg)
    surv_groups, selected, surv_n, skipped = survivor_selection(feat_t, grp_t, cfg)
    res = _walk_forward(agent_id, labels, gross, feats, ["side", *selected], folds, version, n_trials=n_trials,
                        trades_per_year=trades_per_year, extra_cost_usd=extra_cost_usd, holdout=holdout,
                        score_holdout=False, window=window, timeframe=None, swap=swap,
                        fold_cols={s.k: ["side", *s.selected] for s in sels}, threshold_rule=cfg.threshold_rule,
                        tercile_q=cfg.tercile_q, full_coverage_columns=cover)
    oof = res.oof
    selection = res.metrics.get("selection") or selection_summary(oof, folds, cfg.threshold_rule,
                                                                  tercile_q=cfg.tercile_q).model_dump()
    scored = oof[oof["p_raw"].notna()] if "p_raw" in oof else oof.iloc[0:0]
    taken = scored[scored["p"].notna() & scored["taken"]] if len(scored) else scored
    auc = _auc(scored["target_hit"].to_numpy(dtype=int), scored["p_raw"].to_numpy()) if len(scored) else None
    lb = (auc_lower_bound(scored["target_hit"].to_numpy(dtype=int), scored["p_raw"].to_numpy(dtype=float),
                          pd.DatetimeIndex(pd.to_datetime(scored["ts_utc"], utc=True)), seed=cfg.seed)
          if auc is not None else None)
    tk = expectancy(taken["ret"].to_numpy(), taken["risk"].to_numpy()) if len(taken) else {"n": 0}
    verdict = reading_verdict(auc, lb, len(scored), tk.get("mean_r"), int(tk.get("n", 0)),
                              rule=reading_rule(cfg.survivor_unit, cfg.threshold_rule, cfg.tercile_q),
                              min_taken=MIN_READ_EVENTS if cfg.threshold_rule == "tercile_inner" else 1)
    n_groups = len(set(groups[c] for c in pool))
    k_eff = len(pool) if cfg.survivor_unit == "feature" else n_groups
    res.metrics["discovery"] = {
        "config": cfg.model_dump(), "group_mode": group_mode, "n_features_screened": len(pool),
        "n_groups_screened": n_groups, "survivor_unit": cfg.survivor_unit, "k_eff": k_eff, "selected": selected,
        "survivor_groups": surv_groups, "survivor_groups_n_features": surv_n, "skipped_survivor_groups": skipped,
        "group_map_sha256": group_map_hash(groups), "group_measure": "joint_permutation",
        **({"cluster_columns": cluster_columns} if cluster_columns is not None else {}),
        "group_members": {g: [c for c in pool if groups[c] == g] for g in dict.fromkeys(groups[c] for c in pool)},
        "folds": [{"k": s.k, "n_train": s.n_train, "n_subsamples": s.n_subsamples, "selected": s.selected,
                   "fallback": s.fallback,
                   "frequency": {c: round(v, 4) for c, v in s.frequency.items() if v > 0},
                   "group_frequency": {g: round(v, 4) for g, v in s.group_frequency.items() if v > 0},
                   **({"permutation": {c: round(v, 6) for c, v in s.permutation.items()}} if s.permutation else {})}
                  for s in sels],
        "stability": {c: round(float(v), 4) for c, v in feat_t["stability"].items() if v > 0},
        "group_stability": {g: round(float(v), 4) for g, v in grp_t["stability"].items() if v > 0},
        "reading_verdict": verdict,
        **selection,      # threshold_rule, tercile_q, tercile_method, margin, fold_cuts, retention, share below b/e
    }
    return DiscoveryResult(result=res, folds=sels, table=feat_t, group_table=grp_t, selected=selected,
                           survivor_groups=surv_groups, n_features_screened=len(pool), n_groups_screened=n_groups,
                           survivor_unit=cfg.survivor_unit, k_eff=k_eff, group_mode=group_mode, verdict=verdict)


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


def _threshold_line(disc: dict[str, Any]) -> str:
    """The threshold rule, its pins and the retention it gave (recorded, never read)."""
    ret = (disc.get("retention") or {}).get("overall")
    kept = "—" if ret is None else f"{ret:.1%}"
    if disc.get("threshold_rule") != "tercile_inner":
        return f"calibrated p > break-even + {disc.get('margin', THRESHOLD_MARGIN)} (retention {kept})"
    below = disc.get("share_taken_below_breakeven")
    return (f"top tercile: raw p >= the {disc['tercile_q']:.4g} quantile ({disc['tercile_method']}) of the earlier "
            f"folds' out-of-fold p in the fold's training window; retention {kept}; taken below break-even "
            f"{'—' if below is None else f'{below:.1%}'} (diagnostic)")


def render_discovery(d: DiscoveryResult, meta: dict[str, Any], top: int = 25) -> str:
    """Markdown: header, reading verdict, group and feature stability tables, the design's gates."""
    m = d.result.metrics
    v = d.verdict
    lines = [f"## Feature discovery on {meta['specialist']} ({meta['from_year']}-{meta['to_year']}, {meta['tf']})", "",
             f"- registry trial #{meta['trial']} (status `discovery`, one trial), pre-registered at {meta['prereg_ts']}",
             f"- screened {d.n_features_screened} features in {d.n_groups_screened} groups (grouping: {d.group_mode}); "
             f"survivors go forward as {d.survivor_unit}s, so K_eff = {d.k_eff} and a later trial of a survivor uses "
             f"n_trials_effective = {meta['n_trials_effective']} in its deflated Sharpe",
             f"- candidates {d.result.n_candidates:,}, folds {d.result.n_folds}; selection inside each fold on its "
             f"training rows only ({m['discovery']['config']['n_subsamples']} weekly-block half-samples, "
             f"{m['discovery']['config']['method']}, top {m['discovery']['config']['top_k']}"
             + (", permutation-confirmed" if m["discovery"]["config"]["permutation"] else "") + ")",
             f"- threshold rule: {_threshold_line(m['discovery'])}",
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
    sizes = {str(g): n for g, n in d.group_table["n_features"].items()}
    lines += ["", f"Frequency = share of a fold's subsamples with the feature in the top {cfg['top_k']} (a group: its "
                  f"joint permutation importance in the top groups); stability = share of folds with frequency >= {cfg['freq_threshold']}.",
              f"Survivor groups (stability >= {cfg['stability_threshold']}): "
              + (", ".join(f"{g} ({int(sizes[g])} features)" for g in d.survivor_groups)
                 or "none"),
              "Survivor groups skipped (whole groups only, over max_selected): "
              + (", ".join(f"{g} ({int(sizes[g])} features)" for g in m["discovery"].get("skipped_survivor_groups", []))
                 or "none"),
              f"Selected set ({len(d.selected)} of at most {cfg['max_selected']} + side): {', '.join(d.selected) or 'none'}",
              ""]
    for name, g in (("Design gates", m.get("gates")), ("Design gates, rule-only (informational)", m.get("rule_only_gates"))):
        if g:
            lines += [f"### {name}: {'**PASS**' if g['passed'] else '**FAIL**'}", ""]
            lines += [f"- {'pass' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}" for c in g["checks"]] + [""]
    lines.append("A discovery trial is never promoted; survivors need their own pre-registered trial.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- the CLI's pass
def discovery_trial_config(spec: Any, cfg: DiscoveryConfig, group_mode: str, names: list[str], bars_from: str,
                           bars_to: str, holdout: Window | None) -> dict[str, Any]:
    """The registry config of one discovery trial (family `discovery_family(spec.family)`), hashed for its budget
    check and pre-registration. One builder for the run (`discovery_pass`) and for the queued row written before the
    quarter (`research.preregistration`), so the two hash the same: "a discovery uses its reserved slot only when its
    exact discovery config (including the bars range) was pre-registered before the run" (preregistration-2027Q1.md)."""
    return {"specialist": spec.family, "spec": spec.config, "discovery": cfg.model_dump(), "group_mode": group_mode,
            "features": names, "bars_from": bars_from, "bars_to": bars_to,
            "holdout": None if holdout is None else [str(holdout[0]), str(holdout[1])]}


def discovery_pass(spec: Any, b1: pd.DataFrame, reg: Any, *, budget_cap: int, reserved_setting: int,
                   cfg: DiscoveryConfig, group_mode: str = "column", extra_cost_usd: float = 0.0, swap: Any = None,
                   holdout: Window | None = None, ctx: dict | None = None, rationale: str = "",
                   cost_source: str = "settings priors", rule_ref: str | None = None,
                   capped: bool = False, holdout_from: Any = None) -> tuple[dict[str, Any], str]:
    """`research_pass.py --discover`: budget check (one trial), the decision frame's group map and its size check
    (`check_group_sizes`, context columns counted; BACKLOG 81), the pre-registration row, then the run and its result
    row (status "discovery"). Call under `reg.locked()`. Returns (result row, report).

    Budget (`TrialRegistry.check_budget_reserved`): a discovery whose exact config (family discovery_<specialist>,
    config hash) has a pending queued `preregistered` row may use the trials reserved for the pre-registered queue
    (`reserved_setting` = research.reserved_trials_quarter) and is linked to that row; any other discovery must fit in
    budget - used - reserved and writes its own pre-registration just before it runs (`queue=False`: not part of the
    queue, so it neither holds nor uses the reservation), unless a pending queued row of the same family and agent has
    another config hash: then it is refused, never charged to the open budget (`QueuedConfigMismatch`, BACKLOG 50).

    Holdout cut (BACKLOG 50, quant review F2 of BACKLOG 47: "`--discover` cuts b1 at holdout_from"): with a holdout
    window, the 1m bars are cut at its start before the config is built or anything is resampled, as `--h16` does, so
    the hashed `bars_to` is the last bar before the holdout whatever data-v1 reaches on the day of the run. With
    `holdout_from` (research.holdout_from) set but no window (holdout_to missing, so holdout_window() is None) the run
    is refused rather than run uncut over the held-out bars (quant review 2026-10-16: fail closed)."""
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

    check_group_config(cfg)              # before the budget check, the pre-registration and any look
    check_threshold_config(cfg, window_for(spec.timeframe, **spec.walkforward))
    if holdout is None and holdout_from is not None:
        raise ValueError(f"research.holdout_from is {holdout_from} but there is no holdout window (research.holdout_to "
                         f"unset?): --discover would read the held-out bars uncut; set the window or clear holdout_from")
    if holdout is not None:
        b1 = b1.loc[np.asarray(utc_index(b1["ts_utc"]) < pd.Timestamp(holdout[0]))].reset_index(drop=True)
        if b1.empty:
            raise ValueError(f"no 1m bars before the holdout start {holdout[0]}: nothing to discover on")
    names = research_feature_names(ctx)
    family = discovery_family(spec.family)
    config = discovery_trial_config(spec, cfg, group_mode, names, str(b1["ts_utc"].iloc[0]),
                                    str(b1["ts_utc"].iloc[-1]), holdout)
    quarter, (prereg,) = reg.check_budget_reserved([(family, config)], budget_cap, reserved_setting,
                                                   agents=[spec.agent_id])
    from goldbot.research.director import cited_rule
    cited = cited_rule(prereg, rule_ref, capped=capped)      # E-5: a capped run cites its pre-registered rule
    tf = spec.timeframe
    b_dec = resample_bars(b1, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    frame = build_decision_frame(b_dec, context, ctx=ctx)
    # BACKLOG 81: the group map of every frame column (context included; prices only, no label) and its size check
    # come before the pre-registration row, so a refused config writes no row and spends no trial. The pool is a
    # subset of the frame's columns, so a map that passes here passes on the pool in run_discovery.
    frame_groups = column_groups([c for c in frame[1].columns if c != "ts_utc"], group_mode, frame[0], names, ctx,
                                 holdout_start=None if holdout is None else pd.Timestamp(holdout[0]),
                                 clusters=cfg.clusters, context_groups=cfg.context_groups)
    check_group_sizes(frame_groups, cfg, group_mode)
    if prereg is None:
        prereg = reg.preregister(agent_id=spec.agent_id, family=family, config=config,
                                 feature_version=feature_version(names), rationale=rationale,
                                 reading_rule=reading_rule(cfg.survivor_unit, cfg.threshold_rule, cfg.tercile_q),
                                 plan={"features": names, "group_mode": group_mode, "trial_status": DISCOVERY,
                                       "survivor_unit": cfg.survivor_unit, **threshold_plan(cfg)}, queue=False)
    leak = lookahead_check(b_dec, context, ctx=ctx)
    prep = prepare(spec, b_dec, context, extra_cost_usd=extra_cost_usd, holdout=holdout, frame=frame, swap=swap)
    if prep.labels.empty:
        raise ValueError(f"{spec.family} produced no labelled candidates; nothing to discover")
    pool = discovery_pool(prep)
    groups = {c: frame_groups[c] for c in pool}
    n_eff = reg.n_trials_effective + 1
    d = discover(prep, cfg, groups, group_mode, n_trials=n_eff, extra_cost_usd=extra_cost_usd, holdout=holdout)
    metrics = {**d.result.metrics, "lookahead": leak, "cost_source": cost_source,
               "bars_from": config["bars_from"], "bars_to": config["bars_to"]}
    row = reg.record(agent_id=spec.agent_id, family=family, config=config, feature_version=d.result.feature_version,
                     rationale=rationale, results=metrics, status=DISCOVERY, budget_quarter=quarter,
                     preregistration=prereg, rule_ref=cited)
    bad = leak["lookahead_columns"]
    text = render_discovery(d, {"specialist": spec.family, "from_year": int(b1["ts_utc"].iloc[0].year),
                                "to_year": int(b1["ts_utc"].iloc[-1].year), "tf": tf, "trial": row["trial"],
                                "prereg_ts": prereg["ts"], "n_trials_effective": reg.n_trials_effective,
                                "lookahead": "clean" if not bad else f"**{len(bad)} columns use future data**",
                                "cost_source": cost_source})
    return row, text
