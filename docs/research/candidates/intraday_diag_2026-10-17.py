"""Intraday 1h/4h candidate diagnostics (docs/research/candidates/intraday-2026-10-17.md, section 4).

Outcome-free by construction: every number is a count, a coverage share or a cost known at a decision bar's close
(spread, ATR, the frozen 0.37 $/oz, the swap prior). No label is built, no price after a decision bar is read, and
no return of the Asia window (H-04's outcome window) is signed. Inputs are cut at research.holdout_from before
anything is resampled, so no holdout row is loaded.

usage: uv run python docs/research/candidates/intraday_diag_2026-10-17.py <bars dir> <macro dir or ""> <out.json>
Not a script of record: to be promoted into scripts/ with an AST no-outcome test (as tests/test_count_owed_events.py)
before any count from it is pasted into a pre-registration.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.config import load_settings
from goldbot.data.release import read_bar_files, read_macro_files
from goldbot.data.resample import mid, resample_bars
from goldbot.data.timeutil import utc_index
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.registry import FEATURES, build_features
from goldbot.research import h16
from goldbot.research.discovery import H02_CLUSTERS, cluster_of
from goldbot.research.pipeline import research_feature_names
from goldbot.research.walkforward import WINDOWS, walk_forward_splits
from goldbot.specialists import SPECIALISTS

EXTRA = 0.37                 # frozen round trip beyond the spread, $/oz (Q1 pre-registration)
SWAP_LONG = 0.60             # $/oz per night on longs (prior: -60 USD/lot/night, 100 oz); shorts 0
Q = [0.25, 0.5, 0.75]


def folds(ts: pd.DatetimeIndex, hold: pd.Timedelta, tf: str) -> list:
    """The frozen walk-forward folds of `tf` from decision times only: ts_exit := ts + the longest hold (no label)."""
    lab = pd.DataFrame({"ts_utc": ts, "ts_exit": ts + hold})
    return list(walk_forward_splits(lab, **WINDOWS[tf]))


def quart(x: np.ndarray) -> list[float | None]:
    x = x[np.isfinite(x)]
    return [round(float(v), 3) for v in np.quantile(x, Q)] if len(x) else [None, None, None]


def d1_c4h_cost_regime(b1: pd.DataFrame) -> dict:
    """D1, for IC-4: the C4H candidates (tsmom 4h, max_bars 12), their vol_tercile at the decision bar, cost in R at
    the 1.5-ATR stop, swap in R for a long held 2 nights, target / cost multiple per exit geometry, per fold."""
    spec = SPECIALISTS["tsmom"](timeframe="4h", max_bars=12)
    dec = resample_bars(b1, "4h").reset_index(drop=True)
    m = mid(dec)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(spec.timeframe)}
    c = spec.candidates_in_context(m, pd.DataFrame(index=m.index), context, None)
    f = build_features(m, ["atr", "realised_vol"])
    i = c["idx"].to_numpy(dtype=int)
    a, spr = f["atr14"].to_numpy(float)[i], m["spread"].to_numpy(float)[i]
    side, terc = c["side"].to_numpy(int), f["vol_tercile"].to_numpy(float)[i]
    cost_r = (spr + EXTRA) / (1.5 * a)
    swap_r = np.where(side > 0, 2 * SWAP_LONG / (1.5 * a), 0.0)
    ts = utc_index(dec["visible_at"])[i]
    out: dict = {"candidates": int(len(i)), "long_share": round(float((side > 0).mean()), 3) if len(i) else None,
                 "by_tercile": {}, "folds": []}
    for t in (0.0, 1.0, 2.0):
        k = terc == t
        out["by_tercile"][str(int(t))] = {
            "n": int(k.sum()), "cost_r_quartiles": quart(cost_r[k]), "swap_r_long_quartiles": quart(swap_r[k & (side > 0)]),
            "target_over_cost_median": {str(tg): (round(float(np.nanmedian(tg * a[k] / (spr[k] + EXTRA))), 1)
                                                  if k.any() else None) for tg in (2.0, 3.0, 4.0)}}
    for fo in folds(ts, pd.Timedelta(hours=52), "4h"):
        tt = terc[fo.test_idx]
        out["folds"].append({"k": fo.k, "test_start": f"{fo.test_start:%Y-%m}", "n_test": int(len(fo.test_idx)),
                             "n_test_by_tercile": [int((tt == t).sum()) for t in (0.0, 1.0, 2.0)]})
    return out


def d2_session_transition(b1: pd.DataFrame) -> dict:
    """D2, for IC-5: days with a London-open decision bar (the 1h bar closing 08:00 Europe/London) and the UNSIGNED
    size of the move since the Tokyo open (23:00 UTC), z = |move| / (ATR14(1h) x sqrt(bars)); counts by |z| bin, per
    fold. The sign is never computed (H-04 reads that window)."""
    dec = resample_bars(b1, "1h").reset_index(drop=True)
    m = mid(dec)
    a = build_features(m, ["atr"])["atr14"].to_numpy(float)
    close = utc_index(dec["visible_at"])
    ldn = close.tz_convert("Europe/London")
    dec_i = np.flatnonzero((ldn.hour == 8) & (ldn.minute == 0) & (ldn.dayofweek < 5))
    opens = utc_index(dec["ts_utc"])
    pos = {t: j for j, t in enumerate(opens)}
    z, ts = [], []
    for j in dec_i:
        tokyo = (close[j] - pd.Timedelta(days=1)).normalize() + pd.Timedelta(hours=23)
        s = pos.get(tokyo)
        if s is None or not np.isfinite(a[j]) or a[j] <= 0:
            continue
        n = j - s + 1
        z.append(abs(m["close"].iat[j] - m["open"].iat[s]) / (a[j] * np.sqrt(n)))
        ts.append(close[j])
    z_arr, ts_idx = np.asarray(z), pd.DatetimeIndex(ts)
    bins = {"lt0.5": int((z_arr < 0.5).sum()), "0.5-1.0": int(((z_arr >= 0.5) & (z_arr < 1.0)).sum()),
            "ge1.0": int((z_arr >= 1.0).sum())}
    fl = [{"k": fo.k, "test_start": f"{fo.test_start:%Y-%m}", "n_test": int(len(fo.test_idx)),
           "n_test_ge0.5": int((z_arr[fo.test_idx] >= 0.5).sum())}
          for fo in folds(ts_idx, pd.Timedelta(hours=9), "1h")] if len(ts_idx) else []
    return {"decision_days": int(len(z_arr)), "z_bins": bins, "folds": fl}


def d3_clock_rules_cost(b1: pd.DataFrame) -> dict:
    """D3, for IC-1 (O-7) and IC-2 (O-8): cost in R at a 1.5 x ATR14(1h) stop for H-04's own candidates and for
    H-24 under the pinned convention of the O-7 amendment (decision = the 1h bar closing 09:00 America/New_York,
    weekdays); counts."""
    dec = resample_bars(b1, "1h").reset_index(drop=True)
    m = mid(dec)
    a, spr = build_features(m, ["atr"])["atr14"].to_numpy(float), m["spread"].to_numpy(float)
    h04 = SPECIALISTS["asia_drift"]()
    kept = h04.complete_windows(dec, h04.candidates(m, pd.DataFrame(index=m.index)))["idx"].to_numpy(dtype=int)
    ny = utc_index(dec["visible_at"]).tz_convert("America/New_York")
    h24 = np.flatnonzero((ny.hour == 9) & (ny.minute == 0) & (ny.dayofweek < 5) & np.isfinite(a))
    out = {}
    for name, i in (("H-04", kept), ("H-24 (pinned 09:00 NY close)", h24)):
        out[name] = {"n": int(len(i)), "cost_r_quartiles": quart((spr[i] + EXTRA) / (1.5 * a[i]))}
    return out


def d4_cluster_coverage(b1: pd.DataFrame, macro: pd.DataFrame | None) -> dict:
    """D4, for IC-3 and IC-6: per 1h and 4h fold, the share of decision bars where each H-02 cluster has at least
    one finite, non-constant column (feature values only), and NaN share of every macro column."""
    ctx = {"macro": macro} if macro is not None and len(macro) else {}
    out = {}
    for tf in ("1h", "4h"):
        dec = resample_bars(b1, tf).reset_index(drop=True)
        m = mid(dec)
        names = research_feature_names(ctx)
        X = build_features(m, names, ctx)
        owner = {}
        for n in names:
            for col in build_features(m.iloc[:600].reset_index(drop=True), [n], ctx).columns:
                owner[col] = n
        cl: dict[str, list[str]] = {}
        for col in X.columns:
            cid = cluster_of(col, owner.get(col), H02_CLUSTERS)
            if cid:
                cl.setdefault(cid, []).append(col)
        ts = utc_index(dec["visible_at"])
        rows = []
        for fo in folds(ts, pd.Timedelta(hours=52 if tf == "4h" else 9), tf):
            te = X.iloc[fo.test_idx]
            rec = {"k": fo.k, "test_start": f"{fo.test_start:%Y-%m}"}
            for cid, cols in sorted(cl.items()):
                v = te[cols].apply(pd.to_numeric, errors="coerce")
                informative = [c for c in cols if v[c].notna().any() and v[c].nunique() > 1]
                rec[cid] = round(float(v[informative].notna().any(axis=1).mean()), 3) if informative else 0.0
            for col in [c for c in X.columns if c.startswith("macro_")]:
                rec[col] = round(float(te[col].isna().mean()), 3)
            rows.append(rec)
        out[tf] = {"clusters": {k: len(v) for k, v in cl.items()}, "folds": rows}
    return out


def main() -> int:
    bars_dir, macro_dir, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    ho = load_settings().research.holdout_from
    b1 = h16.cut_before_holdout(read_bar_files(Path(bars_dir), 2010, ho.year), ho)
    macro = h16.cut_before_holdout(read_macro_files(Path(macro_dir)), ho, ("available_utc",)) if macro_dir else None
    res = {"holdout_from": str(ho), "outcomes_read": "none", "bars_to": f"{utc_index(b1['ts_utc'])[-1]:%Y-%m-%d %H:%M}",
           "D1_c4h_cost_regime": d1_c4h_cost_regime(b1), "D2_session_transition": d2_session_transition(b1),
           "D3_clock_rules_cost": d3_clock_rules_cost(b1), "D4_cluster_coverage": d4_cluster_coverage(b1, macro)}
    Path(out_path).write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "D4_cluster_coverage"}, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    assert FEATURES  # registry imported
    raise SystemExit(main())
