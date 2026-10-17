"""The broker tick record (BACKLOG 79): the broker's own XAUUSD ticks, one Parquet per server date, for the H-A
holiday diagnostic (docs/research/holiday-list-measurement-2026-10.md, pre-registered: "MT5 ticks (bid, ask, time)
pulled with `copy_ticks` after each server day closes, one Parquet file per server date on the brain VM, outside git;
their sha256 digests are listed in the verdict").

Why a pull after the day and not a hook in the engine's tick loop: the engine reads `last_tick` a few times a second, so
it keeps a sample of the broker's ticks (any ticks between two reads are never seen) and only while it runs; it already
stores that sample (data/ticks/source=<account>, `Engine.flush_ticks`) for the cost tables and D12. H-A measures ticks
per quoted minute and the spread, which need the terminal's complete tick history, and its uptime rule would otherwise
drop every day the engine was restarted. `copy_ticks` returns that history for any closed day, so a missed run is
back-filled by the next one. Nothing is added to the trading path: the daily scheduler job `tick_record` reads through
the scheduler's existing read-only broker (the one feed_reconcile and broker_ledger use), in pages of PAGE_TICKS rows
so the single-threaded bridge is never held for more than a fraction of a second between the engine's quote reads.

Layout (state directory, mode 0700 directories and 0600 files, like the rest of state/):

  state/ticks/<account>/<YYYY-MM-DD>.parquet   server date (the account's server_tz, the day RiskGate uses)
  state/ticks/<account>/index.json             DayRecord per file: rows, London/NY ticks, spread, bytes, sha256

Columns: `time_ns` (int64 epoch nanoseconds through `timeutil.epoch_ns`, so the unit never depends on how pandas
inferred the source), `bid`, `ask` (float64), `flags` (int32, MT5 TICK_FLAG_*). File metadata names the account label,
symbol, server date and zone, source and the pull time (when the file became knowable); never a login, server name or
host. Each file is written to a temp name and renamed into place, so a reader or the backup sees the old file or the
new one; temp names end in `.tmp`, which the backup skips. The files are backed up (goldbot/ops/backup.py takes the
whole state directory): the terminal keeps only a limited tick history, so a lost day cannot be pulled again.

Size: about 12 bytes a tick (zstd; measured on synthetic millisecond ticks), so a 50k-tick day is about 0.6 MB and a
150k-tick day about 1.7 MB; 240 retained days of 50k ticks is about 140 MB an account (tests/test_tick_recorder.py
pins 50k ticks under 1 MB).

  load_day("icm-demo", "2026-11-26")           # research: ts_utc (UTC, ns), bid, ask, flags; sha256 checked
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from goldbot.base import Record, UtcTimestamp, _fsync_dir, load_record_json, write_atomic
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns, server_to_utc, server_wall
from goldbot.ops.accounts import ACCOUNT_ID_LOGIN, ACCOUNT_ID_MAX_BYTES

if TYPE_CHECKING:
    from goldbot.config import TickRecordSettings
    from goldbot.ops.health import Check, HealthContext

log = logging.getLogger("goldbot.ticks")

TICKS_DIR = "ticks"
INDEX_FILE = "index.json"
PAGE_TICKS = 10_000           # rows per copy_ticks call: ~0.6 MB of JSON over the bridge, well inside its timeout
MAX_PAGES = 500               # 5M ticks: a server day never has this many; a runaway loop stops here
SETTLE = pd.Timedelta(minutes=5)   # a server day is pulled only this long after it ended (late ticks reach the terminal)
STALE_DAYS = 4                # health: the newest recorded day older than this (Friday -> Monday is 3) WARNs
SOURCE = "mt5 copy_ticks via the scheduler's read-only broker"
_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SCHEMA = pa.schema([("time_ns", pa.int64()), ("bid", pa.float64()), ("ask", pa.float64()), ("flags", pa.int32())])


class DayRecord(Record):
    """One recorded server day, as index.json keeps it (health and the H-A verdict read it; no identifiers)."""
    server_date: date
    pulled_utc: UtcTimestamp
    rows: int
    london_ny_ticks: int               # ticks in the London and New York sessions (the classifier's bar)
    first_utc: UtcTimestamp | None
    last_utc: UtcTimestamp | None
    median_spread: float | None        # ask - bid, $/oz, over the London/NY ticks (None without any)
    bytes: int
    sha256: str


class _Index(Record):
    days: dict[str, DayRecord] = {}


# ------------------------------------------------------------------------------------------- paths
def _label(account: str) -> str:
    """The account label as a path component: lower-case letters, digits, '-' and '_' only, no five-digit run (a
    login could hide in one, goldbot.ops.accounts), at most ACCOUNT_ID_MAX_BYTES. Anything else is refused."""
    if not _LABEL.match(account) or ACCOUNT_ID_LOGIN.search(account) or len(account) > ACCOUNT_ID_MAX_BYTES:
        raise ValueError(f"not an account label: {account!r}")
    return account


def account_dir(state_dir: str | Path, account: str) -> Path:
    return Path(state_dir) / TICKS_DIR / _label(account)


def _private_dir(d: Path) -> Path:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    for p in (d, d.parent):                     # ticks/ and ticks/<account>/; an existing dir is tightened too
        os.chmod(p, 0o700)
    return d


def _day(day: date | str) -> date:
    return day if isinstance(day, date) else pd.Timestamp(day).date()


# ------------------------------------------------------------------------------------------- server days
def server_day_bounds(day: date, server_tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) in UTC of server date `day`: its server midnights, resolved per date (DST-safe, NY+7 too)."""
    mid = pd.DatetimeIndex([pd.Timestamp(day), pd.Timestamp(day + timedelta(days=1))])
    start, end = server_to_utc(mid, server_tz)
    return pd.Timestamp(start), pd.Timestamp(end)


