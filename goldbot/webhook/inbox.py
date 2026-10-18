"""Durable inbox for TradingView alerts (SQLite, WAL, synchronous=FULL: robust-storage rule, no new JSON state).

"Persist before the ack": the webhook answers 200 only after the alert is committed here, and answers 503 when the
write fails so TradingView retries. Idempotency is durable and on the bar, not the message:
UNIQUE(indicator, version, symbol, tf, bar_time); the first receipt wins (its `available_utc` is the first time we
could have known the values), a retry or a repaint re-send is ignored.

`sync_to_store` copies the inbox into the Parquet dataset `tv_indicators` (long format, one row per plot) that the
feature pipeline reads; it is idempotent and resumes from a cursor kept in the inbox.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.store import Store
from goldbot.db.connection import connect, write
from goldbot.db.migrations import migrate

TABLE = "tv_indicators"


def _iso(t: pd.Timestamp) -> str:
    return t.tz_convert("UTC").isoformat()


class TvInbox:
    def __init__(self, path: str | Path):
        """Opens state/tv.db and brings its schema up to date (goldbot/db/migrations.py TV; the pull log shares it)."""
        self.path = Path(path)
        conn = self._conn()
        try:
            migrate(conn, "tv")
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        return connect(self.path, "tv")

    def put(self, row: dict[str, Any]) -> bool:
        """Commit one alert row (webhook.app.to_row). True when new, False for a bar already held. Raises on a
        storage failure: the caller must not acknowledge."""
        conn = self._conn()
        try:
            with write(conn):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO tv_alerts (indicator, version, symbol, tf, bar_time, fired_at, received_utc,"
                    " available_utc, signal, strength, price, plots, payload, intrabar, signal_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row["indicator"], row["version"], row["symbol"], row["tf"], _iso(row["bar_time"]),
                     _iso(row["fired_at"]), _iso(row["received_utc"]), _iso(row["available_utc"]), row["signal"],
                     float(row["strength"]), row["price"], json.dumps(row["plots"]), json.dumps(row["payload"]),
                     int(row["intrabar"]), row["signal_hash"]))
                return cur.rowcount > 0
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._conn()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM tv_alerts").fetchone()[0])
        finally:
            conn.close()

    def count_since(self, since: datetime) -> int:
        """Rows received at or after `since` (the webhook's per-hour cap)."""
        conn = self._conn()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM tv_alerts WHERE received_utc >= ?",
                                    (_iso(pd.Timestamp(since)),)).fetchone()[0])
        finally:
            conn.close()

    def rows_after(self, last_id: int, limit: int = 5000) -> list[sqlite3.Row]:
        conn = self._conn()
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM tv_alerts WHERE id > ? ORDER BY id LIMIT ?", (last_id, limit)).fetchall()
        finally:
            conn.close()

    def cursor(self) -> int:
        conn = self._conn()
        try:
            r = conn.execute("SELECT last_id FROM tv_sync WHERE id = 1").fetchone()
            return int(r[0]) if r else 0
        finally:
            conn.close()

    def set_cursor(self, last_id: int) -> None:
        conn = self._conn()
        try:
            with write(conn):
                conn.execute("INSERT INTO tv_sync (id, last_id) VALUES (1, ?) "
                             "ON CONFLICT (id) DO UPDATE SET last_id = excluded.last_id", (last_id,))
        finally:
            conn.close()


def source_of(indicator: str, version: str) -> str:
    """The store partition name `indicator@version`, with every character that is not path-safe replaced."""
    return re.sub(r"[^A-Za-z0-9._@+-]+", "_", f"{indicator}@{version}")


def safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", symbol)


def long_rows(r: sqlite3.Row) -> pd.DataFrame:
    """One alert as long rows: one per plot (NaN value for an unreadable plot); an alert without plots writes one
    row with plot "" so that the bar's arrival is still recorded; a long/short/flat signal also writes the plot
    "signal" (+1/-1/0)."""
    plots: dict[str, float | None] = json.loads(r["plots"])
    items: list[tuple[str, float]] = [(k, float("nan") if v is None else float(v)) for k, v in plots.items()]
    if r["signal"] in ("long", "short", "flat"):
        items.append(("signal", {"long": 1.0, "short": -1.0, "flat": 0.0}[r["signal"]]))
    if not items:
        items = [("", float("nan"))]
    bar = pd.Timestamp(r["bar_time"])
    base = {"ts_utc": bar, "bar_time": bar, "available_utc": pd.Timestamp(r["available_utc"]),
            "fired_at": pd.Timestamp(r["fired_at"]), "received_at": pd.Timestamp(r["received_utc"]),
            "indicator": r["indicator"], "version": r["version"], "tf": r["tf"], "price": r["price"],
            "intrabar": bool(r["intrabar"])}
    return pd.DataFrame([{**base, "plot": k, "value": v} for k, v in items])


def sync_to_store(inbox: TvInbox, store: Store) -> int:
    """Copy new inbox alerts into `tv_indicators`; returns the number of alerts copied. Idempotent: the store key is
    (indicator, version, tf, bar_time, plot) within the symbol partition."""
    copied = 0
    while True:
        rows = inbox.rows_after(inbox.cursor())
        if not rows:
            return copied
        groups: dict[tuple[str, str], list[pd.DataFrame]] = {}
        for r in rows:
            key = (source_of(r["indicator"], r["version"]), safe_symbol(r["symbol"]))
            groups.setdefault(key, []).append(long_rows(r))
        for (source, symbol), frames in groups.items():
            store.append(TABLE, pd.concat(frames, ignore_index=True), source=source, symbol=symbol)
        inbox.set_cursor(int(rows[-1]["id"]))
        copied += len(rows)
