"""The 1m bid/ask minutes of ONE account's broker, point in time, for replays that must price on the broker the trade
ran on (exploration labels, goldbot/research/exploration_data.py).

Brain 2026-10-27: the nightly learner read `bars_1m` with source=<account>. On the brain `bars_1m` holds only the
reference feed (source=dukascopy), so every replay saw no bars, 0 of 349 candidates were ever labelled and every
bandit run rested on n = 0, with the job reporting ok. The account's own minutes live in, in this order:

  1. `bars_1m_live`   source = the broker name, symbol = the broker's symbol (XAUUSD+ on Vantage): the engine's minutes
                      from the broker's ticks, written continuously (goldbot/engine/history.py LIVE_TABLE);
  2. `bars_1m_broker` source = the account id: the broker's M1 as its terminal reports it (feed_reconcile, nightly);
  3. `bars_1m`        source = the account id: legacy, kept so an older store still reads.

A minute quarantined (bars_quarantine, source = the broker or the account) is read from no feed at all. Otherwise a
minute is taken from the first feed that has it visible by `as_of`: all three are the same broker's prices, never
another broker's and never the reference feed. Point in time: a row counts only once visible (`visible_at`, and
`available_utc` when stored; a row with neither is visible at its minute's close), and of several stored versions of
one minute the last one visible by `as_of`. Timestamps are compared in ns (`timeutil.epoch_ns`), so the parquet unit
(s/ms/us) never matters.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns

MINUTE = pd.Timedelta(minutes=1)
QUARANTINE = "bars_quarantine"           # data/quarantine.py QUARANTINE_TABLE of bars_1m and bars_1m_live
PRICE_COLUMNS = [f"{s}_{c}" for s in ("bid", "ask") for c in ("open", "high", "low", "close")]
FEEDS: tuple[tuple[str, str], ...] = (("bars_1m_live", "broker"), ("bars_1m_broker", "account"),
                                      ("bars_1m", "account"))


def _visible_ns(df: pd.DataFrame) -> np.ndarray:
    """When each row became knowable: the later of visible_at and available_utc when stored, else the minute's close."""
    ts = pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True))
    none = np.iinfo(np.int64).min
    close = epoch_ns(ts + MINUTE)
    stamped = np.full(len(df), none, dtype=np.int64)
    for col in ("visible_at", "available_utc"):
        if col in df.columns:
            v = pd.DatetimeIndex(pd.to_datetime(df[col], utc=True))
            stamped = np.maximum(stamped, np.where(np.asarray(v.isna()), none, epoch_ns(v)))
    return np.where(stamped == none, close, stamped)


def account_bars_1m(store: Any, *, account_id: str, broker: str, symbol: str, start: pd.Timestamp | None,
                    end: pd.Timestamp | None, as_of: pd.Timestamp) -> pd.DataFrame:
    """ts_utc + bid/ask OHLC of `account_id`'s broker in [start, end), visible by `as_of` (module docstring), one row
    per minute, sorted, with the `feed` it came from. Empty when no feed has a minute."""
    cut = int(pd.Timestamp(as_of).tz_convert("UTC").value)
    parts: list[pd.DataFrame] = []
    # a minute quarantined from the broker's live feed or the account's bars is left out of EVERY feed (quant
    # 2026-10-27, MEDIUM): back-filling it from the terminal's M1 would replay the very spike that was quarantined
    taken: set[int] = set()
    for source in dict.fromkeys((broker, account_id)):
        q = store.read(QUARANTINE, source=source, symbol=symbol, start=start, end=end, columns=["ts_utc"])
        if q is not None and not q.empty:
            taken.update(epoch_ns(pd.DatetimeIndex(pd.to_datetime(q["ts_utc"], utc=True))).tolist())
    for table, keyed in FEEDS:
        source = broker if keyed == "broker" else account_id
        df = store.read(table, source=source, symbol=symbol, start=start, end=end)
        if df is None or df.empty or not set(PRICE_COLUMNS) <= set(df.columns):
            continue
        df = df.reset_index(drop=True)
        df = df[_visible_ns(df) <= cut]
        if df.empty:
            continue
        df = df.assign(_vis=_visible_ns(df)).sort_values(["ts_utc", "_vis"], kind="stable")
        df = df.drop_duplicates("ts_utc", keep="last")                 # the last version visible by as_of
        ts_ns = epoch_ns(pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True)))
        new = np.array([t not in taken for t in ts_ns.tolist()], dtype=bool)
        if not new.any():
            continue
        taken.update(ts_ns[new].tolist())
        parts.append(df.loc[new, ["ts_utc", *PRICE_COLUMNS]].assign(feed=table))
    if not parts:
        return pd.DataFrame(columns=["ts_utc", *PRICE_COLUMNS, "feed"])
    out = pd.concat(parts, ignore_index=True)
    out["ts_utc"] = pd.to_datetime(out["ts_utc"], utc=True)
    return out.sort_values("ts_utc", kind="stable").reset_index(drop=True)


__all__ = ["FEEDS", "account_bars_1m"]
