"""Unit -> cells -> detector -> simulated trades. A unit is one (family, variant) pooled over its instruments AND
timeframes (quant A1); cells only route survivors and are never a verdict.

Source cutovers: a per-cell detector runs separately on each source segment of the bars (lookbacks restart), so a level
from one source is never compared with another source's prices (A3)."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue.core import EB, Ctx, Events
from goldbot.research.catalogue.costs import window_model
from goldbot.research.catalogue.data import (
    DAY,
    FINE_INSTRUMENTS,
    INTRADAY,
    INTRADAY_WARMUP_NS,
    Bars,
    Win,
    assert_sealed,
    load,
    load_minutes,
    raw_dir,
    window,
)
from goldbot.research.catalogue.detectors_catalogue import (
    CATALOGUE_DETECTORS,
    CATALOGUE_XS,
    RATES,
    _month_end,
)
from goldbot.research.catalogue.detectors_patterns import PATTERN_DETECTORS
from goldbot.research.catalogue.sim import INTRABAR, TRADE_COLS, simulate, window_drift_per_ns
from goldbot.research.windows import HoldoutSealed

SPECS_PATH = Path(__file__).with_name("specs.yaml")
RW_GUARD_PATH = Path(__file__).with_name("rw_guard.json")


# ------------------------------------------------------------------ nulls
def _h(*key: object) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, key)).encode()).digest()[:8], "big") / 2**64


def null_rand(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 14)
    eb = EB()
    rate = float(p.get("rate", 0.01))
    for j in np.flatnonzero(np.isfinite(a)):
        if _h("nr", b.inst, b.tf, int(b.t[j])) < rate:
            side = 1 if _h("ns", b.inst, b.tf, int(b.t[j])) < 0.5 else -1
            eb.add(int(j), side, stop_d=a[j], tgt_d=2 * a[j], hold=10)
    return eb.build()


def null_longclose(b: Bars, p: dict, ctx: Ctx) -> Events:
    a = K.atr(b, 20)
    eb = EB()
    for j in np.flatnonzero(np.isfinite(a)):
        eb.add(int(j), 1, stop_d=2.5 * a[j], hold=1)
    return eb.build()


def null_xs(bs: dict[str, Bars], p: dict, ctx: Ctx) -> dict[str, Events]:
    from goldbot.research.catalogue.detectors_catalogue import _xs_book
    score = {}
    for k, b in bs.items():
        mkey = ((b.ta - 1) // DAY // 30).astype(np.int64)
        score[k] = np.array([_h("nx", k, int(m)) for m in mkey])
    return _xs_book(bs, score, 1 / 3, p)


DETECTORS = {**CATALOGUE_DETECTORS, **PATTERN_DETECTORS, "NULL_RAND": null_rand, "NULL_LONGCLOSE": null_longclose}
XS_DETECTORS = {**CATALOGUE_XS, "NULL_XS": null_xs}


# ------------------------------------------------------------------ specs
@lru_cache(maxsize=1)
def specs() -> dict[str, Any]:
    return yaml.safe_load(SPECS_PATH.read_text())


def specs_sha256() -> str:
    return hashlib.sha256(SPECS_PATH.read_bytes()).hexdigest()


def units() -> list[dict[str, Any]]:
    return list(specs()["units"])


def rw_guard() -> dict[str, Any]:
    """The random-walk guard's listing (quant C9): "sparse" units (fewer than 30 trades after 24 driftless walks:
    "excluded from EVAL-1 screening, and it is listed") and "failed" unit|control pairs (the pooled rule failed on the
    first 3 samples and on the retest). tests/test_catalogue_random_walk.py keeps both lists exact."""
    return dict(json.loads(RW_GUARD_PATH.read_text()))


def rw_excluded_units() -> set[str]:
    """Units the screen leaves out under EVAL-1 (ii): sparse on the guard, or a pair of theirs failed it."""
    g = rw_guard()
    return set(g["sparse"]) | {p.split("|")[0] for p in g["failed"]}


def unit_by_id(uid: str) -> dict[str, Any]:
    for u in units():
        if u["unit"] == uid:
            return u
    raise KeyError(uid)


def cells(u: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for inst in u["instruments"]:
        for tf in u["timeframes"]:
            if inst != "XAUUSD" and tf not in ("4h", "1d", "1w"):
                continue                     # non-gold below 4h: no history (plan)
            out.append((inst, tf))
    return out


# ------------------------------------------------------------------ context (exogenous schedules, aux series)
EVENT_SETS = {
    "fomc_statement": ("fomc_statement",),
    "s06_all": ("cpi", "ppi", "nfp", "fomc_statement"),
    "s06_fomc_nfp": ("fomc_statement", "nfp"),
    "s06_cpi_ppi": ("cpi", "ppi"),
    "tier1": ("nfp", "cpi", "fomc_statement", "pce", "ism_manufacturing"),
    "fomc_nfp_cpi": ("fomc_statement", "nfp", "cpi"),
}
LONG_KEYS = {"XAUUSD": "xauusd", "XAGUSD": "xagusd", "XTIUSD": "xtiusd", "US500": "us500", "USTEC": "ustec",
             "DE40": "de40", "UK100": "uk100", "US30": "us30", "USINDEX": "usindex", "EURUSD": "eurusd",
             "GBPUSD": "gbpusd", "AUDUSD": "audusd", "NZDUSD": "nzdusd", "USDJPY": "usdjpy", "USDCAD": "usdcad",
             "USDCHF": "usdchf", "AUDJPY": "audjpy", "EURAUD": "euraud", "GBPAUD": "gbpaud", "EURGBP": "eurgbp",
             "EURJPY": "eurjpy", "GBPJPY": "gbpjpy"}
HOLIDAY_SOURCE = {"US": "us500", "DE": "de40", "UK": "uk100"}


def _long(key: str, end: int) -> pd.DataFrame:
    d = pd.read_parquet(raw_dir() / "daily" / "long" / f"{key}_daily_long.parquet")
    av = pd.to_datetime(d["available_utc"], utc=True)
    return d[av.map(lambda x: x.value) < end].reset_index(drop=True) if len(d) else d


def build_ctx(win: str, *, allow_test_old: bool = False) -> Ctx:
    from goldbot.research.catalogue.data import epoch_ns, fred, us_events
    w = window(win, allow_test_old=allow_test_old)
    ctx = Ctx(window=win)
    for name, types in EVENT_SETS.items():
        ctx.events[name] = us_events(win, types, allow_test_old=allow_test_old)
    for ex, key in HOLIDAY_SOURCE.items():
        d = _long(key, w.end)
        src = d["source"].astype(str)
        dd = d[src.str.startswith("yahoo:^") | src.str.startswith("dukascopy")]
        days = (epoch_ns(pd.to_datetime(dd["date"]).dt.tz_localize("UTC")) // DAY).astype(np.int64)
        if len(days):
            allw = np.arange(days.min(), days.max() + 1)
            wk = allw[((allw + 3) % 7) < 5]
            ctx.events[f"holiday_{ex}"] = np.setdiff1d(wk, days)
    for ccy, series in RATES.items():
        ctx.aux[f"rate_{ccy}"] = fred(series, win, allow_test_old=allow_test_old)
    ctx.aux["costs"] = window_model(win, allow_test_old=allow_test_old)       # EVAL-1 section 1, C2
    for inst, key in LONG_KEYS.items():
        f = raw_dir() / "daily" / "long" / f"{key}_daily_long.parquet"
        if not f.exists():
            continue
        d = _long(key, w.end)
        src = d["source"].astype(str)
        fut = (src.str.startswith("yahoo:") & src.str.endswith("=F")).to_numpy()
        ctx.aux[f"long_{inst}"] = (epoch_ns(pd.to_datetime(d["available_utc"], utc=True)),
                                   d["close"].to_numpy(float), fut)
    return ctx


# ------------------------------------------------------------------ detection
def _merge(parts: list[tuple[int, Events]], n: int) -> Events:
    rows_i, keep = [], []
    el = np.zeros(n, bool)
    es = np.zeros(n, bool)
    has_l = has_s = False
    for off, ev in parts:
        if ev.exit_long is not None:
            el[off:off + len(ev.exit_long)] = ev.exit_long
            has_l = True
        if ev.exit_short is not None:
            es[off:off + len(ev.exit_short)] = ev.exit_short
            has_s = True
        rows_i.append(off)
        keep.append(ev)
    if not keep:
        return K.empty()
    def cat(f: str) -> np.ndarray:
        return np.concatenate([getattr(e, f) + (o if f == "i" else 0) for o, e in zip(rows_i, keep)])

    return Events(i=cat("i"), side=cat("side"), kind=cat("kind"), px=cat("px"), valid=cat("valid"), stop=cat("stop"),
                  stop_d=cat("stop_d"), tgt=cat("tgt"), tgt_d=cat("tgt_d"), hold=cat("hold"),
                  exit_long=el if has_l else None, exit_short=es if has_s else None,
                  oco=np.concatenate([e.oco if e.oco is not None else np.zeros(len(e), np.int8) for e in keep]))


def detect_cell(u: dict[str, Any], b: Bars, ctx: Ctx) -> Events:
    fn = DETECTORS[u["detector"]]
    if len(b) == 0:
        return K.empty()
    cuts = np.flatnonzero(np.diff(b.seg) != 0) + 1
    if not len(cuts):
        return fn(b, u["params"], ctx)
    bounds = [0, *cuts.tolist(), len(b)]
    parts = [(a, fn(b.slice(a, z), u["params"], ctx)) for a, z in zip(bounds[:-1], bounds[1:]) if z - a > 30]
    return _merge(parts, len(b))


_BAR_CACHE: dict[tuple[str, str, str], Bars] = {}


def cache_dir() -> Path:
    return Path(os.environ.get("CATALOGUE_CACHE", str(Path.home() / "tmp" / "catalogue_cache")))


CACHE_META = ("win", "data_from", "dec_from", "end", "lo")


def cache_lo(w: Win, tf: str) -> int:
    """The first bar instant the loader reads for `tf` in window `w` (data._cut / data.load_minutes): the window's
    warm-up start, or the 120-day intraday warm-up for gold 5m-1h and the 1m fill bars."""
    if tf == "1m" or (tf in INTRADAY and tf != "4h"):
        return max(w.data_from, w.dec_from - INTRADAY_WARMUP_NS)
    return w.data_from


def cache_meta(w: Win, lo: int) -> dict[str, np.ndarray]:
    """The span a cache file is built for, stored in the npz next to the bars."""
    return {"win": np.array(w.name), "data_from": np.array(w.data_from, np.int64),
            "dec_from": np.array(w.dec_from, np.int64), "end": np.array(w.end, np.int64), "lo": np.array(lo, np.int64)}


def save_cache(f: Path, b: Bars, w: Win, tf: str) -> None:
    """The one writer of $CATALOGUE_CACHE files (dispatcher and run.prebuild): bars plus the window span they hold."""
    checked_cache(b, w, f, lo=cache_lo(w, tf))
    f.parent.mkdir(parents=True, exist_ok=True)
    np.savez(f, t=b.t, ta=b.ta, o=b.o, h=b.h, l=b.l, c=b.c, spr=b.spr, co=b.co, seg=b.seg, vol=b.vol,
             **cache_meta(w, cache_lo(w, tf)))  # type: ignore[arg-type]  # numpy stub: **kwds vs allow_pickle


def _meta_ok(z: Any, w: Win, lo: int, f: Path) -> bool:
    """False for a legacy file without a recorded span (rebuilt, never served); raises on a recorded span that is not
    this window's (a foreign or mis-pathed file is a corruption signal, not a cache miss)."""
    if any(k not in z.files for k in CACHE_META):
        return False
    got = (str(z["win"]), int(z["data_from"]), int(z["dec_from"]), int(z["end"]), int(z["lo"]))
    want = (w.name, w.data_from, w.dec_from, w.end, lo)
    if got != want:
        raise HoldoutSealed(f"{f}: cache built for {got[0]} span {got[1:]} is not the {w.name} window {want[1:]} "
                            "(foreign or mis-pathed cache file; delete it and rebuild)")
    return True


