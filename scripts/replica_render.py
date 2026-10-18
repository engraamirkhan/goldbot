"""Render OUR replica of the owner's chart indicators for a window of pulled bars. Local-only (matplotlib, the `viz`
extra); nothing is written outside --out and no image or data is ever committed.

    python scripts/replica_render.py --tf 15m --mode boxes     --out ~/tmp/ui-audit/replica_15m.png
    python scripts/replica_render.py --tf 15m --mode structure --out ~/tmp/ui-audit/replica_15m_structure.png

Bars come from the pulled chart-site parquet files (default ~/tmp/tv, the same OANDA bars the owner's chart shows).
Every series is computed on the full history and then cut to the window, so each value is the point-in-time value at
that bar. Modes:

* `boxes` - candles coloured by our bar-colour state (`candle_class`), EMA5/10/20/50/200, the support/resistance
  boxes in force at the last bar, break triangles (up/down) and EMA10/20 cross triangles, the multi-timeframe trend
  table, and the money-flow pane (60-80 / 40-60 / 20-40 bands, extreme circles and our signal markers).
* `structure` - the same candles, MA10/MA20 + EMA50 + MA200, BOS/CHoCH with dashed break lines (internal and swing),
  premium / equilibrium / discount bands of the swing range, strong / weak high / low, PDH/PDL and PWH/PWL, the daily
  floor pivots PP and R1 (the short yellow lines), the golden-zone band and the trend table.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.features import smc_levels
from goldbot.features import smc_replica as R
from goldbot.features.mfi_signals import mfi_signals

TV_FILES = {"15m": ("01", "15"), "1h": ("02", "60"), "4h": ("03", "240"), "1d": ("04", "1D")}
HTF_ORDER = ("1h", "4h", "1d")
UP, DOWN = "#26a69a", "#ef5350"
CLASS_COLOURS = {R.CANDLE_HIGH_BULL: "#4cea24", R.CANDLE_HIGH_BEAR: "#f23645", R.CANDLE_LOW_VOL: "#2962ff",
                 R.CANDLE_ENGULF: "#787b86", R.CANDLE_TRAVERSE: "#ff9800"}
EMA_COLOURS = {5: "#787b86", 10: "#4cea24", 20: "#ee3c7a", 50: "#b51292", 200: "#00bcd4"}


def load_bars(root: Path, tf: str) -> pd.DataFrame:
    n, name = TV_FILES[tf]
    b = pd.read_parquet(root / f"bars_auth{n}_OANDA_XAUUSD_{name}.parquet").sort_values("bar_time")
    b["bar_time"] = pd.to_datetime(b["bar_time"], utc=True)
    return b.reset_index(drop=True)


def _atr(b: pd.DataFrame, n: int = 14) -> np.ndarray:
    return R.atr_seeded(b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy(), n)


def compute(root: Path, tf: str, traverse: bool) -> dict[str, Any]:
    """Everything the figure needs, on the full history of `tf`."""
    b = load_bars(root, tf)
    params = R.ReplicaParams(candle_traverse=traverse)
    frame, state = R.replica_run(b, params)
    o, h, lo, c = (b[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    trace: list[tuple[np.ndarray, np.ndarray]] = []
    R.sr_channel_breaks(o, h, lo, c, params.sr_pivot_len, params.sr_width_pct, params.sr_max_channels,
                        params.sr_lookback, params.sr_range_window, trace)
    ema = R.ma_ribbon(c, "ema")
    htf = {t: load_bars(root, t) for t in HTF_ORDER}
    table = smc_levels.trend_table(b, {t: v for t, v in htf.items() if t != tf}, base_tf=tf)
    for t in HTF_ORDER:
        if t == tf:
            table[smc_levels.TREND_COLUMNS[t]] = table[smc_levels.TREND_COLUMNS[tf]]
    return {"bars": b, "frame": frame, "state": state, "params": params, "trace": trace, "ema": ema,
            "ma": R.ma_ribbon(c, "sma_ema"), "table": table, "atr": _atr(b),
            "mfi_sig": mfi_signals(frame["mfi"].to_numpy(), h, lo, params.sr_pivot_len),
            "zones": smc_levels.swing_zones(h, lo, frame["trend_swing"].to_numpy(), params.swing_len),
            "levels": smc_levels.period_levels(b["bar_time"], h, lo, c),
            "range": R.range_retracement(h, lo, params.range_len, params.range_fracs),
           }


def break_lines(h: np.ndarray, lo: np.ndarray, frame: pd.DataFrame, scale: str,
                length: int) -> list[tuple[int, int, float, str, bool]]:
    """(start bar, end bar, level, label, bullish) per BOS/CHoCH of a scale: the line runs from the broken pivot's bar
    to the break bar."""
    piv_h, piv_l = R.leg_pivots(h, lo, length)
    out = []
    top_i = btm_i = -1
    for i in range(len(h)):
        if not np.isnan(piv_h[i]):
            top_i = i - length
        if not np.isnan(piv_l[i]):
            btm_i = i - length
        for kind in ("bos", "choch"):
            if frame[f"{kind}_bull_{scale}"].iloc[i] and top_i >= 0:
                out.append((top_i, i, h[top_i], kind.upper() if kind == "choch" else "BOS", True))
            if frame[f"{kind}_bear_{scale}"].iloc[i] and btm_i >= 0:
                out.append((btm_i, i, lo[btm_i], kind.upper() if kind == "choch" else "BOS", False))
    return out


def _candles(ax: Any, x: np.ndarray, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray,
             cls: np.ndarray) -> None:
    colours = [CLASS_COLOURS.get(int(k), UP if ci >= oi else DOWN) for k, oi, ci in zip(cls, o, c, strict=True)]
    ax.vlines(x, lo, h, colors=colours, linewidth=1)
    body_lo, body_hi = np.minimum(o, c), np.maximum(o, c)
    ax.bar(x, np.maximum(body_hi - body_lo, 0.05), bottom=body_lo, width=0.7, color=colours, linewidth=0)


def _table(ax: Any, rows: list[tuple[str, int]]) -> None:
    ax.text(0.985, 0.03, transform=ax.transAxes, ha="right", va="bottom", color="#b2b5be", fontsize=8,
            bbox={"facecolor": "#363a45", "edgecolor": "#2962ff", "pad": 6}, zorder=20, linespacing=1.5,
            family="monospace",
            s="\n".join(["Trend table"] + [f"{n:>3}  " + {1: "▲▲", -1: "▼▼", 0: "--"}[v] for n, v in rows]))


def render(root: Path, tf: str, mode: str, bars: int, end: str | None, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = compute(root, tf, traverse=(mode == "boxes"))
    b, f = d["bars"], d["frame"]
    last = len(b) - 1 if end is None else int(b["bar_time"].searchsorted(pd.Timestamp(end, tz="UTC"), side="right")) - 1
    sl = slice(max(0, last - bars + 1), last + 1)
    x = np.arange(sl.start, last + 1)
    o, h, lo, c = (b[k].to_numpy(dtype=float)[sl] for k in ("open", "high", "low", "close"))
    fig, (ax, am) = plt.subplots(2, 1, figsize=(20, 9.5), sharex=True, gridspec_kw={"height_ratios": [3, 1.1], "hspace": 0.02},
                                 facecolor="#0e0e0e")
    for a in (ax, am):
        a.set_facecolor("#0e0e0e")
        a.tick_params(colors="#b2b5be", labelsize=8)
        a.yaxis.tick_right()
        for sp in a.spines.values():
            sp.set_color("#2a2e39")
    _candles(ax, x, o, h, lo, c, f["candle_class"].to_numpy()[sl])
    lo_y, hi_y = lo.min(), h.max()
    pad = (hi_y - lo_y) * 0.08
    lines = d["ema"] if mode == "boxes" else d["ma"]
    if mode == "boxes":
        for n in (5, 10, 20, 50, 200):
            ax.plot(x, lines[f"ma{n}"][sl], color=EMA_COLOURS[n], linewidth={5: 0.8, 10: 1.3, 20: 1.6, 50: 1.8, 200: 3}[n])
        up, dn = f["sr_break_up"].to_numpy()[sl].astype(bool), f["sr_break_dn"].to_numpy()[sl].astype(bool)
        ax.scatter(x[up], lo[up] - pad * 0.3, marker="^", color="#00e676", s=28, zorder=6)
        ax.scatter(x[dn], h[dn] + pad * 0.3, marker="v", color="#f23645", s=28, zorder=6)
        e10, e20 = d["ema"]["ma10"], d["ema"]["ma20"]
        cu, cd = R.cross_over(e10, e20)[sl], R.cross_under(e10, e20)[sl]
        ax.scatter(x[cu], lo[cu] - pad * 0.7, marker="^", color="#2962ff", s=28, zorder=6)
        ax.scatter(x[cd], h[cd] + pad * 0.7, marker="v", color="#2962ff", s=28, zorder=6)
        tops, bots = d["trace"][last]
        for t_, b_ in zip(tops, bots, strict=True):
            ax.axhspan(b_, t_, color="#f23645", alpha=0.35 if t_ - b_ > 0.05 * (hi_y - lo_y) * 0.1 else 0.6, linewidth=0)
            ax.axhline(t_, color="#f23645", linewidth=0.6, alpha=0.7)
    else:
        for key, col, lw in (("ma10", "#ef5350", 1.2), ("ma20", "#4a90d9", 1.6), ("ma50", "#e7b73c", 1.8),
                             ("ma200", "#e7b73c", 3.0)):
            ax.plot(x, lines[key][sl], color=col, linewidth=lw)
        g1, g2 = d["range"][0][sl], d["range"][1][sl]
        ax.fill_between(x, g1, g2, color="#b8860b", alpha=0.35, linewidth=0)
        z = d["zones"].iloc[sl]
        xr = x[-1]
        ax.axhspan(z.premium_lo.iloc[-1], z.swing_top.iloc[-1], xmin=0, xmax=0.985, color="#f23645", alpha=0.25, linewidth=0)
        ax.axhspan(z.swing_btm.iloc[-1], z.discount_hi.iloc[-1], xmin=0, xmax=0.985, color="#089981", alpha=0.25, linewidth=0)
        ax.axhspan(z.equilibrium_lo.iloc[-1], z.equilibrium_hi.iloc[-1], xmin=0, xmax=0.985, color="#b2b5be", alpha=0.25, linewidth=0)
        ax.text(x[0] + 3, z.swing_top.iloc[-1] - pad * 0.5, "Premium", color="#f23645", clip_on=True, fontsize=8)
        ax.text(xr + 2, z.equilibrium_lo.iloc[-1] - pad * 0.4, "Equilibrium", color="#b2b5be", clip_on=True, fontsize=8)
        ax.text(x[0] + 3, z.swing_btm.iloc[-1] + pad * 0.2, "Discount", color="#089981", clip_on=True, fontsize=8)
        strong_hi = bool(z.strong_high.iloc[-1])
        ax.axhline(z.swing_top.iloc[-1], color="#f23645", linewidth=0.7)
        ax.text(xr + 2, z.swing_top.iloc[-1], "Strong High" if strong_hi else "Weak High", color="#f23645", clip_on=True, fontsize=7, va="bottom")
        ax.axhline(z.swing_btm.iloc[-1], color="#089981", linewidth=0.7)
        ax.text(xr + 2, z.swing_btm.iloc[-1], "Weak Low" if strong_hi else "Strong Low", color="#089981", clip_on=True, fontsize=7, va="top")
        lv = d["levels"].iloc[last]
        for key, lab in (("pdh", "PDH"), ("pdl", "PDL"), ("pwh", "PWH"), ("pwl", "PWL")):
            if np.isfinite(lv[key]):
                ax.axhline(lv[key], color="#2157f3", linewidth=0.8)
                ax.text(xr + 2, lv[key], lab, color="#2157f3", clip_on=True, fontsize=7, va="bottom")
        for key in ("pivot_pp", "pivot_r1", "pivot_s1"):
            if np.isfinite(lv[key]):
                ax.hlines(lv[key], xr - 5, xr + 8, color="#ffeb3b", linewidth=2)
        hh, ll = b["high"].to_numpy(dtype=float), b["low"].to_numpy(dtype=float)
        for scale, length, ls, alpha in (("swing", d["params"].swing_len, "--", 1.0), ("internal", d["params"].internal_len, ":", 0.8)):
            for s0, s1, level, label, bull in break_lines(hh, ll, f, scale, length):
                if s1 < sl.start or s0 > last:
                    continue
                col = "#089981" if bull else "#f23645"
                ax.hlines(level, max(s0, sl.start), s1, color=col, linestyle=ls, linewidth=0.9, alpha=alpha)
                if scale == "swing" or label == "CHOCH":
                    ax.text((max(s0, sl.start) + s1) / 2, level, label.replace("CHOCH", "CHoCH"), color=col, clip_on=True, fontsize=8,
                            ha="center", va="bottom" if bull else "top")
    _table(ax, [(tf, int(d["table"].iloc[last][smc_levels.TREND_COLUMNS[tf]])), ("H1", int(d["table"].iloc[last].trend_1h)),
                ("H4", int(d["table"].iloc[last].trend_4h)), ("D", int(d["table"].iloc[last].trend_1d))])
    if mode == "structure":                      # keep the equilibrium band and the weekly high in view
        lo_y = min(lo_y, d["zones"].equilibrium_lo.iloc[last])
        pw = d["levels"].pwh.iloc[last]
        hi_y = max(hi_y, pw if np.isfinite(pw) and pw < hi_y * 1.012 else hi_y)
    ax.set_ylim(lo_y - pad, hi_y + pad)
    ax.set_xlim(x[0] - 1, x[-1] + 12)
    ax.set_title(f"our replica - XAUUSD {tf} - {mode} - to {b['bar_time'].iloc[last]:%Y-%m-%d %H:%M} UTC", color="#b2b5be", fontsize=9, loc="left")
    # money-flow pane
    m = f["mfi"].to_numpy()[sl]
    for lo_b, hi_b, col in ((60, 80, "#2e7d32"), (40, 60, "#4a4e5a"), (20, 40, "#b03a3a")):
        am.axhspan(lo_b, hi_b, color=col, alpha=0.55, linewidth=0)
    for lvl in (20, 40, 60, 80):
        am.axhline(lvl, color="#b2b5be", linewidth=0.4, linestyle="--")
    am.plot(x, m, color="white", linewidth=1.1)
    sg = d["mfi_sig"].iloc[sl]
    ob, os_ = (sg.mfi_extreme_circle == 1).to_numpy(), (sg.mfi_extreme_circle == -1).to_numpy()
    am.scatter(x[ob], m[ob], s=60, color="#f23645", zorder=5)
    am.scatter(x[os_], m[os_], s=60, color="#4caf50", zorder=5)
    for col, mk, name in (("mfi_double_circle", "o", "second circle"), ("mfi_pullback_hold", "^", "pullback hold"),
                          ("mfi_divergence", "D", "divergence"), ("mfi_cross50", "|", "50 cross")):
        v = sg[col].to_numpy()
        for sign, colr in ((1, "#4caf50"), (-1, "#f23645")):
            k = v == sign
            if k.any():
                am.scatter(x[k], m[k], marker=mk, s=45 if mk != "|" else 80, facecolors="none" if mk == "o" else colr,
                           edgecolors=colr, zorder=5, label=name if sign == 1 else None)
    am.set_ylim(0, 100)
    am.set_yticks([0, 20, 40, 60, 80, 100])
    am.text(x[0] + 1, 92, f"money flow {d['params'].mfi_len}   {m[-1]:.1f}", color="#b2b5be", fontsize=8)
    am.legend(loc="upper right", fontsize=7, facecolor="#1a1a1a", labelcolor="#b2b5be", ncol=4, frameon=False)
    ts = b["bar_time"].iloc[sl].reset_index(drop=True)
    ticks = np.linspace(0, len(x) - 1, 9).astype(int)
    am.set_xticks(x[ticks])
    am.set_xticklabels([f"{ts[i]:%m-%d %H:%M}" for i in ticks])
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tf", choices=list(TV_FILES), default="15m")
    ap.add_argument("--mode", choices=("boxes", "structure"), default="boxes")
    ap.add_argument("--bars", type=int, default=150)
    ap.add_argument("--end", default=None, help="last bar open, UTC (default: the last pulled bar)")
    ap.add_argument("--root", default=os.path.expanduser("~/tmp/tv"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    render(Path(a.root), a.tf, a.mode, a.bars, a.end, Path(os.path.expanduser(a.out)))
    print(a.out)


if __name__ == "__main__":
    main()
