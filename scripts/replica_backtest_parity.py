"""Backtest parity of the `smc_replica` indicators: the same rule strategies, on the chart's own bars, traded once from
the chart's plotted events (A) and once from our replica (B), must give the same trade list.

Owner 2026-10-18: "backtesting can be used to validate and verify" the replica. This is verification, NOT a research
trial: no registry row, and no outcome is printed or ranked. The table shows parity only (signal and trade counts,
mismatched trades, |dR| between the two runs); profit and loss never leaves `compare_trades`.

Local only: reads the pull in ~/tmp/tv (loaded by `_load_pull` below), which never enters the repo; prints to
stdout and writes nothing.

Rules, fixed before any run (simple on purpose; they exercise the plots, they are not candidates):

* internal_choch - long on a bullish internal CHoCH, short on a bearish one.
* swing_bos - continuation: long on a bullish swing BOS, short on a bearish one.
* fvg_trend - long on a bullish FVG while the internal trend is bullish, short mirrored (trend = side of the last
  internal BOS/CHoCH, a bearish break on the same bar wins, as in `structure_breaks`).
* mfi_cross - long on the money-flow oversold cross (under 20), short on the overbought cross (over 80).
* ema_cross_b - long / short on study B's EMA10 x EMA20 cross-up / cross-down events.
* sma_cross_a - long / short when study A's SMA10 line crosses over / under its SMA20 line (lines, not events).
* sr_break - long on study B's resistance break, short on its support break (not on 1D: B draws no boxes there).

A bar where both sides fire is no trade. Each signal gets the same ATR barriers through `labels.triple_barrier`
(entry at the signal close, target 2 x ATR14, stop 1 x ATR14, 48 bars; zero spread, mid bars: parity needs no costs)
and `one_at_a_time` (one position at a time, as the engine trades). ATR is the replica's seeded ATR14 on the same
bars for both runs: it is not under test. R = side x (exit - entry) / (stop_atr x ATR).

Holdout (settings.research.holdout_from/to): signals inside it are compared as signals (bar and side) only; no trade
is simulated there (bars from the holdout start are cut before the barrier run, so every trade exits before it). The
15m pull lies wholly inside the holdout, so its rows show signal parity and zero trades. Exit bar and R are functions
of the entry bar, side and the (shared) bars, so signal parity carries the trade parity there.

Verdict per strategy x tf: "exact" (no signal or trade differs), "explained" (every differing signal has a known
cause: an "S/R residual", a bar where study B's S/R-break plot and the replica disagree, the documented residual of
`sr_channel_breaks`; or, for the line-cross rule, a "line tie", where the two lines are equal within 1e-9 and float
summation order picks the side), "UNEXPLAINED" otherwise. A differing signal can shift later trades through
one-at-a-time, so "explained" rows may show more mismatched trades than differing signals. Exit status 1 when any
row is UNEXPLAINED.

    python scripts/replica_backtest_parity.py [--dir ~/tmp/tv] [--tf 15 60 240 1D]
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldbot.base import FrozenRecord  # noqa: E402
from goldbot.config import load_settings  # noqa: E402
from goldbot.features import smc_replica as R  # noqa: E402
from goldbot.labels.triple_barrier import BarrierSpec, one_at_a_time, triple_barrier  # noqa: E402

PULLS = {"15": "auth01_OANDA_XAUUSD_15", "60": "auth02_OANDA_XAUUSD_60", "240": "auth03_OANDA_XAUUSD_240",
         "1D": "auth04_OANDA_XAUUSD_1D"}
DAILY_FROM = "1921-01-01"                                                    # earlier daily rows are yearly/monthly


def _study_letter(n_plots: int) -> str:
    """A (structure overlay), B (S/R boxes), C (money flow), told apart by how many plots carry values."""
    return "a" if n_plots >= 25 else ("b" if n_plots >= 10 else "c")


def _load_pull(root: Path, tf: str) -> pd.DataFrame:
    bars = pd.read_parquet(root / f"bars_{PULLS[tf]}.parquet").set_index("bar_time")
    bars = bars[["open", "high", "low", "close", "volume"]]
    p = pd.read_parquet(root / f"plots_{PULLS[tf]}.parquet")
    used = p[p["value"].notna()].groupby("pine_id")["plot_id"].nunique()
    letter = {pid: _study_letter(int(n)) for pid, n in used.items()}
    p["col"] = p["pine_id"].map(letter) + "_" + p["plot_id"].str.replace("plot_", "p")
    wide = p.pivot_table(index="bar_time", columns="col", values="value", aggfunc="first", dropna=False)
    return bars.join(wide).reset_index()


Arr = np.ndarray
Holdout = tuple[pd.Timestamp, pd.Timestamp] | None

SPEC = BarrierSpec(target_atr=2.0, stop_atr=1.0, max_bars=48, name="replica_parity")
ATR_LEN = 14
R_TOL = 1e-9

# generic input name -> the chart's plot column (column names as built by _load_pull)
TV_EVENTS = {k: f"a_p{7 + i}" for i, k in enumerate(R.STRUCTURE_EVENTS)} | {
    "fvg_bull": "a_p21", "fvg_bear": "a_p22", "mfi_os": "c_p4", "mfi_ob": "c_p5",
    "ema10_x_up": "b_p4", "ema10_x_dn": "b_p6", "sr_up": "b_p2", "sr_dn": "b_p3"}
TV_LINES = {"sma10": "a_p27", "sma20": "a_p29"}
NO_SR_TF = {"1D"}


def tv_inputs(d: pd.DataFrame) -> dict[str, Arr]:
    """Strategy inputs read from the chart's plots (a missing column, e.g. B's boxes on 1D, reads as no event)."""
    out: dict[str, Arr] = {}
    for name, col in TV_EVENTS.items():
        v = d[col].to_numpy(dtype=float) if col in d else np.zeros(len(d))
        out[name] = np.nan_to_num(v).astype(bool)
    for name, col in TV_LINES.items():
        out[name] = d[col].to_numpy(dtype=float)
    return out


def replica_inputs(d: pd.DataFrame, tf: str) -> dict[str, Arr]:
    """The same inputs computed by goldbot.features.smc_replica on the same bars."""
    o, h, lo, c, v = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    st = R.structure_breaks(o, h, lo, c)
    out: dict[str, Arr] = {k: st[k] for k in R.STRUCTURE_EVENTS}
    out["fvg_bull"], out["fvg_bear"] = R.fair_value_gaps(o, h, lo, c)
    m = R.money_flow(h, lo, c, v, 12)
    m = np.where(np.isfinite(m), m, np.nan)
    out["mfi_os"], out["mfi_ob"] = R.cross_under(m, np.full(len(m), 20.0)), R.cross_over(m, np.full(len(m), 80.0))
    ema, sma_ema = R.ma_ribbon(c, "ema"), R.ma_ribbon(c, "sma_ema")
    out["ema10_x_up"], out["ema10_x_dn"] = R.cross_over(ema["ma10"], ema["ma20"]), R.cross_under(ema["ma10"], ema["ma20"])
    out["sma10"], out["sma20"] = sma_ema["ma10"], sma_ema["ma20"]
    if tf in NO_SR_TF:
        out["sr_up"] = out["sr_dn"] = np.zeros(len(d), dtype=bool)
    else:
        out["sr_up"], out["sr_dn"] = R.sr_channel_breaks(o, h, lo, c)
    return out


def trend_from_events(bull: Arr, bear: Arr) -> Arr:
    """+1 after a bullish break, -1 after a bearish one (bear wins on a shared bar), 0 before the first."""
    s = np.where(bear, -1.0, np.where(bull, 1.0, np.nan))
    return pd.Series(s).ffill().fillna(0.0).to_numpy().astype(np.int64)


Sides = tuple[Arr, Arr]
STRATEGIES: dict[str, Callable[[dict[str, Arr]], Sides]] = {
    "internal_choch": lambda x: (x["choch_bull_internal"], x["choch_bear_internal"]),
    "swing_bos": lambda x: (x["bos_bull_swing"], x["bos_bear_swing"]),
    "fvg_trend": lambda x: _fvg_trend(x),
    "mfi_cross": lambda x: (x["mfi_os"], x["mfi_ob"]),
    "ema_cross_b": lambda x: (x["ema10_x_up"], x["ema10_x_dn"]),
    "sma_cross_a": lambda x: (R.cross_over(x["sma10"], x["sma20"]), R.cross_under(x["sma10"], x["sma20"])),
    "sr_break": lambda x: (x["sr_up"], x["sr_dn"]),
}


def _fvg_trend(x: dict[str, Arr]) -> Sides:
    bull = x["bos_bull_internal"] | x["choch_bull_internal"]
    bear = x["bos_bear_internal"] | x["choch_bear_internal"]
    t = trend_from_events(bull, bear)
    return x["fvg_bull"] & (t > 0), x["fvg_bear"] & (t < 0)


def signals(sides: Sides, mask: Arr) -> pd.DataFrame:
    """(idx, side) per signal bar inside `mask`; a bar where both sides fire is no trade."""
    long_, short = (np.asarray(s, dtype=bool) & mask for s in sides)
    both = long_ & short
    idx = np.flatnonzero((long_ | short) & ~both)
    return pd.DataFrame({"idx": idx, "side": np.where(long_[idx], 1, -1)})


def trades(d: pd.DataFrame, sig: pd.DataFrame, holdout: Holdout) -> pd.DataFrame:
    """One-at-a-time ATR-barrier trades on the bars before the holdout start: t_entry, side, t_exit, r."""
    n = len(d) if holdout is None else int((d["bar_time"] < holdout[0]).sum())
    cols = ["t_entry", "side", "t_exit", "r"]
    sig = sig[sig["idx"] < n]
    if n < 2 or sig.empty:
        return pd.DataFrame(columns=cols)
    b = d.iloc[:n]
    o, h, lo, c = (b[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr = pd.Series(R.atr_seeded(h, lo, c, ATR_LEN))
    bars = pd.DataFrame({"ts_utc": b["bar_time"].to_numpy(), "bid_high": h, "bid_low": lo, "bid_close": c,
                         "ask_high": h, "ask_low": lo, "ask_close": c})
    lab = triple_barrier(bars, sig.reset_index(drop=True), SPEC, atr)
    if lab.empty:
        return pd.DataFrame(columns=cols)
    lab = one_at_a_time(lab)
    a = atr.to_numpy()[lab["idx"].to_numpy()]
    lab["r"] = lab["side"] * (lab["exit"] - lab["entry"]) / (SPEC.stop_atr * a)
    return lab[cols].reset_index(drop=True)


def compare_trades(a: pd.DataFrame, b: pd.DataFrame) -> tuple[int, float]:
    """(mismatched trades, sum of |R_A - R_B| over the union of trades keyed by entry bar and side; a trade on one
    side only counts its whole |R|). A shared key with another exit bar or R is one mismatch."""
    ka = {(int(e), int(s)): (int(x), float(r)) for e, s, x, r in a[["t_entry", "side", "t_exit", "r"]].to_numpy()}
    kb = {(int(e), int(s)): (int(x), float(r)) for e, s, x, r in b[["t_entry", "side", "t_exit", "r"]].to_numpy()}
    mism, adr = 0, 0.0
    for k in ka.keys() | kb.keys():
        xa, ra = ka.get(k, (-1, 0.0))
        xb, rb = kb.get(k, (-1, 0.0))
        dr = abs(ra - rb)
        if k not in ka or k not in kb or xa != xb or dr > R_TOL:
            mism += 1
        adr += dr
    return mism, adr


def _stats(t: pd.DataFrame) -> tuple[int, int, int, float]:
    r = t["r"].to_numpy(dtype=float)
    return len(r), int((r > 0).sum()), int((r < 0).sum()), float(r.sum())


def _stats_equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    sa, sb = _stats(a), _stats(b)
    return sa[:3] == sb[:3] and abs(sa[3] - sb[3]) <= R_TOL * max(1, sa[0])


def sr_disagreements(tv: dict[str, Arr], rep: dict[str, Arr], mask: Arr) -> list[tuple[int, str, str]]:
    """Bars where study B's S/R-break plot and the replica differ: (bar, "sr_up"/"sr_dn", "chart only"/"replica only")."""
    out = []
    for k in ("sr_up", "sr_dn"):
        for i in np.flatnonzero((tv[k] != rep[k]) & mask):
            out.append((int(i), k, "chart only" if tv[k][i] else "replica only"))
    return sorted(out)


