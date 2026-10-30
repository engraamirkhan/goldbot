"""H-82 screen (prereg-2026-10-30-hb-exits.md): wider targets / time-only exits on the scalp arms' own entries,
paired against random-side entries within +/-60 min under the same exits. 2010-2017 only, local bars, outputs to
~/tmp/hb_exits (no market data in the repo).

Usage: .venv/bin/python docs/research/candidates/hb_exits/hb_exits_screen.py --raw <raw folder> [--parity 300]
       .venv/bin/python docs/research/candidates/hb_exits/hb_exits_read.py      (the pre-registered read)

Entries: the live rule code (`arm_rules.candidates`) on the hashed arm rows, `limit_open(max_open)` under the arm's
current exit, entry at the open of the first 1m bar at or after close + send delay (`run_arm_backtest` convention).
Exits: a vectorised walk on the 1m mid path, checked against `triple_barrier_next_open_1m` (`--parity`)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from run_arm_backtest import limit_open, mid_priced  # noqa: E402

from goldbot.data.release import read_bar_files  # noqa: E402
from goldbot.data.resample import resample_bars  # noqa: E402
from goldbot.data.timeutil import epoch_ns, utc_index  # noqa: E402
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier_next_open_1m  # noqa: E402
from goldbot.research import arm_rules as R  # noqa: E402
from goldbot.research import arm_rules_scalp as Z  # noqa: E402
from goldbot.research import arms as A  # noqa: E402

ARMS = {"S5": ["S5A", "S5B"], "Z4": ["Z4D", "Z4P"], "Z6": ["Z6D"], "Z7": ["Z7D", "Z7P"]}
VARIANTS = {"1R": 1.0, "1.5R": 1.5, "2R": 2.0, "3R": 3.0, "none": np.inf}
M_CTL, SPAN_MIN = 4, 60
START, END = pd.Timestamp("2010-01-15", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC")
MAXLEN = 40
OUT = Path.home() / "tmp" / "hb_exits"
ARMS_FILE = ROOT / "config" / "exploration_arms.yaml"


def h_u(key: str) -> float:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2 ** 64


def load_b1(raw: Path) -> pd.DataFrame:
    b = read_bar_files(raw, 2010, 2017)
    assert utc_index(b["ts_utc"]).max() < END, "a 2018+ bar was read"
    return b


def mids(b1: pd.DataFrame) -> dict[str, np.ndarray]:
    return {k: (b1[f"bid_{k}"].to_numpy(float) + b1[f"ask_{k}"].to_numpy(float)) / 2
            for k in ("open", "high", "low", "close")}


def path(m1: dict[str, np.ndarray], k0: np.ndarray, k1: np.ndarray, side: np.ndarray
         ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Favourable and adverse excursion ($) per searched minute k0..k1-1 (-inf past the end), entry = open[k0]."""
    L = int((k1 - k0).max()) if len(k0) else 1
    assert L <= MAXLEN, L
    j = k0[:, None] + np.arange(L)[None, :]
    valid = j < k1[:, None]
    jj = np.where(valid, j, 0)
    entry = m1["open"][k0]
    s = side[:, None]
    e = entry[:, None]
    fav = np.where(valid, np.where(s > 0, m1["high"][jj] - e, e - m1["low"][jj]), -np.inf)
    adv = np.where(valid, np.where(s > 0, e - m1["low"][jj], m1["high"][jj] - e), -np.inf)
    return fav, adv, entry, L


def walk(m1: dict[str, np.ndarray], k0: np.ndarray, k1: np.ndarray, side: np.ndarray, risk: np.ndarray) -> dict:
    """Per trade and target: gross R, stop flag, first-exit minute offset; plus MFE (R) before any stop. Stop first in a
    minute holding both barriers; time exit at close[k1-1]."""
    n = len(k0)
    fav, adv, entry, L = path(m1, k0, k1, side)
    r = risk[:, None]
    stop_hit = adv >= r - 1e-9
    first_stop = np.where(stop_hit.any(1), stop_hit.argmax(1), L + 1)
    time_r = side * (m1["close"][k1 - 1] - entry) / risk
    before = np.arange(L)[None, :] < first_stop[:, None]
    mfe = np.maximum(np.where(before, fav, -np.inf).max(1) / risk, 0.0)
    out: dict[str, np.ndarray] = {"mfe": mfe}
    for name, t in VARIANTS.items():
        if np.isfinite(t):
            th = fav >= t * r - 1e-9
            first_t = np.where(th.any(1), th.argmax(1), L + 1)
        else:
            first_t = np.full(n, L + 1)
        stop = (first_stop <= first_t) & (first_stop <= L)
        tgt = (~stop) & (first_t <= L)
        out[name] = np.where(stop, -1.0, np.where(tgt, t if np.isfinite(t) else 0.0, time_r))
        out[name + "_stop"] = stop
        out[name + "_at"] = np.where(stop, first_stop, np.where(tgt, first_t, (k1 - k0) - 1))
    return out


