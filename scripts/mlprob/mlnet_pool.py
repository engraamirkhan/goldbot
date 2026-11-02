"""MLNET candidate pool + point-in-time features (docs/research/mlprob/mlnet-prereg.md sections 2-3).

    .venv/bin/python scripts/mlprob/mlnet_pool.py --window explore
    .venv/bin/python scripts/mlprob/mlnet_pool.py --window confirm       (needs the explore pool for history)
    .venv/bin/python scripts/mlprob/mlnet_pool.py --window old2004       (TEST-OLD; old2006, old2008 likewise)

Simulates the 11 gross-positive sprint-200 trend rules with the trend lane's engine (`mlnet_engine.py`), prices every
trade and its 4 leak-free twins at the cost of record, and writes one row per trade with the 33 features to
~/tmp/mlprob_mlnet/pool_<window>.parquet (trade rows stay on the Mac: never committed).
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mlnet_engine as E  # noqa: E402

from goldbot.data.store import asof_join  # noqa: E402
from goldbot.labels import SwapSpec  # noqa: E402
from goldbot.research import arms as A  # noqa: E402

RULES = [1, 3, 4, 10, 15, 19, 20, 24, 28, 31, 32]
SIDS = [f"S200-TREND-{i:02d}" for i in RULES]
OUT = Path.home() / "tmp" / "mlprob_mlnet"
REF_PX = 4000.0
FEATURES = (["side", "cost_R_ex", "log_stop_atrd", "long_swap_days", "hour_sin", "hour_cos", "weekday", "session",
             "d1_align", "ext50", "ret1", "ret5", "ret20", "atrd_pct", "vol_ratio_1h", "adx4", "chan4", "dbreak",
             "cons_same", "cons_opp", "rule_mom", "pool_mom"])        # + 11 rule one-hots (ridge) / rule (boosting)


# ---------------------------------------------------------------- simulation (workers)
_CTX: Any = None


def _init(raw: str, window: str) -> None:
    global _CTX
    _CTX = E.build_ctx(Path(raw), window)


def _bps(ctx: Any, df: pd.DataFrame) -> pd.DataFrame:
    """W2 bps cost version: round trip, overshoot and swap as (dollar figure / 4,000) x the entry price."""
    if df.empty:
        return df
    px = ctx.o[np.searchsorted(ctx.t, df.t_fill.to_numpy(), side="left")]
    df["px"] = px
    df["R_bps"] = (df.gross + (df.swap - df.cost) * px / REF_PX) / df.risk
    return df


def _drift(ctx: Any, sid: str, spec: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DRIFT control (W2, reported only): same side, stop, target, trail and hold, entered at the same UTC time of day
    on a random other trading day within +/-20 trading days (deterministic hash)."""
    t = ctx.t
    tdays = np.unique(ctx.tday)
    sigd = spec.sig.drop_duplicates("dec").set_index("dec")
    flags: dict[int, np.ndarray] | None = None
    if spec.exit_flags is not None:
        fd, f_long, f_short = spec.exit_flags
        flags = {1: fd[np.asarray(f_long, dtype=bool)], -1: fd[np.asarray(f_short, dtype=bool)]}
    out = []
    big = np.iinfo(np.int64).max
    for tr in rows:
        r = sigd.loc[tr["dec"]]
        k0 = int(np.searchsorted(t, tr["t_fill"], side="left"))
        i = int(np.searchsorted(tdays, ctx.tday[k0]))
        off = int(E.h_u(sid, tr["id"], "drift") * 40) - 20
        off += int(off >= 0)                                    # -20..-1, 1..20
        j = i + off
        if j < 0 or j >= len(tdays):
            continue
        te = int(tr["t_fill"]) + int(tdays[j] - tdays[i]) * E.DAYNS
        kk = int(np.searchsorted(t, te, side="left"))
        if kk >= len(t) - 1 or int(t[kk]) - te > 30 * E.MIN:
            continue
        cap = int(r.cap)
        hold = (cap - int(tr["t_fill"])) if cap < big else None
        fl = flags[tr["side"]] if flags is not None else None
        tw = E._twin(ctx, kk, r, int(tr["side"]), float(tr["risk"]), hold, fl, tr["id"])
        if tw is not None:
            out.append(tw)
    return out


