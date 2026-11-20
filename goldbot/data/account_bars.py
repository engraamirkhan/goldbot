"""The 1m bid/ask minutes of ONE account's broker, point in time, for replays that must price on the broker the trade
ran on (exploration labels, goldbot/research/exploration_data.py).

Brain 2026-10-27: the nightly learner read `bars_1m` with source=<account>. On the brain `bars_1m` holds only the
reference feed (source=dukascopy), so every replay saw no bars, 0 of 349 candidates were ever labelled and every
bandit run rested on n = 0, with the job reporting ok. The account's own minutes live in, in this order:

  1. `bars_1m_live`   source = the account id, symbol = the broker's symbol (XAUUSD+ on Vantage): the engine's minutes
                      from the broker's ticks, written continuously (goldbot/engine/history.py LIVE_TABLE); under them
                      the minutes stored before the per-account key (source = the broker name, `books.legacy_live_key`),
                      only before the account's cut-over (`live_cutover`: its first engine start that held its writer
                      lock, never its first own minute, which a back-fill moves; quant condition 4 of ADR
                      0091) and only where the account's own key has no copy of the minute;
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

import json
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns

MINUTE = pd.Timedelta(minutes=1)
QUARANTINE = "bars_quarantine"           # data/quarantine.py QUARANTINE_TABLE of bars_1m and bars_1m_live
PRICE_COLUMNS = [f"{s}_{c}" for s in ("bid", "ask") for c in ("open", "high", "low", "close")]
# "live": bars_1m_live, the account's own key with the broker's legacy key under it (`live_rows`)
FEEDS: tuple[tuple[str, str], ...] = (("bars_1m_live", "live"), ("bars_1m_broker", "account"),
                                      ("bars_1m", "account"))
LIVE_TABLE = "bars_1m_live"
BOOT_CHECK = "engine_boot"               # engine/history.py BOOT_CHECK: one dq event per engine start
OWN, LEGACY = "own", "legacy"            # `keyed`: which live key a minute was read from


def engine_boots(store: Any, account_id: str) -> pd.DataFrame:
    """Every engine start of `account_id` (dq_events `engine_boot`, any symbol, all time): ts_utc (when it took or
    tried its writer lock), writer (its boot id), locked (it holds the account's exclusive writer lock), sorted.
    Raises what the store read raises (callers fail closed)."""
    cols = ["ts_utc", "writer", "locked"]
    ev = store.read("dq_events", source=account_id, symbol=None)
    if ev is None or ev.empty or "check" not in ev.columns or "writer" not in ev.columns:
        return pd.DataFrame(columns=cols)
    ev = ev[ev["check"].astype(str) == BOOT_CHECK]
    if ev.empty:
        return pd.DataFrame(columns=cols)

    def locked(detail: Any) -> bool:
        try:
            return json.loads(str(detail)).get("writer_lock") is True
        except (ValueError, AttributeError):
            return False                          # a record that does not parse proves nothing
    out = pd.DataFrame({"ts_utc": pd.to_datetime(ev["ts_utc"], utc=True), "writer": ev["writer"].astype(object),
                        "locked": [locked(d) for d in ev["detail"]]})
    out = out[out["writer"].notna()]
    return out.sort_values("ts_utc", kind="stable").reset_index(drop=True)


def live_cutover(boots: pd.DataFrame) -> pd.Timestamp | None:
    """The account's live-key cut-over: its first engine start that held the writer lock (`engine_boots`), None
    before any. Legacy-key minutes are read only before it (quant condition 4)."""
    held = boots[boots["locked"].astype(bool)] if not boots.empty else boots
    return pd.Timestamp(held["ts_utc"].iloc[0]) if not held.empty else None


def live_rows(store: Any, *, account_id: str, broker: str, symbol: str, start: pd.Timestamp | None,
              end: pd.Timestamp | None, legacy_before: pd.Timestamp | None) -> pd.DataFrame:
    """The account's bars_1m_live rows: its own key's (source = account), then the broker's legacy key's before
    `legacy_before` (None: no bound) for minutes its own key has no copy of; `keyed` says which (OWN, LEGACY)."""
    own = store.read(LIVE_TABLE, source=account_id, symbol=symbol, start=start, end=end)
    old = store.read(LIVE_TABLE, source=broker, symbol=symbol, start=start, end=end) if broker != account_id \
        else pd.DataFrame()
    parts = []
    if own is not None and not own.empty:
        parts.append(own.reset_index(drop=True).assign(keyed=OWN))
    if old is not None and not old.empty:
        old = old.reset_index(drop=True)
        ts = epoch_ns(pd.DatetimeIndex(pd.to_datetime(old["ts_utc"], utc=True)))
        keep = ~np.isin(ts, epoch_ns(pd.DatetimeIndex(pd.to_datetime(own["ts_utc"], utc=True)))) \
            if own is not None and not own.empty else np.ones(len(old), dtype=bool)
        if legacy_before is not None:
            keep &= ts < int(pd.Timestamp(legacy_before).tz_convert("UTC").value)
        if keep.any():
            parts.append(old[keep].assign(keyed=LEGACY))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _legacy_before(store: Any, account_id: str,
                   legacy_before: pd.Timestamp | None | Literal["boots"]) -> pd.Timestamp | None:
    return live_cutover(engine_boots(store, account_id)) if isinstance(legacy_before, str) else legacy_before


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
                    end: pd.Timestamp | None, as_of: pd.Timestamp,
                    legacy_before: pd.Timestamp | None | Literal["boots"] = "boots") -> pd.DataFrame:
    """ts_utc + bid/ask OHLC of `account_id`'s broker in [start, end), visible by `as_of` (module docstring), one row
    per minute, sorted, with the `feed` it came from. Empty when no feed has a minute. `legacy_before`: the live
    cut-over ("boots": read from the account's engine starts, `live_cutover`)."""
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
        df = live_rows(store, account_id=account_id, broker=broker, symbol=symbol, start=start, end=end,
                       legacy_before=_legacy_before(store, account_id, legacy_before)) if keyed == "live" \
            else store.read(table, source=account_id, symbol=symbol, start=start, end=end)
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
                    end: pd.Timestamp | None, as_of: pd.Timestamp, reference: str | None = "XAUUSD",
                    legacy_before: pd.Timestamp | None | Literal["boots"] = "boots") -> pd.DataFrame:
    """Every stored version of `account_id`'s broker minutes in [start, end) visible by `as_of`, from each feed in
    FEEDS order (`rank` 0, 1, 2), then the release minutes of the `reference` symbol (the engine's release_symbol) the
    engine loads after a restart (rank 3, used only where no broker feed has the minute; None: none). One row per stored row, with `visible_at`: its nominal
    visibility (`_visible_ns` without the write time), or its write time `available_utc` when that is more than
    LIVE_WRITE_GRACE later (a minute back-filled or re-pulled after the fact is known only once written; one the
    engine built live and stored at once was known at its close, as the engine knew it), `written` (a write time is
    stored), `written_at` (that write time, NaT without one), `replaced` (the engine flagged the minute a replacement
    of the one it decided on), the bar columns the features read (META_COLUMNS, NaN where a feed has none) and `rank`. Quarantined
    minutes are left out of every feed, as in `account_bars_1m`. `as_of_minutes` cuts it at a decision. The live
    feed (rank 0) is `live_rows` (`legacy_before` as in `account_bars_1m`), each row with its `writer` (the engine
    start that wrote it, None without one) and `keyed` (OWN or LEGACY; "" on the other feeds)."""
    cut = int(pd.Timestamp(as_of).tz_convert("UTC").value)
    bad: set[int] = set()
    for source in dict.fromkeys((broker, account_id)):
        q = store.read(QUARANTINE, source=source, symbol=symbol, start=start, end=end, columns=["ts_utc"])
        if q is not None and not q.empty:
            bad.update(epoch_ns(pd.DatetimeIndex(pd.to_datetime(q["ts_utc"], utc=True))).tolist())
    feeds = [(t, k if k == "live" else account_id, symbol) for t, k in FEEDS]
    if reference:
        feeds.append((REFERENCE_FEED[0], REFERENCE_FEED[1], reference))
    parts: list[pd.DataFrame] = []
    cut_live = _legacy_before(store, account_id, legacy_before)
    for rank, (table, source, sym) in enumerate(feeds):
        df = live_rows(store, account_id=account_id, broker=broker, symbol=sym, start=start, end=end,
                       legacy_before=cut_live) if source == "live" \
            else store.read(table, source=source, symbol=sym, start=start, end=end)
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
        # the raw write time and the engine's replacement flag (engine/history.py REPLACED_COL): what the engine held
        # at a decision is only provable for a minute not replaced after it (research/arm_integrity.py)
        part["written_at"] = pd.to_datetime(df.loc[keep, "available_utc"], utc=True) \
            if "available_utc" in df.columns else pd.NaT
        part["replaced"] = df.loc[keep, "replaced"].astype("boolean").fillna(False).astype(bool).to_numpy() \
            if "replaced" in df.columns else False
        # the writing engine start and the key (engine/history.py WRITER_COL; quant conditions 1-4): a live minute is
        # proven this account's engine's own copy only through them (research/arm_integrity.py)
        part["writer"] = df.loc[keep, "writer"].astype(object).where(df.loc[keep, "writer"].notna(), None).to_numpy() \
            if "writer" in df.columns else None
        part["keyed"] = df.loc[keep, "keyed"].to_numpy() if "keyed" in df.columns else ""
        part["rank"] = rank
        parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["ts_utc", *PRICE_COLUMNS, *META_COLUMNS, "visible_at", "written", "written_at",
                                     "replaced", "writer", "keyed", "rank"])
    out = pd.concat(parts, ignore_index=True)
    out["ts_utc"] = pd.to_datetime(out["ts_utc"], utc=True)
    return out.sort_values(["ts_utc", "rank"], kind="stable").reset_index(drop=True)


def as_of_minutes(versions: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    """The minutes as `account_bars_1m` would have read them with as_of = `at`: of the versions visible by `at`, per
    minute the first feed's (lowest `rank`) last visible version. Sorted by ts_utc."""
    v = versions[(pd.to_datetime(versions["visible_at"], utc=True) <= pd.Timestamp(at).tz_convert("UTC")).to_numpy()]
    v = v.sort_values(["ts_utc", "rank", "visible_at"], ascending=[True, True, False], kind="stable")
    return v.drop_duplicates("ts_utc", keep="first").reset_index(drop=True)


__all__ = ["BOOT_CHECK", "FEEDS", "LEGACY", "META_COLUMNS", "OWN", "REFERENCE_FEED", "account_bars_1m", "as_of_minutes",
           "engine_boots", "live_cutover", "live_rows", "minute_versions"]
