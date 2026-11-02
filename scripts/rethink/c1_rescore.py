"""Amendment C1 mechanical re-score (docs/research/rethink/audit.md, ADR 0069): the sprint-200 lanes' frozen rules,
re-run with their own frozen signal and walk code, re-priced with the C1 cost (`cost_of_record.C1Costs`) and read
against the C1 controls (`research.c1_gates`). No rule, threshold or parameter changes: re-scoring past EXPLORE
results adds no trial (amendment C1, declared).

    python scripts/rethink/c1_rescore.py explore [--lanes trend,meanrev,...] [--workers 4]
    python scripts/rethink/c1_rescore.py composites          (writes docs/research/rethink/c1-composites.yaml)
    python scripts/rethink/c1_rescore.py confirm             (once: the committed composites + new single passers)

Each lane adapter replays the lane's own real-trade loop (one position at a time) with the lane's walk, and walks the
opposite side from the same minute with the same stop and exit (the random-side twin, taken in expectation). The
stats and time lanes' scripts are read from their research branches (`git show`), never copied into this tree.
Parity: the "old" column is each trade priced the lane's way (cost of record upper bound, ruled overshoot, shipped
swap), and must match the lane's published explore table.

Nothing at or after 2025-10-01 is read (each lane's own loader asserts it). Outputs are aggregates under ~/tmp
(never committed): no market data and no trade row leaves the process.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import types
from collections.abc import Callable
from multiprocessing import get_context
from pathlib import Path
from typing import Any

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.labels import SwapSpec  # noqa: E402
from goldbot.research import arms as A  # noqa: E402
from goldbot.research import c1_gates as G  # noqa: E402
from goldbot.research.cost_of_record import C1Costs  # noqa: E402
from goldbot.research.metrics import DsrInputs  # noqa: E402

RAW = Path(os.environ.get("GOLDBOT_RAW", "/Users/aamirkhan/myproject/goldbot/raw"))
OUT = Path.home() / "tmp" / "c1_rescore"
COMPOSITES = ROOT / "docs" / "research" / "rethink" / "c1-composites.yaml"
LEGACY_VERSION = "cor-2026-10-31.2"
BROKER = "icm"
MAX_WORKERS = 4
LANES = ("trend", "meanrev", "macro", "patterns", "stats", "time")
BRANCH_SCRIPTS = {"stats": ("origin/research/sprint200-stats", "scripts/sprint200/stats_screen.py"),
                  "time": ("origin/research/sprint200-time", "scripts/sprint200/time_screen.py")}
# rules whose confirm window was already read: TEST-OLD only (amendment C1, "Windows already read stay read")
CONFIRM_READ = frozenset({"S200-TREND-03"})
WINDOW_BOUNDS = {"explore": (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")),
                 "confirm": (pd.Timestamp("2018-01-01", tz="UTC"), A.HOLDOUT_FROM)}
STATS_SLOW = {"01": "1d", "02": "1d", "04": "1d", "05": "1d", "09": "1d", "15": "1d", "18": "1d", "19": "1d",
              "20": "1d", "21": "1d", "22": "1d", "28": "1d", "29": "1d", "30": "1d", "32": "1d", "03": "4h",
              "06": "4h", "07": "4h", "16": "4h", "23": "4h", "26": "4h", "27": "4h", "31": "4h", "33": "4h"}
COLS = ["t_fill", "t_exit", "side", "risk", "gross", "stop_w", "sim_os", "px", "sess", "nights",
        "opp_t_exit", "opp_gross", "opp_stop_w", "opp_sim_os", "opp_nights"]


# ---------------------------------------------------------------- lane modules
def _load_module(lane: str) -> types.ModuleType:
    if lane in BRANCH_SCRIPTS:
        ref, rel = BRANCH_SCRIPTS[lane]
        src = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{rel}"], check=True, capture_output=True,
                             text=True).stdout
        path = OUT / "lanes" / Path(rel).name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src)
    else:
        path = ROOT / "scripts" / "sprint200" / f"{lane}_screen.py"
    spec = importlib.util.spec_from_file_location(f"c1_{lane}_screen", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    setattr(mod, "ROOT", ROOT)          # a branch script read from ~/tmp still finds this checkout's config
    return mod


def _nights(t0: np.ndarray, t1: np.ndarray, swap: SwapSpec) -> np.ndarray:
    from goldbot.labels import rollover_nights
    if len(t0) == 0:
        return np.zeros(0)
    return rollover_nights(pd.DatetimeIndex(pd.to_datetime(t0, utc=True)),
                           pd.DatetimeIndex(pd.to_datetime(t1, utc=True)), swap.server_tz,
                           swap.triple_weekday).astype(float)


def _frame(rows: list[dict[str, Any]], swap: SwapSpec) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLS)
    if "nights" not in df:
        df["nights"] = _nights(df.t_fill.to_numpy(np.int64), df.t_exit.to_numpy(np.int64), swap)
    if "opp_nights" not in df:
        df["opp_nights"] = _nights(df.t_fill.to_numpy(np.int64), df.opp_t_exit.to_numpy(np.int64), swap)
    return df[COLS]


def _gap(gross: float, risk: float, stop: bool) -> float:
    """How far past the initial stop the simulated fill already went (a gap to the minute open), USD per oz."""
    return max(0.0, -gross - risk) if stop else 0.0


# ---------------------------------------------------------------- trend (+ mlexec V0, identical by its parity)
def trend_trades(mod: Any, window: str, swap: SwapSpec) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    ctx = mod.build_ctx(RAW, window)
    out, tfs = {}, {}
    big = np.iinfo(np.int64).max
    for sid, fn in mod.STRATS.items():
        name = mod.NAMES[int(sid[-2:]) - 1]
        tfs[sid] = "1d" if ("1D" in name or "BIGDAY" in name) else "4h" if ("H4" in name or "4H" in name) else "1h"
        spec = fn(ctx)
        sig = spec.sig.sort_values("dec", kind="stable").reset_index(drop=True)
        sig = sig[(sig.dec >= ctx.dec_from) & (sig.dec < ctx.end)]
        flags = None
        if spec.exit_flags is not None:
            fd, f_long, f_short = spec.exit_flags
            flags = {1: fd[np.asarray(f_long, dtype=bool)], -1: fd[np.asarray(f_short, dtype=bool)]}
        rows: list[dict[str, Any]] = []
        busy_until = -1
        for r in sig.itertuples(index=False):
            dec = int(r.dec)
            if dec < busy_until:
                continue
            k0 = int(np.searchsorted(ctx.t, dec, side="left"))
            if k0 >= len(ctx.t) - 1:
                continue
            dl = mod.deadline(ctx, str(r.dl), k0)
            if dl < big and ctx.t[k0] > dl - 15 * mod.MIN:
                continue
            side = int(r.side)
            e = float(ctx.o[k0])
            risk = float(r.risk) if np.isfinite(r.risk) else side * (e - float(r.stop_px))
            if not (np.isfinite(risk) and risk > 0):
                continue
            t_end = min(int(r.cap), dl, ctx.end)
            fl = flags[side] if flags is not None else None
            g, kind, kx, tx = mod.walk(ctx, k0, side, risk, float(r.tgt), float(r.trail), t_end, fl)
            og, okind, okx, otx = mod.walk(ctx, k0, -side, risk, float(r.tgt), float(r.trail), t_end, fl)
            rows.append({"t_fill": int(ctx.t[k0]), "t_exit": tx, "side": side, "risk": risk, "gross": g,
                         "stop_w": float(kind in ("stop", "trail")),
                         "sim_os": _trend_gap(ctx, k0, kx, side, risk, float(r.trail), g, kind), "px": e,
                         "sess": "", "opp_t_exit": otx, "opp_gross": og,
                         "opp_stop_w": float(okind in ("stop", "trail")),
                         "opp_sim_os": _trend_gap(ctx, k0, okx, -side, risk, float(r.trail), og, okind)})
            busy_until = tx
        df = _frame(rows, swap)
        if len(df):                    # the lane priced every trade at its entry's session label
            from goldbot.data.calendar import DEFAULT_SESSIONS
            df["sess"] = np.asarray(DEFAULT_SESSIONS.session_label(
                pd.DatetimeIndex(pd.to_datetime(df.t_fill.to_numpy(np.int64), utc=True))), dtype=object)
        out[sid] = df
    return out, tfs


def _trend_gap(ctx: Any, k0: int, kx: int, side: int, risk: float, trail: float, gross: float, kind: str) -> float:
    if kind not in ("stop", "trail"):
        return 0.0
    e_ = float(ctx.o[k0]) * side
    level = e_ - risk
    if kind == "trail" and np.isfinite(trail) and kx > k0:
        hh = ctx.h[k0:kx] if side > 0 else -ctx.lo[k0:kx]
        level = max(level, float(np.max(hh)) - trail)
    return max(0.0, (level - e_) - gross)


# ---------------------------------------------------------------- meanrev
def meanrev_trades(mod: Any, window: str, swap: SwapSpec) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    d = mod.load(RAW, window)
    B = {"15m": mod.resample(d, 15), "1h": mod.resample(d, 60), "4h": mod.resample(d, 240),
         "1d": mod.resample(d, 1440)}
    out, tfs = {}, {}
    for sid, gen in mod.SPECS.items():
        tfs[sid] = "1h"
        trades, meta = mod.run_trades(d, gen(d, B))
        rows = []
        for t, (s, sd, td) in zip(trades, meta, strict=True):
            dl = mod.deadline(d, t.entry, s)
            w = mod.walk(d, t.entry, -t.side, sd, td, dl)
            assert w is not None
            omove, _, ostop, ox = w
            rows.append({"t_fill": int(d.ts[t.entry]), "t_exit": int(d.ts[t.exit]) + mod.NS_MIN, "side": t.side,
                         "risk": sd, "gross": t.move, "stop_w": float(t.stop_exit),
                         "sim_os": _gap(t.move, sd, t.stop_exit), "px": float(d.o[t.entry]), "sess": t.label,
                         "opp_t_exit": int(d.ts[ox]) + mod.NS_MIN, "opp_gross": omove, "opp_stop_w": float(ostop),
                         "opp_sim_os": _gap(omove, sd, ostop)})
        out[sid] = _frame(rows, swap)
    return out, tfs


# ---------------------------------------------------------------- macro
def macro_trades(mod: Any, window: str, swap: SwapSpec) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    c = mod.Ctx(window)
    st = mod.strategies(c)
    lo, hi = mod.WINDOWS[window]
    b = c.b
    out, tfs = {}, {}
    for k in sorted(st):
        sid = f"S200-MACRO-{k}"
        tfs[sid] = "1h"
        cands = sorted((x for x in st[k]() if lo.value <= x["dec"] < hi.value), key=lambda x: x["dec"])
        rows, last_exit = [], -1
        for x in cands:
            if window == "confirm" and x["exit"] >= mod.HOLDOUT.value:
                continue
            e = b.first_at_or_after(int(x["dec"]))
            if e >= len(b.ts) or int(b.ts[e]) >= int(x["exit"]) or int(b.ts[e]) < last_exit:
                continue
            side, stop = int(x["side"]), float(x["stop"])
            move, ex, s_ = mod.simulate(b, e, side, stop, float(x["tgt"]), int(x["exit"]))
            omove, oex, os_ = mod.simulate(b, e, -side, stop, float(x["tgt"]), int(x["exit"]))
            last_exit = ex
            rows.append({"t_fill": int(b.ts[e]), "t_exit": ex, "side": side, "risk": stop, "gross": move,
                         "stop_w": float(s_), "sim_os": _gap(move, stop, bool(s_)), "px": float(b.o[e]),
                         "sess": b.sess[e], "opp_t_exit": oex, "opp_gross": omove, "opp_stop_w": float(os_),
                         "opp_sim_os": _gap(omove, stop, bool(os_))})
        out[sid] = _frame(rows, swap)
    return out, tfs


# ---------------------------------------------------------------- patterns
def patterns_trades(mod: Any, window: str, swap: SwapSpec) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    from goldbot.data.calendar import DEFAULT_SESSIONS
    ctx = mod.init_ctx(RAW, window)
    mod.CTX = ctx
    m_long, m_short = mod.oriented(ctx.m1, 1), mod.oriented(ctx.m1, -1)
    out, tfs = {}, {}
    for s in mod.STRATS:
        tfs[s.id] = s.tf
        Fl = ctx.frames[s.tf]
        cands = []
        for side, F in ((1, Fl), (-1, Fl.neg())):
            sg = mod.signals(F, s)
            sg["side"] = side
            cands.append(sg)
        cd = pd.concat(cands, ignore_index=True)
        cd = cd[(Fl.close_ns[cd["i"].to_numpy()] >= ctx.lo) & (Fl.close_ns[cd["i"].to_numpy()] < ctx.hi)]
        both = cd["i"].duplicated(keep=False)
        cd = cd[~both].sort_values("i").reset_index(drop=True)
        rows: list[dict[str, Any]] = []
        busy_until = -1
        for i, d, tgt, atr, side in cd[["i", "d", "tgt", "atr", "side"]].itertuples(index=False):
            i, side = int(i), int(side)
            if Fl.close_ns[i] < busy_until:
                continue
            r = mod.trade_at(ctx, Fl, s, side, i, float(d), float(atr), float(tgt), m_long if side > 0 else m_short)
            if r is None:
                continue
            o = mod.trade_at(ctx, Fl, s, -side, i, float(d), float(atr), float(tgt), m_short if side > 0 else m_long)
            assert o is not None
            busy_until = r["exit_ns"]
            k0 = int(np.searchsorted(ctx.m1.ts, r["entry_ns"], side="left"))
            real, opp = _legs(r, float(d), swap), _legs(o, float(d), swap)
            rows.append({"t_fill": r["entry_ns"], "t_exit": r["exit_ns"], "side": side, "risk": float(d),
                         "gross": real[0], "stop_w": real[1], "sim_os": real[2], "nights": real[3],
                         "px": float(ctx.m1.o[k0]), "sess": "", "opp_t_exit": o["exit_ns"], "opp_gross": opp[0],
                         "opp_stop_w": opp[1], "opp_sim_os": opp[2], "opp_nights": opp[3]})
        df = _frame(rows, swap)
        if len(df):
            ent = pd.DatetimeIndex(pd.to_datetime(df.t_fill.to_numpy(np.int64), utc=True))
            df["sess"] = np.where(DEFAULT_SESSIONS.is_open(ent), DEFAULT_SESSIONS.session_label(ent), "").astype(object)
        out[s.id] = df
    return out, tfs


def _legs(r: dict[str, Any], d: float, swap: SwapSpec) -> tuple[float, float, float, float]:
    """(gross, stop weight, simulated gap, nights) of a possibly two-leg trade, each leg at its weight."""
    g = sw = gap = nights = 0.0
    for w, leg in r["legs"]:
        n = _nights(np.array([r["entry_ns"]]), np.array([leg.exit_ts]), swap)[0]
        g += w * leg.move
        sw += w * float(leg.stop)
        gap += w * _gap(leg.move, d, bool(leg.stop))
        nights += w * n
    return g, sw, gap, nights


# ---------------------------------------------------------------- stats
def stats_trades(mod: Any, window: str, swap: SwapSpec) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    m = mod.load_for_window(RAW, window)
    sig = mod.all_signals(m, window)
    M = mod.Minutes(m)
    end_ns = int(mod.window_bounds(window)[1].value)
    out, tfs = {}, {}
    for rid in sorted(sig):
        sid = f"S200-STATS-{rid}"
        tfs[sid] = STATS_SLOW.get(rid, "1h")
        dec = sig[rid]
        rows, busy_until = [], -1
        for t, side, risk, tgt, hold in zip(dec["t"].to_numpy(np.int64), dec["side"].to_numpy(int),
                                            dec["risk"].to_numpy(float), dec["tgt"].to_numpy(float),
                                            dec["hold"].to_numpy(np.int64), strict=True):
            k0 = int(np.searchsorted(M.t, t, side="left"))
            if k0 >= len(M.t) or M.t[k0] - t > mod.MAX_ENTRY_DELAY or M.t[k0] >= end_ns or M.t[k0] <= busy_until:
                continue
            mv, st, kx = mod.simulate(M, k0, int(side), float(risk), float(tgt), int(hold), end_ns)
            omv, ost, okx = mod.simulate(M, k0, -int(side), float(risk), float(tgt), int(hold), end_ns)
            busy_until = int(M.t[kx])
            rows.append({"t_fill": int(M.t[k0]), "t_exit": int(M.t[kx]), "side": int(side), "risk": float(risk),
                         "gross": mv, "stop_w": float(st), "sim_os": _gap(mv, risk, st), "px": float(M.o[k0]),
                         "sess": mod.SESS_NAMES[int(M.sess[k0])], "opp_t_exit": int(M.t[okx]), "opp_gross": omv,
                         "opp_stop_w": float(ost), "opp_sim_os": _gap(omv, risk, ost)})
        out[sid] = _frame(rows, swap)
    return out, tfs


# ---------------------------------------------------------------- time
def time_trades(mod: Any, window: str, swap: SwapSpec) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    from datetime import date, timedelta
    lo, hi, y0, y1 = mod.WINDOWS[window]
    mod._init(str(RAW), y0, y1)
    m = mod._M
    hi_ns = min(int(pd.Timestamp(hi + timedelta(days=1), tz="UTC").value), mod.HOLDOUT_NS)
    out, tfs = {}, {}
    for sid in mod.RULES:
        key = f"S200-TIME-{sid}"
        tfs[key] = "1h"
        tr = mod.simulate(m, mod.candidates(m, sid, lo, hi, date(y0, 1, 1)), hi_ns)
        rows = []
        for r in tr.itertuples(index=False):
            side, stop = int(r.side), float(r.stop_usd)
            tgt = None if pd.isna(r.target_usd) else float(r.target_usd)
            gross = side * (float(r.exit_px) - float(r.entry))
            w = mod.walk(m, int(r.k), float(r.entry), -side, stop, tgt, int(r.t) + int(r.hold_ns))
            assert w is not None
            ogross = -side * (float(w[1]) - float(r.entry))
            rows.append({"t_fill": int(r.entry_ns), "t_exit": int(r.exit_ns), "side": side, "risk": stop,
                         "gross": gross, "stop_w": float(r.kind == "stop"), "sim_os": _gap(gross, stop, r.kind == "stop"),
                         "px": float(r.entry), "sess": str(m.cost_label[int(r.k)]), "opp_t_exit": int(w[2]),
                         "opp_gross": ogross, "opp_stop_w": float(w[0] == "stop"),
                         "opp_sim_os": _gap(ogross, stop, w[0] == "stop")})
        out[key] = _frame(rows, swap)
    return out, tfs


ADAPTERS: dict[str, Callable[[Any, str, SwapSpec], tuple[dict[str, pd.DataFrame], dict[str, str]]]] = {
    "trend": trend_trades, "meanrev": meanrev_trades, "macro": macro_trades, "patterns": patterns_trades,
    "stats": stats_trades, "time": time_trades}


# ---------------------------------------------------------------- pricing and the read
def costs() -> tuple[C1Costs, A.ArmCosts]:
    legacy = A.load_costs(ROOT / "config" / "arm_costs.yaml")
    table = legacy.require_version(LEGACY_VERSION)
    return C1Costs.of_record(table, BROKER), legacy


def window_drift(window: str) -> float:
    """The window's log drift per ns from its first and last 1m mid (2010-01..2017-12, 2018-01..2025-09)."""
    lo, hi = WINDOW_BOUNDS[window]
    first = pd.read_parquet(RAW / f"xauusd_1m_dukascopy_{lo.year}.parquet", columns=["ts_utc", "bid_open", "ask_open"])
    last_y = (hi - pd.Timedelta(minutes=1)).year
    last = pd.read_parquet(RAW / f"xauusd_1m_dukascopy_{last_y}.parquet",
                           columns=["ts_utc", "bid_close", "ask_close"])
    last = last[pd.DatetimeIndex(last["ts_utc"]) < hi]
    A.assert_no_holdout_rows(last)
    f0 = first.sort_values("ts_utc").iloc[0]
    f1 = last.sort_values("ts_utc").iloc[-1]
    from goldbot.data.timeutil import epoch_ns
    t0 = int(epoch_ns(pd.DatetimeIndex([f0["ts_utc"]]))[0])
    t1 = int(epoch_ns(pd.DatetimeIndex([f1["ts_utc"]]))[0])
    return G.window_drift_per_ns((f0.bid_open + f0.ask_open) / 2, (f1.bid_close + f1.ask_close) / 2, t0, t1)


