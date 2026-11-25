"""fs-v1: the vectorised point-in-time feature store (lifecycle N9; critical path of the W3-1 dense factory).

What it replaces as the BUILD path: `history_replay.features_at` / `feature_snapshots.decision_features` rebuild the
engine's frame once per decision (about 5 decisions/s/worker: resample of 90,000 minutes, 25 features on 800 bars,
three context frames), and their cache keys hash the decision set, so an arm change recomputes whole years. fs-v1
computes the sr-v2 input set (`self_retrain.FEATURES`, 39 market columns; `side` is added per candidate by
`self_retrain.design`, 40 inputs) for EVERY closed bar of an instrument x timeframe in one pass from 1m bars, and
serves any decision by an as-of join on `available_utc`. `features_at` stays the reference; the parity oracle
(tests/test_feature_store.py, scripts/research/feature_store_parity.py) holds the two equal.

The contract (spec `SPEC`, id fs-v1):
- Decision frame = the engine's: the closed decision bars visible at the decision (`resample_bars`, mid prices), its
  last `feature_window` (800) bars, no row before `min_bars` (120) bars (engine/frames.py). A decision's row is the
  row of the last decision bar whose `available_utc` is at or before it (`features_for`, `data.store.asof_join`).
- Most columns are window-independent once their own lookback is filled (measured: <= 1e-12 between a full-history
  pass and the 800-bar window), so one pass over the whole span gives them. Two are not (`WINDOW_EXACT`): Wilder ATR
  100 in `atr_ratio_14_100` (about 1e-3 apart) and `bb_z_20` (pandas' rolling std carries its window's start in its
  rounding, about 1e-8); `window_exact` rebuilds them per 800-bar window in batches, bit for bit.
- Context: the d1 columns come from the closed daily bars of the WHOLE history (`data.htf_bars`), merged with the
  engine's rule (`features.mtf.merge_higher_tf`: the last daily bar visible at the decision bar's open; none with fewer
  than `min_context_bars` daily bars visible at the decision close). This is the one intended difference from the
  engine as deployed: its 90,000-minute memory leaves `d1_atr_ratio_14_100` and `d1_rv_ratio` NaN on every decision
  (backlog 271). The oracle compares against the engine frame on the same, complete, minutes. Serving these columns
  live needs the engine/snapshots to read the same store (W3-2), not done here.
- Point in time: a minute counts from max(visible_at, available_utc) (a revised minute from its write time). A bar
  holding a minute visible after its close is no decision point and no context bar (`drop_late`): the revised value
  never enters any window. A row's `available_utc` is the latest visibility over the bars its window and context
  read; `features_for` serves the last available row and NaN when that row is more than STALE_BARS bars old.
  History minutes: `available_utc` = the bar close, and every row is available at its close.
- Partitions: one (instrument, tf, year) per cache file, computed on the year's minutes plus a warm-up of
  `feature_window + warmup_bars` decision bars (or the whole history when shorter). Cache key = sha256 of the spec,
  the code hash (feature, resample and store code), instrument, tf, year and the data (the span's minutes and the
  daily bars it reads). No decision enters a key: an arm change recomputes 0 feature years.
- Holdout: minutes in it are refused (`holdout_guard.refuse`); a year is cut at its start.
- `load` reads a partition only under the key its data gives (never the newest file), runs the holdout guard on
  decisions and window starts, and applies the tiers (r11-v2.1, ADR 0085 A4): intraday A/A-consistent, 1d/1w also B,
  C/F/P/ungraded never. The tiers must grade the rows' own source, or be named as a recorded proxy. A judged read
  is phase 0 and tiered; "parity" is the only purpose that may skip tiers. A row's tier is the session month of its
  bar's last instant; `tier_window` is the worst tier its 800-bar window reads (provenance, not a filter).
- Labels, folds and serving (ADR 0103): labels start strictly after `decision_utc`; folds `purge` on
  [window_from_utc, label end] overlap plus one horizon of embargo; `serve_ready` gates 4h/1d/1w and any d1_ column on
  the deployed serving path's parity; `columns_for` drops the spread/tick columns from pooled bid-only + bid/ask rows.

Market data and the cache stay local (~/tmp or the VM's data dir), never committed or published (ADR 0048).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.ndimage import maximum_filter1d

from goldbot.base import FrozenRecord
from goldbot.config import tf_seconds
from goldbot.data import holdout_guard
from goldbot.data import htf_bars as H
from goldbot.data.daily import session_date
from goldbot.data.resample import BAR_COLUMNS, mid, resample_bars
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, feature_day, floor_tf, utc_index
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.features.registry import feature_version
from goldbot.research import feature_snapshots as fsnap
from goldbot.research.self_retrain import FEATURES as SR_FEATURES

log = logging.getLogger("goldbot.feature_store")

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path.home() / "tmp" / "feature-store"
GOLD = "XAUUSD"


class FeatureSpec(FrozenRecord):
    """What a feature row is: its columns, the registered features that produce them, the engine's window rules."""
    spec_id: str
    columns: tuple[str, ...]
    decision_features: tuple[str, ...]       # registry names computed on the decision frame
    context_features: tuple[str, ...]        # registry names computed on each context frame
    context_tfs: tuple[str, ...]             # context timeframes the columns read (d1 for sr-v2)
    feature_window: int                      # engine EngineConfig.feature_window
    min_bars: int                            # engine/frames.py MIN_BARS
    min_context_bars: int                    # Engine._context: fewer context bars, no context columns
    window_exact: tuple[str, ...]            # columns rebuilt per window (window-start dependent)
    warmup_bars: int                         # decision bars before a partition's first row, beyond feature_window


