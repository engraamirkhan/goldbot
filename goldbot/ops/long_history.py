"""The 1d/1w long daily history kept current on the brain (BACKLOG 251, BACKLOG 144 blocker 4).

Design rule (engine/long_bars.py): "a live 1d/1w arm warms up from `<data_root>/<engine.long_history_dir>/bars/
<symbol>_1d.parquet`; the history must reach the engine window's first complete day, else the arm is held back
(`history_gap:<tf>`)". Nothing kept that file current, so a day after the last manual copy a live arm was held back for
good. Here:

- `refresh_job` (scheduler `long_history_refresh`, every evening after the 21:00 UTC roll): the store's complete days
  after the file's last row, built by the engine's own `long_bars.daily_from_1m` from the release and the traded
  broker's minutes (the engine's history), appended with an atomic write. Point in time: a day is appended only once
  its 21:00 UTC close has passed and the store holds its first minute; nothing already in the file is ever rewritten.
  An up-to-date file is left untouched (the engine re-reads it on a new mtime only).
- `preflight_item`: fails when a live 1d/1w timeframe's history does not reach the engine window (`long_bars.joined`
  is GAP or the file is missing); skipped while 1d/1w are dark (settings `engine.timeframes_live`).

The health check on a held-back arm lives in goldbot/ops/checks/long_history.py. Reads the store, writes only the
history file; never a broker or an order."""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.engine import long_bars

if TYPE_CHECKING:
    from goldbot.config import Settings
    from goldbot.data.store import Store
    from goldbot.ops.accounts import Account

log = logging.getLogger(__name__)

RELEASE_SOURCE, LIVE_TABLE = "dukascopy", "bars_1m_live"     # as engine/history.py reads the engine's history
SOURCE_PREFIX = "engine_1m:"            # `source` of an appended row: the minutes it was built from, never research's
MAX_APPEND_DAYS = 120                   # a file staler than this is re-copied by hand (RUNBOOK), never rebuilt here
PREFLIGHT_WINDOW_DAYS = 14              # the store minutes the preflight judges the join on (stricter than the engine's)


class RefreshResult(FrozenRecord):
    status: Literal["appended", "up_to_date", "no_file", "no_minutes", "no_gold_account"]
    path: str
    appended: int = 0
    last_visible: str | None = None     # the file's last row after the run
    holes: list[str] = []               # weekday closes with no minutes between the file and the appended days


def history_path(settings: Settings) -> Path:
    """`bars/<symbol>_1d.parquet` under settings engine.long_history_dir (the file `long_bars.read_history` reads)."""
    return Path(settings.engine.history_root(settings.data_root)) / "bars" / f"{settings.symbol.lower()}_1d.parquet"


def gold_key(settings: Settings, accounts: Sequence[Account]) -> tuple[str, str] | None:
    """(broker, traded symbol) of the first enabled account whose release symbol is settings.symbol (the engine's
    `books.live_key` of a gold book), by account id; None when there is none."""
    from goldbot.ops.services.engine import release_symbol
    for acc in sorted(accounts, key=lambda a: a.account_id):
        if acc.enabled and release_symbol(settings, acc) == settings.symbol:
            return acc.broker, acc.symbol
    return None


def store_minutes(store: Store, symbol: str, key: tuple[str, str], start: pd.Timestamp,
                  end: pd.Timestamp) -> pd.DataFrame:
    """The engine's history in [start, end), visible by `end`: release minutes, the traded broker's over them (the
    engine takes the broker's from its cut-over on; here the broker's minute wins where both exist)."""
    from goldbot.data.resample import BAR_COLUMNS
    parts = [store.read("bars_1m", source=RELEASE_SOURCE, symbol=symbol, start=start, end=end),
             store.read(LIVE_TABLE, source=key[0], symbol=key[1], start=start, end=end)]
    b = pd.concat([p[[c for c in BAR_COLUMNS if c in p.columns]] for p in parts if not p.empty], ignore_index=True) \
        if any(not p.empty for p in parts) else pd.DataFrame(columns=BAR_COLUMNS)
    if b.empty:
        return b
    b = b[pd.to_datetime(b["visible_at"], utc=True) <= end]
    return b.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc", kind="stable").reset_index(drop=True)