def entry_minutes(arm: A.Arm, close: pd.DatetimeIndex, t1ns: np.ndarray, idx: np.ndarray, hold: int
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(mask over idx of entries with a full window, k0, k1): k0 the first minute at or after close + send delay, k1
    the first minute at or after the close of decision bar idx + hold (the time barrier)."""
    ok = idx + hold < len(close)
    ii = idx[ok]
    delay = np.array([A.send_delay_s(arm.id, arm.timeframe, close[int(i)]) for i in ii], dtype=float)
    sent = epoch_ns((close[ii] + pd.to_timedelta(delay, unit="s")).floor("min"))
    k0 = np.searchsorted(t1ns, sent, side="left")
    k1 = np.searchsorted(t1ns, epoch_ns(close[ii + hold]), side="left")
    good = (k1 > k0) & (k0 < len(t1ns)) & (k1 <= len(t1ns) - 1)      # censored: the window must close inside 2017
    mask = np.zeros(len(idx), dtype=bool)
    mask[np.flatnonzero(ok)[good]] = True
    return mask, k0[good], k1[good]


def get_arm(arm_id: str) -> A.Arm:
    return next(a for a in A.load_arms(ARMS_FILE) if a.id == arm_id)


def run_arm(args: tuple[str, str]) -> pd.DataFrame:
    arm_id, raw = args
    arm = get_arm(arm_id)
    b1 = load_b1(Path(raw))
    bars = b1 if arm.timeframe == "1m" else resample_bars(b1, arm.timeframe).reset_index(drop=True)
    z = np.zeros(0, dtype=np.int64)
    cands = R.candidates(arm, R.RuleContext(bars=bars, b1=b1, blackout_lo=z, blackout_hi=z))
    close = utc_index(bars["visible_at"])
    t = close[cands["idx"].to_numpy()]
    cands = cands[np.asarray((t >= START) & (t < END))].reset_index(drop=True)
    hold, stop_usd, base = int(arm.params["hold_bars"]), float(arm.params["stop_usd"]), float(arm.params["target_r"])
    bname = next(k for k, v in VARIANTS.items() if v == base)
    m = mids(b1)
    t1 = utc_index(b1["ts_utc"])
    t1ns = epoch_ns(t1)
    keep, k0, k1 = entry_minutes(arm, close, t1ns, cands["idx"].to_numpy(dtype=int), hold)
    idx, side = cands["idx"].to_numpy(dtype=int)[keep], cands["side"].to_numpy(dtype=int)[keep]
    w = walk(m, k0, k1, side, np.full(len(idx), stop_usd))
    # the fixed entry set: limit_open under the CURRENT exit, positions on the decision bars (run_arm_backtest)
    exit_k = k0 + w[bname + "_at"].astype(int)
    t_exit = np.minimum(close.searchsorted(t1[exit_k], side="right"), idx + hold)
    lab = pd.DataFrame({"t_entry": idx + 1, "t_exit": t_exit, "pos": np.arange(len(idx))})
    kept = np.sort(limit_open(lab, arm.max_open)["pos"].to_numpy())
    idx, side = idx[kept], side[kept]
    aw = {k: v[kept] for k, v in w.items()}
    # random controls on the same trading day, +/-60 min, the arm's window and clock filters
    cns = epoch_ns(close)
    step = int(pd.Timedelta(arm.timeframe).total_seconds() // 60)
    grid = np.array([o for o in range(-SPAN_MIN, SPAN_MIN + 1, step) if o != 0])
    if arm.rule == "ema_pullback":
        okmask = R.arm_window(close, arm.params) & R.common_filter_ok(close, z, z)
    else:
        okmask = Z.in_w(close)
    tday = Z.trading_days(close)
    stamps = [f"{close[int(i)]:%Y-%m-%dT%H:%M}Z" for i in idx]
    cols: dict[str, np.ndarray] = {}
    for k in range(M_CTL):
        keys = [f"hb-2026-10-30|{arm.id}|{s}|{k}" for s in stamps]
        off = grid[np.array([int(h_u(x) * len(grid)) for x in keys], dtype=int)]
        rside = np.array([1 if h_u(x + "|side") < 0.5 else -1 for x in keys], dtype=int)
        tgt_ns = cns[idx] + off * 60_000_000_000
        p = np.minimum(np.searchsorted(cns, tgt_ns), len(cns) - 1)
        good = (cns[p] == tgt_ns) & okmask[p] & (tday[p] == tday[idx])
        sub0 = np.flatnonzero(good)
        cm, c0, c1 = entry_minutes(arm, close, t1ns, p[sub0], hold)
        sub = sub0[cm]
        cols[f"c{k}_ok"] = np.isin(np.arange(len(idx)), sub)
        for sd_name, sd in (("rand", rside[sub]), ("match", side[sub])):
            cw = walk(m, c0, c1, sd, np.full(len(sub), stop_usd))
            for v in VARIANTS:
                arr = np.full(len(idx), np.nan)
                arr[sub] = cw[v]
                cols[f"c{k}_{sd_name}_{v}"] = arr
                st = np.zeros(len(idx), dtype=bool)
                st[sub] = cw[v + "_stop"]
                cols[f"c{k}_{sd_name}_{v}_stop"] = st
    out = pd.DataFrame({"arm": arm.id, "ts": close[idx], "tday": tday[idx], "side": side, "risk_usd": stop_usd,
                        "base": bname, "mfe": aw["mfe"]})
    for v in VARIANTS:
        out[f"a_{v}"] = aw[v]
        out[f"a_{v}_stop"] = aw[v + "_stop"]
    out = pd.concat([out, pd.DataFrame(cols)], axis=1)
    out["n_cands"] = len(cands)
    return out


def parity(raw: Path, n: int) -> dict:
    """The walk vs the engine labeller (`triple_barrier_next_open_1m`, mid-priced, no swap), n entries per arm in 2010."""
    b1 = load_b1(raw)
    b1 = b1[utc_index(b1["ts_utc"]) < pd.Timestamp("2011-01-01", tz="UTC")].reset_index(drop=True)
    m = mids(b1)
    t1ns = epoch_ns(utc_index(b1["ts_utc"]))
    res = {}
    for arm_id in ("S5A", "Z4P", "Z7D", "Z6D"):
        arm = get_arm(arm_id)
        bars = b1 if arm.timeframe == "1m" else resample_bars(b1, arm.timeframe).reset_index(drop=True)
        z = np.zeros(0, dtype=np.int64)
        c = R.candidates(arm, R.RuleContext(bars=bars, b1=b1, blackout_lo=z, blackout_hi=z)).iloc[50:50 + n]
        close = utc_index(bars["visible_at"])
        hold, stop_usd = int(arm.params["hold_bars"]), float(arm.params["stop_usd"])
        keep, k0, k1 = entry_minutes(arm, close, t1ns, c["idx"].to_numpy(int), hold)
        c = c[keep].reset_index(drop=True)
        w = walk(m, k0, k1, c["side"].to_numpy(int), np.full(len(c), stop_usd))
        risk = pd.Series(np.full(len(bars), stop_usd), index=bars.index)
        for v, tm in VARIANTS.items():
            sig = c[["idx", "side"]].assign(max_bars=hold - 1, target_atr=tm if np.isfinite(tm) else 1e6)
            sig["entry_delay_s"] = [A.send_delay_s(arm.id, arm.timeframe, close[int(i)]) for i in sig["idx"]]
            lab = triple_barrier_next_open_1m(mid_priced(bars), mid_priced(b1), sig,
                                              BarrierSpec(target_atr=1.0, stop_atr=1.0, max_bars=1, name="p"), risk)
            eng = lab["ret"].to_numpy(float) * lab["entry"].to_numpy(float) / stop_usd
            mine = pd.Series(w[v], index=c["idx"].to_numpy()).reindex(lab["idx"].to_numpy()).to_numpy()
            d = np.abs(eng - mine)
            res[f"{arm_id}_{v}"] = {"n": int(len(lab)), "max_abs_diff_R": float(np.nanmax(d)),
                                    "n_diff_gt_1e-6": int((d > 1e-6).sum())}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--parity", type=int, default=0)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.parity:
        p = parity(Path(a.raw), a.parity)
        (OUT / "parity.json").write_text(json.dumps(p, indent=2))
        print(json.dumps(p, indent=1))
        return
    ids = [i for f in ARMS.values() for i in f]
    with ProcessPoolExecutor(max_workers=len(ids)) as ex:
        for arm_id, df in zip(ids, ex.map(run_arm, [(i, a.raw) for i in ids]), strict=True):
            df.to_parquet(OUT / f"pairs_{arm_id}.parquet", index=False)
            print(arm_id, len(df), "kept of", int(df["n_cands"].iloc[0]) if len(df) else 0, "candidates", flush=True)


if __name__ == "__main__":
    main()
