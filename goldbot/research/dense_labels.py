"""dl-v1: the dense label factory (sr-v3 card 2.1, data item D-5; ADR 0098 R-H/R-I, ADR 0102, quant review of PR #462
C5/C6). Every fs-v1 row (every closed bar of an instrument x tf, phase rows included) is labelled on BOTH sides under
the eight pre-named exit templates T0-T7: 16 heads per bar. Market data, rows and labels stay local (~/tmp), never
committed or published (ADR 0048).

The label (`LabelSpec`, id dl-v1; a change is a new spec id and re-keys the cache):
- Entry (C5): the open of the first 1m minute strictly after the row's `decision_utc` (entry_utc > decision_utc for
  every row, tested), side-aware price: a long is judged on the bid it exits on, a short on the ask. Gross R is the
  move of that exit-side quote from its own value at entry, in units of the template's initial risk. With a constant
  spread it is the mid move exactly; the spread is charged once in cost(x), never in the label.
- Base distance d0 = ATR14 (Wilder, mid, the tf's decision bars, known at the decision bar's close) x sqrt(H / h_ref)
  with h_ref = H until D-4 freezes it (factor 1), floored at 1.5 x the median 1m true range of the 1,440 minutes up
  to the decision ("session-median TR").
- Templates: stop / target / trail in units of d0; R = the template's own initial stop distance (T0 has none: R = d0).
  A stop order (initial, break-even or trailed) is `barrier_hit` "stop" (`stop_moved` marks a moved one); a gapped
  stop fills at the minute's open (adverse); a gapped target fills at the target (never a gap gain); a minute that
  touches both is a stop (conservative). Trail and break-even move the stop from the NEXT minute on.
- Time barrier H by tf (card 2.1). Intraday: the last minute whose close is at or before entry + H. 1d / 1w (C5): the
  time exit is the close of the last decision bar of the same grid (fs-v1's feature-day bars, 13:30 New York) at or
  before decision + H, so a close-to-close label runs feature-day close to feature-day close; raw/daily bars are never
  read.
- Clipped at +-10 R (`gross_r`); the raw value is kept (`gross_r_raw`, the 2x-cost leg of ADR 0102 6 reads it).
- A label whose horizon passes the end of the loaded minutes is `label_ok` False (`truncated`), never shortened.
- Point in time: `available_utc` is the fs-v1 row's (the as-of join key of its features); `label_available_utc` is
  the latest effective visibility of every minute up to the horizon end (a revised minute in the horizon moves it).

Cost (ADR 0102 3): the label is GROSS. `LABEL_COST_COLS` (entry, risk_usd, barrier_hit) are persisted with
`max_hold_s`, so cost(x) = decision_cost.cost_x is computed from decision-time quantities only, never from r_mid - r.
p_stop and the night share are label statistics fitted by a booked stage on learn rows before its judged block
(`decision_cost.priors`); the factory never computes them (it reads no label statistic), so net R is computed at read
by `net_r` with the caller's priors and the ub95 rule max(W2, the engine cost of record's ub95 in bps) per cell
(`rt_bp`). Rows built here carry `cost_version` "w2-point": a judged read on W2 point values alone is not a gate read.

Weights (card 2.1, ADR 0085 Q3): one event per bar (all 16 heads), interval = [entry minute, horizon end minute] on
the instrument's market-minute grid. The horizon, not the realised exit, defines the interval: a realised-exit
interval would make a stopped label's weight depend on its outcome. `u_tf` = mean of 1/concurrency over the event's
minutes within its instrument x tf (phase 0 events only: phases add no concurrency and no n_eff); `u_inst` the same
across every tf built for the instrument (the instrument-minute counted once, for cross-unit claims). A phase-p row's
weight is its u / P. n_eff per cell (`n_eff`) follows the card's 2.2 method on the measured uniqueness.

Tiers (ADR 0085 A4, r11-v2.1; ADR 0104 4): the worst tier over the session months from (decision - `input_lookback`
of the requested column set) to the horizon end (quant C-3 / R-a: registry lookbacks, plus the d1 context). Intraday
rows need A / A-consistent for every template (their features do); 1d/1w rows need A / A-consistent for barrier
templates, T0 (close-to-close) takes B too.
The tiers must grade the rows' own source (`<source>:<instrument>`, quant C3; the D-1 served file grades
dukascopy:XAUUSD and histdata:<FX>). Every store is SEALED (card ruling 5): labels are produced and counted
outcome-blind (`skeleton`), but `load_labels` refuses label values until the tiers file's sha256 is in
`RECORDED_TIERS_SHA256` (ADR 0085 A4; card test 21).

Folds (C6): `purge` = `feature_store.purge` on [window_from_utc, horizon_end_utc] vs a test row's [decision_utc,
horizon_end_utc + embargo], embargo = max(1 d, H, feature lookback) (`embargo`). `group_id` counts n_eff, it never replaces the
purge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.config import tf_seconds
from goldbot.data import holdout_guard
from goldbot.data import htf_bars as H
from goldbot.data.daily import session_date
from goldbot.data.resample import BAR_COLUMNS, resample_bars
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features import build_features
from goldbot.features.registry import FEATURES as REGISTRY
from goldbot.research import decision_cost as DC
from goldbot.research import feature_store as F
from goldbot.research import windows as WN
from goldbot.research.self_retrain import FEATURES as SR_FEATURES

log = logging.getLogger("goldbot.dense_labels")

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path.home() / "tmp" / "dense-labels"
MINUTE_NS = 60 * 10**9
DAY_S = 86_400

# --------------------------------------------------------------------------------------------- the spec (card 2.1)
LABEL_COST_COLS: tuple[str, ...] = ("entry", "risk_usd", "barrier_hit")   # = history_replay.LABEL_COST_COLS


class Template(FrozenRecord):
    name: str
    stop_k: float | None            # initial stop in d0; None = no stop (T0)
    target_k: float | None          # target in d0; None = no target
    trail_k: float | None = None    # trailing stop distance in d0 from the best exit-side quote
    be_at_r: float | None = None    # stop to the entry once the best quote reaches this many R


TEMPLATES: tuple[Template, ...] = (
    Template(name="T0", stop_k=None, target_k=None),
    Template(name="T1", stop_k=1.0, target_k=1.0),
    Template(name="T2", stop_k=1.0, target_k=2.0),
    Template(name="T3", stop_k=1.0, target_k=3.0),
    Template(name="T4", stop_k=1.5, target_k=1.5),
    Template(name="T5", stop_k=1.0, target_k=None, trail_k=1.0),
    Template(name="T6", stop_k=2.0, target_k=None),
    Template(name="T7", stop_k=1.0, target_k=3.0, be_at_r=1.0),
)
CLOSE_TO_CLOSE = frozenset({"T0"})          # the only template a B month labels (1d/1w)
H_BY_TF: dict[str, int] = {"1m": 3_600, "5m": 4 * 3_600, "15m": 8 * 3_600, "30m": 16 * 3_600, "1h": DAY_S,
                           "2h": 2 * DAY_S, "4h": 5 * DAY_S, "1d": 10 * DAY_S, "1w": 20 * DAY_S}
GRID_TIME_EXIT_TFS = frozenset({"1d", "1w"})    # C5: time exit on the fs-v1 decision grid (feature-day closes)


class LabelSpec(FrozenRecord):
    spec_id: str
    templates: tuple[Template, ...]
    h_by_tf: dict[str, int]
    clip_r: float
    atr_n: int
    h_ref: str                      # "H": sqrt(H / h_ref) = 1 until D-4 freezes the W2-2 h_ref
    floor_tr_mult: float
    floor_window_min: int
    sides: tuple[int, ...]
    judged_head: str
    lookback_bars: int              # longest named lookback in the sr-v2 set (atr_ratio_14_100's slow leg)
    ctx_lookback_days: int          # d1 context columns' longest lookback (d1_atr_ratio_14_100), calendar days
    rho_day: float                  # card 2.2 / ADR 0085 Q1, as shipped
    feature_spec: str               # the fs-v1 spec hash the rows come from


SPEC = LabelSpec(spec_id="dl-v1", templates=TEMPLATES, h_by_tf=H_BY_TF, clip_r=10.0, atr_n=14, h_ref="H",
                 floor_tr_mult=1.5, floor_window_min=1_440, sides=(1, -1), judged_head="T2", lookback_bars=100,
                 ctx_lookback_days=140, rho_day=0.25, feature_spec=F.spec_hash())

# Card ruling 5 / test 21: label VALUES are read only under a tiers file whose sha256 ADR 0085 A4 records. Empty: every
# store is sealed (labels produced, counted outcome-blind, never read). A member must appear in ADR 0085 (test).
# Candidate: the D-1 served file tiers_served_r11-v2.1.csv, sha256 ff653ea7... (PR #467), not yet recorded.
RECORDED_TIERS_SHA256: frozenset[str] = frozenset()
RESERVE_FROM = pd.Timestamp("2026-10-01", tz="UTC")     # N12: odd ISO weeks from here never train

ROW_KEYS = ("instrument", "tf", "phase", "group_id", "decision_utc", "available_utc", "window_from_utc", "source",
            "bid_only")
SKELETON = (*ROW_KEYS, "side", "template", "entry_utc", "entry", "risk_usd", "base_dist", "horizon_end_utc",
            "max_hold_s", "label_available_utc", "label_ok", "quote_basis", "tier", "usable", "cost_rt_bp",
            "cost_version", "spec_id")
LABEL_VALUES = ("gross_r_raw", "gross_r", "clipped", "barrier_hit", "label_exit_utc", "stop_moved")
COLUMNS = (*SKELETON, *LABEL_VALUES)

CODE_FILES: tuple[str, ...] = ("goldbot/research/dense_labels.py", "goldbot/research/decision_cost.py",
                               "goldbot/data/holdout_guard.py")


class SealedStore(PermissionError):
    """Label values of a store that may be produced but not read yet (card ruling 5: gold before D-1)."""


def _blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@lru_cache(maxsize=1)
def code_hash() -> str:
    """This module, decision_cost, the holdout guard and fs-v1's own code hash."""
    parts = [f"{f}:{_blob_sha(ROOT / f)}" for f in CODE_FILES] + [f"fs:{F.code_hash()}"]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def spec_hash(spec: LabelSpec = SPEC) -> str:
    return hashlib.sha256(json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def embargo(tf: str, spec: LabelSpec = SPEC) -> pd.Timedelta:
    """max(1 d, H, feature lookback): the lookback is `lookback_bars` decision bars (x 7/5 for the weekend on intraday
    and daily bars) and, for an intraday tf with d1 context, `ctx_lookback_days`."""
    sec = tf_seconds(tf)
    lb = spec.lookback_bars * sec * (1.0 if tf == "1w" else 7 / 5)
    if sec < DAY_S:
        lb = max(lb, spec.ctx_lookback_days * DAY_S)
    return pd.Timedelta(seconds=max(DAY_S, spec.h_by_tf[tf], lb))


# --------------------------------------------------------------------------------------------- price arrays
def _ns(ts: Any) -> np.ndarray:
    return epoch_ns(utc_index(pd.Series(ts)) if not isinstance(ts, pd.DatetimeIndex) else ts)


def _to_utc(ns: np.ndarray) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(ns, dtype=np.int64), unit="ns", utc=True))