SR_RESIDUAL = "S/R residual"
LINE_TIE = "line tie"
LINE_RULES = {"sma_cross_a"}
TIE_REL = 1e-9


def line_ties(a: Arr, b: Arr, rel: float = TIE_REL) -> Arr:
    """Bars where a cross of two plotted lines turns on float rounding: the lines are equal within `rel` (relative)
    on this bar or the previous one. The chart's line values match the replica's to ~1e-14, but where the true
    values tie (flat prices: SMA10 == SMA20) the two summation orders round to opposite signs."""
    with np.errstate(invalid="ignore"):
        tie = np.abs(a - b) <= rel * np.maximum(np.abs(b), 1e-12)
    return tie | np.concatenate(([False], tie[:-1])) if len(tie) else tie


class ParityRow(FrozenRecord):
    tf: str
    strategy: str
    signals_a: int
    signals_b: int
    signal_diffs: list[tuple[int, int, str, str]]   # (bar, side, "chart only"/"replica only", reason or "")
    trades_a: int
    trades_b: int
    mismatched: int
    abs_dr: float
    stats_equal: bool
    verdict: str


def _mask(d: pd.DataFrame, tf: str) -> Arr:
    if tf == "1D":
        return (d["bar_time"] >= pd.Timestamp(DAILY_FROM, tz="UTC")).to_numpy()
    return np.ones(len(d), dtype=bool)