WINDOW_EXACT: tuple[str, ...] = ("atr_ratio_14_100", "bb_z_20")
SPEC = FeatureSpec(
    spec_id="fs-v1",
    columns=SR_FEATURES,
    decision_features=("expected_move", "microstructure", "breakout", "atr", "realised_vol", "vol_estimators", "jumps",
                       "session", "returns", "tsmom", "trend_strength", "regime_stats", "moving_averages",
                       "mean_reversion", "session_zones"),
    context_features=("atr", "moving_averages", "realised_vol"),
    context_tfs=("1d",),
    feature_window=fsnap.FEATURE_WINDOW,
    min_bars=fsnap.MIN_BARS,
    min_context_bars=fsnap.MIN_CONTEXT_BARS,
    window_exact=WINDOW_EXACT,
    warmup_bars=600,
)
ROW_META = ("instrument", "tf", "phase", "group_id", "decision_utc", "bar_open_utc", "available_utc",
            "window_from_utc", "n_bars", "ctx_bars", "source", "bid_only")
# columns that read the ask, the spread or the tick count: a bid-only source (HistData) has none of them, so they stay
# NaN with `bid_only` set, never filled from another source or from live (N7)
BID_ONLY_MISSING: tuple[str, ...] = ("em_atr_cost", "em_yz_4_cost", "em_yz_16_cost", "em_yz_48_cost", "spread_atr",
                                     "spread_rel_median_48", "tick_count_z_48", "tick_vol_ratio_20", "dist_vwap48_atr")

# ------------------------------------------------------------------------------------------------- hashes
# the code a row depends on: the static import closure (module- and function-level `goldbot.*` imports) of this
# module, plus every feature module (registered by goldbot.features at import) (quant L4)
CODE_ROOTS: tuple[str, ...] = ("goldbot/research/feature_store.py",)
CODE_DIRS: tuple[str, ...] = ("goldbot/features",)
# modules fs-v1 reads only constants from, all captured in `spec_hash` (FEATURES, FEATURE_WINDOW, MIN_BARS,
# MIN_CONTEXT_BARS): hashing them, and walking on through them, would make every unrelated repo change recompute
# every feature year
CLOSURE_STOP: frozenset[str] = frozenset({"goldbot/research/self_retrain.py", "goldbot/research/feature_snapshots.py"})


def _module_file(mod: str) -> Path | None:
    base = ROOT / Path(*mod.split("."))
    for p in (base.with_suffix(".py"), base / "__init__.py"):
        if p.is_file():
            return p
    return None


LAZY_FOLLOW: tuple[str, ...] = ("goldbot.features", "goldbot.data")


def code_files() -> list[Path]:
    """The goldbot source files a feature row depends on (quant L4): the import closure of CODE_ROOTS and every
    feature module, following module-level imports everywhere and function-level (lazy) imports into
    goldbot.features / goldbot.data only (a lazy import elsewhere is a job or report path, not a value a row reads;
    following those reaches the whole repo and would recompute every year on any change). CLOSURE_STOP modules are
    left out (their values are in the spec hash)."""
    import ast
    todo = [ROOT / f for f in CODE_ROOTS] + sorted(p for d in CODE_DIRS for p in (ROOT / d).glob("*.py"))
    seen: set[Path] = set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        if p.relative_to(ROOT).as_posix() in CLOSURE_STOP:
            continue
        tree = ast.parse(p.read_text())
        top = {id(n) for n in tree.body}
        for n in ast.walk(tree):
            names: list[str] = []
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("goldbot"):
                names = [n.module, *(f"{n.module}.{a.name}" for a in n.names)]
            elif isinstance(n, ast.Import):
                names = [a.name for a in n.names if a.name.startswith("goldbot")]
            if id(n) not in top:
                names = [m for m in names if m.startswith(LAZY_FOLLOW)]
            for mod in names:
                f = _module_file(mod)
                if f is not None and f not in seen:
                    todo.append(f)
    return sorted(q for q in seen if q.relative_to(ROOT).as_posix() not in CLOSURE_STOP)


def _blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@lru_cache(maxsize=1)
def _code_hash() -> str:
    files = code_files()
    return hashlib.sha256("|".join(f"{p.relative_to(ROOT).as_posix()}:{_blob_sha(p)}" for p in files)
                          .encode()).hexdigest()


def code_hash() -> str:
    """sha256 over the git blob SHAs of `code_files()` (the import closure, holdout guard and config included): a
    changed feature, bar rule, guard or setting never reads a stale cached row."""
    return _code_hash()


def spec_hash(spec: FeatureSpec = SPEC) -> str:
    return hashlib.sha256(json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def _ctx_of(instrument: str) -> dict[str, Any] | None:
    """The engine's feature context (books.feature_ctx): None for gold, its symbol for any other instrument."""
    return None if instrument == GOLD else {"symbol": instrument}


# ------------------------------------------------------------------------------------------------- the pass
def _ns(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    return epoch_ns(utc_index(pd.Series(ts)) if not isinstance(ts, pd.DatetimeIndex) else ts)


def _to_utc(ns: np.ndarray) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(ns, dtype=np.int64), unit="ns", utc=True))


