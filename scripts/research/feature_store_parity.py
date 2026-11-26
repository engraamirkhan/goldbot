"""fs-v1 parity oracle on real minutes (lifecycle N9 acceptance): for sampled decisions of one instrument x tf, the
fs-v1 row (as-of on available_utc) against
  1. the engine's frame row (`engine.frames.frame` through `Engine._context`) on every minute visible at the decision,
  2. `feature_snapshots.decision_features` with the same unbounded memory,
  3. `feature_snapshots.decision_features` as deployed (ENGINE_MINUTES memory): the train/serve skew of today,
reported per column (expected: the d1 columns, which the 90,000-minute memory leaves NaN or short).
Pass: max abs diff <= 1e-9 and an identical NaN mask for 1 and 2 on every column.

    python scripts/research/feature_store_parity.py --instrument XAUUSD --tf 15m --history-from 2014 --year 2015 \
        --n 2000 --workers 3

Market data stays local; prints a JSON summary (and writes it with --out).
"""
from __future__ import annotations

import argparse
import json
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd

from goldbot.research import feature_snapshots as fsnap
from goldbot.research import feature_store as F

ROOT = Path(__file__).resolve().parents[2]
_M: pd.DataFrame | None = None
_TF = ""
_INST = F.GOLD


def _init(minutes: pd.DataFrame, tf: str, instrument: str) -> None:
    global _M, _TF, _INST
    warnings.filterwarnings("ignore")
    _M, _TF, _INST = minutes, tf, instrument


def _engine(at: pd.Timestamp) -> dict[str, float] | None:
    from goldbot.data.resample import resample_bars
    from goldbot.engine import frames
    from goldbot.engine.runner import Engine
    assert _M is not None
    primary = object()
    book = primary if _INST == F.GOLD else object()              # books.feature_ctx: a non-gold book's symbol
    eng: Any = SimpleNamespace(book=book, primary=primary, _ctx_cache={}, _resample=resample_bars,
                               cfg=SimpleNamespace(feature_window=fsnap.FEATURE_WINDOW, instrument=_INST))
    eng._context = lambda complete, ctf, ts: Engine._context(eng, complete, ctf, ts)
    complete = _M[_M["visible_at"] <= at].reset_index(drop=True)
    fr = frames.frame(eng, complete, _TF, at)
    if fr is None:
        return None
    row = fr.X.iloc[-1]
    return {c: float(row[c]) if c in row.index else np.nan for c in F.SPEC.columns}


def _snap(at: pd.Timestamp, memory: int | None) -> dict[str, float] | None:
    assert _M is not None
    ctx = None if _INST == F.GOLD else {"symbol": _INST}
    with mock.patch.object(fsnap, "ENGINE_MINUTES", memory if memory is not None else 10**12):
        s = fsnap.decision_features(_M, _TF, at, ctx=ctx)
    if s is None:
        return None
    vals = s.features
    return {c: np.nan if (v := vals.get(c)) is None else float(v) for c in F.SPEC.columns}


def _one(job: tuple[pd.Timestamp, bool]) -> dict[str, Any]:
    at, with_snap = job
    out: dict[str, Any] = {"at": str(at), "engine": _engine(at)}
    if with_snap:
        out["snap_full"] = _snap(at, None)
        out["snap_90k"] = _snap(at, fsnap.ENGINE_MINUTES)
    return out


def _compare(mine: pd.DataFrame, other: list[dict[str, float] | None]) -> dict[str, Any]:
    # a bid-only source's spread/tick columns are NaN by contract (N7), whatever the engine makes of ask = bid
    bid_only = bool(mine["bid_only"].any())
    cols = [c for c in F.SPEC.columns if not (bid_only and c in F.BID_ONLY_MISSING)]
    worst = dict.fromkeys(cols, 0.0)
    mask = dict.fromkeys(cols, 0)
    missing = 0
    for i, o in enumerate(other):
        if o is None:
            missing += 1
            continue
        for c in cols:
            x, y = float(mine[c].iloc[i]), o[c]
            if np.isnan(x) != np.isnan(y):
                mask[c] += 1
            elif not np.isnan(x) and x != y:
                worst[c] = max(worst[c], abs(x - y))
    return {"n": len(other) - missing, "missing": missing, "max_abs_diff": max(worst.values()),
            "nan_mask_mismatches": sum(mask.values()),
            "columns_over_1e-9": {c: v for c, v in worst.items() if v > 1e-9},
            "columns_mask_differs": {c: v for c, v in mask.items() if v}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instrument", default=F.GOLD)
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--history-from", type=int, default=2014)
    ap.add_argument("--year", type=int, default=2015)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--snap-n", type=int, default=300, help="decisions also checked against decision_features")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    warnings.filterwarnings("ignore")
    m = F.history_minutes(a.instrument, a.history_from, a.year, a.raw)
    t = time.perf_counter()
    rows = F.compute(m, a.tf, instrument=a.instrument,
                     rows_from=pd.Timestamp(f"{a.year}-01-01", tz="UTC"),
                     rows_to=pd.Timestamp(f"{a.year + 1}-01-01", tz="UTC"))
    build_s = time.perf_counter() - t
    dec = pd.DatetimeIndex(rows["decision_utc"])
    rng = np.random.default_rng(a.seed)
    pick = np.sort(rng.choice(len(dec), size=min(a.n, len(dec)), replace=False))
    ats = list(dec[pick])
    # a tenth off the bar close (inside the next bar): the engine reads the last closed bar, fs-v1 its as-of row
    off = rng.random(len(ats)) < 0.1
    ats = [x + pd.Timedelta(minutes=int(rng.integers(1, max(2, F.tf_seconds(a.tf) // 60)))) if o else x
           for x, o in zip(ats, off, strict=True)]
    ats = [x for x in ats if x < pd.Timestamp(f"{a.year + 1}-01-01", tz="UTC")]
    mine = F.features_for(rows, ats)
    jobs = [(x, i < a.snap_n) for i, x in enumerate(mine["at"])]
    t = time.perf_counter()
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(m, a.tf, a.instrument)) as ex:
        res = list(ex.map(_one, jobs, chunksize=8))
    oracle_s = time.perf_counter() - t
    sn = [i for i, j in enumerate(jobs) if j[1]]
    summary = {
        "bid_only_columns_excluded": list(F.BID_ONLY_MISSING) if bool(rows["bid_only"].any()) else [],
        "instrument": a.instrument, "tf": a.tf, "year": a.year, "history_from": a.history_from,
        "rows": len(rows), "fs_v1_seconds": round(build_s, 2), "oracle_seconds": round(oracle_s, 1),
        "engine": _compare(mine, [r["engine"] for r in res]),
        "snapshot_unbounded": _compare(mine.iloc[sn].reset_index(drop=True), [res[i]["snap_full"] for i in sn]),
        "snapshot_as_deployed_90k": _compare(mine.iloc[sn].reset_index(drop=True), [res[i]["snap_90k"] for i in sn]),
    }
    txt = json.dumps(summary, indent=1, default=str)
    print(txt)
    if a.out:
        a.out.write_text(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