def parity(d: pd.DataFrame, tf: str, holdout: Holdout) -> list[ParityRow]:
    d = d.reset_index(drop=True)
    mask = _mask(d, tf)
    tv, rep = tv_inputs(d), replica_inputs(d, tf)
    sr_bars = {b for b, _, _ in sr_disagreements(tv, rep, mask)}
    ties = set(np.flatnonzero(line_ties(tv["sma10"], tv["sma20"]) | line_ties(rep["sma10"], rep["sma20"])).tolist())
    rows = []
    for name, rule in STRATEGIES.items():
        if name == "sr_break" and tf in NO_SR_TF:
            continue
        sa, sb = signals(rule(tv), mask), signals(rule(rep), mask)
        ka, kb = set(zip(sa["idx"], sa["side"])), set(zip(sb["idx"], sb["side"]))
        why = {b: SR_RESIDUAL for b in sr_bars} | ({b: LINE_TIE for b in ties} if name in LINE_RULES else {})
        diffs = sorted([(int(i), int(s), "chart only", why.get(int(i), "")) for i, s in ka - kb]
                       + [(int(i), int(s), "replica only", why.get(int(i), "")) for i, s in kb - ka])
        ta, tb = trades(d, sa, holdout), trades(d, sb, holdout)
        mism, adr = compare_trades(ta, tb)
        if not diffs and mism == 0:
            verdict = "exact"
        elif all(reason for *_, reason in diffs):
            verdict = "explained"
        else:
            verdict = "UNEXPLAINED"
        rows.append(ParityRow(tf=tf, strategy=name, signals_a=len(sa), signals_b=len(sb), signal_diffs=diffs,
                              trades_a=len(ta), trades_b=len(tb), mismatched=mism, abs_dr=adr,
                              stats_equal=_stats_equal(ta, tb), verdict=verdict))
    return rows


