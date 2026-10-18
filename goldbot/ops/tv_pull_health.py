"""TradingView pull health: the state of the owner's TradingView sign-in as the direct pull sees it.

Design rule: TradingView is a second, optional feed ("never a primary bar source"), so it must never block trading or
exits. Every bad state here is therefore a WARN, never a FAIL, and nothing in the engine reads it.

State (robust-storage rule: SQLite, no new JSON state file): one row in the table `tv_auth` of state/tv.db (the file of
the webhook inbox, `core` pragmas; created here with IF NOT EXISTS, so no schema migration is shared with the pull):

    status        "ok" | "expired"      written by the pull: `record_ok` after a successful pull, `record_expired` when
                                        TradingView refused the session cookie
    last_ok_utc   the last successful pull
    notified      1 once the owner has been told about the current expiry

* Check `tv_pull` WARNs when the state is expired, stale (no successful pull for STALE_HOURS) or corrupt. A corrupt
  state (unreadable file, unknown status, unparsable time) counts as expired: the sign-in cannot be shown to work.
  No state at all (the pull never ran) is ok: TradingView is opt-in.
* ONE Telegram notice per expiry: `record_expired` queues it (a "notice" row in state/agent_runs.jsonl, which the
  Telegram service relays, as `TelegramBus.notify_owner` does) in the same transaction that sets `notified`, so a
  restart, or the pull failing every cycle, does not repeat it. `record_ok` re-arms it, so the next expiry notifies again.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from goldbot.db.connection import connect, write

if TYPE_CHECKING:
    from goldbot.ops.health import Check, HealthContext

log = logging.getLogger(__name__)

DB_FILE = "tv.db"
STALE_HOURS = 36                      # the pull runs at least daily; a day and a half without success is a fault
NOTICE_TEXT = "TradingView sign-in expired — run `goldbot accounts set tradingview-session` on the brain"
_SCHEMA = """CREATE TABLE IF NOT EXISTS tv_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,
    last_ok_utc TEXT,
    notified INTEGER NOT NULL DEFAULT 0
) STRICT"""


def _iso(t: pd.Timestamp) -> str:
    return pd.Timestamp(t).tz_convert("UTC").isoformat()


def _open(state_dir: Path) -> sqlite3.Connection:
    conn = connect(Path(state_dir) / DB_FILE, "core")
    conn.execute(_SCHEMA)
    return conn


def _queue_notice(state_dir: Path) -> None:
    """The owner notice: one row in state/agent_runs.jsonl (see goldbot/telegram/bus.py notify_owner)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"role": "tradingview", "started_utc": now, "finished_utc": now, "status": "notice", "turns": 0,
           "cost_usd": 0.0, "report_path": None, "detail": NOTICE_TEXT}
    with open(Path(state_dir) / "agent_runs.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def record_ok(state_dir: Path, now: pd.Timestamp) -> None:
    """A pull succeeded: the sign-in works, and the next expiry notifies again. Called by the TradingView pull
    (goldbot/data/tv_pull*)."""
    conn = _open(state_dir)
    try:
        with write(conn):
            conn.execute("INSERT INTO tv_auth (id, status, last_ok_utc, notified) VALUES (1, 'ok', ?, 0) "
                         "ON CONFLICT (id) DO UPDATE SET status = 'ok', last_ok_utc = excluded.last_ok_utc, notified = 0",
                         (_iso(now),))
    finally:
        conn.close()


def record_expired(state_dir: Path, now: pd.Timestamp) -> bool:
    """The pull was refused: mark the sign-in expired and tell the owner once. True when this call sent the notice.
    Called by the TradingView pull (goldbot/data/tv_pull*). A corrupt tv.db is moved aside (tv.db.corrupt-<ts>) and
    recreated, so the expiry is still recorded and notified once. Never raises: a store or notice failure is logged and leaves the expiry un-notified, to be tried again."""
    try:
        try:
            conn = _open(state_dir)
        except sqlite3.DatabaseError as exc:          # corrupt file: keep it for inspection, start a fresh state
            aside = Path(state_dir) / f"{DB_FILE}.corrupt-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            log.warning("tv.db unreadable (%s): moved to %s", exc, aside.name)
            (Path(state_dir) / DB_FILE).rename(aside)
            conn = _open(state_dir)
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        log.warning("tv_auth state unavailable, cannot record the expiry: %s", exc)
        return False
    try:
        with write(conn):
            row = conn.execute("SELECT notified FROM tv_auth WHERE id = 1").fetchone()
            if row is not None and int(row[0]) == 1:
                conn.execute("UPDATE tv_auth SET status = 'expired' WHERE id = 1")
                return False
            _queue_notice(state_dir)                      # inside the transaction: a failed write is not marked sent
            conn.execute("INSERT INTO tv_auth (id, status, last_ok_utc, notified) VALUES (1, 'expired', NULL, 1) "
                         "ON CONFLICT (id) DO UPDATE SET status = 'expired', notified = 1")
            return True
    except (sqlite3.Error, OSError) as exc:
        log.warning("could not record the TradingView expiry: %s", exc)
        return False
    finally:
        conn.close()


def check_tv_pull(ctx: HealthContext) -> Check:
    """tv_pull: WARN when the TradingView sign-in is expired, the pull is stale, or its state is corrupt (counts as
    expired). Never FAIL: TradingView must never block trading or exits."""
    from goldbot.ops.health import Check
    name = "tv_pull"
    path = Path(ctx.state_dir) / DB_FILE
    if not path.exists():
        return Check(name=name, status="ok", reason="no TradingView pull yet (opt-in)")
    expired = "TradingView sign-in expired: run `goldbot accounts set tradingview-session` on the brain"
    try:
        conn = connect(path, "core")
        try:
            has = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'tv_auth'").fetchone()
            row = conn.execute("SELECT status, last_ok_utc FROM tv_auth WHERE id = 1").fetchone() if has else None
        finally:
            conn.close()
        if row is None:
            return Check(name=name, status="ok", reason="no TradingView pull yet (opt-in)")
        status, last_ok = str(row[0]), row[1]
        if status not in ("ok", "expired"):
            raise ValueError(f"unknown status {status!r}")
        if status == "expired":
            return Check(name=name, status="warn", reason=expired)
        if last_ok is None:
            raise ValueError("no last successful pull time")
        age_h = (pd.Timestamp(ctx.now) - pd.Timestamp(last_ok)).total_seconds() / 3600
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError) as exc:
        return Check(name=name, status="warn", reason=f"{expired} (state unreadable: {exc})")
    if age_h > STALE_HOURS:
        return Check(name=name, status="warn", reason=f"last successful TradingView pull {age_h:.0f} h ago")
    return Check(name=name, status="ok", reason=f"last successful TradingView pull {age_h:.1f} h ago")
