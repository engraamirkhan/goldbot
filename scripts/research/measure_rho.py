"""D-12: measure the sr-v3 independence inputs on learn 2010-17 (quant rulings on PR #460 rev 3, ADR 0085 Q1 / R13).

Four quantities, as re-specified by the quant-reviewer (review of #460 rev 3, rulings Q1 and Q2):
1. rho_u,day: the RESIDUAL same-day ICC of candidate outcomes, after a unit intercept and an instrument level are
   removed, per hold bucket (<= 60 min, 1-4 h, > 4 h) and per class; upper 95 % CI.
2. rho_u,inst: the residual cross-instrument same-day ICC, within each class and of each class against gold.
3. tau_a: the upper 95 % CI of the between-instrument SD of mean gross T2 (stop 1, target 2) R, floored at 0.01 R.
4. rho_x,j (label-blind, read first): the within-day and the same-day cross-instrument ICC of each sr-v3 core-12
   column on the features alone. No label, no outcome is read for it.

Whose candidates (1-3). A CONTROL family, never a judged one: decisions on a fixed grid: every hold_bars-th tf-bar close of
each New York 17:00 session day, counted from the day's first bar, so a day's candidates never overlap (Q3 prices overlap separately through
uniqueness), with the same decision times on every instrument (the worst case for the cross-instrument ICC). No rule, no
filter, no sr-v3 or H-102 arm or output chooses a candidate. Geometry, the sr-v3 template form (tr-8 / SYNTH-tr R9):
stop = k x ATR14_tf x sqrt(hold_bars / h_ref), h_ref = 1 bar, ATR14 on tf bars closed at the decision; heads T2 (stop 1,
target 2, time exit at H) and T6 (stop 2, time exit at H); entry at the open of the first 1m bar after the decision;
barriers on 1m bars, stop first on a shared minute. Outcome in R, gross and net of a constant bps cost (a bps cost in R
grows on quiet days, so it is itself day-clustered). Sides: long; short; random (seeded per candidate).

Estimators (pairwise moments on per instrument-day sufficient statistics, centred on each instrument's own mean, i.e.
after the unit intercept and the instrument level):
- within-day:  sum over (instrument, day) of (S^2 - Q) / sum of (n^2 - n), divided by the pooled residual variance;
  the correlation of two same-instrument candidates on one day (the rho in 1 + (m - 1) rho).
- cross, pair: the correlation of two candidates of DIFFERENT instruments on the same day.
- cross, agg:  the correlation of two instruments' day-MEAN residuals on the same day; this is the correlation of
  instrument estimates that 1 + (k - 1) rho_inst divides by (for equal day counts it equals m r_pair / (1 + (m-1) rho_day)).
- vs gold: the same two, with pairs restricted to (gold, class member).
Week-block bootstrap: ISO weeks of the session day, every instrument of a week together, B = 2,000, fixed seeds;
"upper 95 % CI" = the 97.5th percentile (two-sided 95 % interval). Directional series are also read PC1-oriented
(each instrument's sign = its loading on the first principal component of learn daily returns), the orientation that
maximises co-movement; every binding value is the largest upper bound over the variants of its cell.

Data, learn window only: Dukascopy XAUUSD 1m (`windows.read_window_bars(raw, "learn")`, no warm-up) and HistData 1m
(raw/histdata_m1) 2010..2017 files only, rows of r11-v2.1 tier A / A-consistent months only. A candidate whose horizon
ends at or after 2018-01-01 is dropped; no 2009 (test_old), 2018+ (confirm) or holdout file is opened.

Usage: python scripts/research/measure_rho.py [--raw RAW] [--workers 4] [--boot 2000] [--out docs/research]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from goldbot.config import RHO_EXCLUDED_ARMS  # noqa: E402
from goldbot.research import windows as W  # noqa: E402

LEARN_LO, LEARN_HI = W.bounds("learn")          # 2010-01-01 .. 2018-01-01, end exclusive
YEARS = tuple(range(LEARN_LO.year, LEARN_HI.year))
FOLDS = {"learn": (LEARN_LO, LEARN_HI), "fold_2010_11": (LEARN_LO, pd.Timestamp("2012-01-01", tz="UTC"))}
GOOD_TIERS = ("A", "A-consistent")
TIERS_FILE = "histdata_m1/_reports/tiers_r11-v2.1.csv"
SEED = 20261125
MIN_COVERAGE = 0.9
GOLD = "XAUUSD@dukascopy"
FX10 = ("AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")
# sr-v3 classes with graded (A / A-consistent) learn months; index, energy and crosses have none on 2010-17 (r11-v2.1)
CLASSES: dict[str, tuple[str, ...]] = {
    "gold": (GOLD,),
    "metals": (GOLD, "XAGUSD"),
    "fx10": FX10,
    "pooled": (GOLD, "XAGUSD", *FX10),          # the sr-v3 pooled body (a): gold + FX10 + graded metals
    "gold+fx10": (GOLD, *FX10),                 # read for FX10 vs gold only
}
VS_GOLD = {"metals": (GOLD, "XAGUSD"), "gold+fx10": (GOLD, *FX10), "pooled": CLASSES["pooled"]}
COST_BP = {GOLD: (0.575, 1.75), "XAGUSD": (2.5, 3.0)}   # gold: W2 cost of record; silver, FX: stated assumptions
FX_COST_BP = (0.8, 0.5)

CORE = ("tsmom_score", "dist_vwap48_atr", "atr_ratio_14_100", "er_30", "bb_z_20", "ret_16", "ret_96",
        "donchian_pos_20", "adx14", "compression_8_96", "min_since_london_open")    # core-12 less `side`
DIRECTIONAL = {"tsmom_score", "dist_vwap48_atr", "bb_z_20", "ret_16", "ret_96", "donchian_pos_20"}
FEATURE_GROUPS = ("session", "returns", "atr", "trend_strength", "mean_reversion", "breakout", "tsmom", "regime_stats")
FEATURE_TFS = ("1m", "5m", "15m", "30m", "1h", "4h")        # 2h is not a repo timeframe on main
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}


@dataclass(frozen=True)
class Geometry:
    band: str
    tf_min: int
    hold_bars: int
    head: str

    @property
    def hold_min(self) -> int:
        return self.tf_min * self.hold_bars

    @property
    def name(self) -> str:
        return f"{self.tf_min}m x{self.hold_bars} ({self.hold_min} min) {self.head}"


GEOMETRIES = tuple(Geometry(b, tf, h, head) for b, tf, h in (
    ("le60m", 1, 15), ("le60m", 1, 30), ("le60m", 5, 12), ("le60m", 15, 4),
    ("1h-4h", 15, 8), ("1h-4h", 60, 2), ("1h-4h", 60, 4),
    ("gt4h", 240, 2), ("gt4h", 240, 3),
) for head in ("T2", "T6"))
HEADS = {"T2": (1.0, 2.0), "T6": (2.0, None)}
OUTCOMES = ("long_gross", "long_net", "short_gross", "short_net", "rand_gross", "rand_net")
FAMILY_OUTCOMES = ("fam_gross", "fam_net", "long_gross", "long_net", "short_gross", "short_net")
# Replayed families (ADR 0085 Q1 "other families' replayed candidates"; quant ruling on #466): retired arms, none an
# sr-v3 (self_retrain.PRENAMED) or H-102 (mtf-v1 units) arm, none with a USD stop band (the rule runs on FX prices).
# Their own exits are not used: each decision is labelled with every hold-bucket geometry below.
FAMILIES: dict[str, tuple[str, ...]] = {
    "box_break_close": ("MB15", "W2"),
    "sweep_absorb": ("MSA15",),
    "session_range_break": ("GS1",),
}
EXCLUDED_ARMS = tuple(sorted(RHO_EXCLUDED_ARMS))           # sr-v3 (self_retrain.PRENAMED) and H-102 units
if {a_ for arms_ in FAMILIES.values() for a_ in arms_} & RHO_EXCLUDED_ARMS:
    raise SystemExit("a replayed family uses an sr-v3 / H-102 arm")


# ------------------------------------------------------------------------------------------------------------ data

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files_for(raw: Path, inst: str) -> list[Path]:
    if inst == GOLD:
        return [raw / f"xauusd_1m_dukascopy_{y}.parquet" for y in YEARS]
    return sorted(p for y in YEARS for p in (raw / "histdata_m1" / inst).glob(f"*_1m_bid_{y}.parquet"))


def load_bars(raw: Path, inst: str) -> pd.DataFrame:
    """1m bars of `inst` in the repo's bar schema (HistData: bid only, ask = bid, no spread), learn rows only, and for
    HistData only months of tier A / A-consistent."""
    if inst == GOLD:
        b = W.read_window_bars(raw, "learn")
    else:
        b = pd.concat([pd.read_parquet(p, columns=["ts_utc", "visible_at", "bid_open", "bid_high", "bid_low",
                                                   "bid_close"]) for p in files_for(raw, inst)], ignore_index=True)
        for c in ("open", "high", "low", "close"):
            b[f"ask_{c}"] = b[f"bid_{c}"]
        b["tick_count"] = 1.0          # HistData has no volume: VWAP columns are equal-weight here (stated)
        b["spread_mean"] = 0.0
        b["spread_max"] = 0.0
        t = pd.read_csv(raw / TIERS_FILE, usecols=["key", "month", "tier"])
        good = set(t.loc[(t.key == inst) & t.tier.isin(GOOD_TIERS), "month"])
        b = b[np.isin(pd.DatetimeIndex(b["ts_utc"]).strftime("%Y-%m"), sorted(good))]
    b["ts_utc"] = pd.DatetimeIndex(b["ts_utc"]).tz_convert("UTC").as_unit("ns")
    b["visible_at"] = pd.DatetimeIndex(b["visible_at"]).tz_convert("UTC").as_unit("ns")
    b = b.drop_duplicates("ts_utc").sort_values("ts_utc")
    b = b[(b["ts_utc"] >= LEARN_LO) & (b["ts_utc"] < LEARN_HI)].reset_index(drop=True)
    if len(b) and (b["ts_utc"].min() < LEARN_LO or b["ts_utc"].max() >= LEARN_HI):
        raise RuntimeError("a non-learn bar left the loader")
    return b


def minute_grid(b: pd.DataFrame) -> pd.DataFrame:
    px = pd.DataFrame({k: (b[f"bid_{k}"].to_numpy() + b[f"ask_{k}"].to_numpy()) / 2
                       for k in ("open", "high", "low", "close")}, index=pd.DatetimeIndex(b["ts_utc"]))
    grid = pd.date_range(px.index.min().floor("D"), px.index.max().ceil("D"), freq="1min", tz="UTC", inclusive="left")
    return px.reindex(grid)


def session_day(ts: pd.DatetimeIndex) -> np.ndarray:
    t = ts.tz_convert("America/New_York") + pd.Timedelta(hours=7)
    return np.asarray(t.normalize().tz_localize(None).to_numpy().astype("datetime64[D]"))


def cells(day: np.ndarray, y: np.ndarray) -> pd.DataFrame:
    """Per-day sufficient statistics n, S = sum y, Q = sum y^2 of finite y."""
    ok = np.isfinite(y)
    df = pd.DataFrame({"day": day[ok], "y": y[ok]})
    df["y2"] = df["y"] ** 2
    g = df.groupby("day", sort=True).agg(n=("y", "size"), S=("y", "sum"), Q=("y2", "sum")).reset_index()
    return g


# ------------------------------------------------------------------------------------------------------------ candidates

def tf_bars(m: pd.DataFrame, tf_min: int) -> pd.DataFrame:
    if tf_min == 1:
        return m.dropna()
    r = m.resample(f"{tf_min}min", label="left", closed="left")
    o = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(), "low": r["low"].min(),
                      "close": r["close"].last(), "n": r["close"].count()})
    return o[o["n"] >= max(1, tf_min // 2)].drop(columns="n")


def atr14(b: pd.DataFrame) -> pd.Series:
    pc = b["close"].shift(1)
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def label(m: pd.DataFrame, g: Geometry, inst: str, dec: pd.DatetimeIndex, atr: np.ndarray) -> pd.DataFrame:
    """Both sides of the geometry-`g` bracket at each decision `dec` (a minute boundary), entry at the next minute's
    open, barriers on 1m bars, stop first on a shared minute; gross and net R. Rows whose hold is short of
    MIN_COVERAGE minutes or ends at or after LEARN_HI are dropped."""
    k_stop, tgt = HEADS[g.head]
    stop_d = k_stop * atr * np.sqrt(g.hold_bars / 1.0)
    i0 = ((dec - m.index[0]) // pd.Timedelta(minutes=1)).to_numpy().astype(np.int64)
    L = g.hold_min
    ok = (i0 >= 0) & (i0 + L <= len(m)) & np.asarray(dec + pd.Timedelta(minutes=L) <= LEARN_HI) & (stop_d > 0)
    i0, dec, stop_d = i0[ok], dec[ok], stop_d[ok]
    if not len(i0):
        return pd.DataFrame()
    idx = i0[:, None] + np.arange(L)[None, :]
    hi_, lo_, cl_, op_ = (m[c].to_numpy()[idx] for c in ("high", "low", "close", "open"))
    entry = op_[:, 0]
    ok = (np.isfinite(cl_).mean(axis=1) >= MIN_COVERAGE) & np.isfinite(entry)
    hi_, lo_, cl_, entry, stop_d, dec = hi_[ok], lo_[ok], cl_[ok], entry[ok], stop_d[ok], dec[ok]
    last = pd.DataFrame(cl_).ffill(axis=1).to_numpy()[:, -1]
    rt, ov = COST_BP.get(inst, FX_COST_BP)
    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for side in (1, -1):
        stop_px = entry - side * stop_d
        hs = (lo_ <= stop_px[:, None]) if side == 1 else (hi_ >= stop_px[:, None])
        fs = np.where(hs.any(axis=1), hs.argmax(axis=1), L)
        if tgt is not None:
            tp = entry + side * tgt * stop_d
            ht = (hi_ >= tp[:, None]) if side == 1 else (lo_ <= tp[:, None])
            ft = np.where(ht.any(axis=1), ht.argmax(axis=1), L)
        else:
            ft = np.full(len(entry), L)
        stopped = (fs < L) & (fs <= ft)
        targeted = (ft < L) & ~stopped
        r = np.where(stopped, -1.0, np.where(targeted, float(tgt or 0.0), side * (last - entry) / stop_d))
        cost = (rt * 1e-4 * entry + ov * 1e-4 * entry * stopped) / stop_d
        out[side] = (r, r - cost)
    return pd.DataFrame({"decision": dec, "day": session_day(pd.DatetimeIndex(dec)),
                         "long_gross": out[1][0], "long_net": out[1][1], "short_gross": out[-1][0],
                         "short_net": out[-1][1], "both_gross": (out[1][0] + out[-1][0]) / 2})


def candidates(m: pd.DataFrame, g: Geometry, inst: str) -> pd.DataFrame:
    """The control grid: every hold_bars-th tf bar of each session day, counted from the day's first bar (so the day's
    candidates never overlap), labelled with `label`; plus a seeded random side."""
    b = tf_bars(m, g.tf_min)
    a = atr14(b)
    close_t = b.index + pd.Timedelta(minutes=g.tf_min)
    rank = pd.Series(np.arange(len(close_t))).groupby(session_day(pd.DatetimeIndex(close_t))).cumcount().to_numpy()
    keep = (rank % g.hold_bars == 0) & np.isfinite(a.to_numpy())
    c = label(m, g, inst, pd.DatetimeIndex(close_t[keep]), a.to_numpy()[keep])
    if c.empty:
        return c
    rs = np.random.default_rng([SEED, sum(map(ord, inst)), g.tf_min, g.hold_bars, len(g.head)]).choice([1, -1], len(c))
    c["rand_gross"] = np.where(rs == 1, c["long_gross"], c["short_gross"])
    c["rand_net"] = np.where(rs == 1, c["long_net"], c["short_net"])
    return c


def family_decisions(b: pd.DataFrame, blackout: tuple[np.ndarray, np.ndarray]) -> dict[str, pd.DataFrame]:
    """Outcome-blind candidates (decision, side) of each replayed family on `b`, through the established replay
    (`history_replay._horizons`: the arm's rule, its window, the common filters and its stop band, every candidate
    taken or not). Same-minute duplicates within a family keep the first."""
    from goldbot.research import history_replay as H
    out: dict[str, pd.DataFrame] = {}
    for fam, arm_ids in FAMILIES.items():
        parts = []
        for aid in arm_ids:
            c = H._horizons(H.load_arm(aid), b, blackout)
            parts.append(pd.DataFrame({"decision": pd.DatetimeIndex(c["decision_utc"]).tz_convert("UTC").as_unit("ns"),
                                       "side": c["side"].to_numpy(float), "arm": aid}))
        f = pd.concat(parts, ignore_index=True).sort_values(["decision", "arm"], kind="stable")
        out[fam] = f.drop_duplicates("decision").reset_index(drop=True)
    return out


def family_candidates(m: pd.DataFrame, g: Geometry, inst: str, fam: pd.DataFrame) -> pd.DataFrame:
    """A family's decisions labelled with geometry `g` (ATR14 of the last g-tf bar closed at the decision), thinned
    greedily to non-overlapping holds in time; `fam_*` = the family's own side."""
    b = tf_bars(m, g.tf_min)
    a = atr14(b).to_numpy()
    from goldbot.data.timeutil import epoch_ns
    close_t = pd.DatetimeIndex(b.index + pd.Timedelta(minutes=g.tf_min))
    dec = pd.DatetimeIndex(fam["decision"])
    pos = np.searchsorted(epoch_ns(close_t), epoch_ns(dec), side="right") - 1
    ok = (pos >= 0) & np.isfinite(a[np.maximum(pos, 0)])
    dec, side, pos = dec[ok], fam["side"].to_numpy(float)[ok], pos[ok]
    keep, last = [], None
    hold = pd.Timedelta(minutes=g.hold_min)
    for i, t in enumerate(dec):
        if last is None or t >= last + hold:
            keep.append(i)
            last = t
    k = np.asarray(keep, dtype=int)
    c = label(m, g, inst, dec[k], a[pos[k]])
    if c.empty:
        return c
    sd = pd.Series(side[k], index=dec[k]).reindex(pd.DatetimeIndex(c["decision"])).to_numpy(float)
    c["fam_gross"] = np.where(sd > 0, c["long_gross"], c["short_gross"])
    c["fam_net"] = np.where(sd > 0, c["long_net"], c["short_net"])
    return c