def render(res: dict[str, list[ParityRow]]) -> str:
    """Parity columns only: no P&L, no ranking."""
    lines = [f"{'tf':<4} {'strategy':<15} {'sig A':>6} {'sig B':>6} {'sig diff':>8} {'trades A':>8} {'trades B':>8} "
             f"{'mismatched':>10} {'|dR| total':>10} {'stats eq':>8}  verdict"]
    for rows in res.values():
        for r in rows:
            lines.append(f"{r.tf:<4} {r.strategy:<15} {r.signals_a:>6} {r.signals_b:>6} {len(r.signal_diffs):>8} "
                         f"{r.trades_a:>8} {r.trades_b:>8} {r.mismatched:>10} {r.abs_dr:>10.3f} "
                         f"{'yes' if r.stats_equal else 'NO':>8}  {r.verdict}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--dir", default=str(Path.home() / "tmp" / "tv"))
    ap.add_argument("--tf", nargs="*", default=list(PULLS))
    args = ap.parse_args()
    root = Path(args.dir).expanduser()
    holdout = load_settings().research.holdout_window()
    res: dict[str, list[ParityRow]] = {}
    sr_lines: list[str] = []
    diff_lines: list[str] = []
    for tf in args.tf:
        d = _load_pull(root, tf).reset_index(drop=True)
        bt = d["bar_time"]
        res[tf] = parity(d, tf, holdout)
        if tf not in NO_SR_TF:
            sr_lines += [f"  {tf:<4} {bt.iloc[i]}  {k}  {w}"
                         for i, k, w in sr_disagreements(tv_inputs(d), replica_inputs(d, tf), _mask(d, tf))]
        diff_lines += [f"  {tf:<4} {bt.iloc[i]}  {r.strategy} {'long' if s > 0 else 'short'}  {w}"
                       + (f"  ({why})" if why else "  (no known cause)") for r in res[tf] for i, s, w, why in r.signal_diffs]
    print(f"trades on bars before the holdout start {holdout[0].date() if holdout else '-'}; "
          "holdout signals compared as signals only\n")
    print(render(res))
    print(f"\nStudy B S/R-break events where chart and replica differ ({len(sr_lines)}):")
    print("\n".join(sr_lines))
    print(f"\nDiffering strategy signals ({len(diff_lines)}):")
    print("\n".join(diff_lines))
    return int(any(r.verdict == "UNEXPLAINED" for rows in res.values() for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