def checked_cache(b: Bars, w: Win, f: Path, *, lo: int | None = None) -> Bars:
    """A bar cache file is state: re-check it on every load and write, fail closed. A bar that closes after the
    window's end (a 2018 bar in the explore cache is an unsealed confirm read; a confirm cache bar at or after
    2025-10-01 is the holdout), or that opens before the loader's warm-up start `lo` (a 2006 bar in the explore
    cache), raises; it is never served. The loader cuts the same way (data._cut), so a clean cache always passes."""
    if len(b) and (int(np.max(b.ta)) > w.end or int(np.max(b.t)) >= w.end):
        raise HoldoutSealed(f"{f}: a cached bar closes after the {w.name} window end (stale or foreign cache file; "
                            "delete it and rebuild)")
    if lo is not None and len(b) and int(np.min(b.t)) < lo:
        raise HoldoutSealed(f"{f}: a cached bar opens before the {w.name} warm-up start (stale or foreign cache "
                            "file; delete it and rebuild)")
    assert_sealed(b.ta, b.t)
    return b


def _from_npz(z: Any, inst: str, tf: str, w: Win) -> Bars:
    return Bars(inst, tf, z["t"], z["ta"], z["o"], z["h"], z["l"], z["c"], z["spr"], z["co"], z["seg"], z["vol"],
                meta={"window": w.name, "dec_from": w.dec_from, "end": w.end})


