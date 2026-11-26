"""Broker MT5 M1 capture guard for the D-1 fallback (ii) forward window (ADR 0105 (ii), quant K2/K3, 2026-11-26).

The capture record is the brain store table `bars_1m_broker`, filled daily by the scheduler's `feed_reconcile` (MT5
copy_rates_range through the read-only bridge). This module adds what the quant's text asserts, in two checks that
cannot mask each other (SRE review of #471):

* `learning:broker_m1_capture` (daily capture): FAILs once a session day's capture is due (DUE_AFTER after its 21:00
  UTC close, after feed_reconcile's 23:20 slot) and the reference account holds fewer than MIN_DAY_SHARE of the
  session's minutes. Weekends and CME metals FULL closures (goldbot.data.cme_holidays: New Year, Good Friday,
  Christmas, observed dates) are not due; early-halt days stay due (they keep > 60 % of the session);
* `learning:broker_m1_copy` (month copy): FAILs when a closed month's copy into raw/broker_m1/<account>/ is missing (or
  was written before the month ended) more than COPY_WITHIN_DAYS after month-end, and on a SHA256SUMS mismatch.
The daily `broker_m1_copy` job (goldbot/ops/jobs.py) writes the current month, the previous month until it freezes,
and every closed month that is missing or incomplete; a per-month gap log; SHA256SUMS rewritten only for the files
written in the run, the others verified (mismatches reported).

Freeze rule (quant, #471): a closed month freezes once its COPY_WITHIN_DAYS deadline passes. A later rewrite (only when
the frozen copy is missing or incomplete) gets a new sha256 and an entry in that month's gap log and in rewrites.jsonl;
a grade uses only the sha recorded in its tiers rows.

Read-only on the broker and on the store: no order path, no engine or bridge restart, no trading setting. The
reference account (ADR 0105 (ii), K3) is fixed: icm-demo, symbol XAUUSD, never switched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.cme_holidays import cme_metals_holidays

REFERENCE_ACCOUNT = "icm-demo"                  # ADR 0105 (ii), fixed 2026-11-26: no switch to another account
SYMBOL = "XAUUSD"                               # icm-demo's gold symbol (config/accounts.yaml), pinned (SRE 6)
CAPTURE_FROM = pd.Timestamp("2026-10-01", tz="UTC")
SESSION_MINUTES = 1380
MIN_DAY_SHARE = 0.60                            # the r11 thin-day line (828 minutes)
DUE_AFTER = pd.Timedelta(hours=2, minutes=50)   # session close 21:00 UTC + 2 h 50 = 23:50 UTC, after feed_reconcile
COPY_WITHIN_DAYS = 5
TABLE = "bars_1m_broker"
RAW_DIR = Path(__file__).resolve().parents[2] / "raw" / "broker_m1"
RECOVER = ("sudo -u goldbot sh -c 'cd /opt/goldbot && .venv/bin/python -m goldbot.ops.run m1-backfill icm-demo "
           "--days 3'  (then wait up to 15 min for the check)")


def _utc(now: pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(now)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def full_closure(d: dt.date) -> bool:
    c = cme_metals_holidays(d.year).get(d)
    return c is not None and c.kind == "full"


def session_bounds(day: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[21:00 UTC of the previous day, 21:00 UTC of `day`): the session day of goldbot.data.daily (21:00 UTC close)."""
    end = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=21)
    return end - pd.Timedelta(days=1), end


def due_day(now: pd.Timestamp) -> dt.date | None:
    """The latest session day whose capture is due at `now`: a weekday that is not a CME metals full closure, whose
    close + DUE_AFTER has passed. None when that day is before CAPTURE_FROM."""
    d = (_utc(now) - DUE_AFTER - pd.Timedelta(hours=21)).date()
    while d.weekday() >= 5 or full_closure(d):
        d -= dt.timedelta(days=1)
    return d if pd.Timestamp(d, tz="UTC") >= CAPTURE_FROM else None


def day_minutes(bars: pd.DataFrame, day: dt.date) -> int:
    lo, hi = session_bounds(day)
    t = pd.to_datetime(bars["ts_utc"], utc=True) if len(bars) else pd.Series([], dtype="datetime64[ns, UTC]")
    return int(((t >= lo) & (t < hi)).sum())


def read_capture(store: Any, account: str = REFERENCE_ACCOUNT, start: pd.Timestamp = CAPTURE_FROM) -> pd.DataFrame:
    b = store.read(TABLE, source=account, symbol=SYMBOL, start=start)
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


def _month_end(month: str) -> pd.Timestamp:
    return (pd.Period(month, freq="M") + 1).to_timestamp().tz_localize("UTC")


def frozen(month: str, now: pd.Timestamp) -> bool:
    return _utc(now) >= _month_end(month) + pd.Timedelta(days=COPY_WITHIN_DAYS)


def _written_after_end(p: Path, month: str) -> bool:
    return p.exists() and pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC") >= _month_end(month)


def _closed_months(now: pd.Timestamp) -> list[str]:
    last = _utc(now).tz_localize(None).to_period("M") - 1
    first = CAPTURE_FROM.tz_localize(None).to_period("M")
    return [str(p) for p in pd.period_range(first, last, freq="M")] if last >= first else []


def months_to_copy(now: pd.Timestamp, account: str = REFERENCE_ACCOUNT, raw_dir: Path = RAW_DIR) -> list[str]:
    """The current month, the previous month until it freezes, and every closed month whose copy is missing or was
    written before the month ended (SRE review 2: a missed month always recovers)."""
    now = _utc(now)
    cur = now.strftime("%Y-%m")
    prev = str(now.tz_localize(None).to_period("M") - 1)
    out = {cur}
    if pd.Period(prev, freq="M") >= CAPTURE_FROM.tz_localize(None).to_period("M") and not frozen(prev, now):
        out.add(prev)
    for m in _closed_months(now):
        if not _written_after_end(month_file(raw_dir, account, m), m):
            out.add(m)
    return sorted(out)


