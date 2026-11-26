"""Broker MT5 M1 capture guard for the D-1 fallback (ii) forward window (ADR 0105 (ii), quant K2/K3, 2026-11-26).

The capture record is the brain store table `bars_1m_broker`, filled daily by the scheduler's `feed_reconcile` (MT5
copy_rates_range through the read-only bridge). This module adds the two guarantees the quant's text asserts:

* a missed trading day raises the alert the same day: `learning:broker_m1_capture` (goldbot/ops/checks/capture.py)
  FAILs once a session day's capture is due (DUE_AFTER after its close, after feed_reconcile's 23:20 UTC slot) and the
  reference account holds fewer than MIN_DAY_SHARE of the session's minutes for it; a FAIL reaches the owner through
  the health alert loop;
* each closed month is copied into raw/broker_m1/<account>/ within COPY_WITHIN_DAYS of month-end: the daily
  `broker_m1_copy` job (goldbot/ops/jobs.py) rewrites the current and previous month files, with a sha256 per file
  (SHA256SUMS) and a gap log; the check FAILs when a closed month's copy is missing COPY_WITHIN_DAYS after month-end.

Read-only on the broker and on the store: no order path, no engine or bridge restart, no trading setting. The
reference account is fixed (ADR 0105 (ii), K3): icm-demo, never switched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

REFERENCE_ACCOUNT = "icm-demo"                  # ADR 0105 (ii), fixed 2026-11-26: no switch to another account
CAPTURE_FROM = pd.Timestamp("2026-10-01", tz="UTC")
SESSION_MINUTES = 1380
MIN_DAY_SHARE = 0.60                            # the r11 thin-day line: a day below it is a missed capture
DUE_AFTER = pd.Timedelta(hours=2, minutes=50)   # session close 21:00 UTC + 2 h 50 = 23:50 UTC, after feed_reconcile
COPY_WITHIN_DAYS = 5
TABLE = "bars_1m_broker"
RAW_DIR = Path(__file__).resolve().parents[2] / "raw" / "broker_m1"


def _utc(now: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(now)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def session_bounds(day: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[21:00 UTC of the previous day, 21:00 UTC of `day`): the session day of goldbot.data.daily (21:00 UTC close)."""
    end = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=21)
    return end - pd.Timedelta(days=1), end


def due_day(now: pd.Timestamp) -> dt.date | None:
    """The latest session day whose capture is due at `now`: a weekday (not 25 Dec or 1 Jan) whose close + DUE_AFTER
    has passed. None when the latest such day is before CAPTURE_FROM."""
    d = (_utc(now) - DUE_AFTER - pd.Timedelta(hours=21)).date()
    while d.weekday() >= 5 or (d.month, d.day) in ((12, 25), (1, 1)):
        d -= dt.timedelta(days=1)
    return d if pd.Timestamp(d, tz="UTC") >= CAPTURE_FROM else None


def day_minutes(bars: pd.DataFrame, day: dt.date) -> int:
    lo, hi = session_bounds(day)
    t = pd.to_datetime(bars["ts_utc"], utc=True) if len(bars) else pd.Series([], dtype="datetime64[ns, UTC]")
    return int(((t >= lo) & (t < hi)).sum())


def read_capture(store: Any, account: str = REFERENCE_ACCOUNT, start: pd.Timestamp = CAPTURE_FROM) -> pd.DataFrame:
    b = store.read(TABLE, source=account, symbol=None, start=start)
    return b if b is not None else pd.DataFrame(columns=["ts_utc"])


def gaps(ts: pd.Series) -> list[dict[str, object]]:
    """Holes longer than 1 min and shorter than 20 h between consecutive minutes (the daily break shows as one each
    weekday; a weekend or holiday closure is longer and not listed)."""
    t = pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).sort_values()
    out: list[dict[str, object]] = []
    for a, b in zip(t[:-1], t[1:], strict=True):
        g = b - a
        if pd.Timedelta(minutes=1) < g < pd.Timedelta(hours=20):
            out.append({"from": str(a), "to": str(b), "missing_min": int(g / pd.Timedelta(minutes=1)) - 1})
    return out


def month_file(raw_dir: Path, account: str, month: str) -> Path:
    return raw_dir / account / f"{account}_m1_{month}.parquet"


def copy_months(bars: pd.DataFrame, now: pd.Timestamp, account: str = REFERENCE_ACCOUNT,
                raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Rewrite the previous and the current month's file (atomic), the account's SHA256SUMS and gaps.json."""
    if not len(bars):
        return {"rows": 0, "files": {}}
    b = bars.assign(ts_utc=pd.to_datetime(bars["ts_utc"], utc=True)).drop_duplicates("ts_utc").sort_values("ts_utc")
    now = _utc(now)
    cur = now.strftime("%Y-%m")
    prev = (now.tz_localize(None).to_period("M") - 1).strftime("%Y-%m")
    month = b["ts_utc"].dt.strftime("%Y-%m")
    files: dict[str, Any] = {}
    for m in (prev, cur):
        g = b[month == m]
        if not len(g):
            continue
        p = month_file(raw_dir, account, m)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        g.to_parquet(tmp, index=False)
        os.replace(tmp, p)
        files[p.name] = {"rows": len(g), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    acc_dir = raw_dir / account
    if acc_dir.exists():
        (acc_dir / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in sorted(acc_dir.glob("*.parquet"))))
        (acc_dir / "gaps.json").write_text(json.dumps(gaps(b["ts_utc"]), indent=1))
    return {"rows": len(b), "files": files}


def missing_copies(now: pd.Timestamp, account: str = REFERENCE_ACCOUNT, raw_dir: Path = RAW_DIR) -> list[str]:
    """Closed months from CAPTURE_FROM whose copy is missing more than COPY_WITHIN_DAYS after month-end, or was last
    written before that month ended (an incomplete copy)."""
    out = []
    now = _utc(now)
    last_closed = now.tz_localize(None).to_period("M") - 1
    for per in pd.period_range(CAPTURE_FROM.tz_localize(None).to_period("M"), last_closed, freq="M"):
        month_end = (per + 1).to_timestamp().tz_localize("UTC")
        if now < month_end + pd.Timedelta(days=COPY_WITHIN_DAYS):
            continue
        p = month_file(raw_dir, account, str(per))
        if not p.exists() or pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC") < month_end:
            out.append(str(per))
    return out


def judge(bars: pd.DataFrame, now: pd.Timestamp, account: str = REFERENCE_ACCOUNT,
          raw_dir: Path = RAW_DIR) -> tuple[str, str]:
    """(status, reason) of the capture: fail on a missed due day or a late month copy, else ok."""
    day = due_day(now)
    problems = []
    line = "no session day due yet"
    if day is not None:
        n = day_minutes(bars, day)
        line = f"{account} {day}: {n} of {SESSION_MINUTES} broker minutes"
        if n < MIN_DAY_SHARE * SESSION_MINUTES:
            problems.append(f"{line} (< {MIN_DAY_SHARE:.0%}: capture missed; run `m1-backfill {account}`)")
    late = missing_copies(now, account, raw_dir)
    if late:
        problems.append(f"month copy into raw/broker_m1 missing > {COPY_WITHIN_DAYS} days after month-end: "
                        + ", ".join(late))
    return ("fail", "; ".join(problems)) if problems else ("ok", line)


__all__ = ["CAPTURE_FROM", "COPY_WITHIN_DAYS", "MIN_DAY_SHARE", "RAW_DIR", "REFERENCE_ACCOUNT", "copy_months",
           "day_minutes", "due_day", "gaps", "judge", "missing_copies", "read_capture", "session_bounds"]