def _sim(sid: str) -> dict[str, Any]:
    ctx = _CTX
    costs = A.load_costs(E.ROOT / "config" / "arm_costs.yaml")
    swap = SwapSpec.shipped()
    spec = E.STRATS[sid](ctx)
    sig = spec.sig[(spec.sig.dec >= ctx.dec_from - E.DAYNS) & (spec.sig.dec < ctx.end)]     # 24 h for consensus
    res = E.simulate(ctx, sid, spec)
    real = _bps(ctx, E.price(res["real"], costs, swap))
    tw = _bps(ctx, E.price(res["twins"], costs, swap))
    dr = _bps(ctx, E.price(_drift(ctx, sid, spec, res["real"]), costs, swap))
    if not real.empty:
        rt, os_ = costs.screen_charges(E.BROKER, E.DEFAULT_SESSIONS.session_label(
            pd.DatetimeIndex(pd.to_datetime(real.t_fill.to_numpy(), utc=True))), E.COST_VERSION)
        real["cost_R_ex"] = (rt + os_) / real.risk
        for d, pre in ((tw, "twin"), (dr, "drift")):
            if d.empty:
                continue
            g = d.groupby("id").agg(**{f"{pre}_R": ("R", "mean"), f"{pre}_R_stress": ("R_stress", "mean"),
                                       f"{pre}_R_bps": ("R_bps", "mean"), f"{pre}_gross_R": ("gross_R", "mean"),
                                       f"n_{pre}": ("R", "size")})
            real = real.merge(g, left_on="id", right_index=True, how="left")
        real["rule"] = sid
    return {"sid": sid, "real": real, "sig": sig[["dec", "side"]].assign(rule=sid)}


# ---------------------------------------------------------------- features (main process)
def bar_tables(ctx: Any) -> list[pd.DataFrame]:
    """Per-timeframe feature tables keyed by available_utc = the bar's decision (close) time."""
    d = ctx.bars["1d"]
    c = d.s("c")
    atrd = E.atr(d, 20)
    e50 = E.ema(c, 50)
    up = (c > e50) & (e50 > e50.shift(10))
    dn = (c < e50) & (e50 < e50.shift(10))
    t1 = pd.DataFrame({
        "available_utc": pd.to_datetime(d.dec, utc=True), "atrd": atrd, "d1_state": np.where(up, 1, np.where(dn, -1, 0)),
        "ext50_raw": (c - e50) / atrd, "ret1_raw": (c - c.shift(1)) / atrd, "ret5_raw": (c - c.shift(5)) / atrd,
        "ret20_raw": (c - c.shift(20)) / atrd,
        "atrd_pct": atrd.rolling(250, min_periods=60).rank(pct=True), "d_hi": d.h, "d_lo": d.lo})
    h = ctx.bars["1h"]
    t2 = pd.DataFrame({"available_utc": pd.to_datetime(h.dec, utc=True), "vol_ratio_1h": E.atr(h, 5) / E.atr(h, 50),
                       "c1h": h.c})
    b4 = ctx.bars["4h"]
    ax, _, _ = E.adx(b4, 14)
    hi20, lo20 = b4.s("h").rolling(20).max(), b4.s("lo").rolling(20).min()
    rng = (hi20 - lo20).replace(0, np.nan)
    t3 = pd.DataFrame({"available_utc": pd.to_datetime(b4.dec, utc=True), "adx4": ax,
                       "chan4_raw": 2 * (b4.s("c") - lo20) / rng - 1})
    return [t1.dropna(subset=["atrd"]), t2, t3]


def momentum(trades: pd.DataFrame, at: np.ndarray, rule: np.ndarray | None, k: int, min_n: int) -> np.ndarray:
    """Mean net R of the last k trades (of the same rule if `rule` is given) with exit time < at; NaN if < min_n."""
    out = np.full(len(at), np.nan)
    groups = [(None, np.ones(len(at), bool))] if rule is None else [(r, rule == r) for r in np.unique(rule)]
    for r, mask in groups:
        h = trades if r is None else trades[trades.rule == r]
        h = h.sort_values("t_exit")
        tx, cs = h.t_exit.to_numpy(), np.r_[0.0, np.cumsum(h.R.to_numpy())]
        j = np.searchsorted(tx, at[mask], side="left")             # trades with exit < at
        lo = np.maximum(j - k, 0)
        n = j - lo
        val = np.where(n >= min_n, (cs[j] - cs[lo]) / np.maximum(n, 1), np.nan)
        out[mask] = val
    return out