def priced(df: pd.DataFrame, c1: C1Costs, legacy: A.ArmCosts) -> dict[str, np.ndarray]:
    """Per trade: C1 net, its random-side twin's other side, the lane's own (dollar upper bound) net as the stress
    column, gross R."""
    shipped = SwapSpec.shipped()
    side = df.side.to_numpy(int)
    risk = df.risk.to_numpy(float)
    sess = df.sess.to_numpy(object)
    px = df.px.to_numpy(float)
    c_real = c1.cost_r(entry_px=px, stop_usd=risk, stop_exit=df.stop_w.to_numpy(float),
                       nights=df.nights.to_numpy(float), side=side, sessions=sess,
                       sim_overshoot_usd=df.sim_os.to_numpy(float))
    c_opp = c1.cost_r(entry_px=px, stop_usd=risk, stop_exit=df.opp_stop_w.to_numpy(float),
                      nights=df.opp_nights.to_numpy(float), side=-side, sessions=sess,
                      sim_overshoot_usd=df.opp_sim_os.to_numpy(float))
    gross = df.gross.to_numpy(float) / risk
    # sensitivity (reported, not gated): today's short credit may not have existed at 2010-17 rates
    c_nocredit = c1.model_copy(update={"swap_short_bps": min(0.0, c1.swap_short_bps)}).cost_r(
        entry_px=px, stop_usd=risk, stop_exit=df.stop_w.to_numpy(float), nights=df.nights.to_numpy(float), side=side,
        sessions=sess, sim_overshoot_usd=df.sim_os.to_numpy(float))
    rt, os_ = legacy.screen_charges(BROKER, sess, LEGACY_VERSION)
    old = (df.gross.to_numpy(float) + df.nights.to_numpy(float) * shipped.usd_per_oz(side) - rt
           - os_ * df.stop_w.to_numpy(float)) / risk
    return {"net": gross - c_real, "net_opp": df.opp_gross.to_numpy(float) / risk - c_opp, "old": old,
            "gross": gross, "cost": c_real, "net_no_short_credit": gross - c_nocredit}