def window_exact(m: pd.DataFrame, idx: np.ndarray, window: int, *, chunk: int = 4096) -> dict[str, np.ndarray]:
    """`WINDOW_EXACT` columns at decision bars `idx` of the mid bars `m`, each computed on its own window
    m[i - window + 1 .. i] with the feature code's own operations (technical.f_atr, technical.f_mr) column-wise on a
    (window x decisions) block, so the result is the engine's bit for bit. Every i must be >= window - 1."""
    idx = np.asarray(idx, dtype=np.int64)
    if len(idx) and int(idx.min()) < window - 1:
        raise ValueError("window_exact needs a full window before every row")
    h, lo, c = (m[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    prev = np.concatenate(([np.nan], c[:-1])) if len(c) else c
    tr = np.fmax(np.fmax(h - lo, np.abs(h - prev)), np.abs(lo - prev))
    hl = np.fmax(np.fmax(h - lo, np.nan), np.nan)          # a window's first bar has no previous close
    out = {k: np.full(len(idx), np.nan) for k in WINDOW_EXACT}
    steps = np.arange(window, dtype=np.int64)[:, None]
    for a in range(0, len(idx), chunk):
        sel = idx[a:a + chunk]
        rows = (sel - window + 1)[None, :] + steps
        trw = tr[rows]
        trw[0, :] = hl[sel - window + 1]
        trf = pd.DataFrame(trw)
        a14 = trf.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()[-1]
        a100 = trf.ewm(alpha=1 / 100, adjust=False, min_periods=100).mean().to_numpy()[-1]
        out["atr_ratio_14_100"][a:a + chunk] = (pd.Series(a14) / pd.Series(a100)).to_numpy()
        cw = pd.DataFrame(c[rows])
        mu = cw.rolling(20, min_periods=20).mean().to_numpy()[-1]
        sd = cw.rolling(20, min_periods=20).std().to_numpy()[-1]
        sd = pd.Series(sd).replace(0, np.nan).to_numpy()
        out["bb_z_20"][a:a + chunk] = (c[sel] - mu) / sd
    return out


def _trailing_max(x: np.ndarray, window: int) -> np.ndarray:
    """max of x[i - window + 1 .. i] (exact on int64)."""
    if not len(x):
        return x
    return np.asarray(maximum_filter1d(x, size=window, origin=(window - 1) // 2, mode="nearest"))


def _context(dec: pd.DataFrame, X: pd.DataFrame, hb: pd.DataFrame, ctf: str, spec: FeatureSpec,
             ctx: dict[str, Any] | None) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """(context columns per decision bar, context bars visible at each decision close, the availability (ns) of
    the context row each decision reads; -1 where none)."""
    label = TF_LABEL[ctf]
    names = [c for c in spec.columns if c.startswith(f"{label}_")]
    n = len(dec)
    if hb.empty:
        return pd.DataFrame(np.nan, index=range(n), columns=names), np.zeros(n, np.int64), np.full(n, -1, np.int64)
    hb = hb.reset_index(drop=True)
    hf = build_features(mid(hb), list(spec.context_features), ctx=ctx)
    hf = hf[["ts_utc", *[c[len(label) + 1:] for c in names]]].copy()
    merged = merge_higher_tf(X[["ts_utc"]].copy(), hf, hb, label).reset_index(drop=True)
    vis = _ns(hb["visible_at"])
    order = np.argsort(vis, kind="stable")
    seen = np.searchsorted(vis[order], _ns(dec["visible_at"]), side="right")
    cols = merged[names].astype(float)
    few = seen < spec.min_context_bars
    cols.loc[few, :] = np.nan
    # the context row merged at each decision bar's open is the last bar (visible_at order) visible then; its
    # availability is the latest visibility of every context bar up to it (its features read all of them)
    cum = np.maximum.accumulate(_ns(hb["available_utc"]) if "available_utc" in hb else vis)
    pos = np.searchsorted(vis[order], _ns(X["ts_utc"]), side="right") - 1
    avail_ns = np.where((pos >= 0) & ~few, cum[order[np.clip(pos, 0, None)]], -1).astype(np.int64)
    return cols, seen.astype(np.int64), avail_ns


def compute(minutes: pd.DataFrame, tf: str, *, spec: FeatureSpec = SPEC, instrument: str = GOLD,
            htf: dict[str, pd.DataFrame] | None = None, rows_from: pd.Timestamp | None = None,
            rows_to: pd.Timestamp | None = None, history_start: bool = True, phase: int = 0) -> pd.DataFrame:
    """Every closed `tf` bar's feature row from 1m bars, in one pass (see the module doc). `minutes` start at the
    instrument's history start (`history_start`) or hold a warm-up of `feature_window + warmup_bars` decision bars
    before `rows_from`. `htf`: closed context bars by timeframe (default: built from `minutes`). Rows whose decision
    bar closes in [rows_from, rows_to) only, when given. `phase` (minutes, intraday only): offset candles for
    TRAINING (each decision bar opens `phase` minutes after the clock grid); phase 0 is the engine's grid, the only
    one parity holds for and the only one a judged read may load (`load`)."""
    holdout_guard.refuse(minutes, what="fs-v1 feature minutes")
    m1 = H.effective_minutes(minutes)
    ctx = _ctx_of(instrument)
    W = spec.feature_window
    sec = tf_seconds(tf)
    if phase and not (0 < phase * 60 < sec and sec < tf_seconds("1d")):
        raise ValueError(f"phase {phase} min is not an offset inside an intraday {tf} bar")
    bid_only = bool(minutes["bid_only"].any()) if "bid_only" in minutes.columns else False
    shift = pd.Timedelta(minutes=phase)
    src = m1[BAR_COLUMNS] if not phase else m1[BAR_COLUMNS].assign(ts_utc=m1["ts_utc"] - shift,
                                                                    visible_at=m1["visible_at"] - shift)
    # a bar holding a minute visible after the bar's close (a revision) is no decision point (quant L1): no decision
    # could have read its values at its close, and dropping it keeps the revised value out of every window
    dec = resample_bars(src, tf, drop_late=True)
    if phase:
        dec["ts_utc"] = utc_index(dec["ts_utc"]) + shift
        dec["visible_at"] = utc_index(dec["visible_at"]) + shift
    if len(m1):
        dec = dec[_ns(dec["visible_at"]) <= int(_ns(m1["visible_at"]).max())].reset_index(drop=True)
    n = len(dec)
    first = spec.min_bars - 1 if history_start else W - 1 + spec.warmup_bars
    keep = np.arange(n) >= first
    vis = _ns(dec["visible_at"]) if n else np.zeros(0, np.int64)
    if rows_from is not None:
        keep &= vis >= pd.Timestamp(rows_from).value
    if rows_to is not None:
        keep &= vis < pd.Timestamp(rows_to).value
    out_cols = [*ROW_META, *spec.columns]
    if not keep.any():
        return _typed(pd.DataFrame(columns=out_cols))
    m = mid(dec)
    X = build_features(m, list(spec.decision_features), ctx=ctx)
    idx = np.flatnonzero(keep)
    data: dict[str, Any] = {}
    exact_rows = idx[idx >= W - 1]
    exact = window_exact(m, exact_rows, W) if len(exact_rows) else {}
    for c in spec.columns:
        if "_" in c and c.split("_", 1)[0] in TF_LABEL.values():
            continue
        v = X[c].to_numpy(dtype=float)[idx].copy()
        if c in exact and len(exact_rows):
            v[idx >= W - 1] = exact[c]
        if bid_only and c in BID_ONLY_MISSING:
            v[:] = np.nan
        data[c] = v
    avail = _trailing_max(vis, W)[idx]
    ctx_bars = np.zeros(len(idx), np.int64)
    for ctf in spec.context_tfs:
        label = TF_LABEL[ctf]
        names = [c for c in spec.columns if c.startswith(f"{label}_")]
        if ctf not in context_tfs(tf):                       # a 1d/1w decision frame has no d1 context
            for c in names:
                data[c] = np.full(len(idx), np.nan)
            continue
        hb = (htf or {}).get(ctf)
        if hb is None:
            hb = H.closed_bars(minutes, ctf)
        cols, seen, cav = _context(dec, X, hb, ctf, spec, ctx)
        for c in names:
            data[c] = cols[c].to_numpy(dtype=float)[idx]
        avail = np.maximum(avail, cav[idx])
        ctx_bars = seen[idx]
    start = np.maximum(idx - W + 1, 0)
    rows = pd.DataFrame({
        "instrument": instrument, "tf": tf, "phase": phase,
        # the phase-0 bar whose close is at or after this one's: rows of all phases of one moment share it (group
        # folds and n_eff count it once)
        "group_id": -(-vis[idx] // (sec * 10**9)) * (sec * 10**9),
        "decision_utc": _to_utc(vis[idx]),
        "bar_open_utc": _to_utc(_ns(dec["ts_utc"])[idx]),
        "available_utc": _to_utc(avail),
        "window_from_utc": _to_utc(_ns(dec["ts_utc"])[start]),
        "n_bars": np.minimum(idx + 1, W).astype(np.int64),
        "ctx_bars": ctx_bars,
        "source": H._source(m1), "bid_only": bid_only,
    })
    for c in spec.columns:
        rows[c] = data[c]
    return _typed(rows[out_cols])


def _typed(rows: pd.DataFrame) -> pd.DataFrame:
    for c in ("decision_utc", "bar_open_utc", "available_utc", "window_from_utc"):
        rows[c] = utc_index(rows[c]).as_unit("ns") if len(rows) else pd.DatetimeIndex([], tz="UTC").as_unit("ns")
    for c in ("n_bars", "ctx_bars", "phase", "group_id"):
        rows[c] = rows[c].astype(np.int64)
    rows["bid_only"] = rows["bid_only"].astype(bool)
    for c in ("instrument", "tf", "source"):
        rows[c] = rows[c].astype(str)
    for c in rows.columns[len(ROW_META):]:
        rows[c] = rows[c].astype(float)
    return rows.reset_index(drop=True)


STALE_BARS = 2          # a row older than this many decision bars at the decision is not served (quant L1)


def features_for(rows: pd.DataFrame, decisions: Iterable[pd.Timestamp] | pd.DatetimeIndex) -> pd.DataFrame:
    """The row each decision reads: the last row of `rows` (ONE instrument x tf x phase; mixed phases are refused)
    with `available_utc` at or before it (`data.store.asof_join`, the repo's one join on availability). One row per
    distinct decision, in time order, `at` = the decision. Feature columns are NaN where no row was available yet
    or the available row closed more than STALE_BARS decision bars before the decision (a revision held the
    newer rows back); `attrs["stale"]` counts those."""
    if len(rows) and (rows["phase"].nunique() > 1 or rows["tf"].nunique() > 1 or rows["instrument"].nunique() > 1):
        raise ValueError("features_for joins one instrument x tf x phase at a time")
    dec = pd.DatetimeIndex(pd.to_datetime(pd.Series(list(decisions)), utc=True)).unique().sort_values()
    right = rows.assign(_avail=rows["available_utc"]).rename(columns={"available_utc": "_row_available"})
    j = asof_join(pd.DataFrame({"ts_utc": dec}), right, available_col="_avail")
    j = j.rename(columns={"ts_utc": "at", "_row_available": "available_utc"})
    stale = np.zeros(len(j), dtype=bool)
    if len(rows) and len(j):
        limit = STALE_BARS * tf_seconds(str(rows["tf"].iloc[0])) * 10**9
        got = j["decision_utc"].notna().to_numpy()
        age = np.where(got, _ns(dec) - np.where(got, _ns(pd.DatetimeIndex(j["decision_utc"].where(got, dec))), 0), 0)
        stale = got & (age > limit)
        j.loc[stale, [c for c in rows.columns if c not in ROW_META]] = np.nan
    j.attrs["stale"] = int(stale.sum())
    return j


# ------------------------------------------------------------------------------------------------- partitions
def _year_bounds(year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    lo = pd.Timestamp(f"{year}-01-01", tz="UTC")
    hi = min(pd.Timestamp(f"{year + 1}-01-01", tz="UTC"), holdout_guard.HOLDOUT_START) \
        if lo < holdout_guard.HOLDOUT_START else pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
    if holdout_guard.in_holdout(pd.DatetimeIndex([lo])).any():
        raise holdout_guard.HoldoutSealed(f"fs-v1 year {year} starts inside the sealed holdout: refused")
    return lo, hi


def _bucket_keys(ts_ns: np.ndarray, tf: str) -> np.ndarray:
    idx = _to_utc(ts_ns)
    if tf == "1d":
        return epoch_ns(feature_day(idx))
    return epoch_ns(floor_tf(idx, tf_seconds(tf)))


@dataclass(frozen=True)
class Span:
    minutes: pd.DataFrame
    history_start: bool
    lo: pd.Timestamp
    hi: pd.Timestamp


def year_span(minutes: pd.DataFrame, tf: str, year: int, spec: FeatureSpec = SPEC) -> Span:
    """The minutes a (tf, year) partition reads: visible by the year's end (or the holdout start), from the open of
    the decision bar `feature_window + warmup_bars` bars before the year's first (or the whole history)."""
    lo, hi = _year_bounds(year)
    m = H.effective_minutes(minutes)
    vis = _ns(m["visible_at"])
    m = m[vis <= hi.value].reset_index(drop=True)
    ts = _ns(m["ts_utc"])
    keys = np.unique(_bucket_keys(ts[ts < lo.value], tf))
    need = spec.feature_window + spec.warmup_bars
    if len(keys) <= need:
        return Span(m, True, lo, hi)
    start = int(keys[-need - 1])
    return Span(m[ts >= start].reset_index(drop=True), False, lo, hi)


def _htf_for(minutes: pd.DataFrame, spec: FeatureSpec, hi: pd.Timestamp) -> dict[str, pd.DataFrame]:
    m = H.effective_minutes(minutes)
    m = m[_ns(m["visible_at"]) <= hi.value]
    return {ctf: H.closed_bars(m, ctf) for ctf in spec.context_tfs}


def _frame_sha(frame: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for c in frame.columns:
        h.update(str(c).encode())
        col = frame[c]
        if isinstance(col.dtype, pd.DatetimeTZDtype):
            h.update(epoch_ns(utc_index(col)).tobytes())
        elif col.dtype.kind in "fiu":
            h.update(np.ascontiguousarray(col.to_numpy(dtype=float)).tobytes())
        else:
            h.update("\x1f".join(map(str, col.tolist())).encode())
    return h.hexdigest()


def _key(span: Span, htf: dict[str, pd.DataFrame], *, instrument: str, tf: str, year: int,
         spec: FeatureSpec, phase: int = 0) -> tuple[str, dict[str, Any]]:
    parts = {"spec_id": spec.spec_id, "spec_hash": spec_hash(spec), "code_hash": code_hash(), "instrument": instrument,
             "tf": tf, "year": year, "phase": phase, "minutes_sha256": H.minutes_sha(span.minutes),
             "htf_sha256": hashlib.sha256("|".join(_frame_sha(htf[k]) for k in sorted(htf)).encode()).hexdigest(),
             "history_start": span.history_start}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest(), parts


def cache_key(minutes: pd.DataFrame, *, instrument: str, tf: str, year: int, spec: FeatureSpec = SPEC,
              phase: int = 0) -> str:
    """The partition's key: spec, code, instrument, tf, phase, year and the data it reads. Never a decision."""
    span = year_span(minutes, tf, year, spec)
    return _key(span, _htf_for(minutes, spec, span.hi), instrument=instrument, tf=tf, year=year, spec=spec,
                phase=phase)[0]


def _part_dir(cache_dir: Path, spec: FeatureSpec, instrument: str, tf: str, year: int, phase: int = 0) -> Path:
    return Path(cache_dir) / spec.spec_id / instrument / tf / f"p{phase}" / str(year)


def build_year(minutes: pd.DataFrame, *, instrument: str, tf: str, year: int, cache_dir: Path = CACHE,
               spec: FeatureSpec = SPEC, htf: dict[str, pd.DataFrame] | None = None, phase: int = 0) -> pd.DataFrame:
    """The (instrument, tf, year) partition from the cache, or computed and written (atomically, with a manifest).
    `minutes`: the instrument's 1m history from its start (the daily context reads all of it)."""
    span = year_span(minutes, tf, year, spec)
    if htf is None:
        htf = _htf_for(minutes, spec, span.hi)
    else:
        htf = {k: v[_ns(v["visible_at"]) <= span.hi.value].reset_index(drop=True) for k, v in htf.items()}
    key, parts = _key(span, htf, instrument=instrument, tf=tf, year=year, spec=spec, phase=phase)
    d = _part_dir(cache_dir, spec, instrument, tf, year, phase)
    p = d / f"{key[:24]}.parquet"
    if p.exists() and p.with_suffix(".json").exists():
        return _typed(pd.read_parquet(p))
    t0 = time.perf_counter()
    rows = compute(span.minutes, tf, spec=spec, instrument=instrument, htf=htf, rows_from=span.lo, rows_to=span.hi,
                   history_start=span.history_start, phase=phase)
    secs = time.perf_counter() - t0
    d.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    rows.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    man = {**parts, "key": key, "rows": len(rows), "seconds": round(secs, 3), "minutes": len(span.minutes),
           "span_from_utc": str(span.minutes["ts_utc"].min()) if len(span.minutes) else None,
           "feature_version": feature_version(list(spec.decision_features)),
           "context_feature_version": feature_version(list(spec.context_features)),
           "built_utc": str(pd.Timestamp.now(tz="UTC"))}
    tmpm = p.with_suffix(".json").with_name(p.stem + f".json.tmp{os.getpid()}")
    tmpm.write_text(json.dumps(man, indent=1, sort_keys=True))
    os.replace(tmpm, p.with_suffix(".json"))
    return rows


def read_manifests(cache_dir: Path, spec: FeatureSpec = SPEC) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((Path(cache_dir) / spec.spec_id).rglob("*.json"))]


# the per-row cost of a timeframe (rows per year are about 1/tf seconds, the window-exact rebuild dominates)
def job_order(jobs: Sequence[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """(instrument, tf, year) jobs, longest first (most rows per year first), so a pool never ends on its slowest."""
    return sorted(jobs, key=lambda j: (tf_seconds(j[1]), j[0], j[2]))


def build(minutes: pd.DataFrame, *, instrument: str, tf: str, years: Iterable[int], cache_dir: Path = CACHE,
          spec: FeatureSpec = SPEC, phase: int = 0) -> pd.DataFrame:
    """Every year's partition (cached), concatenated. The daily context bars are built once."""
    years = sorted(years)
    hi = _year_bounds(years[-1])[1]
    htf = _htf_for(minutes, spec, hi)
    parts = [build_year(minutes, instrument=instrument, tf=tf, year=y, cache_dir=cache_dir, spec=spec, htf=htf,
                        phase=phase) for y in years]
    return _typed(pd.concat(parts, ignore_index=True)) if parts else _typed(pd.DataFrame(columns=[*ROW_META,
                                                                                                    *spec.columns]))


# ------------------------------------------------------------------------------------------------- sources
HISTDATA_DIR = "histdata_m1"


def history_minutes(instrument: str, first_year: int, last_year: int, raw_dir: Path) -> pd.DataFrame:
    """An instrument's research 1m history, ONE source per instrument (A16 precedence): XAUUSD from the Dukascopy
    yearly files (bid+ask, ticks, spread; `release.read_bar_files`), every other instrument from HistData
    (raw/histdata_m1/<KEY>/<key>_1m_bid_<year>.parquet: bid only, so ask = bid, spread and ticks unknown and the
    `BID_ONLY_MISSING` columns NaN with `bid_only`). Error-flagged minutes are dropped (quarantined at ingest), the
    holdout is cut (`holdout_guard.cut`), `source` is kept."""
    from goldbot.data.release import read_bar_files
    raw_dir = Path(raw_dir)
    if instrument == GOLD:
        b = read_bar_files(raw_dir, first_year, last_year)
        b = holdout_guard.cut(b, what="fs-v1 gold minutes")
        return b.assign(source="dukascopy", bid_only=False)
    files = [raw_dir / HISTDATA_DIR / instrument / f"{instrument.lower()}_1m_bid_{y}.parquet"
             for y in range(first_year, last_year + 1)]
    frames = [pd.read_parquet(f) for f in files if f.exists()]
    if not frames:
        raise FileNotFoundError(f"no HistData 1m files for {instrument} {first_year}-{last_year} in {raw_dir}")
    b = pd.concat(frames, ignore_index=True)
    flags = b["dq_flag"].fillna("").astype(str) if "dq_flag" in b else pd.Series("", index=b.index)
    b = b[~flags.str.startswith("error").to_numpy()]
    b = holdout_guard.cut(b, what=f"fs-v1 {instrument} minutes")
    b = b.drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
    for k in ("open", "high", "low", "close"):
        b[f"ask_{k}"] = b[f"bid_{k}"]
    b["tick_count"] = np.nan
    b["spread_mean"] = np.nan
    b["spread_max"] = np.nan
    b["ts_utc"] = utc_index(b["ts_utc"]).as_unit("ns")
    b["visible_at"] = utc_index(b["visible_at"]).as_unit("ns")
    return b[[*BAR_COLUMNS, "source"]].assign(bid_only=True)


def histdata_instruments(raw_dir: Path) -> list[str]:
    base = Path(raw_dir) / HISTDATA_DIR
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")) if base.exists() else []


# ------------------------------------------------------------------------------------------------- tiers
TIER_RANK: dict[str, int] = {"A": 0, "A-consistent": 1, "B": 2, "C": 3, "F": 4, "P": 5, "ungraded": 6}
INTRADAY_TIERS = frozenset({"A", "A-consistent"})          # barrier labels and intraday features
DAILY_TIERS = frozenset({"A", "A-consistent", "B"})       # close-to-close and daily features


@dataclass(frozen=True)
class Tiers:
    """An r11 tiers file (scripts/histdata_m1.py `tiers_r11-v2.1.csv`): tier per instrument x month of ONE source
    (`source`: whose minutes were graded; the r11-v2.1 file grades HistData), its sha256."""
    path: str
    sha256: str
    r11_id: str | None
    table: dict[str, dict[str, str]] = field(hash=False)
    source: str = "histdata"

    @classmethod
    def from_csv(cls, path: Path, *, source: str = "histdata") -> Tiers:
        raw = Path(path).read_bytes()
        t = pd.read_csv(path, dtype={"key": str, "month": str, "tier": str})
        if t.duplicated(["key", "month"]).any():
            raise ValueError(f"{path}: more than one tier for an instrument-month (one source per instrument-minute)")
        ids = sorted({str(x) for x in t["r11_id"].dropna().unique()}) if "r11_id" in t.columns else []
        if len(ids) > 1:
            raise ValueError(f"{path}: mixed r11 ids {ids}")
        table: dict[str, dict[str, str]] = {}
        for k, mo, tier in zip(t["key"], t["month"], t["tier"], strict=True):
            table.setdefault(str(k), {})[str(mo)] = str(tier)
        return cls(str(path), hashlib.sha256(raw).hexdigest(), ids[0] if ids else None, table, source)

    def key(self, instrument: str) -> str:
        return f"{self.source}:{instrument}"

    def tier_of(self, instrument: str, months: Sequence[str]) -> np.ndarray:
        t = self.table.get(instrument, {})
        return np.array([t.get(m, "ungraded") for m in months], dtype=object)


def _month(ts: pd.Series) -> np.ndarray:
    return np.asarray(session_date(ts).to_period("M").astype(str))


def row_source(rows: pd.DataFrame) -> str:
    """`<source>:<instrument>` of a frame of one instrument and one source (refused otherwise)."""
    inst, src = rows["instrument"].unique(), rows["source"].unique()
    if len(inst) != 1 or len(src) != 1 or "+" in str(src[0]):
        raise ValueError(f"one instrument and one source per tiered read (A16), got {list(inst)} / {list(src)}")
    s = str(src[0])
    return s if ":" in s else f"{s}:{inst[0]}"


def apply_tiers(rows: pd.DataFrame, tiers: Tiers, tf: str, *,
                proxy_source: str | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Rows of the tiers each timeframe may use (intraday: A, A-consistent; 1d/1w: and B), with `tier` and
    `tier_window`; the dropped count per tier. The tiers must grade the rows' own source (`row_source`), unless
    `proxy_source` names the graded source explicitly (e.g. "histdata:XAUUSD" for Dukascopy gold before D-1),
    which the caller records (quant C3). A row's month is the session month of its bar's last instant."""
    dropped = dict.fromkeys(TIER_RANK, 0)
    if rows.empty:
        return rows.assign(tier=pd.Series(dtype=str), tier_window=pd.Series(dtype=str)), dropped
    inst = str(rows["instrument"].iloc[0])
    src = row_source(rows)
    if src != tiers.key(inst) and proxy_source != tiers.key(inst):
        raise ValueError(f"the tiers grade {tiers.key(inst)}, the rows are {src}: pass proxy_source="
                         f"{tiers.key(inst)!r} to read them as a recorded proxy")
    end_m = _month(pd.Series(pd.DatetimeIndex(rows["decision_utc"]) - pd.Timedelta(1, "ns")))
    start_m = _month(rows["window_from_utc"])
    tier = tiers.tier_of(inst, list(end_m))
    rank_of = {m: TIER_RANK.get(x, 6) for m, x in zip(end_m, tier, strict=True)}
    all_m = pd.period_range(min(start_m.min(), end_m.min()), end_m.max(), freq="M").astype(str)
    for m, x in zip(all_m, tiers.tier_of(inst, list(all_m)), strict=True):
        rank_of.setdefault(m, TIER_RANK.get(x, 6))
    order = {m: i for i, m in enumerate(all_m)}
    ranks = np.array([rank_of[m] for m in all_m])
    names = {v: k for k, v in TIER_RANK.items()}
    pairs = {(s, e) for s, e in zip(start_m, end_m, strict=True)}
    worst = {(s, e): names[int(ranks[order[s]:order[e] + 1].max())] for s, e in pairs}
    out = rows.assign(tier=tier.astype(str), tier_window=[worst[(s, e)] for s, e in zip(start_m, end_m, strict=True)])
    allowed = DAILY_TIERS if tf_seconds(tf) >= tf_seconds("1d") else INTRADAY_TIERS
    ok = out["tier"].isin(allowed).to_numpy()
    for t, n in out.loc[~ok, "tier"].value_counts().items():
        dropped[str(t) if str(t) in dropped else "ungraded"] += int(n)
    return out[ok].reset_index(drop=True), dropped


PURPOSES = ("judge", "train", "parity")


def load(cache_dir: Path, *, instrument: str, tf: str, years: Iterable[int], tiers: Tiers | None,
         spec: FeatureSpec = SPEC, phases: Sequence[int] = (0,), purpose: str = "judge",
         minutes: pd.DataFrame | None = None, expect_keys: dict[tuple[int, int], str] | None = None,
         proxy_source: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """The cached partitions whose key is EXACTLY the one the current spec, code and data give (code review M2: a
    revised minute never serves a stale row): from `minutes` (the instrument's history, keys recomputed by
    `cache_key`) or `expect_keys` {(year, phase): key}. Then the holdout guard (code review M1) and the tiers
    (`apply_tiers`), and a manifest: the keys, the tiers sha256 and r11 id, any tier proxy, rows kept and dropped.
    `purpose`: "judge" (default: anything that scores, selects, thresholds or gates) loads phase 0 only and always
    with tiers; "train" may add offset phases, with tiers; "parity" (parity and throughput checks only, never a
    labelled count) may read without tiers or keys."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose {purpose!r}: one of {PURPOSES}")
    if purpose == "judge" and any(int(p) != 0 for p in phases):
        raise ValueError("a judged read loads phase 0 only: offset candles are training augmentation")
    if tiers is None and purpose != "parity":
        raise ValueError(f"a {purpose} read of fs-v1 rows loads with the r11 tiers (ADR 0085 A4); pass tiers")
    if minutes is None and expect_keys is None and purpose != "parity":
        raise ValueError("pass `minutes` or `expect_keys`: a partition is read only under the key its data gives")
    want_spec, want_code = spec_hash(spec), code_hash()
    parts, keys = [], []
    for y, ph in ((y, int(p)) for y in sorted(years) for p in phases):
        want = (expect_keys or {}).get((y, ph))
        if want is None and minutes is not None:
            want = cache_key(minutes, instrument=instrument, tf=tf, year=y, spec=spec, phase=ph)
        cands = []
        for mp in sorted(_part_dir(cache_dir, spec, instrument, tf, y, ph).glob("*.json")):
            man = json.loads(mp.read_text())
            if man.get("spec_hash") != want_spec or man.get("code_hash") != want_code:
                continue
            if want is not None and man.get("key") != want:
                continue
            cands.append((str(man.get("built_utc")), mp, man))
        if not cands:
            raise FileNotFoundError(f"no fs-v1 partition {instrument} {tf} p{ph} {y} under the key of this spec, code "
                                    f"and data in {cache_dir}: build it")
        if want is None and len(cands) > 1:
            raise ValueError(f"{instrument} {tf} p{ph} {y}: {len(cands)} partitions and no data key to choose by")
        _, mp, man = max(cands, key=lambda c: c[0])
        parts.append(_typed(pd.read_parquet(mp.with_suffix(".parquet"))))
        keys.append(man["key"])
    rows = _typed(pd.concat(parts, ignore_index=True))
    holdout_guard.refuse(rows, what="fs-v1 load", col="decision_utc")
    holdout_guard.refuse(rows, what="fs-v1 load (window)", col="window_from_utc")
    meta: dict[str, Any] = {"spec_id": spec.spec_id, "spec_hash": want_spec, "code_hash": want_code, "keys": keys,
                            "purpose": purpose, "rows_read": len(rows), "tiers_sha256": None, "r11_id": None,
                            "tiers_path": None, "tier_proxy": None, "dropped": dict.fromkeys(TIER_RANK, 0)}
    if tiers is not None:
        src = row_source(rows) if len(rows) else None
        rows, dropped = apply_tiers(rows, tiers, tf, proxy_source=proxy_source)
        proxy = tiers.key(instrument) if src is not None and src != tiers.key(instrument) else None
        meta.update(tiers_sha256=tiers.sha256, r11_id=tiers.r11_id, tiers_path=tiers.path, dropped=dropped,
                    tier_proxy=proxy, kept={str(k): int(v) for k, v in rows["tier"].value_counts().items()})
    meta["rows"] = len(rows)
    return rows, meta


# ------------------------------------------------------------------------------------------------- folds, serving
def purge(train: pd.DataFrame, test: pd.DataFrame, *, label_end: str = "label_end_utc",
          embargo: pd.Timedelta | None = None) -> np.ndarray:
    """True per TRAIN row to keep (quant C6): a training row is purged when its [window_from_utc, label end] overlaps
    any test row's [decision_utc, label end + embargo] (embargo default: the test set's longest label horizon).
    group_id counts n_eff; it is no substitute for this purge (a phase-5 copy of a test row overlaps and goes)."""
    if test.empty or train.empty:
        return np.ones(len(train), dtype=bool)
    t0, t1 = _ns(pd.DatetimeIndex(test["decision_utc"])), _ns(pd.DatetimeIndex(test[label_end]))
    emb = int((embargo if embargo is not None else pd.Timedelta(int((t1 - t0).max()), "ns")).value)
    order = np.argsort(t0, kind="stable")
    t0, t1 = t0[order], t1[order] + emb
    reach = np.maximum.accumulate(t1)                       # the furthest test end among tests starting <= x
    a = _ns(pd.DatetimeIndex(train["window_from_utc"]))
    b = _ns(pd.DatetimeIndex(train[label_end]))
    k = np.searchsorted(t0, b, side="right") - 1            # last test starting at or before the train row's end
    overlap = (k >= 0) & (reach[np.clip(k, 0, None)] >= a)
    return ~overlap


SERVE_GATED_TFS = frozenset({"4h", "1d", "1w"})


def serve_ready(tf: str, columns: Sequence[str], parity_json: Path | None) -> tuple[bool, str]:
    """May a model trained on fs-v1 rows at `tf` with `columns` serve (quant C4)? At 4h/1d/1w, or with any d1_
    column, only when the parity oracle's `snapshot_as_deployed_90k` block (scripts/research/feature_store_parity.py
    on the serving path) shows max abs diff <= 1e-9 and no NaN-mask difference on every served column. The promotion
    of any fs-v1-trained model calls this; a missing report is a refusal."""
    gated = tf in SERVE_GATED_TFS or any(str(c).startswith("d1_") for c in columns)
    if not gated:
        return True, "not gated"
    if parity_json is None or not Path(parity_json).exists():
        return False, f"{tf}: no serving-path parity report"
    rep = json.loads(Path(parity_json).read_text())
    if rep.get("tf") != tf:
        return False, f"parity report is for {rep.get('tf')}, not {tf}"
    blk = rep.get("snapshot_as_deployed_90k") or {}
    over = set(blk.get("columns_over_1e-9", {})) | set(blk.get("columns_mask_differs", {}))
    bad = sorted(over & set(columns))
    if not blk.get("n") or bad:
        return False, f"{tf}: deployed serving path differs from fs-v1 on {bad or 'every row (no snapshot)'}"
    return True, "serving path equals fs-v1"


def columns_for(rows: pd.DataFrame, columns: Sequence[str] = SR_FEATURES) -> list[str]:
    """The model columns `rows` may carry (quant L3): rows mixing bid-only and bid+ask sources drop
    BID_ONLY_MISSING (a pooled model never learns 'NaN means HistData'); one source keeps them all."""
    mixed = "bid_only" in rows.columns and rows["bid_only"].nunique() > 1
    return [c for c in columns if not (mixed and c in BID_ONLY_MISSING)]


__all__ = ["BID_ONLY_MISSING", "CACHE", "PURPOSES", "SPEC", "columns_for", "code_files", "purge", "row_source",
           "serve_ready", "history_minutes", "histdata_instruments", "TIER_RANK", "WINDOW_EXACT", "FeatureSpec", "Tiers", "apply_tiers", "build",
           "build_year", "cache_key", "code_hash", "compute", "features_for", "job_order", "load", "read_manifests",
           "spec_hash", "window_exact", "year_span"]
