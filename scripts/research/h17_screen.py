"""H-17 screen on 2010-2017 (amendment A3; frozen rule in docs/research/prereg-2026-10-30-h17-screen.md).

Trend-day continuation with a runner (hypotheses.md row 4; preregistration-2027Q2-draft.md "H-17", rows O-2/O-3 of
preregistration-2027Q1.md), read on the A3 explore window only. Every input is cut at 2018-01-01 before anything is
built; no 2018+ file is opened. Records nothing in any registry. Two stages, run in this order:

    --stage count   events only (high / low days with a qualifying first breach, per year, blackout and ATR drops);
                    no exit is walked and no return is computed
    --stage read    the exits, gross / net R and the frozen reading rule (refused below the frozen event floor)
    --stage selftest  the exit walk on synthetic paths (no market data)

    .venv/bin/python scripts/research/h17_screen.py --stage count --bars raw/ --us-calendar ~/tmp/ts-data/cal \
        --out ~/tmp/h17-screen/count.json

Outputs (statistics only, no prices) go outside the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.data.release import read_bar_files, read_us_calendar_files  # noqa: E402
from goldbot.data.resample import mid, resample_bars  # noqa: E402
from goldbot.data.store import asof_join  # noqa: E402
from goldbot.data.timeutil import epoch_ns, utc_index  # noqa: E402
from goldbot.features import day_type as dt  # noqa: E402
from goldbot.research import h16  # noqa: E402

# ------------------------------------------------------------------ frozen constants (prereg-2026-10-30-h17-screen.md)
SCREEN_FROM_YEAR, SCREEN_TO_YEAR = 2010, 2017
SCREEN_CUT = "2018-01-01"
LONDON, NEWYORK = "Europe/London", "America/New_York"
REF_OPEN_LDN_MIN = 8 * 60            # O_D = open of the 15m bar starting 08:00 London
WIN_FROM_LDN_MIN = 9 * 60            # entry bar close >= 09:00 London
WIN_TO_NY_MIN = 10 * 60 + 30         # entry bar close <= 10:30 New York
FLAT_NY_MIN = 16 * 60 + 30           # flat at the first bar close at or after 16:30 New York
NOISE_DAYS = 14
NOISE_MULT = 1.0
ATR_N = 14
STOP_ATR = 1.5
V0_TARGET_ATR = 3.0
V1_PART_ATR, V1_PART_SHARE = 2.0, 0.5
V1_ARM_ATR, V1_TRAIL_ATR, V1_CAP_ATR = 1.5, 2.5, 10.0
BLACKOUT_BEFORE_MIN, BLACKOUT_AFTER_MIN = 15, 30       # [-15, +30) min around a tier-1 release, on the bar close
COST_RT_USD, COST_STOP_USD = 0.23, 0.70                 # cost of record (evaluator R2): $/oz round trip + per stop exit
COST_PRIOR_USD = 1.07                                   # research prior, flat: reported only
MIN_HIGH_EVENTS = 400                                   # frozen screen floor (strict high-half events)
T_MIN = 2.0
MIN_POS_YEARS = 3
WELCH_T_MIN = 2.0


def code_hash() -> str:
    return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]


# ------------------------------------------------------------------ data
def load(bars_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    b1 = read_bar_files(bars_dir, SCREEN_FROM_YEAR, SCREEN_TO_YEAR)
    b1 = h16.cut_before_holdout(b1, SCREEN_CUT)
    if len(b1) and utc_index(b1["ts_utc"]).max() >= pd.Timestamp(SCREEN_CUT, tz="UTC"):
        raise SystemExit("a 2018+ bar reached the screen")
    m15 = mid(resample_bars(b1, "15m"))
    m1 = mid(b1)
    return m1.reset_index(drop=True), m15.reset_index(drop=True)


def wilder_atr(m: pd.DataFrame, n: int = ATR_N) -> np.ndarray:
    h, lo, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    pc = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - lo, np.fmax(np.abs(h - pc), np.abs(lo - pc)))
    tr[0] = h[0] - lo[0]
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def har_high(m15: pd.DataFrame) -> pd.DataFrame:
    """Per 15m bar: H-16's HAR-only baseline forecast in force and its fit's cut50, joined as of `available_utc`
    (the close of the stub-free day that emitted it), exactly as the day_type feature does."""
    inp = dt.daily_inputs(dt.daily_bars(m15))
    fc = dt.har_forecast(inp)
    emit = pd.DataFrame({"available_utc": utc_index(inp["close_utc"]), "hat": fc["hat"].to_numpy(),
                         "cut50": fc["cut50"].to_numpy()})
    bars = pd.DataFrame({"ts_utc": utc_index(m15["ts_utc"]).as_unit("ns"), "_row": np.arange(len(m15))})
    j = asof_join(bars, emit).sort_values("_row").reset_index(drop=True)
    return j[["hat", "cut50"]]


def blackout_mask(close_utc: pd.DatetimeIndex, start_utc: pd.DatetimeIndex, cal: pd.DataFrame) -> np.ndarray:
    """True where the bar close lies in [-15, +30) min of a tier-1 release scheduled as known at the bar's start (each
    event in the state of its latest row with available_utc <= bar start)."""
    out = np.zeros(len(close_utc), dtype=bool)
    c1 = cal[cal["tier"].astype(int) == 1].copy()
    if len(c1) == 0:
        return out
    c1["_av"] = epoch_ns(pd.DatetimeIndex(pd.to_datetime(c1["available_utc"], utc=True)))
    c1["_ev"] = epoch_ns(pd.DatetimeIndex(pd.to_datetime(c1["event_utc"], utc=True)))
    tc, ts = epoch_ns(close_utc), epoch_ns(start_utc)
    before, after = BLACKOUT_BEFORE_MIN * 60 * 10**9, BLACKOUT_AFTER_MIN * 60 * 10**9
    for _, hist in c1.sort_values("_av", kind="stable").groupby("event_id", sort=False):
        av, ev = hist["_av"].to_numpy(), hist["_ev"].to_numpy()
        sched = (hist["status"].astype(str) == "scheduled").to_numpy()
        lo_ns, hi_ns = ev.min() - before, ev.max() + after
        near = np.flatnonzero((tc >= lo_ns) & (tc < hi_ns))
        if len(near) == 0:
            continue
        pos = np.searchsorted(av, ts[near], side="right") - 1
        ok = pos >= 0
        p = np.where(ok, pos, 0)
        e = ev[p]
        hit = ok & sched[p] & (tc[near] >= e - before) & (tc[near] < e + after)
        out[near[hit]] = True
    return out


def candidates(m15: pd.DataFrame, cal: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """One row per London date with a first breach in the window: the literal-rule event (strict) and the
    next-bar-outside-blackout alternative (reported only). No exit, no return."""
    start = utc_index(m15["ts_utc"])
    close = start + pd.Timedelta(minutes=15)
    sl, cl, cn = start.tz_convert(LONDON), close.tz_convert(LONDON), close.tz_convert(NEWYORK)
    f = pd.DataFrame({
        "i": np.arange(len(m15)), "start": start, "close_utc": close,
        "date": cl.tz_localize(None).normalize(),
        "s_ldn_min": np.asarray(sl.hour * 60 + sl.minute), "c_ldn_min": np.asarray(cl.hour * 60 + cl.minute),
        "c_ny_min": np.asarray(cn.hour * 60 + cn.minute), "ny_date": cn.tz_localize(None).normalize(),
        "open": m15["open"].to_numpy(dtype=float), "close": m15["close"].to_numpy(dtype=float)})
    f["s_date"] = sl.tz_localize(None).normalize()
    ref = f[(f["s_ldn_min"] == REF_OPEN_LDN_MIN)].groupby("s_date")["open"].first()
    f["O"] = ref.reindex(f["date"]).to_numpy()
    f["move"] = (f["close"] - f["O"]).abs()
    # noise boundary: per London close-time tau, mean |close - O| over the previous 14 days that had that bar (and O)
    f["B"] = np.nan
    have = f["O"].notna() & (f["date"] == f["s_date"]) & (f["c_ldn_min"] > REF_OPEN_LDN_MIN)
    g = f[have].sort_values(["c_ldn_min", "date"])
    f.loc[g.index, "B"] = g.groupby("c_ldn_min")["move"].transform(
        lambda x: x.shift(1).rolling(NOISE_DAYS, min_periods=NOISE_DAYS).mean()).to_numpy()
    win = have & (f["c_ldn_min"] >= WIN_FROM_LDN_MIN) & (f["ny_date"] == f["date"]) & (f["c_ny_min"] <= WIN_TO_NY_MIN)
    f["atr"] = wilder_atr(m15)
    hh = har_high(m15)
    f["hat"], f["cut50"] = hh["hat"].to_numpy(), hh["cut50"].to_numpy()
    f["blackout"] = blackout_mask(pd.DatetimeIndex(f["close_utc"]), pd.DatetimeIndex(f["start"]), cal)
    w = f[win & f["B"].notna()].copy()
    w = w[w["move"] > NOISE_MULT * w["B"]]
    rows, drops = [], {"blackout": 0, "no_atr": 0}
    for d, day in w.groupby("date", sort=True):
        first = day.iloc[0]
        if not np.isfinite(first["atr"]) or first["atr"] <= 0:
            drops["no_atr"] += 1
            strict = None
        elif first["blackout"]:
            drops["blackout"] += 1
            strict = None
        else:
            strict = first
        ok = day[(~day["blackout"].astype(bool)) & np.isfinite(day["atr"].to_numpy(dtype=float))]
        nxt = ok.iloc[0] if len(ok) else None
        hat, cut = first["hat"], first["cut50"]
        half = "unclassified" if not (np.isfinite(hat) and np.isfinite(cut)) else ("high" if hat >= cut else "low")
        rows.append({"date": d, "half": half, "strict": strict is not None, "next_bar": nxt is not None,
                     "i": int(strict["i"]) if strict is not None else -1,
                     "side": int(np.sign(strict["close"] - strict["O"])) if strict is not None else 0,
                     "entry": float(strict["close"]) if strict is not None else np.nan,
                     "atr": float(strict["atr"]) if strict is not None else np.nan,
                     "entry_utc": strict["close_utc"] if strict is not None else pd.NaT})
    ev = pd.DataFrame(rows)
    ev["year"] = pd.DatetimeIndex(ev["date"]).year
    counts: dict[str, Any] = {
        "window_dates_with_breach": int(len(ev)),
        "high_events_strict": int(((ev["half"] == "high") & ev["strict"]).sum()),
        "low_events_strict": int(((ev["half"] == "low") & ev["strict"]).sum()),
        "unclassified_strict": int(((ev["half"] == "unclassified") & ev["strict"]).sum()),
        "high_events_next_bar": int(((ev["half"] == "high") & ev["next_bar"]).sum()),
        "dropped_first_breach_blackout": drops["blackout"], "dropped_first_breach_no_atr": drops["no_atr"],
        "high_strict_by_year": {int(k): int(v) for k, v in
                                ev[(ev["half"] == "high") & ev["strict"]].groupby("year").size().items()},
        "low_strict_by_year": {int(k): int(v) for k, v in
                               ev[(ev["half"] == "low") & ev["strict"]].groupby("year").size().items()},
        "first_classified_date": str(ev.loc[ev["half"] != "unclassified", "date"].min())[:10],
        "dates_with_O": int(ref.notna().sum()),
    }
    return ev, counts


# ------------------------------------------------------------------ exit walk (favourable units: x = side*(p-e)/ATR)
def walk(op: np.ndarray, hi: np.ndarray, lo: np.ndarray, cl: np.ndarray, variant: str) -> tuple[float, float, str, bool]:
    """Walk one trade over its 1m path (already in favourable ATR units, last bar = the flat bar). Returns
    (gross R, share of size exited by a stop order, reason, ambiguous bar seen). Inside a bar the stop is checked
    first (conservative); a stop fills at min(level, bar open); limits fill at their level; the trail is computed from
    the best high of completed bars."""
    stop0 = -STOP_ATR
    amb = False
    if variant == "V0":
        for k in range(len(cl)):
            s_hit, t_hit = lo[k] <= stop0, hi[k] >= V0_TARGET_ATR
            amb |= bool(s_hit and t_hit)
            if s_hit:
                return min(stop0, op[k]) / STOP_ATR, 1.0, "stop", amb
            if t_hit:
                return V0_TARGET_ATR / STOP_ATR, 0.0, "target", amb
        return cl[-1] / STOP_ATR, 0.0, "flat", amb
    best, armed = -np.inf, False
    a_open, r, stop_share = True, 0.0, 0.0
    for k in range(len(cl)):
        s = max(stop0, best - V1_TRAIL_ATR) if armed else stop0
        size = 1.0 if a_open else 1.0 - V1_PART_SHARE
        s_hit = lo[k] <= s
        t_hit = (a_open and hi[k] >= V1_PART_ATR) or hi[k] >= V1_CAP_ATR
        amb |= bool(s_hit and t_hit)
        if s_hit:
            fill = min(s, op[k])
            r += size * fill
            stop_share += size
            return r / STOP_ATR, stop_share, ("trail" if armed and s > stop0 else "stop"), amb
        if a_open and hi[k] >= V1_PART_ATR:
            r += V1_PART_SHARE * V1_PART_ATR
            a_open = False
        if hi[k] >= V1_CAP_ATR:
            r += (1.0 - V1_PART_SHARE) * V1_CAP_ATR
            return r / STOP_ATR, stop_share, "cap", amb
        best = max(best, hi[k])
        armed = armed or best >= V1_ARM_ATR
    rem = 1.0 if a_open else 1.0 - V1_PART_SHARE
    r += rem * cl[-1]
    return r / STOP_ATR, stop_share, "flat", amb


def trade_paths(m1: pd.DataFrame, ev: pd.DataFrame) -> list[dict[str, Any]]:
    ts = utc_index(m1["ts_utc"])
    ns = epoch_ns(ts)
    ny = ts.tz_convert(NEWYORK)
    ny_min = np.asarray(ny.hour * 60 + ny.minute)
    ny_date = np.asarray(ny.tz_localize(None).normalize())
    o, h, lo, c = (m1[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    out = []
    for e in ev.itertuples():
        t0 = epoch_ns(pd.DatetimeIndex([e.entry_utc]))[0]
        a = int(np.searchsorted(ns, t0, side="left"))
        d = np.datetime64(pd.Timestamp(e.entry_utc).tz_convert(NEWYORK).tz_localize(None).normalize())
        b = a
        flat_found = False
        while b < len(ns) and ny_date[b] == d and ny_min[b] < 17 * 60:
            if ny_min[b] + 1 >= FLAT_NY_MIN:
                flat_found = True
                break
            b += 1
        if not flat_found:
            b -= 1                       # no bar at/after 16:29 NY before the break: the last bar of the session
        if b < a:
            out.append({"ok": False})
            continue
        sl = slice(a, b + 1)
        sd, en, at = e.side, e.entry, e.atr
        if sd > 0:
            xo, xh, xl, xc = (o[sl] - en) / at, (h[sl] - en) / at, (lo[sl] - en) / at, (c[sl] - en) / at
        else:
            xo, xh, xl, xc = (en - o[sl]) / at, (en - lo[sl]) / at, (en - h[sl]) / at, (en - c[sl]) / at
        out.append({"ok": True, "flat_found": flat_found, "x": (xo, xh, xl, xc)})
    return out


def _t(x: np.ndarray) -> float:
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else float("nan")


def welch_weekly(hi_r: pd.Series, lo_r: pd.Series) -> dict[str, float]:
    """High minus low gross mean R over ISO-week blocks (each week's trades averaged into one value per half)."""
    def wk(s: pd.Series) -> np.ndarray:
        iso = pd.DatetimeIndex(s.index).isocalendar()
        return s.groupby([iso["year"].to_numpy(), iso["week"].to_numpy()]).mean().to_numpy(dtype=float)
    a, b = wk(hi_r), wk(lo_r)
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return {"weeks_high": int(len(a)), "weeks_low": int(len(b)), "diff": float(a.mean() - b.mean()),
            "t": float((a.mean() - b.mean()) / se)}


def read(m1: pd.DataFrame, ev: pd.DataFrame, counts: dict[str, Any]) -> dict[str, Any]:
    if counts["high_events_strict"] < MIN_HIGH_EVENTS:
        return {"verdict": "INCONCLUSIVE (event floor)", "counts": counts}
    tr = ev[ev["strict"] & ev["half"].isin(["high", "low"])].reset_index(drop=True)
    paths = trade_paths(m1, tr)
    recs = []
    for e, p in zip(tr.itertuples(), paths, strict=True):
        if not p["ok"]:
            continue
        r_usd = STOP_ATR * e.atr
        row = {"date": e.date, "half": e.half, "year": e.year, "side": e.side, "r_usd": r_usd,
               "flat_found": p["flat_found"]}
        for v in ("V0", "V1"):
            g, ss, why, amb = walk(*p["x"], variant=v)
            row[f"{v}_gross"] = g
            row[f"{v}_net"] = g - (COST_RT_USD + COST_STOP_USD * ss) / r_usd
            row[f"{v}_net_prior"] = g - COST_PRIOR_USD / r_usd
            row[f"{v}_why"], row[f"{v}_amb"] = why, amb
        recs.append(row)
    t = pd.DataFrame(recs).set_index("date")
    hi, lo = t[t["half"] == "high"], t[t["half"] == "low"]
    out: dict[str, Any] = {"counts": counts, "n_high_walked": int(len(hi)), "n_low_walked": int(len(lo)),
                           "flat_fallback_high": int((~hi["flat_found"]).sum()),
                           "median_r_usd_high": float(hi["r_usd"].median()),
                           "mean_cost_r_high": {}}
    verdicts = {}
    for v in ("V0", "V1"):
        g, n = hi[f"{v}_gross"].to_numpy(), hi[f"{v}_net"].to_numpy()
        yrs = hi.groupby("year")[f"{v}_net"].mean()
        wel = welch_weekly(hi[f"{v}_gross"], lo[f"{v}_gross"])
        passes = bool(_t(g) >= T_MIN and len(g) >= MIN_HIGH_EVENTS and n.mean() > 0 and int((yrs > 0).sum()) >= MIN_POS_YEARS)
        out[v] = {"gross_mean": float(g.mean()), "gross_t": _t(g), "net_mean": float(n.mean()), "net_t": _t(n),
                  "net_prior_mean": float(hi[f"{v}_net_prior"].mean()),
                  "net_by_year": {int(k): float(x) for k, x in yrs.items()},
                  "gross_by_year": {int(k): float(x) for k, x in hi.groupby("year")[f"{v}_gross"].mean().items()},
                  "pos_net_years": int((yrs > 0).sum()),
                  "low_gross_mean": float(lo[f"{v}_gross"].mean()), "low_net_mean": float(lo[f"{v}_net"].mean()),
                  "welch_weekly": wel, "exit_share": hi[f"{v}_why"].value_counts(normalize=True).round(4).to_dict(),
                  "ambiguous_bars": int(hi[f"{v}_amb"].sum()), "screen_pass": passes,
                  "long_net": float(hi.loc[hi["side"] > 0, f"{v}_net"].mean()),
                  "short_net": float(hi.loc[hi["side"] < 0, f"{v}_net"].mean()),
                  "n_long": int((hi["side"] > 0).sum())}
        out["mean_cost_r_high"][v] = float((hi[f"{v}_gross"] - hi[f"{v}_net"]).mean())
        verdicts[v] = passes and wel["t"] >= WELCH_T_MIN
    v0, v1 = out["V0"], out["V1"]
    if not v0["screen_pass"]:
        verdict = "FAIL: V0 fails its screen; V1 recorded as run, not read; O-2/O-3 cancelled; trend-day line retired"
    elif v0["welch_weekly"]["t"] < WELCH_T_MIN:
        verdict = "FAIL: high half not above low half (weekly Welch t < 2); retired as a repeat of R-02/R-05; O-2/O-3 cancelled"
    elif verdicts["V1"] and v1["net_mean"] >= v0["net_mean"]:
        verdict = "PASS (runner): O-2 and O-3 permitted on 2018-01..2025-09"
    else:
        verdict = "PASS (fixed exits only): O-2 permitted; O-3 cancelled; runner retired for this family"
    out["verdict"] = verdict
    return out


# ------------------------------------------------------------------ self-test (synthetic, no market data)
def selftest() -> None:
    def run(path: list[tuple[float, float, float, float]], v: str) -> tuple[float, float, str, bool]:
        a = np.array(path, dtype=float).T
        return walk(a[0], a[1], a[2], a[3], v)
    assert run([(0, 0.5, -1.6, -1.0)], "V0")[:3] == (-1.0, 1.0, "stop")
    assert run([(0, 3.1, -0.5, 2.0)], "V0")[:3] == (2.0, 0.0, "target")
    assert run([(0, 3.1, -1.6, 2.0)], "V0")[1:4] == (1.0, "stop", True)
    assert abs(run([(0, 1.0, -0.5, 0.75)], "V0")[0] - 0.5) < 1e-12
    g, ss, why, _ = run([(-2.0, -1.0, -2.2, -2.0)], "V0")
    assert abs(g - (-2.0 / 1.5)) < 1e-12 and why == "stop"                       # gap through the stop fills at open
    # V1: partial at 2, best 4 -> trail at 1.5, stopped at 1.5 -> 0.5*2 + 0.5*1.5 = 1.75 ATR
    g, ss, why, _ = run([(0, 2.1, -0.2, 2.0), (2.0, 4.0, 1.9, 3.9), (3.9, 3.95, 1.4, 1.5)], "V1")
    assert abs(g - 1.75 / 1.5) < 1e-12 and ss == 0.5 and why == "trail", (g, ss, why)
    # V1: armed at 1.6 (trail -0.9), never reaches 2, stopped at -0.9 on the whole size
    g, ss, why, _ = run([(0, 1.6, -0.2, 1.0), (1.0, 1.2, -1.0, -1.0)], "V1")
    assert abs(g - (-0.9 / 1.5)) < 1e-12 and ss == 1.0 and why == "trail", (g, ss, why)
    # V1: partial then cap: 0.5*2 + 0.5*10 = 6 ATR
    g, ss, why, _ = run([(0, 2.5, -0.1, 2.4), (2.4, 10.5, 2.3, 10.0)], "V1")
    assert abs(g - 6.0 / 1.5) < 1e-12 and why == "cap"
    # V1: flat with partial taken: 0.5*2 + 0.5*close(0.8); trail from best 2.5 is 0 (above the stop), not hit (low 0.1)
    g, ss, why, _ = run([(0, 2.5, -0.1, 2.0), (2.0, 2.1, 0.1, 0.8)], "V1")
    assert abs(g - (1.0 + 0.4) / 1.5) < 1e-12 and why == "flat", (g, why)
    # V1: plain stop before arming
    g, ss, why, _ = run([(0, 0.5, -1.7, -1.6)], "V1")
    assert abs(g + 1.0) < 1e-12 and ss == 1.0 and why == "stop"
    print("selftest ok")


def main() -> int:
    ap = argparse.ArgumentParser(description="H-17 screen on 2010-2017 (A3)")
    ap.add_argument("--stage", choices=("count", "read", "selftest"), required=True)
    ap.add_argument("--bars")
    ap.add_argument("--us-calendar")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.stage == "selftest":
        selftest()
        return 0
    m1, m15 = load(Path(a.bars))
    cal = read_us_calendar_files(Path(a.us_calendar))
    cal = h16.cut_before_holdout(cal, SCREEN_CUT, ("available_utc", "event_utc"))
    ev, counts = candidates(m15, cal)
    counts["bars_from"], counts["bars_to"] = str(utc_index(m1["ts_utc"]).min()), str(utc_index(m1["ts_utc"]).max())
    counts["code_hash"] = code_hash()
    res = counts if a.stage == "count" else read(m1, ev, counts)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