def read_rule(sid: str, df: pd.DataFrame, tf: str, c1: C1Costs, legacy: A.ArmCosts, drift: float,
              window: str) -> dict[str, Any]:
    n_min = G.N_MIN_SLOW if tf in ("4h", "1d") else G.N_MIN
    if df.empty:
        return {"id": sid, "tf": tf, "n": 0, "verdict": "FAIL", "failed": ["n"]}
    p = priced(df, c1, legacy)
    ent = df.t_fill.to_numpy(np.int64)
    hold = (df.t_exit.to_numpy(np.int64) - ent).astype(float)
    v = G.explore_verdict(net_r=p["net"], net_r_opposite=p["net_opp"], gross_r=p["gross"], side=df.side.to_numpy(int),
                          entry_px=df.px.to_numpy(float), risk_usd=df.risk.to_numpy(float), hold_ns=hold,
                          entry_ns=ent, drift_per_ns=drift, n_min=n_min)
    longs = df.side.to_numpy(int) > 0
    yrs = G.by_year(p["net"], ent)
    out: dict[str, Any] = {
        "id": sid, "tf": tf, "n": v.n, "n_min": n_min, "old_net_r": float(p["old"].mean()),
        "old_t": G.t_of(p["old"]), "c1_net_r": v.net_r, "c1_cost_r": float(p["cost"].mean()), "day_t": v.day_t,
        "years_positive": v.years_positive, "years": {str(k): round(x, 4) for k, x in yrs.items()},
        "gross_r": v.gross_r, "gross_t_trade": v.gross_t, "gross_day_t": G.day_t(p["gross"], ent),
        "drift_adjusted_gross_r": v.drift_adjusted_gross_r, "random_side_excess_r": v.random_side_excess_r,
        "long_n": int(longs.sum()), "long_net_r": float(p["net"][longs].mean()) if longs.any() else None,
        "short_n": int((~longs).sum()), "short_net_r": float(p["net"][~longs].mean()) if (~longs).any() else None,
        "median_risk_usd": float(np.median(df.risk)), "failed": list(v.failed),
        "net_no_short_credit_r": float(p["net_no_short_credit"].mean()),
        "day_t_no_short_credit": G.day_t(p["net_no_short_credit"], ent),
        "stop_exit_share": float(df.stop_w.mean()), "mean_nights": float(df.nights.mean()),
    }
    if window == "explore":
        out["verdict"] = "PASS" if v.passed else "FAIL"
        if len(p["net"]) >= 30:
            di = DsrInputs.of(p["net"])
            out["dsr_k34"], out["dsr_k200"] = di.dsr(34), di.dsr(200)
    else:
        out["ex2025_net_r"] = float(p["net"][pd.DatetimeIndex(pd.to_datetime(ent, utc=True)).year < 2025].mean())
    return out


