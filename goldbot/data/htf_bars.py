"""Closed higher-timeframe bars (daily, weekly) rebuilt from 1m bars: the store fs-v1 serves its d1 context from
(lifecycle N9 / W3-3; backlog 271).

Why: the engine's context frames are resampled from its own memory (ENGINE_MINUTES = 90,000 minutes, about 65
trading days), so a daily context column with a 100-bar lookback (`d1_atr_ratio_14_100`, `d1_rv_ratio`) is NaN on
every decision, in the engine and in the nightly snapshots alike. These bars carry the whole history instead.

Contract:
- Bars are exactly `data.resample.resample_bars(minutes, tf)` (the feature-day daily bar closing at the COMEX
  settlement 13:30 New York, the engine's `_context` rule; NOT the 21:00 UTC bar of raw/daily, which the 1d/1w arms
  read): one implementation, so a closed bar here equals the engine's bar on the same minutes.
- Closed only: a bar is kept when its `visible_at` is at or before `as_of` (default: the last minute's visibility).
  A minute's visibility is the later of its `visible_at` and its `available_utc` when the frame carries one (a revised
  minute), and the bar's `visible_at` carries it (resample_bars), so a revision never makes a bar visible early.
- Every row carries `available_utc` (= its `visible_at`: when it became knowable) and `source` (the minutes' sources,
  sorted and joined with '+').
- The holdout is refused (`holdout_guard.refuse`): research stores never hold a sealed minute.
- Files are written atomically (temp file + rename) under a caller's local directory, never in the repository
  (market data stays local, ADR 0048), with a JSON manifest: minutes sha256, rows, first/last bar.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data import holdout_guard
from goldbot.data.resample import BAR_COLUMNS, resample_bars
from goldbot.data.timeutil import epoch_ns, utc_index

HTF_TFS = ("1d", "1w")
_PRICE_COLUMNS = [c for c in BAR_COLUMNS if c not in ("ts_utc", "visible_at")]


def effective_minutes(minutes: pd.DataFrame) -> pd.DataFrame:
    """The minutes sorted by time with `visible_at` = max(visible_at, available_utc): the moment each minute was
    really knowable (a history minute: its close; a revised one: its write time)."""
    m = minutes.sort_values("ts_utc", kind="stable").reset_index(drop=True)
    vis = utc_index(m["visible_at"]).as_unit("ns")
    if "available_utc" in m.columns:
        avail = utc_index(m["available_utc"]).as_unit("ns")
        vis = pd.DatetimeIndex(np.maximum(epoch_ns(vis), epoch_ns(avail))).tz_localize("UTC")
    m = m.copy()
    m["ts_utc"] = utc_index(m["ts_utc"]).as_unit("ns")
    m["visible_at"] = vis
    return m


def minutes_sha(minutes: pd.DataFrame) -> str:
    """sha256 of the minutes' times (open and effective visibility, epoch ns) and price/volume columns: changes with
    any revised value or availability, whatever the timestamp unit."""
    m = effective_minutes(minutes)
    h = hashlib.sha256()
    h.update(epoch_ns(utc_index(m["ts_utc"])).tobytes())
    h.update(epoch_ns(utc_index(m["visible_at"])).tobytes())
    for c in _PRICE_COLUMNS:
        h.update(c.encode())
        h.update(np.ascontiguousarray(m[c].to_numpy(dtype=float)).tobytes())
    return h.hexdigest()


def _source(minutes: pd.DataFrame) -> str:
    if "source" not in minutes.columns:
        return "unknown"
    return "+".join(sorted({str(s) for s in minutes["source"].dropna().unique()})) or "unknown"


def closed_bars(minutes: pd.DataFrame, tf: str, *, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """The closed `tf` bars (1d or 1w) of `minutes` known at `as_of` (default: the last minute's visibility), with
    `available_utc` and `source`. Refuses holdout minutes."""
    if tf not in HTF_TFS:
        raise ValueError(f"closed_bars builds {HTF_TFS}, not {tf!r}")
    if minutes.empty:
        return pd.DataFrame(columns=[*BAR_COLUMNS, "available_utc", "source"])
    holdout_guard.refuse(minutes, what=f"closed {tf} bars")
    m = effective_minutes(minutes)
    cut = pd.Timestamp(as_of).tz_convert("UTC") if as_of is not None else pd.Timestamp(m["visible_at"].max())
    m = m[m["visible_at"] <= cut]
    bars = resample_bars(m[BAR_COLUMNS], tf)
    vis = utc_index(bars["visible_at"]).as_unit("ns")
    bars = bars[np.asarray(vis <= cut)].reset_index(drop=True)
    bars["available_utc"] = utc_index(bars["visible_at"]).as_unit("ns")
    bars["source"] = _source(m)
    return bars


def path_for(root: Path, instrument: str, tf: str) -> Path:
    return Path(root) / "htf" / instrument / f"{instrument}_{tf}.parquet"


def write(root: Path, instrument: str, tf: str, minutes: pd.DataFrame) -> Path:
    """Rebuild the closed `tf` bars of `minutes` and write them (atomically) with a manifest. Idempotent: the same
    minutes give the same file and manifest (bar content; `built_utc` is the only field that moves)."""
    p = path_for(root, instrument, tf)
    p.parent.mkdir(parents=True, exist_ok=True)
    sha = minutes_sha(minutes)
    man = p.with_suffix(".json")
    if p.exists() and man.exists() and read_manifest(p).get("minutes_sha256") == sha:
        return p
    bars = closed_bars(minutes, tf)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    bars.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    meta: dict[str, Any] = {"instrument": instrument, "tf": tf, "minutes_sha256": sha, "rows": len(bars),
                            "first_bar_utc": str(bars["ts_utc"].min()) if len(bars) else None,
                            "last_visible_utc": str(bars["visible_at"].max()) if len(bars) else None,
                            "source": _source(minutes), "built_utc": str(pd.Timestamp.now(tz="UTC"))}
    tmpm = man.with_name(man.name + f".tmp{os.getpid()}")
    tmpm.write_text(json.dumps(meta, indent=1, sort_keys=True))
    os.replace(tmpm, man)
    return p


def read(path: Path) -> pd.DataFrame:
    b = pd.read_parquet(path)
    for c in ("ts_utc", "visible_at", "available_utc"):
        b[c] = utc_index(b[c]).as_unit("ns")
    return b


def read_manifest(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(Path(path).with_suffix(".json").read_text())
    return out


__all__ = ["HTF_TFS", "closed_bars", "effective_minutes", "minutes_sha", "path_for", "read", "read_manifest", "write"]
