"""Combinatorial purged cross-validation (design: Validation, "Combinatorial purged CV (6 groups, 2 test, 15 paths) runs
quarterly"; row M16; López de Prado 2018, ch. 12).

The research window (holdout excluded: every label exits before the holdout starts) is cut into N_GROUPS = 6
contiguous groups of equal duration. Each of the C(6, 2) = 15 splits tests on 2 groups and trains on the other 4,
after purging every training label whose life [signal, exit] overlaps a test label's life (widened by the timeframe's
purge before the test group) and embargoing training labels that start within the timeframe's embargo after it (the
walk-forward's purge/embargo days, research/walkforward.py). Every group is tested in C(5, 1) = 5 splits, so the
15 splits' out-of-sample predictions rebuild 5 complete backtest paths (the design's "15 paths" are the 15 splits;
they make phi = 5 paths). Each path is one full out-of-sample history; the spread of its Sharpe and mean R across the
5 paths is what one walk-forward cannot show.

Selection reuses the walk-forward's own logic, not a copy of it: on each path the calibrator of group g is fitted only
on that path's out-of-sample predictions of the groups before g (pipeline._cross_fitted, with the groups as folds), and
a candidate is taken when its calibrated p is above pipeline.candidate_threshold (break-even + margin from its barriers
and costs). The first group of every path therefore has no calibrator and takes no trade, as the walk-forward's first
fold does not.

Probability of backtest overfitting (Bailey, Borwein, López de Prado and Zhu 2017, CSCV), when several configurations
are compared on the same groups: per configuration, the R earned in each group (mean over the 5 paths); for each of the
C(6, 3) = 20 ways to call 3 groups in-sample, the configuration best in-sample is ranked out of sample; PBO is the share
of those 20 where it ranks at or below the median (logit of its relative rank <= 0).

CPCV is NOT a trial. A trial is a look at the data that chooses a configuration; CPCV re-evaluates a configuration that
is already registered (its trial row holds its config) on the same research window, chooses nothing and changes no
config, so it takes no budget slot and does not raise the deflated-Sharpe count. Its result is recorded as evidence
attached to that trial (`TrialRegistry.attach_evidence`, kind "cpcv"), never as a new trial row. It never sees the
holdout. Run it with `scripts/research_pass.py --cpcv <trial#> [<trial#> ...]` (PBO across the trials named) or the
quarterly scheduler job `cpcv_quarterly` (every trial that passed the gates, compared with its family's other trials).
"""
from __future__ import annotations

from itertools import combinations
from math import comb
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from goldbot.base import Record
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.labels import uniqueness_weights
from goldbot.research.metrics import expectancy, sharpe_annualised
from goldbot.research.model import MetaLabelModel
from goldbot.research.pipeline import (
    Prepared,
    _cross_fitted,
    candidate_threshold,
    eligible_columns,
    model_inputs,
    pool_frames,
    pooled_inputs,
    prepare,
)
from goldbot.research.walkforward import window_for

N_GROUPS = 6
N_TEST_GROUPS = 2
MIN_TRAIN = 200                  # as the walk-forward: a split with fewer purged training rows is not fitted
EVIDENCE_KIND = "cpcv"


class CpcvSplit(Record):
    k: int
    test_groups: tuple[int, ...]
    train_idx: np.ndarray
    test_idx: np.ndarray


def group_edges(start: pd.Timestamp, end: pd.Timestamp, n_groups: int = N_GROUPS) -> list[pd.Timestamp]:
    """n_groups + 1 edges cutting [start, end) into groups of equal duration."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if not end > start:
        raise ValueError(f"empty research window {start} .. {end}")
    step = (end - start) / n_groups
    return [start + i * step for i in range(n_groups)] + [end]


def _times(labels: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Signal and exit times as epoch ns (interval arithmetic)."""
    return epoch_ns(utc_index(labels["ts_utc"])), epoch_ns(utc_index(labels["ts_exit"]))


def _ns(t: pd.Timestamp) -> int:
    return int(epoch_ns(pd.DatetimeIndex([pd.Timestamp(t)]))[0])


