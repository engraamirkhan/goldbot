"""Retail setup screen RS1-RS3 (prereg-2026-11-01-retail-setups.md). 2010-2017 only, local bars; outputs go to
~/tmp/retail_screens (nothing is written to the repo).

Usage: .venv/bin/python docs/research/candidates/retail/retail_screen.py --raw <raw folder> [--workers 3]

Every trade walk works on a long-only view: a short is the same walk on negated prices (high <-> -low)."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from goldbot.data.timeutil import epoch_ns  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
START, END = pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")
COST_VERSION = "cor-2026-10-31.2"
COST_MULT = 1.5
GATE = 0.20               # skip a setup when 1.5 x round trip > GATE x stop distance
SWAP_LONG = 0.60          # $/oz per 22:00 UTC weekday rollover held, Wednesday x3
TARGET_R = 2.0
N_TWIN = 4
N_BOOT = 10_000
SEED = 20261101
N_RULES = 3               # Bonferroni over this lane's rules
MIN = 60_000_000_000
HOUR = 60 * MIN
NY = "America/New_York"
OUT = Path.home() / "tmp" / "retail_screens"


# ---------------------------------------------------------------- data
@dataclass
class M1:
    ts: np.ndarray        # int64 ns, 1m bar open
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray

    def first_at_or_after(self, t: int) -> int:
        return int(np.searchsorted(self.ts, t, side="left"))

    def last_before(self, t: int) -> int:
        return int(np.searchsorted(self.ts, t, side="left")) - 1


def load_m1(raw: Path) -> M1:
    frames = [pd.read_parquet(raw / f"xauusd_1m_dukascopy_{y}.parquet") for y in range(2010, 2018)]
    b = pd.concat(frames, ignore_index=True)
    ts = pd.DatetimeIndex(b["ts_utc"]).as_unit("ns")
    keep = (ts >= START) & (ts < END)
    b, ts = b.loc[keep].reset_index(drop=True), ts[keep]
    assert ts.max() < END, "a 2018+ bar was read"
    m = {k: ((b[f"bid_{k}"] + b[f"ask_{k}"]) / 2).to_numpy(float) for k in ("open", "high", "low", "close")}
    t = epoch_ns(ts)
    order = np.argsort(t, kind="stable")
    t = t[order]
    uniq = np.r_[True, np.diff(t) > 0]
    o, h, lo, c = (m[k][order][uniq] for k in ("open", "high", "low", "close"))
    return M1(t[uniq], o, h, lo, c)


@dataclass
class TF:
    """Bars of one timeframe as arrays; atr[i] = ATR(14) including bar i; a bar is known at close_ns[i]."""
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    atr: np.ndarray
    open_ns: np.ndarray
    close_ns: np.ndarray

    def __len__(self) -> int:
        return len(self.c)


def resample(m: M1, minutes: int) -> TF:
    """tf bars from 1m mid: open_ns, close_ns (= open + tf), o h l c, atr (ATR(14) simple mean of TR, at this bar)."""
    f = pd.DataFrame({"o": m.o, "h": m.h, "l": m.lo, "c": m.c}, index=pd.DatetimeIndex(m.ts, tz="UTC"))
    q = f.resample(f"{minutes}min", label="left", closed="left").agg(
        {"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    pc = q["c"].shift(1)
    tr = pd.concat([q["h"] - q["l"], (q["h"] - pc).abs(), (q["l"] - pc).abs()], axis=1).max(axis=1)
    q["atr"] = tr.rolling(14).mean()
    open_ns = epoch_ns(pd.DatetimeIndex(q.index))
    a = {k: q[k].to_numpy(float) for k in ("o", "h", "l", "c", "atr")}
    return TF(a["o"], a["h"], a["l"], a["c"], a["atr"], open_ns, open_ns + minutes * MIN)


def atr_at(bars: TF, t: int) -> float:
    """ATR of the last bar completed at or before t."""
    i = int(np.searchsorted(bars.close_ns, t, side="right")) - 1
    return float(bars.atr[i]) if i >= 0 else float("nan")


def load_costs() -> dict[str, dict[str, float]]:
    cor = yaml.safe_load((ROOT / "config" / "cost_of_record.yaml").read_text())
    assert cor["version"] == COST_VERSION, cor["version"]
    return {s: {"rt": float(v["round_trip_usd"]), "ov": float(v["overshoot_usd"])}
            for s, v in cor["cells"]["icm"].items() if s != "all"}


def session(t: int) -> str:
    mod = (t // MIN) % 1440
    return "asia" if mod < 7 * 60 else ("london" if mod < 12 * 60 + 30 else "newyork")


def rollovers(t0: int, t1: int) -> float:
    """Long swap units for the 22:00 UTC weekday rollovers in (t0, t1]; Wednesday counts 3."""
    d0 = pd.Timestamp(t0, tz="UTC")
    r = d0.normalize() + pd.Timedelta(hours=22)
    if r <= d0:
        r += pd.Timedelta(days=1)
    n = 0.0
    while r.value <= t1:
        if r.dayofweek < 5:
            n += 3.0 if r.dayofweek == 2 else 1.0
        r += pd.Timedelta(days=1)
    return n


def h_u(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2 ** 64


def ny_ns(date: pd.Timestamp, hh: int, mm: int) -> int:
    """UTC ns of hh:mm New York local on a calendar date."""
    return int(pd.Timestamp(year=date.year, month=date.month, day=date.day, hour=hh, minute=mm, tz=NY)
               .tz_convert("UTC").value)


# ---------------------------------------------------------------- the limit-order walk (long view)
def fill_index(lo: np.ndarray, limit: float) -> int | None:
    """First minute whose low reaches the buy limit (filled at the limit, no price improvement)."""
    hit = np.flatnonzero(lo <= limit)
    return int(hit[0]) if len(hit) else None


def walk_exit(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, limit: float, stop: float,
              tgt: float) -> tuple[float, str, int]:
    """Minute 0 is the fill minute; the last minute is the time exit. Only the stop counts in the fill minute; later a
    minute holding both is a stop; a stop gapped through exits at the open (worse); a target exits at the target.
    Returns (gross pnl per oz, how, exit offset)."""
    if lo[0] <= stop:
        return float(min(stop, o[0]) - limit), "stop", 0
    s = np.flatnonzero(lo[1:] <= stop)
    t = np.flatnonzero(h[1:] >= tgt)
    js = int(s[0]) + 1 if len(s) else 10 ** 9
    jt = int(t[0]) + 1 if len(t) else 10 ** 9
    if js < 10 ** 9 and js <= jt:
        return float(min(stop, o[js]) - limit), "stop", js
    if jt < 10 ** 9:
        return float(tgt - limit), "target", jt
    return float(c[-1] - limit), "time", len(c) - 1


@dataclass
class Setup:
    t_setup: int          # setup bar close (ns)
    side: int
    limit: float
    stop: float
    close: float          # setup-bar close (for the twin's limit offset)
    t_expiry: int         # order live while the 1m bar opens before this
    t_exit_abs: int | None   # absolute time exit (RS1)
    exit_dur: int | None     # time exit duration after the fill (RS2, RS3)


def simulate(m: M1, costs: dict[str, dict[str, float]], s: Setup) -> dict[str, Any]:
    """One limit-order trade; returns a record (filled False if never filled)."""
    k0 = m.first_at_or_after(s.t_setup)
    k_live_end = m.last_before(s.t_expiry)
    if k0 >= len(m.ts) or k_live_end < k0:
        return {"filled": False}
    sg = s.side
    lim, stp = sg * s.limit, sg * s.stop
    risk = lim - stp
    assert risk > 0, (s, risk)
    live_lo = m.lo[k0:k_live_end + 1] if sg > 0 else -m.h[k0:k_live_end + 1]
    jf = fill_index(live_lo, lim)
    if jf is None:
        return {"filled": False}
    kf = k0 + jf
    if s.t_exit_abs is not None:
        kx = m.last_before(s.t_exit_abs)
    else:
        assert s.exit_dur is not None
        kx = m.last_before(int(m.ts[kf]) + s.exit_dur)
    kx = max(kx, kf)
    sl = slice(kf, kx + 1)
    if sg > 0:
        o, h, lo, c = m.o[sl], m.h[sl], m.lo[sl], m.c[sl]
    else:
        o, h, lo, c = -m.o[sl], -m.lo[sl], -m.h[sl], -m.c[sl]
    gross, how, jx = walk_exit(o, h, lo, c, lim, stp, lim + TARGET_R * risk)
    t_in, t_out = int(m.ts[kf]), int(m.ts[kf + jx])
    cc = costs[session(t_in)]
    swap = SWAP_LONG * rollovers(t_in, t_out) if sg > 0 else 0.0
    net = gross - COST_MULT * cc["rt"] - (COST_MULT * cc["ov"] if how == "stop" else 0.0) - swap
    return {"filled": True, "t_fill": t_in, "t_exit": t_out, "side": sg, "R": net / risk,
            "gross_R": gross / risk, "how": how, "risk_usd": risk}


def gated(costs: dict[str, dict[str, float]], s: Setup) -> bool:
    return COST_MULT * costs[session(s.t_setup)]["rt"] > GATE * abs(s.limit - s.stop)


# ---------------------------------------------------------------- setups
def fvg(b: TF, j: int) -> int:
    """+1 bullish FVG completed at bar j (low_j > high_{j-2}), -1 bearish, 0 none; bars must be consecutive."""
    if j < 2:
        return 0
    if b.open_ns[j] - b.open_ns[j - 2] != 2 * (b.open_ns[j] - b.open_ns[j - 1]):
        return 0
    if b.l[j] > b.h[j - 2]:
        return 1
    if b.h[j] < b.l[j - 2]:
        return -1
    return 0


def rs1_setups(m: M1, b5: TF, b15: TF) -> list[tuple[Setup, list[int]]]:
    """ICT silver bullet, NY AM. Returns (setup, twin window bar indices)."""
    et = pd.DatetimeIndex(b5.open_ns, tz="UTC").tz_convert(NY)
    mod = (et.hour * 60 + et.minute).to_numpy()
    date = et.normalize()
    out = []
    for d in pd.unique(date):
        d = pd.Timestamp(d)
        if d.dayofweek >= 5:
            continue
        idx = np.flatnonzero(date == d)
        ref = idx[mod[idx] <= 9 * 60 + 25]
        win = idx[(mod[idx] >= 600) & (mod[idx] <= 655)]
        if len(ref) == 0 or len(win) == 0:
            continue
        ref_h, ref_l = float(b5.h[ref].max()), float(b5.l[ref].min())
        span0 = idx[mod[idx] >= 570]
        t_exp, t_exit = ny_ns(d, 11, 0), ny_ns(d, 12, 0)
        for j in win:
            side = fvg(b5, j)
            if side == 0:
                continue
            tc = int(b5.close_ns[j])
            atr = atr_at(b15, tc)
            if not np.isfinite(atr):
                continue
            span = span0[span0 <= j]
            if side > 0:
                gap = b5.l[j] - b5.h[j - 2]
                swept = len(span) > 0 and float(b5.l[span].min()) < ref_l
                lim = float(b5.l[j])
                stop = min(float(b5.l[j - 2]), float(b5.l[j - 1])) - 0.1 * atr
            else:
                gap = b5.l[j - 2] - b5.h[j]
                swept = len(span) > 0 and float(b5.h[span].max()) > ref_h
                lim = float(b5.h[j])
                stop = max(float(b5.h[j - 2]), float(b5.h[j - 1])) + 0.1 * atr
            if gap < 0.1 * atr or not swept:
                continue
            out.append((Setup(tc, side, lim, stop, float(b5.c[j]), t_exp, t_exit, None), [int(x) for x in win]))
            break
    return out


def rs2_setups(m: M1, b1h: TF) -> list[tuple[Setup, list[int]]]:
    """Supply/demand zone, first retest (1h)."""
    o, h, lo, c, atr = b1h.o, b1h.h, b1h.l, b1h.c, b1h.atr
    rng = h - lo
    base = (np.abs(c - o) <= 0.5 * rng) & np.r_[False, rng[1:] <= atr[:-1]]   # ATR of the bars before the bar
    cl = b1h.close_ns
    day = (cl - 22 * HOUR) // (1440 * MIN)
    out = []
    for d in range(4, len(b1h)):
        a = atr[d - 1]
        if not np.isfinite(a) or a <= 0:
            continue
        body = c[d] - o[d]
        if abs(body) < 1.5 * a:
            continue
        k = 0
        while k < 3 and base[d - 1 - k] and b1h.open_ns[d - k] - b1h.open_ns[d - 1 - k] == HOUR:
            k += 1
        if k == 0:
            continue
        bs = slice(d - k, d)
        side = 1 if body > 0 else -1
        ad = atr[d]
        if side > 0:
            if c[d] <= h[bs].max():
                continue
            prox, dist = float(np.maximum(o[bs], c[bs]).max()), float(lo[bs].min())
            stop = dist - 0.1 * ad
        else:
            if c[d] >= lo[bs].min():
                continue
            prox, dist = float(np.minimum(o[bs], c[bs]).min()), float(h[bs].max())
            stop = dist + 0.1 * ad
        if abs(prox - stop) > 3 * ad or abs(prox - stop) <= 0:
            continue
        t_set = int(cl[d])
        t_exp = t_set + 120 * HOUR
        # cancelled by a 1h close beyond the distal edge before the fill: the order dies at that close
        after = slice(d + 1, min(d + 121, len(b1h)))
        beyond = np.flatnonzero(c[after] < dist) if side > 0 else np.flatnonzero(c[after] > dist)
        if len(beyond):
            t_exp = min(t_exp, int(cl[d + 1 + int(beyond[0])]))
        twin_win = [int(x) for x in np.flatnonzero(day == day[d])]
        out.append((Setup(t_set, side, prox, stop, float(c[d]), t_exp, None, 48 * HOUR), twin_win))
    return out


def rs3_setups(m: M1, b15: TF) -> list[tuple[Setup, list[int]]]:
    """Inverse FVG (15m), setup bar closing 07:00..20:00 UTC."""
    c, h, lo, atr = b15.c, b15.h, b15.l, b15.atr
    cl = b15.close_ns
    cmod = (cl // MIN) % 1440
    day = cl // (1440 * MIN)
    elig = (cmod >= 7 * 60) & (cmod <= 20 * 60)
    out = []
    for j in range(16, len(b15)):
        side_g = fvg(b15, j)
        if side_g == 0:
            continue
        a = atr[j]
        if not np.isfinite(a) or a <= 0:
            continue
        far = h[j - 2] if side_g > 0 else lo[j - 2]
        near = lo[j] if side_g > 0 else h[j]
        if abs(near - far) < 0.25 * a:
            continue
        seg = c[j + 1:j + 33]
        inv = np.flatnonzero(seg < far) if side_g > 0 else np.flatnonzero(seg > far)
        if not len(inv):
            continue
        mi = j + 1 + int(inv[0])
        if not elig[mi]:
            continue
        am = atr[mi]
        if not np.isfinite(am):
            continue
        side = -side_g                       # a failed bullish gap becomes a bearish IFVG
        if side < 0:
            stop = float(h[j:mi + 1].max()) + 0.1 * am
        else:
            stop = float(lo[j:mi + 1].min()) - 0.1 * am
        t_set = int(cl[mi])
        twin_win = [int(x) for x in np.flatnonzero((day == day[mi]) & elig)]
        out.append((Setup(t_set, side, float(far), stop, float(c[mi]), t_set + 16 * 15 * MIN, None, 8 * HOUR),
                    twin_win))
    return out


def twin_setup(rule: str, s: Setup, bars: TF, win: list[int], i: int) -> Setup | None:
    if not win:
        return None
    j = win[int(h_u(rule, s.t_setup, i, "t") * len(win))]
    side = 1 if h_u(rule, s.t_setup, i, "s") < 0.5 else -1
    t = int(bars.close_ns[j])
    close = float(bars.c[j])
    off = abs(s.close - s.limit)
    risk = abs(s.limit - s.stop)
    lim = close - side * off
    if s.t_exit_abs is not None:           # RS1: same absolute expiry and exit of the day
        return Setup(t, side, lim, lim - side * risk, close, s.t_expiry, s.t_exit_abs, None)
    life = s.t_expiry - s.t_setup if rule == "RS3" else 120 * HOUR
    return Setup(t, side, lim, lim - side * risk, close, t + life, None, s.exit_dur)


# ---------------------------------------------------------------- screen
def run_rule(args: tuple[str, str]) -> tuple[str, dict[str, Any]]:
    rule, raw = args
    m = load_m1(Path(raw))
    costs = load_costs()
    if rule == "RS1":
        bars = resample(m, 5)
        setups = rs1_setups(m, bars, resample(m, 15))
    elif rule == "RS2":
        bars = resample(m, 60)
        setups = rs2_setups(m, bars)
    else:
        bars = resample(m, 15)
        setups = rs3_setups(m, bars)
    setups.sort(key=lambda x: x[0].t_setup)
    n_setups = len(setups)
    kept = [(s, w) for s, w in setups if not gated(costs, s)]
    sims = [(s, w, simulate(m, costs, s)) for s, w in kept]
    # one real position at a time: greedy by fill time; skip a setup created or filled inside an accepted trade
    filled = sorted([x for x in sims if x[2]["filled"]], key=lambda x: x[2]["t_fill"])
    starts: list[int] = []
    ends: list[int] = []

    def inside(t: int) -> bool:          # accepted intervals are disjoint and appended in start order
        i = bisect.bisect_right(starts, t) - 1
        return i >= 0 and t <= ends[i]

    accepted_ids = set()
    for s, _, r in filled:
        if inside(s.t_setup) or inside(r["t_fill"]):
            continue
        starts.append(r["t_fill"])
        ends.append(r["t_exit"])
        accepted_ids.add(id(s))
    real, twins = [], []
    busy = 0
    for s, w, r in sims:
        if r["filled"] and id(s) not in accepted_ids:
            busy += 1
            continue
        if not r["filled"] and inside(s.t_setup):
            busy += 1
            continue
        day = s.t_setup // (1440 * MIN)
        real.append({**r, "day": day, "t_setup": s.t_setup})
        for i in range(N_TWIN):
            ts_ = twin_setup(rule, s, bars, w, i)
            tr = simulate(m, costs, ts_) if ts_ is not None else {"filled": False}
            twins.append({**tr, "day": day})
    res = stats(real, twins, np.random.default_rng(SEED))
    res.update({"setups": n_setups, "gated": n_setups - len(kept), "skipped_busy": busy,
                "unfilled": sum(1 for r in real if not r["filled"])})
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([r for r in real if r["filled"]]).to_parquet(OUT / f"{rule}_real.parquet")
    return rule, res


def stats(real: list[dict[str, Any]], twins: list[dict[str, Any]], rng: np.random.Generator) -> dict[str, Any]:
    df = pd.DataFrame([t for t in real if t["filled"]])
    if df.empty:
        return {"n": 0, "pass": False}
    r = df.R.to_numpy()
    u, inv = np.unique(df.day.to_numpy(), return_inverse=True)
    s, n = np.bincount(inv, weights=r), np.bincount(inv).astype(float)
    idx = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    lb = float(np.quantile(s[idx].sum(1) / n[idx].sum(1), 0.05))
    rz = pd.Series([t["R"] if t["filled"] else 0.0 for t in real], index=[t["day"] for t in real]).groupby(level=0).sum()
    tz = pd.Series([t["R"] if t["filled"] else 0.0 for t in twins],
                   index=[t["day"] for t in twins]).groupby(level=0).sum() / N_TWIN
    d = (rz - tz.reindex(rz.index).fillna(0.0)).to_numpy()
    di = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    dm = d[di].mean(1)
    p = float((dm <= 0).mean())
    tw = pd.DataFrame([t for t in twins if t["filled"]])
    yr = df.groupby(pd.to_datetime(df.t_fill, utc=True).dt.year).R.mean()
    out: dict[str, Any] = {
        "n": int(len(r)), "gross_mean_R": float(df.gross_R.mean()), "mean_R": float(r.mean()),
        "sd_R": float(r.std(ddof=1)), "lb90": lb, "median_risk_usd": float(df.risk_usd.median()),
        "cost_R_median": float((df.gross_R - df.R).median()),
        "twin_fill_rate": float(len(tw) / max(1, len(twins))), "real_fill_rate": float(len(df) / len(real)),
        "twin_mean_R": float(tw.R.mean()) if len(tw) else float("nan"),
        "twin_gross_R": float(tw.gross_R.mean()) if len(tw) else float("nan"),
        "diff_mean_per_day": float(d.mean()), "p_diff": p, "p_diff_bonf": min(1.0, N_RULES * p),
        "years_pos": int((yr > 0).sum()), "year_means": {str(k): round(float(v), 3) for k, v in yr.items()},
        "long_n": int((df.side > 0).sum()), "long_R": float(df.R[df.side > 0].mean()),
        "short_n": int((df.side < 0).sum()), "short_R": float(df.R[df.side < 0].mean()),
        "how": {k: float(v) for k, v in df.how.value_counts(normalize=True).items()},
    }
    out["pass"] = bool(out["n"] >= 200 and lb > 0 and out["p_diff_bonf"] < 0.10 and out["years_pos"] >= 5)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    rules = ["RS1", "RS2", "RS3"]
    with Pool(min(3, a.workers)) as pool:
        res = dict(pool.map(run_rule, [(r, str(a.raw)) for r in rules]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
