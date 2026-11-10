"""Shared types, thresholds and helpers of the health checks (design: Deployment / observability).

Moved verbatim from goldbot/ops/health.py, which re-exports every name; `HealthContext` stays there (it builds the
runtime context), so it is imported here for annotations only.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, Record, UtcTimestamp
from goldbot.data.calendar import WeekendWindow
from goldbot.data.timeutil import CLOCK_AHEAD_TOLERANCE_S

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

Status = Literal["ok", "warn", "fail"]
_RANK: dict[str, int] = {"ok": 0, "warn": 1, "fail": 2}

# ---- thresholds (seconds unless named otherwise); the reasons quote them so an alert explains itself
SUPERVISOR_WARN_S = 20
SUPERVISOR_FAIL_S = 60            # engines block entries when the supervisor heartbeat is older than this
# a liveness stamp more than this ahead of the pass's latest clock is a clock fault, never a fresh report (found
# 2026-10-16): the engines' own tolerance, so health and the halt agree. "Ahead" is measured against
# max(ctx.now, the wall clock) (`_ahead_s`): `now` is taken once at the start of a pass that can run for minutes
# (a bridge timing out costs about 20 s an account), and a service that wrote during the pass is not a clock fault
HEALTH_CLOCK_AHEAD_S = CLOCK_AHEAD_TOLERANCE_S
ENGINE_WARN_S = 20 * 60           # engines write their state at every 15m bar close
ENGINE_FAIL_S = 45 * 60
# the engine loop's own heartbeat (goldbot/ops/watchdog.py LoopWatch, rewritten every 15 s whatever the feed does): a
# hung loop shows within minutes, not after ENGINE_FAIL_S; the systemd watchdog restarts it at WatchdogSec=300 s
LOOP_WARN_S = 60
LOOP_FAIL_S = 150
LOOP_STARTING_FAIL_S = 15 * 60    # a start longer than this (watchdog.STARTUP_MAX_S, the unit's TimeoutStartSec) fails
SCHEDULER_HEARTBEAT_FAIL_S = 5 * 60   # run_forever saves every 30 s while idle
JOB_OVERDUE_GRACE_S = 15 * 60
JOB_RUNNING_FAIL_H = 12.0         # a job "running" this long means the scheduler died mid-job
COSTS_WARN_DAYS = 4.0             # nightly on weekdays: Friday -> Monday is 3 days
COSTS_FAIL_DAYS = 8.0
COSTS_PUBLISHED_WARN_DAYS = 8.0   # the release costs-v1 copy research.yml reads (costs.publish_release only)
DQ_WINDOW_HOURS = 24              # data-quality error events within this window raise a warning
NEWS_STALE_POLLS = 3              # feed health older than this many poll intervals: the news service is down
SPEND_WARN_FRACTION = 0.8
STUCK_APPROVAL_GRACE_S = 120
DISK_FAIL_FREE_GB, DISK_FAIL_FREE_PCT = 2.0, 5.0
DISK_WARN_FREE_GB, DISK_WARN_FREE_PCT = 5.0, 10.0
REARM_APPLY_S = 10 * 60           # an engine applies a re-arm on its next tick
HWM_PENDING_WARN_S = 5 * 60      # ADR 0018: a balance rise is confirmed or refused on the next tick
WATCH_STALE_S = 15 * 60           # health_last.json older than this: the Telegram alert loop is not running
HEARTBEAT_EVERY_S = 60            # services write state/heartbeat_<service>.json at most this often
HEARTBEAT_SILENT_S = 5 * 60       # design S5: "a heartbeat alert fires if any service has been silent for five minutes"
HEARTBEAT_SERVICES = ("supervisor", "scheduler", "telegram", "news", "api")   # engines: engine_<account>.json
QUOTE_INVALID_FAIL_S = 60         # an engine whose every quote has been invalid this long in market hours (review MEDIUM-2)
FAILED_ORDER_WINDOW_H = 24        # failed sends (X6) in this window are listed, each alerted once
BRIDGE_TIMEOUT_S = 5.0
BACKUP_WARN_H = 26.0              # daily backup: one missed night warns
BACKUP_FAIL_H = 72.0              # three missed nights fail
DRILL_WARN_DAYS = 8.0             # weekly restore drill
PRUNE_WARN_DAYS = 45.0            # monthly retention from the owner's Mac (scripts/backup_retention.sh), two weeks' slack
BACKUP_REPO_WARN_BYTES = 15e9     # of the 20 GB Oracle Object Storage free tier
BACKUP_SLOW_WARN_S = 600.0        # restic >= 0.16 cancels a backup at its lock-refresh deadline under the
                                  # no-delete key (~20 min; ADR 0002 update 2026-10-16): warn with margin

REQUIRED_SECRETS = ("telegram-bot-token",)
OPTIONAL_SECRETS = ("anthropic-api-key", "github-token")   # features switch off without them


class Check(FrozenRecord):
    name: str
    status: Status
    reason: str


class AccountRef(FrozenRecord):
    """The part of an account the checks need (keeps tests free of accounts.yaml)."""

    account_id: str
    server_tz: str | None = None      # for the server-clock check; None skips it
    symbol: str = "XAUUSD"
    adaptive: bool = False            # ADR 0031: the nightly limits_job decides its limits (check limits_controller)
    live: bool = False                # a real-money account: needs bridge v7 and its order lane up (BACKLOG 135)


class WeekendLaneRef(FrozenRecord):
    """ADR 0062: a weekend_only account as the `weekend_247` check needs it (listed, enabled or not)."""

    account_id: str
    enabled: bool
    window: WeekendWindow


class ObserveLaneRef(FrozenRecord):
    """ADR 0063: an observe_only account as the `observe` check needs it (never a trading account)."""

    account_id: str
    installed: bool                   # its bridge URL is stored on this host (the installer ran)
    symbol: str                       # the 24/7 symbol whose bars must keep coming
    refused: str | None = None        # dropped by load_accounts (its block could trade): why, and health WARNs


# --------------------------------------------------------------------------------------------- helpers
def _worst(statuses: list[Status]) -> Status:
    return max(statuses, key=lambda s: _RANK[s]) if statuses else "ok"


def _age(seconds: float) -> str:
    s = max(seconds, 0.0)
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} d"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _epoch(now: pd.Timestamp) -> float:
    return now.timestamp()


def _ahead_s(ts: float, ctx: HealthContext) -> float:
    """Seconds the stamp `ts` (epoch) is ahead of the latest clock this pass knows, max(ctx.now, the wall clock now),
    for the future-stamp checks only (SRE review MEDIUM, 2026-10-16): a stamp written while a slow pass ran is not
    ahead. Taking the later clock can only clear a future flag, never raise one, and a test's fixed past `ctx.now`
    keeps its stale and fresh verdicts (they still use ctx.now)."""
    return ts - max(_epoch(ctx.now), time.time())


def _market_open_throughout(ctx: HealthContext, seconds: float) -> bool:
    """True when the gold market was open at `now` and `seconds` ago (no weekend or daily break to explain silence).
    Checking both ends is enough for the windows used here (minutes; the daily break is an hour)."""
    idx = pd.DatetimeIndex([ctx.now, ctx.now - pd.Timedelta(seconds=seconds)])
    return bool(ctx.sessions.is_open(idx).all())


def _skipped(name: str, why: str) -> Check:
    return Check(name=name, status="warn", reason=f"skipped: {why}")

log = logging.getLogger("goldbot.health")


def heartbeat_path(state_dir: str | Path, service: str) -> Path:
    return Path(state_dir) / f"heartbeat_{service}.json"      # not engine_*: the supervisor globs those


WATCH_FILE = "health_last.json"


class AlertStreak(Record):
    """A check whose alert state differs from what the owner was last told, not yet confirmed (owner complaint
    2026-11-04, "up and down"): `kind` is the message it would send, seen on `polls` consecutive health passes since
    `first_seen`."""
    kind: Literal["new", "recovered"]
    first_seen: UtcTimestamp
    polls: int = Field(1, ge=1)


class WatchState(Record):
    """state/health_last.json. `statuses` is the last report as run (read by the deploy and daily-review checks);
    `announced` what the owner was last told per check (the alert baseline, behind `statuses` while a change waits in
    `pending`); `flaps` the times a failure cleared before it was confirmed, `flap_noted` when the owner last heard
    of a check's flapping. A file of the older shape (ts and statuses only) loads with announced = statuses: under the
    old rule every status was announced at once."""
    ts: UtcTimestamp | None = None
    statuses: dict[str, Status] = Field(default_factory=dict)
    announced: dict[str, Status] = Field(default_factory=dict)
    pending: dict[str, AlertStreak] = Field(default_factory=dict)
    flaps: dict[str, list[UtcTimestamp]] = Field(default_factory=dict)
    flap_noted: dict[str, UtcTimestamp] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _old_shape(cls, data: Any) -> Any:
        if isinstance(data, dict) and "announced" not in data:
            data = {**data, "announced": dict(data.get("statuses") or {})}
        return data


# ---- part-file caches of Store reads (health pass cost, 2026-11-10): the Telegram service checks every 60 s, the
# engines flush the decisions journal every 15 min, and a DuckDB read of a table costs seconds of one core
WindowCache = dict[tuple[str, ...], tuple[frozenset[str], pd.Timestamp, pd.DataFrame]]


def _part_files(base: Path, first_month: int | None = None) -> frozenset[str] | None:
    """The *.parquet part files under `base` (paths relative to it), in its year=/month= partitions from
    `first_month` (year * 12 + month) on, or every one; None when they cannot be listed. Store writes every part
    under a new random name and never rewrites one (`Store._write_atomic`; a dedupe writes a new part, then unlinks
    the old), so the set changes with every write."""
    if not base.is_dir():
        return frozenset()
    out: set[str] = set()

    def unlistable(exc: OSError) -> None:
        raise exc                             # os.walk skips an unreadable directory silently: a stale set otherwise
    try:
        for dirpath, _dirs, files in os.walk(base, onerror=unlistable):
            names = [f for f in files if f.endswith(".parquet")]
            if not names:
                continue
            rel = os.path.relpath(dirpath, base)
            if first_month is not None:
                part = dict(p.split("=", 1) for p in Path(rel).parts if "=" in p)
                if int(part["year"]) * 12 + int(part["month"]) < first_month:
                    continue
            out.update(f"{rel}/{n}" for n in names)
    except (OSError, ValueError, KeyError):
        return None
    return frozenset(out)


def _cached_window(cache: WindowCache, key: tuple[str, ...], parts: frozenset[str] | None, read_from: pd.Timestamp,
                   since: pd.Timestamp, end: pd.Timestamp,
                   read: Callable[[pd.Timestamp], pd.DataFrame]) -> pd.DataFrame:
    """The rows with since <= ts_utc < end of `read(read_from)` (a Store.read from `read_from` with no upper bound),
    the rows a fresh bounded read of the window returns. `read` runs again only when the part files changed (`parts`,
    listed before the read: a write landing in between costs one more read on the next pass, never a stale answer)
    or the last read does not reach back to `read_from` (a clock that went back); otherwise the last read is filtered
    to the window. The read has no upper bound, so a later window is covered by an earlier read of an unchanged
    table. Nothing is cached while the parts cannot be listed."""
    hit = cache.get(key)
    if parts is None or hit is None or hit[0] != parts or hit[1] > read_from:
        rows = read(read_from)
        if parts is None:
            cache.pop(key, None)
        else:
            cache[key] = (parts, read_from, rows)
    else:
        rows = hit[2]
    if rows.empty or "ts_utc" not in rows.columns:
        return rows.copy()     # never the cached frame itself: a caller may change what it gets
    ts = rows["ts_utc"]
    return rows[(ts >= since) & (ts < end)].reset_index(drop=True)