def assign_groups(labels: pd.DataFrame, edges: list[pd.Timestamp]) -> np.ndarray:
    """Group of each label by its signal time; -1 outside the window."""
    g = np.searchsorted(epoch_ns(pd.DatetimeIndex(edges)), epoch_ns(utc_index(labels["ts_utc"])), side="right") - 1
    return np.where((g >= 0) & (g < len(edges) - 1), g, -1)


def purged_train(labels: pd.DataFrame, groups: np.ndarray, test_groups: tuple[int, ...], edges: list[pd.Timestamp],
                 purge_days: float, embargo_days: float) -> np.ndarray:
    """Training rows for a split: every row of a non-test group except those whose life [signal, exit] touches
    [group start - purge, latest exit of the group's test labels (at least the group's end) + embargo) of any test
    group, so no training label overlaps a test label in time."""
    ts, te = _times(labels)
    ns = pd.Timedelta(nanoseconds=1)
    purge, embargo = int(pd.Timedelta(days=purge_days) / ns), int(pd.Timedelta(days=embargo_days) / ns)
    keep = (groups >= 0) & ~np.isin(groups, test_groups)
    for g in test_groups:
        rows = groups == g
        lo = _ns(edges[g]) - purge
        hi = max(_ns(edges[g + 1]), int(te[rows].max()) if rows.any() else 0) + embargo
        keep &= ~((te >= lo) & (ts < hi))
    return np.flatnonzero(keep)


def cpcv_splits(labels: pd.DataFrame, edges: list[pd.Timestamp], purge_days: float, embargo_days: float,
                n_test: int = N_TEST_GROUPS) -> list[CpcvSplit]:
    """All C(n_groups, n_test) splits, purged and embargoed around every test group. Raises if any split leaks."""
    groups = assign_groups(labels, edges)
    out = []
    for k, tg in enumerate(combinations(range(len(edges) - 1), n_test)):
        split = CpcvSplit(k=k, test_groups=tuple(tg), test_idx=np.flatnonzero(np.isin(groups, tg)),
                          train_idx=purged_train(labels, groups, tuple(tg), edges, purge_days, embargo_days))
        if len(overlaps(labels, split)):
            raise ValueError(f"CPCV split {k} {tg}: training labels overlap test labels in time")
        out.append(split)
    return out


def overlaps(labels: pd.DataFrame, split: Any) -> np.ndarray:
    """Training rows whose life [signal, exit] intersects the life of any test row (empty for a purged split)."""
    ts, te = _times(labels)
    tr, tst = np.asarray(split.train_idx), np.asarray(split.test_idx)
    if not len(tr) or not len(tst):
        return np.array([], dtype=int)
    order = np.argsort(ts[tst])
    t_ts, t_te = ts[tst][order], np.maximum.accumulate(te[tst][order])
    # a training life [a, b] meets some test life [c, d] iff some test row has c <= b and d >= a: among the test rows
    # starting at or before b, the latest exit must reach a
    j = np.searchsorted(t_ts, te[tr], side="right") - 1
    hit = (j >= 0) & (t_te[np.clip(j, 0, None)] >= ts[tr])
    return tr[hit]


def backtest_paths(n_groups: int = N_GROUPS, n_test: int = N_TEST_GROUPS) -> list[list[int]]:
    """paths[p][g] = the split whose predictions of group g form path p: the p-th split (in order) that tests g.
    C(n_groups - 1, n_test - 1) paths."""
    splits = list(combinations(range(n_groups), n_test))
    per_group = [[k for k, tg in enumerate(splits) if g in tg] for g in range(n_groups)]
    n_paths = comb(n_groups - 1, n_test - 1)
    return [[per_group[g][p] for g in range(n_groups)] for p in range(n_paths)]


