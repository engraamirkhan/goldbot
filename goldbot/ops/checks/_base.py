"""Shared types, thresholds and helpers of the health checks (design: Deployment / observability).

Moved verbatim from goldbot/ops/health.py, which re-exports every name; `HealthContext` stays there (it builds the
runtime context), so it is imported here for annotations only.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record, UtcTimestamp

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

Status = Literal["ok", "warn", "fail"]
_RANK: dict[str, int] = {"ok": 0, "warn": 1, "fail": 2}

# ---- thresholds (seconds unless named otherwise); the reasons quote them so an alert explains itself
SUPERVISOR_WARN_S = 20
SUPERVISOR_FAIL_S = 60            # engines block entries when the supervisor heartbeat is older than this
# a liveness stamp further ahead of the pass's `now` than this is a clock fault, never a fresh report (found
# 2026-10-16): wider than the engines' own CLOCK_AHEAD_TOLERANCE_S because `now` is taken once at the start of a
# pass that can run for many seconds (bridge and broker checks) while the services keep writing
HEALTH_CLOCK_AHEAD_S = 60
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

REQUIRED_SECRETS = ("telegram-bot-token",)   # the webhook secret is optional: only the webhook service needs it
OPTIONAL_SECRETS = ("anthropic-api-key", "github-token", "tradingview-webhook-secret")   # features switch off without them


class Check(FrozenRecord):
    name: str
    status: Status
    reason: str


class AccountRef(FrozenRecord):
    """The part of an account the checks need (keeps tests free of accounts.yaml)."""

    account_id: str
    server_tz: str | None = None      # for the server-clock check; None skips it
    symbol: str = "XAUUSD"


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


class WatchState(Record):
    ts: UtcTimestamp | None = None
    statuses: dict[str, Status] = Field(default_factory=dict)