def get_bars(inst: str, tf: str, win: str, *, allow_test_old: bool = False) -> Bars:
    key = (inst, tf, win)
    if key in _BAR_CACHE:
        return _BAR_CACHE[key]
    w = window(win, allow_test_old=allow_test_old)
    f = cache_dir() / win / f"{inst}_{tf}.npz"
    lo = cache_lo(w, tf)
    z = np.load(f, allow_pickle=False) if f.exists() else None
    if z is not None and _meta_ok(z, w, lo, f):
        b = checked_cache(_from_npz(z, inst, tf, w), w, f, lo=lo)
    else:
        b = load(inst, tf, win, allow_test_old=allow_test_old)
        save_cache(f, b, w, tf)
    if inst == "XAUUSD" and tf in ("5m", "15m"):
        pass
    else:
        _BAR_CACHE[key] = b
    return b


_MINUTES: dict[str, Bars] = {}


def get_minutes(win: str, *, allow_test_old: bool = False) -> Bars:
    """Gold 1m bars of the window (cached in memory and as $CATALOGUE_CACHE/<win>/XAUUSD_1m.npz)."""
    if win in _MINUTES:
        return _MINUTES[win]
    w = window(win, allow_test_old=allow_test_old)
    f = cache_dir() / win / "XAUUSD_1m.npz"
    lo = cache_lo(w, "1m")
    z = np.load(f, allow_pickle=False) if f.exists() else None
    if z is not None and _meta_ok(z, w, lo, f):
        b = checked_cache(_from_npz(z, "XAUUSD", "1m", w), w, f, lo=lo)
    else:
        b = load_minutes(win, allow_test_old=allow_test_old)
        save_cache(f, b, w, "1m")
    _MINUTES[win] = b
    return b


