"""The engine's 1m history must be unbroken (brain 2026-10-19: twelve restarts joined release bars ending 10-05 to a few
live minutes, so every decision that day ran on a window with a two-week hole).

Three parts, all off the order path:
- `backfill`: warm_start pulls the broker's ticks between the store's last bar and now (`copy_ticks`, paged one server
  day at a time by `goldbot.data.ticks.pull_day`) and builds them with `ticks_to_1m(..., DEFAULT_SESSIONS)`, the live
  bar builder, so the backfilled minutes are the same representation as the live ones. Bounded (days, rows per page,
  a time budget) and failing soft: a failure is logged and published; the continuity check decides what it means.
- `open_gaps`: holes in the 1m bars longer than the market's normal closures. A missing minute counts only when the
  session table (the one the bars are built with) calls it open, so weekends and the daily break are never holes;
  `max_open_min` tolerates a full-day holiday closure the static table still calls open.
- `live_source`: completed live minutes (and backfilled ones) are written to `bars_1m` under this source, so a restart
  starts from them and never reopens the hole. Never `dukascopy`: the release bars stay what research trained on.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record, UtcTimestamp
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
from goldbot.data.ticks import pull_day
from goldbot.data.timeutil import epoch_ns
from goldbot.engine.shadow import minutes_of

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine
    from goldbot.specialists.base import Specialist

log = logging.getLogger(__name__)

MINUTE_NS = 60 * 1_000_000_000


def live_source(broker_name: str) -> str:
    """The bars_1m source the engine writes its own minutes under (one per broker feed)."""
    return f"live-{broker_name}"


class HistoryGap(FrozenRecord):
    """Minutes missing between two consecutive 1m bars: [start, end), `open_min` of them in session."""
    start: UtcTimestamp       # the first missing minute
    end: UtcTimestamp         # the next bar present
    open_min: int


def open_gaps(ts_utc: pd.DatetimeIndex, sessions: SessionTable, *, max_open_min: int) -> list[HistoryGap]:
    """Every hole in `ts_utc` (sorted 1m bar opens) with more than `max_open_min` missing minutes the session table
    calls open. Only spans longer than `max_open_min` wall minutes are examined, so a 60000-bar window costs one diff."""
    if len(ts_utc) < 2:
        return []
    ns = epoch_ns(ts_utc)
    idx = np.flatnonzero(np.diff(ns) > (max_open_min + 1) * MINUTE_NS)
    out = []
    for i in idx:
        start = pd.Timestamp(ns[i] + MINUTE_NS, unit="ns", tz="UTC")
        end = pd.Timestamp(ns[i + 1], unit="ns", tz="UTC")
        n = int(np.asarray(sessions.is_open(pd.date_range(start, end, freq="min", inclusive="left"))).sum())
        if n > max_open_min:
            out.append(HistoryGap(start=start, end=end, open_min=n))
    return out


def backfill(broker: Any, symbol: str, start: pd.Timestamp, now: pd.Timestamp, sessions: SessionTable, *,
             page_ticks: int, budget_s: float, clock: Callable[[], float] = time.monotonic) -> tuple[pd.DataFrame, str | None]:
    """Completed 1m bars in [start, now) from the broker's ticks, one UTC day per `pull_day`, oldest first, until the
    time budget is spent. Returns (bars, error): the bars pulled before any failure, and the failure's type name (or
    "budget" when the budget ran out first; None when complete)."""
    deadline = clock() + budget_s
    parts: list[pd.DataFrame] = []
    error: str | None = None
    day = start
    while day < now:
        if clock() >= deadline:
            error = "budget"
            break
        end = min(day.floor("D") + pd.Timedelta(days=1), now)
        try:
            ticks = pull_day(broker, symbol, day, end, chunk=page_ticks)
        except Exception as exc:                  # noqa: BLE001 - fail soft: the continuity check decides
            log.warning("history backfill from %s stopped at %s: %s", start, day, type(exc).__name__)
            error = type(exc).__name__
            break
        if not ticks.empty:
            parts.append(ticks_to_1m(ticks[["ts_utc", "bid", "ask"]], sessions))
        day = end
    if not parts:
        return pd.DataFrame(columns=BAR_COLUMNS), error
    bars = pd.concat(parts, ignore_index=True)
    bars = bars[pd.to_datetime(bars["visible_at"], utc=True) <= now]        # complete minutes only
    return bars.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc").reset_index(drop=True), error


# ------------------------------------------------------------------------------------------- the engine's side
class History(Record):
    """The engine's continuity state: the warm start's backfill, each decision timeframe's hole at its last close
    (published as history_gap), and completed minutes not yet stored."""
    backfill: dict[str, Any] = Field(default_factory=lambda: {"from": None, "bars": 0, "error": None})
    gaps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    unstored: list[pd.DataFrame] = Field(default_factory=list)


def warm_start(eng: Engine, now: pd.Timestamp) -> int:
    """Seed eng.bars_1m from the store (release bars synced by the scheduler, and the engine's own stored minutes) so a
    restarted engine can decide at once instead of rebuilding days of bars from live ticks; 1h agents and the daily
    context need weeks. The gap between the store's last bar and `now` is backfilled from the broker's ticks with the
    live bar builder and stored (`backfill`; bounded, failing soft: `refuse` then keeps entries off a window that still
    has a hole). Where the engine's own minutes and the release's overlap, the engine's own win: they are built like
    the live minutes. Returns the number of bars loaded (0 without a store)."""
    cfg = eng.cfg
    if eng.store is None:
        return 0
    days = cfg.max_bars_in_memory // (60 * 23) * 7 // 5 + 3     # trading minutes -> calendar days
    b = eng.store.read("bars_1m", symbol=cfg.symbol, start=now - pd.Timedelta(days=days), end=now)
    if not b.empty:
        b = b.assign(_own=(b["source"] == live_source(cfg.broker_name)) if "source" in b.columns else False)
        b = b[pd.to_datetime(b["visible_at"], utc=True) <= now].sort_values(["ts_utc", "_own"], kind="stable")
        b = b[[c for c in BAR_COLUMNS if c in b.columns]].drop_duplicates("ts_utc", keep="last")
    start = now - pd.Timedelta(days=cfg.backfill_max_days)
    if not b.empty:
        start = max(start, pd.Timestamp(b["visible_at"].iloc[-1]))
    if hasattr(eng.broker, "copy_ticks") and start < now.floor("min"):
        got, err = backfill(eng.broker, cfg.symbol, start, now, DEFAULT_SESSIONS, page_ticks=cfg.backfill_page_ticks,
                            budget_s=cfg.backfill_budget_s)
        eng.history.backfill = {"from": start.isoformat(), "bars": len(got), "error": err}
        log.info("history backfill from %s: %d 1m bars from the broker's ticks%s", start, len(got),
                 f" (stopped: {err})" if err else "")
        if not got.empty:
            store_bars(eng, got)
            b = got if b.empty else pd.concat([b, got]).drop_duplicates("ts_utc", keep="last").sort_values("ts_utc")
    if b.empty:
        return 0
    eng.bars_1m = b.tail(cfg.max_bars_in_memory).reset_index(drop=True)
    return len(eng.bars_1m)


def queue(eng: Engine, done: pd.DataFrame) -> None:
    """Newly completed live minutes, stored at the next bar close (`store_bars`), after its exits and decisions."""
    if eng.store is not None:
        eng.history.unstored.append(done)


def store_bars(eng: Engine, bars: pd.DataFrame | None = None) -> None:
    """Completed 1m bars (and any a failed write left queued) -> bars_1m under the engine's own source (`live_source`;
    the store de-duplicates on ts_utc), so a restart never reopens a hole. Never raises: a failure is logged and the
    bars are retried at the next bar close; storing history never stops trading."""
    h = eng.history
    pending = h.unstored + ([bars] if bars is not None else [])
    if eng.store is None or not pending:
        return
    try:
        eng.store.append("bars_1m", pd.concat(pending, ignore_index=True)[BAR_COLUMNS],
                         source=live_source(eng.cfg.broker_name), symbol=eng.cfg.symbol)
        h.unstored = []
    except Exception as exc:                     # noqa: BLE001 - reporting data: never stops trading
        log.warning("live 1m bars not stored (%s): retried at the next bar close", type(exc).__name__)
        h.unstored = pending[-500:]              # bounded: the latest 500 batches of minutes


def window_gaps(eng: Engine, complete: pd.DataFrame) -> list[HistoryGap]:
    """The open-session holes in the completed 1m bars a bar close decides on."""
    return open_gaps(pd.DatetimeIndex(complete["ts_utc"]), DEFAULT_SESSIONS, max_open_min=eng.cfg.history_gap_max_min)


def refuse(eng: Engine, tf: str, fr: Any, gaps: list[HistoryGap], agents: list[Specialist],
           close_ts: pd.Timestamp) -> list[dict] | None:
    """Design rule: no decision on a window with a hole. When one of `gaps` falls inside the window `fr` (the frame of
    tf) computes its features on, every agent of tf is journalled `history_gap:<tf>` and no live or shadow entry is
    made at this close (the open shadow trades still age); the hole is published as history_gap[tf]. None when the
    window is unbroken. Exits and position management never look at it."""
    first = pd.Timestamp(fr.dec["ts_utc"].iloc[0])
    hit = [g for g in gaps if g.end > first]
    if not hit:
        eng.history.gaps.pop(tf, None)
        return None
    g = max(hit, key=lambda x: x.open_min)
    eng.history.gaps[tf] = {"from": g.start.isoformat(), "to": g.end.isoformat(), "open_min": g.open_min,
                            "at": close_ts.isoformat()}
    if eng.shadow is not None:
        bar = fr.dec.iloc[-1]
        eng.shadow.on_bar(bar, tf, minutes_of(eng.bars_1m, bar, tf))
    return [eng._record(a, close_ts, 0.0, 0.0, f"history_gap:{tf}") for a in agents]


def published(eng: Engine) -> dict[str, Any]:
    """engine_<account>.json: history_gap (per decision timeframe, the hole in its window at its last close; {} =
    every evaluated window unbroken) and history_backfill (the warm start's backfill), for the dashboard and health."""
    return {"history_gap": dict(eng.history.gaps), "history_backfill": dict(eng.history.backfill)}
