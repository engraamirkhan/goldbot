"""Adaptive targets/exits on the owner's box break-and-close, DISCOVERY HALF ONLY (2010-01-01 .. 2017-12-31).
Read-only, no trial. Feeds docs/research/exits-adaptive-targets-2026-10-26.md.

Entries are fixed: the frozen roster rule (`box_break_close.decide_with_stop`, constants unchanged) on 15m/30m/1h/4h
mid bars built from Dukascopy 1m; one position at a time per timeframe under the roster exit (E0), and that same
entry set is then managed by every exit below, so a difference between exits is the exit alone. R = 1.5 x ATR(14) of
the decision bars at the signal bar (the roster's label stop), frozen for the trade. Bars are walked stop-first
(a bar touching the stop in force and anything favourable is a stop). The Friday rule of the live engine
(`runner._weekend_rule`, 21:30 Europe/Athens) is applied to every exit: losers closed at that bar's close, winners'
stop raised to lock half the open profit.

Exits (all fixed before any outcome was read; one pass; no parameter changed after results):
  E0  roster: target 3.0 ATR (2 R), time exit HOLD_BARS
  E1  next box: target = the next box beyond the close (`box_course.next_box` on the boxes in force at t), floored
      at 2 R; no next box -> 2 R
  E2  next box floored at 2 R and capped at 4 R
  E3  runner, whole position (V1u form): no target, trail 2.5 ATR behind the best close-side extreme, armed at +1 R,
      time exit
  E4  E3 + break-even: stop to entry at +1 R
  E5  partial (needs >= 0.02 lot): 50% at E1's target, rest E4
  E6  slice-G PT: 50% at +1 R, rest stop to entry then 1.5 ATR chandelier
  E7  E1 with break-even at +1 R (all-or-nothing next-box target, the 0.01-lot form of E5)
  S1  structural stop (the broken box's far side + 0.1 ATR, `risk_usd`), next box as target, skipped when the next
      box is under 2 R (the course's K6)
  S2  structural stop, target max(next box, 2 R)
  S3  structural stop, fixed 2 R: the geometry the exploration arms S4/I2A/I2B/MB15/MB30/MB1H/W2 trade
      (`research.arm_rules.box_break_close_rule` passes `risk_usd` as R)
  MX  diagnostic only: no target, no trail, no Friday rule; its MFE is the uncapped excursion before stop or time
Costs per trade in R: A = $1.07/oz flat (research basis); M = $0.23/oz + $0.70 overshoot on the stopped share
(initial, BE or trail stop); MT = M at today's ATR (15m 8.01, 30m 11.6, 1h 17.73, 4h 36.61, arms-2026-10-25.md).

Usage: uv run python scripts/research/exits_adaptive_discovery.py --raw ~/tmp/ts/raw --out ~/tmp/exits_adaptive
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.features import smc_replica as smc
from goldbot.features.box_course import next_box
from goldbot.features.technical import atr
from goldbot.specialists.box_break_close import HOLD_BARS, MFI_WINDOW, decide_with_stop

YEARS = range(2010, 2018)
RULES = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
TODAY_ATR = {"15m": 8.01, "30m": 11.6, "1h": 17.73, "4h": 36.61}
STOP_ATR, R_TARGET = 1.5, 2.0
COST_A, COST_M, OVERSHOOT = 1.07, 0.23, 0.70
EXITS = ("E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "S1", "S2", "S3", "MX")


def load(raw: Path) -> pd.DataFrame:
    parts = [pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                             columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
                                      "ask_low", "ask_close", "tick_count"]) for y in YEARS]
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d[d["ts_utc"] < pd.Timestamp("2018-01-01", tz="UTC")].sort_values("ts_utc").drop_duplicates("ts_utc")
    for k in ("open", "high", "low", "close"):
        d[k] = (d[f"bid_{k}"] + d[f"ask_{k}"]) / 2
    assert d["ts_utc"].max() < pd.Timestamp("2018-01-01", tz="UTC")
    return d[["ts_utc", "open", "high", "low", "close", "tick_count"]].set_index("ts_utc")


def bars(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = m1.resample(rule, label="left", closed="left")
    b = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "tick_count": g["tick_count"].sum(), "n": g["close"].count()})
    return b[b["n"] > 0].dropna(subset=["close"]).drop(columns="n")


def friday_cut(idx: pd.DatetimeIndex, rule: str) -> np.ndarray:
    """True on the first bar per week whose CLOSE is at or after Friday 21:30 Europe/Athens (the live weekend rule)."""
    close = (idx + pd.Timedelta(rule)).tz_convert("Europe/Athens")
    after = np.asarray(((close.weekday == 4) & (close.hour * 60 + close.minute >= 21 * 60 + 30))
                       | (close.weekday == 5))
    iso = close.isocalendar()
    wk = iso["year"].to_numpy().astype(int) * 100 + iso["week"].to_numpy().astype(int)
    out = np.zeros(len(idx), dtype=bool)
    pos = np.flatnonzero(after)
    if len(pos):
        _, first = np.unique(wk[pos], return_index=True)
        out[pos[first]] = True
    return out


def signals(b: pd.DataFrame) -> pd.DataFrame:
    """The roster rule, exactly as `BoxBreakCloseSpecialist.candidates_with_risk`, plus the next box at t."""
    bb = b.reset_index()          # keeps ts_utc as a column (the replica reads it)
    p = smc.DEFAULT_PARAMS
    frame = smc.replica_frame(bb, p)
    trace: list[tuple[np.ndarray, np.ndarray]] = []
    o, h, lo, c = (bb[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    smc.sr_channel_breaks(o, h, lo, c, p.sr_pivot_len, p.sr_width_pct, p.sr_max_channels, p.sr_lookback,
                          p.sr_range_window, trace)
    mfi = smc.money_flow(h, lo, c, bb["tick_count"].to_numpy(float), p.mfi_len)
    a = atr(bb, 14).to_numpy(float)
    trend = frame["trend_swing"].to_numpy(float)
    f1, f2 = frame["range_f1_dist_atr"].to_numpy(float), frame["range_f2_dist_atr"].to_numpy(float)
    rows = []
    for t in range(MFI_WINDOW, len(bb)):
        tops, bots = trace[t]
        if not len(tops):
            continue
        side, risk = decide_with_stop(t, o, c, mfi, a, tops, bots, trend[t], f1[t], f2[t])
        if side:
            floor = float(c[t]) + side * R_TARGET * STOP_ATR * a[t]          # the first box at >= 2 R (E8)
            rows.append((t, side, risk, next_box(tops, bots, float(c[t]), side), next_box(tops, bots, floor, side)))
    return pd.DataFrame(rows, columns=["idx", "side", "struct_risk", "next_box", "next_box_2r"])


def walk(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, fri: np.ndarray, t: int, side: int, a: float,
         hold: int, ex: str, nb: float, r: float | None = None) -> dict:
    """One trade under exit `ex`. Returns R (gross), stopped share, bars held, MFE/MAE in R, exit kind. `r` overrides
    the 1.5 ATR risk (S1/S2: the structural stop distance)."""
    e = c[t]
    r = STOP_ATR * a if r is None else r
    stop = e - side * r
    tgt_r = None
    if ex in ("E0", "S3"):
        tgt_r = R_TARGET
    elif ex in ("E1", "E5", "E7", "S1", "S2"):
        d = side * (nb - e) / r if np.isfinite(nb) else np.nan
        tgt_r = max(R_TARGET, d) if np.isfinite(d) else R_TARGET
    elif ex == "E2":
        d = side * (nb - e) / r if np.isfinite(nb) else np.nan
        tgt_r = min(4.0, max(R_TARGET, d)) if np.isfinite(d) else R_TARGET
    elif ex == "E8":
        tgt_r = side * (nb - e) / r if np.isfinite(nb) else R_TARGET       # nb is next_box_2r for E8
    part_r = {"E5": tgt_r, "E6": 1.0}.get(ex)
    trail_atr = {"E3": 2.5, "E4": 2.5, "E5": 2.5, "E6": 1.5}.get(ex)
    be = ex in ("E4", "E5", "E6", "E7")
    final_tgt = None if ex in ("E3", "E4", "E5", "E6", "MX") else tgt_r
    rem, booked, stopped_share = 1.0, 0.0, 0.0
    best = e
    armed = False
    mfe = mae = 0.0
    kind = "time"
    n = len(c)
    last = min(t + hold, n - 1)
    j = t
    for j in range(t + 1, last + 1):
        fav = h[j] if side > 0 else lo[j]
        adv = lo[j] if side > 0 else h[j]
        # 1. stop in force since the last close
        if side * (adv - stop) <= 0:
            px = stop if side * (o[j] - stop) > 0 else o[j]           # a gap through the stop fills at the open
            booked += rem * side * (px - e) / r
            stopped_share += rem
            mae = min(mae, side * (adv - e) / r)
            kind = "stop" if side * (stop - (e - side * r)) <= 1e-12 else ("be" if abs(stop - e) < 1e-9 else "trail")
            rem = 0.0
            break
        mfe = max(mfe, side * (fav - e) / r)
        mae = min(mae, side * (adv - e) / r)
        # 2. partial
        if part_r is not None and rem == 1.0 and side * (fav - e) / r >= part_r:
            booked += 0.5 * part_r
            rem = 0.5
        # 3. target
        if final_tgt is not None and side * (fav - e) / r >= final_tgt:
            booked += rem * final_tgt
            rem = 0.0
            kind = "target"
            break
        # 4. break-even and trail (effective from the next bar)
        best = max(best, fav) if side > 0 else min(best, fav)
        if side * (best - e) / r >= 1.0:
            armed = True
        if armed and be:
            stop = max(stop, e) if side > 0 else min(stop, e)
        if armed and trail_atr is not None:
            ts = best - side * trail_atr * a
            stop = max(stop, ts) if side > 0 else min(stop, ts)
        # 5. Friday rule at this bar's close
        if fri[j] and ex != "MX":
            if side * (c[j] - e) <= 0:
                booked += rem * side * (c[j] - e) / r
                rem = 0.0
                kind = "friday"
                break
            lock = e + 0.5 * (c[j] - e)
            stop = max(stop, lock) if side > 0 else min(stop, lock)
    if rem > 0:
        booked += rem * side * (c[j] - e) / r
        kind = "time" if kind not in ("target",) else kind
    return {"R": booked, "stopped": stopped_share, "bars": j - t, "mfe": mfe, "mae": mae, "kind": kind}


def run_tf(args: tuple[str, str]) -> pd.DataFrame:
    tf, raw = args
    m1 = load(Path(raw))
    b = bars(m1, RULES[tf])
    sig = signals(b)
    o, h, lo, c = (b[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = atr(b.reset_index(drop=True), 14).to_numpy(float)
    fri = friday_cut(b.index, RULES[tf])
    hold = HOLD_BARS[tf]
    # one position at a time under E0 (the roster exit); that entry set is then managed by every exit
    keep, free_at = [], -1
    for row in sig.itertuples():
        if row.idx <= free_at:
            continue
        w = walk(o, h, lo, c, fri, row.idx, row.side, a[row.idx], hold, "E0", row.next_box)
        keep.append(row)
        free_at = row.idx + w["bars"]
    out = []
    for row in keep:
        t, side, nb = row.idx, row.side, row.next_box
        r_usd = STOP_ATR * a[t]
        nb_r = side * (nb - c[t]) / r_usd if np.isfinite(nb) else np.nan
        nb2 = row.next_box_2r
        base = {"tf": tf, "ts": b.index[t], "side": side, "r_usd": r_usd, "atr": a[t], "next_box_r": nb_r,
                "next_box_2r_r": side * (nb2 - c[t]) / r_usd if np.isfinite(nb2) else np.nan,
                "struct_risk_r": row.struct_risk / r_usd}
        for ex in EXITS:
            r_ex = None
            if ex in ("S1", "S2", "S3"):
                r_ex = row.struct_risk
                nb_s = side * (nb - c[t]) / r_ex if np.isfinite(nb) else np.nan
                if ex == "S1" and not (np.isfinite(nb_s) and nb_s >= R_TARGET):
                    continue                                   # course K6: no next box at >= 2 R, no trade
            w = walk(o, h, lo, c, fri, t, side, a[t], hold, ex, row.next_box_2r if ex == "E8" else nb, r_ex)
            out.append({**base, "exit": ex, **w, "r_usd": r_ex if r_ex is not None else r_usd})
    return pd.DataFrame(out)


def clustered_t(x: np.ndarray, days: np.ndarray) -> float:
    s = pd.Series(x).groupby(days).sum()
    cnt = pd.Series(np.ones_like(x)).groupby(days).sum()
    m = x.mean()
    resid = s - m * cnt
    g = len(s)
    se = np.sqrt((resid ** 2).sum() * g / (g - 1)) / len(x)
    return float(m / se) if se > 0 else float("nan")


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["netA"] = df["R"] - COST_A / df["r_usd"]
    df["netM"] = df["R"] - (COST_M + OVERSHOOT * df["stopped"]) / df["r_usd"]
    today = df["tf"].map(TODAY_ATR) * df["r_usd"] / df["atr"]       # the same stop in ATR at today's ATR
    df["netMT"] = df["R"] - (COST_M + OVERSHOOT * df["stopped"]) / today
    df["day"] = df["ts"].dt.tz_convert("America/New_York").dt.date
    df["year"] = df["ts"].dt.year
    rows = []
    for (tf, ex), g in list(df.groupby(["tf", "exit"])) + [(("pooled", ex), g) for ex, g in df.groupby("exit")]:
        yr = g.groupby("year")["R"].mean()
        yrm = g.groupby("year")["netM"].mean()
        rows.append({"tf": tf, "exit": ex, "n": len(g), "gross": g["R"].mean(),
                     "t": clustered_t(g["R"].to_numpy(), g["day"].to_numpy()),
                     "netA": g["netA"].mean(), "netM": g["netM"].mean(), "netMT": g["netMT"].mean(),
                     "yrs_gross_pos": int((yr > 0).sum()), "yrs_netM_pos": int((yrm > 0).sum()),
                     "p_ge2": (g["R"] >= 2 - 1e-9).mean(), "p_ge3": (g["R"] >= 3 - 1e-9).mean(),
                     "p_le_m1": (g["R"] <= -1 + 1e-9).mean(), "bars": g["bars"].mean(),
                     "stopped": g["stopped"].mean(), "median_r_usd": g["r_usd"].median()})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(4) as ex:
        parts = list(ex.map(run_tf, [(tf, str(a.raw)) for tf in RULES]))
    df = pd.concat(parts, ignore_index=True)
    df.to_parquet(a.out / "trades.parquet")
    mx = df[df["exit"] == "MX"]
    df = df[df["exit"] != "MX"]
    s = summarise(df)
    s.to_csv(a.out / "summary.csv", index=False)
    e0 = df[df["exit"] == "E0"].copy()
    e0["mfe_uncapped"] = mx["mfe"].to_numpy()
    e0["nb_struct_r"] = e0["next_box_r"] / e0["struct_risk_r"]
    nb = e0.groupby("tf").agg(n=("next_box_r", "size"), has_next=("next_box_r", lambda x: x.notna().mean()),
                              nb_r_p25=("next_box_r", lambda x: x.quantile(0.25)),
                              nb_r_med=("next_box_r", "median"), nb_r_p75=("next_box_r", lambda x: x.quantile(0.75)),
                              ge2=("next_box_r", lambda x: (x >= 2).mean()),
                              mfe_med=("mfe_uncapped", "median"),
                              p_mfe1=("mfe_uncapped", lambda x: (x >= 1).mean()),
                              p_mfe2=("mfe_uncapped", lambda x: (x >= 2).mean()),
                              p_mfe3=("mfe_uncapped", lambda x: (x >= 3).mean()),
                              p_mfe4=("mfe_uncapped", lambda x: (x >= 4).mean()),
                              nb2_has=("next_box_2r_r", lambda x: x.notna().mean()),
                              nb2_med=("next_box_2r_r", "median"),
                              nbs_med=("nb_struct_r", "median"), nbs_ge2=("nb_struct_r", lambda x: (x >= 2).mean()),
                              r_usd_med=("r_usd", "median"), struct_med=("struct_risk_r", "median"))
    nb.to_csv(a.out / "next_box.csv")
    # MFE reach of the next box (exit-independent, E3 has no target so its MFE is the full path to stop/trail/time)
    e1 = df[df["exit"] == "E1"].copy()
    kinds = df.groupby(["tf", "exit"])["kind"].value_counts(normalize=True).unstack(fill_value=0)
    kinds.to_csv(a.out / "exit_kinds.csv")
    print(s.to_string(float_format=lambda v: f"{v:.3f}"))
    print(nb.to_string(float_format=lambda v: f"{v:.3f}"))
    print(kinds.to_string(float_format=lambda v: f"{v:.3f}"))
    # paired differences against E0 on the same entries (day-clustered t)
    d = df[df["exit"].str.startswith("E")].pivot_table(index=["tf", "ts"], columns="exit", values="R").reset_index()
    d["day"] = pd.to_datetime(d["ts"]).dt.tz_convert("America/New_York").dt.date
    prs = []
    for tf, g in list(d.groupby("tf")) + [("pooled", d)]:
        for ex in ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8"):
            x = (g[ex] - g["E0"]).to_numpy()
            prs.append({"tf": tf, "exit": ex, "diff_vs_E0": x.mean(), "t": clustered_t(x, g["day"].to_numpy())})
    prs_df = pd.DataFrame(prs)
    prs_df.to_csv(a.out / "paired.csv", index=False)
    print(prs_df.to_string(float_format=lambda v: f"{v:.3f}"))
    (a.out / "meta.json").write_text(json.dumps({"years": list(YEARS), "n_trades": int(len(e0)),
                                                  "n_e1": int(len(e1))}))


if __name__ == "__main__":
    main()
