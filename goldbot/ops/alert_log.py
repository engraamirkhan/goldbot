"""The health alert log (aux.db `health_alerts`, migration 22): one row per message the owner was sent.

Owner complaint 2026-11-04 ("the health checks have been horrible ... up and down") could not be measured: nothing
recorded what was sent. Now the Telegram service writes one row per health message it delivered (channel `telegram`,
kinds new / recovered / flapping, the check names) and one per state change the outside dead man's switch accepted
(channel `deadman`, kind ok or fail; each change is an email from the monitor). Additive and best effort: a failed
write is logged and never stops or repeats an alert.

Reasons (migration 34; outage 2026-11-12..14: ~205 entry-blocked minutes whose cause the log could not show): a
telegram row also keeps, per new failure or notice, the first line of its reason, at most REASON_CHARS characters,
through the scrub every written text passes (goldbot/research/daily_review.py `scrub`: a line carrying an MT5 login or
a host address is dropped). A dropped reason is stored as REASON_WITHHELD, never in part. Never a URL, a balance or an
identifier beyond the check name; a row without reasons (dead man's switch) leaves the column NULL.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Literal, Mapping

import pandas as pd

from goldbot.db.connection import Database
from goldbot.research.daily_review import scrub

log = logging.getLogger("goldbot.alert_log")

Channel = Literal["telegram", "deadman"]
REASON_CHARS = 160
REASON_WITHHELD = "(withheld: it carried a login or a host)"


def short_reason(text: str) -> str:
    """The reason as the log keeps it: the first line, scrubbed (REASON_WITHHELD when the scrub drops it), cut to
    REASON_CHARS. The scrub runs on the whole first line before the cut, so a cut can never split an identifier past
    the scrub's pattern."""
    first = (text.splitlines() or [""])[0].strip()
    kept = scrub(first)
    return (kept if kept or not first else REASON_WITHHELD)[:REASON_CHARS]


class AlertLog:
    def __init__(self, state_dir: str | Path):
        self.db = Database.open(state_dir, "aux")

    def append(self, ts: pd.Timestamp, *, channel: Channel, kinds: Iterable[str], checks: Iterable[str],
               reasons: Mapping[str, str] | None = None) -> None:
        body = (json.dumps({name: short_reason(why) for name, why in reasons.items()}, sort_keys=True)
                if reasons else None)
        with self.db.write() as conn:
            conn.execute("INSERT INTO health_alerts (ts_ns, channel, kinds, checks, reasons) VALUES (?, ?, ?, ?, ?)",
                         (int(ts.value), channel, ",".join(kinds), ",".join(checks), body))

    def count_since(self, since: pd.Timestamp, channel: Channel | None = None) -> int:
        with self.db.connection() as conn:
            q = "SELECT COUNT(*) FROM health_alerts WHERE ts_ns >= ?" + (" AND channel = ?" if channel else "")
            row = conn.execute(q, (int(since.value), channel) if channel else (int(since.value),)).fetchone()
        return int(row[0])


def log_alert(state_dir: str | Path, ts: pd.Timestamp, *, channel: Channel, kinds: Iterable[str],
              checks: Iterable[str], reasons: Mapping[str, str] | None = None) -> None:
    """Append one row; never raises (the message already went out)."""
    try:
        AlertLog(state_dir).append(ts, channel=channel, kinds=kinds, checks=checks, reasons=reasons)
    except Exception as exc:                  # noqa: BLE001 - a log row must never stop or repeat an alert
        log.warning("health alert log write failed: %s", type(exc).__name__)