class Minutes:
    """The instrument's 1m bars as arrays (effective visibility, mid true range floor, cumulative visibility)."""

    def __init__(self, minutes: pd.DataFrame, spec: LabelSpec = SPEC):
        holdout_guard.refuse(minutes, what="dl-v1 label minutes")
        m = H.effective_minutes(minutes)
        self.ts = _ns(m["ts_utc"])
        self.vis = _ns(m["visible_at"])
        self.vis_cummax = np.maximum.accumulate(self.vis) if len(self.vis) else self.vis
        self.bid = {k: m[f"bid_{k}"].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
        self.ask = {k: m[f"ask_{k}"].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
        self.bid_only = bool(m["bid_only"].any()) if "bid_only" in m.columns else False
        mid = {k: (self.bid[k] + self.ask[k]) / 2 for k in ("open", "high", "low", "close")}
        self.mid = mid
        self.mid_open = mid["open"]
        self.spec = spec
        self.tr_floor = _tr_median(mid, spec) * spec.floor_tr_mult
        # the latest visibility over each floor window: a window holding a minute revised after the decision is
        # recomputed on the minutes visible by then (`floor_at`), never read from the rolling value
        self.win_vis_max = F._trailing_max(self.vis, spec.floor_window_min) if len(self.vis) else self.vis
        self.frame = m

    def __len__(self) -> int:
        return len(self.ts)

    def floor_at(self, dec: np.ndarray) -> np.ndarray:
        """1.5 x the median 1m true range over the `floor_window_min` minutes up to each decision, from the minutes
        VISIBLE by the decision: the window ends at the last minute closed by the decision (time order; `vis` is not
        sorted once a minute is revised) and a minute revised after the decision is left out (code review LOW-1)."""
        mi = np.searchsorted(self.ts + MINUTE_NS, dec, side="right") - 1
        out = np.where(mi >= 0, self.tr_floor[np.clip(mi, 0, None)], np.nan)
        late = np.flatnonzero((mi >= 0) & (self.win_vis_max[np.clip(mi, 0, None)] > dec))
        W = self.spec.floor_window_min
        for i in late:
            a = max(0, int(mi[i]) - W + 1)
            seen = np.flatnonzero(self.vis[a:int(mi[i]) + 1] <= dec[i]) + a
            sub = {k: v[seen] for k, v in self.mid.items()}
            med = _tr_median(sub, self.spec, rolling=False)
            out[i] = med * self.spec.floor_tr_mult if len(seen) >= 60 else np.nan
        return out


def _tr_median(mid: Mapping[str, np.ndarray], spec: LabelSpec, *, rolling: bool = True) -> Any:
    prev = np.concatenate(([np.nan], mid["close"][:-1]))
    tr = np.fmax(np.fmax(mid["high"] - mid["low"], np.abs(mid["high"] - prev)), np.abs(mid["low"] - prev))
    if not rolling:
        return float(np.nanmedian(tr)) if len(tr) else np.nan
    return pd.Series(tr).rolling(spec.floor_window_min, min_periods=60).median().to_numpy()


def decision_bars(m: Minutes, tf: str, phase: int = 0, spec: LabelSpec = SPEC) -> pd.DataFrame:
    """The tf decision bars of the minutes on fs-v1's grid (phase shift as feature_store.compute), with `atr` (Wilder
    ATR(atr_n) of mid, known at the bar's close) and `close_ns` (the bar's visible_at)."""
    shift = pd.Timedelta(minutes=phase)
    src = m.frame[BAR_COLUMNS]
    if phase:
        src = src.assign(ts_utc=m.frame["ts_utc"] - shift, visible_at=m.frame["visible_at"] - shift)
    dec = resample_bars(src, tf, drop_late=True)       # as fs-v1: a bar holding a revised minute is no decision bar
    if phase:
        dec["ts_utc"] = utc_index(dec["ts_utc"]) + shift
        dec["visible_at"] = utc_index(dec["visible_at"]) + shift
    h = (dec["bid_high"] + dec["ask_high"]).to_numpy(float) / 2
    lo = (dec["bid_low"] + dec["ask_low"]).to_numpy(float) / 2
    c = (dec["bid_close"] + dec["ask_close"]).to_numpy(float) / 2
    prev = np.concatenate(([np.nan], c[:-1]))
    tr = np.fmax(np.fmax(h - lo, np.abs(h - prev)), np.abs(lo - prev))
    atr = pd.Series(tr).ewm(alpha=1 / spec.atr_n, adjust=False, min_periods=spec.atr_n).mean().to_numpy()
    return pd.DataFrame({"close_ns": _ns(dec["visible_at"]), "atr": atr})


# --------------------------------------------------------------------------------------------- first passage
def _quotes(m: Minutes, side: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(open, high, low, close) of the exit-side quote, oriented so that up is favourable: the bid for a long, the
    negated ask for a short (its high is -ask_low)."""
    if side > 0:
        return m.bid["open"], m.bid["high"], m.bid["low"], m.bid["close"]
    return -m.ask["open"], -m.ask["low"], -m.ask["high"], -m.ask["close"]


def first_passage(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, e: np.ndarray, z: np.ndarray,
                  d0: np.ndarray, tpl: Template, *, cells: int = 3_000_000) -> dict[str, np.ndarray]:
    """The template's exit on long-oriented quote arrays for entries at minute e (open) and time exit at minute z
    (close), z >= e. Returns gross_r_raw, barrier (0 time, 1 stop, 2 target), exit index, stop_moved, risk."""
    n = len(e)
    risk = d0 * (tpl.stop_k if tpl.stop_k is not None else 1.0)
    out = {"gross": np.full(n, np.nan), "hit": np.zeros(n, np.int8), "exit": z.copy(),
           "moved": np.zeros(n, bool), "risk": risk}
    if not n:
        return out
    span = (z - e + 1).astype(np.int64)
    order = np.argsort(span, kind="stable")          # chunk by similar spans: no long row pads a short chunk
    a = 0
    while a < n:
        L = int(span[order[min(n - 1, a + max(1, cells // max(1, int(span[order[a]]))) - 1)]])
        step = max(1, cells // L)
        sel = order[a:a + step]
        L = int(span[sel].max())
        a += len(sel)
        ee, zz, dd, rr = e[sel], z[sel], d0[sel], risk[sel]
        J = ee[:, None] + np.arange(L)[None, :]
        valid = J <= zz[:, None]
        Jc = np.minimum(J, len(o) - 1)
        Op, Hh, Lo = o[Jc], h[Jc], lo[Jc]
        entry = o[ee]
        if tpl.stop_k is not None:
            s0 = entry - rr
            S = np.broadcast_to(s0[:, None], (len(sel), L)).copy()
        else:
            s0 = np.full(len(sel), -np.inf)
            S = np.full((len(sel), L), -np.inf)
        if tpl.trail_k is not None or tpl.be_at_r is not None:
            best = np.maximum.accumulate(np.where(valid, Hh, -np.inf), axis=1)
            prev_best = np.concatenate([entry[:, None], np.maximum(best[:, :-1], entry[:, None])], axis=1)
            if tpl.trail_k is not None:
                S = np.maximum(S, prev_best - (tpl.trail_k * dd)[:, None])
            if tpl.be_at_r is not None:
                S = np.where(prev_best >= (entry + tpl.be_at_r * rr)[:, None], np.maximum(S, entry[:, None]), S)
        T = (entry + tpl.target_k * dd) if tpl.target_k is not None else np.full(len(sel), np.inf)
        stop_hit = valid & (Lo <= S)
        tgt_hit = valid & (Hh >= T[:, None])
        anyh = stop_hit | tgt_hit
        first = anyh.argmax(axis=1)
        r = np.arange(len(sel))
        has = anyh[r, first]
        is_stop = has & stop_hit[r, first]
        px = np.where(is_stop, np.minimum(Op[r, first], S[r, first]), np.where(has, T, c[zz]))
        out["gross"][sel] = (px - entry) / rr
        out["hit"][sel] = np.where(is_stop, 1, np.where(has, 2, 0))
        out["exit"][sel] = np.where(has, ee + first, zz)
        out["moved"][sel] = is_stop & (S[r, first] > s0)
    return out


# --------------------------------------------------------------------------------------------- labelling
HIT_NAMES = np.array(["time", "stop", "target"], dtype=object)


def entry_and_horizon(m: Minutes, rows: pd.DataFrame, tf: str, grid_close_ns: np.ndarray | None,
                      spec: LabelSpec = SPEC) -> dict[str, np.ndarray]:
    """Per row: entry minute e (first minute opening strictly after decision_utc, C5), time-exit minute z, horizon end
    (ns), ok (the horizon lies inside the loaded minutes)."""
    dec = _ns(rows["decision_utc"])
    n_m = len(m)
    e = np.searchsorted(m.ts, dec, side="right")
    ok = e < n_m
    ec = np.minimum(e, max(n_m - 1, 0))
    Hns = spec.h_by_tf[tf] * 10**9
    if tf in GRID_TIME_EXIT_TFS:
        if grid_close_ns is None:
            raise ValueError(f"{tf}: the time exit needs the decision grid's closes")
        g = np.searchsorted(grid_close_ns, dec + Hns, side="right") - 1
        end = np.where(g >= 0, grid_close_ns[np.clip(g, 0, None)], dec)
        ok &= end > dec                                     # at least one later grid close
    else:
        end = m.ts[ec] + Hns
    last_close = int(m.ts[-1]) + MINUTE_NS if n_m else 0
    ok &= end <= last_close
    z = np.searchsorted(m.ts + MINUTE_NS, end, side="right") - 1
    ok &= z >= e
    z = np.where(ok, z, ec)
    return {"e": ec, "z": z, "end": end, "ok": ok, "has_entry": e < n_m}


def label_rows(m: Minutes, rows: pd.DataFrame, tf: str, *, bars: pd.DataFrame | None = None,
               spec: LabelSpec = SPEC) -> pd.DataFrame:
    """The 16 heads of every fs-v1 row of one (instrument, tf, phase): the long-format label frame (`COLUMNS`), tier
    columns left for `attach_tiers`."""
    if rows["phase"].nunique() > 1:
        raise ValueError("label_rows: one phase at a time")
    phase = int(rows["phase"].iloc[0]) if len(rows) else 0
    holdout_guard.refuse(rows, what="dl-v1 rows", col="decision_utc")
    bars = decision_bars(m, tf, phase, spec) if bars is None else bars
    dec = _ns(rows["decision_utc"])
    bi = np.searchsorted(bars["close_ns"].to_numpy(), dec, side="right") - 1
    atr = np.where(bi >= 0, bars["atr"].to_numpy()[np.clip(bi, 0, None)], np.nan)
    eh = entry_and_horizon(m, rows, tf, bars["close_ns"].to_numpy(), spec)
    floor = m.floor_at(dec)
    d0 = np.fmax(atr, floor)                                     # fmax: a NaN floor (warm-up) leaves the ATR
    ok = eh["ok"] & np.isfinite(d0) & (d0 > 0) & np.isfinite(atr)
    e, z = eh["e"], eh["z"]
    idx = np.flatnonzero(ok)
    n = len(rows)
    base = {k: rows[k].to_numpy() for k in ROW_KEYS}
    lab_av = np.where(ok, m.vis_cummax[z] if len(m) else 0, np.iinfo(np.int64).max)
    entry_utc = pd.Series(_to_utc(m.ts[e])).where(eh["has_entry"])   # NaT: no minute after the decision loaded
    frames = []
    for side in spec.sides:
        o, h, lo, c = _quotes(m, side)
        for tpl in spec.templates:
            fp = first_passage(o, h, lo, c, e[idx], z[idx], d0[idx], tpl)
            g = np.full(n, np.nan)
            g[idx] = fp["gross"]
            hit = np.full(n, "na", dtype=object)
            hit[idx] = HIT_NAMES[fp["hit"]]
            hit[~eh["ok"]] = "truncated"
            ex = np.full(n, np.iinfo(np.int64).min)
            ex[idx] = m.ts[fp["exit"]] + MINUTE_NS
            moved = np.zeros(n, bool)
            moved[idx] = fp["moved"]
            risk = d0 * (tpl.stop_k if tpl.stop_k is not None else 1.0)
            f = pd.DataFrame({**base, "side": np.int8(side), "template": tpl.name,
                              "entry_utc": entry_utc.to_numpy(), "entry": m.mid_open[e], "risk_usd": risk,
                              "base_dist": d0, "horizon_end_utc": _to_utc(eh["end"]),
                              "max_hold_s": (eh["end"] - dec) / 1e9,
                              "label_available_utc": _to_utc(np.where(ok, lab_av, eh["end"])),
                              "label_ok": ok, "quote_basis": "bid_only" if m.bid_only else "bid_ask",
                              "gross_r_raw": g, "gross_r": np.clip(g, -spec.clip_r, spec.clip_r),
                              "clipped": np.abs(g) > spec.clip_r, "barrier_hit": hit,
                              "label_exit_utc": pd.DatetimeIndex(pd.to_datetime(np.where(ok, ex, eh["end"]),
                                                                                unit="ns", utc=True)),
                              "stop_moved": moved})
            frames.append(f)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    out["tier"] = "ungraded"
    out["usable"] = False
    out["cost_rt_bp"] = DC.RT_BP
    out["cost_version"] = "w2-point"
    out["spec_id"] = spec.spec_id
    return _typed(out[list(COLUMNS)])


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("decision_utc", "available_utc", "window_from_utc", "entry_utc", "horizon_end_utc",
              "label_available_utc", "label_exit_utc"):
        if c in df.columns:
            df[c] = utc_index(df[c]).as_unit("ns") if len(df) else pd.DatetimeIndex([], tz="UTC").as_unit("ns")
    for c in ("phase", "group_id"):
        if c in df.columns:
            df[c] = df[c].astype(np.int64)
    for c in ("instrument", "tf", "source", "template", "quote_basis", "tier", "cost_version", "spec_id",
              "barrier_hit"):
        if c in df.columns:
            df[c] = df[c].astype(str)
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------------------------- tiers
def months_between(a: pd.Series, b: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    return (np.asarray(session_date(a).to_period("M").astype(str)),
            np.asarray(session_date(b).to_period("M").astype(str)))


# card 3.1: the column sets every count is reported for (quant C-3 / R-a); `side` reads no market data
CORE12: tuple[str, ...] = ("side", "tsmom_score", "dist_vwap48_atr", "atr_ratio_14_100", "er_30", "bb_z_20", "ret_16",
                           "ret_96", "donchian_pos_20", "adx14", "compression_8_96", "min_since_london_open")
COST8: tuple[str, ...] = ("em_atr_cost", "em_yz_4_cost", "em_yz_16_cost", "em_yz_48_cost", "spread_atr",
                          "spread_rel_median_48", "tick_count_z_48", "tick_vol_ratio_20")
D1_STRUCTURAL_NAN: tuple[str, ...] = ("d1_atr_ratio_14_100", "d1_rv_ratio")
COLUMN_SETS: dict[str, tuple[str, ...]] = {
    "core-6": CORE12[:6],
    "core-12": CORE12,
    "P30": (*(c for c in SR_FEATURES if c not in COST8 and c not in D1_STRUCTURAL_NAN), "side"),
}
CONTEXT = "d1"


@lru_cache(maxsize=1)
def column_producers() -> dict[str, tuple[str, str]]:
    """column -> (registry feature that produces it, "decision" | "d1"), from fs-v1's feature lists, found by building
    each feature alone on a synthetic mid frame (the registry records lookbacks per feature, not per column)."""
    rng = np.random.default_rng(20261126)
    n = 1_500
    ts = pd.date_range("2015-01-05", periods=n, freq="15min", tz="UTC")
    c = 1200 + np.cumsum(rng.normal(0, 0.5, n))
    hi, lo = c + np.abs(rng.normal(0, 0.4, n)), c - np.abs(rng.normal(0, 0.4, n))
    frame = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=15), "open": np.clip(
        c + rng.normal(0, 0.1, n), lo, hi), "high": hi, "low": lo, "close": c, "spread": 0.2, "tick_count": 50.0})
    out: dict[str, tuple[str, str]] = {}
    for kind, feats in (("decision", F.SPEC.decision_features), (CONTEXT, F.SPEC.context_features)):
        for f in feats:
            cols = [x for x in build_features(frame, [f]).columns if x != "ts_utc"]
            for col in cols:
                out.setdefault(col if kind == "decision" else f"{CONTEXT}_{col}", (f, kind))
    return out


def input_lookback(tf: str, columns: Sequence[str], spec: LabelSpec = SPEC) -> pd.Timedelta:
    """ADR 0104 4 (quant C-3 / R-a): the maximum over `columns` of the registry `lookback` of the feature producing
    them, in decision bars x 7/5 below 1w; with any d1_ column on a tf that has the d1 context, the d1 context
    lookback (max of `ctx_lookback_days` and the producing context feature's lookback in days x 7/5) is included."""
    prod = column_producers()
    cols = [c for c in columns if c != "side"]
    unknown = sorted(set(cols) - set(prod))
    if unknown:
        raise ValueError(f"no fs-v1 feature produces {unknown}: the input lookback cannot be computed")
    sec = tf_seconds(tf)
    wk = 1.0 if tf == "1w" else 7 / 5
    bars = max((REGISTRY[prod[c][0]].lookback for c in cols if prod[c][1] == "decision"), default=0)
    t = bars * sec * wk
    d1 = [c for c in cols if prod[c][1] == CONTEXT]
    if d1 and sec < DAY_S:
        ctx_bars = max(REGISTRY[prod[c][0]].lookback for c in d1)
        t = max(t, spec.ctx_lookback_days * DAY_S, ctx_bars * DAY_S * 7 / 5)
    return pd.Timedelta(seconds=round(t))


def attach_tiers(labels: pd.DataFrame, tiers: F.Tiers | None, *, columns: Sequence[str],
                 proxy_source: str | None = None, spec: LabelSpec = SPEC) -> pd.DataFrame:
    """ADR 0104 4: a row is usable only if every session month its served inputs read (decision - `input_lookback`
    of `columns`), its decision month and its label horizon are usable under the template rule (intraday: A /
    A-consistent; 1d/1w barrier: A / A-consistent, T0 also B). `tier` = the worst tier over those months. The tiers
    must grade the rows' own source (`<source>:<instrument>` = tiers.key, quant C3) unless `proxy_source` names the
    graded one; any other source is `ungraded` and never usable."""
    if labels.empty:
        return labels.assign(tier=pd.Series(dtype=str), usable=pd.Series(dtype=bool))
    out = labels.copy()
    tier = np.full(len(out), "ungraded", dtype=object)
    if tiers is not None:
        inst = str(out["instrument"].iloc[0])
        src = out["source"].astype(str)
        key = np.where(src.str.contains(":"), src, src + ":" + out["instrument"].astype(str))
        graded = (key == tiers.key(inst)) | (proxy_source is not None and proxy_source == tiers.key(inst))
        if graded.any():
            g = out[graded]
            lb = {t: input_lookback(t, columns, spec) for t in g["tf"].unique()}
            start = pd.DatetimeIndex(g["decision_utc"]) - pd.to_timedelta(g["tf"].map(lb).to_numpy())
            a, b = months_between(pd.Series(start), g["horizon_end_utc"])
            lo, hi = min(a.min(), b.min()), max(a.max(), b.max())
            allm = pd.period_range(lo, hi, freq="M").astype(str)
            ranks = np.array([F.TIER_RANK.get(x, 6) for x in tiers.tier_of(inst, list(allm))])
            pos = {mm: i for i, mm in enumerate(allm)}
            names = {v: k for k, v in F.TIER_RANK.items()}
            worst_of = {(x, y): names[int(ranks[pos[x]:pos[y] + 1].max())] for x, y in set(zip(a, b, strict=True))}
            tier[graded] = [worst_of[(x, y)] for x, y in zip(a, b, strict=True)]
    out["tier"] = tier.astype(str)
    is_daily = {t: tf_seconds(t) >= DAY_S for t in out["tf"].unique()}
    daily = out["tf"].map(is_daily).to_numpy(dtype=bool)
    barrier = ~out["template"].isin(CLOSE_TO_CLOSE).to_numpy()
    allowed_a = out["tier"].isin(F.INTRADAY_TIERS).to_numpy()
    allowed_b = out["tier"].isin(F.DAILY_TIERS).to_numpy()
    out["usable"] = out["label_ok"].to_numpy(bool) & np.where(daily & ~barrier, allowed_b, allowed_a)
    return out


# --------------------------------------------------------------------------------------------- uniqueness, n_eff
def uniqueness(entry_idx: np.ndarray, end_idx: np.ndarray, n_grid: int,
               conc: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(mean of 1/c over each event's minutes [entry, end] inclusive, the concurrency c on the grid). `conc` given:
    uniqueness against that concurrency (phase rows, cross-tf)."""
    if conc is None:
        d = np.zeros(n_grid + 1, np.int64)
        np.add.at(d, entry_idx, 1)
        np.add.at(d, end_idx + 1, -1)
        conc = np.cumsum(d)[:n_grid]
    inv = np.where(conc > 0, 1.0 / np.maximum(conc, 1), 0.0)
    cs = np.concatenate(([0.0], np.cumsum(inv)))
    u = (cs[end_idx + 1] - cs[entry_idx]) / (end_idx - entry_idx + 1)
    return u, conc


def event_index(grid_ns: np.ndarray, entry_utc: pd.Series, end_utc: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Grid positions of an event's first minute (its entry) and its last (the last minute closing by the end)."""
    e = np.searchsorted(grid_ns, _ns(entry_utc), side="left")
    z = np.searchsorted(grid_ns + MINUTE_NS, _ns(end_utc), side="right") - 1
    return e, np.maximum(z, e)


def weights(events: Mapping[str, pd.DataFrame], grid_ns: np.ndarray, phases_built: Mapping[str, int]
            ) -> dict[str, pd.DataFrame]:
    """Per tf: one row per bar event (instrument, tf, phase, decision_utc) with u_tf, u_inst and w = u_tf / P.
    `events[tf]`: one row per (phase, decision_utc) with entry_utc, horizon_end_utc, label_ok (any head). Only phase-0
    label_ok events make concurrency; phase rows are weighed against it."""
    n = len(grid_ns)
    conc_tf: dict[str, np.ndarray] = {}
    idx: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    total = np.zeros(n, np.int64)
    for tf, ev in events.items():
        e, z = event_index(grid_ns, ev["entry_utc"], ev["horizon_end_utc"])
        idx[tf] = (e, z)
        base = (ev["phase"].to_numpy() == 0) & ev["label_ok"].to_numpy(bool)
        _, c = uniqueness(e[base], z[base], n)
        conc_tf[tf] = c
        total += c
    out = {}
    for tf, ev in events.items():
        e, z = idx[tf]
        u_tf, _ = uniqueness(e, z, n, conc_tf[tf])
        u_in, _ = uniqueness(e, z, n, total)
        ok = ev["label_ok"].to_numpy(bool)
        P = max(1, int(phases_built.get(tf, 1)))
        out[tf] = ev[["instrument", "tf", "phase", "group_id", "decision_utc"]].assign(
            u_tf=np.where(ok, u_tf, 0.0), u_inst=np.where(ok, u_in, 0.0),
            w=np.where(ok, u_tf, 0.0) / P).reset_index(drop=True)
    return out


def n_eff(events: pd.DataFrame, tf: str, spec: LabelSpec = SPEC) -> dict[str, float]:
    """Card 2.2 on measured uniqueness, phase 0 only: H < 1 d: sum over session days of S_d / (1 + (max(S_d, 1) - 1)
    rho_day), S_d = sum of u_tf decided that day; H >= 1 d: sum of u_tf. Also the rows, the days and the Kish n_eff of
    the weights. `events`: rows of `weights` (with any usability mask applied by the caller)."""
    ev = events[events["phase"].to_numpy() == 0]
    u = ev["u_tf"].to_numpy(float)
    rows = int((u > 0).sum())
    if not rows:
        return {"rows": 0, "days": 0, "sum_u": 0.0, "n_eff": 0.0, "kish": 0.0}
    days = np.asarray(session_date(ev["decision_utc"]).astype(str))
    s = pd.Series(u).groupby(days).sum()
    if spec.h_by_tf[tf] < DAY_S:
        ne = float((s / (1 + (np.maximum(s, 1.0) - 1) * spec.rho_day)).sum())
    else:
        ne = float(u.sum())
    return {"rows": rows, "days": int((s > 0).sum()), "sum_u": float(u.sum()), "n_eff": ne,
            "kish": float(u.sum() ** 2 / (u ** 2).sum())}


# --------------------------------------------------------------------------------------------- folds
def purge(train: pd.DataFrame, test: pd.DataFrame, tf: str, spec: LabelSpec = SPEC) -> pd.DataFrame:
    """C6 through `feature_store.purge` (one rule repo-wide): a training row goes when its [window_from_utc,
    horizon_end_utc] overlaps a test row's [decision_utc, horizon_end_utc + embargo], embargo = `embargo(tf)` (max(1 d,
    H, feature lookback), at least the one label horizon C6 asks). Any phase: a phase-5 copy of a test row goes too."""
    keep = F.purge(train, test, label_end="horizon_end_utc", embargo=embargo(tf, spec))
    return train.loc[keep].reset_index(drop=True)


def iso_reserve(ts_lo: pd.Series, ts_hi: pd.Series) -> np.ndarray:
    """N12: True where [lo, hi] touches an odd ISO week on or after 2026-10-01 (reserve weeks never train)."""
    lo, hi = utc_index(pd.Series(ts_lo)), utc_index(pd.Series(ts_hi))
    late = np.asarray(hi >= RESERVE_FROM)
    out = np.zeros(len(lo), bool)
    for i in np.flatnonzero(late):
        days = pd.date_range(max(pd.Timestamp(lo[int(i)]), RESERVE_FROM).normalize(), pd.Timestamp(hi[int(i)]).normalize(), freq="D")
        out[i] = bool((days.isocalendar().week.to_numpy() % 2 == 1).any())
    return out


# --------------------------------------------------------------------------------------------- cost / net
def net_r(labels: pd.DataFrame, pri: DC.CostPriors, *, rt_bp: np.ndarray | float | None = None,
          spec: LabelSpec = SPEC) -> np.ndarray:
    """clip(gross_r_raw - cost(x), +-clip_r). cost(x) = decision_cost.cost_x on decision-time columns only (arm =
    instrument:tf:template), with the round trip `rt_bp` per row (ADR 0102 3: max(W2, ecr ub95 bps) per broker x
    session x tf; default the row's `cost_rt_bp`, W2). Identical for any two rows with the same decision inputs,
    whatever their exit (test)."""
    rows = labels.assign(arm=labels["instrument"].astype(str) + ":" + labels["tf"].astype(str) + ":"
                         + labels["template"].astype(str))
    base = DC.cost_x(rows, pri)
    rt = labels["cost_rt_bp"].to_numpy(float) if rt_bp is None else np.broadcast_to(np.asarray(rt_bp, float),
                                                                                      (len(labels),))
    k = DC.row_k(rows)
    cost = base + k * (rt - DC.RT_BP)
    return np.clip(labels["gross_r_raw"].to_numpy(float) - cost, -spec.clip_r, spec.clip_r)


# --------------------------------------------------------------------------------------------- store
def _part_dir(cache: Path, spec: LabelSpec, instrument: str, tf: str, year: int, phase: int) -> Path:
    return Path(cache) / spec.spec_id / instrument / tf / f"p{phase}" / str(year)


def label_key(parts: Mapping[str, Any], *, instrument: str, tf: str, year: int, phase: int,
              spec: LabelSpec = SPEC) -> str:
    """The partition key: label spec, code, instrument, tf, year, phase and `parts` (the fs-v1 partition key, the
    minutes' sha256 and `minutes_to_utc`, so a revised or longer minute history never shares a key)."""
    if "minutes_to_utc" not in parts or "fs_key" not in parts or "minutes_sha256" not in parts:
        raise ValueError("label parts need fs_key, minutes_sha256 and minutes_to_utc")
    return hashlib.sha256(json.dumps({**parts, "spec_hash": spec_hash(spec), "code_hash": code_hash(),
                                      "instrument": instrument, "tf": tf, "year": year, "phase": phase},
                                     sort_keys=True, default=str).encode()).hexdigest()


VALUE_KEYS = ("decision_utc", "side", "template")       # alignment columns repeated in the values file


def write_partition(labels: pd.DataFrame, *, cache: Path, instrument: str, tf: str, year: int, phase: int,
                    parts: Mapping[str, Any], spec: LabelSpec = SPEC) -> str:
    """Atomic: `<key>.skeleton.parquet` (the outcome-blind `SKELETON` columns), `<key>.values.parquet` (the label
    values, `LABEL_VALUES`, row-aligned; only `load_labels` opens it, code review LOW-2) and `<key>.json`."""
    key = label_key(parts, instrument=instrument, tf=tf, year=year, phase=phase, spec=spec)
    d = _part_dir(cache, spec, instrument, tf, year, phase)
    d.mkdir(parents=True, exist_ok=True)
    stem = key[:24]
    for suffix, cols in ((".skeleton.parquet", list(SKELETON)), (".values.parquet", [*VALUE_KEYS, *LABEL_VALUES])):
        p = d / f"{stem}{suffix}"
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        labels[cols].to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, p)
    man = {**parts, "key": key, "spec_id": spec.spec_id, "spec_hash": spec_hash(spec), "code_hash": code_hash(),
           "instrument": instrument, "tf": tf, "year": year, "phase": phase, "rows": len(labels),
           "bars": int(labels["decision_utc"].nunique()) if len(labels) else 0,
           "sealed": True, "built_utc": str(pd.Timestamp.now(tz="UTC"))}
    tmpm = d / f"{stem}.json.tmp{os.getpid()}"
    tmpm.write_text(json.dumps(man, indent=1, sort_keys=True, default=str))
    os.replace(tmpm, d / f"{stem}.json")
    return key


def partition_exists(cache: Path, instrument: str, tf: str, year: int, phase: int, parts: Mapping[str, Any],
                     spec: LabelSpec = SPEC) -> bool:
    stem = label_key(parts, instrument=instrument, tf=tf, year=year, phase=phase, spec=spec)[:24]
    d = _part_dir(cache, spec, instrument, tf, year, phase)
    return all((d / f"{stem}{x}").exists() for x in (".skeleton.parquet", ".values.parquet", ".json"))


def readable(tiers: F.Tiers | None) -> bool:
    """Label values may be read only under a tiers file ADR 0085 A4 records (card ruling 5, test 21)."""
    return tiers is not None and tiers.sha256 in RECORDED_TIERS_SHA256


def _stems(cache: Path, spec: LabelSpec, instrument: str, tf: str, years: Iterable[int], phases: Sequence[int],
           keys: Mapping[tuple[int, int], str] | None) -> list[Path]:
    """The partition stem per (year, phase): the one whose key is `keys[(year, phase)]` (quant C-2), else the only
    candidate of this spec and code; several candidates and no key to choose by raise (code review LOW-4)."""
    want_s, want_c = spec_hash(spec), code_hash()
    out = []
    for y in sorted(years):
        for ph in phases:
            d = _part_dir(cache, spec, instrument, tf, y, int(ph))
            mans = [(mp, json.loads(mp.read_text())) for mp in sorted(d.glob("*.json"))]
            cands = [mp for mp, m in mans if m.get("spec_hash") == want_s and m.get("code_hash") == want_c]
            if keys is not None:
                want = keys.get((y, int(ph)))
                if want is None:
                    raise KeyError(f"no expected key for {instrument} {tf} p{ph} {y}")
                cands = [mp for mp, m in mans if m.get("key") == want and mp in cands]
            if not cands:
                raise FileNotFoundError(f"no dl-v1 partition {instrument} {tf} p{ph} {y} under this spec, code"
                                        + (" and data key" if keys is not None else ""))
            if len(cands) > 1:
                raise ValueError(f"dl-v1 {instrument} {tf} p{ph} {y}: {len(cands)} partitions and no data key to choose "
                                 "by: pass keys (label_key of the current fs-v1 key and minutes)")
            out.append(cands[0].with_suffix(""))
    return out


def skeleton(cache: Path, *, instrument: str, tf: str, years: Iterable[int], phases: Sequence[int] = (0,),
             keys: Mapping[tuple[int, int], str] | None = None, spec: LabelSpec = SPEC) -> pd.DataFrame:
    """The outcome-blind columns only (`SKELETON`: never a label value; the values live in another file), for counts,
    weights and n_eff. Allowed on a sealed store: a count is not a label read."""
    stems = _stems(cache, spec, instrument, tf, years, phases, keys)
    parts = [pd.read_parquet(f"{st}.skeleton.parquet") for st in stems]
    return _typed(pd.concat(parts, ignore_index=True)) if parts else pd.DataFrame(columns=list(SKELETON))


# confirm / test_old per unit (quant C-1): a label read touching them needs a booked read id
INDEX_ENERGY = frozenset({"US500", "USTEC", "UK100", "DE40", "US30", "XTIUSD", "USINDEX"})


def judged_spans(instrument: str) -> tuple[tuple[pd.Timestamp, pd.Timestamp], ...]:
    if instrument in INDEX_ENERGY:
        return ((pd.Timestamp("2022-01-01", tz="UTC"), WN.HOLDOUT_FROM),)
    return (WN.SPANS[WN.Window.CONFIRM], WN.SPANS[WN.Window.TEST_OLD])


def learn_end(instrument: str) -> pd.Timestamp:
    return pd.Timestamp("2022-01-01", tz="UTC") if instrument in INDEX_ENERGY else WN.SPANS[WN.Window.LEARN][1]


def booked(ledger: Path, read_id: str, frozen_spec_hash: str) -> dict[str, Any]:
    """The read-once ledger row (`windows.READS_LEDGER`, written before the read by `windows.record_read_once`) whose
    key is `read_id`, for a confirm or test_old window, with `ref.spec_hash` = the frozen spec hash. Else raises."""
    p = Path(ledger)
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    hit = [r for r in rows if r.get("key") == read_id and r.get("window") in (WN.Window.CONFIRM.value,
                                                                               WN.Window.TEST_OLD.value)]
    if not hit:
        raise PermissionError(f"read {read_id!r} is not booked in {p} for confirm or test_old")
    if not any((r.get("ref") or {}).get("spec_hash") == frozen_spec_hash for r in hit):
        raise PermissionError(f"read {read_id!r} is booked under another spec hash than {frozen_spec_hash[:12]}")
    return hit[-1]


def _touches(lo: np.ndarray, hi: np.ndarray, spans: Iterable[tuple[pd.Timestamp, pd.Timestamp]]) -> np.ndarray:
    hit = np.zeros(len(lo), bool)
    for a, b in spans:
        hit |= (lo < b.value) & (hi > a.value)
    return hit


def load_labels(cache: Path, *, instrument: str, tf: str, years: Iterable[int], tiers: F.Tiers | None,
                columns: Sequence[str] | None, keys: Mapping[tuple[int, int], str],
                window: tuple[pd.Timestamp, pd.Timestamp], purpose: str = "judge", phases: Sequence[int] = (0,),
                templates: Sequence[str] | None = None, proxy_source: str | None = None,
                booked_read: str | None = None, ledger: Path | None = None, frozen_spec_hash: str | None = None,
                spec: LabelSpec = SPEC) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Label rows for a booked read. Refuses: a read without tiers or without `columns` (the served column set, ADR
    0104 4); a sealed store (tiers sha256 not recorded in ADR 0085 A4); phase != 0 for a judged read; the holdout (any
    label span); a `window` touching the unit's confirm or test_old span without `booked_read` booked in `ledger`
    under `frozen_spec_hash` (quant C-1). Reads only the partitions of `keys` (quant C-2). Drops (counted): rows
    outside `window`, rows touching confirm/test_old on an unbooked read (learn reads end at the learn end),
    tier-unusable rows under `columns`, reserve-week rows of a training read (N12)."""
    if purpose not in ("judge", "train"):
        raise ValueError(f"purpose {purpose!r}: judge or train")
    if purpose == "judge" and any(int(p) != 0 for p in phases):
        raise ValueError("a judged read loads phase 0 only: offset candles are training augmentation")
    if tiers is None:
        raise ValueError("dl-v1 labels load with the r11 tiers (ADR 0085 A4, quant C3): pass tiers")
    if not columns:
        raise ValueError("dl-v1 judge and train reads name their served column set (ADR 0104 4): pass columns")
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    spans = judged_spans(instrument)
    wants_judged = bool(_touches(np.array([lo.value]), np.array([hi.value]), spans)[0])
    book = None
    if wants_judged:
        if booked_read is None or ledger is None or frozen_spec_hash is None:
            raise PermissionError(f"dl-v1 {instrument}: window {lo.date()}..{hi.date()} touches confirm/test_old; "
                                  "a booked read id, its ledger and the frozen spec hash are required (quant C-1)")
        book = booked(ledger, booked_read, frozen_spec_hash)
    stems = _stems(cache, spec, instrument, tf, years, phases, keys)
    if not readable(tiers):
        raise SealedStore(f"dl-v1 {instrument} {tf}: tiers sha256 {tiers.sha256[:12]} is not recorded in ADR 0085 A4 "
                          "(card ruling 5): label values are sealed; counts read `skeleton`")
    frames = []
    for st in stems:
        sk = pd.read_parquet(f"{st}.skeleton.parquet")
        va = pd.read_parquet(f"{st}.values.parquet")
        if len(sk) != len(va) or not all((sk[k].to_numpy() == va[k].to_numpy()).all() for k in VALUE_KEYS):
            raise ValueError(f"{st}: skeleton and values files are not row-aligned")
        frames.append(pd.concat([sk, va[list(LABEL_VALUES)]], axis=1))
    lab = _typed(pd.concat(frames, ignore_index=True))
    holdout_guard.refuse_labels(lab, what=f"dl-v1 {instrument} {tf}")
    if purpose == "judge" and (lab["phase"] != 0).any():
        raise ValueError("a judged read holds a phase != 0 row")
    lab = attach_tiers(lab, tiers, columns=columns, proxy_source=proxy_source, spec=spec)
    meta: dict[str, Any] = {"spec_id": spec.spec_id, "spec_hash": spec_hash(spec), "code_hash": code_hash(),
                            "tiers_sha256": tiers.sha256, "r11_id": tiers.r11_id, "tier_proxy": proxy_source,
                            "columns": list(columns), "keys": dict((f"{y}:{p}", k) for (y, p), k in keys.items()),
                            "booked_read": booked_read if book is not None else None, "rows_read": len(lab),
                            "purpose": purpose, "window": [str(lo), str(hi)]}
    d, e = _ns(lab["decision_utc"]), _ns(lab["horizon_end_utc"])
    inside = (d >= lo.value) & (e <= hi.value)
    meta["dropped_window"] = int((~inside).sum())
    if book is None:
        judged = _touches(d, e, spans)
        meta["dropped_judged"] = int((inside & judged).sum())
        inside &= ~judged
    usable = lab["usable"].to_numpy(bool)
    meta["dropped_tier"] = int((inside & ~usable).sum())
    keep = inside & usable
    if purpose == "train":
        res = iso_reserve(lab["decision_utc"], lab["horizon_end_utc"])
        meta["dropped_reserve"] = int((keep & res).sum())
        keep &= ~res
    if templates is not None:
        keep &= lab["template"].isin(list(templates)).to_numpy()
    out = lab.loc[keep].reset_index(drop=True)
    meta["rows"] = len(out)
    return out, meta


__all__ = ["CACHE", "COLUMN_SETS", "COLUMNS", "booked", "column_producers", "input_lookback", "judged_spans",
           "label_key", "learn_end", "RECORDED_TIERS_SHA256", "H_BY_TF", "LABEL_COST_COLS", "LABEL_VALUES", "SKELETON", "SPEC",
           "TEMPLATES", "LabelSpec", "Minutes", "SealedStore", "Template", "attach_tiers", "code_hash",
           "decision_bars", "embargo", "entry_and_horizon", "event_index", "first_passage", "iso_reserve",
           "label_rows", "load_labels", "readable", "n_eff", "net_r", "partition_exists", "purge", "skeleton", "spec_hash",
           "uniqueness", "weights", "write_partition"]
