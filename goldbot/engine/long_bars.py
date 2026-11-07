"""1d and 1w exploration arms in the engine (WS-4, consolidated plan 2026-11-07).

The plan's rules, as built here:
- "1d closes at 21:00 UTC" (`is_bar_close`): the long daily history's roll (raw/daily/bars/<symbol>_1d.parquet, every
  row visible at 21:00 UTC, the weekend folded into Monday's bar). A 1d arm decides at a weekday's 21:00 UTC close,
  on its warm-up rows and the engine's own daily bars.
- "'1w' added to the Timeframe literal and TF_SECONDS": a weekly bar is the ISO week (W-FRI) of the daily bars, known
  at the week's last daily close (as the WS-3 screen's `catalogue.data._weekly`), normally Friday 21:00 UTC.
- "The 1w decision runs as a fresh decision on the first pass after the week opens, with the bracket from the
  send-time price and full RiskGate. A Fri 21:00 decision is refused as 'weekend' and is never queued." A 1w arm is
  never decided at Friday's close: it decides at the first base close holding the week's first minute (Sunday or
  Monday UTC), its signal stamped with that close, so its stop and target distances go out from the send-time price
  through `exploration._send` and the RiskGate like every arm's. A 1d decision at Friday's 21:00 close is journalled
  and recorded blocked `weekend` (`exploration._skipped`), never queued.
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
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from goldbot.config import INTRADAY_LIVE, tf_seconds
from goldbot.data.resample import BAR_COLUMNS

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

LONG_TFS = ("1d", "1w")
DAY_CLOSE = pd.Timedelta(hours=21)              # the long history's daily roll, UTC
MIN_ROWS = {"1d": 250, "1w": 52}                # warm-up bars at least (a rule may declare more)
WEEKEND = "weekend"                             # a Friday-close decision: recorded blocked, never queued
MISSING_FILE, GAP, SHORT = "missing_file", "gap", "short"
GAP_ACTION = "history_gap:"
_MID = ("open", "high", "low", "close")


def is_bar_close(tf: str, ts: pd.Timestamp) -> bool:
    """Whether a bar of `tf` closes at `ts`: 1d at 21:00 UTC Monday to Friday, 1w at Friday 21:00 UTC (the week's
    last daily close), every other timeframe on its UTC clock boundary (as the engine always counted them)."""
    if tf in LONG_TFS:
        at_roll = ts == ts.normalize() + DAY_CLOSE
        return bool(at_roll and (ts.weekday() < 5 if tf == "1d" else ts.weekday() == 4))
    return int(ts.timestamp()) % tf_seconds(tf) == 0


def week_open(minutes: pd.DatetimeIndex, close_ts: pd.Timestamp, base_s: int) -> pd.Timestamp | None:
    """The weekly bar decided at `close_ts`: the last Friday 21:00 UTC before it, when the week's first minute (the
    first one after it on a Sunday or Monday UTC) is inside the base bar closing at `close_ts`; else None. One close a
    week, the same after a restart."""
    back = (close_ts.weekday() - 4) % 7
    fri = close_ts.normalize() - pd.Timedelta(days=back) + DAY_CLOSE
    if fri >= close_ts:
        fri -= pd.Timedelta(days=7)
    after = minutes[(minutes >= fri) & np.isin(minutes.dayofweek, (6, 0))]
    if not len(after):
        return None
    first = after[0]
    return fri if close_ts - pd.Timedelta(seconds=base_s) <= first < close_ts else None


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
    first, possibly partial, day left out). GAP when the window has no complete day or the history does not reach the
    day before it (no bar may be missing between them)."""
    win = daily_from_1m(window_1m).iloc[1:]
    win = win[pd.DatetimeIndex(win["visible_at"]) <= through].reset_index(drop=True)
    if win.empty:
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


class LongHistory:
    """The long daily history of one book, read once and again when its file changes, and the arms held back."""

    def __init__(self, root: str | None, symbol: str):
        self.root, self.symbol = (Path(root) if root else None), symbol
        self._cache: tuple[tuple[str, int], pd.DataFrame] | None = None
        self.gaps: dict[str, str] = {}              # arm -> why it is held back (journalled once a change)

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
    log.warning("exploration arm %s (%s) held back at %s: %s", arm.arm_id, arm.tf, at, why)
    eng.decisions.append({"ts": time.time(), "agent": arm.tag, "action": GAP_ACTION + arm.tf, "arm": arm.arm_id,
                          "reason": why, "bar": at.isoformat()})


def step(eng: Engine, ex: Exploration, arms: list[Arm], complete: pd.DataFrame, close_ts: pd.Timestamp) -> None:
    """At a base close: each live 1d arm at its 21:00 UTC close and each live 1w arm at the first close of the week,
    its rule on its warm-up and the engine's bars (`exploration._signal`). A Friday decision is recorded blocked
    WEEKEND, never queued. An arm held back, or whose inputs raise, is journalled and the others go on."""
    from goldbot.engine import exploration as X
    live = set(getattr(eng.cfg, "timeframes_live", INTRADAY_LIVE))
    arms = [a for a in arms if a.arm_id not in ex.retired and a.tf in live]
    if not arms:
        return
    if ex.long is None:
        ex.long = LongHistory(getattr(eng.cfg, "long_history_dir", None), eng.cfg.release_symbol)
    state: LongHistory = ex.long
    base_s = tf_seconds(getattr(eng.cfg, "decision_tf", "15m"))
    minutes = pd.DatetimeIndex(pd.to_datetime(complete["ts_utc"], utc=True))
    for arm in arms:
        due = is_bar_close("1d", close_ts) if arm.tf == "1d" else week_open(minutes, close_ts, base_s) is not None
        if not due:
            continue
        try:
            got = state.bars(arm, complete, close_ts)
        except Exception as exc:                            # noqa: BLE001 - a bad file holds the arm back, logged
            got = f"error:{type(exc).__name__}"
        if isinstance(got, str):
            _held(eng, state, arm, got, close_ts)
            continue
        state.gaps.pop(arm.arm_id, None)
        before = len(ex.queue)
        X._signal(eng, ex, arm, got, complete, close_ts)
        if close_ts.weekday() == 4:                         # a Friday decision: refused, never queued
            for p in list(ex.queue)[before:]:
                ex.queue.remove(p)
                X._skipped(eng, ex, arm, p, WEEKEND)