def server_date(ts_utc: pd.Timestamp, server_tz: str) -> date:
    return pd.Timestamp(server_wall(pd.Timestamp(ts_utc), server_tz)[0]).date()


def latest_closed(now: pd.Timestamp, server_tz: str) -> date:
    """The newest server date that ended at least SETTLE before `now`."""
    latest = server_date(pd.Timestamp(now), server_tz)
    while server_day_bounds(latest, server_tz)[1] + SETTLE > now:
        latest -= timedelta(days=1)
    return latest


def days_due(now: pd.Timestamp, server_tz: str, *, backfill_days: int) -> list[date]:
    """Weekday server dates that have closed (`latest_closed`): the latest and the `backfill_days` calendar days
    before it. Saturdays and Sundays on the server clock have no gold ticks."""
    latest = latest_closed(now, server_tz)
    days = [latest - timedelta(days=k) for k in range(backfill_days, -1, -1)]
    return [d for d in days if d.weekday() < 5]


# ------------------------------------------------------------------------------------------- pull
def pull_day(broker: Any, symbol: str, start: pd.Timestamp, end: pd.Timestamp, *,
             chunk: int = PAGE_TICKS) -> pd.DataFrame:
    """Every tick the terminal holds in [start, end), paged by `chunk` rows. copy_ticks_from starts at a whole second,
    so each page restarts at its last tick's second and the overlap is dropped on (ts_utc, bid, ask, flags), the live
    collector's dedup key. A full page that cannot move past its start second raises instead of looping."""
    pages: list[pd.DataFrame] = []
    since = pd.Timestamp(start)
    for _ in range(MAX_PAGES):
        page = broker.copy_ticks(symbol, since, chunk)
        if page is None or page.empty:
            break
        page = page.assign(ts_utc=pd.to_datetime(page["ts_utc"], utc=True))
        if "flags" not in page.columns:
            page = page.assign(flags=0)
        pages.append(page[["ts_utc", "bid", "ask", "flags"]])
        last = pd.Timestamp(page["ts_utc"].max())
        if len(page) < chunk or last >= end:
            break
        if last.floor("s") <= since.floor("s"):
            raise RuntimeError(f"copy_ticks cannot page past {since}: more than {chunk} ticks in one second")
        since = last
    else:
        raise RuntimeError(f"copy_ticks still returning full pages after {MAX_PAGES} pages from {start}")
    if not pages:
        return pd.DataFrame({"ts_utc": pd.DatetimeIndex([], tz="UTC"), "bid": [], "ask": [], "flags": []})
    df = pd.concat(pages, ignore_index=True)
    df = df[(df["ts_utc"] >= start) & (df["ts_utc"] < end)]
    return df.drop_duplicates(["ts_utc", "bid", "ask", "flags"]).sort_values("ts_utc", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------------------------------- write, index
def _london_ny(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    sess = DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(df["ts_utc"]))
    return df[np.isin(sess, ["london", "newyork"])]


def read_index(state_dir: str | Path, account: str) -> dict[str, DayRecord]:
    f = account_dir(state_dir, account) / INDEX_FILE
    if not f.exists():
        return {}
    return load_record_json(_Index, f.read_text(), source=f"ticks/{account}/{INDEX_FILE}").days


def _write_index(d: Path, days: dict[str, DayRecord]) -> None:
    write_atomic(d / INDEX_FILE, _Index(days=dict(sorted(days.items()))).model_dump_json(indent=1))


def write_day(state_dir: str | Path, account: str, day: date, ticks: pd.DataFrame, *, symbol: str, server_tz: str,
              pulled_utc: pd.Timestamp) -> DayRecord:
    """Write one server day's ticks (columns ts_utc, bid, ask[, flags]) atomically and record it in the index."""
    d = _private_dir(account_dir(state_dir, account))
    df = ticks.sort_values("ts_utc", kind="stable").reset_index(drop=True)
    flags = df["flags"] if "flags" in df.columns else pd.Series(0, index=df.index)
    table = pa.Table.from_arrays(
        [pa.array(epoch_ns(df["ts_utc"]), pa.int64()), pa.array(df["bid"].to_numpy(dtype=np.float64)),
         pa.array(df["ask"].to_numpy(dtype=np.float64)), pa.array(flags.to_numpy(dtype=np.int32))], schema=_SCHEMA,
    ).replace_schema_metadata({
        "goldbot.account": account, "goldbot.symbol": symbol, "goldbot.server_date": day.isoformat(),
        "goldbot.server_tz": server_tz, "goldbot.source": SOURCE, "goldbot.pulled_utc": pd.Timestamp(pulled_utc).isoformat()})
    path = d / f"{day.isoformat()}.parquet"
    fd, name = tempfile.mkstemp(dir=d, prefix=f".{path.name}.", suffix=".tmp")    # 0600, O_EXCL
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as fh:
            pq.write_table(table, fh, compression="zstd")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(d)
    finally:
        tmp.unlink(missing_ok=True)
    blob = path.read_bytes()
    ln = _london_ny(df)
    rec = DayRecord(server_date=day, pulled_utc=pulled_utc, rows=len(df), london_ny_ticks=len(ln),
                    first_utc=df["ts_utc"].iloc[0] if len(df) else None, last_utc=df["ts_utc"].iloc[-1] if len(df) else None,
                    median_spread=float(np.median(ln["ask"] - ln["bid"])) if len(ln) else None,
                    bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
    days = read_index(state_dir, account)
    days[day.isoformat()] = rec
    _write_index(d, days)
    return rec


def prune(state_dir: str | Path, account: str, keep_from: date) -> list[str]:
    """Delete day files (and their index rows) of server dates before `keep_from`; returns the dates removed."""
    d = account_dir(state_dir, account)
    if not d.exists():
        return []
    days = read_index(state_dir, account)
    gone: set[str] = set()
    for f in d.glob("*.parquet"):
        try:
            when = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if when < keep_from:
            f.unlink(missing_ok=True)
            gone.add(f.stem)
    gone |= {k for k in days if date.fromisoformat(k) < keep_from}
    if gone:
        _write_index(d, {k: v for k, v in days.items() if k not in gone})
    return sorted(gone)


# ------------------------------------------------------------------------------------------- the daily job
def record_ticks(broker: Any, state_dir: str | Path, account: str, symbol: str, server_tz: str, now: pd.Timestamp,
                 cfg: TickRecordSettings) -> dict[str, Any]:
    """Pull every due server day not yet on disk (idempotent: a recorded day is never pulled again; delete its file to
    re-pull), then prune days older than `retain_days`. Returns what it did, for the scheduler's job record."""
    now = pd.Timestamp(now)
    have = read_index(state_dir, account)
    written: list[str] = []
    for day in days_due(now, server_tz, backfill_days=cfg.backfill_days):
        if day.isoformat() in have and (account_dir(state_dir, account) / f"{day.isoformat()}.parquet").exists():
            continue
        start, end = server_day_bounds(day, server_tz)
        rec = write_day(state_dir, account, day, pull_day(broker, symbol, start, end), symbol=symbol,
                        server_tz=server_tz, pulled_utc=now)
        written.append(day.isoformat())
        log.info("tick record %s %s: %d ticks (%d London/NY), %d bytes", account, day, rec.rows, rec.london_ny_ticks,
                 rec.bytes)
    pruned = prune(state_dir, account, latest_closed(now, server_tz) - timedelta(days=cfg.retain_days))
    return {"written": written, "pruned": pruned, "days": len(read_index(state_dir, account))}


# ------------------------------------------------------------------------------------------- research loader
def recorded_days(account: str, *, state_dir: str | Path = "state") -> list[date]:
    return sorted(date.fromisoformat(k) for k in read_index(state_dir, account))


def load_day(account: str, day: date | str, *, state_dir: str | Path = "state", verify: bool = True) -> pd.DataFrame:
    """One recorded server day: ts_utc (datetime64[ns, UTC]), bid, ask, flags, in time order. `verify` checks the
    file against the sha256 the index recorded when it was written (the digest the H-A verdict lists)."""
    d = account_dir(state_dir, account)
    key = _day(day).isoformat()
    f = d / f"{key}.parquet"
    if not f.exists():
        raise FileNotFoundError(f"no tick record for {account} on server date {key} ({f})")
    if verify:
        rec = read_index(state_dir, account).get(key)
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if rec is None or rec.sha256 != digest:
            raise ValueError(f"tick record {account} {key}: sha256 {digest[:12]} does not match the index "
                             f"({rec.sha256[:12] if rec else 'no entry'})")
    t = pq.read_table(f).to_pandas()
    return pd.DataFrame({"ts_utc": pd.to_datetime(t["time_ns"].to_numpy(dtype=np.int64), unit="ns", utc=True),
                         "bid": t["bid"], "ask": t["ask"], "flags": t["flags"]})


# ------------------------------------------------------------------------------------------- health
def check_tick_record(ctx: HealthContext, account_id: str) -> Check:
    """ticks:<account>: WARN when the newest recorded trading day has fewer London/NY ticks than
    `ticks.min_london_ny_ticks` (a recorder, terminal or feed gap; a market holiday also thins it, which is what H-A
    measures) or when the record has not advanced for STALE_DAYS. Never FAIL: research data, not a trading input."""
    from goldbot.ops.health import Check
    name = f"ticks:{account_id}"
    try:
        days = read_index(ctx.state_dir, account_id)
    except (ValueError, OSError) as exc:
        return Check(name=name, status="warn", reason=f"tick record index unreadable: {exc}")
    if not days:
        return Check(name=name, status="ok", reason="no broker tick day recorded yet (scheduler job tick_record)")
    key = max(days)
    rec = days[key]
    floor = ctx.settings.ticks.min_london_ny_ticks if ctx.settings is not None else 1000
    msg = (f"{key}: {rec.rows} ticks, {rec.london_ny_ticks} London/NY ticks, median spread "
           f"{'n/a' if rec.median_spread is None else f'{rec.median_spread:.2f}'}; {len(days)} days kept")
    age = (pd.Timestamp(ctx.now).tz_convert("UTC").date() - rec.server_date).days
    if age > STALE_DAYS:
        return Check(name=name, status="warn", reason=f"newest recorded day is {age} days old (tick_record not "
                                                       f"advancing): {msg}")
    if rec.london_ny_ticks < floor:
        return Check(name=name, status="warn", reason=f"thin day (< {floor} London/NY ticks; a market holiday, the "
                                                       f"terminal or the feed): {msg}")
    return Check(name=name, status="ok", reason=msg)


__all__ = ["DayRecord", "INDEX_FILE", "PAGE_TICKS", "account_dir", "check_tick_record", "days_due", "load_day",
           "latest_closed", "prune", "pull_day", "read_index", "record_ticks", "recorded_days", "server_date", "server_day_bounds",
           "write_day"]