def intrabar(b: Bars, ev: Events) -> bool:
    """The cell has stop or limit entries decided inside the window (they may fill inside a bar)."""
    return bool(np.isin(ev.kind[in_window(b, ev)], INTRABAR).any())


def unit_events(u: dict[str, Any], win: str, ctx: Ctx, *, allow_test_old: bool = False
                ) -> dict[tuple[str, str], tuple[Bars, Events]]:
    out: dict[tuple[str, str], tuple[Bars, Events]] = {}
    if u.get("xs"):
        for tf in u["timeframes"]:
            bs = {}
            for inst in u["instruments"]:
                try:
                    bx = get_bars(inst, tf, win, allow_test_old=allow_test_old)
                except FileNotFoundError:
                    continue
                if len(bx) > 30:                 # an instrument with no history in the window is not in the book
                    bs[inst] = bx
            evs = XS_DETECTORS[u["detector"]](bs, u["params"], ctx)
            for inst, ev in evs.items():
                out[(inst, tf)] = (bs[inst], ev)
        return out
    for inst, tf in cells(u):
        try:
            b = get_bars(inst, tf, win, allow_test_old=allow_test_old)
        except FileNotFoundError:
            continue
        out[(inst, tf)] = (b, detect_cell(u, b, ctx))
    return out