def _atomic_text(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(text)
    os.replace(tmp, p)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_sums(acc_dir: Path) -> dict[str, str]:
    f = acc_dir / "SHA256SUMS"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        if "  " in line:
            sha, name = line.split("  ", 1)
            out[name] = sha
    return out


def verify_sums(account: str = REFERENCE_ACCOUNT, raw_dir: Path = RAW_DIR) -> list[str]:
    """Month files whose bytes no longer match their SHA256SUMS entry, or that have no entry."""
    acc_dir = raw_dir / account
    sums = read_sums(acc_dir)
    files = sorted(acc_dir.glob("*.parquet")) if acc_dir.exists() else []
    return [f.name for f in files if sums.get(f.name) != _sha(f)]


def copy_months(bars: pd.DataFrame, now: pd.Timestamp, months: list[str] | None = None,
                account: str = REFERENCE_ACCOUNT, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    """Write `months` (default: months_to_copy) atomically from `bars`; a per-month gap log; SHA256SUMS rewritten
    for the files written now, every other entry kept and its file verified (mismatches returned)."""
    now = _utc(now)
    months = months_to_copy(now, account, raw_dir) if months is None else months
    acc_dir = raw_dir / account
    acc_dir.mkdir(parents=True, exist_ok=True)
    sums = read_sums(acc_dir)
    b = bars.assign(ts_utc=pd.to_datetime(bars["ts_utc"], utc=True)).drop_duplicates("ts_utc").sort_values("ts_utc") \
        if len(bars) else bars
    month = b["ts_utc"].dt.strftime("%Y-%m") if len(b) else pd.Series([], dtype=str)
    written: dict[str, Any] = {}
    for m in months:
        g = b[month == m] if len(b) else b
        if not len(g):
            continue
        p = month_file(raw_dir, account, m)
        old = sums.get(p.name)
        tmp = p.with_name(p.name + ".part")
        g.to_parquet(tmp, index=False)
        os.replace(tmp, p)
        new = _sha(p)
        sums[p.name] = new
        log: list[dict[str, object]] = gaps(g["ts_utc"])
        if frozen(m, now) and old is not None and old != new:
            entry: dict[str, object] = {"rewrite_utc": now.isoformat(), "previous_sha256": old, "sha256": new}
            log.append(entry)
            with (acc_dir / "rewrites.jsonl").open("a") as fh:
                fh.write(json.dumps({"month": m, **entry}) + "\n")
        _atomic_text(acc_dir / f"gaps_{m}.json", json.dumps(log, indent=1))
        written[p.name] = {"rows": len(g), "sha256": new, "frozen": frozen(m, now)}
    _atomic_text(acc_dir / "SHA256SUMS", "".join(f"{s}  {n}\n" for n, s in sorted(sums.items())))
    return {"rows": len(b), "months": months, "files": written, "sum_mismatch": verify_sums(account, raw_dir)}


def missing_copies(now: pd.Timestamp, account: str = REFERENCE_ACCOUNT, raw_dir: Path = RAW_DIR) -> list[str]:
    """Closed months past their COPY_WITHIN_DAYS deadline whose copy is missing or was written before the month
    ended."""
    now = _utc(now)
    return [m for m in _closed_months(now)
            if frozen(m, now) and not _written_after_end(month_file(raw_dir, account, m), m)]


def judge_capture(bars: pd.DataFrame, now: pd.Timestamp, account: str = REFERENCE_ACCOUNT) -> tuple[str, str]:
    """(status, reason) of the daily capture: fail on a missed due session day."""
    day = due_day(now)
    if day is None:
        return "ok", "no session day due yet"
    n = day_minutes(bars, day)
    line = f"{account} {day}: {n} of {SESSION_MINUTES} broker minutes"
    if n < MIN_DAY_SHARE * SESSION_MINUTES:
        return "fail", f"{line} (< {MIN_DAY_SHARE:.0%}: capture missed); recover: {RECOVER}"
    return "ok", line


def judge_copy(now: pd.Timestamp, account: str = REFERENCE_ACCOUNT, raw_dir: Path = RAW_DIR) -> tuple[str, str]:
    """(status, reason) of the month copies: fail on a copy missing past its deadline or a SHA256SUMS mismatch."""
    problems = []
    late = missing_copies(now, account, raw_dir)
    if late:
        problems.append(f"month copy into raw/broker_m1 missing > {COPY_WITHIN_DAYS} days after month-end: "
                        + ", ".join(late) + " (the next broker_m1_copy run writes it)")
    bad = verify_sums(account, raw_dir)
    if bad:
        problems.append("SHA256SUMS mismatch: " + ", ".join(bad))
    if problems:
        return "fail", "; ".join(problems)
    n = len(list((raw_dir / account).glob("*.parquet"))) if (raw_dir / account).exists() else 0
    return "ok", f"{n} month files, sums verified"


__all__ = ["CAPTURE_FROM", "COPY_WITHIN_DAYS", "MIN_DAY_SHARE", "RAW_DIR", "RECOVER", "REFERENCE_ACCOUNT", "SYMBOL",
           "copy_months", "day_minutes", "due_day", "frozen", "full_closure", "gaps", "judge_capture", "judge_copy",
           "missing_copies", "months_to_copy", "read_capture", "session_bounds", "verify_sums"]