def run_lane(args: tuple[str, str, tuple[str, ...] | None, str]) -> dict[str, Any]:
    lane, window, only, tag = args
    mod = _load_module(lane)
    c1, legacy = costs()
    swap = SwapSpec(long_usd_per_lot=0.0, short_usd_per_lot=0.0, triple_weekday=c1.triple_weekday,
                    server_tz=c1.server_tz)
    trades, tfs = ADAPTERS[lane](mod, window, swap)
    drift = window_drift(window)
    res, days = [], {}
    for sid in sorted(trades):
        if only is not None and sid not in only:
            continue
        df = trades[sid]
        res.append(read_rule(sid, df, tfs[sid], c1, legacy, drift, window))
        if len(df):
            p = priced(df, c1, legacy)
            days[sid] = {"entry_ns": df.t_fill.to_numpy(np.int64).tolist(), "net": p["net"].tolist(),
                         "gross": p["gross"].tolist(),
                         "excess": G.random_side_excess(p["net"], p["net_opp"]).tolist()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"series_{lane}_{tag}.json").write_text(json.dumps(days))      # ~/tmp only: per-trade R, never committed
    return {"lane": lane, "window": window, "cost_model": c1.version, "drift_per_ns": drift, "results": res}


def _pool(jobs: list[tuple[str, str, tuple[str, ...] | None, str]], workers: int) -> list[dict[str, Any]]:
    w = max(1, min(workers, MAX_WORKERS, len(jobs)))
    with get_context("spawn").Pool(w) as pool:
        return pool.map(run_lane, jobs, chunksize=1)