# ------------------------------------------------------------------------------------------------------------ features

def features(b: pd.DataFrame, tf: str) -> pd.DataFrame:
    """The core-12 columns (less side) on every closed `tf` bar, computed causally on the bar series (the engine's
    builders); the decision time is the bar's close (`visible_at`)."""
    from goldbot.data.resample import mid, resample_bars
    from goldbot.features.registry import build_features
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES  # noqa: F401  (registers every feature group)
    d = b if tf == "1m" else resample_bars(b, tf)
    d = d.reset_index(drop=True)
    mb = mid(d)
    cols = {}
    for grp in FEATURE_GROUPS:
        X = build_features(mb, [grp])
        for c in CORE:
            if c in X.columns:
                cols[c] = X[c].to_numpy(float)
    out = pd.DataFrame(cols)
    out["decision"] = pd.DatetimeIndex(pd.to_datetime(d["visible_at"], utc=True))
    return out


# ------------------------------------------------------------------------------------------------------------ worker

def work(args: tuple[str, str]) -> tuple[str, dict[tuple[str, ...], pd.DataFrame], pd.Series, dict[str, Any]]:
    raw, inst = Path(args[0]), args[1]
    b = load_bars(raw, inst)
    m = minute_grid(b)
    series: dict[tuple[str, ...], pd.DataFrame] = {}
    ncand = {}
    for g in GEOMETRIES:
        c = candidates(m, g, inst)
        ncand[g.name] = int(len(c))
        if c.empty:
            continue
        day = c["day"].to_numpy()
        for col in (*OUTCOMES, "both_gross"):
            series[("out", g.name, col)] = cells(day, c[col].to_numpy(float))
    from goldbot.research import history_replay as H
    fams = family_decisions(b, H.load_blackout(raw / "calendar"))
    fams["all"] = (pd.concat(list(fams.values()), ignore_index=True).sort_values("decision", kind="stable")
                   .drop_duplicates("decision").reset_index(drop=True))
    for fam, fd in fams.items():
        ncand[f"family {fam} decisions"] = int(len(fd))
        for g in GEOMETRIES:
            c = family_candidates(m, g, inst, fd)
            ncand[f"family {fam} {g.name}"] = int(len(c))
            if c.empty:
                continue
            day = c["day"].to_numpy()
            for col in (*FAMILY_OUTCOMES, "both_gross"):
                series[(f"fam-{fam}", g.name, col)] = cells(day, c[col].to_numpy(float))
    for tf in FEATURE_TFS:
        f = features(b, tf)
        dec = pd.DatetimeIndex(f["decision"])
        for fold, (lo, hi) in FOLDS.items():
            sel = np.asarray((dec >= lo) & (dec < hi))
            fd = f[sel]
            day = session_day(pd.DatetimeIndex(fd["decision"]))
            for col in CORE:
                x = fd[col].to_numpy(float)
                mu, sd = np.nanmean(x), np.nanstd(x)
                z = (x - mu) / sd if sd > 0 else x * np.nan          # per-instrument standardised (label-blind)
                series[("feat", tf, fold, col)] = cells(day, z)
    closes = m["close"].dropna()
    daily = pd.Series(closes.to_numpy(), index=session_day(pd.DatetimeIndex(closes.index))).groupby(level=0).last()
    rets = pd.Series(np.log(daily.to_numpy(float)), index=daily.index).diff()
    info = {"bars": int(len(b)), "days": int(len(np.unique(session_day(pd.DatetimeIndex(b["ts_utc"]))))),
            "first": str(b["ts_utc"].min()), "last": str(b["ts_utc"].max()), "candidates": ncand}
    return inst, series, rets, info