def in_window(b: Bars, ev: Events) -> np.ndarray:
    if not len(ev):
        return np.zeros(0, bool)
    ta = b.ta[np.clip(ev.i, 0, len(b) - 1)]
    return (ta >= b.meta["dec_from"]) & (ta < b.meta["end"])


def long_share(evd: dict[tuple[str, str], tuple[Bars, Events]]) -> tuple[int, float]:
    n = lng = 0
    for b, ev in evd.values():
        m = in_window(b, ev)
        n += int(m.sum())
        lng += int((ev.side[m] > 0).sum())
    return n, (lng / n if n else float("nan"))


def choose_control(u: dict[str, Any], share: float) -> str:
    if u.get("control"):
        return str(u["control"])
    return "random_time" if np.isfinite(share) and abs(share - 0.5) > 0.10 else "random_side"


def unit_trades(u: dict[str, Any], win: str, ctx: Ctx, control: str | None = None, *,
                allow_test_old: bool = False) -> tuple[pd.DataFrame, str, dict[str, int]]:
    """Trades of a unit pooled over its cells, the control used, and the counts EVAL-1 (iv) reports separately:
    "events" (decided inside the window, each OCO leg one event), "trades" (simulated; the unit's n), "risk_floor"
    (events not traded because the stop was inside the risk floor), "fine_miss" (intrabar fills the 1m bars did not
    resolve), "no_1m_cells" and "excluded_no_1m".

    Quant C10: "Every unit with intrabar stop or limit entries ... is screened only after D5 is fixed: the fill bar is
    resolved from 1m bars wherever they exist; otherwise the unit is excluded and listed". A unit with a stop/limit
    entry in any cell without 1m bars (every instrument but gold) is excluded WHOLE (no trades; "excluded_no_1m" 1,
    "no_1m_cells" the cells): screening only its gold cells would be a different unit from the one registered."""
    costs = ctx.aux["costs"]          # the window's measured cost model (EVAL-1 section 1); none: the run stops
    evd = unit_events(u, win, ctx, allow_test_old=allow_test_old)
    n_ev, share = long_share(evd)
    ctrl = control or choose_control(u, share)
    frames = []
    counts: dict[str, int] = {"risk_floor": 0, "fine_miss": 0, "no_1m_cells": 0, "excluded_no_1m": 0}
    need = {cell: intrabar(b, ev) for cell, (b, ev) in evd.items()}
    counts["no_1m_cells"] = sum(1 for (inst, _), v in need.items() if v and inst not in FINE_INSTRUMENTS)
    if counts["no_1m_cells"]:
        counts["excluded_no_1m"] = 1
        evd = {}
    for (inst, tf), (b, ev) in evd.items():
        fine = get_minutes(win, allow_test_old=allow_test_old) if need[(inst, tf)] else None
        tr = simulate(b, ev, control=ctrl, seed_key=u["unit"], drift_per_ns=window_drift_per_ns(b), counts=counts,
                      fine=fine, costs=costs)
        if len(tr):
            df = pd.DataFrame(tr, columns=TRADE_COLS)
            df["inst"] = inst
            df["tf"] = tf
            frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*TRADE_COLS, "inst", "tf"])
    return out, ctrl, {"events": n_ev, "trades": len(out), **counts}


def unit_event_counts(u: dict[str, Any], win: str, ctx: Ctx) -> dict[str, int]:
    """Events per cell decided inside the window: counts only, no prices or returns are computed or returned."""
    evd = unit_events(u, win, ctx)
    return {f"{i}|{t}": int(in_window(b, ev).sum()) for (i, t), (b, ev) in evd.items()}


_ = (_month_end,)