# ---------------------------------------------------------------- composites (frozen before confirm)
def build_composites() -> dict[str, Any]:
    """Per family (lane): its C1 explore passers plus members with day-summed gross t >= 2, minus any rule whose
    confirm window was already read; a family with fewer than two members has no composite (it is its single)."""
    fams: dict[str, Any] = {}
    singles: list[str] = []
    for lane in LANES:
        f = OUT / f"explore_{lane}.json"
        rows = json.loads(f.read_text())["results"]
        passers = [r["id"] for r in rows if r.get("verdict") == "PASS"]
        strong = [r["id"] for r in rows if r.get("n", 0) and np.isfinite(r.get("gross_day_t", np.nan))
                  and r["gross_day_t"] >= 2.0]
        members = sorted(set(passers) | set(strong))
        singles += [x for x in passers if x not in CONFIRM_READ]
        members = [x for x in members if x not in CONFIRM_READ]
        if len(members) >= 2:
            fams[lane] = {"id": f"C1-COMP-{lane.upper()}", "members": members,
                          "passers": [x for x in passers if x in members],
                          "gross_t2_only": [x for x in members if x not in passers]}
    return {"amendment": "C1 (2026-11-02)", "rule": "equal-weight day composite of explore passers + members with "
            "day-summed gross t >= 2; rules whose confirm was read are excluded (TEST-OLD only)",
            "confirm": "once, BH-FDR q=0.10 over singles + composites (one-sided day-bootstrap p), net > 0, >= 5/8 "
            "years, n >= 100", "singles": sorted(singles), "composites": fams,
            "confirm_read_excluded": sorted(CONFIRM_READ)}