# ------------------------------------------------------------------------------------------------------------ bootstrap

def week_key(days: np.ndarray) -> np.ndarray:
    iso = pd.DatetimeIndex(days.astype("datetime64[ns]")).isocalendar()
    return (iso["year"].to_numpy() * 100 + iso["week"].to_numpy()).astype(np.int64)


def icc_block(tab: pd.DataFrame, gold: str | None, B: int, seed: int, chunk: int = 250) -> dict[str, Any]:
    """Pairwise residual ICCs (within-day, cross-instrument pair and day-mean, and vs gold) and the between-instrument
    SD of instrument means, from per (inst, day) cells; point estimate and week-block bootstrap percentiles."""
    tab = tab.sort_values(["day", "inst"]).reset_index(drop=True)
    days, day_idx = np.unique(tab["day"].to_numpy(), return_inverse=True)
    insts, inst_idx = np.unique(tab["inst"].to_numpy(), return_inverse=True)
    starts = np.r_[0, np.flatnonzero(np.diff(day_idx)) + 1]
    n, S, Q = (tab[c].to_numpy(float) for c in ("n", "S", "Q"))
    g = (tab["inst"].to_numpy() == gold).astype(float) if gold is not None else np.zeros(len(tab))
    onehot = np.zeros((len(tab), len(insts)))
    onehot[np.arange(len(tab)), inst_idx] = 1.0
    weeks, wk_of_day = np.unique(week_key(days), return_inverse=True)
    rng = np.random.default_rng(seed)

    def stats(Wd: np.ndarray) -> dict[str, np.ndarray]:          # Wd: b x days
        wc = Wd[:, day_idx]
        mean = (wc * S) @ onehot / np.maximum((wc * n) @ onehot, 1e-12)      # b x insts
        mc = mean[:, inst_idx]
        Sc = S - n * mc
        Qc = Q - 2 * mc * S + n * mc * mc
        var = (wc * Qc).sum(1) / (wc * n).sum(1)
        within = (wc * (Sc * Sc - Qc)).sum(1) / (wc * (n * n - n)).sum(1) / var
        T = np.add.reduceat(Sc, starts, axis=1)
        sumS2 = np.add.reduceat(Sc * Sc, starts, axis=1)
        Nd = np.add.reduceat(n, starts)
        sumn2 = np.add.reduceat(n * n, starts)
        pairs_x = (Wd * (Nd * Nd - sumn2)).sum(1)
        cross_pair = (Wd * (T * T - sumS2)).sum(1) / pairs_x / var
        ybar = Sc / n
        var_a = (wc * ybar * ybar).sum(1) / wc.sum(1)
        A = np.add.reduceat(ybar, starts, axis=1)
        A2 = np.add.reduceat(ybar * ybar, starts, axis=1)
        C = np.add.reduceat(np.ones_like(n), starts)
        cross_agg = (Wd * (A * A - A2)).sum(1) / (Wd * (C * C - C)).sum(1) / var_a
        out = {"within_day": within, "cross_pair": cross_pair, "cross_agg": cross_agg,
               "between_inst_sd": mean.std(axis=1, ddof=1) if len(insts) > 1 else np.full(len(Wd), np.nan)}
        if gold is not None and len(insts) > 1:
            Gs = np.add.reduceat(Sc * g, starts, axis=1)
            Gn = np.add.reduceat(n * g, starts)
            out["vs_gold_pair"] = (Wd * Gs * (T - Gs)).sum(1) / (Wd * Gn * (Nd - Gn)).sum(1) / var
            Ga = np.add.reduceat(ybar * g, starts, axis=1)
            hg = np.add.reduceat(g, starts)
            out["vs_gold_agg"] = (Wd * Ga * (A - Ga)).sum(1) / (Wd * hg * (C - hg)).sum(1) / var_a
        return out

    with np.errstate(invalid="ignore", divide="ignore"):
        point = {k: float(v[0]) for k, v in stats(np.ones((1, len(days)))).items()}
        boots: dict[str, list[np.ndarray]] = {}
        for lo in range(0, B, chunk):
            b = min(chunk, B - lo)
            cnt = rng.multinomial(len(weeks), np.full(len(weeks), 1 / len(weeks)), size=b).astype(float)
            for k, v in stats(cnt[:, wk_of_day]).items():
                boots.setdefault(k, []).append(v)
    res: dict[str, Any] = {"days": int(len(days)), "weeks": int(len(weeks)), "instruments": int(len(insts)),
                           "obs": int(n.sum()), "m_bar_w": float((n * n).sum() / n.sum())}
    for k, p in point.items():
        bs = np.concatenate(boots[k])
        res[k] = {"point": p, "lo95": float(np.nanpercentile(bs, 2.5)), "hi95": float(np.nanpercentile(bs, 97.5))}
    return res


