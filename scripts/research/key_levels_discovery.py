"""Slice G (key levels) measurements on gold, DISCOVERY HALF ONLY (2010-01-01 .. 2017-12-31). Read-only, no trial.

Only the 2010-2017 yearly files are opened (README 4.1). Levels are known only after their period closes (previous-week
high is fixed at Friday 17:00 New York; no look-ahead). For every level and trading window the FIRST touch on 15m/1h/4h
bars is classified and traded under three rules (break-close, fade-of-reject/sweep, resting-limit retest), each with a
fixed 2R exit and a partial-at-1R + trail exit. Every real level is compared with randomised control levels at the same
distance from the same reference price, same window and same rule (day-block bootstrap of real - control).

Usage: uv run python scripts/research/key_levels_discovery.py --raw ~/tmp/ts/raw --out ~/tmp/key_levels
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns

YEARS = range(2010, 2018)            # discovery half; no other file is opened
NY, LDN, TKO = "America/New_York", "Europe/London", "Asia/Tokyo"
H1 = 3_600_000_000_000
TIME_BARS, TARGET_R, REPS = 32, 2.0, 6
PRIOR_FIXED, MEAS_USD, TODAY_ATR15 = 0.37, 0.16, 8.0   # research prior add-on; measured spread 0.10 + 0.06 commission
SESS = {0: "asia", 1: "london", 2: "ny"}


def load(raw: Path) -> pd.DataFrame:
    parts = [pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet",
                             columns=["ts_utc", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
                                      "ask_low", "ask_close", "tick_count"]) for y in YEARS]
    d = pd.concat(parts, ignore_index=True)
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True).astype("datetime64[ns, UTC]")
    d = d[d["ts_utc"] < pd.Timestamp("2018-01-01", tz="UTC")].sort_values("ts_utc").drop_duplicates("ts_utc")
    for k in ("open", "high", "low", "close"):
        d[k] = (d[f"bid_{k}"] + d[f"ask_{k}"]) / 2
    d["spread"] = d["ask_close"] - d["bid_close"]
    assert d["ts_utc"].max() < pd.Timestamp("2018-01-01", tz="UTC")
    return d[["ts_utc", "open", "high", "low", "close", "tick_count", "spread"]].set_index("ts_utc")


def trading_day(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return (idx.tz_convert(NY) + pd.Timedelta(hours=7)).normalize().tz_localize(None)


def local_ns(dates: pd.DatetimeIndex, tz: str, hh: int, mm: int, off: int = 0) -> np.ndarray:
    loc = pd.DatetimeIndex([pd.Timestamp(d + pd.Timedelta(days=off)).replace(hour=hh, minute=mm) for d in dates])
    return epoch_ns(loc.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward").tz_convert("UTC"))


def bars(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = m1.resample(rule, label="left", closed="left")
    b = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "spread": g["spread"].mean(), "n": g["close"].count()})
    return b[b["n"] >= (10 if rule == "15min" else 1)].dropna(subset=["close"])


def wilder_atr(b: pd.DataFrame, n: int = 14) -> np.ndarray:
    pc = b["close"].shift()
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().to_numpy()


# ---------------------------------------------------------------- levels (all causal)
def build_levels(m1: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (days table, levels table). A level row: day k, group, value, t_avail_ns, t_end_ns."""
    td = trading_day(m1.index)
    g = m1.groupby(td)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "n": g["close"].size(),
                      "t0": pd.Series(epoch_ns(m1.index), index=td).groupby(level=0).first()})
    d = d[(d.n > 600) & (d.index.dayofweek < 5)].copy()
    days = pd.DatetimeIndex(d.index)
    d["range"] = d["high"] - d["low"]
    d["fc"] = d["range"].rolling(20).mean().shift()                    # causal range forecast (20-day mean range)
    ema = d["close"].ewm(span=20, adjust=False).mean()
    d["trend"] = np.sign(d["close"] - ema).shift()                      # prior day's close vs EMA20: known at the open
    d["end_ns"] = local_ns(days, NY, 17, 0)
    tok, lon = local_ns(days, TKO, 8, 0), local_ns(days, LDN, 8, 0)
    nyo = local_ns(days, NY, 8, 30)
    m_ns = epoch_ns(m1.index)
    hi, lo, op, cl = (m1[c].to_numpy() for c in ("high", "low", "open", "close"))

    def agg(a: int, b: int) -> tuple[float, float, float, float] | None:
        i, j = np.searchsorted(m_ns, a), np.searchsorted(m_ns, b)
        if j - i < 20:
            return None
        return op[i], hi[i:j].max(), lo[i:j].min(), cl[j - 1]

    rows: list[tuple] = []
    h1 = H1

    def add(k: int, grp: str, val: float, avail: int, endk: int) -> None:
        if np.isfinite(val):
            rows.append((k, grp, float(val), int(avail), int(d["end_ns"].iloc[min(endk, len(d) - 1)])))

    wk, mo, qt, yr = days.to_period("W"), days.to_period("M"), days.to_period("Q"), days.year
    firstdays = {}
    for name, per in (("W", wk), ("M", mo), ("Q", qt), ("Y", yr)):
        firstdays[name] = np.r_[True, np.asarray(per[1:] != per[:-1])]
    # period aggregates
    def per_agg(per):
        s = pd.DataFrame({"o": d["open"].to_numpy(), "h": d["high"].to_numpy(), "l": d["low"].to_numpy(),
                          "c": d["close"].to_numpy()}, index=per)
        return s.groupby(level=0).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
    pa = {"W": per_agg(wk), "M": per_agg(mo), "Q": per_agg(qt), "Y": per_agg(pd.PeriodIndex(yr, freq="Y"))}
    pers = {"W": wk, "M": mo, "Q": qt, "Y": pd.PeriodIndex(yr, freq="Y")}
    sess_cache = []
    for k in range(len(d)):
        t0 = int(d["t0"].iloc[k])
        sa = agg(int(tok[k]), int(lon[k])) if tok[k] > 0 else None
        sl = agg(int(lon[k]), int(nyo[k]))
        sn = agg(int(nyo[k]), int(d["end_ns"].iloc[k]))
        sess_cache.append((sa, sl, sn))
        # daily class (window = this trading day)
        add(k, "DO", d["open"].iloc[k], t0 + h1, k)
        if k > 0:
            add(k, "PDH", d["high"].iloc[k - 1], t0, k)
            add(k, "PDL", d["low"].iloc[k - 1], t0, k)
            add(k, "PDC", d["close"].iloc[k - 1], t0, k)
            pn = sess_cache[k - 1][2]
            if pn:
                add(k, "PNH", pn[1], t0, k)
                add(k, "PNL", pn[2], t0, k)
                add(k, "PNC", pn[3], t0, k)
        if sa:
            add(k, "AO", sa[0], int(tok[k]) + h1, k)
            add(k, "AH", sa[1], int(lon[k]), k)
            add(k, "AL", sa[2], int(lon[k]), k)
            add(k, "AC", sa[3], int(lon[k]), k)
        if sl:
            add(k, "LO", sl[0], int(lon[k]) + h1, k)
            add(k, "LH", sl[1], int(nyo[k]), k)
            add(k, "LL", sl[2], int(nyo[k]), k)
            add(k, "LC", sl[3], int(nyo[k]), k)
        if sn:
            add(k, "NO", sn[0], int(nyo[k]) + h1, k)
        # weekly-and-longer class (window = 5 trading days from the first day of the period)
        for name in ("W", "M", "Q", "Y"):
            if firstdays[name][k]:
                cur = pers[name][k]
                add(k, f"{name}O", d["open"].iloc[k], t0 + h1, k + 4)
                prev = cur - 1
                if prev in pa[name].index:
                    add(k, f"P{name}H", pa[name].at[prev, "h"], t0, k + 4)
                    add(k, f"P{name}L", pa[name].at[prev, "l"], t0, k + 4)
        # round numbers around the day's first price
        ref = d["open"].iloc[k]
        for step, grp, skip in ((10, "R10", 50), (50, "R50", 100), (100, "R100", 0)):
            up = np.floor(ref / step) * step + step
            dn = np.ceil(ref / step) * step - step
            for v in (dn, up):
                if skip and v % skip == 0:
                    continue
                add(k, grp, v, t0 + h1, k)
    lv = pd.DataFrame(rows, columns=["k", "grp", "val", "avail", "end"])
    return d, lv


