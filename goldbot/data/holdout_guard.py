"""The one holdout guard every loader and ingest outside `research/windows.py` calls (quant-reviewer ruling R10,
plan 2026-11-25 S1).

R10: "holdout 2025-10-01..2026-09-30 never used ... EVERY new loader refuses the holdout explicitly (forward_replay,
closed-bar seed, dense/augment generators, free 1m pulls, broker backfills) - live paths bypass windows.py; m1_backfill
--days N and 'to present' pulls put holdout minutes into stores."

* A research or judging frame that touches the holdout raises `HoldoutSealed` (`refuse`). A loader whose files hold
  holdout rows by construction (a yearly file, a broker store) drops them before anything reads them (`cut`), and an
  ingest that must keep them stores them apart (`split`).
* A pull never asks for a holdout minute (`pull_spans`): the span is split around the holdout.
* The holdout seal is irreversible. Once a minute of it is read, no later read of it is fresh, so the guard runs
  where the data comes in, not where a result goes out.
* Live paths are exempt only when listed (`LIVE_EXEMPT`, with the reason). Research scripts written before this
  guard that open bar files without one are frozen in `UNGUARDED`: none may join, and a script leaves the list by
  calling the guard (tests/data/test_holdout_guard.py holds both lists to the code).

`research/windows.py` seals more (everything from 2025-10-01 on is outside its windows) and keeps its own checks; its
`HoldoutSealed` is this class. Nothing here imports goldbot.research, so ingest and ops code can call it.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

__all__ = ["HOLDOUT_END", "HOLDOUT_START", "LIVE_EXEMPT", "UNGUARDED", "HoldoutSealed", "cut", "in_holdout",
           "pull_spans", "refuse", "split"]

HOLDOUT_START = pd.Timestamp("2025-10-01", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-10-01", tz="UTC")       # exclusive: the holdout is 2025-10-01 .. 2026-09-30


class HoldoutSealed(PermissionError):
    """The holdout (2025-10-01 .. 2026-09-30) was asked for or reached a research frame. Nothing in research reads it."""


TimeLike = pd.DataFrame | pd.Series | pd.DatetimeIndex | Iterable[pd.Timestamp]


def _utc(ts: TimeLike, col: str) -> pd.DatetimeIndex:
    if isinstance(ts, pd.DataFrame):
        ts = ts[col]
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Series(list(ts)) if not isinstance(ts, (pd.Series, pd.DatetimeIndex))
                                          else ts))
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def in_holdout(ts: TimeLike, col: str = "ts_utc") -> np.ndarray:
    """True per row inside [HOLDOUT_START, HOLDOUT_END). Naive timestamps are read as UTC; any unit (s/ms/us/ns)."""
    idx = _utc(ts, col)
    return np.asarray((idx >= HOLDOUT_START) & (idx < HOLDOUT_END), dtype=bool)


def refuse(ts: TimeLike, *, what: str, col: str = "ts_utc") -> None:
    """Raise `HoldoutSealed` when any row of `ts` (a frame's `col`, a series or an index) is inside the holdout."""
    hit = in_holdout(ts, col)
    if hit.any():
        first = _utc(ts, col)[hit].min()
        raise HoldoutSealed(f"{what}: {int(hit.sum())} row(s) inside the sealed holdout "
                            f"({HOLDOUT_START.date()} .. {(HOLDOUT_END - pd.Timedelta(days=1)).date()}), first "
                            f"{first}: refused")


def cut(frame: pd.DataFrame, *, what: str, col: str = "ts_utc") -> pd.DataFrame:
    """`frame` without its holdout rows (logged when any are dropped): for loaders whose files hold them by
    construction (a yearly file, a broker store), before any reader sees the frame."""
    hit = in_holdout(frame, col)
    if hit.any():
        log.info("%s: %d holdout row(s) dropped at load", what, int(hit.sum()))
    return frame.loc[~hit].reset_index(drop=True)


def split(frame: pd.DataFrame, col: str = "ts_utc") -> tuple[pd.DataFrame, pd.DataFrame]:
    """(research part, sealed part): an ingest that keeps the holdout writes the sealed part to its own partition,
    which no research loader opens."""
    hit = in_holdout(frame, col)
    return frame.loc[~hit].reset_index(drop=True), frame.loc[hit].reset_index(drop=True)


def pull_spans(start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """The parts of the pull [start, end) outside the holdout, in time order (empty when it lies inside it)."""
    a, z = pd.Timestamp(start), pd.Timestamp(end)
    out = [(a, min(z, HOLDOUT_START)), (max(a, HOLDOUT_END), z)]
    return [(s, e) for s, e in out if s < e]


# Live paths that read or write holdout minutes on purpose (the engine trades on today's bars; these stores also hold
# 2025-10..2026-09). They are never research inputs: a research reader of them calls `refuse` or `cut` first.
LIVE_EXEMPT: dict[str, str] = {
    "goldbot.data.store": "the live store (ticks, bars, broker M1, fills) the engine and bridge write and read; "
                          "research readers of it guard at their own call",
    "goldbot.data.compact": "compacts the live store's closed months in place; reads and writes the same rows",
    "goldbot.data.quarantine": "moves live-store rows that failed a data-quality check aside; no research output",
    "goldbot.data.ticks": "the live tick store reader the engine and reconcile use; research reads bars through "
                          "windows.py or a guarded loader",
    "goldbot.data.release": "read_bar_files: the shared yearly-file reader; every research caller cuts or refuses "
                            "the holdout (tests/test_holdout_seal.py static check), and the gated --score-holdout "
                            "read goes through it",
    "goldbot.data.daily": "daily bars for the engine's 1d/1w warm-up; research daily readers filter < holdout",
    "goldbot.data.long_history": "the long daily history the engine's 1d/1w warm-up reads to today; research "
                                 "position loaders filter < holdout at read",
    "goldbot.data.loaders": "CSV loaders for the owner's broker statements and calendars, no market bars",
    "goldbot.data.macro": "FRED macro series as published, read point in time by the engine's macro features",
    "goldbot.data.positioning": "CFTC positioning as published, point in time for the engine's features",
    "goldbot.data.positioning_local": "the local positioning store the engine features read point in time",
    "goldbot.data.us_calendar": "the economic calendar archive the engine's news blackout reads",
    "goldbot.ops.long_history": "appends the live store's complete days to raw/daily (the engine's 1d/1w warm-up); "
                                "research readers of raw/daily filter < holdout",
    "goldbot.engine.long_bars": "the engine's 1d/1w bars from raw/daily up to now, live trading only",
}

# Scripts that open bar files with no guard call the static check recognises, written before this guard (2026-11-25
# inventory; some cut by other means, e.g. model_zoo_history's min(end, HOLDOUT_FROM), unverified here). Frozen: none
# may join; each calls `refuse`/`cut` (or windows.py) before its next research run, and then leaves the list.
UNGUARDED: frozenset[str] = frozenset({
    "scripts/check_coverage.py", "scripts/count_owed_events.py", "scripts/cross_asset_fetch.py",
    "scripts/daily_fetch.py", "scripts/dukascopy_year.py", "scripts/fred_macro.py", "scripts/kb1_retest_selection.py",
    "scripts/kb5_magnet_base_rate.py", "scripts/kb_prereg_counts.py", "scripts/mlprob/h94_gate.py",
    "scripts/mlprob/mlnet_pool.py", "scripts/mlprob/probtrend_screen.py", "scripts/pa_claims_check.py",
    "scripts/profile_arm_rules.py", "scripts/quarantine_tz_ticks_2026_10_26.py", "scripts/replica_backtest_parity.py",
    "scripts/replica_parity.py", "scripts/replica_render.py", "scripts/research/h16_screen.py",
    "scripts/research/h17_screen.py", "scripts/research/h84_macro_screen.py", "scripts/research/meta_label_screen.py",
    "scripts/research/micro_tick_screen.py", "scripts/research/model_zoo_history.py",
    "scripts/research/positioning_screen.py", "scripts/research/tight_stop_entry_study.py", "scripts/research_pass.py",
    "scripts/ship_long_history.py", "scripts/smc_counts.py", "scripts/sprint200/patterns_ctrl_diag.py",
})