def job(args: tuple[str, str, pd.DataFrame, str | None, int, int]) -> tuple[str, str, dict[str, Any]]:
    key, cls, tab, gold, B, seed = args
    return key, cls, icc_block(tab, gold, B, seed)


# ------------------------------------------------------------------------------------------------------------ main

def rounded(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 5)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


def git_blob(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "research")
    a = ap.parse_args()
    if a.workers > 4:
        raise SystemExit("at most 4 workers")
    insts = sorted({i for v in CLASSES.values() for i in v})
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(work, [(str(a.raw), i) for i in insts]))
    per: dict[tuple[str, ...], list[pd.DataFrame]] = {}
    for inst, series, _, _ in res:
        for k, v in series.items():
            if len(v):
                per.setdefault(k, []).append(v.assign(inst=inst))
    rets = pd.DataFrame({r[0]: r[2] for r in res})
    info = {r[0]: r[3] for r in res}

    # PC1 orientation per class from learn daily close-to-close returns (prices only)
    pc1: dict[str, dict[str, int]] = {}
    for cls, members in CLASSES.items():
        if len(members) < 2:
            continue
        x = rets[list(members)].dropna()
        x = (x - x.mean()) / x.std()
        _, vec = np.linalg.eigh(np.cov(x.to_numpy().T))
        load = vec[:, -1] if vec[:, -1].sum() >= 0 else -vec[:, -1]
        sign = {m_: int(np.sign(float(val)) or 1) for m_, val in zip(members, load, strict=True)}
        if GOLD in sign and sign[GOLD] < 0:
            sign = {k: -s for k, s in sign.items()}
        pc1[cls] = sign
    # per-instrument sign (D-13 ruling (b)): each member signed by its learn daily-return correlation with gold
    gsign: dict[str, dict[str, int]] = {}
    for cls, members in CLASSES.items():
        if GOLD in members and len(members) > 1:
            x = rets[list(members)].dropna()
            gsign[cls] = {m_: (1 if m_ == GOLD else int(np.sign(float(x[m_].corr(x[GOLD]))) or 1)) for m_ in members}

    jobs = []
    seed = SEED
    for key, parts in per.items():
        full = pd.concat(parts, ignore_index=True)
        directional = ((key[0] == "feat" and key[3] in DIRECTIONAL)
                       or (key[0] != "feat" and key[2] != "both_gross"))
        for cls, members in CLASSES.items():
            tab = full[full["inst"].isin(members)]
            if tab.empty or tab["day"].nunique() < 20:
                continue
            gold = GOLD if (cls in VS_GOLD and GOLD in members) else None
            orients: list[tuple[str, dict[str, int] | None]] = [("raw", None)]
            if directional and cls in pc1:
                orients.append(("pc1", pc1[cls]))
            if directional and cls in gsign and gsign[cls] != pc1.get(cls):
                orients.append(("goldsign", gsign[cls]))
            for oname, osign in orients:
                t = tab
                if osign is not None:
                    s = t["inst"].map(osign).to_numpy(float)
                    t = t.assign(S=t["S"].to_numpy() * s)
                seed += 1
                jobs.append(("|".join(key) + f"|{oname}", cls, t[["inst", "day", "n", "S", "Q"]], gold, a.boot, seed))
    with ProcessPoolExecutor(a.workers) as ex:
        out_rows = list(ex.map(job, jobs, chunksize=4))

    cells_out = []
    for jkey, cls, r in out_rows:
        kind, *rest = jkey.split("|")
        row: dict[str, Any] = {"kind": kind, "class": cls, "key": jkey, **{rk: rv for rk, rv in r.items()
                                                                           if not isinstance(rv, dict)}}
        if kind != "feat":
            geom, col, orient = rest
            row.update(source="grid" if kind == "out" else kind.removeprefix("fam-"),
                       geometry=geom, outcome=col, orientation=orient,
                       band=next(g.band for g in GEOMETRIES if g.name == geom),
                       head=geom.split()[-1])
        else:
            tf, fold, col, orient = rest
            row.update(tf=tf, fold=fold, column=col, orientation=orient)
        for rk, rv in r.items():
            if isinstance(rv, dict):
                for kk, vv in rv.items():
                    row[f"{rk}.{kk}"] = vv
        cells_out.append(row)
    tab = pd.DataFrame(cells_out)

    def worst(t: pd.DataFrame, stat: str) -> dict[str, Any]:
        col = f"{stat}.hi95"
        t = t[t[col].notna()]
        if t.empty:
            return {"upper95": None}
        top = t.iloc[int(np.argmax(t[col].to_numpy(float)))]
        return {"upper95": float(top[col]), "point_at_max": float(top[f"{stat}.point"]),
                "argmax": str(top["key"]), "point_range": [float(t[f"{stat}.point"].min()),
                                                            float(t[f"{stat}.point"].max())],
                "days": [int(t["days"].min()), int(t["days"].max())], "variants": int(len(t))}

    def summarise(src: pd.DataFrame) -> dict[str, dict[str, Any]]:
        """rho_u,day, rho_u,inst (day-mean and pair), vs gold, and the between-instrument SD of mean gross T2 R, per
        (class, hold bucket): each the largest upper 95 % bound over the variants in `src`."""
        o = src[src["outcome"] != "both_gross"]
        res: dict[str, dict[str, Any]] = {"rho_u_day": {}, "rho_u_inst": {}, "rho_u_inst_vs_gold": {}, "tau_sd": {}}
        for band in ("le60m", "1h-4h", "gt4h"):
            for cls in CLASSES:
                t = o[(o["band"] == band) & (o["class"] == cls)]
                k = f"{cls}|{band}"
                res["rho_u_day"][k] = worst(t, "within_day")
                if cls != "gold":
                    res["rho_u_inst"][k] = {"agg": worst(t, "cross_agg"), "pair": worst(t, "cross_pair")}
                if cls in VS_GOLD:
                    res["rho_u_inst_vs_gold"][k] = {"agg": worst(t, "vs_gold_agg"), "pair": worst(t, "vs_gold_pair")}
                tt = src[(src["band"] == band) & (src["class"] == cls) & (src["head"] == "T2")
                         & src["outcome"].isin(["both_gross", "long_gross"]) & (src["orientation"] == "raw")]
                if cls != "gold" and len(tt):
                    res["tau_sd"][k] = worst(tt, "between_inst_sd")
        return res

    def up(w: dict[str, Any] | None) -> float | None:
        return None if not w else w.get("upper95")

    def mx(*vals: float | None) -> float | None:
        v = [x for x in vals if x is not None and np.isfinite(x)]
        return max(v) if v else None

    out_tab = tab[tab["kind"] != "feat"]
    by_source = {"grid": summarise(out_tab[out_tab["source"] == "grid"]),
                 "families": summarise(out_tab[out_tab["source"] != "grid"])}
    for fam in (*FAMILIES, "all"):
        by_source[f"family:{fam}"] = summarise(out_tab[out_tab["source"] == fam])
    binding: dict[str, dict[str, Any]] = {"rho_u_day": {}, "rho_u_inst_day_mean": {},
                                          "rho_u_inst_vs_gold_day_mean": {}, "tau_a": {}}
    grid_s, fam_s, pool_s = by_source["grid"], by_source["families"], by_source["family:all"]

    def cap(v: float | None) -> float | None:          # a correlation bound above 1 is sampling noise: capped
        return None if v is None else min(1.0, v)

    for bk in grid_s["rho_u_day"]:
        g_, f_ = up(grid_s["rho_u_day"][bk]), up(fam_s["rho_u_day"].get(bk))
        e: dict[str, Any] = {"grid": g_, "families": f_, "families_pooled": up(pool_s["rho_u_day"].get(bk)),
                             "max": cap(mx(g_, f_))}
        if bk.endswith("|le60m"):
            e["rho_value"] = max(0.05, e["max"]) if e["max"] is not None else None
        binding["rho_u_day"][bk] = e
    for name, src_key in (("rho_u_inst_day_mean", "rho_u_inst"), ("rho_u_inst_vs_gold_day_mean", "rho_u_inst_vs_gold")):
        for bk, w in grid_s[src_key].items():
            g_, f_ = up(w["agg"]), up(fam_s[src_key].get(bk, {}).get("agg"))
            binding[name][bk] = {"grid": g_, "families": f_,
                                 "families_pooled": up(pool_s[src_key].get(bk, {}).get("agg")), "max": cap(mx(g_, f_))}
    for bk, w in grid_s["tau_sd"].items():
        g_, f_ = up(w), up(fam_s["tau_sd"].get(bk))
        m_ = mx(g_, f_)
        tau = max(0.01, m_) if m_ is not None else None
        binding["tau_a"][bk] = {"grid": g_, "families": f_, "families_pooled": up(pool_s["tau_sd"].get(bk)), "tau_a": tau,
                               "lambda_a": 1.35 / tau ** 2 if tau else None}
    rho_u_day = {k: v for k, v in binding["rho_u_day"].items()}
    f = tab[tab["kind"] == "feat"]
    rho_x: dict[str, Any] = {}
    for keys, t in f.groupby(["fold", "tf", "class", "column"], sort=True):
        fold, tf, cls, col = (str(x) for x in keys)
        e = {"day": worst(t, "within_day")}
        if cls != "gold":
            e["inst_agg"] = worst(t, "cross_agg")
            e["inst_pair"] = worst(t, "cross_pair")
        if cls in VS_GOLD:
            e["vs_gold_agg"] = worst(t, "vs_gold_agg")
        rho_x[f"{fold}|{tf}|{cls}|{col}"] = e

    script = Path(__file__).resolve()
    data_files = sorted({p for i in insts for p in files_for(a.raw, i) if p.exists()})
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = {
        "schema": "rho_day_measurement/v2",
        "item": "D-12 (quant rulings on PR #460 rev 3; ADR 0085 amendment 2026-11-25 Q1, R13)",
        "window": {"name": "learn", "from": str(LEARN_LO), "to_exclusive": str(LEARN_HI),
                   "feature_folds": {k: [str(v[0]), str(v[1])] for k, v in FOLDS.items()}},
        "never_read": ["test_old 2006-02..2009-12", "confirm 2018-01..2025-09", "holdout 2025-10-01 onward"],
        "candidates": [
            {"kind": "control", "name": "control_grid",
             "how": "every hold_bars-th tf bar of each NY-17:00 session day; same times on every instrument"},
            *({"kind": "family", "name": fam, "arms": list(arms), "replay": "goldbot/research/history_replay.py _horizons",
               "labelled_with": "the hold-bucket geometries below (the family's own exits are not used); "
                                "greedy non-overlap per instrument"}
              for fam, arms in FAMILIES.items()),
        ],
        "excluded_arms": list(EXCLUDED_ARMS),
        "stated_deviations": [
            "stop floor 1.5 x session-median true range (sr-v3 template rule) not applied; stop = k x ATR14_tf x "
            "sqrt(hold_bars)",
            "HistData has no volume: VWAP-based columns are equal-weight on FX and XAG",
            "XAG and FX bps costs are assumptions (gold: W2 cost of record)",
            "2h has no feature rho_x (not a repo timeframe on main)",
        ],
        "geometries": [g.name for g in GEOMETRIES],
        "bootstrap": {"block": "ISO week of the NY-17:00 session day, all instruments jointly", "B": a.boot,
                      "upper": "97.5th percentile", "seed": SEED},
        "script": {"path": str(script.relative_to(ROOT)), "sha256": sha256_file(script), "git_blob": git_blob(script),
                   "base_commit": head},
        "data": {"tiers_file": {"path": f"raw/{TIERS_FILE}", "sha256": sha256_file(a.raw / TIERS_FILE),
                                "tiers": list(GOOD_TIERS)},
                 "bars": [{"path": f"raw/{p.relative_to(a.raw)}", "sha256": sha256_file(p)} for p in data_files],
                 "instruments": info},
        "classes": {k: list(v) for k, v in CLASSES.items()},
        "not_measurable": {"index": "no A/A-consistent learn month (r11-v2.1)", "energy": "XTIUSD 3 A months",
                           "crosses": "EURAUD, EURGBP, GBPAUD: tier B/C only", "XAUEUR": "tier B only"},
        "pc1_orientation": pc1,
        "gold_sign_orientation": gsign,
        "rho_x": rho_x,
        "binding": binding,
        "by_source": by_source,
        "rho_day_le_60m": {cls: rho_u_day[f"{cls}|le60m"].get("rho_value") for cls in CLASSES},
    }
    a.out.mkdir(parents=True, exist_ok=True)
    # every cell (point, 95 % interval, counts) in a gzip CSV beside the summary (the repo caps files at 800 kB)
    cells_path = a.out / "rho-measurement-2026-11-cells.csv.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(tab.sort_values("key").to_csv(index=False, float_format="%.6g").encode())
    cells_path.write_bytes(buf.getvalue())
    rel = cells_path.relative_to(ROOT) if cells_path.is_relative_to(ROOT) else cells_path
    out["cells_file"] = {"path": str(rel), "sha256": sha256_file(cells_path),
                         "rows": int(len(tab))}
    (a.out / "rho-measurement-2026-11.json").write_text(json.dumps(rounded(out), indent=1, default=str) + "\n")
    print(json.dumps({k: out[k] for k in ("rho_day_le_60m",)}, indent=1))


if __name__ == "__main__":
    main()