def pbo(perf: np.ndarray) -> dict[str, Any] | None:
    """Probability of backtest overfitting (CSCV) from a configurations x groups matrix of performance (higher is
    better); None with fewer than 2 configurations."""
    perf = np.asarray(perf, dtype=float)
    m, n = perf.shape
    if m < 2:
        return None
    logits = []
    for ins in combinations(range(n), n // 2):
        oos = [g for g in range(n) if g not in ins]
        best = int(np.argmax(perf[:, list(ins)].sum(axis=1)))
        omega = rankdata(perf[:, oos].sum(axis=1))[best] / (m + 1)
        logits.append(float(np.log(omega / (1 - omega))))
    lam = np.array(logits)
    return {"pbo": float(np.mean(lam <= 0)), "n_configs": int(m), "n_combinations": len(lam),
            "logit_median": float(np.median(lam))}


def _dist(x: list[float]) -> dict[str, float]:
    a = np.asarray([v for v in x if np.isfinite(v)], dtype=float)
    if not len(a):
        return {}
    return {"mean": float(a.mean()), "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0, "min": float(a.min()),
            "median": float(np.median(a)), "max": float(a.max())}


def run_cpcv(labels: pd.DataFrame, feats: pd.DataFrame, cols: list[str], *, edges: list[pd.Timestamp],
             purge_days: float, embargo_days: float, extra_cost_usd: float, feature_version: str = "",
             n_test: int = N_TEST_GROUPS, min_train: int = MIN_TRAIN) -> dict[str, Any]:
    """CPCV of one configuration. `labels` (net, with weight, target_hit, ret, risk, atr_sig, stop_atr, target_atr,
    ts_utc, ts_exit) and `feats` are row-aligned; `cols` are the meta-model's inputs. Returns per-path metrics, their
    distribution, and `group_r` (R earned per path and group) for PBO."""
    n_groups = len(edges) - 1
    splits = cpcv_splits(labels, edges, purge_days, embargo_days, n_test)
    groups = assign_groups(labels, edges)
    y = labels["target_hit"].astype(int)
    w = labels["weight"] if "weight" in labels else None
    preds: list[np.ndarray] = []
    split_info = []
    for sp in splits:
        p = np.full(len(labels), np.nan)
        fitted = len(sp.train_idx) >= min_train and y.iloc[sp.train_idx].nunique() == 2 and len(sp.test_idx) > 0
        if fitted:
            mdl = MetaLabelModel(feature_names=cols, feature_version=feature_version).fit(
                feats.iloc[sp.train_idx], y.iloc[sp.train_idx], None if w is None else w.iloc[sp.train_idx])
            p[sp.test_idx] = mdl.predict_raw(feats.iloc[sp.test_idx])
        preds.append(p)
        split_info.append({"k": sp.k, "test_groups": list(sp.test_groups), "n_train": int(len(sp.train_idx)),
                           "n_test": int(len(sp.test_idx)), "fitted": bool(fitted)})
    threshold = candidate_threshold(labels, extra_cost_usd)
    r = labels["ret"].to_numpy(dtype=float) / labels["risk"].to_numpy(dtype=float)
    g_years = np.array([(edges[g + 1] - edges[g]).days / 365.25 for g in range(n_groups)])
    paths_out, group_r = [], []
    for p_idx, path in enumerate(backtest_paths(n_groups, n_test)):
        p_raw = np.full(len(labels), np.nan)
        for g, k in enumerate(path):
            rows = groups == g
            p_raw[rows] = preds[k][rows]
        folds = [SimpleNamespace(test_idx=np.flatnonzero((groups == g) & np.isfinite(p_raw))) for g in range(n_groups)]
        p_cal = _cross_fitted(np.nan_to_num(p_raw, nan=0.5), y.to_numpy(), folds)
        taken = np.isfinite(p_cal) & (p_cal > threshold)
        scored_groups = [g for g in range(n_groups) if np.isfinite(p_cal[groups == g]).any()]
        years = float(g_years[scored_groups].sum()) if scored_groups else 0.0
        ret = labels["ret"].to_numpy(dtype=float)[taken]
        ex = expectancy(ret, labels["risk"].to_numpy(dtype=float)[taken])
        tpy = len(ret) / years if years > 0 else 0.0
        paths_out.append({"path": p_idx, "splits": path, "n_trades": int(len(ret)), "trades_per_year": tpy,
                          "mean_r": ex.get("mean_r", float("nan")), "t_stat": ex.get("t_stat", float("nan")),
                          "hit_rate": ex.get("hit_rate", float("nan")),
                          "sharpe_ann": sharpe_annualised(ret, tpy) if len(ret) > 1 else float("nan")})
        group_r.append([float(r[taken & (groups == g)].sum()) for g in range(n_groups)])
    return {
        "n_groups": n_groups, "n_test_groups": n_test, "n_splits": len(splits), "n_paths": len(paths_out),
        "edges": [str(e) for e in edges], "purge_days": purge_days, "embargo_days": embargo_days,
        "n_candidates": int(len(labels)), "group_sizes": [int((groups == g).sum()) for g in range(n_groups)],
        "splits": split_info, "paths": paths_out, "group_r": group_r,
        "sharpe": _dist([x["sharpe_ann"] for x in paths_out]), "mean_r": _dist([x["mean_r"] for x in paths_out]),
        "share_paths_positive": float(np.mean([x["mean_r"] > 0 for x in paths_out if np.isfinite(x["mean_r"])]))
        if any(np.isfinite(x["mean_r"]) for x in paths_out) else None,
        "evaluation": "CPCV, cross-fitted calibration per path (earlier groups only), pipeline threshold",
    }


# ---------------------------------------------------------------------------------------------- registered trials
def trial_timeframe(row: dict[str, Any]) -> str:
    """Decision timeframe of a registered trial's configuration."""
    from goldbot.specialists import SPECIALISTS
    from goldbot.specialists.base import AgentIdentity
    family, config = str(row["family"]), dict(row.get("config") or {})
    if family.startswith("pooled_"):
        return str(config["timeframe"])
    return SPECIALISTS[family](identity=AgentIdentity(family=family, config=config)).timeframe


def cpcv_eligible(row: dict[str, Any]) -> str | None:
    """Why a registry row cannot be re-evaluated by CPCV, or None when it can: only a walk-forward trial (status
    "evaluated") of a known family; never a holdout scoring, a screen without a model, a discovery or a
    pre-registration."""
    from goldbot.specialists import SPECIALISTS
    if row.get("status") != "evaluated":
        return f"trial #{row.get('trial')} has status {row.get('status')!r}; CPCV re-evaluates walk-forward trials only"
    fam = str(row.get("family"))
    if not fam.startswith("pooled_") and fam not in SPECIALISTS:
        return f"trial #{row.get('trial')}: unknown family {fam!r}"
    return None


def trial_inputs(row: dict[str, Any], b_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None, *,
                 extra_cost_usd: float, holdout: tuple[pd.Timestamp, pd.Timestamp] | None, swap: Any = None,
                 frame: tuple[pd.DataFrame, pd.DataFrame] | None = None, ctx: dict | None = None
                 ) -> tuple[pd.DataFrame, pd.DataFrame, list[str], dict[str, Any], str]:
    """Labels (with uniqueness weights), features, model inputs, walk-forward window (purge/embargo) and feature
    version of a registered trial's configuration, prepared exactly as the walk-forward prepares it (holdout
    excluded). A pooled trial ("pooled_<tf>") rebuilds its members from the row's config."""
    from goldbot.specialists import SPECIALISTS
    from goldbot.specialists.base import AgentIdentity
    family, config = str(row["family"]), dict(row.get("config") or {})
    if family.startswith("pooled_"):
        tf = str(config["timeframe"])
        specs = [SPECIALISTS[f](identity=AgentIdentity(family=f, config=c)) for f, c in sorted(config["families"].items())]
    else:
        specs = [SPECIALISTS[family](identity=AgentIdentity(family=family, config=config))]
        tf = specs[0].timeframe
    preps: list[Prepared] = [prepare(s, b_dec, context, ctx=ctx, extra_cost_usd=extra_cost_usd, holdout=holdout,
                                     frame=frame, swap=swap) for s in specs]
    version = preps[0].feature_version
    if family.startswith("pooled_"):
        labels, feats, _ = pool_frames(preps)
        if labels.empty:
            return labels, feats, [], window_for(tf), version
        labels["weight"] = uniqueness_weights(labels, max(p.n_bars for p in preps)).to_numpy()
        cols = pooled_inputs(tf, sorted({p.spec.family for p in preps}), eligible_columns(feats))
        return labels, feats, cols, window_for(tf), version
    prep = preps[0]
    labels = prep.labels.copy()
    if labels.empty:
        return labels, prep.feats, [], window_for(tf, **specs[0].walkforward), version
    labels["weight"] = uniqueness_weights(labels, prep.n_bars).to_numpy()
    cols = model_inputs(specs[0], eligible_columns(prep.feats))
    return labels, prep.feats, cols, window_for(tf, **specs[0].walkforward), version


def research_window(b_dec: pd.DataFrame, holdout: tuple[pd.Timestamp, pd.Timestamp] | None
                    ) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[first decision bar, holdout start or the last bar) : the window the groups cut, the holdout excluded."""
    ts = pd.to_datetime(b_dec["ts_utc"], utc=True)
    start, end = pd.Timestamp(ts.min()), pd.Timestamp(ts.max()) + pd.Timedelta(seconds=1)
    if holdout is not None:
        end = min(end, pd.Timestamp(holdout[0]))
    return start, end


def cpcv_trials(rows: list[dict[str, Any]], b_dec: pd.DataFrame, context: dict[str, pd.DataFrame] | None, *,
                extra_cost_usd: float, holdout: tuple[pd.Timestamp, pd.Timestamp] | None, swap: Any = None,
                frame: tuple[pd.DataFrame, pd.DataFrame] | None = None, ctx: dict | None = None
                ) -> dict[str, Any]:
    """CPCV of each registered trial in `rows` (one decision timeframe) on shared groups, plus PBO across them when
    there are several. Returns {"per_trial": {trial: result}, "pbo": ... | None, "edges": [...]}."""
    edges = group_edges(*research_window(b_dec, holdout))
    per: dict[int, dict[str, Any]] = {}
    for row in rows:
        labels, feats, cols, window, version = trial_inputs(row, b_dec, context, extra_cost_usd=extra_cost_usd,
                                                            holdout=holdout, swap=swap, frame=frame, ctx=ctx)
        if labels.empty:
            per[int(row["trial"])] = {"error": "no candidates in the research window"}
            continue
        per[int(row["trial"])] = run_cpcv(labels, feats, cols, edges=edges, purge_days=window["purge_days"],
                                          embargo_days=window["embargo_days"], extra_cost_usd=extra_cost_usd,
                                          feature_version=version)
    ok = {t: r for t, r in per.items() if "group_r" in r}
    perf = np.array([np.mean(np.asarray(r["group_r"]), axis=0) for r in ok.values()]) if ok else np.zeros((0, N_GROUPS))
    p = pbo(perf) if len(ok) >= 2 else None
    if p is not None:
        p["trials"] = list(ok)
    return {"per_trial": per, "pbo": p, "edges": [str(e) for e in edges]}


def evidence_payload(result: dict[str, Any], pbo_info: dict[str, Any] | None, source: str) -> dict[str, Any]:
    """What is attached to the trial: its CPCV result and the PBO of the comparison it was part of."""
    return {**result, "pbo": pbo_info, "source": source}


def report_lines(per_trial: dict[int, dict[str, Any]], pbo_info: dict[str, Any] | None) -> list[str]:
    out = ["| trial | candidates | paths | Sharpe mean (min .. max) | mean R mean (min .. max) | paths with mean R > 0 |",
           "|---:|---:|---:|---|---|---:|"]
    for t, r in per_trial.items():
        if "paths" not in r:
            out.append(f"| {t} | | | {r.get('error', '')} | | |")
            continue
        s, m = r["sharpe"], r["mean_r"]
        sh = f"{s['mean']:.2f} ({s['min']:.2f} .. {s['max']:.2f})" if s else "—"
        mr = f"{m['mean']:+.3f} ({m['min']:+.3f} .. {m['max']:+.3f})" if m else "—"
        share = r.get("share_paths_positive")
        out.append(f"| {t} | {r['n_candidates']:,} | {r['n_paths']} | {sh} | {mr} | "
                   f"{'—' if share is None else f'{share:.0%}'} |")
    out.append("")
    if pbo_info is not None:
        out.append(f"PBO across trials {pbo_info['trials']}: **{pbo_info['pbo']:.0%}** over {pbo_info['n_combinations']} "
                   f"in/out-of-sample group splits (median logit {pbo_info['logit_median']:+.2f}).")
    else:
        out.append("PBO: needs at least two configurations compared on the same groups.")
    out += ["", f"{N_GROUPS} groups, {N_TEST_GROUPS} tested per split: {comb(N_GROUPS, N_TEST_GROUPS)} purged splits rebuild "
            f"{comb(N_GROUPS - 1, N_TEST_GROUPS - 1)} paths. Holdout excluded. Evidence on existing trials, not a trial."]
    return out
