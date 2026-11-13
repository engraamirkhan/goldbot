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


META_COLUMNS = ["tick_count", "spread_mean", "spread_max"]
LIVE_WRITE_GRACE = pd.Timedelta(minutes=2)   # a minute stored this soon after its close was built live
REFERENCE_FEED = ("bars_1m", "dukascopy")     # the release minutes the engine warm-starts on (engine/history.py)


def minute_versions(store: Any, *, account_id: str, broker: str, symbol: str, start: pd.Timestamp | None,
                    end: pd.Timestamp | None, as_of: pd.Timestamp, reference: str | None = "XAUUSD") -> pd.DataFrame:
    """Every stored version of `account_id`'s broker minutes in [start, end) visible by `as_of`, from each feed in
    FEEDS order (`rank` 0, 1, 2), then the release minutes of the `reference` symbol (the engine's release_symbol) the
    engine loads after a restart (rank 3, used only where no broker feed has the minute; None: none). One row per stored row, with `visible_at`: its nominal
    visibility (`_visible_ns` without the write time), or its write time `available_utc` when that is more than
    LIVE_WRITE_GRACE later (a minute back-filled or re-pulled after the fact is known only once written; one the
    engine built live and stored at once was known at its close, as the engine knew it), `written` (a write time is
    stored), the bar columns the features read (META_COLUMNS, NaN where a feed has none) and `rank`. Quarantined
    minutes are left out of every feed, as in `account_bars_1m`. `as_of_minutes` cuts it at a decision."""
    cut = int(pd.Timestamp(as_of).tz_convert("UTC").value)
    bad: set[int] = set()
    for source in dict.fromkeys((broker, account_id)):
        q = store.read(QUARANTINE, source=source, symbol=symbol, start=start, end=end, columns=["ts_utc"])
        if q is not None and not q.empty:
            bad.update(epoch_ns(pd.DatetimeIndex(pd.to_datetime(q["ts_utc"], utc=True))).tolist())
    feeds = [(t, broker if k == "broker" else account_id, symbol) for t, k in FEEDS]
    if reference:
        feeds.append((REFERENCE_FEED[0], REFERENCE_FEED[1], reference))
    parts: list[pd.DataFrame] = []
    for rank, (table, source, sym) in enumerate(feeds):
        df = store.read(table, source=source, symbol=sym, start=start, end=end)
        if df is None or df.empty or not set(PRICE_COLUMNS) <= set(df.columns):
            continue
        df = df.reset_index(drop=True)
        nominal = _visible_ns(df.drop(columns=["available_utc"], errors="ignore"))
        if "available_utc" in df.columns:
            w = pd.DatetimeIndex(pd.to_datetime(df["available_utc"], utc=True))
            wns = np.where(np.asarray(w.isna()), np.iinfo(np.int64).min, epoch_ns(w))
            # written within LIVE_WRITE_GRACE of its close: built live, known at its close as the engine knew it;
            # written later (a back-fill, a re-pull): known only from its write time
            vis = np.where(wns > nominal + LIVE_WRITE_GRACE.value, wns, nominal)
        else:
            vis = nominal
        ts_ns = epoch_ns(pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True)))
        keep = (vis <= cut) & np.array([t not in bad for t in ts_ns.tolist()], dtype=bool)
        if not keep.any():
            continue
        part = df.loc[keep, ["ts_utc", *PRICE_COLUMNS]].copy()
        for col in META_COLUMNS:
            part[col] = pd.to_numeric(df.loc[keep, col], errors="coerce") if col in df.columns else np.nan
        part["visible_at"] = pd.to_datetime(vis[keep], utc=True)
        written = pd.to_datetime(df["available_utc"], utc=True).notna().to_numpy() if "available_utc" in df.columns \
            else np.zeros(len(df), dtype=bool)
        part["written"] = written[keep]
        part["rank"] = rank
        parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["ts_utc", *PRICE_COLUMNS, *META_COLUMNS, "visible_at", "written", "rank"])
    out = pd.concat(parts, ignore_index=True)
    out["ts_utc"] = pd.to_datetime(out["ts_utc"], utc=True)
    return out.sort_values(["ts_utc", "rank"], kind="stable").reset_index(drop=True)


def as_of_minutes(versions: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    """The minutes as `account_bars_1m` would have read them with as_of = `at`: of the versions visible by `at`, per
    minute the first feed's (lowest `rank`) last visible version. Sorted by ts_utc."""
    v = versions[(pd.to_datetime(versions["visible_at"], utc=True) <= pd.Timestamp(at).tz_convert("UTC")).to_numpy()]
    v = v.sort_values(["ts_utc", "rank", "visible_at"], ascending=[True, True, False], kind="stable")
    return v.drop_duplicates("ts_utc", keep="first").reset_index(drop=True)


__all__ = ["FEEDS", "META_COLUMNS", "REFERENCE_FEED", "account_bars_1m", "as_of_minutes", "minute_versions"]