FAMILY = {"DO": "daily_open", "PDH": "prev_day_HL", "PDL": "prev_day_HL", "PDC": "prev_day_close",
          "PNH": "prev_NY_session_HL", "PNL": "prev_NY_session_HL", "PNC": "prev_NY_session_close",
          "AH": "asia_HL", "AL": "asia_HL", "AC": "asia_close", "AO": "session_open", "LO": "session_open",
          "NO": "session_open", "LH": "london_HL", "LL": "london_HL", "LC": "london_close",
          "WO": "week_month_open", "MO": "week_month_open", "QO": "quarter_year_open", "YO": "quarter_year_open",
          "PWH": "prev_week_HL", "PWL": "prev_week_HL", "PMH": "prev_month_HL", "PML": "prev_month_HL",
          "PQH": "prev_quarter_year_HL", "PQL": "prev_quarter_year_HL", "PYH": "prev_quarter_year_HL",
          "PYL": "prev_quarter_year_HL", "R10": "round_10", "R50": "round_50", "R100": "round_100"}


# ---------------------------------------------------------------- simulation
def sim(hi, lo, cl, side, entry, stop, s, limit, a0):
    """Stop-first. Returns (R fixed 2R, R partial-at-1R + 1.5 ATR trail). Bars s .. s+TIME_BARS."""
    risk = abs(entry - stop)
    n = len(hi)
    if s >= n or risk <= 0:
        return np.nan, np.nan
    tgt = entry + side * TARGET_R * risk
    one = entry + side * risk
    end = min(n, s + TIME_BARS + 1)
    r2 = rpt = None
    stage = 0
    best = trail = 0.0
    for t in range(s, end):
        h, lw = hi[t], lo[t]
        if r2 is None:
            sh = (lw <= stop) if side > 0 else (h >= stop)
            th = ((h >= tgt) if side > 0 else (lw <= tgt)) and (t > s or not limit)
            if sh:
                r2 = -1.0
            elif th:
                r2 = TARGET_R
        if rpt is None:
            if stage == 0:
                sh = (lw <= stop) if side > 0 else (h >= stop)
                if sh:
                    rpt = -1.0
                elif (t > s or not limit) and ((h >= one) if side > 0 else (lw <= one)):
                    stage, trail, best = 1, entry, (h if side > 0 else lw)
            else:
                if (side > 0 and lw <= trail) or (side < 0 and h >= trail):
                    rpt = 0.5 + 0.5 * side * (trail - entry) / risk
                else:
                    if side > 0:
                        best = max(best, h)
                        trail = max(trail, best - 1.5 * a0)
                    else:
                        best = min(best, lw)
                        trail = min(trail, best + 1.5 * a0)
        if r2 is not None and rpt is not None:
            break
    last = cl[end - 1]
    if r2 is None:
        r2 = side * (last - entry) / risk
    if rpt is None:
        rpt = side * (last - entry) / risk if stage == 0 else 0.5 + 0.5 * side * (last - entry) / risk
    return r2, rpt