def composite_read(members: list[dict[str, Any]], window: str) -> dict[str, Any]:
    days, net = G.composite_days([(np.array(m["entry_ns"]), np.array(m["net"])) for m in members])
    _, gross = G.composite_days([(np.array(m["entry_ns"]), np.array(m["gross"])) for m in members])
    _, exc = G.composite_days([(np.array(m["entry_ns"]), np.array(m["excess"])) for m in members])
    ent = days * G.DAY_NS
    yrs = G.by_year(net, ent)
    out = {"n_trades": int(sum(len(m["net"]) for m in members)), "n_days": int(len(days)),
           "day_net_r": float(net.mean()), "day_t": G.t_of(net), "years_positive": G.years_positive(net, ent),
           "years": {str(k): round(v, 4) for k, v in yrs.items()}, "day_gross_r": float(gross.mean()),
           "day_random_side_excess_r": float(exc.mean())}
    if window == "confirm":
        out["ex2025_day_net_r"] = float(net[pd.DatetimeIndex(pd.to_datetime(ent, utc=True)).year < 2025].mean())
    return out


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("explore", "composites", "confirm"))
    ap.add_argument("--lanes", default=",".join(LANES))
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--rehearse", action="store_true",
                    help="confirm: run the whole confirm path on the EXPLORE window (no confirm bar read), to find a "
                         "defect before the one real look")
    a = ap.parse_args()
    if a.workers > MAX_WORKERS:
        raise SystemExit(f"at most {MAX_WORKERS} worker processes")
    lanes = [x for x in a.lanes.split(",") if x]
    if a.mode == "explore":
        for r in _pool([(lane, "explore", None, "explore") for lane in lanes], a.workers):
            (OUT / f"explore_{r['lane']}.json").write_text(json.dumps(r, indent=1, default=float))
            for x in r["results"]:
                print(x["id"], x.get("n"), round(x.get("old_net_r", float("nan")), 3),
                      round(x.get("c1_net_r", float("nan")), 3), x.get("verdict"), x.get("failed"))
        return
    if a.mode == "composites":
        comp = build_composites()
        for lane, fam in comp["composites"].items():
            series = json.loads((OUT / f"series_{lane}_explore.json").read_text())
            fam["explore_read"] = composite_read([series[x] for x in fam["members"]], "explore")
        COMPOSITES.parent.mkdir(parents=True, exist_ok=True)
        COMPOSITES.write_text(yaml.safe_dump(comp, sort_keys=False, width=118))
        print(COMPOSITES.read_text())
        return
    # confirm: once, on the committed definitions only (a rehearsal reads the explore window instead)
    window, tag = ("explore", "rehearsal") if a.rehearse else ("confirm", "confirm")
    done = OUT / f"{tag}.json"
    if done.exists() and not a.rehearse:
        raise SystemExit(f"confirm already read ({done}): one look only")
    comp = yaml.safe_load(COMPOSITES.read_text())
    ids = set(comp["singles"]) | {m for fam in comp["composites"].values() for m in fam["members"]}
    if ids & CONFIRM_READ:
        raise SystemExit(f"refused: confirm already read for {sorted(ids & CONFIRM_READ)}")
    need = sorted({_lane_of(x) for x in ids})
    lane_res = _pool([(lane, window, tuple(sorted(x for x in ids if _lane_of(x) == lane)), tag) for lane in need],
                     a.workers)
    rng = np.random.default_rng(20261102)
    tests: list[dict[str, Any]] = []
    by_id = {x["id"]: x for r in lane_res for x in r["results"]}
    series = {lane: json.loads((OUT / f"series_{lane}_{tag}.json").read_text()) for lane in need}
    for sid in comp["singles"]:
        s = series[_lane_of(sid)].get(sid)
        x = by_id[sid]
        p = G.day_bootstrap_p(G.day_sums(np.array(s["net"]), np.array(s["entry_ns"])), rng) if s else 1.0
        tests.append({"id": sid, "kind": "single", "p": p, "n": x["n"], "net_r": x.get("c1_net_r"),
                      "years_positive": x.get("years_positive"), "day_t": x.get("day_t"),
                      "ex2025_net_r": x.get("ex2025_net_r"), "gross_r": x.get("gross_r"),
                      "drift_adjusted_gross_r": x.get("drift_adjusted_gross_r"),
                      "random_side_excess_r": x.get("random_side_excess_r")})
    for lane, fam in comp["composites"].items():
        mem = [series[lane][m] for m in fam["members"] if m in series[lane]]
        cr = composite_read(mem, window)
        days, net = G.composite_days([(np.array(m["entry_ns"]), np.array(m["net"])) for m in mem])
        tests.append({"id": fam["id"], "kind": "composite", "p": G.day_bootstrap_p(net, rng), "n": cr["n_trades"],
                      "net_r": cr["day_net_r"], "years_positive": cr["years_positive"], "day_t": cr["day_t"],
                      "ex2025_net_r": cr.get("ex2025_day_net_r"), "gross_r": cr["day_gross_r"],
                      "random_side_excess_r": cr["day_random_side_excess_r"], "members": fam["members"],
                      "member_reads": {m: by_id.get(m) for m in fam["members"]}})
    rej = G.bh_fdr(np.array([t["p"] for t in tests]), G.FDR_Q)
    for t, r in zip(tests, rej, strict=True):
        t["bh_reject"] = bool(r)
        t["pass"] = bool(r and (t["net_r"] or 0) > 0 and (t["years_positive"] or 0) >= G.MIN_YEARS_POSITIVE
                         and t["n"] >= G.N_MIN)
    done.write_text(json.dumps({"window": window, "m": len(tests), "q": G.FDR_Q, "tests": tests, "lanes": lane_res},
                               indent=1, default=float))
    for t in tests:
        print(t["id"], t["kind"], t["n"], round(t["net_r"] or 0, 4), round(t["p"], 4), t["bh_reject"], t["pass"])


def _lane_of(sid: str) -> str:
    return sid.split("-")[1].lower()


if __name__ == "__main__":
    main()