def consensus(sig: pd.DataFrame, dec: np.ndarray, side: np.ndarray, rule: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    same, opp = np.zeros(len(dec)), np.zeros(len(dec))
    for r in np.unique(rule):
        m = rule == r
        o = sig[sig.rule != r]
        for s in (1, -1):
            t = np.sort(o.dec[o.side == s].to_numpy())
            cnt = np.searchsorted(t, dec[m], side="right") - np.searchsorted(t, dec[m] - 24 * E.HOUR, side="right")
            same[m] += np.where(side[m] == s, cnt, 0)
            opp[m] += np.where(side[m] != s, cnt, 0)
    return same, opp


def features(ctx: Any, real: pd.DataFrame, sig: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    df = real.copy()
    df["ts_utc"] = pd.to_datetime(df.dec.to_numpy(), utc=True)
    for tb in bar_tables(ctx):
        df = asof_join(df, tb, on="ts_utc")
    df = df.sort_values(["dec", "rule"], kind="stable").reset_index(drop=True)
    s = df.side.to_numpy()
    df["log_stop_atrd"] = np.log(df.risk / df.atrd)
    df["long_swap_days"] = np.where(s > 0, np.minimum((df.t_end - df.dec) / E.DAYNS, 10.0), 0.0)
    hr = pd.DatetimeIndex(df.ts_utc).hour.to_numpy() + pd.DatetimeIndex(df.ts_utc).minute.to_numpy() / 60
    df["hour_sin"], df["hour_cos"] = np.sin(2 * np.pi * hr / 24), np.cos(2 * np.pi * hr / 24)
    df["weekday"] = pd.DatetimeIndex(df.ts_utc).weekday
    code, _ = E.DEFAULT_SESSIONS.session_windows(pd.DatetimeIndex(df.ts_utc))
    df["session"] = code
    df["d1_align"] = s * df.d1_state
    for k in ("ext50", "ret1", "ret5", "ret20", "chan4"):
        df[k] = s * df[f"{k}_raw"]
    df["dbreak"] = np.where(s > 0, df.c1h - df.d_hi, df.d_lo - df.c1h) / df.atrd
    df["cons_same"], df["cons_opp"] = consensus(sig, df.dec.to_numpy(), s, df.rule.to_numpy())
    hist = pd.concat([history[["rule", "t_exit", "R"]], df[["rule", "t_exit", "R"]]], ignore_index=True)
    hist = hist.drop_duplicates()
    df["rule_mom"] = momentum(hist, df.dec.to_numpy(), df.rule.to_numpy(), 20, 5)
    df["pool_mom"] = momentum(hist, df.dec.to_numpy(), None, 50, 10)
    df["tday"] = ((pd.DatetimeIndex(df.ts_utc).tz_convert(E.NYZ).tz_localize(None) + pd.Timedelta(hours=7))
                  .normalize())
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=E.ROOT.parents[2] / "raw" if (E.ROOT.parents[2] / "raw").exists()
                    else E.ROOT / "raw")
    ap.add_argument("--window", choices=list(E.WINDOWS), required=True)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    assert a.workers <= 2, "the shared Mac: at most 2 workers per lane"
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(str(a.raw), a.window)) as ex:
        res = list(ex.map(_sim, SIDS))
    real = pd.concat([r["real"] for r in res if not r["real"].empty], ignore_index=True)
    sig = pd.concat([r["sig"] for r in res], ignore_index=True)
    history = pd.DataFrame(columns=["rule", "t_exit", "R"])
    if a.window == "confirm":
        history = pd.read_parquet(OUT / "pool_explore.parquet", columns=["rule", "t_exit", "R"])
        history = history[history.t_exit < pd.Timestamp("2018-01-01", tz="UTC").value]
    ctx = E.build_ctx(a.raw, a.window)
    df = features(ctx, real, sig, history)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / f"pool_{a.window}.parquet")
    print(a.window, "trades", len(df), "per rule", df.rule.value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