def process_tf(args):
    tf, raw = args
    m1 = load(Path(raw))
    b = bars(m1, tf)
    d, lv = build_levels(m1)
    rng = np.random.default_rng({"15min": 1, "1h": 2, "4h": 3}[tf] + 20261026)
    t_ns = epoch_ns(b.index)
    hi, lo, op, cl = (b[c].to_numpy() for c in ("high", "low", "open", "close"))
    spr = b["spread"].to_numpy()
    at = wilder_atr(b)
    # per-bar context: trading day, running day range, day open, session
    tdb = trading_day(b.index)
    kd = np.searchsorted(d.index.to_numpy(), tdb.to_numpy())
    ok_day = (kd < len(d)) & (d.index.to_numpy()[np.clip(kd, 0, len(d) - 1)] == tdb.to_numpy())
    kd = np.where(ok_day, kd, -1)
    s_td = pd.Series(tdb)
    cmax = pd.Series(hi).groupby(s_td.to_numpy()).cummax().to_numpy()
    cmin = pd.Series(lo).groupby(s_td.to_numpy()).cummin().to_numpy()
    dopen = pd.Series(op).groupby(s_td.to_numpy()).transform("first").to_numpy()
    ldn_h = b.index.tz_convert(LDN).hour.to_numpy()
    nyi = b.index.tz_convert(NY)
    nymin = (nyi.hour * 60 + nyi.minute).to_numpy()
    sess = np.where((ldn_h < 8) | (nymin >= 1020), 0, np.where(nymin < 510, 1, 2))
    fc = d["fc"].to_numpy()
    trend = d["trend"].to_numpy()
    rows, touches = [], []
    nb = len(b)
    for lvl in lv.itertuples(index=False):
        i0 = int(np.searchsorted(t_ns, lvl.avail))
        i1 = int(np.searchsorted(t_ns, lvl.end))
        if i0 < 20 or i1 - i0 < 2 or i1 > nb:
            continue
        ref = cl[i0 - 1]
        dist = abs(lvl.val - ref)
        a_ref = at[i0 - 1]
        if dist < 0.05 * a_ref or not np.isfinite(a_ref):
            continue
        fam = FAMILY[lvl.grp]
        cands = [(lvl.val, 0)] + [(ref + (1 if rng.random() < 0.5 else -1) * dist, 1) for _ in range(REPS)]
        for L, kind in cands:
            side0 = 1 if ref > L else -1                   # +1: price above level (level is support)
            m = (lo[i0:i1] <= L) & (hi[i0:i1] >= L)
            if not m.any():
                touches.append((tf, lvl.grp, fam, kind, 0, lvl.k, -1, np.nan, np.nan))
                continue
            j = i0 + int(m.argmax())
            a = at[j - 1]
            if not np.isfinite(a) or a <= 0:
                continue
            kk = kd[j]
            f = fc[kk] if kk >= 0 else np.nan
            rs = (cmax[j - 1] - cmin[j - 1]) / f if (j > 0 and kk >= 0 and kd[j - 1] == kk and f > 0) else np.nan
            dt = abs(cl[j - 1] - dopen[j]) / f if (kk >= 0 and f > 0 and kd[j - 1] == kk) else np.nan
            tr = trend[kk] if kk >= 0 else np.nan
            piece = side0 * (cl[j] - L)                    # >0: closed on the original side
            pierce = (L - lo[j]) if side0 > 0 else (hi[j] - L)
            brk = -piece >= 0.1 * a
            cat = 0 if brk else (1 if (piece > 0 and pierce >= 0.1 * a) else (2 if piece > 0 else 3))  # break/sweep/reject/stall
            jf = min(j + 8, nb - 1)
            ft = -side0 * (cl[jf] - cl[j]) / a             # fixed-horizon drift in the break direction (ATR)
            touches.append((tf, lvl.grp, fam, kind, 1, lvl.k, cat, ft, dist / a_ref))
            base = (tf, lvl.grp, fam, kind, int(lvl.k), sess[j], rs, dt, a, spr[j])

            def put(strat, side, entry, stop, s, limit, extra=0):
                r2, rpt = sim(hi, lo, cl, side, entry, stop, s, limit, a)
                if np.isfinite(r2):
                    ag = np.nan if not np.isfinite(tr) else float(tr == side)
                    rows.append(base + (strat, side, abs(entry - stop), r2, rpt, ag, extra))
            d_b = -side0
            if brk:                                          # break-close: enter at close, stop S ATR back through the level
                for sn, sv in (("BRK_s0.5", 0.5), ("BRK_s1.0", 1.0)):
                    put(sn, d_b, cl[j], L + side0 * sv * a, j + 1, False)
            if piece > 0:                                    # reject / sweep: fade at the close, stop beyond the touch extreme
                ext = (lo[j] if side0 > 0 else hi[j])
                stop = min(ext, L) - 0.1 * a if side0 > 0 else max(ext, L) + 0.1 * a
                if abs(cl[j] - stop) <= 3 * a:
                    put("FADE", side0, cl[j], stop, j + 1, False, int(pierce >= 0.1 * a))
            for sn, sv in (("RET_s0.5", 0.5), ("RET_s1.0", 1.0)):   # resting limit at the level (E's generic retest)
                put(sn, side0, L, L - side0 * sv * a, j, True)
    cols = ["tf", "grp", "fam", "kind", "k", "sess", "rs", "dist", "atr", "spr", "strat", "side", "risk", "r2", "rpt",
            "agree", "sweep"]
    tr_df = pd.DataFrame(touches, columns=["tf", "grp", "fam", "kind", "touched", "k", "cat", "ft", "dist_atr"])
    return pd.DataFrame(rows, columns=cols), tr_df, d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(3) as ex:
        res = list(ex.map(process_tf, [(tf, str(a.raw)) for tf in ("15min", "1h", "4h")]))
    pd.concat([r[0] for r in res]).to_parquet(a.out / "trades.parquet")
    pd.concat([r[1] for r in res]).to_parquet(a.out / "touches.parquet")
    res[0][2].drop(columns=["t0"]).to_parquet(a.out / "days.parquet")
    print(json.dumps({"rows": int(sum(len(r[0]) for r in res)), "touch_rows": int(sum(len(r[1]) for r in res))}))


if __name__ == "__main__":
    main()
