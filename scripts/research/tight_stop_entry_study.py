"""Tight-stop entry study (pre-trial, exploratory; NOT a registry trial). docs/research/tight-stop-entry-study-2026-10-18.md

Question: can a deferred 1m pullback/retest entry with a structural stop make the specialists' signals fit the
£1,000 account's £5 risk at 0.01 lot (stop <= ~$6.65 per oz), and what does it do to expectancy per trade and per day?

Signals: each specialist's own code (`candidates_in_context` on goldbot's decision frame, context bars from
`resample_bars`, no macro/positioning ctx), one position at a time per family and entry variant.
Execution: 1m Dukascopy mid bars; flat round-trip cost 0.71 $/oz; long swap -0.60 $/oz per rollover (22:00 UTC,
Wednesday x3), short 0 (settings priors). Barrier exits only (the families' trail/scale-out policies are not modelled).

Holdout (research.holdout_from/to 2025-10-01..2026-09-30): outcomes (R, £) are NOT computed there; only outcome-free
descriptives (ATR, stop distances, fill rates, fit fractions). Design sample 2024-01-01..2025-09-30, check 2023.

Usage:
  gh release download data-v1 -p 'xauusd_1m_dukascopy_202[2-6].parquet' -D data/dl_tight
  uv run python scripts/research/tight_stop_entry_study.py --data data/dl_tight --out ~/tmp/tight_stop
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from goldbot.config import tf_seconds  # noqa: E402
from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402
from goldbot.features.mtf import TF_LABEL, context_tfs  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.research.pipeline import build_decision_frame  # noqa: E402
from goldbot.specialists import SPECIALISTS  # noqa: E402

COST = 0.71            # $/oz round trip (brief: IC Markets Raw, spread + commission + slippage)
SWAP_LONG = -0.60      # $/oz per rollover (settings swap_long_usd_per_lot -60 / 100 oz)
GBPUSD = 1.33
RISK_USD = 5.0 * GBPUSD   # £5 = 0.5% of £1,000
BUF = 0.30             # $ beyond the structural level (about two raw spreads)
PULL_ATR15 = 0.25      # T1: minimum pullback below/above the signal close, x ATR15
WINDOWS = (15, 30, 60, 120)
HOLD_FROM, HOLD_TO = pd.Timestamp("2025-10-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC")
PERIODS = {"check_2023": ("2023-01-01", "2024-01-01"), "design_2024_25Q3": ("2024-01-01", "2025-10-01")}
FAMILIES = [("session_open", "15m"), ("mean_reversion", "15m"), ("intraday_momentum", "15m"), ("breakout", "15m"),
            ("breakout", "1h"), ("trend", "1h"), ("tsmom", "1h"), ("asia_drift", "1h")]


# ------------------------------------------------------------------------------------------------ signals
def signals(b1: pd.DataFrame) -> pd.DataFrame:
    m15 = mid(resample_bars(b1, "15m")).reset_index(drop=True)
    a15 = atr(m15, 14).to_numpy()
    vis15 = epoch_ns(m15["visible_at"])
    lo15, hi15 = m15["low"].to_numpy(), m15["high"].to_numpy()
    swing_lo = pd.Series(lo15).rolling(4, min_periods=4).min().to_numpy()   # last 4 completed 15m bars
    swing_hi = pd.Series(hi15).rolling(4, min_periods=4).max().to_numpy()
    out, frames = [], {}
    for fam, tf in FAMILIES:
        if tf not in frames:
            b_dec = resample_bars(b1, tf).reset_index(drop=True)
            ctx_bars = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
            frames[tf] = (b_dec, ctx_bars, build_decision_frame(b_dec, ctx_bars))
            print(f"frame {tf}: {len(b_dec):,} bars", flush=True)
        b_dec, ctx_bars, (m, X) = frames[tf]
        cls = SPECIALISTS[fam]
        spec = cls(timeframe=tf) if tf != cls.timeframe else cls()
        c = spec.candidates_in_context(m, X, ctx_bars, None)
        c = spec.complete_windows(b_dec, c)
        own = spec.barrier_atr(m, ctx_bars)
        a = (atr(m, 14) if own is None else own).to_numpy()
        ls = spec.label_spec
        idx = c["idx"].to_numpy(dtype=int)
        t0 = epoch_ns(m["visible_at"])[idx]
        j15 = np.searchsorted(vis15, t0, side="right") - 1        # last 15m bar visible at t0
        rr = ls.target_atr / ls.stop_atr if getattr(ls, "has_target", True) and ls.target_atr < 50 else np.nan
        df = pd.DataFrame({
            "family": f"{fam}/{tf}", "t0": t0, "side": c["side"].to_numpy(dtype=int),
            "atr_sig": a[idx], "sig_high": m["high"].to_numpy()[idx], "sig_low": m["low"].to_numpy()[idx],
            "sig_close": m["close"].to_numpy()[idx], "atr15": a15[j15], "swing_lo15": swing_lo[j15],
            "swing_hi15": swing_hi[j15], "rr": rr, "hold_ns": (ls.max_bars + 1) * tf_seconds(tf) * 10**9})
        print(f"{fam}/{tf}: {len(df):,} signals (rr {rr:.2f}, hold {(ls.max_bars + 1) * tf_seconds(tf) / 3600:.1f} h)",
              flush=True)
        out.append(df)
    return pd.concat(out, ignore_index=True).dropna(subset=["atr_sig", "atr15"]).reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ 1m execution
class Path1m:
    def __init__(self, b1: pd.DataFrame) -> None:
        m = mid(b1)
        self.ts = epoch_ns(m["ts_utc"])
        self.o, self.h, self.l, self.c = (m[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))

    def first(self, t: int) -> int:
        return int(np.searchsorted(self.ts, t, side="left"))

    def exit(self, j: int, side: int, entry: float, stop: float, tgt: float, t_end: int,
             limit_fill: bool) -> tuple[int, float, str, bool]:
        """Barrier exit from bar j (the entry bar). Stop before target on a shared bar (conservative); on a limit-fill
        bar the stop counts, the target does not. Time exit at the close of the last bar before t_end."""
        k = int(np.searchsorted(self.ts, t_end, side="left"))
        if k <= j:
            return j, entry, "time", False
        lo, hi, op = self.l[j:k], self.h[j:k], self.o[j:k]
        s_hit = (lo <= stop) if side > 0 else (hi >= stop)
        if np.isfinite(tgt):
            g_hit = (hi >= tgt) if side > 0 else (lo <= tgt)
            if limit_fill:
                g_hit[0] = False
        else:
            g_hit = np.zeros(len(lo), dtype=bool)
        si = int(np.argmax(s_hit)) if s_hit.any() else 10**9
        gi = int(np.argmax(g_hit)) if g_hit.any() else 10**9
        if si == 10**9 and gi == 10**9:
            return k - 1, float(self.c[k - 1]), "time", False
        if si <= gi:
            px = stop
            if si > 0 or not limit_fill:          # gap through the stop at a bar open fills at the open
                px = min(stop, float(op[si])) if side > 0 else max(stop, float(op[si]))
            return j + si, px, "stop", si == gi        # ambiguous: stop and target inside one 1m bar
        return j + gi, tgt, "target", False


def rollovers(t_in: int, t_out: int) -> int:
    """Swap nights between entry and exit: 22:00 UTC rollovers Mon-Fri, Wednesday counted three times."""
    a, b = pd.Timestamp(t_in, tz="UTC"), pd.Timestamp(t_out, tz="UTC")
    if b <= a:
        return 0
    days = pd.date_range(a.normalize(), b.normalize(), freq="D", tz="UTC") + pd.Timedelta(hours=22)
    days = days[(days > a) & (days <= b) & (days.weekday < 5)]
    return int(len(days) + 2 * (days.weekday == 2).sum())


class Sig(NamedTuple):
    family: str
    t0: int
    side: int
    atr_sig: float
    sig_high: float
    sig_low: float
    sig_close: float
    atr15: float
    swing_lo15: float
    swing_hi15: float
    rr: float
    hold_ns: int


def _sigs(sig: pd.DataFrame) -> list[Sig]:
    cols = {f: sig[f].tolist() for f in Sig._fields}
    def f(k: str, i: int) -> float:
        return float(cols[k][i])

    return [Sig(str(cols["family"][i]), int(cols["t0"][i]), int(cols["side"][i]), f("atr_sig", i), f("sig_high", i),
                f("sig_low", i), f("sig_close", i), f("atr15", i), f("swing_lo15", i), f("swing_hi15", i), f("rr", i),
                int(cols["hold_ns"][i]))
            for i in range(len(sig))]


def simulate(sig: pd.DataFrame, p: Path1m, randomize: bool = False) -> pd.DataFrame:
    """Every variant for every signal (no one-at-a-time yet). Returns long rows: signal, variant, fill, stop, exit."""
    rng = np.random.default_rng(0)
    rows: list[tuple[Any, ...]] = []
    nmax = max(WINDOWS)
    for r in _sigs(sig):
        side = int(rng.choice([-1, 1])) if randomize else r.side
        i0 = p.first(r.t0)
        if i0 >= len(p.ts) or p.ts[i0] - r.t0 > 30 * 60 * 10**9:      # no 1m bar within 30 min: skip
            continue
        t_end = r.t0 + r.hold_ns
        e0 = float(p.o[i0])
        rr = r.rr

        def b_tgt(e: float, d: float, side: int = side, rr: float = rr) -> float:
            return e + side * rr * d if np.isfinite(rr) else float("nan")
        thesis = b_tgt(e0, 1.5 * r.atr_sig)              # baseline target price (the signal's own target level)

        def add(var: str, fill_min: float, j: int, entry: float, stop: float, tgt: float, limit_fill: bool,
                side: int = side, t_end: int = t_end, r: Sig = r) -> None:
            d = (entry - stop) * side
            if d <= 0:
                return
            k, px, why, amb = p.exit(j, side, entry, stop, tgt, t_end, limit_fill)
            gross = (px - entry) * side
            nights = rollovers(int(p.ts[j]), int(p.ts[k]) + 60 * 10**9)
            swap = SWAP_LONG * nights if side > 0 else 0.0
            rows.append((r.family, r.t0, side, var, fill_min, d, gross, gross - COST + swap, why,
                         int(p.ts[k]) - int(p.ts[j]), r.atr15, amb))

        # B: market at the next 1m open, stop 1.5 x ATR(signal tf); B15: stop 1.5 x ATR(15m)
        for var, d in (("B_1.5atr_sig", 1.5 * r.atr_sig), ("B15_1.5atr15", 1.5 * r.atr15)):
            add(var, 0, i0, e0, e0 - side * d, b_tgt(e0, d), False)
        # T3: market entry, stop beyond the last four completed 15m bars' extreme + buffer
        st3 = (r.swing_lo15 - BUF) if side > 0 else (r.swing_hi15 + BUF)
        if np.isfinite(st3):
            add("T3_mkt_swing15_rr", 0, i0, e0, st3, b_tgt(e0, (e0 - st3) * side), False)
        # window bars
        k_w = int(np.searchsorted(p.ts, r.t0 + nmax * 60 * 10**9, side="left"))
        lo, hi, cl, op = p.l[i0:k_w], p.h[i0:k_w], p.c[i0:k_w], p.o[i0:k_w]
        mins = (p.ts[i0:k_w] - r.t0) / 6e10
        # T2: limit at the signal bar's 50% (or the close if better), stop beyond the signal bar + buffer
        if side > 0:
            lim, st2 = min(r.sig_close, (r.sig_high + r.sig_low) / 2), r.sig_low - BUF
            touch = lo <= lim
        else:
            lim, st2 = max(r.sig_close, (r.sig_high + r.sig_low) / 2), r.sig_high + BUF
            touch = hi >= lim
        if touch.any():
            j = int(np.argmax(touch))
            fill = min(lim, float(op[j])) if side > 0 else max(lim, float(op[j]))
            for var, tg in (("T2_retrace50_rr", b_tgt(fill, (fill - st2) * side)), ("T2_retrace50_thesis", thesis)):
                if not (np.isfinite(tg) and (tg - fill) * side <= 0):
                    add(var, float(mins[j]), i0 + j, fill, st2, tg, True)
        # T1: pullback >= 0.25 ATR15 beyond the signal close, then a 1m close back beyond the extreme bar's far side;
        # entry at the next 1m open, stop beyond the pullback extreme + buffer
        need = PULL_ATR15 * r.atr15
        ext, ext_i = (np.inf if side > 0 else -np.inf), -1
        for j in range(len(lo)):
            if side > 0 and lo[j] < ext:
                ext, ext_i = float(lo[j]), j
            elif side < 0 and hi[j] > ext:
                ext, ext_i = float(hi[j]), j
            if ext_i < j and ((r.sig_close - ext) * side >= need) and (
                    (cl[j] > hi[ext_i]) if side > 0 else (cl[j] < lo[ext_i])):
                if i0 + j + 1 < len(p.ts) and j + 1 < len(lo):
                    st1 = ext - side * BUF
                    e1 = float(op[j + 1])
                    for var, tg in (("T1_pullback_reclaim_rr", b_tgt(e1, (e1 - st1) * side)),
                                    ("T1_pullback_reclaim_thesis", thesis)):
                        if not (np.isfinite(tg) and (tg - e1) * side <= 0):
                            add(var, float(mins[j + 1]), i0 + j + 1, e1, st1, tg, False)
                break
    return pd.DataFrame(rows, columns=["family", "t0", "side", "variant", "fill_min", "stop", "gross", "net", "why",
                                       "held_ns", "atr15", "ambiguous"])


def expand_windows(tr: pd.DataFrame) -> pd.DataFrame:
    """Deferred variants (T1, T2) become one variant per fill window N: filled iff the fill came within N minutes."""
    out = [tr[~tr["variant"].str.startswith(("T1", "T2"))]]
    for n in WINDOWS:
        d = tr[tr["variant"].str.startswith(("T1", "T2")) & (tr["fill_min"] < n)].copy()
        d["variant"] = d["variant"].astype(str).str.cat([f"_N{n}"] * len(d))
        out.append(d)
    return pd.concat(out, ignore_index=True)


def one_at_a_time(sig: pd.DataFrame, tr: pd.DataFrame) -> pd.DataFrame:
    """Per family and variant: a signal is eligible only when no earlier eligible signal's trade (or unfilled pending
    order, until its window ends) is still live. Returns per eligible signal: filled (bool) and the trade row."""
    res = []
    for (fam, var), g in tr.groupby(["family", "variant"]):
        s = sig[sig["family"] == fam].sort_values("t0")
        n = int(var.rsplit("_N", 1)[1]) if "_N" in var else 0
        by_t = g.set_index("t0")
        busy = -1
        for t0 in s["t0"].to_numpy():
            if t0 < busy:
                continue
            if t0 in by_t.index:
                row = by_t.loc[t0]
                row = row.iloc[0] if isinstance(row, pd.DataFrame) else row
                busy = t0 + int(row["fill_min"] * 6e10) + int(row["held_ns"]) + 60 * 10**9
                res.append({"family": fam, "variant": var, "t0": t0, "filled": True, **row.drop(["family", "variant"]).to_dict()})
            else:
                busy = t0 + n * 60 * 10**9
                res.append({"family": fam, "variant": var, "t0": t0, "filled": False})
    return pd.DataFrame(res)


# ------------------------------------------------------------------------------------------------ summaries
def sized(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["oz"] = np.where(df["filled"] & (df["stop"] <= RISK_USD), np.floor(RISK_USD / df["stop"].where(df["stop"] > 0)), 0)
    df["oz"] = df["oz"].fillna(0).clip(upper=100)
    df["gbp"] = df["oz"] * df["net"].fillna(0) / GBPUSD
    df["R_net"] = df["net"] / df["stop"]
    df["R_gross"] = df["gross"] / df["stop"]
    return df


def tstat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else np.nan


def summary(df: pd.DataFrame, days: int, outcomes: bool) -> pd.DataFrame:
    rows = []
    for (fam, var), g in df.groupby(["family", "variant"]):
        f = g[g["filled"]]
        fit = f[f["oz"] > 0]
        row = {"family": fam, "variant": var, "signals": len(g), "fill%": 100 * len(f) / max(len(g), 1),
               "stop_p25": f["stop"].quantile(0.25), "stop_med": f["stop"].median(), "stop_p75": f["stop"].quantile(0.75),
               "stop_atr15_med": (f["stop"] / f["atr15"]).median(),
               "fit%_of_fills": 100 * len(fit) / max(len(f), 1), "fit%_of_signals": 100 * len(fit) / max(len(g), 1)}
        if outcomes:
            row.update({"R_gross": f["R_gross"].mean(), "R_net": f["R_net"].mean(), "t_net": tstat(f["R_net"]),
                        "cost_R_med": (COST / f["stop"]).median(),
                        "ambig%": 100 * f["ambiguous"].astype(float).mean(),
                        "stop15m%": 100 * ((f["why"] == "stop") & (f["held_ns"] <= 15 * 6e10)).mean(),
                        "fit_n": len(fit), "fit_R_net": fit["R_net"].mean(), "fit_t": tstat(fit["R_net"]),
                        "gbp_per_trade": fit["gbp"].mean() if len(fit) else np.nan,
                        "gbp_per_day": fit["gbp"].sum() / days, "trades_per_day": len(fit) / days})
        rows.append(row)
    return pd.DataFrame(rows)


def hour_map(b1: pd.DataFrame, lo: str, hi: str) -> pd.DataFrame:
    m15 = mid(resample_bars(b1, "15m")).reset_index(drop=True)
    m15["atr15"] = atr(m15, 14)
    ts = pd.DatetimeIndex(m15["ts_utc"])
    sel = (ts >= pd.Timestamp(lo, tz="UTC")) & (ts < pd.Timestamp(hi, tz="UTC"))
    d = m15[sel].copy()
    d["hour"] = pd.DatetimeIndex(d["ts_utc"]).hour
    g = d.groupby("hour")["atr15"]
    return pd.DataFrame({"atr15_med": g.median(), "atr15_p25": g.quantile(0.25), "atr15_p75": g.quantile(0.75),
                         "fit%_1.5atr15": g.apply(lambda s: 100 * (1.5 * s <= RISK_USD).mean()),
                         "fit%_1.0atr15": g.apply(lambda s: 100 * (s <= RISK_USD).mean())})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    b1 = read_bar_files(Path(args.data), 2022, 2026)
    print(f"1m bars {len(b1):,}: {b1['ts_utc'].iloc[0]} .. {b1['ts_utc'].iloc[-1]}", flush=True)
    sig_f = out / "signals.parquet"
    sig = pd.read_parquet(sig_f) if sig_f.exists() else signals(b1)
    sig.to_parquet(sig_f)
    sig = sig[sig["t0"] >= pd.Timestamp("2023-01-01", tz="UTC").value].reset_index(drop=True)
    p = Path1m(b1)
    ts1 = pd.DatetimeIndex(b1["ts_utc"])
    for tag, rand in (("signal", False), ("random", True)):
        tr_f = out / f"trades_{tag}.parquet"
        tr = pd.read_parquet(tr_f) if tr_f.exists() else simulate(sig, p, randomize=rand)
        tr.to_parquet(tr_f)
        tr = expand_windows(tr)
        el = sized(one_at_a_time(sig, tr))
        el["ts"] = pd.to_datetime(el["t0"], utc=True)
        el.to_parquet(out / f"eligible_{tag}.parquet")
        for name, (lo, hi) in {**PERIODS, "holdout_descriptive": (str(HOLD_FROM.date()), str(HOLD_TO.date()))}.items():
            lo_t, hi_t = pd.Timestamp(lo, tz="UTC"), pd.Timestamp(hi, tz="UTC")
            d = el[(el["ts"] >= lo_t) & (el["ts"] < hi_t)]
            dd = pd.DatetimeIndex(ts1[(ts1 >= lo_t) & (ts1 < hi_t)].normalize().unique())
            days = int((dd.weekday < 5).sum())                 # trading days Mon-Fri (Sunday-evening minutes excluded)
            outcomes = name != "holdout_descriptive"          # never score the holdout
            if tag == "random" and not outcomes:
                continue
            s = summary(pd.concat([d, d.assign(family="ALL (pooled)")], ignore_index=True), days, outcomes)
            s.to_csv(out / f"summary_{tag}_{name}.csv", index=False)
            print(f"{tag} {name}: {len(d):,} eligible rows, {days} days", flush=True)
            if outcomes:
                f = d[d["filled"] & ~d["variant"].str.contains("_N15|_N120")].copy()
                f["w"] = pd.cut(f["stop"] / f["atr15"], [0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 99])
                f["early"] = (f["why"] == "stop") & (f["held_ns"] <= 15 * 6e10)
                f.groupby("w", observed=True).agg(n=("R_net", "size"), stop_med=("stop", "median"),
                                                  R_gross=("R_gross", "mean"), R_net=("R_net", "mean"),
                                                  stopped_15m=("early", "mean")).to_csv(
                    out / f"by_width_{tag}_{name}.csv")
            # hour map of fitting fills by signal hour (pooled families)
            if tag == "signal":
                f = d[d["filled"]].copy()
                f["hour"] = f["ts"].dt.hour
                f["fit"] = f["oz"] > 0
                f.pivot_table(index="hour", columns="variant", values="fit", aggfunc="mean").mul(100).to_csv(
                    out / f"fit_by_hour_{name}.csv")
    hour_map(b1, "2024-01-01", "2025-10-01").to_csv(out / "atr15_by_hour_design.csv")
    hour_map(b1, "2025-10-18", "2026-10-18").to_csv(out / "atr15_by_hour_last12m.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
