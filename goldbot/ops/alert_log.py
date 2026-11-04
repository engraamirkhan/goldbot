"""The health alert log (aux.db `health_alerts`, migration 22): one row per message the owner was sent.

Owner complaint 2026-11-04 ("the health checks have been horrible ... up and down") could not be measured: nothing
recorded what was sent. Now the Telegram service writes one row per health message it delivered (channel `telegram`,
kinds new / recovered / flapping, the check names) and one per state change the outside dead man's switch accepted
(channel `deadman`, kind ok or fail; each change is an email from the monitor). Check names only, never a reason, a
URL or an account identifier beyond the check name. Additive and best effort: a failed write is logged and never
stops or repeats an alert.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd

from goldbot.db.connection import Database

log = logging.getLogger("goldbot.alert_log")

Channel = Literal["telegram", "deadman"]


class AlertLog:
    def __init__(self, state_dir: str | Path):
        self.db = Database.open(state_dir, "aux")

    def append(self, ts: pd.Timestamp, *, channel: Channel, kinds: Iterable[str], checks: Iterable[str]) -> None:
        with self.db.write() as conn:
            conn.execute("INSERT INTO health_alerts (ts_ns, channel, kinds, checks) VALUES (?, ?, ?, ?)",
                         (int(ts.value), channel, ",".join(kinds), ",".join(checks)))

    def count_since(self, since: pd.Timestamp, channel: Channel | None = None) -> int:
        with self.db.connection() as conn:
            q = "SELECT COUNT(*) FROM health_alerts WHERE ts_ns >= ?" + (" AND channel = ?" if channel else "")
            row = conn.execute(q, (int(since.value), channel) if channel else (int(since.value),)).fetchone()
        return int(row[0])


def log_alert(state_dir: str | Path, ts: pd.Timestamp, *, channel: Channel, kinds: Iterable[str],
              checks: Iterable[str]) -> None:
    """Append one row; never raises (the message already went out)."""
    try:
        AlertLog(state_dir).append(ts, channel=channel, kinds=kinds, checks=checks)
    except Exception as exc:                  # noqa: BLE001 - a log row must never stop or repeat an alert
        log.warning("health alert log write failed: %s", type(exc).__name__)
