"""1d and 1w exploration arms in the engine (WS-4, consolidated plan 2026-11-07).

The plan's rules, as built here:
- "1d closes at 21:00 UTC" (`is_bar_close`): the long daily history's roll (raw/daily/bars/<symbol>_1d.parquet, every
  row visible at 21:00 UTC, the weekend folded into Monday's bar). A 1d arm decides at a weekday's 21:00 UTC close,
  on its warm-up rows and the engine's own daily bars.
- "'1w' added to the Timeframe literal and TF_SECONDS": a weekly bar is the ISO week (W-FRI) of the daily bars, known
  at the week's last daily close (as the WS-3 screen's `catalogue.data._weekly`), normally Friday 21:00 UTC.
- "The 1w decision runs as a fresh decision on the first pass after the week opens, with the bracket from the
  send-time price and full RiskGate. A Fri 21:00 decision is refused as 'weekend' and is never queued." A 1d decision at
  Friday's 21:00 close is journalled and recorded blocked `weekend` (`exploration._skipped`), never queued.

The go-live blockers of BACKLOG 144 (ADR 0081), as built here:
- "Send 1d entries after 22:15 UTC once the broker has reopened, the bracket from the send price; tests in EET and
  EEST" and "move the 1w decision out of the first 15-60 minutes after the Sunday open": a Monday-Thursday 1d decision
  and a 1w decision run at their entry close (`entry_close`): the first base close at least SETTLE after the broker's
  reopen (`reopen_after`, the engine's session table: the daily break, the weekly open) and, for 1d, after 22:15 UTC.
  On the bars through the decision close; the signal is stamped with the entry close (`bar_ts`, so `exploration._send`
  prices its stop and target from the send-time price through the RiskGate) and keeps the decision close in
  `decision_utc`. "Once the broker has reopened" is evidence, not the table's word: a minute after the reopen, every
  minute of the last base bar open, and the common filter passing at the entry close (`_tradable`); else it waits.
- "The 1w missed-week catch-up (a restart across the weekly open), persisted": the last decision close each arm was
  decided (or given up) for is written to aux.db `long_tf_decisions` before its signal is queued (`Decided`), so a
  restart decides a week it was down over once, up to LATE_AFTER after its entry close, and never a day or week twice. Later
  than LATE_AFTER (or no reopen within REOPEN_HORIZON: a 24/7 table, a long closure) it is given up and journalled
  `exploration_missed:<tf>` once. Unreadable or unwritable: nothing is decided (fail closed, `history_gap:<tf>`).
- "The rules' standing common filter at the 21:00 UTC close drops every 1d/1w candidate (quant decision)": the filter
  (byte-unchanged) judges a 1d/1w bar's candidate at its nominal entry (`entry_after`, the rules' `filter_close`), as a
  research replay can, and the engine sends only where it passes at the real entry close too. A Friday 1d bar's entry
  stays inside the Friday filter: weekends are never entered by a 1d decision.
- "`_trail_runners` and `score_twins` on `long_bars.is_bar_close`" (exploration.py).
- The swap of a multi-day hold is projected through the RiskGate: a 1d/1w Intent carries every paid rollover of its
  whole hold (`hold_end`, `Engine._swap_usd_per_oz`); an unknown swap is NaN, which the gate refuses.
- "Warm-up from the long daily history via asof on available_utc": `read_history` reads the bars file (its
  `visible_at`) or, failing it, the long file (its `available_utc`), and `as_of` keeps only rows available at the
  decision close. The engine's window (its complete 1m bars) supplies the recent daily bars; the history must reach
  the window's first complete day, else the arm is held back (never decided on a history with a hole).
- "missing file -> quiet book + history_gap journal, never a crash": a missing or unreadable file, a gap to the window
  or too few rows hold the arm back this close, journalled `history_gap:<tf>` once each time the reason changes.

Nothing here touches the weekend cut, the rollover or weekend gate reasons, the stale rule or any cap
(`exploration.crosses_friday`, `weekend_lane`, `stale_s`: byte-unchanged, tests/test_engine_long_tf.py). The arms ship
dark: settings `engine.timeframes_live` (default: no 1d, no 1w) holds them retired from the start."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
import pandas as pd

from goldbot.config import INTRADAY_LIVE, tf_seconds
from goldbot.data.resample import BAR_COLUMNS

# the nominal entry the rules' common filter judges a 1d/1w bar at (ADR 0081): research's, so the engine and every
# replay share one definition (`arm_rules.long_tf_context` enforces it on every research caller)
from goldbot.research.arm_rules import NOMINAL_ENTRY, entry_after  # noqa: F401 - re-exported

if TYPE_CHECKING:
    from goldbot.data.calendar import SessionTable
    from goldbot.db import Database
    from goldbot.engine.exploration import Arm, Exploration
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

LONG_TFS = ("1d", "1w")
DAY_CLOSE = pd.Timedelta(hours=21)              # the long history's daily roll, UTC
MIN_ROWS = {"1d": 250, "1w": 52}                # warm-up bars at least (a rule may declare more)
WEEKEND = "weekend"                             # a Friday-close decision: recorded blocked, never queued
MISSING_FILE, GAP, SHORT = "missing_file", "gap", "short"
NEEDS_REPLICA = "needs_replica_state"            # the long path carries no spec_ctx
GAP_ACTION = "history_gap:"
_MID = ("open", "high", "low", "close")
# BACKLOG 144 (ADR 0081): when a decision is sent, and how late it may still be
ROLLOVER_END = pd.Timedelta(hours=22, minutes=15)   # the common filter's rollover ends: a 1d entry is sent after it
SETTLE = {"1d": pd.Timedelta(minutes=15), "1w": pd.Timedelta(minutes=60)}   # after the reopen: no opening spreads
LATE_AFTER = {"1d": pd.Timedelta(hours=2), "1w": pd.Timedelta(hours=24)}   # a later entry close: given up
REOPEN_HORIZON = pd.Timedelta(days=4)               # no reopen within it (a 24/7 table, a long closure): given up
MISSED_ACTION = "exploration_missed:"
LATE, NO_REOPEN = "late", "no_reopen"              # why a decision was given up


def is_bar_close(tf: str, ts: pd.Timestamp) -> bool:
    """Whether a bar of `tf` closes at `ts`: 1d at 21:00 UTC Monday to Friday, 1w at Friday 21:00 UTC (the week's
    last daily close), every other timeframe on its UTC clock boundary (as the engine always counted them)."""
    if tf in LONG_TFS:
        at_roll = ts == ts.normalize() + DAY_CLOSE
        return bool(at_roll and (ts.weekday() < 5 if tf == "1d" else ts.weekday() == 4))
    return int(ts.timestamp()) % tf_seconds(tf) == 0


def decision_close(tf: str, ts: pd.Timestamp) -> pd.Timestamp:
    """The last close of `tf` at or before `ts`: 1d the last weekday 21:00 UTC, 1w the last Friday 21:00 UTC."""
    c = ts.normalize() + DAY_CLOSE
    if c > ts:
        c -= pd.Timedelta(days=1)
    back = (c.weekday() - 4) % 7 if tf == "1w" else max(0, c.weekday() - 4)
    return c - pd.Timedelta(days=back)


def reopen_after(sessions: SessionTable, at: pd.Timestamp) -> pd.Timestamp | None:
    """The broker's next reopen after `at` on the engine's session table: the first open minute after the first closed
    one (the daily break, the weekend, a holiday the table knows), or None within REOPEN_HORIZON (a 24/7 table)."""
    grid = pd.date_range(at, at + REOPEN_HORIZON, freq="1min")
    is_open = np.asarray(sessions.is_open(grid), dtype=bool)
    shut = np.flatnonzero(~is_open)
    if not len(shut):
        return None
    back = np.flatnonzero(is_open[shut[0]:])
    return grid[shut[0] + back[0]] if len(back) else None


def entry_close(tf: str, decision: pd.Timestamp, reopen: pd.Timestamp, base_s: int) -> pd.Timestamp:
    """The base close a decision is sent at: the first at or after `reopen` + SETTLE[tf] and, for 1d, after 22:15 UTC
    (the rules' rollover filter ends there, inclusive)."""
    t = reopen + SETTLE[tf]
    if tf == "1d":
        t = max(t, decision.normalize() + ROLLOVER_END + pd.Timedelta(minutes=1))
    return t.ceil(f"{base_s}s")


def hold_end(tf: str, now: pd.Timestamp, hold_bars: int) -> pd.Timestamp | None:
    """Where a 1d/1w entry at `now` is projected to hold to, for its swap through the RiskGate: the `hold_bars`-th close
    of its timeframe after `now` (1d: weekday 21:00 UTC closes, so a hold across a weekend reaches past it and its
    weekend and triple nights are paid; 1w: Friday 21:00 UTC; quant review of ADR 0081). None for every other timeframe
    (the risk day's projection)."""
    if tf not in LONG_TFS:
        return None
    days = pd.date_range(now.normalize(), periods=7 * max(1, hold_bars) + 8, freq="D") + DAY_CLOSE
    closes = days[(days > now) & ((days.dayofweek < 5) if tf == "1d" else (days.dayofweek == 4))]
    return closes[max(1, hold_bars) - 1]


def origin(tf: str, bar_ts: pd.Timestamp, decision_utc: pd.Timestamp | None) -> pd.Timestamp:
    """Where a signal's hold and its order id count from (BACKLOG 254, ADR 0081: "the time exit of a 1d/1w position
    counts from its decision close (order id, `_week_due`, the barrier and `crosses_friday` alike)"): a 1d/1w signal's
    decision close (`decision_utc`, set by `_decide`), every other signal's bar close (`bar_ts`), unchanged."""
    return decision_utc if tf in LONG_TFS and decision_utc is not None else bar_ts


def hold_s(tf: str, hold_bars: int, bar_ts: pd.Timestamp, decision_utc: pd.Timestamp | None,
           now: pd.Timestamp) -> int:
    """A signal's hold from `now` in seconds: a 1d/1w signal holds to the history run's exit, the `hold_bars`-th close
    of its timeframe after its decision close (`hold_end`), so a send up to 2.5 h (1d) or a day (1w catch-up) after
    the decision close shortens it by that much; every other signal holds `hold_bars` x its timeframe (unchanged)."""
    if tf not in LONG_TFS or decision_utc is None:
        return hold_bars * tf_seconds(tf)
    end = hold_end(tf, decision_utc, hold_bars)
    assert end is not None
    return max(0, int((end - now).total_seconds()))


def time_exit_due(tf: str, client_order_id: str | None, max_bars: int, base_s: int, at: pd.Timestamp) -> bool:
    """A 1d/1w position at its history time exit: `hold_end` of the decision close its order id carries (`origin`)
    and its hold (`max_bars` base bars back in bars of `tf`). False for every other timeframe or an id that carries
    no close (the base-bar barrier, counted from the send and so later, stays the backstop): only ever closes
    earlier."""
    from goldbot.engine.exploration import signal_close
    decision = signal_close(client_order_id) if tf in LONG_TFS else None
    if decision is None:
        return False
    end = hold_end(tf, decision, max(1, max_bars * base_s // tf_seconds(tf)))
    return end is not None and at >= end


def _store_frame(ts_utc: pd.DatetimeIndex, visible: pd.DatetimeIndex, mid: dict[str, np.ndarray],
                 spread: np.ndarray, ticks: np.ndarray) -> pd.DataFrame:
    """Store-schema daily bars from mid prices: bid = ask = mid, so `resample.mid` gives back exactly the mid the long
    history (and the WS-3 screen) holds; the spread is carried as its own column."""
    f: dict[str, Any] = {"ts_utc": ts_utc, "visible_at": visible}
    for side in ("bid", "ask"):
        for c in _MID:
            f[f"{side}_{c}"] = mid[c]
    f["tick_count"], f["spread_mean"], f["spread_max"] = ticks, spread, spread
    return pd.DataFrame(f)[BAR_COLUMNS]


def read_history(root: Path, symbol: str) -> pd.DataFrame | None:
    """The long daily history of `symbol` under `root` in store schema, sorted by visibility: `bars/<key>_1d.parquet`
    (visible_at; mid open/high/low/close and spread_mean), else `long/<key>_daily_long.parquet` (available_utc; a
    close-only row is its close on every price). None when neither file exists; an unreadable file raises."""
    key = symbol.lower()
    bars, long = root / "bars" / f"{key}_1d.parquet", root / "long" / f"{key}_daily_long.parquet"
    if bars.exists():
        d = pd.read_parquet(bars, columns=["visible_at", "open", "high", "low", "close", "spread_mean"])
        vis, spread = d["visible_at"], d["spread_mean"].to_numpy(dtype=float)
    elif long.exists():
        d = pd.read_parquet(long, columns=["available_utc", "open", "high", "low", "close"])
        vis, spread = d["available_utc"], np.full(len(d), np.nan)
    else:
        return None
    d = d.assign(_vis=pd.to_datetime(vis, utc=True), _spread=spread)
    d = d[np.isfinite(d["close"].to_numpy(dtype=float))]
    d = d.sort_values("_vis", kind="stable").drop_duplicates("_vis", keep="last").reset_index(drop=True)
    c = d["close"].to_numpy(dtype=float)
    mid = {k: np.where(np.isfinite(v := d[k].to_numpy(dtype=float)), v, c) for k in _MID}
    visible = pd.DatetimeIndex(d["_vis"])
    opened = visible - pd.Timedelta(days=1)                 # a bar opens at the previous one's close
    if len(visible) > 1:
        opened = pd.DatetimeIndex([opened[0], *visible[:-1]])
    return _store_frame(opened, visible, mid, d["_spread"].to_numpy(dtype=float), np.zeros(len(d), dtype=np.int64))


def as_of(history: pd.DataFrame, through: pd.Timestamp) -> pd.DataFrame:
    """The rows available at `through` (point in time)."""
    return history[pd.DatetimeIndex(history["visible_at"]) <= through].reset_index(drop=True)


def daily_from_1m(b1: pd.DataFrame) -> pd.DataFrame:
    """The engine's minutes as daily bars on the 21:00 UTC roll (a minute in [D-1 21:00, D 21:00) is day D's; the
    weekend's minutes belong to Monday), mid prices as the history's. ts_utc is the previous weekday's 21:00."""
    if b1.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    ts = pd.DatetimeIndex(pd.to_datetime(b1["ts_utc"], utc=True))
    close = (ts + pd.Timedelta(hours=3)).normalize() + DAY_CLOSE
    close = close + pd.to_timedelta(np.select([close.dayofweek == 5, close.dayofweek == 6], [2, 1], 0), unit="D")
    m = pd.DataFrame({c: (b1[f"bid_{c}"].to_numpy(dtype=float) + b1[f"ask_{c}"].to_numpy(dtype=float)) / 2
                      for c in _MID})
    m["spread"] = b1["spread_mean"].to_numpy(dtype=float)
    m["ticks"] = b1["tick_count"].to_numpy(dtype=np.int64)
    m["_k"] = close
    g = m.groupby("_k", sort=True)
    vis = pd.DatetimeIndex(g.size().index)
    opened = vis - pd.to_timedelta(np.where(vis.dayofweek == 0, 3, 1), unit="D")
    mid = {"open": g["open"].first().to_numpy(), "high": g["high"].max().to_numpy(),
           "low": g["low"].min().to_numpy(), "close": g["close"].last().to_numpy()}
    return _store_frame(opened, vis, mid, g["spread"].mean().to_numpy(), g["ticks"].sum().to_numpy())


def weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """ISO weeks (W-FRI) of daily bars keyed by the week of each bar's close, known at the week's last daily close
    (the WS-3 screen's `catalogue.data._weekly`, on store-schema bars)."""
    if daily.empty:
        return daily
    vis = pd.DatetimeIndex(daily["visible_at"]) - pd.Timedelta(1, "ns")
    key = vis.tz_localize(None).to_period("W-FRI").astype(str)
    g = daily.groupby(np.asarray(key), sort=False)
    out: dict[str, Any] = {"ts_utc": g["ts_utc"].first(), "visible_at": g["visible_at"].last()}
    for side in ("bid", "ask"):
        out[f"{side}_open"], out[f"{side}_high"] = g[f"{side}_open"].first(), g[f"{side}_high"].max()
        out[f"{side}_low"], out[f"{side}_close"] = g[f"{side}_low"].min(), g[f"{side}_close"].last()
    out["tick_count"], out["spread_mean"], out["spread_max"] = (g["tick_count"].sum(), g["spread_mean"].mean(),
                                                                 g["spread_max"].max())
    return pd.DataFrame(out)[BAR_COLUMNS].sort_values("ts_utc", kind="stable").reset_index(drop=True)


def joined(history: pd.DataFrame, window_1m: pd.DataFrame, through: pd.Timestamp) -> pd.DataFrame | str:
    """The daily bars at `through`: the history's before the window's first complete day, then the window's (its
    first, possibly partial, day left out). GAP when the window has no complete day, when its days are not consecutive
    weekday closes (a weekday with no minutes, a holiday included) or the history does not reach the day before it (no
    bar may be missing between them)."""
    win = daily_from_1m(window_1m).iloc[1:]
    win = win[pd.DatetimeIndex(win["visible_at"]) <= through].reset_index(drop=True)
    if win.empty:
        return GAP
    vis = pd.DatetimeIndex(win["visible_at"])
    step = pd.to_timedelta(np.where(vis[:-1].dayofweek == 4, 3, 1), unit="D")
    if len(vis) > 1 and not (vis[1:] == vis[:-1] + step).all():       # a weekday with no minutes inside the window
        return GAP
    first = pd.Timestamp(win["visible_at"].iloc[0])
    pre = as_of(history, through)
    pre = pre[pd.DatetimeIndex(pre["visible_at"]) < first]
    if pre.empty or pd.Timestamp(pre["visible_at"].iloc[-1]) < pd.Timestamp(win["ts_utc"].iloc[0]):
        return GAP
    return pd.concat([pre, win], ignore_index=True)


def rows_needed(arm: Arm) -> int:
    """Bars of the arm's timeframe its rule must see: MIN_ROWS, or more when the rule declares it."""
    from goldbot.engine.exploration_context import history_bars
    from goldbot.research.arm_rules import lookback_bars
    return max(lookback_bars(arm.spec, MIN_ROWS[arm.tf]), history_bars(arm.spec))


class _Ledger(Protocol):
    def connection(self) -> Any: ...
    def write(self) -> Any: ...


class Decided:
    """aux.db `long_tf_decisions` of one account: the last decision close each 1d/1w arm was decided or given up for
    (BACKLOG 144: "the 1w missed-week catch-up, persisted"). Read once, written before a signal is queued. No ledger,
    or one that fails, raises: the caller decides nothing (fail closed)."""

    def __init__(self, db: Database | _Ledger | None, account_id: str):
        self.db, self.account_id = db, account_id
        self._last: dict[str, pd.Timestamp] | None = None

    def last(self, arm_id: str) -> pd.Timestamp | None:
        if self.db is None:
            raise RuntimeError("no exploration ledger")
        if self._last is None:
            with self.db.connection() as conn:
                rows = conn.execute("SELECT arm_id, decision_ns FROM long_tf_decisions WHERE account_id = ?",
                                    (self.account_id,)).fetchall()
            self._last = {str(a): pd.Timestamp(int(ns), unit="ns", tz="UTC") for a, ns in rows}
        return self._last.get(arm_id)

    def mark(self, arm_id: str, decision: pd.Timestamp) -> None:
        if self.db is None or self._last is None:
            raise RuntimeError("long_tf_decisions not read")
        with self.db.write() as conn:
            conn.execute("INSERT INTO long_tf_decisions (account_id, arm_id, decision_ns, updated_ns)"
                         " VALUES (?, ?, ?, ?) ON CONFLICT (account_id, arm_id) DO UPDATE SET"
                         " decision_ns = excluded.decision_ns, updated_ns = excluded.updated_ns",
                         (self.account_id, arm_id, int(decision.value), time.time_ns()))
        self._last[arm_id] = decision


class LongHistory:
    """The long daily history of one book, read once and again when its file changes, and the arms held back."""

    def __init__(self, root: str | None, symbol: str, decided: Decided | None = None):
        self.root, self.symbol = (Path(root) if root else None), symbol
        self._cache: tuple[tuple[str, int], pd.DataFrame] | None = None
        self.gaps: dict[str, str] = {}              # arm -> why it is held back (journalled once a change)
        self.held_since: dict[str, tuple[str, pd.Timestamp]] = {}   # arm -> (tf, the first close it was held back at)
        self.decided = decided if decided is not None else Decided(None, "")
        self.reopens: dict[pd.Timestamp, pd.Timestamp | None] = {}     # decision close -> the broker's reopen after it

    def history(self) -> pd.DataFrame | None:
        if self.root is None:
            return None
        key = self.symbol.lower()
        for path in (self.root / "bars" / f"{key}_1d.parquet", self.root / "long" / f"{key}_daily_long.parquet"):
            if path.exists():
                stamp = (str(path), path.stat().st_mtime_ns)
                if self._cache is None or self._cache[0] != stamp:
                    got = read_history(self.root, self.symbol)
                    if got is None:
                        return None
                    self._cache = (stamp, got)
                return self._cache[1]
        return None

    def bars(self, arm: Arm, complete: pd.DataFrame, through: pd.Timestamp) -> pd.DataFrame | str:
        """The arm's bars through `through` (daily, or weekly of them), or why it is held back."""
        hist = self.history()
        if hist is None:
            return MISSING_FILE
        got = joined(hist, complete, through)
        if isinstance(got, str):
            return got
        bars = weekly(got) if arm.tf == "1w" else got
        return SHORT if len(bars) < rows_needed(arm) else bars


def _held(eng: Engine, state: LongHistory, arm: Arm, why: str, at: pd.Timestamp) -> None:
    if state.gaps.get(arm.arm_id) == why:
        return
    state.gaps[arm.arm_id] = why
    state.held_since.setdefault(arm.arm_id, (arm.tf, at))
    log.warning("exploration arm %s (%s) held back at %s: %s", arm.arm_id, arm.tf, at, why)
    eng.decisions.append({"ts": time.time(), "agent": arm.tag, "action": GAP_ACTION + arm.tf, "arm": arm.arm_id,
                          "reason": why, "bar": at.isoformat()})


def published(ex: Exploration | None) -> dict[str, Any]:
    """engine_<account>.json `long_history_gap`: per held-back 1d/1w arm its timeframe, why, and the engine close it
    has been held back since (BACKLOG 251: health warns, NOTIFY_ON_WARN, once that is more than a day). {} = none."""
    state = getattr(ex, "long", None)
    if state is None:
        return {"long_history_gap": {}}
    return {"long_history_gap": {a: {"tf": tf, "reason": state.gaps.get(a), "since": at.isoformat()}
                                 for a, (tf, at) in sorted(state.held_since.items())}}


def _given_up(eng: Engine, state: LongHistory, arm: Arm, decision: pd.Timestamp, why: str,
              at: pd.Timestamp) -> None:
    """A decision never sent (LATE, NO_REOPEN): persisted as decided, journalled `exploration_missed:<tf>` once."""
    state.decided.mark(arm.arm_id, decision)
    log.warning("exploration arm %s (%s): decision %s given up at %s (%s)", arm.arm_id, arm.tf, decision, at, why)
    eng.decisions.append({"ts": time.time(), "agent": arm.tag, "action": MISSED_ACTION + arm.tf, "arm": arm.arm_id,
                          "reason": why, "decision": decision.isoformat(), "bar": at.isoformat()})


def _tradable(eng: Engine, complete: pd.DataFrame, reopen: pd.Timestamp, close_ts: pd.Timestamp,
              base_s: int) -> bool:
    """The broker has reopened in fact: a minute at or after `reopen` in the engine's bars, every minute of the base
    bar closing at `close_ts` open on the session table, and the rules' common filter passing at `close_ts`."""
    from goldbot.research.arm_rules import common_filter_ok
    if complete.empty or pd.Timestamp(complete["ts_utc"].iloc[-1]) < reopen:
        return False
    bar = pd.date_range(close_ts - pd.Timedelta(seconds=base_s), close_ts, freq="1min", inclusive="left")
    none = np.zeros(0, dtype=np.int64)
    return bool(np.asarray(eng.sessions.is_open(bar), dtype=bool).all()
                and common_filter_ok(pd.DatetimeIndex([close_ts]), none, none)[0])


def _due(eng: Engine, state: LongHistory, arm: Arm, complete: pd.DataFrame, close_ts: pd.Timestamp,
         base_s: int) -> pd.Timestamp | None:
    """The decision close `arm` is to be decided for at `close_ts`, or None: its last close (`decision_close`; a
    Friday 1d one never), not decided yet, at or after its entry close, the broker tradable. Past LATE_AFTER, or with
    no reopen, it is given up."""
    d = decision_close(arm.tf, close_ts)
    if arm.tf == "1d" and d.weekday() == 4:
        return None                                  # the weekend: recorded blocked at its own close (`step`)
    last = state.decided.last(arm.arm_id)
    if last is not None and last >= d:
        return None
    if d not in state.reopens:
        if len(state.reopens) > 32:
            state.reopens.clear()
        state.reopens[d] = reopen_after(eng.sessions, d)
    reopen = state.reopens[d]
    if reopen is None:
        _given_up(eng, state, arm, d, NO_REOPEN, close_ts)
        return None
    e = entry_close(arm.tf, d, reopen, base_s)
    if close_ts < e:
        return None
    if close_ts > e + LATE_AFTER[arm.tf]:
        _given_up(eng, state, arm, d, LATE, close_ts)
        return None
    return d if _tradable(eng, complete, reopen, close_ts, base_s) else None


def step(eng: Engine, ex: Exploration, arms: list[Arm], complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    """At a base close: each live 1d/1w arm whose decision is due (`_due`) decided on its warm-up and the engine's
    bars through its decision close (`exploration._signal`), its signal stamped with this close and its decision
    close in `decision_utc`. A Friday 1d decision is recorded blocked WEEKEND at its close, never queued. An arm held
    back, whose inputs raise or whose decision store cannot be read, is journalled and the others go on."""
    from goldbot.engine import exploration_context
    live = set(getattr(eng.cfg, "timeframes_live", INTRADAY_LIVE))
    arms = [a for a in arms if a.arm_id not in ex.retired and a.tf in live]
    if not arms:
        return
    if ex.long is None:
        ex.long = LongHistory(getattr(eng.cfg, "long_history_dir", None), eng.cfg.release_symbol,
                              Decided(getattr(ex, "db", None), eng.cfg.account_id))
    state: LongHistory = ex.long
    base_s = tf_seconds(getattr(eng.cfg, "decision_tf", "15m"))
    for arm in arms:
        friday = arm.tf == "1d" and is_bar_close("1d", close_ts) and close_ts.weekday() == 4
        try:
            d = close_ts if friday else _due(eng, state, arm, complete, close_ts, base_s)
        except Exception as exc:                            # noqa: BLE001 - no decision store: nothing decided
            _held(eng, state, arm, f"decisions_unread:{type(exc).__name__}", close_ts)
            continue
        if d is None:
            continue
        if arm.arm_id in ex.needs_context and exploration_context.replica_bars(arm.spec) > 0:
            _held(eng, state, arm, NEEDS_REPLICA, close_ts)             # never decided without its replica state
            continue
        _decide(eng, ex, state, arm, complete, d, close_ts)


def _decide(eng: Engine, ex: Exploration, state: LongHistory, arm: Arm, complete: pd.DataFrame, d: pd.Timestamp,
            close_ts: pd.Timestamp) -> None:
    """`arm` decided for its decision close `d` at `close_ts` (`d == close_ts`: a Friday 1d close, refused WEEKEND).
    Held back while its bars are not there (retried until given up); persisted as decided before its signal."""
    from goldbot.engine import exploration as X
    try:
        got = state.bars(arm, complete, d)
    except Exception as exc:                            # noqa: BLE001 - a bad file holds the arm back, logged
        got = f"error:{type(exc).__name__}"
    if isinstance(got, str):
        _held(eng, state, arm, got, close_ts)
        return
    state.gaps.pop(arm.arm_id, None)
    state.held_since.pop(arm.arm_id, None)
    friday = d == close_ts
    if not friday:
        try:
            state.decided.mark(arm.arm_id, d)           # before the signal: a restart never sends it twice
        except Exception as exc:                        # noqa: BLE001 - unwritable: nothing decided
            _held(eng, state, arm, f"decisions_unread:{type(exc).__name__}", close_ts)
            return
    b1 = complete[pd.DatetimeIndex(complete["visible_at"]) <= d].reset_index(drop=True)
    before = len(ex.queue)
    X._signal(eng, ex, arm, got, b1, close_ts,
              filter_close=entry_after(pd.DatetimeIndex(pd.to_datetime(got["visible_at"], utc=True)), arm.tf))
    new = [ex.queue.pop() for _ in range(len(ex.queue) - before)][::-1]
    for p in new:
        if friday:                                      # a Friday decision: refused, never queued
            X._skipped(eng, ex, arm, p, WEEKEND)
        else:
            ex.queue.append(p.model_copy(update={"decision_utc": d}))