def new_days(minutes: pd.DataFrame, last_visible: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    """Daily bars (`long_bars.daily_from_1m`) closing after `last_visible` and by `now`. A day whose first minute the
    minutes may not hold (they start after `last_visible`) is left out: never a partial day."""
    if minutes.empty:
        return pd.DataFrame()
    days = long_bars.daily_from_1m(minutes)
    vis = pd.DatetimeIndex(pd.to_datetime(days["visible_at"], utc=True))
    keep = (vis > last_visible) & (vis <= now)
    first_min = pd.Timestamp(pd.to_datetime(minutes["ts_utc"], utc=True).min())
    if first_min > last_visible and keep.any():
        keep[int(np.flatnonzero(keep)[0])] = False      # the store starts inside that day: it may be partial
    return days[keep].reset_index(drop=True)


def _file_rows(days: pd.DataFrame, columns: Sequence[str], source: str) -> pd.DataFrame:
    """`days` (store schema, bid = ask = mid) in the history file's columns; a column it has no value for is NaN."""
    vis = pd.DatetimeIndex(pd.to_datetime(days["visible_at"], utc=True))
    mid = {c: days[f"bid_{c}"].to_numpy(dtype=float) for c in ("open", "high", "low", "close")}
    vals: dict[str, Any] = {"date": vis.tz_localize(None).normalize(), "ts_utc": pd.to_datetime(days["ts_utc"], utc=True),
                            "visible_at": vis, "spread_mean": days["spread_mean"].to_numpy(dtype=float),
                            "volume": days["tick_count"].to_numpy(dtype=float), "source": source, **mid}
    for side in ("bid", "ask"):
        vals.update({f"{side}_{c}": mid[c] for c in mid})
    out = pd.DataFrame({c: vals[c] if c in vals else np.nan for c in columns}, index=range(len(days)))
    return out


def _holes(last_visible: pd.Timestamp, vis: pd.DatetimeIndex) -> list[str]:
    """Weekday 21:00 UTC closes after `last_visible` up to the last appended day that have no row."""
    if not len(vis):
        return []
    grid = pd.date_range(last_visible + pd.Timedelta(days=1), vis[-1], freq="D")
    return [f"{t:%Y-%m-%d}" for t in grid if t.dayofweek < 5 and t not in set(vis)]


def _write_atomic(frame: pd.DataFrame, path: Path) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def refresh(path: Path, minutes: pd.DataFrame, now: pd.Timestamp, source: str) -> RefreshResult:
    """Append the complete days of `minutes` after the file's last row (by `visible_at`), atomically. The file is not
    created here: a missing one is copied by hand first (RUNBOOK), its columns are the research file's."""
    if not path.exists():
        return RefreshResult(status="no_file", path=str(path))
    old = pd.read_parquet(path)
    last = pd.Timestamp(pd.to_datetime(old["visible_at"], utc=True).max())
    if minutes.empty:
        return RefreshResult(status="no_minutes", path=str(path), last_visible=last.isoformat())
    days = new_days(minutes, last, now)
    if days.empty:
        return RefreshResult(status="up_to_date", path=str(path), last_visible=last.isoformat())
    vis = pd.DatetimeIndex(pd.to_datetime(days["visible_at"], utc=True))
    rows = _file_rows(days, list(old.columns), source)
    _write_atomic(pd.concat([old, rows.astype({c: old[c].dtype for c in rows.columns
                                                if c in ("date", "ts_utc", "visible_at")})], ignore_index=True), path)
    holes = _holes(last, vis)
    log.info("long history %s: %d day(s) appended through %s%s", path, len(days), vis[-1],
             f" ({len(holes)} weekday(s) without minutes: {', '.join(holes)})" if holes else "")
    return RefreshResult(status="appended", path=str(path), appended=len(days), last_visible=vis[-1].isoformat(),
                         holes=holes)


def refresh_job(settings: Settings, store: Store, accounts: Sequence[Account], slot: pd.Timestamp,
                now: pd.Timestamp | None = None) -> dict[str, Any]:
    """The scheduler job: the engine store's complete days appended to the long daily history (`refresh`). The read
    starts a day before the file's last row (so the first new day's opening minutes are there) and goes back at most
    MAX_APPEND_DAYS."""
    now = slot if now is None else now
    path = history_path(settings)
    key = gold_key(settings, accounts)
    if key is None:
        return RefreshResult(status="no_gold_account", path=str(path)).model_dump()
    if not path.exists():
        return RefreshResult(status="no_file", path=str(path)).model_dump()
    last = pd.Timestamp(pd.to_datetime(pd.read_parquet(path, columns=["visible_at"])["visible_at"], utc=True).max())
    start = max(last - pd.Timedelta(days=1), now - pd.Timedelta(days=MAX_APPEND_DAYS))
    minutes = store_minutes(store, settings.symbol, key, start, now)
    return refresh(path, minutes, now, SOURCE_PREFIX + key[0]).model_dump()


def reach(history_root: str | Path, symbol: str, window_1m: pd.DataFrame, now: pd.Timestamp) -> str | None:
    """None when the long history reaches the window (`long_bars.joined` gives bars), else why not: `missing_file`,
    `gap`, or the read's error."""
    try:
        hist = long_bars.read_history(Path(history_root), symbol)
    except Exception as exc:                        # noqa: BLE001 - an unreadable file is a failed check, not a crash
        return f"unreadable:{type(exc).__name__}"
    if hist is None:
        return long_bars.MISSING_FILE
    got = long_bars.joined(hist, window_1m, now)
    return got if isinstance(got, str) else None


def long_tfs_live(settings: Settings) -> list[str]:
    return sorted(set(settings.engine.timeframes_live) & set(long_bars.LONG_TFS))


def preflight_status(settings: Settings, store: Store | None, accounts: Sequence[Account],
                     now: pd.Timestamp) -> tuple[Literal["ok", "fail"], str]:
    """(status, detail) of the preflight item: ok "skipped" while no 1d/1w timeframe is live; otherwise fail unless
    the history reaches the last PREFLIGHT_WINDOW_DAYS of the store's minutes (the engine's window ends there)."""
    live = long_tfs_live(settings)
    if not live:
        return "ok", "skipped: 1d/1w are dark (engine.timeframes_live)"
    key = gold_key(settings, accounts)
    if store is None or key is None:
        return "fail", f"{'/'.join(live)} live but no store or gold account to judge the history on"
    window = store_minutes(store, settings.symbol, key, now - pd.Timedelta(days=PREFLIGHT_WINDOW_DAYS), now)
    root = settings.engine.history_root(settings.data_root)
    why = reach(root, settings.symbol, window, now)
    if why is None:
        return "ok", f"{'/'.join(live)}: the long history reaches the engine window"
    return "fail", f"{'/'.join(live)} live: the long history under {root} does not reach the engine window ({why})"
